# 智能财富管理平台：数据库表结构与 DataGrip 使用说明

> **用途**：答辩数据库说明、日常排查、DataGrip 连接与表关系查阅。  
> **当前主库**：PostgreSQL 16 + pgvector，Docker 容器 `wealth-postgres`。  
> **事实源原则**：客户、账户、订单、资金流水、风险、工单、权限和审计均以 PostgreSQL 为业务事实源；Redis、Milvus、MySQL 迁移库不替代主库事实。

---

## 1. DataGrip 连接 Docker PostgreSQL

### 1.1 当前本机连接参数

| DataGrip 字段 | 值 |
|---|---|
| 数据库类型 | PostgreSQL |
| Host | `127.0.0.1` |
| Port | `5433` |
| Database | `wealth_manager` |
| User | `wealth` |
| Password | `wealth_dev` |
| SSL | 不启用 / Disable（本地 Docker 开发环境） |

JDBC URL：

```text
jdbc:postgresql://127.0.0.1:5433/wealth_manager
```

> 密码来自当前项目 `docker-compose.yml` 的开发默认值：`POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-wealth_dev}`。如果本机 `.env` 将 `POSTGRES_PASSWORD` 或端口覆盖为其他值，应以实际 Compose 环境变量为准；不要把生产密码写进本说明或提交进 Git。

### 1.2 DataGrip 操作步骤

1. 打开 **DataGrip → File → New → Data Source → PostgreSQL**；
2. 若 DataGrip 提示下载 PostgreSQL Driver，选择下载；
3. 填入 Host、Port、Database、User、Password；
4. SSL 页面选择 **Disable**；
5. 点击 **Test Connection**，成功后点击 **OK**；
6. 在 Database 面板展开：
   ```text
   wealth_manager → schemas → public → tables
   ```
7. 推荐右键 `public` → **Jump to Query Console** 执行只读查询。

### 1.3 连接失败排查

```powershell
# 在项目根目录查看容器和端口映射
docker compose ps
docker port wealth-postgres

# 查看 PostgreSQL 日志
docker compose logs postgres --tail 100
```

若 `5433` 被占用或 `.env` 覆盖了 `POSTGRES_PORT`，以 `docker compose ps` / `docker port wealth-postgres` 显示的宿主机端口为准。应用内部 `DATABASE_URL` 可能使用 `asyncpg` 格式，DataGrip 不使用该 URL，直接按上面的 JDBC 参数连接即可。

---

## 2. 表关系总览

```text
users
 ├─ user_roles → roles → role_permissions → permissions
 ├─ refresh_session
 ├─ customer_profile / customer_risk_assessment / customer_subjective_profile
 ├─ account → orders → trades / order_status_history
 ├─ money_flow
 ├─ business_operation
 ├─ customer_advisor_authorization → advisor_operation_draft
 ├─ risk_alert → risk_investigation / risk_report_draft / work_order
 ├─ work_order → work_order_update / work_order_attachment / notification
 └─ audit_logs

knowledge_base → knowledge_document → knowledge_document_version → knowledge_chunk
outbox_event / async_task 支持异步可靠投递与任务追踪
aml_batch_run → 风险预警 Evidence 中的扫描窗口/批次关联
```

---

## 3. 身份、权限与会话

| 表 | 关键字段 | 作用 |
|---|---|---|
| `users` | `id`, `username`, `display_name`, `password_hash`, `status`, `is_super_admin`, `deleted_at` | 系统用户，客户和工作人员共用身份主表。 |
| `roles` | `id`, `code`, `name` | 角色，如 customer、advisor、risk_manager、operations、auditor、super_admin。 |
| `permissions` | `id`, `code`, `name` | 细粒度权限码，例如 `transfer:submit_staff`、`risk:alert_action`。 |
| `user_roles` | `user_id`, `role_id` | 用户—角色多对多关联。 |
| `role_permissions` | `role_id`, `permission_id` | 角色—权限多对多关联。 |
| `refresh_session` | `user_id`, `token_hash`, `expires_at`, `revoked_at` | Refresh Token 轮换和撤销的持久化会话记录。 |
| `customer_advisor_authorization` | `customer_id`, `advisor_id`, `actions`, `product_scope`, `single_limit`, `status` | 客户对投顾/工作人员的预授权边界。 |

**排查重点**：角色只代表人员分类；接口最终还会校验权限码和资源数据范围。不要仅通过前端菜单判断某人是否有权操作。

---

## 4. 客户、产品、资产与适当性

| 表 | 关键字段 | 作用 |
|---|---|---|
| `customer_profile` | `user_id`, `risk_level`, `annual_income`, `investment_horizon`, `customer_tier` | 客户画像、收入、期限、层级等适当性输入。 |
| `customer_risk_assessment` | `user_id`, `answers`, `risk_level`, `status` | 风险测评及其结果。 |
| `customer_subjective_profile` | `user_id`, `investment_goal`, `liquidity_preference` | 客户主观偏好。 |
| `customer_asset_snapshot` | `user_id`, `total_asset`, `cash_balance`, `net_asset`, `snapshot_time`, `snapshot_type` | 资产快照；`derived` 用于派生估值，历史交易/日估值快照保留事实。 |
| `customer_holding` | `user_id`, `product_id`, `quantity`, `cost_amount`, `market_value`, `status` | 客户持仓和收益展示基础。 |
| `product` | `id`, `name`, `risk_level`, `status`, `daily_yield_rate`, `target_customer_type` | 理财产品主数据。 |
| `product_suitability_rule` | `product_id`, `minimum_risk_level`, `minimum_customer_tier`, `status` | 确定性适当性规则。 |
| `risk_rule` | `rule_code`, `config`, `risk_level`, `version`, `source_document` | 风险/AML 规则配置与版本来源。 |

---

## 5. 交易、赎回、转账与资金流水

| 表 | 关键字段 | 作用 |
|---|---|---|
| `account` | `user_id`, `available_balance`, `frozen_balance`, `status` | 客户资金账户。 |
| `orders` | `order_no`, `user_id`, `product_id`, `amount`, `status`, `idempotency_key`, `source` | 申购订单；普通客户与投顾订单的流程来源可区分。 |
| `order_status_history` | `order_id`, `from_status`, `to_status`, `actor_user_id`, `note` | 订单状态机历史，防止只看当前状态无法追溯。 |
| `trades` | `order_id`, `user_id`, `product_id`, `amount`, `quantity`, `status` | 订单成交事实。 |
| `money_flow` | `customer_id`, `account_id`, `flow_type`, `direction`, `amount`, `occurred_at`, `counterparty` | 所有资金进出流水；AML 的重要证据源。 |
| `business_operation` | `operation_type`, `user_id`, `operator_user_id`, `product_id`, `to_account`, `amount`, `quantity`, `status`, `idempotency_key` | 赎回与转账申请；可区分客户本人发起和工作人员代办。 |
| `advisor_operation_draft` | `customer_id`, `advisor_id`, `operation_type`, `payload`, `status` | 投顾生成的待客户确认操作草稿，不直接扣款。 |

**关键约束**：

```text
申购：订单状态历史 + 成交 + 持仓/资金变化
赎回：business_operation 审核通过 → 持仓减少 + redeem/in 流水 + 余额增加
转账：business_operation 审核通过 → 源账户扣减 + 目标账户增加 + transfer/out 与 transfer/in 两条流水
幂等：idempotency_key 防止网络重试造成重复申请或重复资金副作用
```

---

## 6. 风险、AML 与交易限制

| 表 | 关键字段 | 作用 |
|---|---|---|
| `risk_alert` | `customer_id`, `rule_code`, `alert_level`, `evidence`, `status`, `assessment_result`, `action_code`, `restriction_status` | 风险预警/风险处置主记录；可视为风险处置工单。 |
| `risk_investigation` | `risk_alert_id`, `status`, `assignee`, `notes` | 风控调查过程。 |
| `risk_report_draft` | `risk_alert_id`, `status`, `content` | 风险/AML 报告草稿。 |
| `aml_batch_run` | `batch_key`, `scan_window_start`, `scan_window_end`, `status`, `created_alerts`, `error_summary` | AML 日批扫描运行记录。 |
| `work_order` | `risk_alert_id`, `category`, `status`, `assigned_to` | 当风控要求客户补充资料时，关联 `risk_investigation` 工单。 |

**风险状态与门禁**：

```text
RiskAlert open / acknowledged / investigating / pending_customer / report_pending
  → 订单审核、赎回、转账服务检查未结案预警和限制状态
  → 可能返回 RISK_CONTROL_BLOCKED、HOLD 或 REJECT

RESTRICT_TRADING
  → risk_alert.restriction_status = restricted
  → 客户后续资金类操作由后端拒绝

RELEASE_RESTRICTION
  → 由有 risk action 权限的后台人员执行
  → 不删除历史预警、调查和审计，只恢复交易资格
```

---

## 7. 工单、通知与附件

| 表 | 关键字段 | 作用 |
|---|---|---|
| `work_order` | `ticket_no`, `customer_id`, `created_by`, `assigned_to`, `category`, `priority`, `status`, `risk_alert_id` | 通用业务工单；风险补件时与风险预警一对一关联。 |
| `work_order_update` | `work_order_id`, `author_user_id`, `author_role`, `content` | 客户补充、工作人员处理意见等时间线。 |
| `work_order_attachment` | `work_order_id`, `update_id`, `stored_name`, `content_type`, `size_bytes` | JPG/PNG/PDF 受控附件元数据；文件本体在本地受控目录/后续对象存储。 |
| `notification` | `user_id`, `type`, `title`, `content`, `work_order_id`, `read_at` | 站内通知，例如分派、客户补件、风险补件要求。 |

**风险补件链路**：

```text
RiskAlert + REQUEST_MATERIALS
→ WorkOrder(category=risk_investigation, status=pending_customer, risk_alert_id 唯一)
→ Notification 给客户
→ 客户提交 WorkOrderUpdate / WorkOrderAttachment
→ WorkOrder 回到 in_progress 并通知原处理人
→ RiskAlert 仍需由风控人工复核和结案
```

---

## 8. 知识库、RAG 与异步基础设施

| 表 | 关键字段 | 作用 |
|---|---|---|
| `knowledge_base` | `id`, `name`, `status` | 知识库集合。 |
| `knowledge_document` | `document_key`, `content_hash`, `current_version_id`, `status`, `permission_level` | 稳定文档身份、版本指针、权限和生命周期。 |
| `knowledge_document_version` | `document_id`, `version_no`, `content_hash`, `status`, `chunk_count` | 不可变文档版本；激活后供检索使用。 |
| `knowledge_chunk` | `document_id`, `version_id`, `chunk_index`, `content_hash`, `embedding`, `status` | 文本分块和 pgvector 向量；仅 active chunk 参与检索。 |
| `outbox_event` | `event_type`, `aggregate_id`, `payload_json`, `status`, `retry_count` | 与业务事务同提交的异步事件，降低双写不一致。 |
| `async_task` | `task_type`, `status`, `attempts`, `max_attempts`, `error_message` | 后台任务、重试和失败追踪。 |

**RAG 版本切换原则**：新版本为 `processing` 时旧 active 版本继续可检索；新增版本切分/向量写入成功后再切换 `current_version_id`，旧版本变为 superseded，避免半成品知识上线。

---

## 9. 审计与排查建议

| 表 | 关键字段 | 作用 |
|---|---|---|
| `audit_logs` | `actor_user_id`, `action`, `resource_type`, `resource_id`, `detail`, `created_at` | 关键业务审计：订单审核、风险动作、工单分派、补件、附件、赎回/转账结算等。 |

常用只读 SQL：

```sql
-- 最近风险预警与限制状态
SELECT id, customer_id, rule_code, status, assessment_result, action_code,
       restriction_status, created_at
FROM risk_alert
ORDER BY created_at DESC
LIMIT 50;

-- 某客户最近的赎回/转账申请
SELECT id, operation_type, status, amount, quantity, to_account,
       operator_user_id, created_at
FROM business_operation
WHERE user_id = :customer_id
ORDER BY created_at DESC;

-- 风险补件工单及其关联预警
SELECT w.ticket_no, w.status AS work_order_status, w.assigned_to,
       r.rule_code, r.status AS risk_alert_status
FROM work_order w
LEFT JOIN risk_alert r ON r.id = w.risk_alert_id
WHERE w.category = 'risk_investigation'
ORDER BY w.created_at DESC;

-- 某订单为什么被风险阻断：查未结案预警
SELECT id, rule_code, status, assessment_result, action_code, evidence
FROM risk_alert
WHERE customer_id = :customer_id
  AND status NOT IN ('resolved', 'dismissed')
ORDER BY created_at DESC;
```

---

## 10. 维护规则

1. 新增实体时，必须同步：ORM Model、Alembic Migration、Schema/Repository/Service、测试和本说明；
2. 不在 DataGrip 中直接修改生产或演示关键业务状态；资金、订单、风险限制应优先通过 API/Service 产生完整审计；
3. 调试数据如必须手工改动，应记录操作时间、SQL、影响行数，并在演示后清理；
4. 结构演进优先使用 Alembic；`ensure_schema` 中的兼容补列只用于开发环境兼容，不替代规范迁移治理；
5. 连接凭据仅适用于当前本地 Docker 开发环境。生产环境必须使用独立账号、最小权限、TLS 和密钥管理。
