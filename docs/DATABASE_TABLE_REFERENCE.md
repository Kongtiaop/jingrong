# 智能财富管理平台：数据库表结构参考

> **用途**：答辩数据库说明、日常排查、DataGrip 连接与表关系查阅。
> **当前主库**：PostgreSQL 16 + pgvector，Docker 容器 `wealth-postgres`。
> **事实源原则**：客户、账户、订单、资金流水、风险、工单、权限、审计均以 PostgreSQL 为业务事实源；Redis、Milvus、MySQL 迁移库不替代主库事实。
> **更新日期**：2026-09-14（依据 `app/models/*.py` 复核；实际 **49** 张表）
> ⚠️ 2026-08-27 版本声称"50 张表"，2026-09-14 复核实际为 49 张 —— 原计数有误，
> 且遗漏了 `bad_case` 表（已补入第 10 节）。
> **说明**：类型标注 `String(n)`=varchar(n)、`Numeric(a,b)`=定点数、`JSONB`/`JSON`=json、`VECTOR(1024)`=pgvector、`DateTime(tz)`=带时区时间；外键指向表名，`ondelete` 行为标注。

---

## 1. DataGrip 连接 Docker PostgreSQL

### 1.1 本机连接参数

| DataGrip 字段 | 值 |
|---|---|
| 数据库类型 | PostgreSQL |
| Host | `127.0.0.1` |
| Port | `5433`（以 `docker compose ps` 为准） |
| Database | `wealth_manager` |
| User | `wealth` |
| Password | `wealth_dev`（开发默认，以 `.env` 为准） |
| SSL | 不启用 |

JDBC URL：`jdbc:postgresql://127.0.0.1:5433/wealth_manager`

### 1.2 连接排查

```powershell
docker compose ps; docker port wealth-postgres
docker compose logs postgres --tail 100
```

> 不要把生产密码写进本说明或提交进 Git。

---

## 2. 表关系总览（49 张表）

```text
身份权限：users ─┬─ user_roles → roles → role_permissions → permissions
                 ├─ refresh_sessions（刷新令牌轮换）
                 ├─ customer_advisor_authorization（投顾授权）
                 └─ customer_enterprise_verification（企业认证）

客户画像/资产：customer_profile / customer_risk_assessment / customer_subjective_profile
              customer_asset_snapshot / customer_holding / product / product_suitability_rule / risk_rule

资金交易：account → orders → trades / order_status_history
         natural_language_order_draft（NL 草稿）→ orders
         advisor_operation_draft（投顾草稿）
         business_operation（赎回/转账）→ money_flow（资金流水）

风险/AML：risk_alert → risk_investigation / risk_report_draft
         aml_batch_run → aml_batch_alert / aml_scheduler_state

工单/通知：work_order → work_order_update / work_order_attachment
         notification

客服会话：customer_service_session → customer_service_message

记忆：customer_memory → customer_memory_event

知识库/RAG：knowledge_base → knowledge_document → knowledge_document_version → knowledge_chunk

数据目录/NL2SQL：data_catalog_domain → data_catalog_table → data_catalog_column
                data_catalog_relation / data_catalog_example_query / data_catalog_revision

异步基础设施：outbox_event（事务发件箱）/ async_task（后台任务）
审计：audit_logs

数据飞轮：bad_case（bad case 采集与标注；无外键，按 customer_id 逻辑关联 users）
```

---

## 3. 身份、权限与会话

### users（用户主表）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | UUID |
| username | String(128) unique | 登录名 |
| password_hash | String(255) | Argon2 密码哈希 |
| display_name | String(128) | 显示名 |
| status | String(20) | active / deleted / disabled |
| is_super_admin | Boolean | 超级管理员标志 |
| deleted_at | DateTime(tz) | 软删除时间 |
| created_at / updated_at | DateTime(tz) | 审计 |

### roles（角色）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| code | String(64) unique | customer、advisor、risk_manager、operations、auditor、super_admin 等 |
| name | String(128) | 角色名 |

### permissions（权限码）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| code | String(128) unique | 如 `order:review`、`risk:alert_action` |
| name | String(128) | 权限名 |

### user_roles（用户-角色 关联）
| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | FK→users(id) CASCADE | 复合主键 |
| role_id | FK→roles(id) CASCADE | 复合主键 |

### role_permissions（角色-权限 关联）
| 字段 | 类型 | 说明 |
|---|---|---|
| role_id | FK→roles(id) CASCADE | 复合主键 |
| permission_id | FK→permissions(id) CASCADE | 复合主键 |

### refresh_sessions（刷新令牌会话）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE | |
| token_hash | String(64) unique | SHA-256，不存明文 |
| expires_at | DateTime(tz) | 过期时间（默认 30 天） |
| revoked_at | DateTime(tz) 可空 | 轮换/撤销时间 |
| created_at | DateTime(tz) | |

### customer_advisor_authorization（客户对投顾/工作人员授权）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| customer_id | FK→users(id) CASCADE | 被授权客户 |
| advisor_id | FK→users(id) CASCADE | 投顾/客户经理 |
| allowed_actions | String(255) | 允许动作，默认 buy |
| product_scope | String(255) | 产品范围，默认 * |
| max_single_amount | Numeric | 单笔上限 |
| max_daily_amount | Numeric | 日上限 |
| risk_level_ceiling | String(8) | 风险等级上限，默认 C2 |
| effective_from | DateTime(tz) | 生效起始 |
| effective_to | DateTime(tz) 可空 | 生效截止（空=长期） |
| status | String(32) | active |
| requires_customer_confirmation | Boolean | 是否需要客户确认 |
| created_at | DateTime(tz) | |
| revoked_at | DateTime(tz) 可空 | 撤销时间 |

### customer_enterprise_verification（企业认证）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE unique | |
| company_name | String(255) | 企业名 |
| registration_no | String(128) | 注册号 |
| legal_representative | String(128) | 法人 |
| contact_phone | String(64) | 联系电话 |
| status | String(32) | pending / approved / rejected |
| review_note | Text | 审核意见 |
| reviewed_by | FK→users(id) 可空 | 审核人 |
| reviewed_at | DateTime(tz) 可空 | 审核时间 |
| created_at / updated_at | DateTime(tz) | |

---

## 4. 客户、产品、资产与适当性

### customer_profile（客户画像）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE unique | |
| age | Integer 可空 | 年龄 |
| occupation | String(128) | 职业 |
| annual_income | Numeric(18,2) 可空 | 年收入 |
| region | String(128) | 地区 |
| customer_type | String(64) | individual / enterprise |
| customer_tier | String(64) | 普通/黄金/白金/钻石/私人银行 |
| investment_experience_years | Integer | 投资经验年限 |
| investment_goal | String(255) | 投资目标 |
| investment_horizon | String(64) | 投资期限（兼容遗留列） |
| investment_horizon_years | Integer 可空 | 期限年数 |
| liquidity_preference | String(16) | 流动性偏好 low/medium/high |
| source_type | String(32) | synthetic |
| created_at / updated_at | DateTime(tz) | |

### customer_risk_assessment（风险测评）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE | |
| risk_level | String(8) | C1-C5 |
| score | Integer | 测评分数 |
| answers_json | Text | 答题明细 JSON |
| status | String(32) | active 等 |
| source_type | String(32) | questionnaire |
| assessed_at | DateTime(tz) | 测评时间 |
| expires_at | DateTime(tz) 可空 | 过期时间 |
| created_at / updated_at | DateTime(tz) | |

### customer_subjective_profile（客户主观画像）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE | |
| field_name | String(128) | 字段名 |
| field_value | Text | 字段值 |
| source_text | Text | 来源原文 |
| source_type | String(32) | user_declared |
| confirmed | Boolean | 是否客户确认 |
| confidence | Numeric(5,4) | 置信度 |
| collected_at | DateTime(tz) | 采集时间 |

### customer_asset_snapshot（资产快照）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE | |
| total_asset | Numeric(18,2) | 总资产 |
| cash_balance | Numeric(18,2) | 现金余额 |
| investable_asset | Numeric(18,2) | 可投资资产 |
| liability | Numeric(18,2) | 负债 |
| net_asset | Numeric(18,2) | 净资产 |
| snapshot_time | DateTime(tz) | 快照时间 |
| source_type | String(32) | synthetic / trade_derived |
| created_at | DateTime(tz) | |

### product（理财产品主数据）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| name | String(255) | 产品名 |
| product_type | String(64) | fund 等 |
| risk_level | String(16) | C1-C5 |
| term_days | Integer | 期限天数 |
| minimum_amount | Numeric(18,2) | 起购金额 |
| daily_yield_rate | Numeric(12,8) | 日收益率 |
| liquidity | String(64) | 流动性 |
| description | Text | 描述 |
| target_customer_type | String(64) | 目标客户类型 |
| status | String(32) | active 等 |
| source_type | String(32) | synthetic |
| created_at / updated_at | DateTime(tz) | |
| deleted_at | DateTime(tz) 可空 | 软删除 |

### product_suitability_rule（适当性规则）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| product_id | FK→product(id) CASCADE | |
| minimum_risk_level | String(16) | 最低风险等级要求 |
| investor_type | String(64) | 投资者类型 |
| minimum_investable_asset | Numeric(18,2) | 最低可投资资产 |
| rule_text | Text | 规则文本 |
| created_at / updated_at | DateTime(tz) | |

### risk_rule（风险/AML 规则配置）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| rule_code | String(64) unique | 规则编码 |
| name | String(255) | 规则名 |
| rule_type | String(32) | suitability 等 |
| config_json | Text | 规则配置 JSON |
| risk_level | String(32) | 风险级别 |
| enabled | Boolean | 是否启用 |
| version | String(32) | 版本 |
| source_document | String(255) | 来源文档 |
| created_at / updated_at | DateTime(tz) | |

### customer_holding（客户持仓）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE | |
| product_id | FK→product(id) CASCADE | |
| quantity | Numeric(18,4) | 份额 |
| cost_amount | Numeric(18,2) | 成本 |
| market_value | Numeric(18,2) | 市值 |
| profit_loss | Numeric(18,2) | 盈亏 |
| holding_days | Integer | 持有天数 |
| status | String(32) | active |
| created_at / updated_at | DateTime(tz) | |

---

## 5. 交易、订单、资金

### account（资金账户）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE unique | |
| account_no | String(64) unique | 账号 |
| currency | String(8) | 币种，默认 CNY |
| available_balance | Numeric(18,2) | 可用余额 |
| frozen_balance | Numeric(18,2) | 冻结余额 |
| status | String(32) | active |
| risk_restriction_status | String(32) | none / restricted |
| risk_restriction_reason | Text | 限制原因 |
| risk_restricted_until | DateTime(tz) 可空 | 限制截止 |
| updated_at | DateTime(tz) | |

### orders（申购订单）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| order_no | String(64) unique | 订单号 |
| user_id | FK→users(id) CASCADE | |
| account_id | FK→account(id) | |
| product_id | FK→product(id) | |
| amount | Numeric(18,2) | 金额 |
| quantity | Numeric(18,4) | 份额 |
| status | String(32) | pending_confirmation / pending_review / executing / executed / rejected / cancelled |
| side | String(16) | buy |
| review_note | Text | 审核意见 |
| failure_reason | Text | 失败原因 |
| idempotency_key | String(128) 可空 | 幂等键（唯一索引防重） |
| order_source | String(32) | customer_direct / advisor_assisted |
| advisor_id | FK→users(id) 可空 | 关联投顾 |
| confirmation_mode | String(32) | customer_order / nl_draft |
| created_at / updated_at | DateTime(tz) | |

### natural_language_order_draft（自然语言下单草稿）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| customer_id | FK→users(id) CASCADE | |
| product_id | FK→product(id) | |
| side | String(16) | buy |
| amount | Numeric(18,2) | 申购金额 |
| version | Integer | 草稿版本 |
| status | String(32) | awaiting_user_confirmation / expired / confirmed_order_created 等 |
| input_fingerprint | String(128) | 参数指纹 SHA-256 |
| confirmation_token_hash | String(128) unique | 确认令牌哈希（不存明文） |
| precheck_snapshot_json | Text | 预检结果快照 |
| expires_at | DateTime(tz) | 有效期（15 分钟） |
| confirmed_at | DateTime(tz) 可空 | 确认时间 |
| order_id | FK→orders(id) 可空 unique | 确认后关联订单 |
| created_at / updated_at | DateTime(tz) | |

### trades（成交）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| trade_no | String(64) unique | 成交号 |
| order_id | FK→orders(id) CASCADE unique | 一单一成交 |
| user_id | FK→users(id) CASCADE | |
| product_id | FK→product(id) | |
| amount | Numeric(18,2) | 金额 |
| quantity | Numeric(18,4) | 份额 |
| executed_at | DateTime(tz) | 成交时间 |

### order_status_history（订单状态历史）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| order_id | FK→orders(id) CASCADE | |
| from_status | String(32) 可空 | 原状态 |
| to_status | String(32) | 新状态 |
| operator_user_id | FK→users(id) | 操作人 |
| note | Text | 备注 |
| created_at | DateTime(tz) | |

### advisor_operation_draft（投顾操作草稿）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| customer_id | FK→users(id) CASCADE | |
| advisor_id | FK→users(id) | |
| product_id | FK→product(id) | |
| side | String(16) | buy |
| amount | Numeric(18,2) | 金额 |
| status | String(32) | pending_customer_confirmation 等 |
| note | Text | |
| customer_confirmed_at | DateTime(tz) 可空 | 客户确认时间 |
| created_at / updated_at | DateTime(tz) | |

### business_operation（业务操作：赎回/转账）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE | 客户 |
| operator_user_id | FK→users(id) 可空 | 代办人 |
| authorization_id | FK→customer_advisor_authorization(id) 可空 | 授权来源 |
| confirmation_mode | String(32) | customer / advisor |
| operation_type | String(32) | redeem / transfer 等 |
| status | String(32) | accepted 等 |
| idempotency_key | String(128) 可空 | 幂等键 |
| amount | Numeric(18,2) | 金额 |
| quantity | Numeric(18,4) | 份额 |
| product_id | FK→product(id) 可空 | |
| to_account | String(128) 可空 | 目标账户 |
| payload_json | Text | 附加 JSON |
| created_at / updated_at | DateTime(tz) | |

### money_flow（资金流水）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| customer_id | FK→users(id) CASCADE | |
| account_id | FK→account(id) 可空 | |
| order_id | FK→orders(id) 可空 | |
| amount | Numeric(18,2) | 金额 |
| currency | String(8) | 币种 |
| flow_type | String(32) | buy / redeem / transfer 等 |
| direction | String(16) | in / out |
| counterparty_id | String(128) 可空 | 对手方 |
| channel | String(64) | 渠道 |
| occurred_at | DateTime(tz) | 发生时间 |
| created_at | DateTime(tz) | |

---

## 6. 风险与 AML

### risk_alert（风险预警/处置主记录）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| customer_id | FK→users(id) CASCADE | |
| order_id | FK→orders(id) 可空 | 关联订单 |
| rule_code | String(64) | 触发规则 |
| alert_level | String(16) | low / medium / high |
| status | String(32) | open / acknowledged / investigating / pending_customer / report_pending / resolved / dismissed |
| confidence | Numeric(5,4) | 置信度 |
| evidence_json | Text | 证据 JSON |
| recommendation | Text | 建议 |
| handled_by | FK→users(id) 可空 | 处理人 |
| handled_note | Text | 处理意见 |
| created_at / handled_at | DateTime(tz) | |
| assessment_result | String(32) 可空 | 评估结论 |
| assessment_note | Text | |
| customer_consulted | Boolean | 是否已咨询客户 |
| customer_consultation_note | Text | |
| disposition_code / disposition_note | String(64)/Text | 处置编码/说明 |
| action_code | String(64) 可空 | 动作编码 |
| action_status | String(32) | not_started 等 |
| action_note | Text | |
| investigation_id | String(36) 可空 | 关联调查 |
| report_draft_id | String(36) 可空 | 关联报告草稿 |
| restriction_status | String(32) | none / restricted |
| restriction_expires_at | DateTime(tz) 可空 | 限制截止 |

### risk_investigation（风控调查）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| alert_id | FK→risk_alert(id) CASCADE unique | |
| customer_id | FK→users(id) CASCADE | |
| assigned_to | FK→users(id) 可空 | 负责人 |
| status | String(32) | open 等 |
| conclusion | Text | 调查结论 |
| created_at / updated_at | DateTime(tz) | |

### risk_report_draft（风险/AML 报告草稿）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| alert_id | FK→risk_alert(id) CASCADE | |
| customer_id | FK→users(id) CASCADE | |
| report_type | String(32) | large_transaction 等 |
| status | String(32) | draft 等 |
| payload_json | Text | 报告内容 |
| created_by | FK→users(id) | 创建人 |
| created_at | DateTime(tz) | |
| submitted_at | DateTime(tz) 可空 | 提交时间 |

### aml_batch_run（AML 日批扫描记录）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| batch_key | String(160) unique | 批次键 |
| scan_window_start / scan_window_end | DateTime(tz) | 扫描窗口 |
| scan_days | Integer | 扫描天数，默认 7 |
| status | String(32) | running / success / failed |
| total_customers | Integer | 客户总数 |
| succeeded_customers | Integer | 成功数 |
| failed_customers | Integer | 失败数 |
| created_alerts | Integer | 新建预警数 |
| error_summary | Text | 错误汇总 |
| run_type | String(32) | daily |
| source_batch_id | FK→aml_batch_run(id) 可空 | 补跑来源 |
| started_at / finished_at / created_at / updated_at | DateTime(tz) | |

### aml_batch_alert（批次-预警 关联）
| 字段 | 类型 | 说明 |
|---|---|---|
| batch_id | FK→aml_batch_run(id) CASCADE PK | 复合主键 |
| alert_id | FK→risk_alert(id) CASCADE PK | 复合主键 |
| created_at | DateTime(tz) | |

### aml_scheduler_state（AML 调度器状态）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(64) PK | 调度器标识 |
| last_heartbeat_at | DateTime(tz) | 心跳时间 |
| last_scan_started_at | DateTime(tz) 可空 | 上次扫描开始 |
| last_scan_finished_at | DateTime(tz) 可空 | 上次扫描结束 |
| last_scan_status | String(32) | never / success / failed |
| last_error | Text | 上次错误 |
| updated_at | DateTime(tz) | |

---

## 7. 工单、通知、附件

### work_order（业务工单）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| ticket_no | String(64) unique | 工单号 |
| customer_id | FK→users(id) 可空 | 关联客户 |
| created_by | FK→users(id) | 创建人 |
| assigned_to | FK→users(id) 可空 | 负责人 |
| category | String(32) | risk_investigation 等 |
| priority | String(16) | normal 等 |
| status | String(32) | open / pending_customer / in_progress / resolved / closed |
| title | String(200) | 标题 |
| description | Text | 描述 |
| context_json | Text | 上下文 JSON |
| resolution_note | Text | 解决意见 |
| risk_alert_id | FK→risk_alert(id) 可空 unique | 关联预警 |
| created_at / updated_at | DateTime(tz) | |
| resolved_at / closed_at | DateTime(tz) 可空 | 解决/关闭时间 |

### work_order_update（工单时间线）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| work_order_id | FK→work_order(id) CASCADE | |
| author_user_id | FK→users(id) | 作者 |
| author_role | String(32) | 作者角色 |
| content | Text | 内容（客户补件/处理意见） |
| created_at | DateTime(tz) | |

### work_order_attachment（工单附件元数据）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| work_order_id | FK→work_order(id) CASCADE | |
| update_id | FK→work_order_update(id) 可空 | 关联时间线 |
| uploaded_by | FK→users(id) | 上传人 |
| original_name | String(255) | 原始文件名 |
| stored_name | String(255) unique | 存储文件名 |
| content_type | String(128) | MIME |
| size_bytes | Integer | 大小 |
| created_at | DateTime(tz) | |

### notification（站内通知）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| user_id | FK→users(id) CASCADE | |
| notification_type | String(64) | |
| title | String(200) | |
| content | Text | |
| resource_type / resource_id | String(64) | 关联资源 |
| idempotency_key | String(160) 可空 | 幂等键（唯一索引防重复通知） |
| is_read | Boolean | 是否已读 |
| read_at | DateTime(tz) 可空 | 已读时间 |
| created_at | DateTime(tz) | |

---

## 8. 客服会话

### customer_service_session（人工客服会话）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| customer_id | FK→users(id) CASCADE | |
| work_order_id | FK→work_order(id) 可空 | 关联工单 |
| status | String(32) | waiting_human / human_active / paused / resolved / closed / expired |
| mode | String(16) | human |
| assigned_agent_id | FK→users(id) 可空 | 坐席 |
| handoff_reason | String(64) | 转人工原因（rag_low_confidence / customer_requested） |
| handoff_at | DateTime(tz) | 转人工时间 |
| accepted_at | DateTime(tz) 可空 | 坐席接管时间 |
| ended_at | DateTime(tz) 可空 | 结束时间 |
| last_message_at | DateTime(tz) 可空 | 最近消息 |
| agent_last_heartbeat_at | DateTime(tz) 可空 | 坐席心跳 |
| created_at / updated_at | DateTime(tz) | |

> 约束：`uq_customer_service_session_active_agent`（同一坐席同一时刻只有一条 human_active）。

### customer_service_message（客服消息）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| session_id | FK→customer_service_session(id) CASCADE | |
| sender_type | String(32) | customer / agent |
| sender_user_id | FK→users(id) 可空 | |
| content | Text | 消息内容 |
| message_type | String(32) | text |
| source | String(32) | workflow |
| created_at | DateTime(tz) | |

---

## 9. 记忆

### customer_memory（长期记忆卡片）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| customer_id | FK→users(id) CASCADE | |
| memory_type | String(32) | confirmed_parameter / service_preference / conversation_fact |
| key | String(128) | 记忆键 |
| value | Text | 记忆值 |
| source | String(32) | conversation / customer_confirmed |
| confidence | Numeric | 置信度（默认 1.0） |
| status | String(24) | active（支持软失效） |
| last_confirmed_at | DateTime(tz) | 最近确认时间 |
| expires_at | DateTime(tz) 可空 | 过期时间 |
| created_at / updated_at | DateTime(tz) | |

> 约束：`uq_customer_memory_active_key(customer_id, key)` 部分唯一索引（同 key 只有一条 active）——"记忆卡片"语义。

### customer_memory_event（记忆变更审计）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| customer_id | FK→users(id) CASCADE | |
| conversation_id | String(36) 可空 | 来源会话 |
| memory_id | FK→customer_memory(id) 可空 | 关联记忆 |
| event_type | String(32) | upsert |
| payload | JSON | 变更负载 |
| created_at | DateTime(tz) | |

---

## 10. 知识库 / RAG

### knowledge_base（知识库）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| name | String(128) | 库名 |
| description | Text | 描述 |
| status | String(20) | active |
| metadata_json | JSONB | |
| created_at / updated_at | DateTime(tz) | |

### knowledge_document（文档）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| knowledge_base_id | FK→knowledge_base(id) | 所属库 |
| document_key | String(1000) | 文档稳定键 |
| file_name | String(255) | 文件名 |
| source_path | String(1000) | 源路径 |
| file_type | String(32) | docx / plain_text |
| file_size | Integer | 大小 |
| content_hash | String(64) | 内容 SHA-256 |
| category | String(64) | 分类 |
| permission_level | String(32) | public / internal / advisor 等 |
| status | String(32) | processing / active / failed |
| current_version_id | UUID 可空 | 当前激活版本指针 |
| metadata_json | JSONB | |
| error_message | Text 可空 | 失败原因 |
| created_at / updated_at | DateTime(tz) | |
| deleted_at | DateTime(tz) 可空 | 软删除 |

> 约束：`uq_knowledge_document_key(knowledge_base_id, document_key)`。

### knowledge_document_version（文档版本）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| document_id | FK→knowledge_document(id) | |
| version_no | Integer | 版本号 |
| content_hash | String(64) | 版本内容哈希 |
| parser_type | String(32) | |
| status | String(32) | processing / active / superseded / failed |
| chunk_count | Integer | 分块数 |
| error_message | Text 可空 | |
| created_at | DateTime(tz) | |
| activated_at | DateTime(tz) 可空 | 激活时间 |
| deleted_at | DateTime(tz) 可空 | |

> 约束：`uq_knowledge_document_version(document_id, version_no)`。

### knowledge_chunk（文本分块 + 向量）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| document_id | FK→knowledge_document(id) | |
| version_id | FK→knowledge_document_version(id) | |
| chunk_index | Integer | 块序号 |
| content | Text | 文本内容 |
| content_hash | String(64) | 块哈希（embedding 复用） |
| title_path | String(1000) | 标题路径 |
| page_number | Integer 可空 | 页码 |
| metadata_json | JSONB | |
| embedding | VECTOR(1024) 可空 | pgvector 向量 |
| status | String(20) | active |
| created_at | DateTime(tz) | |
| deleted_at | DateTime(tz) 可空 | |

> 约束：`uq_knowledge_chunk_position(version_id, chunk_index)`。检索只 JOIN 激活版本 + active 文档。

---

## 11. 数据目录 / NL2SQL

### data_catalog_domain（业务域）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| code | String(64) unique | 域编码 |
| name | String(128) | 域名 |
| description | Text | |
| permission_level | String(32) | |
| allowed_roles_json | JSONB | 允许角色 |
| is_active | Boolean | |
| created_at / updated_at | DateTime(tz) | |

### data_catalog_table（目录表）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| domain_id | FK→data_catalog_domain(id) RESTRICT | |
| database_name / schema_name / table_name | String | 物理表定位 |
| display_name | String(200) | 显示名 |
| description / business_purpose / grain_description | Text | 描述/业务用途/粒度 |
| usage_guidance / non_usage_guidance | Text | 推荐/不推荐使用 |
| table_kind | String(32) | entity 等 |
| permission_level | String(32) | |
| allowed_roles_json | JSONB | 允许角色 |
| aliases_json | JSONB | 别名 |
| is_queryable / is_active | Boolean | 可查询/启用 |
| embedding | VECTOR(1024) 可空 | 语义向量 |
| metadata_json | JSONB | |
| created_at / updated_at | DateTime(tz) | |

> 约束：`uq_data_catalog_table_physical_name(database_name, schema_name, table_name)`。

### data_catalog_column（目录列）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| table_id | FK→data_catalog_table(id) CASCADE | |
| column_name | String(128) | 列名 |
| display_name | String(200) | |
| description / business_meaning | Text | |
| data_type | String(128) | 数据类型 |
| semantic_type | String(64) | 语义类型 |
| unit | String(32) | 单位 |
| ordinal_position | Integer | 顺序 |
| is_nullable / is_primary_key / is_foreign_key | Boolean | |
| is_sensitive / is_queryable | Boolean | 敏感/可查询 |
| allowed_aggregations_json | JSONB | 允许聚合 |
| aliases_json / example_values_json | JSONB | 别名/示例值 |
| embedding | VECTOR(1024) 可空 | |
| metadata_json | JSONB | |
| created_at / updated_at | DateTime(tz) | |

> 约束：`uq_data_catalog_column_name(table_id, column_name)`。

### data_catalog_relation（表关系）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| from_table_id / from_column_name | FK/String | 起点 |
| to_table_id / to_column_name | FK/String | 终点 |
| relation_type | String(32) | foreign_key |
| cardinality | String(32) | many_to_one 等 |
| description | Text | |
| join_guidance | Text | 关联指引 |
| is_preferred / is_active | Boolean | |
| created_at / updated_at | DateTime(tz) | |

> 约束：`uq_data_catalog_relation_edge(from_table_id, from_column_name, to_table_id, to_column_name)`。

### data_catalog_example_query（示例查询 = 受审 SQL 模板）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| domain_id | FK→data_catalog_domain(id) CASCADE | |
| question | Text | 自然语言问题 |
| normalized_question | Text | 规范化问题 |
| intent_code | String(128) | 意图编码 |
| target_table_names_json / target_column_names_json | JSONB | 声明白名单 |
| sql_template | Text | 受审 SQL 模板 |
| explanation | Text | 解释 |
| allowed_roles_json | JSONB | 允许角色 |
| is_approved / is_active | Boolean | 审核/启用 |
| embedding | VECTOR(1024) 可空 | |
| metadata_json | JSONB | |
| created_at / updated_at | DateTime(tz) | |

### data_catalog_revision（目录修订审计）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| resource_type / resource_id | String | 被修订资源 |
| revision_no | Integer | 修订号 |
| action | String(32) | updated |
| snapshot_json | JSONB | 修订快照 |
| changed_by | FK→users(id) 可空 | 修改人 |
| change_note | Text | 修改说明 |
| created_at | DateTime(tz) | |

> 约束：`uq_data_catalog_revision_number(resource_type, resource_id, revision_no)`。

---

## 12. 异步基础设施

### outbox_event（事务发件箱）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| event_type | String(100) | knowledge.document_created 等 |
| aggregate_type | String(64) | 聚合类型 |
| aggregate_id | UUID 可空 | 聚合 ID |
| payload_json | JSONB | 事件负载 |
| status | String(20) | pending / published / dead |
| attempt | Integer | 重试次数 |
| error_message | Text 可空 | 错误信息 |
| published_at | DateTime(tz) 可空 | 发布时间 |
| created_at / updated_at | DateTime(tz) | |

> 与业务行同事务提交；`outbox_worker` 用 `FOR UPDATE SKIP_LOCKED` 认领。

### async_task（后台任务）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| task_type | String(100) | |
| aggregate_type | String(64) | 默认 system |
| aggregate_id | UUID 可空 | |
| idempotency_key | String(128) unique 可空 | 幂等键 |
| status | String(32) | queued / running / success / failed / dead / cancelled |
| attempt | Integer | 重试次数 |
| max_attempts | Integer | 默认 3 |
| payload_json / result_json | JSONB | 入参/结果 |
| error_message | Text 可空 | |
| available_at | DateTime(tz) 可空 | 延迟执行预留 |
| started_at / finished_at | DateTime(tz) 可空 | |
| created_at / updated_at | DateTime(tz) | |

---

## 13. 审计

### audit_logs（审计日志）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| actor_user_id | FK→users(id) 可空 | 操作人 |
| action | String(64) | create_draft / confirm_draft / order_review 等 |
| resource_type | String(64) | |
| resource_id | String(64) | |
| detail | Text | 详情 JSON |
| created_at | DateTime(tz) | |

---

## 14. 关键约束与一致性原则

```text
幂等：
  orders.idempotency_key 唯一（并发防重下单）
  natural_language_order_draft.confirmation_token_hash 唯一（令牌）
  notification.idempotency_key 部分唯一（防重复通知）
  async_task.idempotency_key 唯一（防重复任务）

记忆：
  customer_memory 同 (customer_id, key) 仅一条 active

客服：
  customer_service_session 同坐席同一时刻仅一条 human_active

资金/订单一致性：
  冻结：available -= amount; frozen += amount（confirm_order）
  成交：frozen 扣减 → holding upsert → trade → 资产快照 → money_flow
  状态迁移唯一权威在 Order.transition()

风险门禁：
  risk_alert 未结案(open/acknowledged/investigating/pending_customer/report_pending) → 订单/赎回/转账被拦截
```

---

## 15. 数据飞轮

### bad_case（bad case 采集与标注）
> 2026-09-14 补入：本表由迁移 `0023_bad_case` 引入，但 2026-08-27 版本文档**遗漏**，
> 且当时表数计数（50）与实际（49）不符。按第 15 节"维护规则 1"应同步更新本文档。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | UUID |
| customer_id | String(36) | 关联客户（逻辑关联 `users.id`，未建外键）|
| query | Text | 原始问句 |
| query_hash | String(64) index | 问句哈希（去重）|
| intent | String(64) | 规则/模型识别出的意图 |
| confidence | Float | 置信度 |
| evidence_status | String(64) | RAG 证据状态（sufficient / low_confidence / …）|
| handoff | Boolean | 是否转人工 |
| clarifications | Integer | 澄清轮数 |
| reasons | JSON | 判定为 bad case 的**信号列表**（如 intent=unknown / 低置信 / 规则回退 …）|
| status | String(16) index | 处理状态 |
| created_at | DateTime(tz) | 采集时间 |
| labeled_intent | String(64) | 人工标注的正确意图 |
| requires_clarification | Boolean | 人工判定是否应澄清 |
| category | String(64) | 标注分类 |
| note | Text | 标注备注 |
| split | String(16) | 数据集划分（train/test）|
| labeled_by | String(36) | 标注人 |
| labeled_at | DateTime(tz) | 标注时间 |

**用途**：运行时自动判定"当前问答是不是 bad case"并落库，形成可标注、可回流的数据集，
支撑意图识别与 RAG 的持续评测（见 `docs/数据飞轮与意图识别评测.md`）。

---

## 16. 维护规则

1. 新增实体时，必须同步：ORM Model、Alembic Migration、Schema/Repository/Service、测试和本说明。
2. 不在 DataGrip 中直接修改生产/演示关键业务状态；资金、订单、风险限制应通过 API/Service 产生完整审计。
3. 结构演进优先使用 Alembic；`ensure_schema` 兼容补列只用于开发环境，不替代规范迁移。
4. 连接凭据仅适用本地 Docker 开发环境；生产必须独立账号、最小权限、TLS 和密钥管理。
