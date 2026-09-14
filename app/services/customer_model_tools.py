# -*- coding: utf-8 -*-
"""只读查询的**模型自主工具调度**（function calling）。

设计目标（与用户明确的范围一致）：
- **只读查询交给模型自主决定调哪个工具、传什么参数、是否再调一轮**。
- **资金/风控路径保持确定性**——本模块只暴露只读工具，写操作永不进入工具集。

安全边界（硬约束，非提示词约束）：
1. 工具白名单：`CUSTOMER_READ_TOOL_SCHEMAS` 只含只读查询工具；`execute` 前再次校验名字在
   `_ALLOWED_TOOL_NAMES` 内，模型即使编造 `create_order` 也只会拿到 `tool_not_allowed`。
2. 客户身份由**服务端注入**（`customer_id`），模型**不能**通过参数指定查谁——
   防越权读他人数据（SubjectScope 仍由工具内部强制）。
3. 有界循环：`max_rounds` 上限 + 每轮记账，超限即止，杜绝无限工具轮。
4. 全程审计：每轮工具调用落 `AuditLog(action="customer_agent.tool_call")`。
5. 无工具结果或模型不调工具 → 降级为现有确定性路径，绝不空转。

与既有实现的区别：`graph.py` 的 `react_step_node` 工具名**硬编码**为
`search_customer_knowledge`、改写用 2 条固定模板硬试；本模块把"选哪个工具 + 怎么改写"
交给模型，是真正的 Agentic 工具调度。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.common.logging.config import get_logger

logger = get_logger(__name__)


# ---- 只读工具 schema（OpenAI function-calling 格式；直接被 qwen.request_tool_call 使用）----
CUSTOMER_READ_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_customer_knowledge",
            "description": (
                "检索客服知识库，回答公司业务、服务流程、政策制度、常见问题等公开知识类问题。"
                "当用户问'是什么/怎么办/有什么规定'且不涉及他的个人账户数据时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索用的问题，可改写为更贴合知识库的表述"},
                    "intent": {
                        "type": "string",
                        "enum": ["company_info", "company_business", "faq", "workflow", "policy", "risk_public"],
                        "description": "知识类别，决定检索范围",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_asset_summary",
            "description": "查询当前客户的资产总览（总资产、持仓数量等）。用户问'我的持仓/我的资产/我有多少钱'时使用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_order_status",
            "description": "查询当前客户的订单状态。用户问'我的订单/某笔订单进展'时使用（可从话术中带出订单号）。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "含订单号或描述的原话"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_workorder_status",
            "description": "查询当前客户的工单进展。用户问'我的工单/我的问题处理到哪了'时使用。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "含工单号或描述的原话"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_risk_restriction",
            "description": "查询当前客户是否被风控限制（账户限制、冻结、交易权限等）。用户问'我有没有风险限制/能不能交易'时使用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_status",
            "description": "查询某产品是否在售及其基本状态。用户点名某个产品问'还在售吗/什么状态'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "包含产品名称的原话"},
                    "product_name": {"type": "string", "description": "产品名（若已从话术中识别）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_order_status",
            "description": "在已知客户订单状态的基础上，解释该状态对应的流程与后续操作。需先调用 get_my_order_status。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_workorder_status",
            "description": "在已知客户工单状态的基础上，解释该工单类型的处理流程与时效。需先调用 get_my_workorder_status。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": [],
            },
        },
    },
]

# 硬白名单：与 CustomerReadTools.execute 内部 allowed 集合保持一致。
# 双重校验——即使模型返回未声明/写操作工具名，也会在此被拒。
_ALLOWED_TOOL_NAMES = frozenset(item["function"]["name"] for item in CUSTOMER_READ_TOOL_SCHEMAS)

# 期望路由：工具名 → CustomerQueryRouter 的 route，用于校验工具与意图一致
_EXPECTED_ROUTE = {
    "get_my_workorder_status": "workorder_status",
    "get_my_order_status": "order_status",
    "get_my_asset_summary": "asset_summary",
    "get_my_risk_restriction": "risk_restriction",
    "get_product_status": "product_status",
}


@dataclass
class ToolRound:
    """一轮工具调用的记账（用于审计与观测）。"""

    round_index: int
    tool: str
    arguments: dict
    success: bool
    error: str | None = None
    evidence_status: str | None = None
    citations: int = 0


@dataclass
class ModelToolOutcome:
    """模型自主工具调度的最终产物。"""

    rounds: list[ToolRound] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)   # 供上游组装的 TaskExecutionResult 形态
    stopped_reason: str = "no_tool_call"                # no_tool_call / budget_exhausted / done / model_unavailable
    tool_names: list[str] = field(default_factory=list)  # 实际调用过的工具（按序）

    @property
    def called_any(self) -> bool:
        return bool(self.rounds)


_TOOL_LOOP_SYSTEM = (
    "你是财富管理客服的只读查询调度器。基于用户问题，决定调用哪个只读工具来获取事实依据。\n"
    "规则：\n"
    "1. 只调用提供的只读查询工具；绝不尝试任何写操作（下单、转账、赎回、修改数据）。\n"
    "2. 一次只调用一个最合适的工具；若信息已足够回答，就不要继续调用。\n"
    "3. 涉及客户个人数据（资产/订单/工单/风险限制）时必须用对应工具取真实数据，不得凭记忆作答。\n"
    "4. 知识类问题（公司业务/流程/政策/FAQ）用 search_customer_knowledge，并把 query 改写为更贴合知识库的表述。\n"
    "5. 用户问题已被前面的工具结果覆盖时，直接结束（不再调用）。\n"
    "6. 不要臆造工具名与参数。\n"
)


class CustomerModelToolLoop:
    """只读查询的模型自主工具调度（有界循环 + 硬白名单 + 全程审计）。

    用法：
        loop = CustomerModelToolLoop(provider=QwenProvider(settings), tools=CustomerReadTools(db), database=db)
        outcome = await loop.run(customer_id=..., conversation_id=..., query=...)
    """

    def __init__(self, *, provider, tools, database, max_rounds: int = 4, model: str | None = None) -> None:
        self.provider = provider
        self.tools = tools
        self.database = database
        self.max_rounds = max(1, int(max_rounds))
        self.model = model

    async def _audit(self, *, customer_id: str, conversation_id: str, round_index: int,
                     tool: str, arguments: dict, success: bool, error: str | None,
                     evidence_status: str | None, citations: int) -> None:
        """每轮工具调用落审计。失败不影响主流程。"""
        try:
            from app.models.audit import AuditLog
            async with self.database.session_factory() as session:
                session.add(AuditLog(
                    id=str(uuid4()), actor_user_id=customer_id,
                    action="customer_agent.tool_call",
                    resource_type="customer_service_conversation",
                    resource_id=conversation_id,
                    detail=json.dumps({
                        "round": round_index, "tool": tool, "arguments": arguments,
                        "success": success, "error": error,
                        "evidence_status": evidence_status, "citations": citations,
                        "read_only": True, "selection": "model",
                    }, ensure_ascii=False),
                ))
                await session.commit()
        except Exception:  # noqa: BLE001 - 审计失败不阻塞
            pass

    async def run(self, *, customer_id: str, conversation_id: str, query: str) -> ModelToolOutcome:
        """让模型自主选工具并循环，直到模型不再调用或预算用尽。"""
        outcome = ModelToolOutcome()
        context_lines: list[str] = []

        for round_index in range(1, self.max_rounds + 1):
            user_block = f"用户问题：{query}"
            if context_lines:
                user_block += "\n\n已获得的工具结果：\n" + "\n".join(context_lines)

            decision = await self.provider.request_tool_call(
                system=_TOOL_LOOP_SYSTEM, user=user_block,
                tools=CUSTOMER_READ_TOOL_SCHEMAS, model=self.model,
            )
            if not decision:
                outcome.stopped_reason = (
                    "model_unavailable" if self.provider is None or self.provider._client is None
                    else "no_tool_call"
                )
                break

            tool_name = str(decision.get("name") or "").strip()
            arguments = decision.get("arguments") or {}

            # 硬白名单：模型编造工具名（或试图写操作）在此被拒
            if tool_name not in _ALLOWED_TOOL_NAMES:
                outcome.rounds.append(ToolRound(
                    round_index=round_index, tool=tool_name, arguments=arguments,
                    success=False, error="tool_not_allowed"))
                await self._audit(customer_id=customer_id, conversation_id=conversation_id,
                                  round_index=round_index, tool=tool_name, arguments=arguments,
                                  success=False, error="tool_not_allowed",
                                  evidence_status=None, citations=0)
                outcome.stopped_reason = "tool_not_allowed"
                break

            # customer_id 由服务端注入，模型无法指定查谁（防越权）
            tool_query = str(arguments.get("query") or query)
            intent = arguments.get("intent")
            entities = {k: v for k, v in arguments.items() if k == "product_name" and v}

            result = await self.tools.execute(
                name=tool_name, customer_id=customer_id, query=tool_query,
                intent=intent, entities=entities or None,
            )
            data = result.data or {}
            round_record = ToolRound(
                round_index=round_index, tool=tool_name, arguments=arguments,
                success=result.success, error=result.error,
                evidence_status=data.get("evidence_status"),
                citations=len(data.get("citations") or []),
            )
            outcome.rounds.append(round_record)
            outcome.tool_names.append(tool_name)
            await self._audit(customer_id=customer_id, conversation_id=conversation_id,
                              round_index=round_index, tool=tool_name, arguments=arguments,
                              success=result.success, error=result.error,
                              evidence_status=round_record.evidence_status,
                              citations=round_record.citations)

            if result.success:
                outcome.results.append({
                    "task_id": f"model-tool-{round_index}",
                    "intent": _EXPECTED_ROUTE.get(tool_name, tool_name),
                    "status": "succeeded",
                    "answer": result.answer,
                    "data": {**data, "execution_mode": "model_tool_selection", "tool": tool_name},
                    "error": None,
                    "clarification": None,
                })
                # 事实类工具（客户数据）拿到结果即可结束；知识检索允许继续补充
                if tool_name != "search_customer_knowledge":
                    outcome.stopped_reason = "done"
                    break
                context_lines.append(f"[{tool_name}] {result.answer[:600]}")
            else:
                context_lines.append(f"[{tool_name}] 未取得结果（{result.error}）")
                if round_index == self.max_rounds:
                    outcome.stopped_reason = "budget_exhausted"
        else:
            outcome.stopped_reason = "budget_exhausted"

        return outcome
