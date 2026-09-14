# -*- coding: utf-8 -*-
"""只读查询的模型自主工具调度测试。

覆盖的核心风险（安全边界 > 功能）：
1. **写操作进不来**：模型即使返回 create_order 等工具名，必须被硬白名单拒绝。
2. **不能越权指定客户**：customer_id 由服务端注入，模型无法通过参数查他人数据。
3. **有界循环**：max_rounds 用尽即停，不无限调用。
4. **降级安全**：模型不可用/不调工具时，返回 stopped_reason 而非异常。
5. **审计留痕**：每轮工具调用落 AuditLog。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.services.customer_model_tools import (
    CUSTOMER_READ_TOOL_SCHEMAS,
    CustomerModelToolLoop,
    _ALLOWED_TOOL_NAMES,
)


class FakeProvider:
    """按预设脚本依次返回工具决策（模拟模型自主选择）。"""

    def __init__(self, decisions, client=object()):
        self.decisions = list(decisions)
        self.calls = []
        self._client = client

    async def request_tool_call(self, *, system, user, tools, model=None, temperature=0.0):
        self.calls.append({"system": system, "user": user, "tools": tools})
        if not self.decisions:
            return None
        return self.decisions.pop(0)


class FakeReadTools:
    """记录被调用的工具名与参数；按工具返回成功/失败。"""

    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    async def execute(self, *, name, customer_id, query, intent=None, entities=None):
        self.calls.append({"name": name, "customer_id": customer_id, "query": query,
                           "intent": intent, "entities": entities})
        preset = self.results.get(name)
        if preset is not None:
            return preset
        return SimpleNamespace(success=True, data={"evidence_status": "sufficient",
                                                  "citations": [{"chunk_id": "c1"}]},
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


def _loop(decisions, *, tools=None, db=None, max_rounds=4):
    return CustomerModelToolLoop(
        provider=FakeProvider(decisions),
        tools=tools or FakeReadTools(),
        database=db or FakeDatabase(),
        max_rounds=max_rounds,
    )


# ---------- 安全边界：写操作与越权 ----------

def test_write_tool_is_rejected_by_hard_allowlist():
    """模型编造写操作工具名 → 必须被拒，且不执行、不产生结果。"""
    tools = FakeReadTools()
    db = FakeDatabase()
    loop = _loop([{"name": "create_order", "arguments": {"amount": 10000}}], tools=tools, db=db)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="帮我买1万"))

    assert outcome.results == [], "写操作不得产生任何结果"
    assert tools.calls == [], "写操作不得触达工具层"
    assert outcome.stopped_reason == "tool_not_allowed"
    assert outcome.rounds[0].error == "tool_not_allowed"
    assert db.audits, "被拒的调用也必须留审计"


@pytest.mark.parametrize("bad_tool", [
    "create_order", "transfer", "redeem", "update_balance",
    "get_my_asset_summary_v2", "search_customer_knowledge;DROP",
])
def test_undeclared_tools_never_reach_tool_layer(bad_tool):
    tools = FakeReadTools()
    loop = _loop([{"name": bad_tool, "arguments": {}}], tools=tools)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q"))
    assert tools.calls == []
    assert outcome.stopped_reason == "tool_not_allowed"


def test_customer_id_injected_server_side_not_from_model():
    """模型无法通过参数指定查谁——防止越权读他人数据。"""
    tools = FakeReadTools()
    loop = _loop([{"name": "get_my_asset_summary",
                   "arguments": {"customer_id": "someone-else", "query": "查他资产"}}],
                 tools=tools)
    asyncio.run(loop.run(customer_id="real-customer", conversation_id="v1", query="查我的资产"))

    assert tools.calls, "应调用工具"
    assert tools.calls[0]["customer_id"] == "real-customer", \
        "customer_id 必须由服务端注入，不得采信模型参数"


# ---------- 模型自主选择 ----------

def test_model_selects_fact_tool_and_stops_when_model_stops():
    """事实类工具拿到结果后仍把结果回灌模型，由模型决定是否继续。

    回归 ISSUE-20260914-MODEL-TOOL-FORCED-EARLY-STOP：此前实现"事实类工具一次成功即 break"，
    导致多意图 query（"我有没有风险限制"同时命中 risk_restriction + risk_public）
    模型拿不到第二个工具的机会，graph 只剩 1 个 task 而管线有 2 个。
    现改为：结果一律回灌，模型不调用才结束（stopped_reason=no_tool_call）。
    """
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "get_my_asset_summary", "arguments": {}},
        None,   # 模型判断已足够，主动结束
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="查我的持仓"))

    assert outcome.tool_names == ["get_my_asset_summary"]
    assert outcome.stopped_reason == "no_tool_call"
    assert len(outcome.results) == 1
    assert outcome.results[0]["data"]["execution_mode"] == "model_tool_selection"
    assert len(provider.calls) == 2, "事实结果必须回灌，让模型有机会继续调用"


def test_multi_intent_query_can_cover_both_tools():
    """多意图：模型可连续调用事实类 + 知识类工具，两个 task 都保留。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "get_my_risk_restriction", "arguments": {}},
        {"name": "search_customer_knowledge", "arguments": {"query": "风险限制制度", "intent": "risk_public"}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1",
                                   query="我有没有风险限制",
                                   planned_intents=["risk_restriction", "risk_public"]))

    assert outcome.tool_names == ["get_my_risk_restriction", "search_customer_knowledge"]
    assert len(outcome.results) == 2, "两个意图的工具结果都应保留"
    assert {r["intent"] for r in outcome.results} == {"risk_restriction", "risk_public"}


def test_planned_intents_are_passed_as_guidance():
    """planned_intents 应作为提示出现在给模型的输入里（引导覆盖多意图）。"""
    tools = FakeReadTools()
    provider = FakeProvider([None])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=2)
    asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="我有没有风险限制",
                         planned_intents=["risk_restriction", "risk_public"]))
    assert "risk_restriction" in provider.calls[0]["user"]
    assert "risk_public" in provider.calls[0]["user"]


# ---------- 未覆盖意图的规则兜底（回归：模型提前收手导致丢 task） ----------
# 实测 "我有没有风险限制" 命中 risk_restriction(事实) + risk_public(知识)，
# 模型拿到事实后即停止 → 图比管线少一个 task，用户拿不到制度解释。
# 提示词层面已要求"多意图逐个覆盖"仍不够，故加规则兜底：还有未覆盖意图就点名补齐。

def test_model_stopping_early_is_overridden_until_intents_covered():
    """模型想停，但计划中仍有未覆盖意图 → 规则层**确定性补齐**，不容忍提前收手。

    注意：不是"再问模型一次"——实测明确追问后模型连续多轮仍返回 None。
    故由 _fill_uncovered 直接按映射执行缺失工具。
    """
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "get_my_risk_restriction", "arguments": {}},   # 模型只调了事实
        None,                                                   # 模型想停 → 触发规则补齐
        None,                                                   # 补齐后再问，模型仍不调
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1",
                                   query="我有没有风险限制",
                                   planned_intents=["risk_restriction", "risk_public"]))

    assert outcome.tool_names == ["get_my_risk_restriction", "search_customer_knowledge"]
    assert {r["intent"] for r in outcome.results} == {"risk_restriction", "risk_public"}
    # 补齐的那条应标记为 rule_backstop，便于审计区分"模型选的"与"规则补的"
    backstop = [r for r in outcome.results if r["data"].get("execution_mode") == "rule_backstop"]
    assert len(backstop) == 1 and backstop[0]["intent"] == "risk_public"


def test_backstop_works_even_if_model_never_calls_second_tool():
    """模型被追问后仍坚持不调 → 规则补齐依然生效（这是本修复的核心）。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "get_my_risk_restriction", "arguments": {}},
        None, None, None,   # 模型 thereafter 一直返回 None
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1",
                                   query="我有没有风险限制",
                                   planned_intents=["risk_restriction", "risk_public"]))
    assert len(outcome.results) == 2, "模型不配合时也必须补齐，否则用户丢答案"
    assert outcome.stopped_reason in {"no_tool_call", "intent_uncovered"}


def test_backstop_fill_is_audited_with_marker():
    """规则补齐的工具调用同样要审计，并标注 filled_by=rule_backstop。"""
    tools = FakeReadTools()
    db = FakeDatabase()
    provider = FakeProvider([
        {"name": "get_my_risk_restriction", "arguments": {}},
        None, None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=db, max_rounds=4)
    asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q",
                         planned_intents=["risk_restriction", "risk_public"]))
    assert any("rule_backstop" in row.detail for row in db.audits), \
        "规则补齐应有可区分的审计标记"


def test_backstop_respects_allowlist():
    """规则补齐同样受硬白名单约束（不因是内部路径而绕过）。"""
    from app.services.customer_model_tools import _INTENT_TO_TOOL, _ALLOWED_TOOL_NAMES
    for intent, tool in _INTENT_TO_TOOL.items():
        assert tool in _ALLOWED_TOOL_NAMES, f"意图 {intent} 映射到非白名单工具 {tool}"


def test_backstop_reports_intent_uncovered_when_budget_runs_out():
    """预算用尽且仍有未覆盖意图 → stopped_reason=intent_uncovered（便于观测，而非静默）。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "get_my_risk_restriction", "arguments": {}},
        None, None, None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=2)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q",
                                   planned_intents=["risk_restriction", "risk_public"]))
    assert outcome.stopped_reason == "intent_uncovered"


def test_no_backstop_when_all_intents_covered():
    """全部覆盖时正常结束，不做无谓追问（省一轮 LLM 调用）。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "get_my_risk_restriction", "arguments": {}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q",
                                   planned_intents=["risk_restriction"]))
    assert outcome.stopped_reason == "no_tool_call"
    assert len(provider.calls) == 2, "已覆盖时不应额外追问"


def test_backstop_ignores_intents_outside_readonly_tools():
    """greeting/human_handoff 等不由只读工具覆盖的意图，不触发兜底追问。"""
    tools = FakeReadTools()
    provider = FakeProvider([None])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=3)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="你好",
                                   planned_intents=["greeting", "human_handoff"]))
    assert outcome.stopped_reason == "no_tool_call"
    assert len(provider.calls) == 1, "非只读意图不应触发兜底"


def test_knowledge_intents_sharing_one_tool_are_still_tracked_individually():
    """回归 ISSUE-20260914-COVERAGE-FALSE-POSITIVE：共用同一工具的意图必须**分别**判定覆盖。

    旧逻辑用"工具是否被调用过"兜底，导致只检索了 company_info 时误判 company_business
    也已覆盖（实测模型 4 轮全用 intent=company_info，company_business 从未检索，
    但 pending=[]，兜底不触发，图少一个 task）。
    现改为**只按 intent 字段判覆盖**，因此 company_business 应被识别为未覆盖并被补齐。
    """
    tools = FakeReadTools()

    class ScriptedProvider:
        def __init__(self):
            self.calls = []
            self._client = object()
            self._n = 0

        async def request_tool_call(self, *, system, user, tools, model=None, temperature=0.0):
            self.calls.append(user)
            self._n += 1
            if self._n == 1:
                return {"name": "search_customer_knowledge",
                        "arguments": {"query": "公司业务", "intent": "company_info"}}
            return None   # 之后一直想停

    provider = ScriptedProvider()
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="公司业务",
                                   planned_intents=["company_info", "company_business"]))
    intents = {r["intent"] for r in outcome.results}
    assert "company_business" in intents, \
        "company_business 未被检索却被误判已覆盖（共用工具的假阳性）"


# ---------- 重复调用同一 intent 应去重（回归） ----------
# ISSUE-20260914-MODEL-TOOL-DUPLICATE-INTENT-RESULT：知识类工具成功后结果回灌，
# 模型可能认为"还需补充"再调一次（同 intent 不同 query），产出两个同 intent 的 task，
# 下游 summarize 拼接出重复答案（graph 实测出现两个 risk_public）。

def test_duplicate_same_intent_is_deduplicated():
    """同一 intent 重复成功时不追加新结果，只更新已有条目。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "search_customer_knowledge", "arguments": {"query": "风险限制制度", "intent": "risk_public"}},
        {"name": "search_customer_knowledge", "arguments": {"query": "交易权限影响", "intent": "risk_public"}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="我有没有风险限制",
                                   planned_intents=["risk_public"]))
    intents = [r["intent"] for r in outcome.results]
    assert intents == ["risk_public"], f"同 intent 应去重，实际 {intents}"
    assert len(outcome.rounds) == 2, "两轮调用仍应各自记账（审计需要）"


def test_duplicate_dedup_keeps_latest_answer():
    """去重时以最新结果覆盖答案（后一次检索可能更充分）。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "search_customer_knowledge", "arguments": {"query": "q1", "intent": "policy"}},
        {"name": "search_customer_knowledge", "arguments": {"query": "q2", "intent": "policy"}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q",
                                   planned_intents=["policy"]))
    assert len(outcome.results) == 1
    assert outcome.results[0]["answer"] == "search_customer_knowledge 的结果"


def test_different_intents_are_not_deduplicated():
    """不同 intent 的正常多意图结果不应被误合并。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "search_customer_knowledge", "arguments": {"query": "a", "intent": "risk_public"}},
        {"name": "search_customer_knowledge", "arguments": {"query": "b", "intent": "policy"}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q",
                                   planned_intents=["risk_public", "policy"]))
    assert {r["intent"] for r in outcome.results} == {"risk_public", "policy"}


def test_fact_tool_duplicate_is_deduplicated():
    """事实类工具重复调用同一 intent 也去重。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "get_my_asset_summary", "arguments": {}},
        {"name": "get_my_asset_summary", "arguments": {}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="查资产"))
    assert len(outcome.results) == 1, "同 intent 重复调用应去重"


# ---------- intent 硬门禁：知识检索工具不得承载非知识类意图 ----------
# ISSUE-20260914-MODEL-TOOL-MISROUTES-NON-RAG-INTENTS：模型曾用 search_customer_knowledge
# 去答 product_recommendation / greeting / human_handoff，拿到 RAG 的短路占位串
# （"产品推荐使用实时 Product 数据…"）当成答案，导致对照 DIFF。

@pytest.mark.parametrize("bad_intent", [
    "product_recommendation", "greeting", "human_handoff", "business_operation", "unknown",
])
def test_non_knowledge_intent_is_coerced_to_faq(bad_intent):
    """知识检索工具收到非知识类 intent 时必须回落为 faq，不得透传。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "search_customer_knowledge",
         "arguments": {"query": "q", "intent": bad_intent}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q"))
    assert tools.calls, "工具应被调用"
    assert tools.calls[0]["intent"] == "faq", \
        f"{bad_intent} 不应透传给知识检索（会被 RAG 短路成占位串）"


@pytest.mark.parametrize("good_intent", sorted({
    "company_info", "company_business", "faq", "workflow", "policy", "risk_public",
}))
def test_knowledge_intents_pass_through_unchanged(good_intent):
    """合法知识类 intent 必须原样透传（否则会丢失多意图语义）。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "search_customer_knowledge",
         "arguments": {"query": "q", "intent": good_intent}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q"))
    assert tools.calls[0]["intent"] == good_intent


def test_knowledge_intents_whitelist_excludes_dedicated_paths():
    """白名单本身不得含任何"有专用确定性路径"的意图。"""
    from app.services.customer_model_tools import _KNOWLEDGE_INTENTS
    must_exclude = {"product_recommendation", "greeting", "human_handoff",
                    "business_operation", "asset_summary", "order_status",
                    "workorder_status", "risk_restriction", "product_status"}
    assert not (_KNOWLEDGE_INTENTS & must_exclude), \
        f"白名单混入了专用路径意图: {_KNOWLEDGE_INTENTS & must_exclude}"


def test_model_can_chain_knowledge_then_stop():
    """知识类工具可续调补充，模型不再调用时结束。"""
    tools = FakeReadTools()
    provider = FakeProvider([
        {"name": "search_customer_knowledge", "arguments": {"query": "开户流程", "intent": "workflow"}},
        None,   # 模型认为信息已足够，主动结束
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="怎么开户"))

    assert outcome.tool_names == ["search_customer_knowledge"]
    assert outcome.stopped_reason == "no_tool_call"
    assert len(provider.calls) == 2, "应把首轮结果回灌给模型再问一轮"


def test_model_rewrites_query_via_arguments():
    """改写内容由模型给出（不再是 2 条固定模板）。"""
    tools = FakeReadTools()
    rewritten = "公司主营业务范围与经营资质说明"
    loop = _loop([{"name": "search_customer_knowledge",
                   "arguments": {"query": rewritten, "intent": "company_business"}}],
                 tools=tools)
    asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="你们公司做什么的"))
    assert tools.calls[0]["query"] == rewritten
    assert tools.calls[0]["intent"] == "company_business"


def test_intent_passed_through_for_knowledge_tool():
    tools = FakeReadTools()
    loop = _loop([{"name": "search_customer_knowledge",
                   "arguments": {"query": "q", "intent": "policy"}}], tools=tools)
    asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q"))
    assert tools.calls[0]["intent"] == "policy"


# ---------- 有界循环与降级 ----------

def test_budget_exhausted_when_model_keeps_calling():
    """模型持续调用（每次都失败）→ 到 max_rounds 即停，不无限循环。"""
    tools = FakeReadTools(results={
        "search_customer_knowledge": SimpleNamespace(
            success=False, data={"evidence_status": "low_confidence"}, answer="", error="low_confidence")
    })
    decisions = [{"name": "search_customer_knowledge", "arguments": {"query": f"q{i}"}} for i in range(10)]
    loop = _loop(decisions, tools=tools, max_rounds=3)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q"))

    assert len(outcome.rounds) == 3, f"应恰好用满 3 轮，实际 {len(outcome.rounds)}"
    assert outcome.stopped_reason == "budget_exhausted"
    assert outcome.results == []


def test_model_unavailable_degrades_without_exception():
    """provider 无 client（未配置 API KEY）→ 降级，不抛异常。"""
    loop = CustomerModelToolLoop(
        provider=FakeProvider([], client=None),
        tools=FakeReadTools(), database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q"))
    assert outcome.stopped_reason == "model_unavailable"
    assert outcome.results == []


def test_no_tool_call_returns_empty_for_fallback():
    """模型不调工具 → 结果为空，供图回退到确定性 ReAct。"""
    loop = _loop([None, None])
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q"))
    assert outcome.stopped_reason == "no_tool_call"
    assert outcome.results == []
    assert outcome.called_any is False


def test_failed_tool_result_is_recorded_but_no_success_result():
    """工具失败 → 记账但不出成功结果，且把**失败原因+参数**回灌模型。

    回归 ISSUE-20260914-TOOL-FAILURE-NOT-FEED-BACK：旧实现只回灌 error 字符串，
    模型不知道该怎么调整，于是重复同样的调用直到预算耗尽（实测 4 轮全失败）。
    """
    tools = FakeReadTools(results={
        "get_my_order_status": SimpleNamespace(
            success=False, data={}, answer="", error="fact_not_found")
    })
    provider = FakeProvider([
        {"name": "get_my_order_status", "arguments": {"query": "ORD-1"}},
        {"name": "get_my_order_status", "arguments": {"query": "ORD-2"}},   # 换参数再试
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=3)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="查订单"))

    assert outcome.rounds[0].success is False
    assert outcome.rounds[0].error == "fact_not_found"
    assert outcome.results == []
    # 回灌内容必须含失败原因、已试参数与可执行的调整建议
    feedback = provider.calls[1]["user"]
    assert "fact_not_found" in feedback, "应回灌具体 error"
    assert "ORD-1" in feedback, "应回灌已试参数"
    assert "重复" in feedback or "换" in feedback, "应给出可执行的调整建议"


# ---------- 审计与 schema ----------

def test_every_round_is_audited():
    tools = FakeReadTools()
    db = FakeDatabase()
    decisions = [
        {"name": "search_customer_knowledge", "arguments": {"query": "a", "intent": "faq"}},
        {"name": "get_my_asset_summary", "arguments": {}},
    ]
    loop = _loop(decisions, tools=tools, db=db)
    asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="q"))

    assert len(db.audits) == 2, f"每轮都应审计，实际 {len(db.audits)}"
    for row in db.audits:
        assert row.action == "customer_agent.tool_call"
        assert '"read_only": true' in row.detail
        assert '"selection": "model"' in row.detail


def test_schema_exposes_only_read_only_tools():
    """工具集必须只含只读查询——写操作绝不出现在声明给模型的 schema 里。"""
    names = {item["function"]["name"] for item in CUSTOMER_READ_TOOL_SCHEMAS}
    forbidden = {"create_order", "confirm_order", "transfer", "redeem",
                 "update_balance", "cancel_order", "review_order"}
    assert not (names & forbidden), f"schema 泄露了写操作: {names & forbidden}"


def test_allowlist_matches_declared_schema():
    """硬白名单必须与声明的 schema 完全一致（防止两者漂移）。"""
    declared = {item["function"]["name"] for item in CUSTOMER_READ_TOOL_SCHEMAS}
    assert _ALLOWED_TOOL_NAMES == declared


def test_every_schema_entry_well_formed():
    """每个 schema 必须是合法的 OpenAI function 结构且带描述（模型据此选择）。"""
    for item in CUSTOMER_READ_TOOL_SCHEMAS:
        assert item["type"] == "function"
        fn = item["function"]
        assert fn["name"] and fn["description"], f"{fn.get('name')} 缺 name/description"
        assert fn["parameters"]["type"] == "object"


# ---------- 重复调用闸门 ----------
# ISSUE-20260914-TOOL-FAILURE-NOT-FEED-BACK：实测"我的订单 ORD-123456 状态"连续 4 轮
# 传入**完全相同**的 (工具, 参数)，4 次全失败、无任何结果，纯浪费预算与配额。

def test_identical_repeated_call_is_blocked():
    """完全相同的 (工具, 参数) 重复调用应被拦截，不再消耗预算。"""
    tools = FakeReadTools(results={
        "get_my_order_status": SimpleNamespace(success=False, data={}, answer="", error="fact_not_found")
    })

    class AlwaysSameProvider:
        def __init__(self):
            self._client = object()

        async def request_tool_call(self, *, system, user, tools, model=None, temperature=0.0):
            return {"name": "get_my_order_status", "arguments": {"query": "ORD-123456"}}

    loop = CustomerModelToolLoop(provider=AlwaysSameProvider(), tools=tools,
                                 database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1",
                                   query="我的订单 ORD-123456 状态",
                                   planned_intents=["order_status"]))
    assert len(tools.calls) == 1, f"相同调用应只执行一次，实际 {len(tools.calls)} 次"
    assert outcome.stopped_reason == "duplicate_call_skipped"


def test_duplicate_call_is_audited():
    """被拦截的重复调用也要留审计。"""
    tools = FakeReadTools(results={
        "get_my_order_status": SimpleNamespace(success=False, data={}, answer="", error="fact_not_found")
    })
    db = FakeDatabase()

    class AlwaysSameProvider:
        def __init__(self):
            self._client = object()

        async def request_tool_call(self, *, system, user, tools, model=None, temperature=0.0):
            return {"name": "get_my_order_status", "arguments": {"query": "ORD-1"}}

    loop = CustomerModelToolLoop(provider=AlwaysSameProvider(), tools=tools,
                                 database=db, max_rounds=4)
    asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="查订单"))
    assert any("duplicate_call_skipped" in row.detail for row in db.audits)


def test_successful_call_then_identical_retry_is_blocked():
    """成功结果之后若再发同一调用（参数完全相同）也应拦截——结果不会变。"""
    tools = FakeReadTools()

    class SameAfterSuccess:
        def __init__(self):
            self._client = object()

        async def request_tool_call(self, *, system, user, tools, model=None, temperature=0.0):
            return {"name": "get_my_asset_summary", "arguments": {}}

    loop = CustomerModelToolLoop(provider=SameAfterSuccess(), tools=tools,
                                 database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="查资产"))
    assert len(tools.calls) == 1
    assert len(outcome.results) == 1


def test_near_duplicate_with_different_args_is_allowed():
    """参数不同的调用（如换 query 改写）应允许——只有完全相同才拦截。"""
    tools = FakeReadTools(results={
        "get_my_order_status": SimpleNamespace(success=False, data={}, answer="", error="fact_not_found")
    })
    provider = FakeProvider([
        {"name": "get_my_order_status", "arguments": {"query": "ORD-1"}},
        {"name": "get_my_order_status", "arguments": {"query": "ORD-1 帮我查明细"}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="查订单"))
    assert len(tools.calls) == 2, "参数不同的重试不应被拦截"
    assert outcome.stopped_reason != "duplicate_call_skipped"
