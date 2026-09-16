# -*- coding: utf-8 -*-
"""受控只读子代理并行执行器（受控子代理并行层设计方案 P0）。

职责：接收一组"可并行、可独立、只读"的子任务 → 并发执行受控子代理 →
确定性融合为结构化结果。核心护栏：
- **只读白名单**：read_only_capability 必须在 READ_ONLY_CAPABILITIES 内，否则直接拒绝；
  本层从不暴露写工具/资金/订单/风控结论。
- **并发控制**：asyncio.Semaphore(max_concurrent) + 每子代理独立超时 + 全局超时。
- **降级**：单个子代理失败/超时只把该子任务标 failed/partial，不拖垮整体（gather 容错）。
- **审计**：通过注入的 audit 回调落 AuditLog(action="subagent.ran")，只含结构化元数据，
  绝不落私有推理链。

runner 与 audit 均为可注入：真实路径由调用方提供只读处理器与 DB 审计；单元测试注入 fake。
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from app.schemas.subagent import SubagentMergeResult, SubagentResult, SubagentTask

# 允许进入子代理并行的只读能力白名单（对照现有只读能力/工具）
READ_ONLY_CAPABILITIES = frozenset({
    "customer_asset_summary",     # get_my_asset_summary
    "customer_product_status",    # get_product_status
    "customer_order_status",      # get_my_order_status
    "customer_workorder_status",  # get_my_workorder_status
    "customer_risk_restriction",  # get_my_risk_restriction
    "customer_rag_knowledge",     # search_customer_knowledge
    "customer_risk_explanation",  # 只读风险解释
})

SubagentRunner = Callable[[SubagentTask], Awaitable[SubagentResult]]
AuditCallback = Callable[[SubagentTask, SubagentResult, float], Awaitable[None]]


def _content_key(text: str) -> str:
    """把一段回答归一化成去重键。

    为什么要归一化而不是直接比字符串（实测依据）：两个子代理对**同一份知识**
    生成的答案往往**措辞不同**（如"公司主营财富管理。"vs"公司主营业务为财富管理"），
    直接相等比较**去不掉**。故做轻量归一化：
      - 去掉空白（含换行/全角空格）；
      - 去掉常见标点与引用标注（"（引用1）"这类）；
      - 统一大小写。
    保留**中文字符与数字**——它们是内容的实质，不能去掉（否则"R1"和"R2"会被视为相同，
    而那正是必须区分的内容）。

    刻意**不做**语义级去重（如向量相似度聚类）：那会引入不确定性与阈值调参，
    而融合层的设计要求是**确定性**（见设计文档 §5.5）。此处只处理
    "同一份知识的不同表述"中最明显的表层差异。
    """
    import re as _re
    s = str(text or "")
    s = _re.sub(r"\s+", "", s)
    # 引用标注形如（引用1）/（引用 1）/ [引用1] —— 它们是出处标记，不是内容
    s = _re.sub(r"[（(\[【]?\s*引用\s*\d+\s*[)）\]】]?", "", s)
    s = _re.sub(r"[，。、；：！？,.;:!?\"'“”‘’\-—…·]", "", s)
    return s.strip().lower()


async def _noop_audit(_task: SubagentTask, _result: SubagentResult, _dur: float) -> None:
    return None


class SubagentExecutor:
    # 每子任务超时默认值。
    # **为什么不是 8.0**（初版值，实测踩坑）：知识类子任务的耗时 = 检索(含向量+BM25 双路)
    # **+ 一次答案生成 LLM 调用**，实测单测用例耗时 11.5s。
    # 8.0 会把这类子任务一律判为 `subagent_timeout`，且**结果被丢掉**——
    # 表现为"资产子任务成功、知识子任务 partial 且摘要为空"，看着像模型/检索故障，
    # 实际是超时阈值与真实工作量不匹配。
    # 现在按"最慢一类子任务 + 余量"取值；单次工具查询（资产/订单等）远快于此，不受影响。
    DEFAULT_PER_TASK_TIMEOUT_S = 20.0
    # 全局超时：必须 ≥ 单任务超时，否则多子任务串行时最后一个必被砍。
    DEFAULT_GLOBAL_TIMEOUT_S = 45.0

    def __init__(
        self,
        *,
        runner: SubagentRunner | None = None,
        max_concurrent: int = 2,
        per_task_timeout_s: float = DEFAULT_PER_TASK_TIMEOUT_S,
        global_timeout_s: float = DEFAULT_GLOBAL_TIMEOUT_S,
        audit: AuditCallback | None = None,
    ) -> None:
        self.runner = runner
        self.max_concurrent = max(1, int(max_concurrent))
        self.per_task_timeout_s = per_task_timeout_s
        self.global_timeout_s = global_timeout_s
        self._audit = audit or _noop_audit

    async def execute(
        self,
        tasks: list[SubagentTask],
        *,
        runner: SubagentRunner | None = None,
    ) -> SubagentMergeResult:
        r = runner or self.runner
        if r is None:
            raise ValueError("SubagentExecutor needs a runner (real read-only handler or test fake)")
        if not tasks:
            return SubagentMergeResult(results=[], overall_status="succeeded", answer="")

        sem = asyncio.Semaphore(self.max_concurrent)

        async def _run_one(task: SubagentTask) -> SubagentResult:
            t0 = time.monotonic()
            if task.read_only_capability not in READ_ONLY_CAPABILITIES:
                res = SubagentResult(
                    task_id=task.task_id, status="failed",
                    error="read_only_capability_not_allowed", summary="",
                )
            else:
                try:
                    async with sem:
                        # 全局超时由外层 wait_for 兜底；此处先按任务超时等待
                        res = await asyncio.wait_for(r(task), timeout=self.per_task_timeout_s)
                        if not isinstance(res, SubagentResult):
                            res = SubagentResult(task_id=task.task_id, status="failed", error="runner_returned_non_result")
                except asyncio.TimeoutError:
                    res = SubagentResult(task_id=task.task_id, status="partial", error="subagent_timeout")
                except Exception as exc:  # noqa: BLE001 - 子代理失败仅降级该子任务
                    res = SubagentResult(task_id=task.task_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            dur = time.monotonic() - t0
            try:
                await self._audit(task, res, dur)
            except Exception:  # noqa: BLE001 - 审计失败不影响执行
                pass
            return res

        coros = [_run_one(t) for t in tasks]
        # 全局超时：超时未返回的按 global_timeout 降级
        try:
            raw = await asyncio.wait_for(
                asyncio.gather(*coros, return_exceptions=True), timeout=self.global_timeout_s
            )
        except asyncio.TimeoutError:
            raw = [asyncio.TimeoutError() for _ in tasks]

        results: list[SubagentResult] = []
        for task, item in zip(tasks, raw):
            if isinstance(item, BaseException):
                item = SubagentResult(task_id=task.task_id, status="partial", error="global_timeout")
            if not isinstance(item, SubagentResult):
                item = SubagentResult(task_id=task.task_id, status="failed", error="runner_error")
            results.append(item)
        return self._merge(results)

    @staticmethod
    def _merge(results: list[SubagentResult]) -> SubagentMergeResult:
        """确定性融合：**去重 + 保留可操作提示**，不再无条件拼接。

        修复三个实测缺陷（2026-09-15，均有复现证据）：

        1. **同一内容重复拼接**：两个子代理命中同一 chunk、给出相同答案时，
           旧实现输出 `[sub-1] 公司主营财富管理…\\n[sub-2] 公司主营财富管理…`。
           现按 `summary` 归一化去重（相同内容只保留置信度最高的那条）。
           **这与 `ISSUE-20260914-SUMMARIZE-DUPLICATE-FILLER` 同族**——
           该类缺陷在该模块已犯过一次（`CustomerTaskExecutor.summarize` 无条件拼接
           导致占位文案重复），此处不再重犯。

        2. **引用级重复**：不同子代理命中**同一批 chunk**（实测三个近义 query
           共返回 15 条引用、去重后仅 8 条，重复率 47%），答案里会出现同一份知识
           的不同表述。现按 `citations[].chunk_id` 去重，并把去重统计写入
           `data.dedup` 供观测（**不静默丢弃**，让"重复率"可被量化追踪）。

        3. **失败方的可操作提示被整个丢弃**：旧实现只拼接 `status == "success"`
           的 summary，导致 `fact_not_found` 产生的"请补充订单号"这类**可操作提示
           完全丢失**，用户只看到"（部分子任务未完成）"——
           比"重复文案"更糟：**用户不知道要补充什么**。
           现改为：失败/部分失败子任务若带有**面向用户的提示**（非空 summary，
           且不是纯粹的空串），以"待补充"段落单独呈现；纯技术错误
           （如 `subagent_timeout`、`capability_not_mapped`）不进用户可见文案，
           只保留在 `results[].error` 里供排查。

        排序保持确定性：先按置信度降序、再按 task_id，保证同一输入结果稳定。
        """
        ok = [r for r in results if r.status == "success"]
        ok_sorted = sorted(ok, key=lambda r: (-r.confidence, r.task_id))

        # ---- 1. 按内容去重（保留置信度最高者，顺序已保证确定性）----
        parts: list[str] = []
        seen_content: set[str] = set()
        dedup_content = 0
        for r in ok_sorted:
            summary = (r.summary or "").strip()
            if not summary:
                continue
            key = _content_key(summary)
            if key in seen_content:
                dedup_content += 1
                continue
            seen_content.add(key)
            parts.append(f"[{r.task_id}] {summary}")

        # ---- 2. 失败子任务的处置：**区分"可操作提示"与"降级声明"** ----
        #
        # 为什么必须区分（2026-09-15 真实端到端测出来的问题）：
        # 上一版把所有失败子任务的 summary 都当"提示"塞进答案，实测出现：
        #   `[sub-1] 您的总资产为 3,000,000.00 元…`
        #   `当前知识库没有足够依据，建议转人工客服或创建工单。`      ← 紧跟其后
        # 后者是**整体降级声明**，不是"某一项要补充什么"。把它和明确的成功答案并排，
        # 用户会以为**整个回答都失败了**（实际上资产答案是完全正确的）。
        #
        # 分类依据用 **error 码**（结构性），不用文本匹配（脆且易随文案改动失效）：
        #   factual/ambiguous → **可操作提示**：告诉用户"补充什么"就能重试，必须保留；
        #   low_confidence    → **降级声明**：知识库没依据，需要转人工，属全局信号；
        #   其余技术错误      → 不进用户可见文案（只在 results[].error 留痕）。
        _ACTIONABLE_ERRORS = {"fact_not_found", "ambiguous_product"}
        _HANDOFF_ERRORS = {"low_confidence"}

        pending_parts: list[str] = []
        seen_pending: set[str] = set()
        handoff_notices: list[str] = []
        seen_handoff: set[str] = set()
        for r in sorted(results, key=lambda x: x.task_id):
            if r.status == "success":
                continue
            text = (r.summary or "").strip()
            if not text:
                # 无面向用户的文案（如纯超时）→ 仅在 error 中留痕
                continue
            key = _content_key(text)
            err = str(r.error or "")
            if err in _ACTIONABLE_ERRORS:
                if key not in seen_pending:
                    seen_pending.add(key)
                    pending_parts.append(text)
            elif err in _HANDOFF_ERRORS:
                # 降级声明单独收集：只在**没有任何成功答案**时才作为主文案给出。
                # 有成功答案时它不该污染答案——是否转人工由 requires_handoff 字段表达，
                # 而不是塞进正文（正文混排会让正确内容看起来也失败了）。
                if key not in seen_handoff:
                    seen_handoff.add(key)
                    handoff_notices.append(text)
            # 其他 error（subagent_timeout / capability_not_mapped / route_mismatch 等）
            # 属技术细节，不进用户可见文案。

        failed = [r for r in results if r.status == "failed"]
        partial = [r for r in results if r.status == "partial"]
        if ok and (failed or partial):
            overall = "partial"
        elif ok:
            overall = "succeeded"
        elif partial:
            overall = "partial"
        else:
            overall = "failed"

        answer = "\n".join(parts)
        if pending_parts:
            # 明确标注"需补充"而不是只说"部分未完成"——前者可操作，后者只会让用户困惑。
            answer = (answer + "\n" + "\n".join(pending_parts)).strip()
        elif not parts and handoff_notices:
            # 没有任何成功答案 → 降级声明就是唯一能给用户的实质信息，直接给出。
            answer = "\n".join(handoff_notices).strip()
        elif failed or partial:
            # 有成功答案、且失败方只给了降级声明 → **不把降级声明混进正文**
            # （那会让正确内容看起来也失败了）。仅用中性的概括说明收尾。
            note = "（部分子任务未完成）" if overall == "partial" else "（子任务失败）"
            answer = f"{answer}{note}".strip()

        # ---- 3. 引用级/内容级去重统计（观测用，便于持续追踪重复率）----
        seen_chunks: set[str] = set()
        duplicate_chunks = 0
        for r in results:
            for c in (r.data or {}).get("citations") or []:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("chunk_id") or c.get("id") or "")
                if not cid:
                    continue
                if cid in seen_chunks:
                    duplicate_chunks += 1
                else:
                    seen_chunks.add(cid)

        return SubagentMergeResult(
            results=results, overall_status=overall, answer=answer,
            # 降级信号**结构化表达**而不是塞进正文：
            # 正文混排会让"资产答案正确"和"知识库没依据"看起来同样失败；
            # 而调用方（Supervisor）需要的是"要不要转人工"这个**判定**，不是一句文案。
            requires_handoff=bool(handoff_notices),
            dedup={
                "dropped_duplicate_summaries": dedup_content,
                "duplicate_citation_refs": duplicate_chunks,
                "unique_citations": len(seen_chunks),
                "pending_hints": len(pending_parts),
                # 降级声明条数：便于观测"多少个知识子任务无依据"
                "handoff_notices": len(handoff_notices),
            },
        )
