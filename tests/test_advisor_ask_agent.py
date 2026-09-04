"""AdvisorBoundedLoop 确定性单次接地：命中产品则 find_products+product_suitability 一次 attach，
未命中则直接透传（fake tools/db，不碰 LLM/DB）。"""
import asyncio

from app.services.advisor_ask_agent import AdvisorBoundedLoop


class _FakeSession:
    def add(self, obj):
        pass
    async def commit(self):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False


class _FakeDb:
    def session_factory(self):
        return _FakeSession()


class _FakeProvider:
    def __init__(self):
        self.requested = 0
    async def request_tool_call(self, **kwargs):
        return None  # 新实现不再用于工具选择轮


class _Product:
    def __init__(self, id, name, risk_level, minimum_amount, product_type="fund", term_days=0, description=""):
        self.id = id
        self.name = name
        self.risk_level = risk_level
        self.minimum_amount = minimum_amount
        self.product_type = product_type
        self.term_days = term_days
        self.description = description
        self.liquidity = "medium"


class _SuitFact:
    def __init__(self, product_id, product_name, product_risk_level, matched, reasons=None):
        self.product_id = product_id
        self.product_name = product_name
        self.product_risk_level = product_risk_level
        self.minimum_amount = 10000
        self.matched = matched
        self.reasons = reasons or []


class _FakeTools:
    def __init__(self, products, suits=None):
        self._products = products or []
        self._suits = suits or {}
    async def find_products(self, session, *, query, top_k=6):
        # 简单匹配：query 非空就返回首个（若库里存在）
        return self._products[:top_k] if query.strip() else []
    async def product_suitability(self, session, *, customer_id, product_id=None):
        return self._suits.get(product_id, [])


def _loop_with(tools, audits):
    loop = AdvisorBoundedLoop(_FakeProvider(), tools, _FakeDb())

    class Patch:
        @staticmethod
        async def fake_audit(self, conversation_id, actor_user_id, name, arguments, had_result):
            audits.append((name, arguments, had_result))

    import app.services.advisor_ask_agent as aaa
    orig = aaa.AdvisorBoundedLoop._audit_tool
    aaa.AdvisorBoundedLoop._audit_tool = Patch.fake_audit.__get__(loop, AdvisorBoundedLoop)
    return loop, lambda: setattr(aaa.AdvisorBoundedLoop, "_audit_tool", orig)


def test_grounds_when_product_hit():
    audits = []
    p = _Product("p1", "中短债增利计划", "C2", 10000)
    suit = _SuitFact("p1", "中短债增利计划", "C2", True, ["C5 满足 C2"])
    tools = _FakeTools([p], suits={"p1": [suit]})
    loop, restore = _loop_with(tools, audits)
    ctx = "客户画像：C5\n员工问题：中短债增利计划适不适合这位客户"
    final, had = asyncio.run(loop.run_to_text_user(
        conversation_id="c", actor_user_id="u", customer_id="cc",
        query="中短债增利计划适不适合这位客户", context_user=ctx, system="s"))
    restore()
    assert had is True
    assert "中短债增利计划" in final
    assert "C2" in final
    assert "命中" in final
    assert audits  # 落了 audit


def test_passthrough_when_no_product():
    audits = []
    loop, restore = _loop_with(_FakeTools([]), audits)
    ctx = "客户画像：C5\n员工问题：适合配置激进型产品吗"
    final, had = asyncio.run(loop.run_to_text_user(
        conversation_id="c", actor_user_id="u", customer_id="cc",
        query="适合配置激进型产品吗", context_user=ctx, system="s"))
    restore()
    assert had is False
    assert final == ctx  # 纯透传，不改
