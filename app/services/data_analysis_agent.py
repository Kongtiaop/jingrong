from __future__ import annotations

from dataclasses import dataclass
import re
import logging
from datetime import date, datetime, timedelta
from sqlalchemy import func, select, text

from app.models.auth import User
from app.services import nl2sql_policy
from app.models.data_catalog import DataCatalogColumn, DataCatalogExampleQuery, DataCatalogTable
from app.models.operations import BusinessOperation
from app.models.profile import CustomerAssetSnapshot, Product
from app.models.trading import Order, RiskAlert


@dataclass(frozen=True)
class AnalysisResult:
    metric_code: str
    title: str
    value: int | float
    unit: str
    explanation: str
    query_plan: dict
    chart_spec: dict


@dataclass(frozen=True)
class TemplateAnalysisAttempt:
    result: AnalysisResult | None
    reason: str
    template_id: str | None = None


logger = logging.getLogger(__name__)


class DataAnalysisAgent:
    """安全的数据分析执行器：仅允许批准模板，且模板必须通过结构和目录白名单校验。"""

    MAX_RESULT_ROWS = nl2sql_policy.MAX_RESULT_ROWS
    MAX_SQL_LENGTH = nl2sql_policy.MAX_SQL_LENGTH
    MAX_PARAMETER_COUNT = nl2sql_policy.MAX_PARAMETER_COUNT
    MAX_STRING_PARAMETER_LENGTH = nl2sql_policy.MAX_STRING_PARAMETER_LENGTH
    MAX_DATE_RANGE_DAYS = nl2sql_policy.MAX_DATE_RANGE_DAYS
    _safe_select = nl2sql_policy.SAFE_SELECT
    _blocked_sql = nl2sql_policy.BLOCKED_SQL
    _identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)?$")
    _table_ref = re.compile(r"\b(?:from|join)\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?(?:\s+as)?(?:\s+[A-Za-z_][\w$]*)?)", re.IGNORECASE)
    _column_ref = re.compile(r"\b([A-Za-z_][\w$]*)(?:\.([A-Za-z_][\w$]*))?\b")
    _parameter_ref = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

    @classmethod
    def evaluate_query_safety(cls, query: str) -> tuple[bool, str]:
        return nl2sql_policy.evaluate_query_safety(query)

    @classmethod
    def validate_template(cls, sql_template: str) -> tuple[bool, str]:
        return nl2sql_policy.validate_readonly_template(sql_template)

    @classmethod
    def _validate_parameters(cls, params: dict[str, object]) -> tuple[bool, str]:
        if len(params) > cls.MAX_PARAMETER_COUNT:
            return False, "too many parameters"
        for name, value in params.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                return False, "invalid parameter name"
            if isinstance(value, str) and len(value) > cls.MAX_STRING_PARAMETER_LENGTH:
                return False, f"parameter {name} is too long"
            if isinstance(value, int) and abs(value) > 1_000_000:
                return False, f"parameter {name} is out of range"
        if "limit" in params and (not isinstance(params["limit"], int) or not 1 <= params["limit"] <= cls.MAX_RESULT_ROWS):
            return False, "limit is out of range"
        if "start_date" in params and "end_date" in params:
            if params["end_date"] < params["start_date"] or (params["end_date"] - params["start_date"]).days > cls.MAX_DATE_RANGE_DAYS:
                return False, "date range is out of range"
        return True, ""

    @classmethod
    def template_match_score(cls, template, query: str) -> int:
        """Character n-gram lexical score that remains effective for unspaced Chinese text."""
        normalize = lambda value: re.sub(r"\s+", "", (value or "").lower())
        question = normalize(getattr(template, "normalized_question", None) or getattr(template, "question", ""))
        candidate = normalize(query)
        if not question or not candidate:
            return 0
        if candidate == question:
            return 2_000 + len(question)
        if question in candidate or candidate in question:
            return 1_000 + min(len(question), len(candidate))
        def grams(value: str) -> set[str]:
            if len(value) < 2:
                return {value} if value else set()
            return {value[index:index + 2] for index in range(len(value) - 1)}
        overlap = grams(question) & grams(candidate)
        return len(overlap)

    @classmethod
    def clarification_for_parameter_reason(cls, reason: str, *, template=None, authorized_subject_ids: set[str] | None = None) -> str | None:
        """Return a safe, actionable clarification for recoverable parameter errors."""
        parameter_schema = ((getattr(template, "metadata_json", None) or {}).get("parameters") or {})
        if reason.startswith("parameter_out_of_range:"):
            parameter_name = reason.split(":", 1)[1]
            rule = parameter_schema.get(parameter_name, {})
            if rule.get("type") == "date_range":
                maximum = int(rule.get("max_range_days", cls.MAX_DATE_RANGE_DAYS))
                return f"查询日期范围超出允许上限。请将时间范围缩小到最近 {maximum} 天以内后再试。"
            minimum = int(rule.get("min", 1))
            maximum = int(rule.get("max", cls.MAX_RESULT_ROWS))
            return f"请求的返回条数超出允许范围。请将 {parameter_name} 调整为 {minimum} 到 {maximum} 之间后再试。"
        if reason.startswith("missing_authorized_subject:"):
            return "当前查询范围内存在多个已授权客户。请明确要查询的客户 ID 或客户标识后再试。"
        return None

    @classmethod
    def bind_template_parameters(cls, sql_template: str, query: str, *, template=None, user: User | None = None, authorized_subject_ids: set[str] | None = None) -> tuple[dict[str, object] | None, str]:
        now = datetime.now().astimezone()
        schema = ((getattr(template, "metadata_json", None) or {}).get("parameters") or {})
        params: dict[str, object] = {
            "limit": cls.MAX_RESULT_ROWS,
            "month_start": now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            "start_date": (now - timedelta(days=90)).date(),
            "end_date": now.date(),
        }
        if not schema:
            ids = re.findall(r"[A-Za-z0-9_-]{6,}", query or "")
            if ids:
                params["user_id"] = ids[0][:cls.MAX_STRING_PARAMETER_LENGTH]
            return params, ""
        requested_limit = re.search(r"(?:前|top\s*|limit\s*)(\d{1,7})\s*(?:条|个|rows?)?", query or "", re.IGNORECASE)
        requested_days = re.search(r"(?:过去|近)\s*(\d{1,5})\s*(?:天|日)", query or "")
        requested_subjects = re.findall(r"(?i)(?:客户|用户)(?:ID|编号|号)?[：:\s]*([0-9a-f]{8}-[0-9a-f-]{27,}|[A-Za-z0-9_-]{6,})", query or "")
        requested_subjects += re.findall(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", query or "", flags=re.IGNORECASE)
        # Tests and internal callers may use deterministic non-UUID subject aliases.
        # Only accept aliases that are already in the authorized scope; never infer arbitrary text as a subject.
        if authorized_subject_ids:
            query_tokens = set(re.findall(r"[A-Za-z0-9_-]{6,}", query or ""))
            requested_subjects += [subject for subject in authorized_subject_ids if subject in query_tokens]
            # If the query explicitly names a customer-style identifier but it is not in scope,
            # retain it so the authorization branch produces an explicit rejection.
            requested_subjects += [token for token in query_tokens if token.startswith(("customer-", "user-"))]
        requested_subjects = list(dict.fromkeys(requested_subjects))
        for name, rule in schema.items():
            source = rule.get("source", "system_constant")
            kind = rule.get("type", "string")
            if source == "system_now" and kind == "month_start":
                params[name] = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            elif source == "system_constant":
                params[name] = rule.get("default", params.get(name))
            elif source == "authorized_scope_only":
                subject_scope = None if user is not None and getattr(user, "is_super_admin", False) and authorized_subject_ids == set() else (set(authorized_subject_ids) if authorized_subject_ids is not None else {str(user.id)} if user is not None else set())
                if requested_subjects:
                    if subject_scope is not None and (not subject_scope or any(value not in subject_scope for value in requested_subjects)):
                        return None, f"unauthorized_subject:{name}"
                    params[name] = requested_subjects[0]
                elif subject_scope is not None and len(subject_scope) == 1:
                    params[name] = next(iter(subject_scope))
                elif subject_scope is None and user is not None:
                    params[name] = str(user.id)
                else:
                    return None, f"missing_authorized_subject:{name}"
            elif source == "natural_language" and kind == "integer":
                value = int(requested_limit.group(1)) if requested_limit else int(rule.get("default", params.get(name, 0)))
                minimum, maximum = int(rule.get("min", 1)), int(rule.get("max", cls.MAX_RESULT_ROWS))
                if value < minimum or value > maximum:
                    return None, f"parameter_out_of_range:{name}"
                params[name] = value
            elif source == "natural_language" and kind == "date_range":
                days = int(requested_days.group(1)) if requested_days else int(rule.get("default_days", 90))
                max_days = int(rule.get("max_range_days", cls.MAX_DATE_RANGE_DAYS))
                if days < 1 or days > max_days:
                    return None, f"parameter_out_of_range:{name}"
                params[rule.get("start_param", "start_date")] = (now - timedelta(days=days)).date()
                params[rule.get("end_param", "end_date")] = now.date()
        return params, ""

    @staticmethod
    def _is_customer(user: User) -> bool:
        return any(role.code == "customer" for role in user.roles)

    @staticmethod
    def _permissions(user: User) -> set[str]:
        return {permission.code for role in user.roles for permission in role.permissions}

    @staticmethod
    def _role_allowed(user: User, allowed_roles: list[str] | None) -> bool:
        if user.is_super_admin:
            return True
        roles = {role.code for role in user.roles}
        return bool(roles & set(allowed_roles or []))

    @classmethod
    def _extract_references(cls, sql: str) -> tuple[set[str], set[str], str | None]:
        tables: set[str] = set()
        aliases: set[str] = set()
        for match in cls._table_ref.finditer(sql):
            raw = re.sub(r"\s+as\s+|\s+", " ", match.group(1), flags=re.IGNORECASE).strip()
            parts = raw.split(" ")
            tables.add(parts[0].lower())
            if len(parts) > 1:
                aliases.add(parts[-1].lower())
        if not tables:
            return tables, set(), "no table reference found"
        # Remove SQL keywords/functions from candidate column names.
        keywords = {"select", "from", "where", "and", "or", "as", "on", "join", "left", "right", "inner", "outer", "group", "by", "order", "limit", "offset", "desc", "asc", "count", "sum", "avg", "min", "max", "case", "when", "then", "else", "end", "distinct", "is", "null", "in", "between", "like", "not", "with", "having", "true", "false"}
        columns: set[str] = set()
        # Remove parameters and quoted string literals before tokenizing identifiers;
        # literal values such as 'open' are not column references.
        scrubbed = cls._parameter_ref.sub(" ", sql)
        scrubbed = re.sub(r"'(?:''|[^'])*'", " ", scrubbed)
        for left, right in cls._column_ref.findall(scrubbed):
            candidate = (right or left).lower()
            left_name = left.lower()
            # In a qualified reference (table_or_alias.column), validate the right-hand column.
            # In an unqualified reference, validate the token itself unless it is a known table/alias.
            if candidate in keywords or candidate.isdigit():
                continue
            if right:
                if candidate not in tables | aliases:
                    columns.add(candidate)
            elif left_name not in tables | aliases:
                columns.add(candidate)
        return tables, columns, None

    @classmethod
    def validate_catalog_allowlist(cls, template: DataCatalogExampleQuery, *, catalog_tables: list[DataCatalogTable] | None = None, catalog_columns: list[DataCatalogColumn] | None = None) -> tuple[bool, str]:
        declared_tables = {str(name).lower().split(".")[-1] for name in (template.target_table_names_json or [])}
        declared_columns = {str(name).lower().split(".")[-1] for name in (template.target_column_names_json or [])}
        referenced_tables, referenced_columns, error = cls._extract_references(template.sql_template)
        if error:
            return False, error
        if declared_tables and not referenced_tables.issubset(declared_tables):
            return False, "SQL references a table outside template table allowlist"
        if declared_columns and not referenced_columns.issubset(declared_columns):
            return False, "SQL references a column outside template column allowlist"
        if catalog_tables is not None:
            allowed = {table.table_name.lower() for table in catalog_tables if table.is_active and table.is_queryable}
            if not referenced_tables.issubset(allowed):
                return False, "SQL references a table outside active queryable catalog"
            for table in catalog_tables:
                if table.table_name.lower() in referenced_tables and not cls._role_allowed_for_resource(template.allowed_roles_json, table.allowed_roles_json):
                    return False, "template role policy conflicts with table role policy"
        if catalog_columns is not None:
            allowed_columns = {
                column.column_name.lower()
                for column in catalog_columns
                if column.is_queryable and not column.is_sensitive
            }
            if not referenced_columns.issubset(allowed_columns):
                return False, "SQL references a non-queryable or sensitive column"
        return True, ""

    @staticmethod
    def _role_allowed_for_resource(template_roles: list[str] | None, resource_roles: list[str] | None) -> bool:
        if not resource_roles or not template_roles:
            return True
        return bool(set(template_roles) & set(resource_roles))

    async def analyze_template_attempt(self, session, *, user: User, template, query: str, catalog_tables: list[DataCatalogTable] | None = None, catalog_columns: list[DataCatalogColumn] | None = None, authorized_subject_ids: set[str] | None = None) -> TemplateAnalysisAttempt:
        template_id = str(template.id) if template is not None and getattr(template, "id", None) else None
        if not template:
            return TemplateAnalysisAttempt(None, "template_not_found")
        if not template.is_active:
            return TemplateAnalysisAttempt(None, "template_inactive", template_id)
        if not template.is_approved:
            return TemplateAnalysisAttempt(None, "template_not_approved", template_id)
        if not self._role_allowed(user, template.allowed_roles_json):
            return TemplateAnalysisAttempt(None, "role_denied", template_id)
        valid, reason = self.validate_template(template.sql_template)
        if not valid:
            return TemplateAnalysisAttempt(None, f"invalid_sql:{reason}", template_id)
        # Parameter binding occurs before dynamic catalog retrieval. Recoverable input errors
        # must produce a controlled clarification, while every SQL execution still passes the
        # dynamic table/column allowlist below.
        params, reason = self.bind_template_parameters(template.sql_template, query, template=template, user=user, authorized_subject_ids=authorized_subject_ids)
        if params is None:
            return TemplateAnalysisAttempt(None, f"invalid_parameters:{reason}", template_id)
        valid, reason = self._validate_parameters(params)
        if not valid:
            return TemplateAnalysisAttempt(None, f"invalid_parameters:{reason}", template_id)
        valid, reason = self.validate_catalog_allowlist(template, catalog_tables=catalog_tables, catalog_columns=catalog_columns)
        if not valid:
            return TemplateAnalysisAttempt(None, f"catalog_denied:{reason}", template_id)
        # The limit is enforced twice: parameter binding and an outer bounded fetch.
        bounded_sql = f"SELECT * FROM ({template.sql_template}) AS approved_template LIMIT :__result_limit"
        params["__result_limit"] = self.MAX_RESULT_ROWS
        try:
            result = (await session.execute(text(bounded_sql), params)).mappings().fetchmany(self.MAX_RESULT_ROWS)
        except Exception as exc:
            logger.exception("approved template execution failed template_id=%s", template_id)
            # A failed SQL execution aborts the current PostgreSQL transaction. Roll back before
            # the caller writes the rejection audit, otherwise the audit commit masks the real
            # refusal and poisons the entire Agent request with InFailedSQLTransactionError.
            await session.rollback()
            return TemplateAnalysisAttempt(None, "execution_failed", template_id)
        columns = list(result[0].keys()) if result else []
        chart_type = "table"
        if len(columns) == 2:
            chart_type = "line" if any("date" in column or "time" in column for column in columns) else "bar"
        analysis = AnalysisResult(metric_code=template.intent_code, title=template.question, value=len(result), unit="rows", explanation=template.explanation, query_plan={"mode": "approved_template", "template_id": template_id, "source_tables": template.target_table_names_json, "source_columns": template.target_column_names_json, "parameters": sorted(params), "max_result_rows": self.MAX_RESULT_ROWS, "read_only": True, "free_sql": False, "validated": True}, chart_spec={"type": chart_type, "title": template.question, "dimension": columns[0] if columns else None, "metric": columns[1] if len(columns) > 1 else None, "unit": ""})
        return TemplateAnalysisAttempt(analysis, "executed", template_id)

    async def analyze_template(self, session, *, user: User, template, query: str, catalog_tables: list[DataCatalogTable] | None = None, catalog_columns: list[DataCatalogColumn] | None = None, authorized_subject_ids: set[str] | None = None) -> AnalysisResult | None:
        return (await self.analyze_template_attempt(session, user=user, template=template, query=query, catalog_tables=catalog_tables, catalog_columns=catalog_columns, authorized_subject_ids=authorized_subject_ids)).result

    _metrics = (
        ("open_risk_alert_count", ("风险预警", "预警数量", "多少预警"), "待处理风险预警数量", "count", "risk_alert", "status in open/acknowledged/investigating/report_pending"),
        ("pending_order_count", ("待审核订单", "订单数量", "多少订单"), "待审核订单数量", "count", "orders", "status = pending_review"),
        ("pending_operation_count", ("待审核业务", "待审核操作", "赎回转账"), "待审核业务操作数量", "count", "business_operation", "status = pending_review"),
        ("active_product_count", ("在售产品", "产品数量", "多少产品"), "在售产品数量", "count", "product", "status = active and deleted_at is null"),
        ("latest_total_asset_sum", ("总资产", "资产总额", "资产规模"), "最新资产快照总额", "CNY", "customer_asset_snapshot", "latest snapshot per customer; aggregate total_asset"),
    )

    def _metric_for(self, query: str) -> tuple[str, str, str, str, str] | None:
        metric_code = nl2sql_policy.metric_code_for_query(query)
        return next(((metric[0], metric[2], metric[3], metric[4], metric[5]) for metric in self._metrics if metric[0] == metric_code), None)

    async def analyze(self, session, *, user: User, query: str) -> AnalysisResult | None:
        if self._is_customer(user):
            return None
        selected = self._metric_for(query)
        if selected is None:
            return None
        code, title, unit, source_table, filter_description = selected
        permissions = self._permissions(user)
        if code == "open_risk_alert_count":
            if not user.is_super_admin and not ({"risk:alert_read", "risk:alert_handle", "risk:alert_action", "audit:read"} & permissions): return None
            value = int((await session.execute(select(func.count()).select_from(RiskAlert).where(RiskAlert.status.in_(["open", "acknowledged", "investigating", "report_pending"])))).scalar_one())
        elif code == "pending_order_count":
            if not user.is_super_admin and not ({"order:review", "operation:read_staff", "audit:read"} & permissions): return None
            value = int((await session.execute(select(func.count()).select_from(Order).where(Order.status == "pending_review"))).scalar_one())
        elif code == "pending_operation_count":
            if not user.is_super_admin and not ({"order:review", "operation:read_staff", "operation:audit_read", "audit:read"} & permissions): return None
            value = int((await session.execute(select(func.count()).select_from(BusinessOperation).where(BusinessOperation.status == "pending_review"))).scalar_one())
        elif code == "active_product_count":
            if not user.is_super_admin and "product:read" not in permissions: return None
            value = int((await session.execute(select(func.count()).select_from(Product).where(Product.status == "active", Product.deleted_at.is_(None)))).scalar_one())
        else:
            if not user.is_super_admin and not ({"asset:read", "customer:read_all", "customer:read_assigned", "audit:read"} & permissions): return None
            latest_per_customer = select(CustomerAssetSnapshot.user_id, func.max(CustomerAssetSnapshot.snapshot_time).label("snapshot_time")).group_by(CustomerAssetSnapshot.user_id).subquery()
            value = float((await session.execute(select(func.coalesce(func.sum(CustomerAssetSnapshot.total_asset), 0)).join(latest_per_customer, (CustomerAssetSnapshot.user_id == latest_per_customer.c.user_id) & (CustomerAssetSnapshot.snapshot_time == latest_per_customer.c.snapshot_time)))).scalar_one())
        return AnalysisResult(metric_code=code, title=title, value=value, unit=unit, explanation=f"使用已批准的只读指标 {code} 统计，未执行用户提供的 SQL。", query_plan={"mode": "approved_metric", "metric_code": code, "source_table": source_table, "filter": filter_description, "read_only": True, "free_sql": False, "max_result_rows": 1}, chart_spec={"type": "metric", "title": title, "metric": "value", "unit": unit})
