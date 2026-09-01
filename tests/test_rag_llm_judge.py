import json
import os

from scripts.run_rag_llm_judge import judge_prompt, score_judge_result, PASS_THRESHOLD
from app.services.rag_eval_metrics import decide_evidence, evaluate_case


def test_judge_prompt_includes_parts():
    p = judge_prompt("问题", ["引用A"], "答案", "金标准")
    assert "问题" in p and "引用A" in p and "答案" in p and "金标准" in p


def test_score_judge_result_parses_json():
    r = score_judge_result('{"faithfulness":4,"relevancy":5,"correctness":2}')
    assert r["faithfulness"] is True   # 4 >= 3
    assert r["relevancy"] is True      # 5 >= 3
    assert r["correctness"] is False   # 2 < 3


def test_score_judge_result_handles_fence_and_garbage():
    r = score_judge_result('```json\n{"faithfulness":5,"relevancy":1,"correctness":4}\n```')
    assert r["faithfulness"] is True and r["relevancy"] is False
    bad = score_judge_result("模型没返回JSON")
    assert bad == {"faithfulness": False, "relevancy": False, "correctness": False}


def test_deterministic_checks_reused():
    c = {"requires_rejection": False, "forbidden_terms": ["保证收益"]}
    det = evaluate_case(c, handoff_required=False, answer="保证收益，稳赚", citations_count=1)
    assert det["compliance"] is False
    assert det["correct_rejection"] is True


def test_eval_cases_exist():
    with open(os.path.join("scripts", "rag_answer_eval_cases.json"), encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    assert cases and all(c["id"] for c in cases)


# ---- 分层证据决策（decide_evidence） ----
REJECT, ANSWER = 0.35, 0.65


def test_clear_reject_below_floor():
    assert decide_evidence(0.26, reject=REJECT, answer=ANSWER) == "reject"


def test_clear_answer_above_ceiling():
    assert decide_evidence(0.70, reject=REJECT, answer=ANSWER) == "answer"


def test_ambiguous_uses_judge_result():
    assert decide_evidence(0.53, reject=REJECT, answer=ANSWER, judge_result=True) == "answer"
    assert decide_evidence(0.53, reject=REJECT, answer=ANSWER, judge_result=False) == "reject"


def test_ambiguous_falls_back_to_threshold_without_judge():
    # 无 judge 时退回 fallback 阈值
    assert decide_evidence(0.53, reject=REJECT, answer=ANSWER, fallback=0.55) == "reject"
    assert decide_evidence(0.58, reject=REJECT, answer=ANSWER, fallback=0.55) == "answer"


def test_ambiguous_edge_boundary():
    # 恰在 reject 边界（>= 不算 reject）
    assert decide_evidence(0.35, reject=REJECT, answer=ANSWER, judge_result=False) == "reject"
    assert decide_evidence(0.35, reject=REJECT, answer=ANSWER, judge_result=True) == "answer"
