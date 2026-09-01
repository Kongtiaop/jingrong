import ast
from pathlib import Path


def test_intent_shadow_metrics_endpoint_aggregates_safe_observations():
    source = Path("app/api/admin.py").read_text(encoding="utf-8")
    assert "/intent-shadow/metrics" in source
    assert "customer_agent.intent_shadow_evaluated" in source
    assert "unknown_rule_trade_blocked" in source
    assert "fallback_decision" in source
    endpoint = source[source.index('async def intent_shadow_metrics'):source.index('@router.put', source.index('async def intent_shadow_metrics'))]
    assert 'payload.get("query")' not in endpoint
    assert '"query":' not in endpoint
    ast.parse(source)
