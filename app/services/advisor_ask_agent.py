# -*- coding: utf-8 -*-
"""advisor-ask 有界工具调用 agent（范围仅投顾段）。

把"库里真实在售产品/适当性"喂回给模型，替换纯单次非工具的生成：
1) 先做 context（客户画像）;
2) 有界 ≤3 轮：非流式 tool 轮，让模型决定是否调 find_products / product_suitability；
   命中即用 AdvisorReadTools(只读)执行，把库内真实 facts 以文本附加进 user 上下文；
3) 到无工具请求时，用 stream_text 以最终 user 上下文流式生成回答。

护栏：
- 只读白名单（ADVISOR_TOOL_ALLOWLIST）
- 轮数上限：ToolRoundLimit=3，超限直接进生成
- 工具名+参数落 AuditLog(action="advisor_agent.tool_call")，只含结构化元数据
- 降级：工具/LLM 异常 → 用已知客户事实(纯上下文)回答，不抛崩主流程
"""
from __future__ import annotations

from uuid import uuid4
import json

from app.services.advisor_tools import AdvisorReadTools, ADVISOR_TOOL_ALLOWLIST, ADVISOR_TOOL_SCHEMAS


class AdvisorBoundedLoop:
    ToolRoundLimit = 3

    def __init__(self, provider, tools: AdvisorReadTools, database) -> None:
        self.provider = provider
        self.tools = tools
        self.database = database

    async def _audit_tool(self, conversation_id: str, actor_user_id: str, name: str, arguments: dict, had_result: bool) -> None:
        try:
            async with self.database.session_factory() as session:
                from app.models.audit import AuditLog
                session.add(AuditLog(
                    id=str(uuid4()), actor_user_id=actor_user_id,
                    action="advisor_agent.tool_call", resource_type="customer_conversation",
                    resource_id=conversation_id,
                    detail=json.dumps({"tool": name, "arguments": arguments, "had_result": had_result}, ensure_ascii=False),
                ))
                await session.commit()
        except Exception:  # noqa: BLE001 - 审计失败不影响主流程
            pass

    async def run_to_text_user(self, *, conversation_id: str, actor_user_id: str, customer_id: str, context_user: str, system: str):
        """执行工具轮，返回 (最终 user 上下文, 是否有工具命中)。"""
        user = context_user
        had_tool = False
        rounds = 0
        while rounds < self.ToolRoundLimit:
            decision = await self.provider.request_tool_call(
                system=system,
                user=user,
                tools=ADVISOR_TOOL_SCHEMAS,
            )
            if decision is None:
                break
            name = decision.get("name") or ""
            arguments = decision.get("arguments") or {}
            if name not in ADVISOR_TOOL_ALLOWLIST:
                # 不认识的工具：停掉工具轮，避免无限
                break
            rounds += 1
            result = "（无结果）"
            ok = False
            try:
                async with self.database.session_factory() as session:
                    _, text = await self.tools.execute_tool(session, name=name, arguments=arguments, customer_id=customer_id)
                    if text:
                        result = text
                        ok = True
            except Exception:  # noqa: BLE001 - 工具执行异常仅记录并降级
                result = "（工具执行失败）"
            await self._audit_tool(conversation_id, actor_user_id, name, arguments, ok)
            if ok:
                had_tool = True
                user = user + f"\n\n##### 工具[{name}]返回（库内真实，以下为权威）：\n{result}"
        return user, had_tool
