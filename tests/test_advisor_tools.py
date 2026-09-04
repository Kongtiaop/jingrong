"""advisor_tools 只读工具层：真实 product.risk/amount 与客户适当性命中（fake uow，不碰 LLM/DB）。"""
import asyncio
from types import SimpleNamespace

from app.schemas.profile import SuitabilityResult
from app.services.advisor_tools import AdvisorReadTools, product_facts_to_text


def _product(id, name, risk_level="C3", minimum_amount=10000, product_type="fund", term_days=0, description=""):
    return SimpleNamespace(id=id, name=name, risk_level=risk_level, minimum_amount=minimum_amount,
                           product_type=product_type, term_days=term_days, description=description, liquidity="medium")


class _FakeProducts:
    def __init__(self, rows):
        self._rows = rows
    async def list_active(self, session):
        return self._rows


class _FakeUow:
    def __init__(self, products):
        self.products = _FakeProducts(products)
        self.risks = None
        self.profiles = None
        self.accounts = None
        self.holdings = None
        self.assets = None


def _tools(products):
    return AdvisorReadTools(_FakeUow(products))


def test_find_products_returns_real_fields():
    rows = [
        _product("p1", "中短债增利计划", risk_level="C2", minimum_amount=10000),
        _product("p2", "稳健增值计划", risk_level="C2"),
        _product("p3", "成长精选组合", risk_level="C4"),
    ]
    out = asyncio.run(_tools(rows).find_products(object(), query="中短债"))
    assert len(out) == 1
    assert out[0].name == "中短债增利计划"
    assert out[0].risk_level == "C2"
    assert out[0].minimum_amount == 10000


def test_find_products_empty_when_no_match():
    rows = [_product("p1", "中短债增利计划", risk_level="C2")]
    out = asyncio.run(_tools(rows).find_products(object(), query="现金"))
    assert out == []


def test_product_facts_text_grounds_risk_level():
    rows = [_product("p1", "中短债增利计划", risk_level="C2", minimum_amount=10000)]
    facts = asyncio.run(_tools(rows).find_products(object(), query="中短债"))
    text = product_facts_to_text(facts)
    assert "中短债增利计划" in text
    assert "C2" in text
    assert "10,000" in text


async def _fake_recommend(self, session, customer_id, *, risk_operation=None, preferences=None, top_n=5, **kwargs):
    from types import SimpleNamespace as NS
    matched = [SuitabilityResult(product_id="p1", product_name="中短债增利计划", product_type="fund",
                                 product_risk_level="C2", minimum_amount=10000, minimum_investable_asset=10000,
                                 liquidity="medium", term_days=0, suitability_rule="", matched=True,
                                 reasons=["客户当前风险等级 C5 满足产品要求 C2。"], recommendation_reason="")]
    excluded = []
    return NS(matches=matched, excluded=excluded)


def test_product_suitability_returns_single_hit():
    rows = [_product("p1", "中短债增利计划", risk_level="C2", minimum_amount=10000)]
    tools = _tools(rows)
    tools._suitability.recommend = _fake_recommend.__get__(tools._suitability)
    facts = asyncio.run(tools.product_suitability(object(), customer_id="c1", product_id="p1"))
    assert len(facts) == 1
    assert facts[0].product_name == "中短债增利计划"
    assert facts[0].matched is True
    assert any("C5" in r for r in facts[0].reasons)
