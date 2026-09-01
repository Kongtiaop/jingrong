from __future__ import annotations

import asyncio
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

    MAX_REACT_ITERATIONS = 3
    MAX_REACT_TOOL_CALLS = 3
    REACT_INTENTS = {"company_info", "company_business", "faq", "workflow", "policy", "risk_public"}

    def __init__(self, database, max_parallel_tasks: int = 4):
        self.database = database
        self.tools = CustomerReadTools(database)
        self.max_parallel_tasks = max_parallel_tasks

    async def _bounded_react_knowledge(self, *, customer_id: str, item: IntentTask) -> TaskExecutionResult:
        """Run a deterministic, read-only retrieve→observe→refine loop without storing private reasoning."""
        attempts: list[dict[str, object]] = []
        query = item.query.strip()
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
                "evidence_status": result.data.get("evidence_status"),
            })
            if result.success:
                final_answer = result.answer
                generation_status = "not_generated"
                citations = result.data.get("citations", [])
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
                    generated = await CustomerRagAnswerService(QwenProvider(get_settings())).generate(query=item.query, result=rag_result)
                    final_answer = generated.answer
                    generation_status = generated.generation_status
                return TaskExecutionResult(
                    item.task_id,
                    item.intent,
                    "succeeded",
                    answer=final_answer,
                    data={**result.data, "generation_status": generation_status, "execution_mode": "bounded_react", "tool_calls": attempts},
                )
            # A low-confidence first retrieval receives exactly one query refinement; other failures
            # must clarify or hand off rather than letting the agent keep probing.
            if iteration == 1 and result.error == "low_confidence" and query != item.query.strip():
                break
            if iteration == 1 and result.error == "low_confidence":
                query = f"{item.query.strip()} 请提供官方流程、适用条件和注意事项"
                continue
            break
        return TaskExecutionResult(
            item.task_id,
            item.intent,
            "needs_clarification",
            answer=result.answer if "result" in locals() else "当前资料不足。",
            data={"execution_mode": "bounded_react", "tool_calls": attempts},
            error=result.error if "result" in locals() else "tool_budget_exhausted",
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

    async def _execute_one(self, *, customer_id: str, item: IntentTask, results: dict[str, TaskExecutionResult]) -> TaskExecutionResult:
        if item.intent == "greeting":
            return TaskExecutionResult(item.task_id, item.intent, "succeeded", answer="您好，我可以协助查询业务事实、客服知识和人工服务。")
        if item.intent == "human_handoff":
            return TaskExecutionResult(item.task_id, item.intent, "needs_clarification", answer="可以为您转接人工客服。", clarification="是否现在转接人工客服？")
        if item.intent in self.REACT_INTENTS:
            return await self._bounded_react_knowledge(customer_id=customer_id, item=item)
        if item.intent == "business_operation":
            is_purchase = any(marker in item.query for marker in ("申购", "购买", "买入", "下单"))
            recommendation_result = next((result for result in results.values() if result.intent == "product_recommendation"), None)
            if is_purchase and recommendation_result is not None:
                recommendation = (recommendation_result.data or {}).get("recommendation", {})
                matches = recommendation.get("matches") or []
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
                recommendation = await suitability.recommend(session, customer_id)
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
