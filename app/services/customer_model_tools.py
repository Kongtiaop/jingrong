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

# search_customer_knowledge 允许的 intent 白名单：**只含公开知识类**。
# product_recommendation / greeting / human_handoff / business_operation 等
# 都有专用确定性路径，绝不由知识检索工具承担（越界会产出错误答案）。
_KNOWLEDGE_INTENTS = frozenset({
    "company_info", "company_business", "faq", "workflow", "policy", "risk_public",
})

# 知识检索无依据时的澄清文案。取值与 graph.py 的 _FALLBACK_CLARIFICATION、
# CustomerTaskExecutor._bounded_react_knowledge 的占位串**逐字一致**，避免同一语义多种文案。
_FALLBACK_CLARIFICATION = "当前资料不足，可以补充问题或转人工客服。"

# 期望路由：工具名 → CustomerQueryRouter 的 route，用于校验工具与意图一致
_EXPECTED_ROUTE = {
    "get_my_workorder_status": "workorder_status",
    "get_my_order_status": "order_status",
    "get_my_asset_summary": "asset_summary",
    "get_my_risk_restriction": "risk_restriction",
    "get_product_status": "product_status",
}

# 意图 → 覆盖它所需的工具名（用于"未覆盖意图"兜底判定）。
# 知识类意图（company_info/policy/risk_public/...）统一由 search_customer_knowledge 覆盖。
_INTENT_TO_TOOL = {
    "workorder_status": "get_my_workorder_status",
    "order_status": "get_my_order_status",
    "asset_summary": "get_my_asset_summary",
    "risk_restriction": "get_my_risk_restriction",
    "product_status": "get_product_status",
    "company_info": "search_customer_knowledge",
    "company_business": "search_customer_knowledge",
    "faq": "search_customer_knowledge",
    "workflow": "search_customer_knowledge",
    "policy": "search_customer_knowledge",
    "risk_public": "search_customer_knowledge",
    "risk_explanation": "search_customer_knowledge",
}


def _tools_for_intents(planned_intents: list[str] | None) -> set[str]:
    """计划意图所映射的工具集合（模型的**可调用范围**）。

    用途：约束模型不得调用计划外的工具（见 ISSUE-20260914-MODEL-CALLS-OFFPLAN-TOOL）。
    返回空集表示"无约束"（未传计划时不限制），保持向后兼容。
    """
    if not planned_intents:
        return set()
    return {tool for intent in planned_intents if (tool := _INTENT_TO_TOOL.get(intent))}


def _uncovered_intents(planned_intents: list[str] | None,
                       results: list[dict]) -> list[str]:
    """计划中的意图里，哪些尚未被任何成功结果覆盖。

    判定依据：已成功结果里的 `intent` 字段（**不**用 `data.tool` 兜底）。

    为什么不能用"工具是否被调用过"兜底（ISSUE-20260914-COVERAGE-FALSE-POSITIVE）：
    多个知识类意图共用 `search_customer_knowledge`，若按工具判覆盖，
    则"只检索了 company_info"会被误判为"company_business 也已覆盖"。
    实测该 query 模型 4 轮全用 intent=company_info，company_business **从未检索**，
    但旧逻辑返回 pending=[]（判定已覆盖），导致图少一个 task 且兜底不触发。
    """
    if not planned_intents:
        return []
    covered_intents: set[str] = set()
    for item in results:
        if item.get("intent"):
            covered_intents.add(str(item["intent"]))

    pending: list[str] = []
    for intent in planned_intents:
        if intent in covered_intents:
            continue
        if _INTENT_TO_TOOL.get(intent) is None:
            # 未知意图（如 greeting/human_handoff/business_operation）不由只读工具覆盖，跳过
            continue
        pending.append(intent)
    return pending


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
    reasoning: str = ""          # 模型自述的调用理由（审计/排查用，不参与控制流）


@dataclass
class ModelToolOutcome:
    """模型自主工具调度的最终产物。"""

    rounds: list[ToolRound] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)   # 供上游组装的 TaskExecutionResult 形态
    stopped_reason: str = "no_tool_call"                # no_tool_call / budget_exhausted / done / model_unavailable
    tool_names: list[str] = field(default_factory=list)  # 实际调用过的工具（按序）
    # 每轮模型的原始决策（含自述理由），供排查"为什么多调/调错工具"。
    # 与 rounds 的区别：rounds 只记**已执行**的调用，decisions 也记**被拦下**的（越界/重复）。
    decisions: list[dict] = field(default_factory=list)

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
    "5. **多意图问题要逐个覆盖**：例如'我有没有风险限制'既需要查账户的事实限制，也可能需要解释风险制度，"
    "两类依据都应取得后再结束。\n"
    "6. 用户问题已被前面的工具结果完整覆盖时，直接结束（不再调用）。\n"
    "7. **工具调用失败时留在同一意图内**：换参数或改写 query 重试；"
    "**不要改调与用户问题无关的其他工具**（例如查订单失败后不要改查工单或知识库），"
    "反复失败就停止，交由下游提示用户补充信息或转人工。\n"
    "8. 不要臆造工具名与参数。\n"
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
        # 模型缺省取配置的 qwen_tool_model（实测 qwen-flash 最快且工具选择 8/8 匹配）。
        if model is None:
            try:
                from app.core.settings import get_settings
                model = getattr(get_settings(), "qwen_tool_model", None)
            except Exception:  # noqa: BLE001 - 配置不可读时回退 provider 默认
                model = None
        self.model = model

    async def _audit(self, *, customer_id: str, conversation_id: str, round_index: int,
                     tool: str, arguments: dict, success: bool, error: str | None,
                     evidence_status: str | None, citations: int,
                     reasoning: str | None = None) -> None:
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
                        "reasoning": reasoning or "",
                    }, ensure_ascii=False),
                ))
                await session.commit()
        except Exception:  # noqa: BLE001 - 审计失败不阻塞
            pass

    async def _fill_uncovered(self, *, pending: list[str], customer_id: str,
                              conversation_id: str, query: str, round_index: int,
                              outcome: ModelToolOutcome) -> bool:
        """规则层确定性补齐缺失意图的工具调用。

        为什么不让模型再试一次：实测明确告知"以下方面尚未取得依据"后，模型连续 3 轮仍返回 None
        （见 ISSUE-20260914-MODEL-EARLY-STOP-NEEDS-RULE-BACKSTOP）。**提示词无法强制模型行动**，
        故这里按映射直接执行——模型负责"主路径怎么查"，规则负责"别漏"。
        返回是否真的补齐了至少一项（True 表示可以继续循环让模型再判断）。
        """
        filled_any = False
        for intent in pending:
            tool_name = _INTENT_TO_TOOL.get(intent)
            if tool_name is None or tool_name not in _ALLOWED_TOOL_NAMES:
                continue
            result = await self.tools.execute(
                name=tool_name, customer_id=customer_id, query=query,
                intent=intent if tool_name == "search_customer_knowledge" else None,
            )
            data = result.data or {}
            outcome.rounds.append(ToolRound(
                round_index=round_index, tool=tool_name,
                arguments={"query": query, "intent": intent, "filled_by": "rule_backstop"},
                success=result.success, error=result.error,
                evidence_status=data.get("evidence_status"),
                citations=len(data.get("citations") or []),
            ))
            outcome.tool_names.append(tool_name)
            await self._audit(
                customer_id=customer_id, conversation_id=conversation_id,
                round_index=round_index, tool=tool_name,
                arguments={"intent": intent, "filled_by": "rule_backstop"},
                success=result.success, error=result.error,
                evidence_status=data.get("evidence_status"),
                citations=len(data.get("citations") or []),
            )
            if result.success:
                outcome.results.append({
                    "task_id": f"rule-backstop-{intent}",
                    "intent": intent,
                    "status": "succeeded",
                    "answer": result.answer,
                    "data": {**data, "execution_mode": "rule_backstop", "tool": tool_name},
                    "error": None,
                    "clarification": None,
                })
                filled_any = True
        return filled_any

    async def run(self, *, customer_id: str, conversation_id: str, query: str,
                  planned_intents: list[str] | None = None) -> ModelToolOutcome:
        """让模型自主选工具并循环，直到模型不再调用或预算用尽。

        planned_intents：规则规划器已识别出的意图列表（可选）。作为**提示**告知模型
        "这个问题需要覆盖哪些方面"，避免多意图 query 只查到一半就被模型判定"够了"。
        它不是硬约束——模型仍可自主决定调什么、调几次。
        """
        outcome = ModelToolOutcome()
        context_lines: list[str] = []
        seen_calls: set[str] = set()   # 已执行过的 (工具, 参数) 组合，用于重复调用闸门
        planned_block = ""
        if planned_intents:
            planned_block = (
                "\n\n该问题经规则分析需要覆盖以下方面（请确保都取得依据后再结束）：\n"
                + "\n".join(f"- {item}" for item in planned_intents)
            )

        for round_index in range(1, self.max_rounds + 1):
            user_block = f"用户问题：{query}{planned_block}"
            if context_lines:
                user_block += "\n\n已获得的工具结果：\n" + "\n".join(context_lines)
                user_block += "\n\n请判断上述方面是否都已覆盖；若仍有未覆盖的，继续调用相应工具。"

            decision = await self.provider.request_tool_call(
                system=_TOOL_LOOP_SYSTEM, user=user_block,
                tools=CUSTOMER_READ_TOOL_SCHEMAS, model=self.model,
            )
            if not decision:
                # 模型主动停止。但若规则规划器识别出的意图**尚未被覆盖**，则不能就此结束——
                # 实测："我有没有风险限制" 命中 risk_restriction(事实) + risk_public(知识)，
                # 模型拿到事实后即停止，用户便拿不到制度解释（图比管线少一个 task）。
                #
                # 关键教训（两次踩坑）：**再次追问模型是无效的**——实测把"以下方面尚未取得依据，
                # 请调用相应工具补齐"明确告诉模型后，它连续 3 轮仍坚持返回 None。
                # 提示词无法强制模型行动，故此处改为**规则层确定性补齐**：
                # 直接按 _INTENT_TO_TOOL 映射执行缺失的工具，不再征求模型同意。
                # 注意：这只作用于"模型已经停止"的收尾阶段；主路径仍是模型自主选择。
                pending = _uncovered_intents(planned_intents, outcome.results)
                if pending and round_index < self.max_rounds:
                    filled = await self._fill_uncovered(
                        pending=pending, customer_id=customer_id,
                        conversation_id=conversation_id, query=query,
                        round_index=round_index, outcome=outcome,
                    )
                    if filled:
                        continue
                outcome.stopped_reason = (
                    "model_unavailable" if self.provider is None or self.provider._client is None
                    else ("no_tool_call" if not pending else "intent_uncovered")
                )
                break

            tool_name = str(decision.get("name") or "").strip()
            arguments = decision.get("arguments") or {}
            # 模型发起 tool_call 时同时给出的文本（"为什么调这个工具"）。
            # 仅用于留痕：判断"模型多调一个工具"是**有意补充**还是**误判**，必须看它的自述理由，
            # 否则只能凭结果反推（此前正是如此，导致同一现象反复猜测）。
            reasoning = str(decision.get("reasoning") or "").strip()
            outcome.decisions.append({
                "round": round_index, "tool": tool_name, "arguments": arguments,
                "reasoning": reasoning, "outcome": "pending",
            })

            # 工具范围约束：模型只能调用**计划意图对应**的工具。
            # 为什么需要（ISSUE-20260914-MODEL-CALLS-OFFPLAN-TOOL）：
            # 实测"我的工单进展如何"（计划只有 workorder_status），模型拿到工单状态后
            # **自行多调** `explain_workorder_status` 去"解释状态"，该调用失败
            # （知识库无流程资料）→ 污染整体状态 → 用户明明已拿到工单状态，
            # 却被回复"当前知识库没有足够依据，已转人工"（自相矛盾）。
            # 差异 = 多一次调用 + 误转人工 + 答案自相矛盾。
            # 关键认识：`explain_*` 这类工具本应由规划器通过**多意图 + depends_on** 触发
            # （见 CustomerTaskExecutor._execute_one:243），不该由模型临时决定。
            # 故此处约束"在计划范围内自主"——模型仍可自由选工具/改参数/决定续调，
            # 但不能越出计划意图所映射的工具集。
            allowed_tools = _tools_for_intents(planned_intents)
            if allowed_tools and tool_name not in allowed_tools:
                outcome.rounds.append(ToolRound(
                    round_index=round_index, tool=tool_name, arguments=arguments,
                    success=False, error="tool_out_of_plan_scope"))
                await self._audit(customer_id=customer_id, conversation_id=conversation_id,
                                  round_index=round_index, tool=tool_name, arguments=arguments,
                                  success=False, error="tool_out_of_plan_scope",
                                  evidence_status=None, citations=0, reasoning=reasoning)
                context_lines.append(
                    f"[{tool_name}] 该工具不在本次计划范围内（计划仅涉及：{'、'.join(planned_intents or [])}），"
                    "请改用计划内的工具或直接结束。"
                )
                # 记录越界原因并结束循环。
                # 注意不能写 `outcome.stopped_reason or "..."`：ModelToolOutcome 的
                # stopped_reason 默认值是 "no_tool_call"（非空），`or` 会永远保留默认值，
                # 导致真实原因丢失（这个坑被新测试抓到）。
                outcome.stopped_reason = "tool_out_of_plan_scope"
                outcome.decisions[-1]["outcome"] = "rejected_off_plan"
                break

            # 重复调用闸门：完全相同的 (工具, 参数) 重复调用在确定性工具上必然得到相同结果，
            # 继续下去只是浪费预算与配额。实测"我的订单 ORD-123456 状态"连续 4 轮传入完全相同的
            # 参数，4 次都失败、无任何结果（ISSUE-20260914-TOOL-FAILURE-NOT-FEED-BACK）。
            call_key = f"{tool_name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
            if call_key in seen_calls:
                outcome.rounds.append(ToolRound(
                    round_index=round_index, tool=tool_name, arguments=arguments,
                    success=False, error="duplicate_call_skipped"))
                await self._audit(customer_id=customer_id, conversation_id=conversation_id,
                                  round_index=round_index, tool=tool_name, arguments=arguments,
                                  success=False, error="duplicate_call_skipped",
                                  evidence_status=None, citations=0)
                # 明确告知模型"该调用已试过"，并结束——再循环不会有新信息
                context_lines.append(
                    f"[{tool_name}] 该调用（参数={arguments}）本轮已执行过且结果相同，不再重复。"
                )
                outcome.stopped_reason = "duplicate_call_skipped"
                outcome.decisions[-1]["outcome"] = "rejected_duplicate"
                break
            seen_calls.add(call_key)

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
                outcome.decisions[-1]["outcome"] = "rejected_not_allowed"
                break

            # 客户身份由服务端注入，模型无法指定查谁（防越权）
            tool_query = str(arguments.get("query") or query)
            intent = arguments.get("intent")
            # intent 硬门禁：search_customer_knowledge 只处理**公开知识类**意图。
            # 防止模型把 product_recommendation / greeting / human_handoff 等
            # "有专用确定性路径"的意图塞进知识检索——那会让 RAG 的短路占位串被当成答案
            # （ISSUE-20260914-MODEL-TOOL-MISROUTES-NON-RAG-INTENTS）。
            if tool_name == "search_customer_knowledge" and intent not in _KNOWLEDGE_INTENTS:
                intent = "faq"
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
                reasoning=reasoning,
            )
            outcome.rounds.append(round_record)
            outcome.tool_names.append(tool_name)
            outcome.decisions[-1]["outcome"] = "executed"
            outcome.decisions[-1]["success"] = result.success
            outcome.decisions[-1]["error"] = result.error
            await self._audit(customer_id=customer_id, conversation_id=conversation_id,
                              round_index=round_index, tool=tool_name, arguments=arguments,
                              success=result.success, error=result.error,
                              evidence_status=round_record.evidence_status,
                              citations=round_record.citations, reasoning=reasoning)

            if result.success:
                # 意图标注：知识类工具的真实类别由模型传入的 intent 参数决定（company_info/policy/...），
                # 若固定用工具名会退化成 "search_customer_knowledge"，丢失多意图语义。
                intent_label = (
                    str(intent) if tool_name == "search_customer_knowledge" and intent
                    else _EXPECTED_ROUTE.get(tool_name, tool_name)
                )
                # 去重：同一意图若已成功取到结果，后续重复调用（模型认为"还需补充"而再调一次）
                # 不再追加新结果，只更新已有条目的引用数——否则会出现两个同 intent 的 task，
                # 下游 summarize 会拼接出重复答案（ISSUE-20260914-MODEL-TOOL-DUPLICATE-INTENT-RESULT）。
                existing = next((r for r in outcome.results if r["intent"] == intent_label), None)
                if existing is not None:
                    existing["answer"] = result.answer or existing["answer"]
                    existing["data"] = {**existing["data"], **data}
                    context_lines.append(f"[{tool_name}] {result.answer[:600]}")
                    continue
                outcome.results.append({
                    "task_id": f"model-tool-{round_index}",
                    "intent": intent_label,
                    "status": "succeeded",
                    "answer": result.answer,
                    "data": {**data, "execution_mode": "model_tool_selection", "tool": tool_name},
                    "error": None,
                    "clarification": None,
                })
                # 结果一律回灌给模型，由**模型**决定是否还需要另一个工具。
                # 此前"事实类工具一次成功即 break"是错误设计：多意图 query（如"我有没有风险限制"
                # 同时命中 risk_restriction 事实 + risk_public 知识）会让模型拿不到第二个工具的机会，
                # 导致 task 丢失（实测 graph 只剩 1 个 task，管线有 2 个）。
                context_lines.append(f"[{tool_name}] {result.answer[:600]}")
            else:
                # 失败也要把**具体原因 + 已试过的参数**回灌，否则模型只能重复同样的调用。
                # 实测："我的订单 ORD-123456 状态" 模型 4 轮都传同一 query、4 次全失败，
                # stopped_reason=budget_exhausted 且无任何结果
                # （ISSUE-20260914-TOOL-FAILURE-NOT-FED-BACK）。
                # 失败反馈必须**结构化且不误导**。教训（ISSUE-20260914-FAILURE-HINT-MISLEADS-TOOL-SWITCH）：
                # 旧文案"请换一个更明确的查询对象，或不要再重复相同参数"被模型理解成"换个工具试试"，
                # 于是实测链路变成：order → workorder → knowledge → knowledge，
                # 产出 3 个无关 task（pipeline 只有 1 个）。**失败时应留在同一意图内调参/改写，
                # 而不是换用其他意图的工具。**
                failure_hint = {
                    "fact_not_found": "未查到该对象（单号可能不存在或不属于当前客户）。"
                                      "**请勿改调其他工具**；可在本工具内换一个更明确的查询参数，"
                                      "或直接停止（让下游提示用户补充/转人工）。",
                    "ambiguous_product": "产品不明确。**请勿改调其他工具**；请补充完整产品名称，或停止。",
                    "low_confidence": "知识库无匹配依据。可在本摘要内改写 query 换种说法再试一次；"
                                      "**若已改写两次以上仍失败请立即停止**，不要改调其他工具。",
                    "route_mismatch": "该工具与查询意图不匹配，请改回计划内的工具。",
                    "tool_not_allowed": "该工具不允许调用，请停止。",
                }.get(str(result.error or ""),
                      "请在本工具内调整参数或改写 query；**不要改调与计划意图无关的其他工具**；"
                      "若反复失败请停止。")
                context_lines.append(
                    f"[{tool_name}] 调用失败（error={result.error}，参数={arguments}）→ {failure_hint}"
                )
                # 失败结果也必须**保留为 task 结果**（而非只回灌给模型就丢弃）。
                # 原因（ISSUE-20260914-TOOL-FAILURE-DROPPED）：
                # 管线把 `fact_not_found` / `ambiguous_product` 映射为 **needs_clarification**
                # （"请补充更明确的查询对象或订单/产品信息"），而不是失败或转人工。
                # 若这里丢弃失败结果，`outcome.results` 为空 → `_route_after_model_tool`
                # 回退到确定性 ReAct（对 order_status 走知识检索）→ 检索不到 → low_confidence
                # → 误转人工。实测"我的订单 ORD-123456 状态"图 requires_handoff=True 而管线 False。
                intent_label = (
                    str(intent) if tool_name == "search_customer_knowledge" and intent
                    else _EXPECTED_ROUTE.get(tool_name, tool_name)
                )
                # 与管线 CustomerTaskExecutor 的语义对齐（`_execute_one` 与
                # `_bounded_react_knowledge` 两处）：
                #   fact_not_found / ambiguous_product → needs_clarification（请用户补充）
                #   low_confidence                     → needs_clarification（知识库无依据，转人工）
                #   其他                               → failed
                # 此前只映射了 fact_not_found/ambiguous_product，导致**知识检索的 low_confidence
                # 落到 failed**，与管线的 needs_clarification 不一致
                # （ISSUE-20260914-FAILED-VS-CLARIFICATION-MISMATCH：实测"开户流程是怎样的"
                #   graph=task failed / pipeline=needs_clarification，两边都已转人工、仅状态标记不同）。
                error_code = str(result.error or "")
                needs_clarification = error_code in {"fact_not_found", "ambiguous_product", "low_confidence"}
                if not any(r["intent"] == intent_label for r in outcome.results):
                    outcome.results.append({
                        "task_id": f"model-tool-{round_index}",
                        "intent": intent_label,
                        "status": "needs_clarification" if needs_clarification else "failed",
                        "answer": result.answer or "",
                        "data": {**data, "execution_mode": "model_tool_selection", "tool": tool_name},
                        "error": result.error,
                        "clarification": (
                            ("请补充更明确的查询对象或订单/产品信息。"
                             if error_code in {"fact_not_found", "ambiguous_product"}
                             else _FALLBACK_CLARIFICATION)
                            if needs_clarification else None
                        ),
                    })
                if round_index == self.max_rounds:
                    outcome.stopped_reason = "budget_exhausted"
        else:
            outcome.stopped_reason = "budget_exhausted"

        # ---- 收尾兜底：所有退出路径都必须检查"计划意图是否漏覆盖" ----
        # ISSUE-20260914-COVERAGE-BACKSTOP-SKIPPED-ON-BUDGET：
        # `_fill_uncovered` 此前**只挂在"模型主动停止"分支**（`if not decision`）。
        # 若模型不主动停、一路调到预算耗尽，就会走 `budget_exhausted` 出口，
        # 而该出口**完全不检查** `_uncovered_intents` —— 兜底被静默跳过。
        # 实测后果：`你们公司的主营业务是什么` 计划有 company_info + company_business 两个 task，
        # 模型 4 轮全用 intent=company_info，`company_business` 从未检索，
        # 图最终只产出 1 个 task（管线 2 个），而 `overall_status` 仍报 `completed`
        # —— 用户少拿一路答案却看不出异常。
        # 兜底是"别漏"的**结构性保证**，不能依赖"模型恰好主动停下"这种偶然条件。
        if not outcome.stopped_reason.startswith("tool_out_of_plan_scope"):
            remaining = _uncovered_intents(planned_intents, outcome.results)
            if remaining:
                filled = await self._fill_uncovered(
                    pending=remaining, customer_id=customer_id,
                    conversation_id=conversation_id, query=query,
                    round_index=self.max_rounds + 1, outcome=outcome,
                )
                if filled and not outcome.stopped_reason.startswith("tool_out_of_plan_scope"):
                    # 兜底确实补了东西 → 覆盖退出原因，让"主循环是被兜底收的尾"可见。
                    # 不限于 budget_exhausted：重复调用闸门（duplicate_call_skipped）同样属于
                    # "模型没主动停但循环结束"，那条路径以前也完全跳过兜底。
                    outcome.stopped_reason = "rule_backstop_filled"

        return outcome
