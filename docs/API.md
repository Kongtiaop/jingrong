# API 接口文档

- Base URL：`http://127.0.0.1:8010/api/v1`
- 认证：除公开接口外，请在请求头携带 `Authorization: Bearer <access_token>`。
- 响应通常包装为：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "trace_id": "..."
}
```

HTTP 错误可能返回 FastAPI detail，例如 `400`、`401`、`403`、`404`、`409` 或统一 `422`。

## 1. 健康检查

### `GET /health`

无需认证。返回 PostgreSQL/pgvector、Redis、模型和 Embedding 状态。

## 2. 认证 `/auth`

### `POST /auth/login`

```json
{"username":"customer_demo","password":"Demo@2026Customer"}
```

返回 access token 和 refresh token。

### `POST /auth/refresh`

```json
{"refresh_token":"<refresh_token>"}
```

### `POST /auth/logout`

撤销 refresh token。

### `GET /auth/me`

返回当前用户、角色和权限。

### `POST /auth/register`

注册个人客户或员工。

```json
{
  "register_type": "customer",
  "username": "new_customer",
  "password": "AtLeast8Chars",
  "display_name": "新客户"
}
```

员工注册还需要 `company_code`。

## 3. 客户画像 `/profile`

### `GET /profile/me`

返回当前客户画像、最新派生资产和有效持仓。

### `PUT /profile/me`

更新客户可编辑画像。

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

`investment_goal`：`capital_preservation`、`income`、`balanced`、`growth`、`aggressive`。

`liquidity_preference`：`high`、`medium`、`low`。

### `GET /profile/products`

返回正常产品目录，包括风险等级、客户类型、起购金额和产品说明。

### `GET /profile/me/risk-assessment`

返回当前有效风险测评；未正式测评时可能返回 `null`，交易确认会按 C1 provisional 处理。

### `POST /profile/me/risk-assessment`

提交正式风险测评：

```json
{
  "investment_experience_years": 5,
  "max_loss_tolerance": 30,
  "investment_horizon_years": 3,
  "liquidity_need": "medium",
  "investment_goal": "balanced",
  "risk_willingness": 40
}
```

返回 `C1`-`C5`、分数、状态和有效期。

### `GET /profile/me/enterprise-verification`

查询企业认证状态。

### `POST /profile/me/enterprise-verification`

提交企业认证资料：

```json
{
  "company_name": "示例企业",
  "registration_no": "统一社会信用代码",
  "legal_representative": "法定代表人",
  "contact_phone": "13800000000"
}
```

已通过认证的客户重复提交返回 `409`。

### `GET /profile/staff/customers`

需要工作人员客户读取权限。支持：

```text
q
tier
customer_type
limit
offset
```

返回客户资产、层级、风险等级、持仓数量和画像摘要。

### `GET /profile/staff/customer/{user_id}`

返回指定客户的画像、资产和持仓。

## 4. 模拟交易 `/trading`

### `GET /trading/account/me`

返回客户模拟账户余额。

### `POST /trading/orders`

创建待确认订单。

```json
{
  "product_id": "<product_id>",
  "amount": "1000.00",
  "idempotency_key": "optional-unique-key"
}
```

### `GET /trading/orders/me`

返回当前客户订单。

### `POST /trading/orders/{order_id}/confirm`

确认订单并执行：

```text
账户余额检查
→ C1-C5 风险等级检查
→ 客户类型检查
→ 进入 pending_review
```

未测评客户按 `C1 provisional`，仅可确认 C1 产品。

### `POST /trading/orders/{order_id}/cancel`

取消待确认或待审核订单。

### `GET /trading/trades/me`

返回当前客户交易记录。

### `GET /trading/orders/pending`

需要 `order:write` 权限，返回待审核订单。

### `POST /trading/orders/{order_id}/review`

需要 `order:write` 权限，审核通过并执行模拟交易。

```json
{"note":"审核通过"}
```

### `POST /trading/orders/{order_id}/reject`

需要 `order:write` 权限，拒绝订单。

### `GET /trading/orders/customer/{user_id}`

工作人员查看指定客户订单。

## 5. 管理员 `/admin`

需要超级管理员或对应管理员权限。

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

企业认证审核请求：

```json
{"approved":true,"note":"同意"}
```

审核通过后客户类型更新为 `enterprise`，层级为 `enterprise_standard`。

## 6. 知识库 `/knowledge`

知识库具体导入、文档、版本和检索接口以当前 OpenAPI 文档为准：

```text
http://127.0.0.1:8010/docs
```

当前主检索仍为 PostgreSQL/pgvector，Milvus 已完成迁移验证但尚未作为主链路切换。

## 7. 任务 `/tasks`

任务提交和查询接口以 OpenAPI 为准，使用 Redis Streams/Outbox 相关基础设施。

## 8. 权限边界

- 客户不能访问管理员接口。
- 普通工作人员不能操作超级管理员回收站和用户权限。
- 企业客户类型必须通过审核产生。
- 风险等级和适当性判断由后端确定性规则最终决定。
- Agent 只负责理解、解释和建议，不直接改变交易、资产、客户类型或风险等级。
