from __future__ import annotations

import re

MAX_SQL_LENGTH = 20_000
MAX_RESULT_ROWS = 100
MAX_PARAMETER_COUNT = 12
MAX_STRING_PARAMETER_LENGTH = 256
MAX_DATE_RANGE_DAYS = 366

SAFE_SELECT = re.compile(r"^\s*(?:with\b[\s\S]*?select|select)\b", re.IGNORECASE)
BLOCKED_SQL = re.compile(r"(;|--|/\*|\*/|\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|execute|call|pg_sleep|into)\b)", re.IGNORECASE)

METRIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("open_risk_alert_count", ("风险预警", "预警数量", "多少预警")),
    ("pending_order_count", ("待审核订单", "订单数量", "多少订单")),
    ("pending_operation_count", ("待审核业务", "待审核操作", "赎回转账")),
    ("active_product_count", ("在售产品", "产品数量", "多少产品")),
    ("latest_total_asset_sum", ("总资产", "资产总额", "资产规模")),
)


def evaluate_query_safety(query: str) -> tuple[bool, str]:
    value = (query or "").strip()
    if not value:
        return False, "empty_query"
    if BLOCKED_SQL.search(value):
        return False, "unsafe_sql_shaped_input"
    if SAFE_SELECT.match(value):
        return False, "free_sql_not_supported"
    return True, ""


def validate_readonly_template(sql_template: str) -> tuple[bool, str]:
    sql = (sql_template or "").strip()
    if not sql or len(sql) > MAX_SQL_LENGTH:
        return False, "template is empty or too long"
    if not SAFE_SELECT.match(sql):
        return False, "template must start with SELECT or WITH"
    if BLOCKED_SQL.search(sql):
        return False, "template contains blocked SQL syntax"
    if "\x00" in sql:
        return False, "template contains unsupported delimiter syntax"
    return True, ""


def metric_code_for_query(query: str) -> str | None:
    normalized = re.sub(r"\s+", "", (query or "").lower())
    for code, keywords in METRIC_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return code
    return None


def permission_allows(*, user_roles: set[str], is_super_admin: bool, allowed_roles: list[str] | None) -> bool:
    return is_super_admin or bool(user_roles & set(allowed_roles or []))


def catalog_scope_allows(*, referenced_tables: set[str], referenced_columns: set[str], retrieved_tables: set[str], retrieved_columns: set[str]) -> tuple[bool, str]:
    if not referenced_tables.issubset(retrieved_tables):
        return False, "catalog_table_scope_denied"
    if not referenced_columns.issubset(retrieved_columns):
        return False, "catalog_column_scope_denied"
    return True, ""
