"""偏好软排序：推荐里按用户偏好调整匹配产品顺序，但不动适当性硬门禁。"""
from types import SimpleNamespace

from app.services.suitability_service import SuitabilityService


def _p(risk_level, term_days=0):
    return SimpleNamespace(product_risk_level=risk_level, term_days=term_days)


def _risk(score, level):
    return score(_p(level))[0]


def test_conservative_prefers_low_risk():
    score = SuitabilityService._preference_score_mapper({"risk_preference": "conservative"})
    assert score is not None
    assert _risk(score, "C1") == 0
    assert _risk(score, "C2") == 0
    assert _risk(score, "C3") == 1
    assert _risk(score, "C5") == 1


def test_aggressive_prefers_high_risk():
    score = SuitabilityService._preference_score_mapper({"risk_preference": "aggressive"})
    assert _risk(score, "C5") == 0
    assert _risk(score, "C1") == 1


def test_product_steady_prefers_low_risk():
    score = SuitabilityService._preference_score_mapper({"product_preference": "steady"})
    assert _risk(score, "C2") == 0
    assert _risk(score, "C4") == 1


def test_no_preference_returns_none():
    assert SuitabilityService._preference_score_mapper({}) is None
    assert SuitabilityService._preference_score_mapper({"risk_preference": "whatever"}) is None


def test_stable_sort_puts_preferred_first():
    score = SuitabilityService._preference_score_mapper({"risk_preference": "conservative"})
    items = [_p("C3"), _p("C1"), _p("C2"), _p("C4")]
    # 稳定排序：C1、C2（risk score0）排前且保持原相对顺序；C3、C4（score1）排后
    assert [i.product_risk_level for i in sorted(items, key=score)] == ["C1", "C2", "C3", "C4"]


def test_horizon_long_term_prefers_long_terms():
    score = SuitabilityService._preference_score_mapper({"investment_horizon_preference": "long_term"})
    assert score is not None
    # 长期持有：≥180天记长期(0)，否则(1)
    assert score(_p("C2", term_days=365))[1] == 0
    assert score(_p("C2", term_days=90))[1] == 1
    assert score(_p("C2", term_days=0))[1] == 1  # 活期不算长期


def test_horizon_long_term_sorts_longer_terms_first():
    score = SuitabilityService._preference_score_mapper({"investment_horizon_preference": "long_term"})
    items = [_p("C3", term_days=0), _p("C3", term_days=365), _p("C3", term_days=180)]
    # 稳定排序：365、180（horizon 0）在前；0（horizon 1）在后
    assert [i.term_days for i in sorted(items, key=score)] == [365, 180, 0]


def test_horizon_and_risk_combine():
    score = SuitabilityService._preference_score_mapper({
        "risk_preference": "conservative", "investment_horizon_preference": "long_term"})
    # 风险与期限都对齐 → (0,0)
    assert score(_p("C1", term_days=365)) == (0, 0)
    # 风险对齐但期限不符 → (0,1)
    assert score(_p("C1", term_days=30)) == (0, 1)
    # 风险不符但期限对齐 → (1,0)
    assert score(_p("C4", term_days=365)) == (1, 0)
