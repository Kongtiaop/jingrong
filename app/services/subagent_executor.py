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
        ok = [r for r in results if r.status == "success"]
        ok_sorted = sorted(ok, key=lambda r: (-r.confidence, r.task_id))
        parts = [f"[{r.task_id}] {r.summary}" for r in ok_sorted if (r.summary or "").strip()]
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
        if failed or partial:
            note = "（部分子任务未完成）" if overall == "partial" else "（子任务失败）"
            answer = f"{answer}{note}".strip()
        return SubagentMergeResult(results=results, overall_status=overall, answer=answer)
