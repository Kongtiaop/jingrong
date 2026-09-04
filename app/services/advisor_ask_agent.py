# -*- coding: utf-8 -*-
"""advisor-ask 库内真实接地（范围仅投顾段，确定性一次 DB，不做多轮模型决定）。

把“库里真实在售产品/适当性”喂回给模型，替换对具体产品凭空编 risk/收益：
- 命中产品词的题面 → 确定性两步：find_products(召回库内产品) → product_suitability(该产品, 客户)，
  一次 DB 完成并 attach 到 user；然后由调用方单次流式。
- 不命中（纯画像/开放句）→ 不进工具，直接返回原上下文（快）。
不做“让 LLM 在多轮里自选工具”的 2~3 次非流式 request_tool_call，从而避免点名产品句白等 ~seconds。

护栏：
- 只读（AdvisorReadTools 只读）
- 工具命中落 AuditLog(action="advisor_agent.tool_call")
- 异常/无匹配 → 降级为纯客户上下文回答
"""
from __future__ import annotations

import json
from uuid import uuid4

from app.services.advisor_tools import AdvisorReadTools, product_facts_to_text, suitability_facts_to_text


class AdvisorBoundedLoop:
    def __init__(self, provider, tools: AdvisorReadTools, database) -> None:
        # provider 在本确定性方案中不再用于工具选择轮；保留字段以兼容测试/未来工具轮。
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

    async def run_to_text_user(self, *, conversation_id: str, actor_user_id: str, customer_id: str, query: str, context_user: str, system: str):
        """库内确定性 single-shot 接地。

        题面能召回真实在售产品 → find_products + product_suitability 一次完成 → attach 库内真实结果。
        query 为原始员工问题（不含工具追加）; 返回 (最终 user 上下文, 是否发生过接地)。
        """
        user = context_user
        try:
            async with self.database.session_factory() as session:
                cands = await self.tools.find_products(session, query=query or "", top_k=3)
            if not cands:
                return user, False
            pid = cands[0].id
            suits = None
            async with self.database.session_factory() as session:
                suits = await self.tools.product_suitability(session, customer_id=customer_id, product_id=pid) or []
            await self._audit_tool(conversation_id, actor_user_id, "find_products+product_suitability", {"product_id": pid}, True)
            parts = [product_facts_to_text(cands)]
            if suits:
                parts.append(suitability_facts_to_text(suits))
            user = user + "\n\n##### 库内真实（find_products + product_suitability，为权威依据，勿臆测）：\n" + "\n".join(parts)
            return user, True
        except Exception:  # noqa: BLE001 - 工具异常降级为纯上下文
            return user, False
