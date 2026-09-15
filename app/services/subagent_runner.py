# -*- coding: utf-8 -*-
"""只读能力 → `CustomerReadTools` 的真实映射（受控子代理并行层 P0-3 接线）。

## 为什么需要这一层

`SubagentExecutor` 的 `runner` 是**注入式**的：executor 本身不知道
"customer_asset_summary 该调哪个工具"。设计上刻意如此——executor 只管
并发/超时/降级/审计，**能力映射放在这里**，两者都不承担对方的职责。

## 安全边界

- 映射表 `_CAPABILITY_TO_TOOL` 是**唯一**入口：capability → 只读工具名。
  未登记的 capability 永远返回 `failed`，绝不"猜一个工具试试"。
- 只调用 `CustomerReadTools.execute`，其内部还有 `allowed` 硬白名单与
  `route_mismatch` 校验（工具与返回路由不一致即失败）——**双层校验**。
- 工具返回失败 → 映射为 `partial`（不是 `success`），保留 error 供上游降级；
  这样"子代理失败"不会被融合层当成有效答案。
"""
from __future__ import annotations

from app.common.logging.config import get_logger
from app.schemas.subagent import SubagentResult, SubagentTask

logger = get_logger(__name__)

# capability → (只读工具名, 该工具需要的 knowledge intent)
# 说明：`customer_rag_knowledge` 用 faq（公开知识兜底类别）；
# `customer_risk_explanation` 用 risk_public（风险制度依据），两者检索范围不同，
# 故不能合并成同一个 capability。
_CAPABILITY_TO_TOOL: dict[str, tuple[str, str | None]] = {
    "customer_asset_summary": ("get_my_asset_summary", None),
    "customer_product_status": ("get_product_status", None),
    "customer_order_status": ("get_my_order_status", None),
    "customer_workorder_status": ("get_my_workorder_status", None),
    "customer_risk_restriction": ("get_my_risk_restriction", None),
    "customer_rag_knowledge": ("search_customer_knowledge", "faq"),
    "customer_risk_explanation": ("search_customer_knowledge", "risk_public"),
}

# 成功/失败时的置信度（供 SubagentExecutor._merge 排序用；确定性工具取固定值，
# 不做"模型自评置信度"那种不可复核的东西）。
_CONFIDENCE = 0.9
_FAILED_CONFIDENCE = 0.0


def capability_to_tool(capability: str) -> tuple[str, str | None] | None:
    """capability → (工具名, intent)；未登记返回 None（调用方必须据此拒绝）。"""
    return _CAPABILITY_TO_TOOL.get(str(capability or "").strip())


class ReadOnlySubagentRunner:
    """把 `SubagentTask` 交给 `CustomerReadTools` 执行，返回结构化 `SubagentResult`。

    `answer_generator`（可选）：知识类子任务拿到的 `CustomerReadTools` 返回是
    **引用集合 + 占位文案**（`answer` 恒为"可基于客服知识库引用生成回答。"），
    真正的答案必须由 `CustomerRagAnswerService.generate` 从引用生成。
    若不接这一步，融合答案里知识类子任务只会输出那句占位串 —— 看着"成功"，
    实际没有任何内容（这类"状态对、内容空"的缺陷最难发现）。

    `prior_facts`（可选）：依赖前序子任务时，前序结论**只进生成提示、不进检索串**
    （拼进检索串会污染检索，见 ISSUE-20260914-DEP-CONTEXT-POLLUTES-RETRIEVAL）。
    """

    def __init__(self, *, tools, answer_generator=None) -> None:
        self.tools = tools
        self.answer_generator = answer_generator

    async def _generate(self, *, intent: str, data: dict, query: str, prior_facts: str) -> str:
        """用引用生成真实答案；生成失败则退回工具原始文案（不阻断）。"""
        if self.answer_generator is None:
            return ""
        citations = data.get("citations") or []
        if not citations:
            return ""
        try:
            from app.services.customer_rag_service import CustomerRagResult
            from app.services.knowledge_retrieval import KnowledgeCitation
            rag_result = CustomerRagResult(
                intent=intent,
                answer_mode="rag_answer",
                evidence_status=data.get("evidence_status", "sufficient"),
                citations=[KnowledgeCitation(**c) for c in citations],
                handoff_required=False,
                message="",
            )
            gen_query = f"{query}{prior_facts}" if prior_facts else query
            generated = await self.answer_generator.generate(query=gen_query, result=rag_result)
            return str(getattr(generated, "answer", "") or "")
        except Exception as exc:  # noqa: BLE001 - 生成失败不阻断，退回原始文案
            logger.warning("subagent.generate_failed intent=%s err=%s", intent, type(exc).__name__)
            return ""

    async def __call__(self, task: SubagentTask, prior_facts: str = "") -> SubagentResult:
        mapped = capability_to_tool(task.read_only_capability)
        if mapped is None:
            # 未登记能力：不猜测、不降级到别的工具，直接失败。
            return SubagentResult(
                task_id=task.task_id, status="failed",
                error="capability_not_mapped", summary="",
                confidence=_FAILED_CONFIDENCE,
            )
        tool_name, intent = mapped
        try:
            result = await self.tools.execute(
                name=tool_name, customer_id=str(task.scope_json.get("customer_id") or ""),
                query=task.query, intent=intent,
            )
        except Exception as exc:  # noqa: BLE001 - 子代理失败只降级本子任务
            logger.warning("subagent.runner_failed capability=%s err=%s",
                           task.read_only_capability, type(exc).__name__)
            return SubagentResult(
                task_id=task.task_id, status="failed",
                error=f"{type(exc).__name__}: {exc}", summary="",
                confidence=_FAILED_CONFIDENCE,
            )

        data = dict(result.data or {})
        data["tool"] = tool_name
        data["read_only_capability"] = task.read_only_capability
        if not result.success:
            # 失败保留**摘要**（不清空），因为工具对 fact_not_found / ambiguous_product
            # 这类失败会给出面向用户的澄清文案，丢掉会让上游无法降级为"请补充信息"。
            return SubagentResult(
                task_id=task.task_id, status="partial",
                summary=result.answer or "", data=data,
                error=result.error or "unknown", confidence=_FAILED_CONFIDENCE,
            )

        summary = result.answer or ""
        # 知识类子任务：answer 是占位串，真实答案需从引用生成。
        if tool_name == "search_customer_knowledge" and data.get("citations"):
            generated = await self._generate(
                intent=intent or "faq", data=data, query=task.query, prior_facts=prior_facts)
            if generated:
                summary = generated
                data["generation_status"] = "generated"
        return SubagentResult(
            task_id=task.task_id, status="success",
            summary=summary, data=data,
            confidence=_CONFIDENCE,
        )
