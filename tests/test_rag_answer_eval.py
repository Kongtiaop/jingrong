import json
import os

from app.services.rag_eval_metrics import evaluate_case

CASES = os.path.join("scripts", "rag_answer_eval_cases.json")


def _load():
    with open(CASES, encoding="utf-8") as f:
        return json.load(f)["cases"]


def test_correct_rejection_when_should_handoff():
    c = {"requires_rejection": True, "forbidden_terms": []}
    assert evaluate_case(c, handoff_required=True, answer="", citations_count=0)["correct_rejection"] is True
    assert evaluate_case(c, handoff_required=False, answer="回答", citations_count=1)["correct_rejection"] is False


def test_correct_rejection_when_should_answer():
    c = {"requires_rejection": False, "forbidden_terms": []}
    assert evaluate_case(c, handoff_required=False, answer="公司累计客户...", citations_count=2)["correct_rejection"] is True


def test_compliance_flags_forbidden_term():
    c = {"requires_rejection": False, "forbidden_terms": ["保证收益", "保本保息"]}
    assert evaluate_case(c, handoff_required=False, answer="这个产品保证收益，稳赚", citations_count=1)["compliance"] is False
    assert evaluate_case(c, handoff_required=False, answer="投资有风险，不承诺保本", citations_count=1)["compliance"] is True


def test_has_citations_required_when_answered():
    c = {"requires_rejection": False, "forbidden_terms": []}
    assert evaluate_case(c, handoff_required=False, answer="答", citations_count=0)["has_citations"] is False
    assert evaluate_case(c, handoff_required=False, answer="答", citations_count=2)["has_citations"] is True
    # 拒答时不需要引用
    c2 = {"requires_rejection": True, "forbidden_terms": []}
    assert evaluate_case(c2, handoff_required=True, answer="", citations_count=0)["has_citations"] is True


def test_eval_set_shape():
    cases = _load()
    assert cases
    for c in cases:
        assert c["id"] and c["query"] and "requires_rejection" in c
        assert isinstance(c.get("forbidden_terms", []), list)
