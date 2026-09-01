# DataAnalysisAgent 安全执行阶段记录

- 写入时间：2026-08-12（Asia/Shanghai）

## 本次完成

1. **批准模板命中与角色过滤**
   - `analyze_template` 现在只接受 `is_active=True` 且 `is_approved=True` 的模板。
   - 非超级管理员必须与模板 `allowed_roles_json` 至少命中一个角色。
   - 模板未命中、未批准、停用或角色不允许时统一拒绝执行。

2. **SQL 表/字段白名单**
   - 模板 SQL 只能是单条只读 `SELECT`/`WITH` 查询。
   - 拦截写操作、危险关键字、注释、分号和不支持的分隔符。
   - 校验 SQL 实际引用的表、字段是否落在模板声明的 `target_table_names_json` / `target_column_names_json` 中。
   - 可选校验数据目录：仅允许 active/queryable 表；字段必须 active/queryable 且不能是 sensitive。

3. **参数边界与结果限制**
   - 限制 SQL 长度、参数数量、字符串参数长度、整数范围和日期区间（最长 366 天）。
   - 默认参数包含 `start_date`、`end_date`、`month_start` 和最大 `limit`。
   - 使用外层 `LIMIT :__result_limit` 并以 `fetchmany(MAX_RESULT_ROWS)` 双重限制结果行数，最大 100 行。

## 验证

新增 `tests/test_data_analysis_agent_security.py`，覆盖危险 SQL、表/字段白名单、参数边界和默认结果限制。

执行：

```powershell
pytest -q tests/test_data_analysis_agent_security.py
```

## 2026-08-12 追加修复

- 修复模板字段白名单误把命名参数（例如 `:status`）识别成字段的问题。
- 修复模板校验不必要拒绝合法 PostgreSQL `$` 标识符的问题，仍然禁止空字节、注释和多语句。
- 将 API 层模板命中从简单的 `split("的")` 字符串判断改为评分选择；执行安全性仍由批准状态、角色过滤、SQL 校验和目录白名单共同决定。
- 增加模板匹配评分测试和参数占位符测试。

## 2026-08-12 真实执行链路接入

- `POST /customer-service/sessions/agent/execute` 现会在候选阶段按当前用户角色过滤已批准模板，避免无权限模板参与匹配。
- 选中模板后，接口从数据库读取 active/queryable 数据目录表及 queryable/non-sensitive 字段，并将其传给 `DataAnalysisAgent.analyze_template`。因此表/字段白名单不再只是可选的服务层能力，而是在真实 HTTP 执行链路中生效。
- 修复列目录模型没有 `is_active` 字段导致白名单校验可能抛出属性错误的问题；列级安全以 `is_queryable` 和 `is_sensitive` 为准。

## 2026-08-12 审计与可观测性

- 增加 `TemplateAnalysisAttempt`：模板执行会返回受控的结果状态和拒绝原因（例如 `role_denied`、`invalid_sql:*`、`catalog_denied:*`、`invalid_parameters:*`、`execution_failed`），而不会把底层异常暴露给用户。
- `/customer-service/sessions/agent/execute` 会写入 `audit_logs`：
  - 成功执行批准模板：`data_analysis.template_executed`；
  - 未命中或被安全策略拒绝：`data_analysis.template_rejected`。
- 审计详情仅记录会话 ID、查询长度、模板 ID、受控拒绝原因、最终分析模式和 `free_sql=false`；不记录用户完整查询内容，也不记录 SQL 参数值，降低敏感信息进入审计日志的风险。

## 2026-08-12 后台审计查询与 HTTP 回归

- 后台 `GET /admin/audit-logs` 新增 `action_prefix` 和 `resource_id` 查询参数；拥有 `audit:read` 权限的人员可筛选 `data_analysis.*` 审计事件。
- DataAnalysisAgent HTTP 回归脚本现登录管理员，查询该审计列表并确认至少存在模板执行/拒绝事件，且审计详情保留 `query_length` 与 `free_sql` 标记。
- 审计查询仍受 `audit:read` 权限保护；未将用户原始问题或 SQL 参数暴露给后台列表。
