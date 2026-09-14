# -*- coding: utf-8 -*-
"""LLM 调用的资源保护测试（超时 / 重试 / 并发闸门）。

背景 ISSUE-20260914-MODEL-TOOL-LLM-CALL-AMPLIFICATION：
  实测出现 `llm.usage model=qwen-plus status=failed duration_ms=95760`（95.7 秒后失败），
  且生成降级为占位文案，使双跑对照结果不可信。
  根因：① openai SDK 默认 max_retries=2 → 30s 超时实际重试 3 次 ≈ 90s+；
        ② 模型工具轮把单请求 LLM 调用从 2~3 次放大到 4~6 次，并发下打满上游配额。

这些测试钉住三项保护，防止日后被无声改回：
  1. SDK 重试次数被显式限制（不再用默认 2）
  2. 存在进程级并发信号量，且在 chat_completions 内真正生效
  3. 工具轮有比普通对话更短的显式超时
"""
import asyncio
import inspect
import httpx
import pytest

from app.core.settings import get_settings
from app.infrastructure.qwen import QwenProvider


def test_sdk_retries_are_explicitly_capped():
    """SDK 自动重试必须被显式限制——默认 2 会让 30s 超时变成 90s+。"""
    settings = get_settings()
    assert getattr(settings, "qwen_max_retries", None) is not None, \
        "缺少 qwen_max_retries 配置"
    assert settings.qwen_max_retries < 2, \
        "qwen_max_retries 不应 >= 2（SDK 默认值），否则超时会成倍放大"


def test_provider_client_uses_configured_retries():
    """Provider 构造的 client 必须采用配置的重试次数，而不是 SDK 默认。"""
    settings = get_settings()
    if not settings.dashscope_api_key:
        pytest.skip("未配置 DASHSCOPE_API_KEY，无法构造 client")
    provider = QwenProvider(settings)
    assert provider._client is not None
    assert provider._client.max_retries == settings.qwen_max_retries


def test_concurrency_semaphore_configured_and_shared():
    """并发闸门按 limit 值缓存复用（同一进程内共享，才能真正限流）。"""
    settings = get_settings()
    provider = QwenProvider(settings)
    sem_a = provider._semaphore()
    sem_b = provider._semaphore()
    if settings.qwen_max_concurrency <= 0:
        assert sem_a is None, "配置 <=0 时应不限流"
    else:
        assert sem_a is not None, "应存在并发信号量"
        assert sem_a is sem_b, "同一 limit 应复用同一信号量实例（否则限流失效）"


def test_semaphore_bound_is_respected():
    """信号量确实限制并发数：>limit 个任务同时进入时，峰值不超过 limit。"""
    settings = get_settings().model_copy(update={"qwen_max_concurrency": 2})
    provider = QwenProvider(settings)
    sem = provider._semaphore()
    assert sem is not None and sem._value == 2

    peak = {"current": 0, "max": 0}

    async def worker():
        async with sem:
            peak["current"] += 1
            peak["max"] = max(peak["max"], peak["current"])
            await asyncio.sleep(0.01)
            peak["current"] -= 1

    async def run():
        await asyncio.gather(*(worker() for _ in range(8)))

    asyncio.run(run())
    assert peak["max"] <= 2, f"并发峰值 {peak['max']} 超过 limit=2"


def test_chat_completions_acquires_semaphore():
    """chat_completions 必须真正经过信号量（源码级钉住，防止被移除）。"""
    src = inspect.getsource(QwenProvider.chat_completions)
    assert "_semaphore()" in src, "chat_completions 未使用并发信号量"
    assert "async with sem" in src, "chat_completions 未在信号量内发起调用"


def test_tool_call_has_shorter_timeout_than_chat():
    """工具轮超时须短于普通对话——工具轮是辅助决策且一请求可能多轮。"""
    settings = get_settings()
    tool_to = getattr(settings, "qwen_tool_call_timeout_seconds", None)
    assert tool_to is not None, "缺少 qwen_tool_call_timeout_seconds 配置"
    assert tool_to <= settings.qwen_chat_timeout_seconds, \
        "工具轮超时应 <= 普通对话超时，否则多轮会成倍放大延迟"


def test_request_tool_call_passes_explicit_timeout():
    """request_tool_call 必须显式传 timeout（否则落到 httpx 的 120s 默认）。"""
    src = inspect.getsource(QwenProvider.request_tool_call)
    assert "timeout=" in src, "request_tool_call 未传显式 timeout"


def test_worst_case_latency_bounded():
    """最坏情况总延迟 = 超时 × (1 + 重试) 应可控（回归 95.7s 那种情况）。"""
    settings = get_settings()
    worst_chat = settings.qwen_chat_timeout_seconds * (1 + settings.qwen_max_retries)
    worst_tool = settings.qwen_tool_call_timeout_seconds * (1 + settings.qwen_max_retries)
    assert worst_chat <= 60.0, f"普通对话最坏 {worst_chat}s 过长"
    assert worst_tool <= 30.0, f"工具轮最坏 {worst_tool}s 过长"
