# -*- coding: utf-8 -*-
"""Agent 链路追踪的观测字段测试（阶段一）。

前端"查看推理过程"面板依赖 ToolRound 上的四个观测字段：
duration_ms / prompt_tokens / completion_tokens / output_preview。

这些字段**不参与任何控制流**，但一旦缺失或口径错误，前端就会显示空白或误导性数字
（例如把确定性路径记成 0 token，会让成本看起来"免费"）。故在此锁定契约：

1. 已执行轮次必须带耗时、token 与输出摘要。
2. **被拦截的轮次同样记 token**——决策 LLM 调用已经发生了，开销真实产生，
   漏记会让成本统计偏低。
3. provider 不提供用量时（测试假 provider / 上游未返回 usage）必须自然降级，
   绝不能因为缺字段就让整条工具链路失败。
4. 规则补齐（确定性路径）token 留空，与"模型决策"区分开。
5. output_preview 是**结构性摘要**而非原文，且必须截断。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.services.customer_model_tools import (
    CustomerModelToolLoop,
    _OUTPUT_PREVIEW_MAX_CHARS,
    _summarize_output,
)


class UsageProvider:
    """每次决策都记录用量的假 provider（模拟 qwen.py 的 last_call_usage）。"""

    def __init__(self, decisions, usage=None):
        self.decisions = list(decisions)
        self._client = object()
        self.usage = usage or {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "duration_ms": 210.5,
        }

    async def request_tool_call(self, *, system, user, tools, model=None, temperature=0.0):
        self.last_call_usage = dict(self.usage)
        if not self.decisions:
            return None
        return self.decisions.pop(0)


class BareProvider:
    """**没有** last_call_usage 属性的假 provider（模拟老 provider / 测试替身）。"""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self._client = object()

    async def request_tool_call(self, *, system, user, tools, model=None, temperature=0.0):
        if not self.decisions:
            return None
        return self.decisions.pop(0)


class FakeReadTools:
    def __init__(self, results=None):
        self.results = results or {}

    async def execute(self, *, name, customer_id, query, intent=None, entities=None):
        preset = self.results.get(name)
        if preset is not None:
            return preset
        return SimpleNamespace(success=True,
                               data={"evidence_status": "sufficient",
                                     "citations": [{"chunk_id": "c1"}, {"chunk_id": "c2"}]},
                               answer=f"{name} 的结果", error=None)


class FakeDatabase:
    def __init__(self):
        self.audits = []

    def session_factory(self):
        return _AuditCtx(self)


class _AuditCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.db.audits.append(obj)

    async def commit(self):
        pass


def _run(provider, *, tools=None, db=None, planned_intents=None, max_rounds=4):
    loop = CustomerModelToolLoop(provider=provider, tools=tools or FakeReadTools(),
                                 database=db or FakeDatabase(), max_rounds=max_rounds)
    return asyncio.run(loop.run(customer_id="c1", conversation_id="conv1",
                                query="我的工单进展如何", planned_intents=planned_intents))


# ---------- 1. 已执行轮次的观测字段 ----------

def test_executed_round_carries_duration_tokens_and_preview():
    """已执行的工具轮必须带耗时、token 与输出摘要，否则前端面板会是空白。"""
    decision = {"name": "get_my_workorder_status", "arguments": {"query": "工单"},
                "reasoning": "用户询问工单进展，需查真实状态"}
    outcome = _run(UsageProvider([decision]), planned_intents=["workorder_status"])

    assert len(outcome.rounds) == 1
    r = outcome.rounds[0]
    assert r.success is True
    # 耗时：工具执行与决策 LLM 分开记，两者都应 > 0
    assert r.duration_ms is not None and r.duration_ms >= 0
    assert r.llm_duration_ms == 210.5
    # token：来自决策 LLM 调用
    assert r.prompt_tokens == 120
    assert r.completion_tokens == 30
    # 输出摘要：结构性描述而非原文
    assert r.output_preview == "命中 2 条知识块"
    # 思维链
    assert r.reasoning == "用户询问工单进展，需查真实状态"


# ---------- 2. 被拦截的轮次也要记 token ----------

def test_blocked_round_still_records_decision_tokens():
    """越界调用被拦下，但决策 LLM 已经跑过——token 开销真实存在，不能漏记。

    这条容易被忽略：若只给"执行成功"的轮次记账，被安全护栏拦下的那些轮次
    开销就会凭空消失，成本统计系统性偏低。
    """
    # 计划只有 workorder_status，模型却要调知识检索 → 越界被拦
    decision = {"name": "search_customer_knowledge", "arguments": {"query": "工单流程"},
                "reasoning": "想解释工单流程"}
    outcome = _run(UsageProvider([decision]), planned_intents=["workorder_status"])

    assert len(outcome.rounds) == 1
    r = outcome.rounds[0]
    assert r.success is False
    assert r.error == "tool_out_of_plan_scope"
    assert r.prompt_tokens == 120, "被拦截轮次的决策 token 必须记账"
    assert r.completion_tokens == 30
    assert "不在本次计划范围内" in r.output_preview


# ---------- 3. provider 缺用量字段时降级 ----------

def test_missing_usage_attribute_degrades_silently():
    """provider 没有 last_call_usage 时，观测字段留空但**流程不受影响**。

    为什么重要：`_consume_llm_usage` 用 getattr 读取，测试替身与旧 provider
    都没有该属性。若这里抛异常，整条工具链路会失败——观测绝不能拖垮业务。
    """
    decision = {"name": "get_my_workorder_status", "arguments": {"query": "工单"},
                "reasoning": ""}
    outcome = _run(BareProvider([decision]), planned_intents=["workorder_status"])

    assert len(outcome.rounds) == 1
    r = outcome.rounds[0]
    assert r.success is True, "降级不得影响工具正常执行"
    assert r.prompt_tokens is None
    assert r.completion_tokens is None
    assert r.duration_ms is not None, "工具耗时不依赖 provider 用量，仍应记录"


# ---------- 4. 规则补齐不记 token ----------

def test_rule_backstop_round_has_no_tokens():
    """规则确定性补齐不经过 LLM，token 必须留空（前端显示"—"而非 0）。"""
    # 模型不调工具 → 触发规则补齐；计划含两个意图但一个都没覆盖
    outcome = _run(UsageProvider([]), planned_intents=["workorder_status"])

    assert outcome.rounds, "应有规则补齐产生的轮次"
    r = outcome.rounds[0]
    assert r.arguments.get("filled_by") == "rule_backstop"
    assert r.prompt_tokens is None, "确定性路径不得记 token"
    assert r.completion_tokens is None
    assert r.duration_ms is not None


# ---------- 5. output_preview 是摘要且截断 ----------

def test_summarize_output_prefers_structure_over_text():
    """有 citations / items 时优先输出结构性描述，而不是把原文塞给前端。"""
    preview = _summarize_output(
        answer="很长的原文" * 100,
        data={"citations": [{"chunk_id": str(i)} for i in range(5)], "items": [{"id": 1}]},
        success=True)
    assert preview == "命中 5 条知识块；返回 1 条记录"


def test_summarize_output_truncates_plain_text():
    """无结构信息时退化成文本摘要，但必须截断，避免把对话挤爆/带出敏感原文。"""
    long_text = "客" * 500
    preview = _summarize_output(answer=long_text, data={}, success=True)
    assert preview.endswith("…")
    assert len(preview) == _OUTPUT_PREVIEW_MAX_CHARS + 1


def test_summarize_output_reports_failure_reason():
    preview = _summarize_output(answer="", data={}, success=False, error="fact_not_found")
    assert "fact_not_found" in preview
