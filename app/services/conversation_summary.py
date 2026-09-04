# -*- coding: utf-8 -*-
"""客服转人工摘要（L3 稳定摘要 · 首个场景）。

会话转人工/结束时，把该会话关键点浓缩成结构化 `conversation_summary` 落共享记忆
（scope=shared, memory_type=observation, key=conversation_summary），供客服/投顾等
其它 Agent 复用为上下文。LLM 生成失败则跳过，绝不阻塞转人工主流程。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.settings import get_settings
from app.infrastructure.qwen import QwenProvider
from app.services.shared_memory_service import SharedMemoryService

SYSTEM = (
    "你是财富管理客服的会话摘要器。把一次客服会话浓缩成结构化 JSON，只允许输出如下字段："
    '{"concern":"客户的主要诉求/关注点","resolved":"已确认或已回答的事实","pending":"待处理/待澄清事项",'
    '"preference_hint":"可推断的客户风险/产品/期限偏好（若有，否则空字符串）"}。'
    "要求：只依据给定上下文；不编造；不输出客户身份/账户/资产等敏感明细；简洁中文。"
)


class ConversationSummaryService:
    def __init__(self, database, redis_client=None, provider: QwenProvider | None = None):
        self.database = database
        self.shared = SharedMemoryService(database, redis_client)
        self.provider = provider

    @staticmethod
    def _build_prompt(context: dict) -> str:
        parts = [
            "以下是本次会话的上下文：",
            f"客户最初诉求：{context.get('original_query') or ''}",
            f"最近一句：{context.get('latest_query') or ''}",
            f"已处理结果：{json.dumps(context.get('task_results') or [], ensure_ascii=False)[:800]}",
            f"澄清/待办：{context.get('clarification') or context.get('pending') or ''}",
            f"涉及产品：{context.get('active_product') or ''}",
            f"转人工原因：{context.get('handoff_reason') or ''}",
        ]
        existing = context.get("existing_summary") or ""
        if existing:
            parts.append(f"\n[已有的会话摘要，请在其基础上合并/更新本轮信息]：{existing[:1200]}")
        parts.append("请生成/更新该会话的结构化摘要。")
        return "\n".join(parts)

    async def summarize(self, context: dict) -> str | None:
        provider = self.provider
        owned = provider is None
        if provider is None:
            settings = get_settings()
            if not settings.dashscope_api_key:
                return None
            provider = QwenProvider(settings)
        try:
            # 兼容真实 provider（有 chat_completions，带受控超时）与测试 fake（仅有 _client.chat…）
            caller = getattr(provider, "chat_completions", None)
            if caller is not None:
                resp = await caller(
                    model=provider.settings.qwen_chat_model, temperature=0.2,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": self._build_prompt(context)}],
                )
            else:
                resp = await provider._client.chat.completions.create(
                    model=provider.settings.qwen_chat_model, temperature=0.2,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": self._build_prompt(context)}],
                )
            content = (resp.choices[0].message.content or "").strip()
            json.loads(content)  # 校验是合法 JSON，否则不落库
            return content
        except Exception:  # noqa: BLE001 - 摘要失败不影响主流程
            return None
        finally:
            if owned and provider is not None:
                await provider.close()

    async def summarize_and_store(self, *, customer_id: str, conversation_id: str,
                                  context: dict, actor_user_id: str | None = None) -> Any | None:
        summary = await self.summarize(context)
        if not summary:
            return None
        return await self.shared.remember(
            customer_id=customer_id, agent_type="customer_service", key="conversation_summary",
            value=summary, memory_type="observation", scope="shared", source="handoff_summary",
            conversation_id=conversation_id, actor_user_id=actor_user_id)
