import json
import os
import shutil
import uuid

import pytest

from app.services.bad_case_collector import BadCaseStore, detect_bad_case, query_hash


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
