"""客服转人工摘要（L3）：LLM 生成结构化摘要 + 落共享记忆（用 fake provider，不依赖真实 API）。"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.conversation_summary import ConversationSummaryService


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content

    async def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


class _FakeChat:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str):
        self.chat = _FakeChat(content)


class _FakeSettings:
    qwen_chat_model = "fake-model"


class _FakeProvider:
    def __init__(self, content: str):
        self.settings = _FakeSettings()
        self._client = _FakeClient(content)

    async def close(self):
        pass


def _service(content: str):
    provider = _FakeProvider(content)
    # 不触发真实 DB：只测 summarize（prompt 构造 + JSON 校验）
    return ConversationSummaryService.__new__(ConversationSummaryService), provider


def test_summarize_returns_valid_json():
    svc, provider = _service('{"concern":"查持仓","resolved":"无","pending":"无","preference_hint":"偏保守"}')
    svc.provider = provider
    out = asyncio.run(svc.summarize({"original_query": "查持仓", "latest_query": "查持仓"}))
    assert out is not None
    assert json.loads(out)["concern"] == "查持仓"


def test_summarize_rejects_invalid_json():
    svc, provider = _service("not json")
    svc.provider = provider
    assert asyncio.run(svc.summarize({})) is None


def test_summarize_prompt_includes_context():
    svc, provider = _service('{}')
    svc.provider = provider
    asyncio.run(svc.summarize({"original_query": "我要买稳健产品", "active_product": "稳健增值计划"}))
    # 校验 _build_prompt 会带上关键上下文（不触发真实 API）
    prompt = ConversationSummaryService._build_prompt({"original_query": "我要买稳健产品", "active_product": "稳健增值计划"})
    assert "稳健产品" in prompt
    assert "稳健增值计划" in prompt


def test_build_prompt_includes_existing_summary_for_refinement():
    prompt = ConversationSummaryService._build_prompt({
        "latest_query": "那赎回呢", "existing_summary": '{"concern":"稳健理财","resolved":"","pending":"","preference_hint":"偏稳健"}'})
    assert "已有的会话摘要" in prompt
    assert "稳健理财" in prompt
    assert "赎回" in prompt
