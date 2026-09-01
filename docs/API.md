# 智能财富管理平台 API 接口文档

> **文档用途**：前后端联调、答辩接口说明、DataGrip/HTTP 回归排查。
> **当前 API 前缀**：`/api/v1`。本地实际端口以启动命令和环境变量为准；不要把旧文档中的固定 `8010` 当作唯一端口。
> **权威契约**：运行中的 FastAPI OpenAPI：`/docs`、`/openapi.json`；本文件说明稳定业务边界和常用请求。

---

## AML Scheduler 状态

`GET /risk-alerts/scheduler-status`

- 权限：`risk:alert_read`。
- 返回 `running`、`last_heartbeat_at`、`last_scan_started_at`、`last_scan_finished_at`、`last_scan_status`、`last_error`。
- `running` 依据最近心跳是否在 `max(3 * AML_SCHEDULER_POLL_SECONDS, 180)` 秒内判定，只表示 Worker 存活，不表示当前正在扫描。

`POST /risk-alerts/batches/{batch_id}/retry`

- 权限：`risk:scan_execute`。
- 仅允许 `failed` 或 `completed_with_errors` 批次；解析原 `error_summary` 中失败客户，按客户创建或复用独立补跑批次。
- 原批次不被修改，返回 `source_batch_id` 和每个客户的补跑批次，保证错误证据和补跑记录可追溯。

`POST /risk-alerts/batches/{batch_id}/late-rescan`

- 权限：`risk:scan_execute`；仅已完成窗口可补扫迟到流水。
- 创建或复用 `run_type=late_rescan` 批次，稳定键为 `late_rescan:{source_batch_id}`；返回来源批次和补扫批次 ID。

---

## 1. 通用约定

### 1.1 认证

除健康检查、登录、注册和公开产品等接口外，携带：

```http
Authorization: Bearer <access_token>
X-Trace-Id: <uuid，可选，前端默认自动写入>
```

Access Token 失效后，前端使用 Refresh Token 轮换；刷新失败会清除本地会话并回到登录页。

### 1.2 统一成功响应

```json
{
  "success": true,
  "data": {},
  "error": null,
  "trace_id": "..."
}
```

### 1.3 错误响应

业务错误通常在 FastAPI `detail` 中返回稳定 code/message：

```json
{
  "detail": {
    "code": "RISK_CONTROL_BLOCKED",
    "message": "存在未处理风险预警，订单未执行"
  }
}
```

常见状态码：

| 状态码 | 含义 |
|---|---|
| `400` | 请求参数或业务前置条件错误 |
| `401` | 未认证、Token 失效或刷新失败 |
| `403` | 缺少权限或资源数据范围不足 |
| `404` | 资源不存在或不在可见范围 |
| `409` | 幂等冲突、重复审核、非法状态转换、风险门禁 |
| `422` | Schema 校验或风险处置必填字段不完整 |

### 1.4 幂等

订单、赎回、转账等写操作支持 `idempotency_key`。客户端网络重试必须复用同一个键；业务后端、数据库唯一约束和状态机共同防止重复资金副作用。

---

## 2. 健康检查与认证

### `GET /health`

无需认证。返回 PostgreSQL/pgvector、Redis、模型/Embedding 等健康状态。

### `/auth`

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/auth/login` | 登录，返回 Access/Refresh Token。 |
| `POST` | `/auth/refresh` | 使用 Refresh Token 获取轮换后的 Token。 |
| `POST` | `/auth/logout` | 撤销 Refresh Token 会话。 |
| `GET` | `/auth/me` | 返回当前用户、角色、权限和超级管理员标识。 |
| `POST` | `/auth/register` | 注册客户或员工；员工注册需要公司码。 |

登录示例：

```json
{"username":"customer_demo","password":"Demo@2026Customer"}
```

---

## 3. 客户画像、产品、投顾与企业认证 `/profile`

### 客户本人

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/profile/me` | 客户画像、最新资产、持仓、当前风险操作状态。 |
| `PUT` | `/profile/me` | 更新客户可编辑画像。 |
| `GET` | `/profile/me/assets/history` | 资产历史快照。 |
| `POST` | `/profile/me/assets` | 创建资产快照（开发/测试场景）。 |
| `GET` | `/profile/products` | 正常产品目录。 |
| `GET` | `/profile/me/tier` | 当前客户分层。 |
| `GET` | `/profile/me/recommendations` | 当前客户适当性推荐与排除原因。 |
| `GET/POST` | `/profile/me/risk-assessment` | 查询/提交风险测评。 |
| `GET/POST` | `/profile/me/enterprise-verification` | 查询/提交企业认证。 |

更新画像示例：

```json
{
  "age": 35,
  "occupation": "企业管理",
  "region": "上海",
  "investment_goal": "balanced",
  "investment_horizon_years": 3,
  "liquidity_preference": "medium"
}
```

### 投顾授权与草稿

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET/POST` | `/profile/me/advisor-authorizations` | 查询/创建客户—投顾授权。 |
| `DELETE` | `/profile/me/advisor-authorizations/{authorization_id}` | 撤销授权。 |
| `GET` | `/profile/me/operation-drafts` | 查询投顾操作草稿。 |
| `POST` | `/profile/me/operation-drafts/{draft_id}/confirm` | 客户确认草稿。 |
| `POST` | `/profile/me/operation-drafts/{draft_id}/reject` | 客户拒绝草稿。 |
| `POST` | `/profile/me/operation-drafts/{draft_id}/submit-order` | 将客户确认草稿提交为订单。 |

### 工作人员客户与产品接口

| 方法 | 路径 | 权限/范围 |
|---|---|---|
| `GET` | `/profile/staff/customers` | `customer:read_all`；支持 `q/tier/customer_type/limit/offset`。 |
| `GET` | `/profile/staff/customers/{user_id}` | 管理客户详情，返回 `user/profile/latest_asset/holdings/risk_operation`。 |
| `GET` | `/profile/staff/advisor-customers` | 仅当前投顾的 active 授权客户。 |
| `GET` | `/profile/staff/advisors` | 客户选择顾问列表。 |
| `POST` | `/profile/staff/recommendations` | 针对指定客户调用确定性适当性推荐。 |
| `POST` | `/profile/staff/operation-drafts` | 工作人员/投顾创建操作草稿；不直接扣款。 |
| `GET/POST` | `/profile/staff/products` | 工作人员产品列表/创建。 |
| `PUT/DELETE` | `/profile/staff/products/{product_id}` | 更新/软删除产品。 |
| `PUT` | `/profile/staff/products/{product_id}/restore` | 恢复已删除产品。 |

---

## 4. 订单与模拟交易 `/trading`

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/trading/account/me` | 当前客户模拟账户余额。 |
| `POST` | `/trading/orders` | 创建客户待确认订单。 |
| `GET` | `/trading/orders/me` | 当前客户订单，分页响应。 |
| `GET` | `/trading/orders/{order_id}` | 订单详情。 |
| `POST` | `/trading/orders/{order_id}/confirm` | 客户确认订单，进入 `pending_review`。 |
| `POST` | `/trading/orders/{order_id}/cancel` | 取消可取消订单。 |
| `GET` | `/trading/orders/pending` | 工作人员待审核订单。 |
| `POST` | `/trading/orders/{order_id}/review` | 审核通过并执行；会再次进行风险门禁。 |
| `POST` | `/trading/orders/{order_id}/reject` | 审核拒绝。 |
| `GET` | `/trading/orders/customer/{user_id}` | 工作人员查看指定客户订单。 |
| `GET` | `/trading/trades/me` | 当前客户成交记录。 |

创建订单：

```json
{
  "product_id": "<product_id>",
  "amount": "1000.00",
  "idempotency_key": "customer-order-uuid"
}
```

审核前若发现未结案风险预警或交易限制，可能返回：

```text
409 RISK_CONTROL_BLOCKED
```

订单、资金、持仓和审计由确定性交易 Service 在事务中处理；前端或 Agent 不可绕过。

---

## 5. 赎回、转账与业务操作 `/operations`

### 客户本人发起

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/operations/redeem` | 客户提交赎回申请。 |
| `POST` | `/operations/transfer` | 客户提交转账申请。 |
| `GET` | `/operations/me` | 查询本人赎回/转账，支持 `operation_type/status/limit/offset`。 |
| `GET` | `/operations/me/{operation_id}` | 查询本人单笔业务操作。 |

赎回请求：

```json
{
  "product_id": "<product_id>",
  "quantity": "10.00",
  "amount": "1000.00",
  "idempotency_key": "customer-redeem-uuid"
}
```

转账请求：

```json
{
  "to_account": "目标账户号",
  "amount": "1000.00",
  "confirmed": true,
  "idempotency_key": "customer-transfer-uuid"
}
```

### 工作人员代办与审核

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/operations/staff/redeem` | 为已授权且已确认的客户代办赎回。 |
| `POST` | `/operations/staff/transfer` | 为已授权且已确认的客户代办转账。 |
| `GET` | `/operations/staff/pending` | 工作人员待审核业务队列。 |
| `GET` | `/operations/staff/{operation_id}` | 工作人员查看自己可读的代办操作。 |
| `POST` | `/operations/redeem/{operation_id}/review` | 审核赎回通过/拒绝。 |
| `POST` | `/operations/transfer/{operation_id}/review` | 审核转账通过/拒绝。 |
| `GET` | `/operations/audit-logs` | 业务操作审计，按角色隔离可见范围。 |

工作人员代办额外校验客户授权、授权动作、客户确认、额度、风险、余额/持仓和幂等。审核结算时仍会再次检查状态和风险。

审核请求：

```json
{"approve": true, "note": "审核通过"}
```

状态语义：

```text
pending_review → settled / rejected
```

转账通过时在同一事务内完成双方余额、`transfer/out`、`transfer/in` 流水及审计；赎回通过时完成持仓减少、余额增加、`redeem/in` 流水及审计。

---

## 6. 风险、AML 与交易限制 `/risk-alerts`

| 方法 | 路径 | 权限/说明 |
|---|---|---|
| `GET` | `/risk-alerts` | `risk:alert_read`；支持状态、等级、客户、分页过滤。 |
| `GET` | `/risk-alerts/{alert_id}` | 读取单条预警。 |
| `POST` | `/risk-alerts/scan` | `risk:scan_execute`；手工扫描指定客户窗口。 |
| `GET` | `/risk-alerts/batches` | AML 日批列表，支持状态和分页。 |
| `GET` | `/risk-alerts/batches/{batch_id}/alerts` | 查看批次关联预警。 |
| `POST` | `/risk-alerts/{alert_id}/handle` | `risk:alert_handle`；接单、调查、补件、评估、结案。 |
| `POST` | `/risk-alerts/{alert_id}/action` | `risk:alert_action`；执行挂起、拒绝、限制、解除等动作。 |

风险处置请求关键字段：

```json
{
  "status": "investigating",
  "assessment_result": "confirmed_risk",
  "assessment_note": "命中规则、资金来源核验与复核依据",
  "customer_consulted": false,
  "customer_consultation_note": "",
  "disposition_code": "RESTRICT_TRADING",
  "disposition_note": "限制原因和后续复核安排"
}
```

常用动作：

```text
HOLD_ORDER
REJECT_ORDER
RESTRICT_TRADING
RELEASE_RESTRICTION
REQUEST_MATERIALS
CREATE_INVESTIGATION
CREATE_REPORT_DRAFT
ESCALATE
```

风险补件：`REQUEST_MATERIALS` 会创建/复用 `risk_investigation` 工单并通知客户。客户在“我的工单”补件后，风险预警仍需风控人工复核。

解除限制条件：当前确实 restricted，且已有 `cleared_after_review` 或 `false_positive` 的复核结论和评估依据；`cleared_after_review` 还需要客户沟通记录。解除后再回风险工作台结案。

---

## 7. 工单、通知与附件

### `/workorders`

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/workorders` | 创建工单。 |
| `GET` | `/workorders` | 列表，支持 `scope/status/category/priority/limit/offset`。 |
| `GET` | `/workorders/summary` | 待办统计。 |
| `GET` | `/workorders/assignees` | 可分派工作人员。 |
| `GET` | `/workorders/{id}` | 工单详情。 |
| `POST` | `/workorders/{id}/assign` | 分派。 |
| `POST` | `/workorders/{id}/handle` | 处理中、待客户补充、已解决。 |
| `POST` | `/workorders/{id}/supplements` | 客户补充文字材料。 |
| `GET` | `/workorders/{id}/updates` | 工单时间线。 |
| `POST` | `/workorders/{id}/close` | 关闭。 |
| `GET/POST` | `/workorders/{id}/attachments` | 附件列表/上传。 |
| `GET` | `/workorders/{id}/attachments/{attachment_id}/download` | 鉴权下载附件。 |

工单状态：

```text
open → assigned → in_progress → pending_customer → resolved → closed
```

客户只能读取本人、在 `pending_customer` 时补件；工作人员默认只能处理分派给自己的工单；附件下载必须携带 Bearer Token。

### `/notifications`

通知接口用于读取站内通知、标记单条已读和全部已读。具体路径以 OpenAPI 为准；风险补件、工单分派、客户补充和结案均可生成通知。

---

## 8. 知识库、异步任务与后台管理

### `/knowledge`

用于知识库默认库、文档创建、导入、检索和版本/Chunk 生命周期。当前主检索为 PostgreSQL + pgvector；知识文档创建后通过 Outbox + Redis Streams + Knowledge Worker 异步切分、Embedding 和激活版本。

### `/tasks`

提交和查询异步任务。任务受理通常返回 `202`；任务状态、重试、失败原因和结果由 `AsyncTask` 记录。

### `/admin`

管理员用户、角色、权限、状态、回收站、审计日志和企业认证审核接口。典型接口：

```text
GET  /admin/users
GET  /admin/roles
GET  /admin/permissions
PUT  /admin/users/{user_id}/roles
PUT  /admin/users/{user_id}/status
DELETE /admin/users/{user_id}
PUT  /admin/users/{user_id}/restore
GET  /admin/recycle-bin
GET  /admin/audit-logs
GET  /admin/enterprise-verifications
POST /admin/enterprise-verifications/{verification_id}/review
```

---

## 9. 权限与安全边界

1. 前端按钮和菜单不是安全边界，所有接口由后端 RBAC + 资源数据范围校验；
2. 客户不能读取工作人员客户管理、审核、风险动作或管理员接口；
3. 工作人员代办不等于可无限操作客户资金，必须满足客户授权、确认、额度、风险和审计约束；
4. 风险限制由后端业务入口检查，不能通过直接调用 API 绕过；
5. RAG/Agent 只能检索、分析、生成建议或草稿；不能直接修改余额、持仓、订单状态、权限或风险限制；
6. 对受保护附件使用带 JWT 的 API 下载，不能使用裸静态 URL。

---

## 10. 开发调试建议

```text
OpenAPI：/docs
数据库表说明：docs/DATABASE_TABLE_REFERENCE.md
错误码与成功码：docs/ERROR_CODES.md
系统答辩/架构说明：docs/DEFENSE_SYSTEM_DESIGN_AND_EVOLUTION.md
权限矩阵：docs/RBAC_PERMISSION_MATRIX.md
测试记录：docs/TESTS.md
问题与修复过程：docs/ISSUES.md
```

涉及资金、风险状态、订单或权限时，优先通过 API/Service 操作并保留审计；不要为了演示直接在数据库修改关键状态。

`POST /api/v1/customer-rag/answer`

- 认证：当前登录用户；客服 RAG 只返回引用型回答结果，不执行交易。
- `intent`：`company_info`、`company_business`、`faq`、`workflow`、`policy`、`risk_public`、`product_recommendation`。
- 非产品意图：按客服公开 Scope 检索知识库，低于阈值返回 `low_confidence` 和 `handoff_required=true`。
- `product_recommendation`：返回 `database_recommendation`，由 Product/画像/适当性流程处理，不检索当前缺失的产品 RAG 文档。

## 客服受控事实查询（Query Router MVP）

### `POST /api/v1/customer-rag/route`

客户已登录后发起自然语言查询。该接口只对首批已批准的客户本人实时事实进行确定性路由，不调用 RAG、不会创建工单，也不会执行任何业务操作。

请求：

```json
{
  "query": "我的订单审核到哪一步了"
}
```

当前支持的 `route`：

| route | 权威来源 | 客户数据范围 |
|---|---|---|
| `workorder_status` | `work_order` | 仅 `customer_id = 当前用户` |
| `order_status` | `orders` + `product` | 仅 `orders.user_id = 当前用户` |
| `risk_restriction` | `account` + 当前风险预警 | 仅 `account.user_id = 当前用户` |
| `asset_summary` | 最近资产快照 + 有效持仓 | 仅 `user_id = 当前用户` |
| `product_status` | `product` 实时状态 | 仅按明确产品名称查询，不进行推荐 |

响应中的 `source_type`：

- `business_fact`：已返回实时业务事实；
- `rag_explanation`：当前问题不属于已接入事实查询，客户端可继续调用 `/customer-rag/answer` 获取公开知识库解释；
- `handoff`：为后续扩展预留，当前 MVP 不由该接口创建工单。

示例响应：

```json
{
  "success": true,
  "data": {
    "source_type": "business_fact",
    "route": "order_status",
    "answer": "您最近的订单 WO... 当前状态为“pending_review”。",
    "data": {"items": []},
    "citations": [],
    "evidence_status": "sufficient",
    "next_action": "订单状态以系统实时记录为准；如需了解流程规则，可继续询问具体状态含义。"
  }
}
```

路由优先级遵循 `DECISION-20260807-01`：实时业务事实优先，RAG 只用于流程、条款和背景解释，不能覆盖实时状态或执行受控操作。


## 客服会话消息增量查询与断线补偿

### 查询人工消息

```http
GET /api/v1/customer-service/sessions/{session_id}/messages
Authorization: Bearer <access_token>
```

可选查询参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `after` | string | 最后一条已消费消息的 ID。传入后仅返回该游标之后的消息；不传则返回会话消息快照。 |

返回结果按 `created_at ASC, id ASC` 排序，单次最多返回 200 条。客户只能访问自己的会话，坐席只能访问自己已接入的会话；`after` 只用于缩小读取范围，不改变原有权限校验。

### 前端恢复约定

1. 收到人工消息后更新 `lastMessageId`；
2. 合并增量消息前按消息 ID 去重；
3. WebSocket `error`/`close` 时显示 recovering 状态并执行一次 `after` 查询；
4. 固定轮询继续作为降级同步机制；
5. 会话进入 resolved/closed 后停止轮询并清理浏览器会话缓存。

WebSocket/PubSub 是低延迟通知通道，HTTP 增量查询和 PostgreSQL 才是断线恢复所依赖的事实链路。
