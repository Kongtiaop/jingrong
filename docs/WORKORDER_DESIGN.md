# 工单权限与最小闭环设计

> 状态：Accepted for MVP design  
> 日期：2026-08-04

## 1. 目标

建立最小可用工单闭环：

```text
创建 → 读取 → 分派（可选）→ 处理中 → 解决/关闭
```

工单不是交易订单，不得复用 `order:*` 权限；工单状态变化必须写入 `audit_logs`。

## 2. 工单类型

```text
customer_service       客户服务
complaint               投诉
risk_investigation     风控调查
business_exception     业务异常
```

## 3. 状态机

```text
open
  → assigned
  → in_progress
  → pending_customer
  → resolved
  → closed
```

辅助终态：

```text
cancelled
```

MVP 约束：

- 创建时状态为 `open`；
- 分派后变为 `assigned`；
- 处理动作可设置 `in_progress`、`pending_customer` 或 `resolved`；
- 只有 `resolved` 工单可以关闭；
- `closed`、`cancelled` 不允许继续修改；
- 所有非法状态跳转返回 `409`。

## 4. 数据模型

| 字段 | 说明 |
|---|---|
| `id` | UUID |
| `ticket_no` | 可读工单编号，唯一 |
| `customer_id` | 关联客户；系统工单可为空 |
| `created_by` | 创建人 |
| `assigned_to` | 当前处理人，可为空 |
| `category` | 工单类型 |
| `priority` | low/normal/high/urgent |
| `status` | 状态机状态 |
| `title` | 标题 |
| `description` | 问题描述 |
| `resolution_note` | 处理/结案说明 |
| `created_at/updated_at` | 时间 |
| `resolved_at/closed_at` | 处理/关闭时间 |

## 5. 数据范围

- 客户：只能创建和读取自己的工单；不能分派、处理或关闭；
- 客服：可创建、读取、处理客户服务/投诉工单；
- 客户经理：可创建、读取、分派、处理业务服务工单；
- 理财顾问：本 MVP 不自动获得工单权限；
- 风控：可创建、读取、分派、处理风控调查工单；
- 运营：可创建、读取、分派、处理业务异常工单；
- 审计：只读所有职责范围内工单；
- 超级管理员：系统级放行。

MVP 先采用角色和 `customer_id` 范围校验；后续若引入组织/客户分配关系，再收紧工作人员的客户范围。

## 6. API 与权限

| API | 权限 |
|---|---|
| `POST /workorders` | `workorder:create` |
| `GET /workorders` | `workorder:read` |
| `GET /workorders/{id}` | `workorder:read` |
| `POST /workorders/{id}/assign` | `workorder:assign` |
| `POST /workorders/{id}/handle` | `workorder:handle` |
| `POST /workorders/{id}/close` | `workorder:handle` |

客户本人接口通过资源归属二次校验；权限码不替代数据范围。

## 7. 非目标

- 不接入外部工单系统；
- 不实现邮件、短信、站内通知；
- 不实现复杂 SLA 计时和自动升级；
- 不实现附件/富文本；
- 不把工单直接绑定到风险报告上报或交易审核。
'''


## 8. MVP 实现结果（2026-08-04）

已实现：

- `work_order` 模型和 `0010_work_order` Alembic migration；
- `POST/GET /workorders`、`GET /workorders/{id}`；
- `POST /workorders/{id}/assign`；
- `POST /workorders/{id}/handle`；
- `POST /workorders/{id}/close`；
- 创建、分派、处理、关闭审计日志；
- 客户本人范围、工作人员角色权限、审计只读边界。

暂未实现：通知、附件、SLA、外部系统、复杂组织/客户分配关系和独立前端工单页面。


## 9. 前端 MVP 入口（2026-08-04）

已接入：

- 客户中心新增“我的工单”，支持创建和查看本人工单；
- 后台工作台新增“工单工作台”，按 `workorder:read` 显示；
- 客户/后台按钮按 `workorder:create`、`workorder:assign`、`workorder:handle` 控制；
- 工单状态、优先级、类型、处理说明和分派入口已形成统一卡片式界面；
- 保持移动端单列布局和表单可访问性。

暂未实现：工单筛选分页、处理人下拉选择、附件、通知和 SLA。


## 10. 超级管理员分派补充（2026-08-04）

- 后端 `workorder:assign` 对 `is_super_admin` 明确放行；
- 新增 `GET /workorders/assignees`，为具备分派权限的人员返回 active 非客户账号；
- 前端不再要求手工填写处理人 UUID，改为处理人下拉选择；
- 超级管理员可以选择具备 `workorder:handle` 的 active 工作人员（客服、客户经理、风控、运营，或其他被授予该权限的人员）并分派；理财顾问没有处理权限，不能作为处理人。


## 11. 分派持久化验证（2026-08-04）

分派不是只改变前端卡片：

- API 返回 `assigned_to` 为目标工作人员 ID；
- API 返回状态为 `assigned`；
- 数据库 `work_order.assigned_to` 持久化目标工作人员 ID；
- `audit_logs` 写入 `action=assign`；
- 重新读取工单仍能看到处理人和 assigned 状态。

## 12. 分派、处理权限与反馈验收（2026-08-04）

本轮进一步确认并收紧工单执行契约：

- 分派会写入 `work_order.assigned_to`，状态变为 `assigned`，并写入 `audit_logs.action=assign`；重新读取仍可看到处理人和状态。
- 具备 `workorder:handle` 的角色为：客服、客户经理、风控、运营；客户、理财顾问、产品经理、审计没有处理权限；超级管理员走系统级放行。
- 即使有 `workorder:handle`，非超级管理员也只能处理/关闭 `assigned_to == 当前用户` 的工单；不允许处理其他人的分派工单。
- 处理流程为：`assigned/open → in_progress 或 pending_customer → resolved → closed`；`resolved` 时必须提交非空处理说明，说明写入 `resolution_note` 并记录处理审计；已解决工单不能重新打开。
- 处理人可以通过 `GET /workorders?scope=assigned` 查看自己的分派工单；不具备分派权限的处理角色前端默认只加载该范围，非本人分派工单不显示处理按钮。
- 当前仍没有站内信、邮件、短信或推送通知。也就是说，分派人不会主动收到通知，只能登录工单工作台查看自己的分派列表；通知/未读数是下一阶段工作。


## 13. 站内通知与未读提醒（2026-08-04）

已实现最小站内通知闭环：

- 新增 `notification` 表、`0011_notification` migration 和 `/notifications` API；用户只能读取、标记已读自己的通知。
- 分派成功后向 `assigned_to` 创建 `workorder_assigned` 通知；分派目标必须有处理权限，避免通知指向无法处理的人。
- 处理人将工单置为 `pending_customer` 或 `resolved` 时，向客户（系统工单则向创建人）创建进度通知；关闭后再创建关闭通知。
- 后台顶部提供通知收件箱，通知点击后会标记已读并进入对应工单；处理人可以主动看到自己的新分派提醒。
- 当前通知是站内持久化通知；邮件、短信、移动推送仍不是本阶段目标。

## 14. 客户补充材料闭环（2026-08-04）

“待客户补充”不再只是状态和通知，现已形成完整的材料往返流程：

```text
工作人员填写补充要求并置为 pending_customer
  → 要求写入 work_order_update，客户可查看
  → 客户提交补充内容
  → 客户补充写入 work_order_update，状态自动回到 in_progress
  → assigned_to 收到客户补充通知并在工单中查看内容
  → 原处理人继续处理，才能 resolved / closed
```

权限边界：客户只能为自己的、且状态为 `pending_customer` 的工单提交补充；工作人员不能伪造客户补充；客户和有工单读取权限的工作人员可查看该工单的补充记录。

## 15. 工单待办视图、筛选分页与状态标签（2026-08-04）

已将工单工作台从“全量卡片堆叠”提升为可处理的待办视图：

- `GET /workorders` 新增 `priority` 筛选，并按紧急、高、普通、低优先级后再按创建时间排序；
- 新增 `GET /workorders/summary`，按当前数据范围返回待处理、已分派、处理中、待客户补充、已解决及高/紧急数量；
- 前端支持“我的待办/全部可见”、状态、优先级、类型筛选、重置和分页；
- 工单状态不再使用纯圆形胶囊：改为带语义图标的方圆角状态标签，例如处理中 `↻`、待客户补充 `…`、已解决 `✓`，颜色与状态一一对应。

## 16. 工单附件/凭证上传（2026-08-05）

已实现最小附件闭环：

- 新增 `work_order_attachment` 表和 `0013_work_order_attachment` migration；
- 客户仅能给自己处于 `pending_customer` 的工单上传附件；
- 仅允许 JPG、PNG、PDF，默认单文件上限 10 MB；服务器存储使用随机文件名，避免原始文件名路径穿越；
- 新增附件列表和下载 API，工作人员按工单读取范围查看附件；
- 前端客户补充区域增加文件选择和上传按钮；工单记录中显示附件下载链接；
- 上传行为写入 `audit_logs.action=upload_attachment`。

## 17. 工作人员附件自动加载与认证下载（2026-08-05）

修复工作人员“看到附件但无法下载”的前端认证问题：

- 原实现使用 `<a href>` 直接跳到受保护下载 URL，浏览器不会携带 `sessionStorage` 中的 Bearer Token，服务端返回 401；
- 改为通过 axios 请求下载接口（`responseType=blob`），复用 HTTP 拦截器自动携带 Authorization，再由浏览器触发保存；
- 工单列表加载时，工作人员也自动拉取每条可见工单的附件列表，不再要求先点击“查看补充记录”。
