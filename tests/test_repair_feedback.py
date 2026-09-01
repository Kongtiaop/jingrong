import json
import os
import shutil
import uuid

import pytest

from scripts.auto_repair_intent import _load_feedback, _record_feedback, _validate_candidate


@pytest.fixture()
def fb_path(monkeypatch):
    import scripts.auto_repair_intent as m
    path = os.path.join("logs", f"_fb_{uuid.uuid4().hex[:8]}.jsonl")
    monkeypatch.setattr(m, "FEEDBACK_PATH", path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_validate_good_candidates():
    assert _validate_candidate({"change_type": "rule_mapping", "intent": "risk_restriction", "keywords": ["帮我看看风险"]}) == (True, "")
    assert _validate_candidate({"change_type": "few_shot", "query": "q", "output": '{"primary_intent":"business_operation","template":"T4_PURCHASE_DRAFT"}'}) == (True, "")


def test_validate_rejects_malformed_fewshot():
    # 之前出现的 sub_intent 乱 schema → 应被拒
    ok, reason = _validate_candidate({"change_type": "few_shot", "query": "我要买1000元", "output": '{"intent":"business_operation","sub_intent":"purchase"}'})
    assert not ok
    assert "primary_intent" in reason
    ok2, _ = _validate_candidate({"change_type": "few_shot", "query": "q", "output": "not-json"})
    assert not ok2


def test_validate_rejects_bad_intent_and_empty_keywords():
    assert _validate_candidate({"change_type": "rule_mapping", "intent": "not_an_intent", "keywords": ["x"]})[0] is False
    assert _validate_candidate({"change_type": "rule_mapping", "intent": "policy", "keywords": []})[0] is False
    assert _validate_candidate({"change_type": "weird"})[0] is False


def test_feedback_roundtrip(fb_path):
    _record_feedback({"change_type": "rule_mapping", "intent": "risk_restriction", "keywords": ["帮我看看风险"]}, "accepted", "合理")
    _record_feedback({"change_type": "few_shot", "query": "q", "output": "bad"}, "rejected", "schema 失败")
    fb = _load_feedback(limit=10)
    assert any("risk_restriction" in json.dumps(a) for a in fb["accepted"])
    assert any(r["candidate"].get("change_type") == "few_shot" for r in fb["rejected"])
