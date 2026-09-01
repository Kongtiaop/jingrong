"""偏好软排序：推荐里按用户偏好调整匹配产品顺序，但不动适当性硬门禁。"""
from app.services.suitability_service import SuitabilityService


def test_conservative_prefers_low_risk():
    score = SuitabilityService._preference_score_mapper({"risk_preference": "conservative"})
    assert score is not None
    assert score("C1") == 0
    assert score("C2") == 0
    assert score("C3") == 1
    assert score("C5") == 1


def test_aggressive_prefers_high_risk():
    score = SuitabilityService._preference_score_mapper({"risk_preference": "aggressive"})
    assert score("C5") == 0
    assert score("C1") == 1


def test_product_steady_prefers_low_risk():
    score = SuitabilityService._preference_score_mapper({"product_preference": "steady"})
    assert score("C2") == 0
    assert score("C4") == 1


def test_no_preference_returns_none():
    assert SuitabilityService._preference_score_mapper({}) is None
    assert SuitabilityService._preference_score_mapper({"risk_preference": "whatever"}) is None


def test_stable_sort_puts_preferred_first():
    score = SuitabilityService._preference_score_mapper({"risk_preference": "conservative"})
    levels = ["C3", "C1", "C2", "C4"]
    # 稳定排序：C1、C2（score0）排前且保持原相对顺序；C3、C4（score1）排后
    assert sorted(levels, key=score) == ["C1", "C2", "C3", "C4"]
