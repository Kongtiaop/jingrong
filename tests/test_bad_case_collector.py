import json
import os
import shutil
import uuid

import pytest

from app.services.bad_case_collector import BadCaseStore, detect_bad_case, detect_intent_misroute, query_hash


@pytest.fixture()
def tmp_workspace():
    """工作区内临时目录（规避沙箱禁止写系统 temp）。"""
    path = os.path.join(os.getcwd(), "logs", "_bctest_" + uuid.uuid4().hex[:8])
    os.makedirs(path, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


class FakeIntent:
    def __init__(self, primary_intent="order_status", confidence=0.9, source="llm_fallback"):
        self.primary_intent = primary_intent
        self.confidence = confidence
        self.source = source


class FakeRag:
    def __init__(self, evidence_status="sufficient", handoff_required=False):
        self.evidence_status = evidence_status
        self.handoff_required = handoff_required


class FakeStructured:
    def __init__(self, template, confidence, primary_intent="order_status"):
        self.template = template
        self.confidence = confidence
        self.primary_intent = primary_intent


@pytest.fixture()
def store_path(tmp_workspace):
    return os.path.join(tmp_workspace, "bad_cases.jsonl")


def test_not_bad_case_when_all_good(store_path):
    s = BadCaseStore(store_path)
    assert s.collect("正常查询", intent=FakeIntent(), rag=FakeRag(), clarifications=0) is False


def test_detect_intent_unknown(store_path):
    s = BadCaseStore(store_path)
    assert s.collect("看不懂的话", intent=FakeIntent(primary_intent="unknown", confidence=0.2)) is True
    rows = s.load()
    assert rows and "intent_unknown" in rows[0]["reasons"]


def test_detect_low_confidence_and_rules_fallback():
    r = detect_bad_case("x", intent=FakeIntent(confidence=0.3))
    assert r and "intent_low_confidence" in r.reasons
    r2 = detect_bad_case("x", intent=FakeIntent(confidence=0.9, source="rules_fallback_needed"))
    assert r2 and "intent_rules_fallback" in r2.reasons


def test_detect_rag_bad_and_handoff():
    r = detect_bad_case("x", rag=FakeRag(evidence_status="low_confidence"))
    assert r and "rag_low_confidence" in r.reasons
    r2 = detect_bad_case("x", rag=FakeRag(handoff_required=True))
    assert r2 and "rag_handoff" in r2.reasons


def test_detect_clarify_stuck():
    r = detect_bad_case("x", clarifications=3)
    assert r and "clarify_stuck" in r.reasons


def test_dedup_same_query(store_path):
    s = BadCaseStore(store_path)
    assert s.collect("重复的坏话", intent=FakeIntent(primary_intent="unknown")) is True
    assert s.collect("重复的坏话", intent=FakeIntent(primary_intent="unknown")) is False  # 去重
    assert len(s.load()) == 1
    # 不同空白/大小写视为同一（归一化）
    assert query_hash("重 复 的 坏 话") == query_hash("重复的坏话")


def test_detect_confident_misroute_rule_trade_vs_llm_recommend():
    # "我能购买什么产品"类：规则自信路由成交易草稿(T4)，影子 LLM 高置信判产品推荐(T2)
    r = detect_intent_misroute("x", rule_intent=FakeStructured("T4_PURCHASE_DRAFT", 0.98, "business_operation"),
                               llm_intent=FakeStructured("T2_PRODUCT_RECOMMENDATION", 0.85, "product_recommendation"))
    assert r is not None
    assert "intent_misroute" in r.reasons
    assert r.rule_template == "T4_PURCHASE_DRAFT"
    assert r.llm_template == "T2_PRODUCT_RECOMMENDATION"


def test_no_misroute_when_rule_not_confident():
    # 规则不确定(<阈值)就不是 confident misroute
    r = detect_intent_misroute("x", rule_intent=FakeStructured("T4_PURCHASE_DRAFT", 0.5, "business_operation"),
                               llm_intent=FakeStructured("T2_PRODUCT_RECOMMENDATION", 0.9, "product_recommendation"))
    assert r is None


def test_no_misroute_when_shadow_not_confident():
    # 影子 LLM 只是低置信乱猜，不算可信分歧
    r = detect_intent_misroute("x", rule_intent=FakeStructured("T4_PURCHASE_DRAFT", 0.98, "business_operation"),
                               llm_intent=FakeStructured("T2_PRODUCT_RECOMMENDATION", 0.3, "product_recommendation"))
    assert r is None


def test_no_misroute_when_same_or_same_commit_level():
    # 相同模板不报；只读档内的小分歧(order vs faq)无害，不当作 misroute
    assert detect_intent_misroute("x", rule_intent=FakeStructured("T4_PURCHASE_DRAFT", 0.98), llm_intent=FakeStructured("T4_PURCHASE_DRAFT", 0.9)) is None
    assert detect_intent_misroute("x", rule_intent=FakeStructured("T1_INFORMATION_QUERY", 0.9, "faq"), llm_intent=FakeStructured("T5_ORDER_STATUS", 0.9, "order_status")) is None


def test_no_misroute_when_rule_or_llm_unknown():
    assert detect_intent_misroute("x", rule_intent=FakeStructured("T0_UNKNOWN", 0.9), llm_intent=FakeStructured("T2_PRODUCT_RECOMMENDATION", 0.9)) is None


def test_collect_misroute_store_dedup(store_path):
    s = BadCaseStore(store_path)
    rule = FakeStructured("T4_PURCHASE_DRAFT", 0.98, "business_operation")
    llm = FakeStructured("T2_PRODUCT_RECOMMENDATION", 0.85, "product_recommendation")
    assert s.collect_misroute("我能买什么产品", rule_intent=rule, llm_intent=llm) is True
    assert s.collect_misroute("我能买什么产品", rule_intent=rule, llm_intent=llm) is False  # 去重
    rows = s.load()
    assert len(rows) == 1
    assert rows[0]["reasons"] == ["intent_misroute"]
