from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.services.customer_intent import IntentPlan, IntentTask
from app.services.customer_tools import CustomerReadTools
from app.services.customer_rag_answer import CustomerRagAnswerService
from app.infrastructure.qwen import QwenProvider
from app.core.settings import get_settings
from app.services.suitability_service import SuitabilityService
from app.services.trading_service import TradingService
from app.repositories.trading import SqlAlchemyTradingUnitOfWork
from app.services.asset_summary_service import AssetSummaryService

TOOL_BY_INTENT = {
    "workorder_status": "get_my_workorder_status",
    "order_status": "get_my_order_status",
    "risk_restriction": "get_my_risk_restriction",
    "asset_summary": "get_my_asset_summary",
    "product_status": "get_product_status",
}

@dataclass(frozen=True)
class TaskExecutionResult:
    task_id: str
    intent: str
    status: str
    answer: str = ""
    data: dict[str, Any] | None = None
    error: str | None = None
    clarification: str | None = None

OPERATION_ACTION_TARGETS = {"赎回": "/customer-center?tab=operations&action=redeem", "转账": "/customer-center?tab=operations&action=transfer", "下单": "/customer-center?tab=operations&action=order"}

class CustomerTaskExecutor:
    """Plan executor with a bounded, read-only ReAct-style fallback for knowledge tasks."""

    MAX_REACT_ITERATIONS = 20
    MAX_REACT_TOOL_CALLS = 20
    REACT_INTENTS = {"company_info", "company_business", "faq", "workflow", "policy", "risk_public"}

    def __init__(self, database, max_parallel_tasks: int = 4, max_iterations: int | None = None, max_tool_calls: int | None = None, conversation_summary: str = ""):
        self.database = database
        self.tools = CustomerReadTools(database)
        self.conversation_summary = conversation_summary or ""
        self.max_parallel_tasks = max_parallel_tasks
        if max_iterations is not None:
            self.MAX_REACT_ITERATIONS = max_iterations
        if max_tool_calls is not None:
            self.MAX_REACT_TOOL_CALLS = max_tool_calls
        # 有界改写模板：仅在 low_confidence 时逐条尝试；重复结果由 hash 早停兜底
        self._rewrite_pool = [
            "请提供官方流程、适用条件和注意事项",
            "请说明相关制度或流程，并给出依据",
        ]

    async def _load_preference_facts(self, customer_id: str) -> dict:
        """读偏好事实（软排序用）。先读私有长时记忆，再补客服写入的 shared 观察（跨 Agent 复用）。"""
        from app.services.memory_service import CustomerMemoryService
        from app.services.shared_memory_service import SharedMemoryService
        whitelist = {"risk_preference", "product_preference", "investment_horizon_preference"}
        memory = CustomerMemoryService(self.database, None)
        rows = await memory.load_long_term(customer_id=customer_id)
        facts = {row.key: str(row.value) for row in rows if getattr(row, "key", None) in whitelist and getattr(row, "value", "")}
        # 共享记忆语义层：投顾/推荐复用客服写入的 shared observation（私有未命中时）
        try:
            shared = await SharedMemoryService(self.database, None).recall(
                customer_id=customer_id, agent_type="advisor", memory_type="observation")
            for row in shared:
                if getattr(row, "key", None) in whitelist and getattr(row, "value", "") and row.key not in facts:
                    facts[row.key] = str(row.value)
        except Exception:  # noqa: BLE001 - 记忆异常不阻塞推荐
            pass
        return facts

    @staticmethod
    def _result_signature(result) -> str:
        """对工具返回结果做规范化哈希（引用+证据状态+回答），用于检测"重复结果→局部最优/已完成"的早停。"""
        data = result.data if result.data else {}
        citations = data.get("citations") or []
        key = []
        for c in citations:
            if isinstance(c, dict):
                key.append((c.get("chunk_id") or c.get("id") or "", c.get("content") or ""))
        key.sort(key=lambda x: x[0])
        canonical = {
            "citations": key,
            "evidence_status": data.get("evidence_status"),
            "answer": result.answer,
        }
        return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    async def _bounded_react_knowledge(self, *, customer_id: str, item: IntentTask) -> TaskExecutionResult:
        """确定性只读 ReAct：硬上限(20) + 结果哈希早停。

        每轮都对工具返回结果算哈希；若某结果之前已出现过（重复 hash）→ 判定进入局部最优 /
        实际已完成但模型未意识到 → 早停，避免空转。只要结果在变化就继续（最多 20 轮）。
        """
        attempts: list[dict[str, object]] = []
        seen_results: set[str] = set()
        query = item.query.strip()
        result = None
        rewrite_index = 0
        for iteration in range(1, self.MAX_REACT_ITERATIONS + 1):
            if len(attempts) >= self.MAX_REACT_TOOL_CALLS:
                break
            result = await self.tools.execute(
                name="search_customer_knowledge",
                customer_id=customer_id,
                query=query,
                intent=item.intent,
                entities=item.arguments,
            )
            attempts.append({
                "tool": "search_customer_knowledge",
                "iteration": iteration,
                "success": result.success,
                "error": result.error,
                "evidence_status": result.data.get("evidence_status") if result.data else None,
            })
            # 结果哈希早停：任意两轮结果相同 → 无进展
            sig = self._result_signature(result)
            if sig in seen_results:
                attempts[-1]["early_stop"] = "repeated_result"
                break
            seen_results.add(sig)
            if result.success:
                citations = result.data.get("citations", []) if result.data else []
                if citations:
                    from app.services.customer_rag_service import CustomerRagResult
                    from app.services.knowledge_retrieval import KnowledgeCitation
                    rag_result = CustomerRagResult(
                        intent=item.intent,
                        answer_mode="rag_answer",
                        evidence_status=result.data.get("evidence_status", "sufficient"),
                        citations=[KnowledgeCitation(**citation) for citation in citations],
                        handoff_required=False,
                        message=result.answer,
                    )
                    generated = await CustomerRagAnswerService(QwenProvider(get_settings())).generate(query=item.query, result=rag_result, conversation_summary=self.conversation_summary)
                    return TaskExecutionResult(
                        item.task_id,
                        item.intent,
                        "succeeded",
                        answer=generated.answer,
                        data={**result.data, "generation_status": generated.generation_status, "execution_mode": "bounded_react", "tool_calls": attempts},
                    )
                break  # success 但无引用 → 无法生成
            # 失败（low_confidence）→ 尝试下一种有界改写（重复结果由早停兜底）
            if result.error == "low_confidence" and rewrite_index < len(self._rewrite_pool):
                query = f"{item.query.strip()} {self._rewrite_pool[rewrite_index]}"
                rewrite_index += 1
                continue
            break
        return TaskExecutionResult(
            item.task_id,
            item.intent,
            "needs_clarification",
            answer=result.answer if result else "当前资料不足。",
            data={"execution_mode": "bounded_react", "tool_calls": attempts},
            error=result.error if result else "tool_budget_exhausted",
            clarification="当前资料不足，可以补充问题或转人工客服。",
        )

    async def execute(self, *, customer_id: str, plan: IntentPlan) -> list[TaskExecutionResult]:
        results: dict[str, TaskExecutionResult] = {}
        pending = list(plan.tasks)
        while pending:
            ready = [item for item in pending if all(dep in results for dep in item.depends_on)]
            if not ready:
                results.update({item.task_id: TaskExecutionResult(item.task_id, item.intent, "blocked", error="dependency_not_completed") for item in pending})
                break
            batch, pending = ready[:self.max_parallel_tasks], [item for item in pending if item not in ready]
            batch_results = await asyncio.gather(*(self._execute_one(customer_id=customer_id, item=item, results=results) for item in batch))
            results.update({item.task_id: item for item in batch_results})
        return [results[item.task_id] for item in plan.tasks]

    @staticmethod
    def _dependency_context(item: IntentTask,
                            results: dict[str, TaskExecutionResult]) -> str:
        """把 `depends_on` 指向的前序 task 结果拼成"上下文块"，供后序 task 使用。

        **为什么需要（ISSUE-20260914-NO-TASK-ANSWER-PASSING）**：
        此前 `depends_on` 只用于**执行顺序门控**（`ready = all(dep in results ...)`），
        最多在 workflow/faq/policy 分支读一下依赖的 `intent` 用来选工具名，
        **从不把前序 task 的答案传下去**。于是"后一个 task 依赖前一个 task 的结论"这件事
        在系统里做不了——例如"用客户风险等级（事实）套制度规则（知识）"，
        制度 task 拿不到等级，只能空谈规则甚至编造结论
        （见 ISSUE-20260914-STATUS-MACHINE-LACKS-CONSISTENCY-DIMENSION）。

        **与既有硬编码写法的区别**：`business_operation` 分支是"遍历 results 按 intent 找
        product_recommendation"，属于**隐式耦合**（不依赖 depends_on 声明）。
        本方法改走**显式声明**：只有规划器在 `depends_on` 里声明了依赖，才会注入。
        这样"谁能看到谁的结论"由图结构决定，而不是散落在各分支里的硬编码。

        返回空串表示无依赖或无可用结果（调用方应原样使用 item.query）。
        """
        if not item.depends_on:
            return ""
        blocks: list[str] = []
        for dep_id in item.depends_on:
            dep = results.get(dep_id)
            if dep is None or not dep.answer:
                continue
            blocks.append(f"[{dep.intent}] {dep.answer}")
        if not blocks:
            return ""
        return ("\n\n【前序查询结果（请基于这些事实作答，不要臆造）】\n"
                + "\n".join(blocks))

    async def _execute_one(self, *, customer_id: str, item: IntentTask, results: dict[str, TaskExecutionResult]) -> TaskExecutionResult:
        if item.intent == "greeting":
            return TaskExecutionResult(item.task_id, item.intent, "succeeded", answer="您好，我可以协助查询业务事实、客服知识和人工服务。")
        if item.intent == "human_handoff":
            return TaskExecutionResult(item.task_id, item.intent, "needs_clarification", answer="可以为您转接人工客服。", clarification="是否现在转接人工客服？")
        if item.intent in self.REACT_INTENTS:
            # 把前序事实作为上下文传给知识/制度类 task。
            # 这是"用事实套规则"得以成立的前提：制度 task 必须拿到客户真实等级，
            # 否则它只能空谈规则（实测会编出"是的，您有风险限制"这种错误断言）。
            dep_context = self._dependency_context(item, results)
            if dep_context:
                from dataclasses import replace as _replace
                item = _replace(item, query=f"{item.query}{dep_context}")
            return await self._bounded_react_knowledge(customer_id=customer_id, item=item)
        if item.intent == "business_operation":
            is_purchase = any(marker in item.query for marker in ("申购", "购买", "买入", "下单"))
            recommendation_result = next((result for result in results.values() if result.intent == "product_recommendation"), None)
            if is_purchase and recommendation_result is not None:
                recommendation = (recommendation_result.data or {}).get("recommendation", {})
                matches = recommendation.get("matches") or []
                if not matches:
                    return TaskExecutionResult(
                        item.task_id, item.intent, "needs_clarification",
                        answer="当前没有可直接推荐的适配产品，请先补充风险偏好、投资期限或转人工客服。",
                        data={
                            "operation": "申购",
                            "recommended_purchase": True,
                            "recommendation": recommendation,
                            "explicit_amount_text": item.query,
                            "confirmation_required": True,
                            "executed": False,
                        },
                        clarification="当前没有通过适当性匹配的产品；系统不会自动选择产品或创建订单。",
                    )
                return TaskExecutionResult(
                    item.task_id, item.intent, "needs_clarification",
                    answer="已完成适当性推荐。请从推荐产品中明确选择一个产品后，再为您生成申购草稿。",
                    data={
                        "operation": "申购",
                        "recommended_purchase": True,
                        "recommendation": recommendation,
                        "explicit_amount_text": item.query,
                        "confirmation_required": True,
                        "executed": False,
                    },
                    clarification="请选择一个通过适当性匹配的产品；系统不会自动替您选择产品或创建订单。",
                )
            operation = next((name for name in ("申购", "购买", "买入", "赎回", "转账", "下单") if name in item.query), "业务操作")
            return TaskExecutionResult(item.task_id, item.intent, "needs_confirmation", answer=f"可以为您准备“{operation}”业务，但不会在客服对话中直接执行。", data={"operation": operation, "confirmation_required": True, "executed": False, "action_target": OPERATION_ACTION_TARGETS.get(operation, "/customer-center?tab=operations"), "prefill_hint": f"请在统一助手中确认{operation}的对象、金额和最终提交。"}, clarification=f"请补充或确认“{operation}”的对象、金额和最终提交。")
        if item.intent == "product_recommendation":
            uow = SqlAlchemyTradingUnitOfWork()
            suitability = SuitabilityService(
                risk_repository=uow.risks,
                product_repository=uow.products,
                profile_repository=uow.profiles,
                asset_service=AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets),
            )
            async with self.database.session_factory() as session:
                # 长时记忆偏好（软排序，不改变适当性硬门禁）
                preferences = await self._load_preference_facts(customer_id)
                recommendation = await suitability.recommend(session, customer_id, preferences=preferences)
            return TaskExecutionResult(
                item.task_id, item.intent, "succeeded",
                answer="根据当前风险测评、客户类型和资产门槛，以下产品通过适当性匹配。",
                data={"recommendation": recommendation.model_dump(mode="json"), "execution_mode": "plan_rules"},
            )
        if item.intent == "unknown":
            return TaskExecutionResult(item.task_id, item.intent, "needs_clarification", answer="我还不能确定您要查询或办理的事项。", clarification="请补充具体对象或选择转人工客服。")
        if item.depends_on and item.intent in {"workflow", "faq", "policy"}:
            tool_name = "explain_order_status" if any(results.get(dep) and results[dep].intent == "order_status" for dep in item.depends_on) else "explain_workorder_status"
            result = await self.tools.execute(name=tool_name, customer_id=customer_id, query=item.query, intent=item.intent, entities=item.arguments)
            if not result.success:
                return TaskExecutionResult(item.task_id, item.intent, "needs_clarification", answer=result.answer, data=result.data, error=result.error, clarification="当前没有足够的流程资料，可以转人工客服。")
            return TaskExecutionResult(item.task_id, item.intent, "succeeded", answer=result.answer, data=result.data)
        tool_name = TOOL_BY_INTENT.get(item.intent)
        if not tool_name:
            return TaskExecutionResult(item.task_id, item.intent, "failed", error="no_tool_mapping")
        result = await self.tools.execute(name=tool_name, customer_id=customer_id, query=item.query, entities=item.arguments)
        if not result.success:
            needs = result.error in {"fact_not_found", "ambiguous_product"}
            return TaskExecutionResult(item.task_id, item.intent, "needs_clarification" if needs else "failed", error=result.error, clarification="请补充更明确的查询对象或订单/产品信息。" if needs else None)
        return TaskExecutionResult(item.task_id, item.intent, "succeeded", answer=result.answer, data=result.data)

    @staticmethod
    def summarize(results: list[TaskExecutionResult], *, requires_handoff: bool = False) -> tuple[str, str | None, str]:
        """Return stable overall status, first clarification, and aggregate answer.

        状态语义（含中间态）：
        - `completed`          ：全部 task 成功且证据完整。
        - `partially_answered` ：有 task **拿到了引用并生成了答案，但答案自述资料不足**
                                 （引用相关却不完整）。与 `partially_completed` 的区别：
                                 后者是"部分 task 失败"，前者是"任务都在、但答案不完整"。
                                 此前缺失该状态，导致"答了但没答全"被标成 `completed`
                                 （ISSUE-20260914-NO-PARTIAL-STATE）。
        - `partially_completed`：部分 task 成功、部分失败/需澄清。
        - `needs_clarification` / `failed` / `blocked_by_human`：见下。
        """
        if requires_handoff:
            status = "handoff"
        elif not results:
            status = "needs_clarification"
        else:
            succeeded = sum(item.status == "succeeded" for item in results)
            # "已作答"包含完整成功与半成功（partially_answered 也是答了，只是不完整）
            answered = sum(item.status in {"succeeded", "partially_answered"} for item in results)
            partial_answer = any(item.status == "partially_answered" for item in results)
            needs_clarification = any(item.status in {"needs_clarification", "needs_confirmation"} for item in results)
            failed = any(item.status in {"failed", "blocked"} for item in results)
            if succeeded == len(results):
                status = "completed"
            elif partial_answer and not (needs_clarification or failed):
                # 全部 task 都已作答（无失败/待澄清），只是答案自述不完整 → 中间态
                status = "partially_answered"
            elif answered and (needs_clarification or failed):
                # 部分 task 作答（含半成功）、部分失败/待澄清
                status = "partially_completed"
            elif needs_clarification:
                status = "needs_clarification"
            elif failed:
                status = "failed"
            else:
                status = "blocked_by_human"
        clarification = next((item.clarification for item in results if item.clarification), None)
        lines = [item.answer for item in results if item.answer]
        if clarification:
            lines.append(f"下一步：{clarification}")
        return status, clarification, "\n".join(lines) or "当前没有可返回的结果。"
