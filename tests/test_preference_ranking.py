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


def test_recommendation_reason_uses_preferences():
    assert "保守" in SuitabilityService._recommendation_reason("C2", 0, {"risk_preference": "conservative"})
    assert "长期持有" in SuitabilityService._recommendation_reason("C3", 365, {"investment_horizon_preference": "long_term"})
    assert "稳健" in SuitabilityService._recommendation_reason("C1", 0, {"product_preference": "steady"})
    assert "适当性" in SuitabilityService._recommendation_reason("C2", 0, {})


def _product(i):
    return SimpleNamespace(
        id=f"prod-{i}", name=f"稳健产品{i}", product_type="wealth", risk_level="C1", term_days=90,
        minimum_amount=100, liquidity="medium", target_customer_type="individual", status="active",
    )


class _EmptyRules:
    def scalars(self):
        return _EmptyRules()

    def all(self):
        return []


class _FakeSession:
    async def execute(self, _stmt):
        return _EmptyRules()


async def _recommend(many: int):
    risk = SimpleNamespace(risk_level="C1", status="active")
    profile = SimpleNamespace(customer_type="individual")
    asset = SimpleNamespace(total_asset=1_000_000, investable_asset=500_000)

    async def _get_active(_session, _user_id):
        return risk

    async def _latest_or_derived(_session, _user_id):
        return asset

    async def _get_profile(_session, _user_id):
        return profile

    async def _list_active(_session):
        return [_product(i) for i in range(many)]

    service = SuitabilityService(
        risk_repository=SimpleNamespace(get_active=_get_active),
        product_repository=SimpleNamespace(list_active=_list_active),
        profile_repository=SimpleNamespace(get=_get_profile),
        asset_service=SimpleNamespace(latest_or_derived=_latest_or_derived),
    )
    return await service.recommend(_FakeSession(), "u", preferences={"risk_preference": "conservative"}, top_n=5)


def test_recommend_top_n_caps_and_annotates_each_match():
    # 10 个都通过适当性门禁的产品 → 应只精选返回 top_n=5，且每条带逐条推荐理由
    import asyncio
    resp = asyncio.run(_recommend(many=10))
    assert len(resp.matches) == 5
    for m in resp.matches:
        assert m.recommendation_reason and m.recommendation_reason.strip()
    assert resp.preference_note.startswith("满足适当性的产品共 10 个")
    assert "精选前 5" in resp.preference_note


def test_recommend_under_top_n_keeps_all_and_lists_note():
    import asyncio
    resp = asyncio.run(_recommend(many=3))
    assert len(resp.matches) == 3
    assert all(m.recommendation_reason for m in resp.matches)
    assert "以下 3 个产品通过适当性匹配" in resp.preference_note
