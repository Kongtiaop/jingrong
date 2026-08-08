from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.services.customer_intent import IntentPlan, IntentTask
from app.services.customer_tools import CustomerReadTools

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

class CustomerTaskExecutor:
    def __init__(self, database, max_parallel_tasks: int = 4):
        self.tools = CustomerReadTools(database)
        self.max_parallel_tasks = max_parallel_tasks

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

    async def _execute_one(self, *, customer_id: str, item: IntentTask, results: dict[str, TaskExecutionResult]) -> TaskExecutionResult:
        if item.intent == "greeting":
            return TaskExecutionResult(item.task_id, item.intent, "succeeded", answer="您好，我可以协助查询业务事实、客服知识和人工服务。")
        if item.intent == "human_handoff":
            return TaskExecutionResult(item.task_id, item.intent, "needs_clarification", answer="可以为您转接人工客服。", clarification="是否现在转接人工客服？")
        if item.intent in {"company_info", "company_business", "faq", "workflow", "policy", "risk_public"}:
            tool_name = "search_customer_knowledge"
            result = await self.tools.execute(name=tool_name, customer_id=customer_id, query=item.query, intent=item.intent, entities=item.arguments)
            if not result.success:
                return TaskExecutionResult(item.task_id, item.intent, "needs_clarification", answer=result.answer, data=result.data, error=result.error, clarification="当前资料不足，可以补充问题或转人工客服。")
            return TaskExecutionResult(item.task_id, item.intent, "succeeded", answer=result.answer, data=result.data)
        if item.intent == "business_operation":
            operation = next((name for name in ("赎回", "转账", "下单") if name in item.query), "业务操作")
            return TaskExecutionResult(item.task_id, item.intent, "needs_confirmation", answer=f"可以为您准备“{operation}”业务，但不会在客服对话中直接执行。", data={"operation": operation, "confirmation_required": True, "executed": False}, clarification=f"请通过对应业务页面确认“{operation}”的对象、金额和最终提交。")
        if item.intent == "product_recommendation":
            return TaskExecutionResult(item.task_id, item.intent, "needs_clarification", answer="产品推荐需要结合您的风险承受能力和适当性信息。", clarification="请先完成风险评估，或转人工客服咨询。")
        if item.intent == "unknown":
            return TaskExecutionResult(item.task_id, item.intent, "needs_clarification", answer="我还不能确定您要查询或办理的事项。", clarification="请补充具体对象或选择转人工客服。")
            if not result.success:
                return TaskExecutionResult(item.task_id, item.intent, "needs_clarification", answer=result.answer, data=result.data, error=result.error, clarification="当前没有足够的流程资料，可以转人工客服。")
            return TaskExecutionResult(item.task_id, item.intent, "succeeded", answer=result.answer, data=result.data)
        tool_name = TOOL_BY_INTENT.get(item.intent)
        if not tool_name:
            return TaskExecutionResult(item.task_id, item.intent, "failed", error="no_tool_mapping")
        result = await self.tools.execute(name=tool_name, customer_id=customer_id, query=item.query, entities=item.arguments)
        if not result.success:
            return TaskExecutionResult(item.task_id, item.intent, "needs_clarification" if result.error in {"fact_not_found", "ambiguous_product"} else "failed", error=result.error, clarification="请补充更明确的查询对象或订单/产品信息。" if result.error in {"fact_not_found", "ambiguous_product"} else None)
        return TaskExecutionResult(item.task_id, item.intent, "succeeded", answer=result.answer, data=result.data)

    @staticmethod
    def summarize(results: list[TaskExecutionResult], *, requires_handoff: bool = False) -> tuple[str, str | None, str]:
        """Return stable overall status, first clarification, and aggregate answer."""
        if requires_handoff:
            status = "handoff"
        elif not results:
            status = "needs_clarification"
        else:
            succeeded = sum(item.status == "succeeded" for item in results)
            needs_clarification = any(item.status in {"needs_clarification", "needs_confirmation"} for item in results)
            failed = any(item.status in {"failed", "blocked"} for item in results)
            if succeeded == len(results):
                status = "completed"
            elif succeeded and (needs_clarification or failed):
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


