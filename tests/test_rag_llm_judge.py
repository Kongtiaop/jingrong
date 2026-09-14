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


# ---- 回归：CustomerReadTools 必须把 ambiguous_judge 接上 ----
# ISSUE-20260914-AMBIGUOUS-JUDGE-NOT-WIRED：config/understanding.yaml 声明
# `judge_ambiguous: true`，但 customer_tools.py 构造 CustomerRagService 时未传
# ambiguous_judge → 0.35~0.65 中间带实际走死阈值 min_similarity=0.55，judge 形同虚设。
# decide_evidence 单测全绿也发现不了——缺的是"接线"这一层，故此处专门钉住。


def test_customer_read_tools_wires_ambiguous_judge():
    """检索知识工具构造 RAG 服务时必须传入 ambiguous_judge（否则配置的 judge 不生效）。

    注意：customer_tools.py 是**模块级** `from ... import CustomerRagService`，
    因此要 patch `app.services.customer_tools.CustomerRagService`（该模块自己的绑定），
    而不是源模块 customer_rag_service。
    """
    import asyncio
    from app.services import customer_tools as tools_mod

    captured = {}
    original = tools_mod.CustomerRagService

    class SpyRag:
        def __init__(self, retriever, embedding_provider=None, config=None, ambiguous_judge=None):
            captured["ambiguous_judge"] = ambiguous_judge

        async def answer(self, *, query, intent, top_k=None):
            from app.services.customer_rag_service import CustomerRagResult
            return CustomerRagResult(intent=intent, answer_mode="rag_answer",
                                     evidence_status="sufficient", citations=[],
                                     handoff_required=False, message="ok")

    tools_mod.CustomerRagService = SpyRag
    try:
        tools = tools_mod.CustomerReadTools(database=object())
        asyncio.run(tools.execute(name="search_customer_knowledge", customer_id="c1",
                                  query="什么是双录", intent="policy"))
    finally:
        tools_mod.CustomerRagService = original

    assert captured.get("ambiguous_judge") is not None, \
        "CustomerReadTools 未传 ambiguous_judge → 配置的 judge_ambiguous 不会生效"


def test_judge_ambiguous_config_is_true_so_wiring_matters():
    """若配置把 judge_ambiguous 关掉，本接线问题就不成立；钉住前提便于日后判断。"""
    from app.services.customer_rag_service import CustomerRagService
    svc = CustomerRagService(retriever=object(), embedding_provider=None)
    assert svc.thresholds.get("judge_ambiguous") is True, \
        "配置 judge_ambiguous 已改为非 true，需重新评估接线必要性"


# ---- 回归：judge 曾误杀"相关但相似度低"的引用 ----
# ISSUE-20260914-JUDGE-MISJUDGES-RELEVANT-EVIDENCE：实测 best_sim=0.5559 的检索，
# 引用内容明确在讲"风险等级匹配/C4可买R5"（与问题高度相关），judge 却判 no，
# 使该结果从 sufficient 翻转为 low_confidence。故收窄 judge 介入区间到低分区。
#
# 注意：config/understanding.yaml 在测试进程里默认未被加载（main.py lifespan 才 reload），
# 故这些测试显式 reload，否则读到的是代码内置默认值（judge 带 = 完整 ambiguous 带）。

def _reloaded_service(retriever, **kw):
    from app.services.understanding_config import understanding_config
    from app.services.customer_rag_service import CustomerRagService
    understanding_config.reload()
    return CustomerRagService(retriever, **kw)


def test_judge_band_is_narrower_than_ambiguous_band():
    """judge 只在低分区介入；高分区（judge_answer~answer）直接放行。"""
    from app.services.understanding_config import understanding_config
    understanding_config.reload()
    t = understanding_config.get_rejection_thresholds()
    assert t["judge_reject"] >= t["reject"], "judge 下界不应低于 reject 下界"
    assert t["judge_answer"] <= t["answer"], "judge 上界不应高于 answer 上界"
    assert t["judge_answer"] < t["answer"], \
        "judge 上界必须窄于 answer 上界，否则高分区仍会被 judge 误杀"


def test_high_band_score_bypasses_judge_entirely():
    """best_sim 落在 [judge_answer, answer) 时不应调用 judge（曾在此误杀 0.5559）。"""
    import asyncio

    calls = []

    async def spy_judge(query, contexts):
        calls.append(query)
        return False   # 故意总判 no：若被调用就会 reject

    class FakeCitation:
        def __init__(self, sim):
            self.chunk_id = "c1"; self.document_id = "d1"; self.version_id = "v1"
            self.content = "C4投资者购买R5产品需签风险揭示书，单只不超总资产10%"
            self.score = 0.6; self.title_path = ""; self.page_number = None
            self.metadata = {}; self.vector_similarity = sim

    class FakeRetriever:
        async def search(self, **kw):
            return [FakeCitation(0.5559)]

    svc = _reloaded_service(FakeRetriever(), embedding_provider=None,
                            ambiguous_judge=spy_judge, min_score=0.0)
    result = asyncio.run(svc.answer(query="客户存在待处理风险记录时是否影响交易权限", intent="risk_public"))
    assert calls == [], f"0.5559 落在高分区，不应调用 judge（实际调用 {len(calls)} 次）"
    assert result.handoff_required is False, "高分区应直接作答，不应因 judge 误判而转人工"


def test_low_band_score_still_uses_judge():
    """低分区（0.35~0.55）仍交给 judge 拦截明显无关引用。"""
    import asyncio

    calls = []

    async def spy_judge(query, contexts):
        calls.append(query)
        return False

    class FakeCitation:
        def __init__(self, sim):
            self.chunk_id = "c1"; self.document_id = "d1"; self.version_id = "v1"
            self.content = "无关内容"; self.score = 0.4; self.title_path = ""
            self.page_number = None; self.metadata = {}; self.vector_similarity = sim

    class FakeRetriever:
        async def search(self, **kw):
            return [FakeCitation(0.45)]

    svc = _reloaded_service(FakeRetriever(), embedding_provider=None,
                            ambiguous_judge=spy_judge, min_score=0.0)
    result = asyncio.run(svc.answer(query="无关问题", intent="risk_public"))
    assert len(calls) == 1, "低分区应调用 judge"
    assert result.handoff_required is True, "judge 判 no → 应拒答转人工"
