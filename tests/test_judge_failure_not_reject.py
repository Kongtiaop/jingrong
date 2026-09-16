# -*- coding: utf-8 -*-
"""证据门控：judge 故障不得伪装成"证据不足"。

ISSUE-20260915-JUDGE-FAILURE-MASQUERADES-AS-REJECT

背景（真实复现）：同一问句"你们公司主营业务是什么"时好时坏 ——
检索确定（sims 完全一致），但模型每次改写的措辞不同，best_sim 在 0.53~0.71 浮动；
其中 8/10 的措辞落在 judge 区间 [0.35,0.65)。此时答案**取决于一次 LLM judge 调用**，
judge 若抛异常（限流/超时/网络）就被 `except` 吞成 `judge_result=False`
→ reject → low_confidence → "知识库没有足够依据，建议转人工"。
**基础设施抖动被伪装成"数据不存在"。**
"""
from app.services.rag_eval_metrics import decide_evidence

REJECT, ANSWER, FALLBACK = 0.35, 0.65, 0.55


def _decide(best, *, judge_result=None, judge_failed=False):
    return decide_evidence(best, reject=REJECT, answer=ANSWER,
                           judge_result=judge_result, fallback=FALLBACK,
                           judge_failed=judge_failed)


def test_judge_failure_does_not_become_reject():
    """**核心回归**：judge 抛异常不得导致 reject。

    best_sim=0.5971（实测该 query 的真实值）落在 judge 区间，
    judge 故障时必须走确定性阈值兜底（0.5971 >= 0.55 → answer），
    而不是当作"judge 说证据不足"。
    """
    assert _decide(0.5971, judge_failed=True) == "answer"


def test_judge_false_still_rejects():
    """judge **真的**判定证据不足时，仍必须 reject（不能因为修 bug 而放水）。"""
    assert _decide(0.5971, judge_result=False) == "reject"


def test_judge_true_answers():
    assert _decide(0.5971, judge_result=True) == "answer"


def test_judge_failure_below_fallback_still_rejects():
    """judge 故障 + 相似度确实低 → 仍应 reject（兜底阈值照常生效）。"""
    assert _decide(0.40, judge_failed=True) == "reject"


def test_obvious_cases_unaffected_by_judge_state():
    """明显无关/明显相关的短路不受 judge 状态影响。"""
    for judge_failed in (False, True):
        assert _decide(0.10, judge_failed=judge_failed) == "reject"
        assert _decide(0.90, judge_failed=judge_failed) == "answer"


def test_no_judge_uses_fallback_threshold():
    """无 judge（judge_result=None）时走 fallback 阈值，行为与 judge 故障一致。"""
    assert _decide(0.5971) == _decide(0.5971, judge_failed=True)


def test_judge_failure_vs_no_judge_consistency():
    """未接 judge 与 judge 故障应得到同一结论 —— 故障只是"失去增强"，不改变判定。"""
    for sim in (0.20, 0.36, 0.50, 0.56, 0.64, 0.66, 0.95):
        assert _decide(sim, judge_failed=True) == _decide(sim), f"sim={sim} 不一致"
