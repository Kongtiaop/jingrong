from __future__ import annotations

import importlib.util
from pathlib import Path


def _policy():
    path = Path(__file__).resolve().parents[1] / "app" / "services" / "nl2sql_policy.py"
    spec = importlib.util.spec_from_file_location("nl2sql_policy_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_query_safety_and_metric_route_are_shared_policy():
    policy = _policy()
    assert policy.evaluate_query_safety("SELECT * FROM orders") == (False, "free_sql_not_supported")
    assert policy.evaluate_query_safety("DROP TABLE orders") == (False, "unsafe_sql_shaped_input")
    assert policy.metric_code_for_query("待审核订单有多少") == "pending_order_count"


def test_permission_and_catalog_scope_policy():
    policy = _policy()
    assert policy.permission_allows(user_roles={"risk_manager"}, is_super_admin=False, allowed_roles=["risk_manager"])
    assert not policy.permission_allows(user_roles={"operations"}, is_super_admin=False, allowed_roles=["risk_manager"])
    assert policy.catalog_scope_allows(referenced_tables={"orders"}, referenced_columns={"order_id"}, retrieved_tables={"orders"}, retrieved_columns={"order_id"}) == (True, "")
    assert policy.catalog_scope_allows(referenced_tables={"orders"}, referenced_columns={"order_id"}, retrieved_tables=set(), retrieved_columns=set()) == (False, "catalog_table_scope_denied")
