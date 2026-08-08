# 智能财富管理平台：错误码与成功码参考

> **用途**：前后端联调、浏览器错误提示、HTTP 回归、答辩异常处理说明。  
> **权威来源**：运行中的 FastAPI OpenAPI 和后端实现；本文件归纳当前稳定业务语义。  
> **原则**：前端应优先按 `detail.code` 判断业务分支，按 `detail.message` 展示用户可读提示；不要只按 HTTP 状态码或把错误对象直接转为字符串。

---

## 1. 响应约定

### 1.1 成功响应

```json
{
  "success": true,
  "data": {},
  "error": null,
  "trace_id": "..."
}
```

成功通常不使用统一的字符串“成功码”；客户端应以 **2xx HTTP 状态 + `success=true`** 判断成功。不同操作的成功事实由 `data.status`、`data.action_status` 或资源字段表达。

### 1.2 业务错误响应

```json
{
  "detail": {
    "code": "RISK_CONTROL_BLOCKED",
    "message": "存在未处理风险预警，订单未执行"
  }
}
```

### 1.3 参数验证错误

Pydantic/FastAPI 请求验证失败使用：

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "request validation failed",
    "details": {"errors": []}
  },
  "trace_id": "..."
}
```

---

## 2. HTTP 成功码（正确码）

| HTTP 状态 | 名称 | 本项目中的典型场景 | 前端处理 |
|---|---|---|---|
| `200 OK` | 查询/更新成功 | 查询客户、审核成功、风险动作成功、工单处理、AML 批次查询 | 刷新当前资源或列表，展示 `data` 中的最新状态。 |
| `201 Created` | 资源创建成功 | 创建订单、创建工单、客户补件、上传附件、创建投顾授权/草稿 | 展示创建结果，必要时刷新列表；保留返回的资源 ID。 |
| `202 Accepted` | 异步任务已受理 | `/tasks` 提交异步任务 | 不代表任务完成；轮询任务状态或等待通知/Worker 完成。 |
| `204 No Content` | 无响应体的成功 | 可能用于删除/撤销类接口 | 仅在确有 204 契约时刷新列表，不要尝试解析 JSON。 |

### 2.1 常见业务成功状态

这些不是 HTTP code，而是 `data` 中应展示的业务结果：

```text
订单：pending_confirmation / pending_review / executed / rejected / cancelled
业务操作：pending_review / settled / rejected
风险预警：open / acknowledged / investigating / pending_customer / report_pending / resolved / dismissed
交易限制：none / restricted
工单：open / assigned / in_progress / pending_customer / resolved / closed / cancelled
AML 批次：running / completed / completed_with_errors / failed
Outbox：pending / published / dead
任务：pending / running / success / failed / dead / cancelled
```

---

## 3. HTTP 错误码总览

| HTTP 状态 | 语义 | 处理原则 |
|---|---|---|
| `400` | 参数组合不合法或缺少业务必填条件 | 提示用户补齐参数，不要盲目重试。 |
| `401` | 未登录、Access/Refresh Token 失效 | 尝试一次 Token 刷新；失败则清会话并登录。 |
| `403` | 身份、权限、客户授权或数据范围不足 | 不重试；隐藏/禁用入口只是体验，后端拒绝是最终边界。 |
| `404` | 资源不存在或不可见 | 刷新列表、确认资源 ID、确认数据范围。 |
| `409` | 幂等冲突、重复审核、非法状态、风控阻断 | 回读资源最新状态；不得自动重复提交。 |
| `413` | 附件过大 | 提示压缩或更换文件。 |
| `415` | 附件类型不支持 | 仅允许 JPG/PNG/PDF。 |
| `422` | Schema 或业务字段校验失败 | 显示 `detail.message`；按字段补全，不要仅显示“操作失败”。 |

---

## 4. 认证、管理员与通用权限

| 错误码 | 常见 HTTP | 触发条件 | 建议处理 |
|---|---:|---|---|
| `VALIDATION_ERROR` | 422 | 请求体、查询参数不符合 Pydantic Schema。 | 展示字段错误，修正类型、范围和必填项。 |
| `ADMIN_PERMISSION_REQUIRED` | 403 | 调用管理员专用接口但无管理员权限。 | 隐藏入口并提示权限不足。 |
| `DASHBOARD_PERMISSION_REQUIRED` | 403 | 无后台仪表盘读取权限。 | 返回工作台或申请角色。 |
| `STAFF_REQUIRED` | 403 | 客户身份调用工作人员审核/代办接口。 | 使用工作人员账号。 |
| `USER_NOT_FOUND` | 404 | 管理操作目标用户不存在。 | 刷新用户列表。 |
| `ROLE_NOT_FOUND` / `UNKNOWN_ROLE` | 404 / 400 | 角色不存在或角色编码非法。 | 刷新角色字典，检查请求。 |
| `UNKNOWN_PERMISSION` | 400 | 请求中包含未知权限码。 | 使用 `/admin/permissions` 返回的权限码。 |
| `SUPER_ADMIN_PERMISSIONS_FIXED` | 409 | 尝试修改超级管理员固定权限。 | 不允许通过普通角色编辑收回核心管理权限。 |
| `SELF_SUPER_ADMIN_ROLE_FORBIDDEN` | 409 | 超级管理员尝试移除自己的超级管理员角色。 | 使用其他管理员或保持至少一个超级管理员。 |
| `SELF_ACCOUNT_DISABLE_FORBIDDEN` | 409 | 当前用户尝试禁用自己的账号。 | 由其他管理员执行。 |
| `SELF_ACCOUNT_DELETE_FORBIDDEN` | 409 | 当前用户尝试删除自己的账号。 | 由其他管理员执行。 |

---

## 5. 客户、画像、产品与企业认证

| 错误码 | 常见 HTTP | 触发条件 | 建议处理 |
|---|---:|---|---|
| `CUSTOMER_ACCOUNT_REQUIRED` | 403 | 非客户调用客户本人业务接口。 | 使用客户账号。 |
| `CUSTOMER_NOT_FOUND` | 404 | 客户不存在、非客户身份或不在可见范围。 | 刷新客户列表，确认授权/范围。 |
| `CUSTOMER_ID_REQUIRED` | 400 / 422 | 创建特定业务时缺少客户 ID。 | 补齐客户选择。 |
| `PROFILE_NOT_FOUND` | 404 | 目标客户画像不存在。 | 先完成/补齐客户资料。 |
| `PRODUCT_NOT_FOUND` | 404 | 产品不存在、已删除或不可用。 | 刷新产品列表。 |
| `ALREADY_ENTERPRISE_CUSTOMER` | 409 | 已是企业客户仍重复提交企业认证。 | 查询认证状态，不重复提交。 |
| `VERIFICATION_NOT_FOUND` | 404 | 企业认证记录不存在。 | 刷新审核列表。 |
| `VERIFICATION_ALREADY_APPROVED` | 409 | 已通过的认证再次按通过处理。 | 回读当前认证状态。 |

---

## 6. 投顾授权、推荐与操作草稿

| 错误码 | 常见 HTTP | 触发条件 | 建议处理 |
|---|---:|---|---|
| `ADVISOR_NOT_FOUND` | 404 | 选择的顾问不存在或不是可用投顾。 | 刷新顾问列表。 |
| `ADVISOR_AUTHORIZATION_REQUIRED` | 403 | 投顾读取客户、推荐、草稿或代办时没有 active 授权。 | 由客户先创建有效授权。 |
| `ADVISOR_AUTHORIZATION_EXPIRED` | 403 | 授权已到期。 | 更新/重新创建授权。 |
| `AUTHORIZATION_NOT_FOUND` | 404 | 授权记录不存在。 | 刷新授权列表。 |
| `AUTHORIZED_ACTION_REQUIRED` | 403 | 授权范围不包含 redeem 或 transfer 动作。 | 客户增加相应动作授权。 |
| `AUTHORIZATION_ACTION_NOT_ALLOWED` | 403 | 草稿动作不在授权允许范围。 | 修改授权或更换动作。 |
| `AUTHORIZATION_PRODUCT_NOT_ALLOWED` | 403 | 产品不在授权产品范围。 | 修改授权范围或选择允许产品。 |
| `AUTHORIZATION_RISK_LIMIT_EXCEEDED` | 403 | 产品风险等级超过授权风险上限。 | 降低产品风险或重新授权。 |
| `AUTHORIZATION_SINGLE_LIMIT_EXCEEDED` | 409 | 单笔金额超过授权单笔额度。 | 降低金额或调整授权。 |
| `AUTHORIZATION_DAILY_LIMIT_EXCEEDED` | 409 | 当日累计金额超过授权日额度。 | 等待下一日或调整授权。 |
| `CUSTOMER_CONFIRMATION_REQUIRED` | 409 | 工作人员代办但未提供客户确认。 | 在授权/确认流程完成后再提交。 |
| `DRAFT_NOT_FOUND` | 404 | 投顾操作草稿不存在或不属于当前客户。 | 刷新草稿列表。 |
| `DRAFT_STATE_CONFLICT` | 409 | 草稿状态不允许确认、拒绝或提交订单。 | 回读草稿当前状态。 |
| `DRAFT_ORDER_SUBMISSION_FAILED` | 409 | 确认草稿转订单失败。 | 查看具体后端 message；可能是授权、余额、风险、适当性或状态问题。 |

---

## 7. 订单、赎回、转账与资金操作

| 错误码 | 常见 HTTP | 触发条件 | 建议处理 |
|---|---:|---|---|
| `ACCOUNT_NOT_FOUND` | 404 | 客户、源账户或目标账户不存在。 | 检查客户/账户夹具和收款账户。 |
| `TARGET_ACCOUNT_NOT_FOUND` | 404 | 转账目标账户不存在。 | 修正收款账户。 |
| `SELF_TRANSFER_FORBIDDEN` | 409 | 源账户和目标账户相同。 | 选择其他目标账户。 |
| `INVALID_AMOUNT` | 422 | 金额非正、格式非法或超出规则。 | 使用正数金额。 |
| `INVALID_OPERATION_TYPE` | 422 | `operation_type` 不是 redeem/transfer。 | 使用合法类型。 |
| `INVALID_PAGING` / `INVALID_LIMIT` | 422 | `limit/offset` 范围非法。 | 按接口限制分页。 |
| `INSUFFICIENT_BALANCE` | 409 | 可用余额不足或冻结余额状态不一致。 | 降低金额、等待资金到账或核对订单状态。 |
| `INSUFFICIENT_HOLDING` | 409 | 可赎回持仓不足。 | 降低赎回数量/金额。 |
| `SECOND_CONFIRMATION_REQUIRED` | 409 | 高额转账缺少二次确认。 | 使用确认参数/前端确认流程。 |
| `IDEMPOTENCY_CONFLICT` | 409 | 同一幂等键并发竞争或数据库唯一约束冲突。 | 回读已有资源；不要生成相同业务的新键后盲目重试。 |
| `ORDER_CONFIRMATION_FAILED` | 409 | 订单确认阶段业务校验失败。 | 根据 message 检查适当性、余额、状态或风险。 |
| `ORDER_STATE_CONFLICT` | 409 | 订单不在当前动作允许状态，或重复审核。 | 刷新订单后再处理。 |
| `OPERATION_NOT_FOUND` | 404 | 赎回/转账业务操作不存在或不在查询范围。 | 刷新业务操作列表。 |
| `OPERATION_STATE_CONFLICT` | 409 | 赎回/转账重复审核或状态不允许结算。 | 回读状态，禁止重复提交。 |
| `RISK_CONTROL_BLOCKED` | 409 | 未结案风险预警、交易限制或预审核风险命中阻断订单/业务操作。 | 进入风险预警工作台完成调查、补件、限制/解除和结案；不得绕过。 |

---

## 8. 风险、AML 与交易限制

| 错误码 | 常见 HTTP | 触发条件 | 建议处理 |
|---|---:|---|---|
| `AML_BATCH_NOT_FOUND` | 404 | AML 批次不存在。 | 刷新批次列表。 |
| `RISK_ALERT_NOT_FOUND` | 404 | 风险预警不存在或不可见。 | 刷新预警列表。 |
| `RISK_ALERT_STATE_CONFLICT` | 409 | 已关闭预警再次处理，或状态不允许当前动作。 | 回读预警状态。 |
| `RISK_ACTION_CONFLICT` | 409 | 风险动作不适用于当前订单/账户/预警状态。 | 查看 message，例如订单不能挂起、账户不存在等。 |
| `RISK_PENDING_CUSTOMER_REQUIRES_INSUFFICIENT_INFO` | 422 | 预警转 `pending_customer`，但结论不是 `insufficient_information`。 | 补件场景使用“信息不足”。 |
| `RISK_MATERIAL_REQUEST_NOTE_REQUIRED` | 422 | 要求客户补件但缺少评估说明或资料清单。 | 填写信息不足原因和具体补件材料。 |
| `ASSESSMENT_NOTE_REQUIRED` | 422 | 误报或信息不足未填写评估说明。 | 填写规则证据和判断依据。 |
| `DISPOSITION_REQUIRED` | 422 | `confirmed_risk` 但没有实际处置动作。 | 选择限制交易、挂起/拒绝订单、报告草稿等。 |
| `RISK_CANNOT_CLOSE_CONFIRMED` | 422 | 确认风险后直接结案。 | 先完成调查、报告、限制或其他处置。 |
| `RISK_ASSESSMENT_INCOMPLETE` | 422 | 结案前结论为空或仍为信息不足。 | 形成明确复核结论。 |
| `REVIEW_EVIDENCE_REQUIRED` | 422 | “评估后通过”缺少评估依据、客户沟通或沟通记录。 | 补齐三项证据。 |
| `RISK_RESTRICTION_MUST_BE_RELEASED` | 422 | 账户仍 restricted 时尝试结案。 | 先保存 `cleared_after_review/false_positive` 复核，再解除交易限制，最后结案。 |

### 解除交易限制的正确顺序

```text
confirmed_risk + RESTRICT_TRADING
→ investigating / restricted
→ cleared_after_review 或 false_positive + 复核证据
→ RELEASE_RESTRICTION
→ restriction_status = none
→ resolved / dismissed
```

`RELEASE_RESTRICTION` 的底层 Service 若拒绝，常见原因是：当前未限制、没有复核结论、没有评估依据，或“评估后通过”缺少客户沟通记录。前端应显示后端 message。

---

## 9. 工单、客户补件与附件

| 错误码 | 常见 HTTP | 触发条件 | 建议处理 |
|---|---:|---|---|
| `WORKORDER_PERMISSION_REQUIRED` | 403 | 缺少工单创建/读取/分派/处理权限。 | 检查 RBAC 权限码。 |
| `WORKORDER_CUSTOMER_SCOPE` | 403 | 客户创建、读取、补件或附件操作超出本人范围。 | 仅处理本人工单。 |
| `WORKORDER_CUSTOMER_ONLY` | 403 | 非工单客户提交补充或上传附件。 | 使用工单所属客户账号。 |
| `WORKORDER_ASSIGNEE_REQUIRED` | 403 | 非分派处理人尝试处理/关闭工单。 | 由 `assigned_to` 人员或超级管理员操作。 |
| `WORKORDER_NOT_FOUND` | 404 | 工单不存在或无可见范围。 | 刷新工单列表。 |
| `ASSIGNEE_NOT_FOUND` | 404 | 目标分派人员不存在、非 active staff。 | 刷新可分派人员。 |
| `WORKORDER_STATE_CONFLICT` | 409 | 状态不允许补件、分派、处理或关闭。 | 回读工单状态机。 |
| `WORKORDER_FEEDBACK_NOTE_REQUIRED` | 422 | 标记待客户补充/已解决但没有填写要求或解决说明。 | 填写具体说明。 |
| `WORKORDER_ATTACHMENT_EMPTY` | 422 | 上传空附件。 | 选择非空文件。 |
| `WORKORDER_ATTACHMENT_TYPE` | 415 | 文件不是 JPG/PNG/PDF。 | 更换允许类型。 |
| `WORKORDER_ATTACHMENT_TOO_LARGE` | 413 | 超出附件大小上限。 | 压缩文件或拆分材料。 |
| `WORKORDER_ATTACHMENT_NOT_FOUND` | 404 | 附件元数据不存在。 | 刷新附件列表。 |
| `WORKORDER_ATTACHMENT_FILE_MISSING` | 404 | 元数据存在但受控存储文件丢失。 | 检查上传目录/对象存储与审计。 |

---

## 10. 知识库与异步任务

| 错误码 | 常见 HTTP | 触发条件 | 建议处理 |
|---|---:|---|---|
| `KNOWLEDGE_BASE_NOT_FOUND` | 404 | 知识库不存在或未激活。 | 查询默认知识库/刷新 ID。 |
| `KNOWLEDGE_DOCUMENT_NOT_FOUND` | 404 | 知识文档不存在、已删除或不在可见范围。 | 刷新文档列表。 |
| `TASK_NOT_FOUND` | 404 | 异步任务不存在。 | 核对 task_id。 |

异步任务提交成功 `202 Accepted` 仅代表已受理，不代表 Worker 已完成；应查询任务状态或观察通知/业务结果。

---

## 11. 前端错误处理规范

1. Axios 统一错误处理必须保留 `code`、`detail` 和 `response`；禁止 `new Error(detailObject)`，否则页面会显示 `[object Object]`；
2. `409 RISK_CONTROL_BLOCKED` 应明确展示“订单/业务未执行，需风控处置”；
3. `422` 应展示具体 `detail.message`，如“评估后通过必须记录客户沟通”，而不是“按钮没反应”；
4. `401` 仅尝试一次 Refresh Token 轮换，失败后清理本地 Token；
5. 幂等/状态冲突后应刷新资源，而不是自动再次提交；
6. 文件下载错误需通过带 JWT 的 Axios Blob 请求读取错误，不能依赖裸链接。

---

## 12. 维护规则

- 新增业务错误码时，同步更新：后端接口、前端错误提示、HTTP 回归、`docs/API.md` 和本文件；
- code 命名使用全大写下划线，语义稳定，不因页面文案变化而随意改名；
- 对外展示使用中文 `message`，前端分支与测试使用稳定 `code`；
- 不要将数据库驱动异常直接透出给 API，应映射为稳定业务错误语义。
