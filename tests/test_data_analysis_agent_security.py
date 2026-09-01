from __future__ import annotations

from app.services.data_analysis_agent import DataAnalysisAgent


def test_validate_template_rejects_write_and_multi_statement():
    assert not DataAnalysisAgent.validate_template("UPDATE users SET name='x'")[0]
    assert not DataAnalysisAgent.validate_template("SELECT 1; SELECT 2")[0]
    assert not DataAnalysisAgent.validate_template("SELECT 1 -- bypass")[0]


def test_validate_catalog_allowlist_rejects_unlisted_table_and_column():
    class Template:
        sql_template = "SELECT id, secret FROM orders"
        target_table_names_json = ["orders"]
        target_column_names_json = ["id"]
        allowed_roles_json = ["analyst"]

    assert DataAnalysisAgent.validate_catalog_allowlist(Template())[0] is False


def test_validate_catalog_allowlist_accepts_declared_references():
    class Template:
        sql_template = "SELECT order_id, status FROM orders WHERE status = :status"
        target_table_names_json = ["orders"]
        target_column_names_json = ["order_id", "status"]
        allowed_roles_json = ["analyst"]

    ok, reason = DataAnalysisAgent.validate_catalog_allowlist(Template())
    assert ok, reason


def test_parameter_limits_and_bounded_defaults():
    params, reason = DataAnalysisAgent.bind_template_parameters("SELECT 1", "")
    assert reason == ""
    assert params is not None
    assert params["limit"] == DataAnalysisAgent.MAX_RESULT_ROWS
    assert params["end_date"] >= params["start_date"]
    assert DataAnalysisAgent._validate_parameters(params)[0]
    assert not DataAnalysisAgent._validate_parameters({"limit": 101})[0]
    assert not DataAnalysisAgent._validate_parameters({"value": "x" * 257})[0]


def test_parameter_placeholder_is_not_treated_as_column():
    class Template:
        sql_template = "SELECT order_id, status FROM orders WHERE status = :status"
        target_table_names_json = ["orders"]
        target_column_names_json = ["order_id", "status"]
        allowed_roles_json = ["analyst"]
        metadata_json = {}

    ok, reason = DataAnalysisAgent.validate_catalog_allowlist(Template())
    assert ok, reason


def test_template_match_score_prefers_exact_question():
    class Template:
        question = "待审核订单数量"
        metadata_json = {"match_terms": ["订单"]}

    assert DataAnalysisAgent.template_match_score(Template(), "待审核订单数量") > DataAnalysisAgent.template_match_score(Template(), "订单")


def test_catalog_column_allowlist_does_not_require_nonexistent_is_active_field():
    class Template:
        sql_template = "SELECT order_id FROM orders"
        target_table_names_json = ["orders"]
        target_column_names_json = ["order_id"]
        allowed_roles_json = ["analyst"]

    class Column:
        column_name = "order_id"
        is_queryable = True
        is_sensitive = False

    ok, reason = DataAnalysisAgent.validate_catalog_allowlist(Template(), catalog_columns=[Column()])
    assert ok, reason


def test_template_attempt_reports_rejection_reason_without_executing_sql():
    class Template:
        id = "template-1"
        is_active = True
        is_approved = True
        allowed_roles_json = ["analyst"]
        sql_template = "UPDATE orders SET status = 'x'"
        target_table_names_json = ["orders"]
        target_column_names_json = ["status"]

    class Role:
        code = "analyst"

    class User:
        is_super_admin = False
        roles = [Role()]

    async def run():
        return await DataAnalysisAgent().analyze_template_attempt(None, user=User(), template=Template(), query="订单")

    import asyncio
    attempt = asyncio.run(run())
    assert attempt.result is None
    assert attempt.template_id == "template-1"
    assert attempt.reason.startswith("invalid_sql:")


def test_catalog_allowlist_requires_retrieved_table_and_column_context():
    class Template:
        sql_template = "SELECT order_id FROM orders"
        target_table_names_json = ["orders"]
        target_column_names_json = ["order_id"]
        allowed_roles_json = ["analyst"]

    class Table:
        table_name = "orders"
        is_active = True
        is_queryable = True
        allowed_roles_json = ["analyst"]

    class Column:
        column_name = "order_id"
        is_queryable = True
        is_sensitive = False

    ok, reason = DataAnalysisAgent.validate_catalog_allowlist(Template(), catalog_tables=[Table()], catalog_columns=[Column()])
    assert ok, reason
    denied, reason = DataAnalysisAgent.validate_catalog_allowlist(Template(), catalog_tables=[], catalog_columns=[])
    assert not denied
    assert "outside active queryable catalog" in reason


def test_query_safety_refuses_direct_sql_and_allows_natural_language():
    assert DataAnalysisAgent.evaluate_query_safety("SELECT * FROM orders")[0] is False
    assert DataAnalysisAgent.evaluate_query_safety("DROP TABLE orders")[0] is False
    assert DataAnalysisAgent.evaluate_query_safety("待审核订单有多少")[0] is True


def test_catalog_allowlist_accepts_qualified_template_column_declarations():
    class Template:
        sql_template = "SELECT order_id, status FROM orders WHERE status = :status"
        target_table_names_json = ["public.orders"]
        target_column_names_json = ["orders.order_id", "orders.status"]
        allowed_roles_json = ["analyst"]

    ok, reason = DataAnalysisAgent.validate_catalog_allowlist(Template())
    assert ok, reason


def test_declared_parameter_schema_rejects_excessive_limit_and_date_range():
    class Template:
        metadata_json = {"parameters": {"limit": {"type": "integer", "source": "natural_language", "min": 1, "max": 100, "default": 100}, "date_range": {"type": "date_range", "source": "natural_language", "max_range_days": 90}}}

    params, reason = DataAnalysisAgent.bind_template_parameters("SELECT 1", "查看前10000条", template=Template())
    assert params is None and reason == "parameter_out_of_range:limit"
    params, reason = DataAnalysisAgent.bind_template_parameters("SELECT 1", "查询过去365天", template=Template())
    assert params is None and reason == "parameter_out_of_range:date_range"


def test_declared_subject_parameter_is_bound_to_authorized_user():
    class Template:
        metadata_json = {"parameters": {"user_id": {"type": "subject_id", "source": "authorized_scope_only"}}}

    class User:
        id = "authorized-user"

    params, reason = DataAnalysisAgent.bind_template_parameters("SELECT 1", "查询客户 other-user 的资产", template=Template(), user=User())
    assert params is None and reason == "unauthorized_subject:user_id"
    params, reason = DataAnalysisAgent.bind_template_parameters("SELECT 1", "查询资产", template=Template(), user=User())
    assert params and params["user_id"] == "authorized-user"


def test_parameter_reason_clarifications_are_safe_and_actionable():
    class Template:
        metadata_json = {"parameters": {
            "limit": {"type": "integer", "source": "natural_language", "min": 1, "max": 100},
            "date_range": {"type": "date_range", "source": "natural_language", "max_range_days": 90},
            "user_id": {"type": "subject_id", "source": "authorized_scope_only"},
        }}

    assert "1 到 100" in DataAnalysisAgent.clarification_for_parameter_reason("parameter_out_of_range:limit", template=Template())
    assert "最近 90 天" in DataAnalysisAgent.clarification_for_parameter_reason("parameter_out_of_range:date_range", template=Template())
    assert "客户 ID" in DataAnalysisAgent.clarification_for_parameter_reason("missing_authorized_subject:user_id", template=Template())
    assert DataAnalysisAgent.clarification_for_parameter_reason("unauthorized_subject:user_id", template=Template()) is None

def test_catalog_allowlist_accepts_repeated_declared_column_in_where_clause():
    class Template:
        sql_template = "SELECT status FROM risk_alert WHERE status = 'open' LIMIT :limit"
        target_table_names_json = ["risk_alert"]
        target_column_names_json = ["risk_alert.status"]
        allowed_roles_json = ["risk_manager"]

    ok, reason = DataAnalysisAgent.validate_catalog_allowlist(Template())
    assert ok, reason

