"""QwenProvider.stream_text 真流式：按 delta 产出文本并记录用量/TTFT（fake client，不依赖真实 API）。"""
import asyncio
from types import SimpleNamespace

from app.infrastructure.qwen import QwenProvider


def _chunk(content=None, usage=None, has_choices=True):
    if has_choices:
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=content))],
            usage=usage,
        )
    return SimpleNamespace(choices=[], usage=usage)


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kwargs):
        assert kwargs.get("stream") is True

        async def _gen():
            for c in self._chunks:
                yield c

        return _gen()


class _FakeClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks))


class _FakeSettings:
    qwen_chat_model = "qwen-plus"
    qwen_chat_timeout_seconds = 30.0
    qwen_chat_connect_timeout_seconds = 8.0


def _provider(chunks):
    p = QwenProvider.__new__(QwenProvider)
    p.settings = _FakeSettings()
    p._client = _FakeClient(chunks)
    return p


def test_stream_text_yields_deltas_and_captures_usage():
    chunks = [
        _chunk(content="投资有风险"),
        _chunk(content="，请谨慎。"),
        _chunk(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=6, total_tokens=16), has_choices=False),
    ]
    provider = _provider(chunks)
    out = asyncio.run(_collect(provider.stream_text(system="s", user="u")))
    assert "".join(out) == "投资有风险，请谨慎。"


def test_stream_text_empty_when_unconfigured():
    p = QwenProvider.__new__(QwenProvider)
    p.settings = _FakeSettings()
    p._client = None
    out = asyncio.run(_collect(p.stream_text(system="s", user="u")))
    assert out == []


async def _collect(agen):
    return [delta async for delta in agen]
