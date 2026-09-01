import asyncio
from types import SimpleNamespace

from app.schemas.intent import StructuredIntent
from app.services.customer_agent_execution import CustomerAgentExecutionService
from app.services.customer_intent import CustomerIntentPlanner


class FakeSession:
    def add(self, _row):
        pass

    async def commit(self):
        pass


class FakeFactory:
    def __call__(self):
        class Context:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, *_args):
                return False

        return Context()


class FakeFallback:
    async def classify(self, **_kwargs):
        return StructuredIntent(template="T2_PRODUCT_RECOMMENDATION", primary_intent="product_recommendation", confidence=0.8, source="llm_fallback")


def test_shadow_audit_does_not_enable_execution_fallback():
    settings = SimpleNamespace(llm_intent_shadow_enabled=True, llm_intent_fallback_enabled=False, llm_intent_fallback_timeout_seconds=1)
    service = CustomerAgentExecutionService(SimpleNamespace(session_factory=FakeFactory()), None, intent_fallback=FakeFallback(), settings=settings)
    assert service.settings.llm_intent_shadow_enabled is True
    assert service.settings.llm_intent_fallback_enabled is False
    asyncio.run(service._audit_intent_shadow(
        customer_id="customer-1", conversation_id="conversation-1", query="推荐一下",
        rule_intent=StructuredIntent(template="T0_UNKNOWN", primary_intent="unknown", confidence=0.1, source="rule"),
        llm_intent=None, resolved_intent=None, error=None,
    ))


def test_allowlisted_recommendation_can_change_plan_but_trade_cannot():
    settings = SimpleNamespace(
        llm_intent_shadow_enabled=False, llm_intent_fallback_enabled=True,
        llm_intent_fallback_timeout_seconds=1,
        llm_intent_fallback_allowlist="T1_INFORMATION_QUERY,T2_PRODUCT_RECOMMENDATION,T7_HUMAN_HANDOFF",
    )
    service = CustomerAgentExecutionService(SimpleNamespace(session_factory=FakeFactory()), None, settings=settings)
    rule_plan = CustomerIntentPlanner().plan("这个问题我说不清")
    recommendation = StructuredIntent(template="T2_PRODUCT_RECOMMENDATION", primary_intent="product_recommendation", confidence=0.8, source="llm_fallback")
    changed = service._apply_structured_intent(rule_plan, recommendation)
    assert changed.tasks[0].intent == "product_recommendation"

    trade = StructuredIntent(template="T4_PURCHASE_DRAFT", primary_intent="purchase_draft", confidence=0.9, source="llm_fallback")
    unchanged = service._apply_structured_intent(rule_plan, trade)
    assert unchanged == rule_plan


def test_allowlisted_handoff_can_change_plan():
    settings = SimpleNamespace(
        llm_intent_shadow_enabled=False, llm_intent_fallback_enabled=True,
        llm_intent_fallback_timeout_seconds=1,
        llm_intent_fallback_allowlist="T7_HUMAN_HANDOFF",
    )
    service = CustomerAgentExecutionService(SimpleNamespace(session_factory=FakeFactory()), None, settings=settings)
    plan = CustomerIntentPlanner().plan("有个复杂问题")
    handoff = StructuredIntent(template="T7_HUMAN_HANDOFF", primary_intent="human_handoff", confidence=0.8, source="llm_fallback")
    changed = service._apply_structured_intent(plan, handoff)
    assert changed.requires_handoff is True
    assert changed.tasks[0].intent == "human_handoff"


def test_allowlisted_information_query_maps_only_to_public_rag():
    settings = SimpleNamespace(
        llm_intent_shadow_enabled=False, llm_intent_fallback_enabled=True,
        llm_intent_fallback_timeout_seconds=1,
        llm_intent_fallback_allowlist="T1_INFORMATION_QUERY",
    )
    service = CustomerAgentExecutionService(SimpleNamespace(session_factory=FakeFactory()), None, settings=settings)
    plan = CustomerIntentPlanner().plan("这句话规则看不懂")
    policy = StructuredIntent(template="T1_INFORMATION_QUERY", primary_intent="policy", confidence=0.8, source="llm_fallback")
    changed = service._apply_structured_intent(plan, policy)
    assert changed.tasks[0].intent == "policy"
    assert changed.tasks[0].route == "rag_explanation"

    unsafe = StructuredIntent(template="T1_INFORMATION_QUERY", primary_intent="order_status", confidence=0.8, source="llm_fallback")
    contained = service._apply_structured_intent(plan, unsafe)
    assert contained.tasks[0].intent == "faq"
    assert contained.tasks[0].route == "rag_explanation"


def test_fallback_concurrency_limit_is_at_least_one():
    settings = SimpleNamespace(
        llm_intent_shadow_enabled=False, llm_intent_fallback_enabled=False,
        llm_intent_fallback_timeout_seconds=1, llm_intent_fallback_allowlist="T1_INFORMATION_QUERY",
        llm_intent_fallback_max_concurrency=0,
    )
    service = CustomerAgentExecutionService(SimpleNamespace(session_factory=FakeFactory()), None, settings=settings)
    assert service._fallback_semaphore()._value == 1
