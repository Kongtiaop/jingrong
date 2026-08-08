# 智能财富管理平台：系统设计、架构演进与工程复盘（答辩版）

> **文档用途**：公司答辩、项目交接、技术复盘。  
> **项目名称**：智能财富管理平台（Wealth Manager）  
> **文档日期**：2026 年 8 月 5 日  
> **说明**：本文以当前代码、`docs/PLAN.md`、`docs/ISSUES.md`、`docs/TESTS.md`、`docs/DECISIONS.md` 和 `docs/WORKORDER_DESIGN.md` 为依据。文中明确区分“已实现”与“后续增强”，避免将设计目标误表述为已上线能力。

---

## 目录

1. [项目背景与建设目标](#1-项目背景与建设目标)
2. [总体架构](#2-总体架构)
3. [为什么采用这种架构](#3-为什么采用这种架构)
4. [技术栈与部署拓扑](#4-技术栈与部署拓扑)
5. [核心功能模块设计](#5-核心功能模块设计)
6. [权限、数据范围与审计设计](#6-权限数据范围与审计设计)
7. [风险、AML 与交易控制设计](#7-风险aml-与交易控制设计)
8. [RAG 与受控 Agent 架构：已实现能力与演进路线](#8-rag-与受控-agent-架构已实现能力与演进路线)
9. [投顾、赎回与转账：关键业务闭环](#9-投顾赎回与转账关键业务闭环)
10. [工单系统从 MVP 到闭环的迭代过程](#10-工单系统从-mvp-到闭环的迭代过程)

11. [关键踩坑、根因与工程改进](#11-关键踩坑根因与工程改进)
12. [测试策略与质量保障](#12-测试策略与质量保障)
13. [数据模型与核心实体关系](#13-数据模型与核心实体关系)
14. [分阶段迭代路线](#14-分阶段迭代路线)
15. [当前能力边界与后续计划](#15-当前能力边界与后续计划)
16. [答辩演示建议与常见问题](#16-答辩演示建议与常见问题)

---

## 1. 项目背景与建设目标

### 1.1 业务问题

财富管理业务不是单纯的“买卖产品”页面，而是同时包含：

- 客户准入、客户资料、风险测评与资产画像；
- 产品管理、产品适当性匹配与推荐；
- 认购、赎回、转账等资金和交易操作；
- 工作人员代办、客户确认、审核、拒绝及审计追踪；
- AML（反洗钱）规则扫描、风险预警、限制交易与调查处置；
- 客户服务、投诉、风险调查、业务异常等工单协同；
- 知识库问答与制度/产品资料检索。

因此项目的目标不是拼接若干页面，而是建立一套能够说明“**谁可以做什么、对谁做、做完后如何追溯、异常时如何拦截和协同**”的业务系统。

### 1.2 项目目标

本项目的建设目标可归纳为五条：

1. **业务闭环**：客户、工作人员、审核人员和管理员能够在同一系统中完成主要业务流程。
2. **权限可信**：前端隐藏按钮不等于权限控制，所有关键动作必须由后端 RBAC 和资源数据范围校验。
3. **风险前置**：交易与关键操作在执行前经过适当性、风险状态、AML/预警等控制。
4. **过程可追溯**：创建、分派、处理、审核、下载凭证、风险动作等关键行为可审计。
5. **可演进性**：先交付最小闭环，再通过迁移、测试、文档和模块边界持续扩展，而不是一次性堆砌复杂功能。

---

## 2. 总体架构

### 2.1 分层架构图

```text
┌───────────────────────────────────────────────────────────────────────┐
│                             Vue 3 前端                                │
│                                                                       │
│  客户中心 / 工作人员后台 / 管理后台 / 工单工作台 / 风险管理 / 产品展示 │
│  Pinia 登录态 / Axios 认证拦截 / 路由守卫 / 响应式 UI                 │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ HTTPS / JSON / multipart 文件上传
┌───────────────────────────────▼───────────────────────────────────────┐
│                         FastAPI 应用层                                │
│                                                                       │
│ auth / profile / trading / operations / risk / workorders             │
│ notifications / knowledge / admin / tasks / health                    │
│                                                                       │
│ TraceIdMiddleware / CORS / 统一异常处理 / JWT 认证                    │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
┌───────────────────────────────▼───────────────────────────────────────┐
│                       领域服务与安全层                                │
│                                                                       │
│ AuthService / TradingService / SuitabilityService / RiskAlertService  │
│ AML 扫描 / 估值服务 / 工单通知 / 任务服务 / 客户分层                   │
│ RBAC、数据范围、幂等、审计、状态机、风险门禁                           │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
┌───────────────┬───────────────┼────────────────┬──────────────────────┐
│ PostgreSQL    │ Redis         │ Milvus/pgvector│ 本地附件存储         │
│ 业务主数据    │ 缓存/队列     │ 知识库向量检索 │ uploads/workorders   │
│ 审计/工单/权限│ 异步任务支撑  │ RAG 语义召回   │ 受认证下载           │
└───────────────┴───────────────┴────────────────┴──────────────────────┘
```

### 2.2 后端目录职责

```text
app/
├── api/              HTTP 路由与请求入口
├── common/           通用响应、异常、认证、中间件、日志、限流等
├── core/             配置与应用级设置
├── db/               数据库连接、元数据、健康检查、Schema 初始化
├── models/           SQLAlchemy 持久化实体
├── schemas/          Pydantic 请求/响应契约
├── services/         领域服务与业务编排
├── infrastructure/   Redis、模型服务、第三方基础设施适配
├── repositories/     仓储实现与数据访问适配
├── ports/            仓储/基础设施抽象接口
└── tasks/            异步任务及相关逻辑
```

### 2.3 前端目录职责

```text
frontend/src/
├── api/              后端接口封装、Axios 请求
├── stores/           登录态等跨页面状态
├── views/            页面级组件：客户中心、后台、工单、风险等
├── components/       可复用组件：通知收件箱等
├── router/           路由基础设施
├── assets/           静态资源
└── styles.css        全局样式与门户布局风格
```

---

## 3. 为什么采用这种架构

### 3.1 不把所有业务写进 API 路由

早期业务系统容易出现“一个路由几十行到几百行，查询、校验、资金变化、审计全写在一起”的问题。这样短期开发快，但后续会出现：

- 同一适当性逻辑在认购、赎回、推荐中重复；
- 风险限制容易遗漏在某个交易入口；
- 单元测试和端到端测试无法覆盖关键分支；
- 新增角色或数据范围时需要在多处修补；
- 出错时难以判断问题来自 API、权限还是状态机。

因此项目将复杂业务逻辑沉到服务层：

```text
API 路由：认证、参数解析、返回 HTTP 响应
服务层：业务规则、状态流转、风控、审计、资金动作
模型层：持久化结构与关系
Schema：对外数据契约
```

例如：

- `TradingService` 管理交易执行和订单动作；
- `SuitabilityService` 管理客户与产品风险匹配；
- `RiskAlertService` 管理风险预警、动作和限制；
- 工单路由负责具体的工单状态机、权限和通知联动；
- `NotificationService` 负责创建站内通知。

### 3.2 数据库是业务事实来源，前端只是呈现层

项目多次出现过“页面看似变了，但数据库是否真的变了”的风险。因此明确原则：

```text
前端卡片状态 ≠ 业务状态
API 返回成功 ≠ 已验证持久化
数据库字段 + 审计记录 + 重读验证 = 真实业务完成
```

工单分派就是典型案例。最终验收不仅检查接口 200，还检查：

- `work_order.assigned_to` 是否写入目标人员；
- 工单状态是否变为 `assigned`；
- `audit_logs` 是否产生 `assign`；
- 重新读取是否仍看到分派人；
- 处理人能否在 `scope=assigned` 范围看到工单；
- 非分派人员是否被后端拒绝处理。

### 3.3 用状态机而不是任意字符串更新状态

金融交易、风险预警和工单都存在状态。若允许任意修改，会造成：

- 已关闭工单被重新处理；
- 未解决工单直接关闭；
- 客户补充后无法回到工作人员待处理状态；
- 风险动作、订单状态与余额变化不一致。

所以关键流程采用受控状态机：

```text
工单：
open / assigned
  → in_progress
  → pending_customer
  → resolved
  → closed

客户补充：
pending_customer
  → 客户提交补充
  → in_progress
```

非法流转由后端返回 `409 Conflict`，而不是依靠前端禁用按钮。

### 3.4 用“权限 + 资源范围”双重控制，而不是只看角色

“客服可以处理工单”并不代表“客服可以处理所有工单”。

项目最终形成如下模型：

```text
第一层：功能权限
    是否具备 workorder:handle？

第二层：资源范围
    该工单是否 assigned_to 当前用户？

第三层：例外规则
    是否超级管理员？
```

这使以下规则同时成立：

- 客服、客户经理、风控、运营具备处理能力；
- 非超级管理员只能处理分派给自己的工单；
- 客户、理财顾问、产品经理、审计不能处理；
- 超级管理员可作为系统级例外处理；
- 客户只能读取、补充自己的工单。

---

## 4. 技术栈、运行时与部署拓扑（技术答辩详版）

> 本章不只罗列框架名称，而是说明：**为什么选、在代码中落在哪里、解决了什么问题、替代方案是什么、项目中踩过哪些坑**。

### 4.1 技术选型总览

| 层级 | 技术/组件 | 当前落点 | 解决的问题 | 关键取舍 |
|---|---|---|---|---|
| 前端 | Vue 3 + Composition API | `frontend/src/views`、`components`、`stores` | 多角色门户、响应式交互、组件状态组织 | 轻量，适合快速形成后台和客户中心；未引入重量级 UI 库以保持页面风格可控 |
| 前端构建 | Vite | `npm run dev` / `npm run build` | 极速热更新、生产打包 | 开发体验好，但构建通过不等于运行时模板路径正确，需要额外现场验证 |
| HTTP 客户端 | Axios | `frontend/src/api/http.ts` | JWT 自动携带、401 刷新、Blob 文件下载 | 统一拦截器减少每个接口重复带 Token 的错误 |
| Web API | FastAPI | `app/main.py`、`app/api/*.py` | 异步 API、OpenAPI、参数校验、文件上传 | Python 异步模型适合 PostgreSQL/Redis/HTTP 并发 I/O；必须避免 Async ORM 懒加载陷阱 |
| 数据契约 | Pydantic v2 | `app/schemas/*.py` | 请求合法性、响应稳定性、自动 OpenAPI | Schema 与 ORM 分离，避免把数据库字段直接暴露为 API 契约 |
| ORM | SQLAlchemy 2 Async | `app/models`、`app/db/session.py` | 异步事务、实体映射、复杂查询 | 强一致业务仍以关系库为核心；异步场景必须显式预加载关系 |
| 主数据库 | PostgreSQL 16 + pgvector | Docker `postgres`、业务主表 | 交易、权限、审计、工单、风险等强一致事实数据 | 选择 ACID 关系库，而非把 Redis/向量库当业务事实源 |
| 数据库迁移 | Alembic | `migrations/versions` | 表结构版本化、部署演进、历史环境升级 | 已有 13 个版本迁移，避免直接手改生产表 |
| 缓存/任务支撑 | Redis 7 | `RedisClient`、任务/Stream 相关模块 | 缓存、异步任务、队列能力 | Redis 是辅助状态，不承担交易事实写入 |
| 向量检索 | Milvus + pgvector 兼容路线 | `app/infrastructure`、知识库模块 | 文档语义检索、RAG 召回 | Milvus 面向独立向量检索；pgvector 保留统一数据库路线，便于不同部署规模选择 |
| LLM/Agent | OpenAI SDK、LangChain、LangGraph、Qwen Provider | `app/agents`、`app/infrastructure/qwen.py` | 知识问答、Agent 编排、模型适配 | 模型能力与业务事实分离；模型输出不能直接绕过权限或写入交易数据 |
| 认证 | PyJWT + Argon2 | `auth_service`、`common/security/auth.py` | JWT、Refresh Token、密码哈希 | Access/Refresh 分离；Refresh 轮换避免 Token 重放 |
| 文件上传 | python-multipart + 本地受控目录 | 工单附件 API、`uploads/workorders` | 客户上传 JPG/PNG/PDF 凭证 | MVP 用本地存储，生产可迁移对象存储；下载必须走鉴权 API |
| 基础设施编排 | Docker Compose | `docker-compose.yml` | 本地可复现 PostgreSQL、Redis、Milvus、MySQL 验证环境 | MySQL 当前是迁移验证 profile，不作为主写库 |
| 质量保障 | Python 编译、ASGI HTTP 回归、Vite 构建、文档记录 | `scripts/test_*.py`、`docs/TESTS.md` | 防止权限、状态机、数据持久化回归 | 不只断言 200，还检查数据库字段、审计、文件、余额、状态 |

### 4.2 后端运行时架构：一次请求如何流动

以“客户提交工单补充并上传附件”为例，完整链路如下：

```text
Vue 组件事件
  → workordersApi（Axios）
  → 请求拦截器自动注入 Authorization / X-Trace-Id
  → FastAPI 路由
  → current_user 解 JWT 并从数据库加载用户、角色、权限
  → 功能权限校验 + 资源数据范围校验
  → SQLAlchemy AsyncSession 事务
  → WorkOrder / WorkOrderUpdate / WorkOrderAttachment 持久化
  → AuditLog 写入
  → Notification 写入
  → commit
  → Pydantic 响应模型
  → Axios 响应
  → Vue 刷新工单、补充记录、附件与提示信息
```

这里有三个关键设计：

1. **请求身份不是直接相信前端传参**。用户由 JWT 的 `sub` 解析并数据库复核得到。
2. **权限不是唯一条件**。例如客户必须同时满足“是客户、属于该工单、工单处于待补充状态”。
3. **一个业务动作的相关写入在同一事务内完成**。例如补充文本、状态回流、通知、审计应一起提交，避免页面显示成功但数据库只写了一半。

### 4.3 FastAPI：为什么选异步 API 框架

#### 选型原因

系统包含大量 I/O：

```text
PostgreSQL 查询
Redis 通信
文件读取/上传
向量检索
模型 HTTP 调用
后台任务触发
```

FastAPI 的 `async def` 与 ASGI 运行模式适合并发等待型业务，不需要为每个 I/O 请求阻塞一个线程。

#### 实际落点

```text
app/main.py
    应用生命周期、数据库/Redis 初始化、CORS、中间件、异常处理

app/api/*.py
    auth、profile、trading、risk、operations、workorders、notifications 等路由

app/common/middleware/trace.py
    为每个请求关联 Trace ID，记录完成状态和耗时
```

#### 踩坑：异步 ORM 的 `MissingGreenlet`

在工单处理人列表中，最初查询用户时只预加载了 `User.roles`，后续访问 `role.permissions` 触发了异步环境下的隐式懒加载，报错：

```text
sqlalchemy.exc.MissingGreenlet
```

修复为显式预加载：

```text
selectinload(User.roles).selectinload(Role.permissions)
```

工程结论：

> 在 Async SQLAlchemy 中，不能把关系懒加载当作“随时可用”的同步属性；读到的关系必须在查询阶段明确加载。

### 4.4 Pydantic Schema：为什么 ORM 模型不能直接当 API 返回

数据库模型关心的是持久化：

```text
字段类型、外键、索引、默认值、关系
```

API 契约关心的是：

```text
请求字段是否必填
枚举是否合法
对外返回哪些字段
日期、空值、分页如何表达
```

因此每个主要业务域都有 Schema，例如：

```text
WorkOrderCreateRequest
WorkOrderHandleRequest
WorkOrderCustomerSupplementRequest
WorkOrderResponse
WorkOrderUpdateResponse
WorkOrderAttachmentResponse
WorkOrderTodoSummaryResponse
```

收益：

- 传入非法状态、优先级会在边界处得到 `422`；
- 前端类型更稳定；
- OpenAPI 自动体现真实接口；
- 数据库新增内部字段不会无意暴露给客户端；
- 便于测试请求/响应契约。

### 4.5 PostgreSQL：为什么业务事实必须落关系型主库

财富管理场景存在典型强一致需求：

```text
订单状态
账户余额
持仓数量
资金流水
权限角色
工单分派
审计日志
风险限制
```

这些数据具有关系、约束、事务和可追溯要求，因此选择 PostgreSQL 作为主事实库。

#### PostgreSQL 在项目中的职责

```text
users / roles / permissions / refresh_sessions
customer_profile / account / holding / asset snapshot
orders / trades / money flow / order status history
risk_alert / aml batch run / investigation
business_operation / audit_logs
work_order / work_order_update / work_order_attachment
notification / async_task / outbox_event
knowledge 文档元数据与可选 pgvector 索引
```

#### 为什么不是把 Redis 当数据库

Redis 很适合：

```text
缓存
临时状态
任务队列/Stream
限流
短生命周期协调
```

但不适合作为订单余额、工单分派、审计等长期事实来源，因为这些需要：

```text
ACID 事务
外键关系
复杂过滤
持久化审计
可迁移 Schema
```

#### 为什么要 Alembic

项目不断增加：

```text
AML 批次表
工单表
通知表
客户补充记录表
工单附件表
```

如果直接执行手工 SQL，环境之间容易不一致。Alembic 将表结构变化编码为版本：

```text
0001_p0_baseline
...
0010_work_order
0011_notification
0012_work_order_update
0013_work_order_attachment
```

答辩时可强调：

> 数据库不是一次建完的，业务变化通过可追踪迁移演进，支持新环境初始化和历史环境升级。

### 4.6 SQLAlchemy Async：事务、锁与状态机如何配合

#### 事务边界

典型模式：

```python
async with session_factory() as session:
    # 查询 + 校验
    # 修改业务实体
    # 写审计/通知
    await session.commit()
```

#### `with_for_update=True` 的作用

在工单分派、处理、关闭、客户提交补充等会修改状态的操作中，采用行锁查询：

```python
await session.get(WorkOrder, work_order_id, with_for_update=True)
```

意图是降低并发情况下两个请求同时把同一工单推进到不同状态的风险。

#### 状态机比简单 update 更安全

错误方式：

```text
UPDATE work_order SET status = 'closed'
```

正确方式：

```text
读取当前状态
→ 校验允许的下一状态
→ 校验操作者权限和数据范围
→ 写状态、时间、说明、审计、通知
→ commit
```

这也是为什么“已解决必须有说明”“只有已解决才能关闭”“客户补充后回到处理中”均由后端规则保证。

### 4.7 Redis、AsyncTask 与 Outbox：为什么不把所有事情同步做完

#### Redis 的定位

项目中 Redis 用于：

```text
健康检查
缓存/短状态
流式任务基础
异步任务协调
```

#### AsyncTask 的定位

有些操作不适合放在用户等待的 HTTP 请求里：

```text
AML 日批扫描
批量估值
知识文档处理
可能的外部通知投递
```

因此用 `AsyncTask` 记录：

```text
任务类型
状态
尝试次数
最大重试次数
失败原因
创建时间
```

#### Outbox 的定位

当未来需要邮件、短信、推送时，不建议在“分派工单”的数据库事务中直接调用外部服务。推荐模式：

```text
业务事务提交
  → 写 OutboxEvent
  → 后台 Worker 读取并投递
  → 成功/失败/重试可追踪
```

原因：外部服务超时不能拖垮主业务请求，也不能导致“工单已分派但数据库回滚/通知不一致”。

### 4.8 向量检索、Milvus、pgvector 与知识库路线

#### 为什么需要向量检索

制度、产品、公司资料和客户研判规则以长文档形式存在。关键词检索难以覆盖：

```text
同义表达
问句与原文措辞不一致
跨段落语义关联
```

向量检索把文本转成 embedding 后，通过语义相似度召回相关片段。

#### 双路线设计

```text
Milvus：独立向量数据库，适合规模化检索与专用索引
pgvector：PostgreSQL 扩展，适合较小规模、统一运维和关系数据联动
```

项目保留两条适配路线，而当前业务写入仍以 PostgreSQL 为主。

#### 关键边界

RAG/LLM 只能作为：

```text
制度问答辅助
知识检索辅助
业务解释辅助
```

不能直接作为：

```text
交易执行事实来源
权限判断来源
风险动作最终判定来源
```

原因是模型输出可能不稳定、不可证明、难审计。最终交易和权限仍要回到结构化规则与数据库事实。

### 4.9 LLM、LangChain、LangGraph 与模型适配层

依赖中包含：

```text
openai
langchain
langchain-core
langchain-community
langgraph
langchain-openai
```

同时项目提供 `QwenProvider` 等基础设施适配。

设计思路不是把模型 SDK 散落在业务路由，而是通过基础设施/Provider 层隔离：

```text
业务服务
  → Provider / Agent 抽象
  → OpenAI / Qwen / 本地模型等实现
```

好处：

- 模型供应商可替换；
- prompt、检索、Agent 编排可集中管理；
- 业务模块不依赖某一家模型 API；
- 模型不可用时可以降级，不影响核心交易与权限业务。

### 4.10 JWT、Refresh Token 与密码安全

#### 认证策略

```text
密码：Argon2 哈希
短期凭证：Access Token（JWT）
长期会话：Refresh Token（数据库持久化）
刷新策略：Refresh Token Rotation
```

#### 为什么要 Refresh Rotation

Refresh Token 被使用后会轮换，旧 Token 再用返回 401。这样可以降低 Token 被复制后长期重放的风险。

#### 踩坑：开发重置后的持续 401

开发环境重置数据库后，浏览器仍保存旧 refresh token。此前前端会不断重复 refresh，造成连续 401。

修复策略：

```text
refresh 失败
  → 清除 sessionStorage access/refresh token
  → 派发 auth:changed
  → 受保护路由跳转登录页
```

工程结论：

> 安全设计不仅要处理“正常登录”，还要处理令牌失效、开发重置、多标签页和旧 Token 重放等异常路径。

### 4.11 Axios 拦截器：认证、401 重试与受保护文件下载

`frontend/src/api/http.ts` 统一实现：

```text
请求前：
  写入 X-Trace-Id
  从 sessionStorage 获取 access_token
  注入 Authorization: Bearer ...

响应 401：
  非 refresh 请求时尝试刷新 Token
  刷新成功后重放原请求
  刷新失败后清 Token 并通知应用登录态变化
```

#### 踩坑：普通 `<a href>` 下载附件返回 401

附件最初用：

```html
<a href="/api/v1/workorders/.../download">
```

但浏览器原生链接不会读取 `sessionStorage` 并附加 JWT，因此后端下载接口返回 401。

最终改为：

```text
Axios GET + Authorization
responseType = blob
URL.createObjectURL
创建临时 <a> 触发浏览器保存
```

这证明：

> 受保护资源的下载不是“有 URL 就能下载”，必须走带认证的请求链路。

### 4.12 前端 Vue 3 设计：页面、组件、状态与交互边界

#### 为什么使用 Composition API

工单页面同时包含：

```text
列表
分页
筛选
待办统计
分派
处理
客户补充
附件上传
附件下载
通知跳转定位
```

Composition API 可以按业务状态组织：

```text
rows / filters / page / summary
updates / attachments
saving / loading / error / message
```

而不是把逻辑分散在 Options API 的 data、methods、computed 中。

#### Store 边界

登录状态集中在 `stores/auth.ts`：

```text
access token
refresh token
当前用户 me
登录、登出、刷新、清理会话
```

业务页面不重复维护用户身份，只消费 `auth.me` 的角色和权限。

#### 踩坑：构建成功但运行时函数不存在

某次修改后页面出现：

```text
loadUpdates is not defined
```

虽然 `npm run build` 可以通过，但运行时 `load()` 调用了被误删的函数，导致客户补充页面初始化中断。

改进：

```text
构建验证
+ Vite 实时源文件检查
+ 浏览器控制台/网络请求现场验收
+ ASGI 端到端回归
```

结论：

> TypeScript/Vite 构建不能覆盖所有动态模板与运行时路径，尤其是文本替换后的 Vue 组件。

### 4.13 文件上传与本地受控存储：MVP 为什么这样做

#### 上传限制

```text
MIME 类型：image/jpeg、image/png、application/pdf
默认单文件限制：10 MB
文件名：服务端 uuid 随机名
原始文件名：仅作为展示和下载文件名
目录：uploads/workorders
```

#### 为什么随机存储名

不能直接以用户原始文件名作为服务器路径，因为会有：

```text
重名覆盖
路径穿越
特殊字符
猜测文件路径
```

因此保存逻辑是：

```text
原始名：用于用户展示
随机名：用于实际文件存储
附件表：保存二者映射、工单、上传人、类型、大小
```

#### 当前局限与生产演进

当前本地存储适合答辩和单机开发。生产可升级为：

```text
MinIO / S3 / OSS
预签名 URL 或受控下载代理
病毒扫描
文件内容检测
图片缩略图
CDN
对象生命周期管理
```

但无论存哪里，工单权限校验不能丢失。

### 4.14 Docker Compose 与环境隔离

当前 Compose 服务：

```text
postgres（pgvector 镜像）
redis
milvus
mysql（migration profile）
```

#### 为什么 MySQL 不是主写库

项目保留 MySQL 作为迁移验证路线，但业务主库仍是 PostgreSQL。这样做是因为跨数据库迁移最危险的不是“连不上”，而是：

```text
同一写入在两个库语义不一致
约束/索引/事务行为不同
历史数据迁移不完整
生产读写混用导致事实源分裂
```

当前设置明确：

```text
PostgreSQL = 主写入事实源
MySQL = 验证/迁移 profile
```

在迁移门禁未完成前，不允许 MySQL 直接服务核心 HTTP 交易写入。

### 4.14A 异步任务（AsyncTask）：定义、职责与当前实现边界

#### 1. 什么是异步任务

异步任务不是“把任意函数扔到后台执行”这么简单。它的本质是：

```text
用户请求先完成可确认的业务登记
  → 把耗时/可重试/可延后执行的工作记录为任务
  → Worker 在后续独立执行
  → 将执行结果、重试次数、失败原因回写
```

它适用于不要求用户同步等待的工作，例如：

```text
AML 日批扫描
批量资产估值
知识文档切分、向量化、索引
未来的邮件、短信、推送投递
报表生成
批量风险复核
```

不适用于必须立即完成并返回最终事实的关键交易写入，例如：

```text
扣减余额
变更订单最终状态
变更持仓
工单分派最终落库
```

这些核心动作仍应在当前数据库事务中同步完成，否则会出现“页面显示交易成功，但后台任务失败”的事实不一致。

#### 2. 当前 AsyncTask 表的定义

当前 `AsyncTask` 记录的不是业务结果本身，而是后台执行的可管理状态：

```text
id                 任务唯一 ID
task_type          任务类型
aggregate_type     关联业务聚合类型
aggregate_id       关联业务实体 ID
idempotency_key    防止重复创建同一任务
status             queued / running / succeeded / failed 等
attempt            当前尝试次数
max_attempts       最大重试次数
payload_json       任务输入参数
result_json        执行结果
error_message      失败信息
available_at       可执行时间
created_at         创建时间
updated_at         更新时间
```

这套字段说明项目的异步设计已经考虑了：

```text
排队
幂等
重试
延迟执行
结果记录
错误追踪
```

而不是只用一个“后台线程”悄悄执行。

#### 3. 当前代码如何创建任务

`TaskService.create_task()` 的关键逻辑是：

```text
1. 如果携带 idempotency_key，先查询是否已经创建过；
2. 若已存在，直接返回原任务，避免重复入队；
3. 创建 AsyncTask，初始状态 queued；
4. 同时创建 OutboxEvent（event_type=task.created）；
5. 在同一 PostgreSQL 事务中 commit；
6. API 返回 202 Accepted，而不是假装任务已经执行完成。
```

这里 `202 Accepted` 的语义很重要：

```text
请求被接收并已登记
≠ 后台工作已经完成
```

#### 4. 为什么任务需要数据库表，而不是只用 Redis 队列

只把任务放在 Redis 中的问题是：

```text
Redis 消息被消费后，业务侧很难查询历史
任务重试、失败原因、关联实体不容易审计
服务重启后任务状态的可追踪性不足
用户无法通过 API 查询任务进度
```

因此项目采用：

```text
PostgreSQL AsyncTask：任务业务事实与状态记录
Redis：队列/Stream/消费者协调的基础设施
```

换句话说：

```text
数据库回答“任务是什么、有没有成功、失败原因是什么”
Redis 帮助回答“谁来执行、何时执行、如何分发”
```

#### 5. 当前实现与未来 Worker 的关系

当前项目已经具备任务表、任务 API、OutboxEvent、Redis Stream 配置项：

```text
redis_stream_prefix
redis_consumer_group
redis_consumer_name
task_max_attempts
task_block_ms
```

这为 Worker 订阅、消费、重试打下基础。当前可对外说明为：

> 项目已经完成异步任务的持久化与可靠投递基础设计；具体的每一种任务 Worker 可按业务需求逐步接入。

不能夸大为：

```text
所有后台任务已经通过分布式 Worker 完整自动运行
```

除非相应 Worker、调度和运行记录已经在现场部署并验收。

### 4.14B Outbox 模式：为什么它是异步可靠性的关键

#### 1. 要解决的经典问题：双写不一致

假设“客户上传知识文档后需要异步向量化”。最朴素的代码可能是：

```text
先把文档写入 PostgreSQL
再发送 Redis 消息
```

会有两种危险情况：

```text
情况 A：数据库写成功，Redis 消息发送失败
    → 文档存在，但永远不会向量化

情况 B：Redis 消息发送成功，数据库事务最终失败
    → Worker 收到一条指向不存在文档的消息
```

这就是典型的“双写不一致”。

#### 2. Outbox 的定义

Outbox 模式的思路是：

```text
把“需要发送给外部系统/异步 Worker 的事件”
先作为一条普通数据库记录写入与业务数据同一个事务。
```

当前 `OutboxEvent` 主要字段包括：

```text
id
 event_type
aggregate_type
aggregate_id
payload_json
status
attempt
error_message
published_at
created_at
updated_at
```

#### 3. 当前项目的真实使用

项目已经在至少两类场景使用 Outbox：

```text
任务创建：
AsyncTask + OutboxEvent(event_type=task.created)

知识文档创建：
KnowledgeDocument + OutboxEvent(event_type=knowledge.document_created)
```

它们在同一数据库事务中写入。

因此可以保证：

```text
业务实体提交成功
→ 对应待投递事件也一定存在

业务实体回滚
→ 对应事件也不会出现
```

#### 4. Outbox 与 Worker 的完整目标流程

```text
业务 API
  → PostgreSQL 事务：写业务表 + 写 OutboxEvent(pending)
  → commit

Outbox Publisher / Worker
  → 轮询或订阅 pending event
  → 发布到 Redis Stream / 消息系统
  → 处理任务
  → 更新 AsyncTask / OutboxEvent 状态
  → 成功写 published_at，失败增加 attempt 和 error_message
```

这是一种“至少一次投递（at-least-once）”方向的设计。因此消费者还必须具备：

```text
幂等处理
重复消息容忍
可重试
失败可观测
```

#### 5. 为什么 Outbox 与工单通知也有关

当前工单站内通知直接在主事务中写入 `notification` 表，这是合理的，因为通知本身是本系统内的数据库数据。

未来如果要扩展：

```text
邮件
短信
企业微信
APP Push
第三方工单系统
```

则推荐：

```text
工单分派事务
  → 写 Notification（站内立即可见）
  → 写 OutboxEvent（外部通知待投递）
  → Worker 异步发送
```

这样外部服务不可用时，工单分派仍然成功，外部提醒可重试，不会阻塞核心业务。

### 4.14C 双库适配：当前做了什么、没做什么、为什么做

#### 1. 先明确结论

**答辩口径（必须明确）**：当前项目尚未上线生产级主从读写分离、跨库双写或 PostgreSQL/MySQL 双主事实源；所有核心业务写入仍以 PostgreSQL 为准。

当前项目的双库能力是：

```text
PostgreSQL：当前主业务读写事实源
MySQL：迁移/兼容验证适配路线
```

它**不是**：

```text
已经上线的 PostgreSQL 主库 + MySQL 从库读写分离
也不是生产环境双写双读
```

答辩时必须明确这一点，避免技术表述不严谨。

#### 2. 当前配置表达的意图

配置中存在：

```text
DATABASE_BACKEND
DATABASE_READ_BACKEND
DATABASE_WRITE_BACKEND
MIGRATION_SHADOW_READ
MYSQL_URL
MYSQL_VALIDATION_WRITES
```

同时存在强约束：

```text
当 DATABASE_WRITE_BACKEND 不是 postgresql 时，
若没有显式打开 MYSQL_VALIDATION_WRITES，则应用拒绝启动。

MYSQL_VALIDATION_WRITES 在生产环境禁止启用。
```

这意味着项目明确把“迁移验证”和“生产主写切换”隔离开，防止有人误把未验证的 MySQL 写入当作正式事实源。

#### 3. 为什么需要双库适配/仓储抽象

项目已经出现：

```text
ports/repositories.py      定义 CustomerRepository、ProductRepository、RiskAssessmentRepository 等协议
repositories/*.py          PostgreSQL/SQLAlchemy 实现
repositories/mysql_profile.py
repositories/unit_of_work.py
```

其中 MySQL 适配器提供与部分业务服务兼容的读取/事务访问接口，例如：

```text
MySQLUsersAdapter
MySQLProfilesAdapter
MySQLProductsAdapter
MySQLAccountsAdapter
MySQLOrdersAdapter
MySQLHoldingsAdapter
MySQLTradesAdapter
MySQLStatusHistoryAdapter
```

这说明架构已经开始将：

```text
业务服务依赖的“数据能力”
```

与：

```text
具体是 PostgreSQL 还是 MySQL 的 SQL 实现
```

分开。

#### 4. 它为什么不是简单“为了好看”的抽象

没有仓储/Unit of Work 边界时，业务代码常直接写：

```text
select(User)...
update(Order)...
insert(MoneyFlow)...
```

并散落在路由与服务中。此时如果未来改数据库、增加只读副本、做影子读校验，几乎需要全项目替换 SQL。

引入适配边界后，理论上可以做到：

```text
业务服务：我要读取订单、创建交易、查询客户画像
适配器：决定从哪个数据库、以什么 SQL、怎样映射实体读取
```

这为未来演进留出切换点。

#### 5. 与“主从读写分离”的关系

是的，双库适配和 `database_read_backend` / `database_write_backend` 配置，**可以看作是为未来读写分离、多库迁移和影子验证打地基**，但两者不是同一件事。

可以分三层理解：

```text
第一层：数据库方言/多库适配
    PostgreSQL 与 MySQL 都能提供相似的数据访问能力。

第二层：影子读（Shadow Read）
    主库正常返回结果，同时从候选库读取并比对，用于验证迁移正确性。

第三层：读写分离/主从复制
    写入固定主库；查询按一致性需求路由到主库或只读副本。
```

当前项目主要处于：

```text
第一层已开始实现
第二层已有配置与验证方向
第三层尚未作为正式生产能力启用
```

#### 6. 未来读写分离应该如何设计

如果未来使用 PostgreSQL 主库 + PostgreSQL 只读副本，建议不是简单按“所有 GET 都读从库”分流，而应按一致性要求分流：

```text
强一致读（必须主库）：
- 刚提交订单后读取订单状态
- 刚分派工单后读取处理人
- 审核、余额、持仓、风险限制
- 当前登录态、权限、Refresh Session

可延迟读（可考虑副本）：
- 历史报表
- 已归档审计查询
- 产品公共列表
- 长时间范围资产趋势
- 非实时知识库检索元数据
```

原因是复制存在延迟。若用户刚提交工单却从副本读取，可能看到“还没分派”；刚审核订单却从副本读取，可能看到旧余额，这在金融业务中不能接受。

#### 7. 双写为什么危险

未来数据库迁移时最危险的方案是：

```text
请求同时写 PostgreSQL 和 MySQL
其中一个成功、另一个失败
```

会产生事实分叉。因此正确演进更接近：

```text
阶段 1：PostgreSQL 主写，MySQL 影子读比对
阶段 2：小范围验证写入，但不服务生产事实
阶段 3：迁移回放、校验、对账
阶段 4：明确切换主写库
阶段 5：保留旧库只读回退窗口
```

并需要：

```text
幂等键
事件重放
审计对账
主键策略统一
时间字段与金额精度一致
约束/索引一致性验证
回滚预案
```

#### 9. Unit of Work 的作用

`Unit of Work` 的意义是把一组相关仓储操作放到同一事务边界中：

```text
订单更新
资金流水写入
成交记录写入
状态历史写入
审计写入
```

必须：

```text
全部成功一起提交
或其中任一步失败一起回滚
```

这不仅对 PostgreSQL 有意义，也为未来数据库适配提供统一的事务入口。

### 4.14C-1 Repository、Unit of Work 与分库思想：把“业务语义”从“数据库细节”中分离出来

项目中的 Repository 和 Unit of Work（UoW）不是为了抽象而抽象，而是为了在金融业务中同时解决两个问题：**数据库实现可演进**，以及**跨多张表的关键业务写入必须原子提交**。

#### 1. 四层职责边界

```text
API Router
  → 解析 HTTP、认证依赖、请求/响应 Schema、状态码映射

Service
  → 权限与资源范围、状态机、适当性、风险、额度、幂等、业务决策

Repository
  → 单类数据的读取、锁定、保存、查询细节

Unit of Work
  → 把多个 Repository 放入同一个事务，决定 commit / rollback 边界
```

这四层必须各司其职：

| 层 | 应负责 | 不应负责 |
|---|---|---|
| Router | HTTP 参数、身份依赖、DTO、错误响应 | 散落业务 SQL、跨表事务、资金计算 |
| Service | “能不能做”、状态如何流转、规则和错误语义 | 拼接 PostgreSQL/MySQL 方言 SQL |
| Repository | “怎样读写这类实体”、查询/锁定/映射 | 决定客户是否有权限、是否适当、是否允许审核 |
| Unit of Work | “哪些 Repository 操作必须一起成功/回滚” | 自行解释业务规则或绕过 Service |

例如 `TradingService` 不应关心 `SELECT ... FOR UPDATE` 的具体 SQL 写法，它只表达“锁定源账户、锁定目标账户、结算并写流水”；具体锁定查询在 Account Repository 中实现，UoW 再保证账户、流水、业务操作、审计和 Outbox 同属一个事务。

#### 2. Repository：隔离数据库、方言与查询实现

如果 API 路由或 Service 中到处直接写：

```python
select(User)
update(Account)
insert(MoneyFlow)
```

那么未来切换 MySQL、增加只读副本、做影子读、引入数据库专用查询或调整索引时，SQLAlchemy/方言细节会扩散到客户、交易、风险、工单等所有业务模块，既难替换也难测试。

Repository 把底层实现隐藏在稳定业务接口之后，例如：

```text
UsersRepository
AccountsRepository
OrdersRepository
HoldingsRepository
MoneyFlowsRepository
RiskAlertsRepository
WorkOrdersRepository
AssetsRepository
KnowledgeRepository
```

业务 Service 面向的是稳定语义：

```text
获取客户账户
锁定订单或账户
读取活动风险预警
保存资金流水
统计活跃持仓市值
写入资产快照
```

而不是面向“PostgreSQL 的某种 SQL 语法”。因此 PostgreSQL 和 MySQL 可以拥有不同 SQL、不同索引、不同执行计划，只要它们返回统一 DTO、金额精度、状态与错误语义即可。

#### 3. Unit of Work：解决多 Repository 写入的原子性

Repository 解决“单类实体如何访问”，但不能单独保证多个实体一起更新时不出现半提交。以转账审核通过为例，需要跨多个 Repository：

```text
AccountsRepository：锁定源/目标账户，更新双方余额
BusinessOperationsRepository：pending_review → settled
MoneyFlowsRepository：写 transfer/out 与 transfer/in
AuditLogsRepository：写审核/结算审计
OutboxRepository：写 transfer.settled 待投递事件（适用时）
```

若每个 Repository 自行 `commit`，可能发生：

```text
源账户扣款已提交
→ 目标账户入账失败
→ 流水或状态未写入
→ 产生不可接受的资金部分一致
```

UoW 将这些操作绑定到同一个 PostgreSQL Session/Transaction：

```text
开始事务
  → 锁定并校验源账户、目标账户、业务操作状态
  → 更新双方余额
  → 写双边流水
  → 更新业务操作状态
  → 写 AuditLog 与 OutboxEvent
全部成功 → commit
任一步失败 → rollback
```

因此 UoW 的核心价值不是“提供一个对象”，而是把**业务原子性边界**从零散 Repository 提升为可复用、可测试、可审计的事务边界。

#### 4. 当前分库定位：不是双主写，而是主库 + 兼容/影子验证

当前系统的真实边界必须明确：

```text
PostgreSQL：唯一核心业务读写事实源
MySQL：迁移、兼容、Repository/UoW 契约与影子验证
Redis：缓存、队列、Streams 协调，不承担资金事实
pgvector / Milvus：知识检索索引，不承担业务状态事实
```

这意味着当前没有上线：

```text
PostgreSQL + MySQL 双主事实源
一次 HTTP 请求同时双写两库
跨库分布式事务
生产主从读写分离
```

这是一项主动风险控制，而不是功能缺失。金融主链路在双库迁移未通过完整契约前，宁可保持单主库强一致，也不能因为“看起来高可用”而引入双写部分成功、写入顺序、回滚困难、重复事件和人工修复成本。

#### 5. 双库契约：允许实现不同，但业务结果必须一致

Repository/UoW 不是要求 PostgreSQL 与 MySQL 的表结构、SQL 和索引一字不差；它们可以利用各自的方言和性能特性。真正需要一致的是：

```text
DTO 字段和分页语义
金额精度与舍入结果
状态机迁移与非法状态处理
权限/资源范围后的可见结果
幂等重放与并发冲突语义
事务成功/失败后的最终数据状态
NotFound、Validation、Conflict 等稳定业务错误码
```

例如重复幂等键在两个数据库底层都可能产生不同驱动异常，但 Service 层必须统一映射为 `IDEMPOTENCY_CONFLICT`；非法审核状态在两端都必须保持原状态，且不得生成伪造流水、成交或历史记录。

#### 6. 未来演进路线：先影子读，再决定分库查询，不贸然双写

推荐的分阶段路线是：

```text
阶段 1：PostgreSQL 主读写（当前）
阶段 2：Repository/UoW 协议 + MySQL 兼容适配与迁移安全验证
阶段 3：影子读（主请求仍读 PostgreSQL，同时用 MySQL 对比 DTO/数量/错误语义）
阶段 4：少数允许延迟的只读查询路由至副本或专用 Query Service
阶段 5：在明确复制延迟、回滚、监控和容量证据后，再考虑生产读写分离
```

这里的“影子读”只用于验证，不影响真实 HTTP 响应和资金决策。强一致读（账户余额、订单刚提交后的状态、风险限制）始终走主库；只有可容忍延迟、已通过契约验证的统计/列表类查询，才可能成为副本或专用 Query Service 候选。

#### 7. 与 Outbox 的关系

UoW 解决“当前单库内多个业务写入是否一起提交”；Outbox 解决“事务成功后如何可靠通知异步系统”。两者互补：

```text
Unit of Work / PostgreSQL Transaction
  → Order / Account / MoneyFlow / AuditLog
  → OutboxEvent(pending)
  → commit 或 rollback

Outbox Publisher
  → Redis Streams
  → 通知、知识导入、AML、未来 Agent 等最终一致消费者
```

因此不能用 Outbox 替代资金事务，也不能用单库事务替代跨进程异步可靠投递。前者保证局部强一致，后者保证整体最终一致。

#### 8. 答辩总结

> Repository 让业务 Service 不依赖具体数据库 SQL；Unit of Work 把账户、流水、订单、审计和 Outbox 等跨 Repository 操作放进同一事务，保证关键金融事实一起提交或一起回滚。当前 PostgreSQL 是唯一主事实库，MySQL 仅用于迁移和影子契约验证；项目刻意不做双主双写，先通过 DTO、金额、状态、幂等和错误码契约验证，再逐步演进影子读和读写分离，从而避免分库带来的部分一致性风险。

### 4.14D 读写分离、双库迁移与事件架构之间的总体关系

可以把未来演进图理解为：

```text
                    ┌───────────────────────────────┐
                    │        业务服务层              │
                    │  Trading / Risk / WorkOrder    │
                    └──────────────┬────────────────┘
                                   │
                    ┌──────────────▼────────────────┐
                    │ 仓储 / Unit of Work / 读写路由 │
                    │  强一致读、可延迟读、影子读    │
                    └───────┬───────────────┬───────┘
                            │               │
              ┌─────────────▼───┐       ┌───▼─────────────┐
              │ PostgreSQL 主库 │       │  候选库/只读库   │
              │ 当前事实主源    │       │ MySQL/Replica    │
              └─────────────┬───┘       └─────────────────┘
                            │
              ┌─────────────▼────────────────────────────┐
              │ OutboxEvent：事务内记录待投递业务事件      │
              └─────────────┬────────────────────────────┘
                            │
              ┌─────────────▼────────────────────────────┐
              │ Redis Stream / Worker / 外部通知/索引任务 │
              └──────────────────────────────────────────┘
```

其中：

```text
仓储/适配层：解决“数据从哪里读写”
Unit of Work：解决“多个写入如何原子提交”
Outbox：解决“数据库提交后如何可靠通知外部/异步系统”
AsyncTask：解决“后台工作如何可查询、可重试、可审计”
Redis/Worker：解决“任务如何异步调度和消费”
读写路由：解决“不同一致性需求如何使用主库/副本”
```

因此答案是：

> 是的，双库适配、读写后端配置、仓储抽象、Unit of Work、AsyncTask 和 Outbox 共同为未来主从读写分离、迁移验证、异步任务和外部事件投递打基础；但当前系统明确保持 PostgreSQL 为主业务读写事实源，以避免在金融核心流程中提前引入数据不一致风险。

### 4.15 可观测性：Trace ID、日志、审计三件套

| 类型 | 解决的问题 | 示例 |
|---|---|---|
| Trace ID | 一次 HTTP 请求跨模块定位 | `X-Trace-Id`、middleware 日志 |
| 应用日志 | 性能、状态码、异常堆栈 | `http_request_completed status=... duration_ms=...` |
| 审计日志 | 业务责任与历史追溯 | assign、handle、close、upload_attachment |

三者区别：

```text
Trace/应用日志：开发和运行排错
审计日志：业务事实、责任追溯、合规记录
```

例如附件下载 401，首先通过应用日志发现：

```text
GET /attachments/{id}/download → 401
```

再结合前端实现定位到裸链接未带 Token；而上传行为本身由审计日志记录。

### 4.16 测试工具链与验证金字塔

项目当前不是只依赖单元测试，而是更适合业务系统的组合验证：

```text
静态检查
  └─ python -m py_compile

服务内 ASGI HTTP 回归
  └─ httpx.AsyncClient + ASGITransport

数据库事实验证
  └─ 查询 WorkOrder / AuditLog / Notification / Attachment

前端生产构建
  └─ vite build

开发服务器/浏览器现场验证
  └─ Vite 实时源码、Network、Console、真实账号演示
```

#### 为什么使用 ASGITransport

部分本地 `uvicorn --reload` 场景中，外部端口进程可能未及时加载新子进程。ASGITransport 直接在应用生命周期中调用 API：

```text
不依赖外部端口
仍会执行 FastAPI 路由、认证、数据库、事务和种子初始化
适合稳定回归
```

它不是替代真实浏览器验收，而是避免“服务没 reload 导致测试误报”。

---

## 5. 核心功能模块设计

## 5.1 认证、账号与 RBAC 模块

### 设计目标

支持客户与员工登录，提供角色、权限、Token 刷新、演示账号种子和后台可见性控制。

### 核心能力

- 登录、注册、退出、刷新 Token；
- Access Token + Refresh Token；
- Refresh Token 轮换；
- 角色、权限、用户角色关联；
- 超级管理员标识；
- 演示账号和角色权限种子；
- 前端 401 后自动刷新或安全退出。

### 主要角色

```text
customer           客户
customer_service   客服
customer_manager   客户经理
advisor            理财顾问
product_manager    产品经理
risk_manager       风控人员
operations         运营人员
auditor            审计人员
super_admin        超级管理员
```

### 关键设计

权限码采用细粒度表达，例如：

```text
customer:read_all
customer:read_assigned
order:submit_staff
transfer:submit_staff
order:review
operation:read_staff
operation:audit_read
risk:alert_read
risk:alert_handle
risk:alert_action
risk:scan_execute
workorder:create
workorder:read
workorder:assign
workorder:handle
```

这替代了早期含义过宽的旧权限，如：

```text
customer:read
order:write
risk:read
risk:write
```

### 为什么要拆权限

旧权限的问题是“一把钥匙打开太多门”。例如 `order:write` 同时可能代表：

- 员工代客户提交业务；
- 运营审核业务；
- 读取后台待办；
- 执行交易动作。

这会导致最小权限无法实现。项目后续将其拆为代办、审核、读取等不同能力，降低角色扩权风险。

---

## 5.2 客户资料、画像、资产与持仓模块

### 设计目标

为交易适当性、投顾推荐、风险控制、客户分层和后台服务提供统一客户数据基础。

### 核心数据

```text
客户基础资料
客户主观风险测评
客户风险等级
客户投资期限与流动性偏好
资产快照
账户余额
持仓
历史资产走势
客户层级（普通、金、白金、钻石、私行等）
```

### 前端呈现

客户中心提供：

- 个人资料；
- 风险测评；
- 资产总览；
- 持仓与收益；
- 订单与业务办理；
- 我的工单；
- 企业认证、投顾服务等入口。

后台提供：

- 客户管理；
- 客户详情；
- 客户资产、风险状态、授权关系；
- 经营与风险看板。

### 设计原则

客户本人接口和工作人员客户管理接口不共用同一权限语义：

```text
客户本人：基于当前登录身份访问本人数据
工作人员：使用 customer:read_all 或 customer:read_assigned
投顾：还要校验 active advisor authorization
```

这是为了解决“客户被意外授予同名权限后访问工作人员接口”的边界风险。

---

## 5.3 产品、适当性与投顾推荐模块

### 设计目标

将产品收益、风险等级、目标客户类型、客户画像和授权关系结合起来，避免简单展示“所有产品”。

### 主要能力

- 产品资料、上下架、产品风险等级；
- 产品日收益率；
- 适当性规则与客户风险匹配；
- 客户持仓估值与资产快照；
- 理财顾问查看已授权客户；
- 投顾推荐与草稿；
- 客户确认与授权边界。

### 适当性原则

核心判断可抽象为：

```text
客户风险等级 >= 产品风险等级
并且满足客户类型、期限、授权、产品范围等约束
```

项目没有把适当性判断只放在 UI 提示，而是用于后端交易/推荐流程，避免绕过页面直接调接口。

---

## 5.4 交易、订单、赎回与转账模块

### 设计目标

实现客户本人业务、员工代办、审核执行、余额与持仓变化、幂等与审计的最小可信闭环。

### 主要对象

```text
账户 Account
订单 Order
成交 Trade
订单状态历史 OrderStatusHistory
资金流水 MoneyFlow
业务操作 BusinessOperation
```

### 主要流程

```text
客户发起 / 工作人员代办
  → 风险与适当性校验
  → 待确认 / 待审核
  → 审核通过或拒绝
  → 资金、持仓、流水变化
  → 审计记录
```

### 关键设计点

#### 1. 幂等控制

交易与转账不能因为网络重试重复扣款或重复成交，因此通过 `idempotency_key` 控制重复请求。

#### 2. 订单状态历史

只保留订单当前状态不够，必须记录状态从哪里变到哪里、由谁操作、何时操作，便于审计和问题追溯。

#### 3. 员工代办与审核职责分离

工作人员代办并不等于有审核权限。项目将：

```text
代办能力：order:submit_staff / transfer:submit_staff
审核能力：order:review
后台操作查看：operation:read_staff
业务审计查看：operation:audit_read
```

拆开，避免同一人员同时无限制发起与审核。

---

## 5.5 风险预警、AML 与交易限制模块

### 设计目标

将“风险发现”与“交易阻断/限制”关联，做到不仅能看预警，还能影响实际交易。

### 主要能力

- 风险规则；
- AML 扫描；
- 风险预警；
- 风险调查；
- 风险处置；
- 交易限制；
- AML 批次执行记录；
- 风险状态面向客户和后台展示。

### 风险规则示例

项目已经围绕 AML 窗口规则构建了实时与日批两类路径，例如：

```text
RW-001：大额现金交易
RW-002：频繁小额转账
RW-003 至 RW-006：窗口证据、去重与幂等规则扩展
```

### 风险动作

典型动作包括：

```text
HOLD_ORDER       暂停订单
REJECT_ORDER     拒绝订单
限制客户交易
解除或关闭风险预警
```

### 核心经验

风险模块最容易出现的问题是“页面显示风险，但交易接口不受影响”。项目后续将风险门禁接入订单、赎回、转账等实际业务入口，并验证：

```text
风险阻断时：
订单状态不应错误推进
余额不应错误变化
持仓不应错误变化
流水不应错误生成
重复动作不应造成二次影响
```

---

## 5.6 知识库、检索与智能问答模块

### 设计目标

对公司资料、业务产品、金融政策、用户研判规则等文档建立可检索的知识基础，为智能问答、制度查询、业务辅助提供能力。

### 内容来源

项目根目录包含结构化业务资料，例如：

```text
公司信息
公司业务
金融政策
用户测试数据
用户研判规则
```

并整理为：

```text
BUSINESS_KNOWLEDGE_COMPANY.md
BUSINESS_KNOWLEDGE_PRODUCTS.md
BUSINESS_KNOWLEDGE_POLICIES.md
BUSINESS_KNOWLEDGE_RISK.md
```

### 架构思路

```text
原始文档
  → 文档切分与版本管理
  → 向量嵌入
  → Milvus / pgvector 检索
  → 语义召回
  → 问答或业务辅助输出
```

### 设计边界

知识库用于“辅助理解和检索”，不替代金融制度原文。涉及金额阈值、监管要求、期限、牌照、保存年限等，仍应回到有效原文或当前版本规则。

---

## 5.7 异步任务、Outbox 与基础设施模块

### 设计目标

为日批 AML 扫描、估值、知识处理、通知扩展等任务提供异步化基础，而不把长耗时操作直接阻塞在 HTTP 请求内。

### 主要对象

```text
AsyncTask
OutboxEvent
Redis Stream / Redis 队列能力
任务状态、重试次数、失败原因
```

### 设计意义

- HTTP 接口快速返回；
- 后台任务可重试；
- 外部通知、邮件、短信未来可通过 Outbox 异步投递；
- 批次 AML 扫描可以有明确执行记录与失败审计；
- 降低直接在请求线程中执行长任务的风险。

---

## 5.8 工单、通知、客户补充与附件模块

该模块是近期迭代最完整、最适合答辩演示的业务闭环，详见第 8 节。

能力包括：

```text
创建、读取、分派、处理、解决、关闭
数据范围控制
状态机
审计日志
站内通知
客户补充材料
补充记录回流
附件上传、列表、受认证下载
待办筛选、分页、优先级排序
```

---

## 6. 权限、数据范围与审计设计

## 6.1 三层访问控制模型

项目没有只依赖“角色名”，而是建立三层控制：

```text
1. 身份认证
   当前请求是否有有效 JWT？

2. 功能权限
   当前角色是否拥有指定 permission code？

3. 资源数据范围
   该客户、订单、工单、附件是否属于当前用户可访问范围？
```

### 示例：工单处理

```text
必须具有 workorder:handle
且非超级管理员时：assigned_to == current_user.id
否则返回 403
```

### 示例：客户补充

```text
必须是 customer 角色
且 work_order.customer_id == current_user.id
且 work_order.status == pending_customer
否则拒绝
```

### 示例：附件下载

```text
必须具有 workorder:read
客户只能下载自己的工单附件
工作人员必须先能读取该工单
下载请求必须携带 Bearer Token
```

## 6.2 审计日志设计

关键事件统一写入 `audit_logs`：

```text
create
assign
handle
close
customer_supplement
upload_attachment
风险动作
订单审核
业务操作
```

审计日志至少记录：

```text
操作者 actor_user_id
动作 action
资源类型 resource_type
资源 ID resource_id
详情 detail
时间 created_at
```

设计目的：

- 事后追责；
- 业务状态排查；
- 答辩时说明“系统不是只改前端”；
- 为审计人员只读查询提供依据。

---

## 7. 风险、AML 与交易控制设计

### 7.1 为什么风险控制必须在后端

如果风险控制只显示在 UI，会被直接 API 请求绕过。因此风险判断必须位于：

```text
交易执行前
订单审核前
赎回/转账执行前
风险动作执行时
```

### 7.2 规则、预警、限制、动作四层模型

```text
规则 Rule
  → 命中证据 Evidence
  → 风险预警 Alert
  → 调查/处置 Investigation / Disposition
  → 交易限制或订单动作 Restriction / HOLD / REJECT
```

这种分层使系统可以区分：

- 只是发现异常；
- 正在调查；
- 是否已联系客户；
- 是否限制交易；
- 是否关闭预警；
- 是否已经报告或形成草稿。

### 7.3 AML 是什么：为什么风控不能只看“单笔大额”

AML（Anti-Money Laundering，反洗钱）不是简单地给“大额交易”打红色标签。它关注的是可疑资金行为模式：资金是否在特定时间窗口内异常集中、是否通过多笔小额拆分规避阈值、是否出现异常快进快出、对手方集中或与客户收入能力明显不匹配等。

因此系统将 AML 设计为“**规则识别 → 证据固化 → 预警调查 → 处置/限制 → 交易门禁 → 审计复盘**”的完整闭环，而不是独立的风险看板。

```text
交易/资金流水发生
  → 实时规则评估（可即时提示或拦截）
  → RiskAlert + Evidence
  → 风控人员接单、调查、记录客户沟通和评估结论
  → 选择处置：继续监控 / 生成报告草稿 / 限制交易 / HOLD / REJECT
  → 风险状态同步到交易入口和客户/后台页面
  → 日批窗口扫描补漏、批次记录、失败隔离与审计
```

### 7.4 规则、证据、预警、调查、限制、动作六层模型

```text
RiskRule（规则定义）
  → Evidence（命中证据：窗口、阈值、统计值、触发流水）
  → RiskAlert（预警：级别、状态、去重键、关联客户/订单）
  → RiskInvestigation（调查：评估结论、依据、客户沟通）
  → Restriction / ReportDraft（限制交易或报告草稿）
  → RiskAction（HOLD / REJECT / RELEASE 等实际动作）
```

之所以分层，是为了避免把“发现异常”和“已经确认违法”混为一谈：规则命中只意味着需要关注；预警需要调查；调查后才能根据证据决定是误报、继续监控、限制交易还是形成报告材料。系统不能因为一次规则命中就直接结案，也不能只记录一个 `resolved` 状态而没有评估依据和客户沟通。

风险处理接口因此要求记录评估结论、评估说明、客户沟通和实际处置。确认风险时不能选择 `NO_ACTION`；评估后通过也必须留下完整依据。这些约束让“风险已处理”成为可审计业务事实，而不是页面上的按钮点击。

### 7.4A 风险预警到解决的处置工单闭环：谁做、做什么、何时能关闭

风险预警不能“命中后直接点解决”。在系统中，`RiskAlert` 承担风险处置工单的主记录：它记录规则命中和证据，风控人员围绕它完成接单、调查、客户沟通、处置、限制或解除限制，并以状态机和审计保证每一步可追溯。

```text
规则命中 / AML 日批命中
  → 创建 RiskAlert（open）并固化 Evidence、关联客户/订单/流水
  → 风控人员接单（acknowledged / investigating）
  → 调查：查看窗口流水、订单、账户、画像、历史风险和关联预警
  → 记录评估结论、评估依据、客户沟通
  → 选择处置动作
       ├─ 误报 / 评估后通过：保留依据，进入 monitoring 或关闭
       ├─ 信息不足：保持 investigating，补充材料后继续
       ├─ 确认风险：报告草稿、限制交易、HOLD / REJECT 等
       └─ 已限制后复核通过：解除限制，再完成结案
  → 写 RiskInvestigation / RiskAction / AuditLog / Notification（适用时）
  → 客户与后台按数据范围看到最新风险状态和结果
```

#### 第一步：预警生成与“风险工单”建立

预警由实时交易评估或 AML 批次扫描创建。创建时必须落库而不是只在页面提示，至少包括：

```text
rule_code / 风险等级
customer_id、关联订单或资金流水
Evidence：窗口起止、阈值、实际统计值、触发流水/对手方等
scan_type：real_time 或 customer_window
idempotency_key：防止同一证据反复生成预警
初始状态：open
```

生成预警并不等于冻结资金，也不等于客户已被认定有问题。它只表示“系统发现值得调查的证据”，后续必须由有权限的风控人员进行研判。

#### 第二步：接单、调查与证据补全

拥有风险处理权限的人员先接单，预警进入 `acknowledged` 或 `investigating`。此时系统要把调查对象从一个 UUID 变成可阅读的业务上下文：客户姓名、订单号、产品名、规则中文说明、命中证据、建议处置、历史预警和当前风险状态。

调查至少应回答：

```text
命中的是哪条规则、哪个时间窗口、哪些流水？
这些流水是否真实、是否重复、是否属于同一客户/对手方？
客户画像、收入、持仓和历史行为能否合理解释？
是否需要联系客户补充用途、资金来源或证明材料？
是否需要立即阻断尚未完成的订单、赎回或转账？
```

如果需要客户材料，可创建/关联普通业务工单，进入“待客户补充 → 客户提交文本或附件 → 回到处理中”的协同链路；但风险预警本身仍保持 `investigating`，不能因为已发送补充要求就提前结案。

#### 第二步 A：要求客户补充资料时，风险预警如何与通用工单协同

本次风控后台更新解决了一个关键断点：过去选择 `REQUEST_MATERIALS` 只是在 `RiskAlert` 上记录了一条风险动作，客户侧没有真正可见、可提交材料的任务，等同于“要求补充资料”停留在后台备注里。

现在该动作会复用已经成熟的通用工单闭环，并在 `RiskAlert` 与 `WorkOrder` 之间建立一对一关联：

```text
风控人员在风险预警评估弹窗选择 REQUEST_MATERIALS
  → 填写 assessment_note（为什么当前无法完成判断）
  → 填写 disposition_note（需要客户提供的具体资料清单）
  → 后端校验：assessment_result 必须为 insufficient_information
  → 后端创建或复用一条 category = risk_investigation 的 WorkOrder
  → WorkOrder.status = pending_customer
  → WorkOrder.assigned_to = 当前风控处理人
  → WorkOrder.risk_alert_id = 当前 RiskAlert（唯一关联）
  → 创建客户站内通知，提醒到“我的工单”提交说明、JPG/PNG/PDF
  → RiskAlert.status = pending_customer
  → 原订单/赎回/转账仍保持风险门禁，不允许绕过
```

为避免“点两次要求补材料，就创建两张同样工单”，`work_order.risk_alert_id` 使用唯一关联。后端会优先复用同一预警已经存在的资料补充工单，而不是重复创建；这使重复点击、网络重放或前端重试不会让客户收到多份相互矛盾的资料要求。

客户处理工单时走普通工单的安全范围：仅能读取本人风险资料工单，在 `pending_customer` 状态下提交文字补充和受限类型附件。提交成功后：

```text
WorkOrder：pending_customer → in_progress
WorkOrderUpdate：写入客户补充内容
WorkOrderAttachment：保存受控凭证元数据
Notification：通知原 assigned_to 风控人员
RiskAlert：保持 pending_customer，等待风控人员复核
交易门禁：继续生效
```

这里特意不让风险预警在客户一提交材料后自动“结案”或自动解除限制：客户提供材料只是新证据，不是风控结论。当前风险预警保持 `pending_customer`，由原处理人回到风险预警工作台查看关联工单、补充内容和附件后，手动转为 `investigating` 并完成复核。下一步可进一步实现“客户补充后自动将 RiskAlert 从 `pending_customer` 回推到 `investigating`”，但仍必须由人工完成最终评估与风险动作。

前端也同步优化了失败可见性：当表单缺少资料清单、评估说明，或“等待客户补充”却选择了非 `insufficient_information` 的结论时，后端返回字段级 422 错误，页面提示具体原因，不再表现为“点击处理按钮没有反应”。
#### 第三步：风险评估处理表单与强制字段

为避免后台人员点击“解决”却没有形成任何研判事实，风险处理要求填写结构化信息：

```text
assessment_result：确认风险 / 评估后通过 / 误报 / 信息不足
assessment_note：评估依据与说明
customer_communication：是否联系、沟通内容、补充材料结论
resolution / disposition：实际处置代码和说明
```

强制规则如下：

| 评估结论 | 状态与动作要求 | 是否可以直接关闭 |
|---|---|---|
| 信息不足 | 保持 investigating，继续补证 | 否 |
| 误报 | 记录误报原因，可转 monitoring/关闭 | 可以，但必须留依据 |
| 评估后通过 | 必须有完整评估依据和客户沟通记录 | 可以，通常转 monitoring 或关闭 |
| 确认风险 | 必须选择非 `NO_ACTION` 的实际处置 | 否，必须先完成调查/报告/限制等路径 |

这解决了早期“旧页面只发送 `status=resolved`，后端无法知道为什么解决、是否联系客户、采取了什么处置”的问题。前端因此改为独立风险评估弹窗，风控人员先填写结论和证据，再提交状态变化；页面按钮不是业务事实，后端校验和审计才是最终约束。

#### 第三步 A：风控后台处置界面重构——区分“阶段、结论、动作”，避免表单冲突

风险后台曾将“处理状态、评估结论、评估说明、是否已沟通、沟通记录、实际处置、处置说明”全部平铺在一个弹窗中。字段本身都有审计价值，但并非每次处置都需要填写；例如要求客户补件时，风控人员不应同时选择“已结案/评估后通过”，也不需要填写“已与客户沟通”。平铺表单容易让操作人员选择相互矛盾的组合，也使客户补件是否真正送达客户不够直观。

前端调整为“**先选择本次处置，再按场景显示必要字段**”。其中：

```text
处理状态：调查当前走到哪一步（investigating / pending_customer / resolved）
评估结论：风控人员对风险的判断（信息不足 / 误报 / 评估后通过 / 确认风险）
实际处置：系统将执行的业务动作（补件、挂起、拒绝、限制、报告、升级）
```

三者的关系不是重复选择：状态描述流程阶段，结论描述判断，动作描述系统副作用。界面以“实际处置”作为入口，并自动约束前两项，减少无效输入。

| 本次处置 | 页面仅要求的关键输入 | 自动/推荐状态 | 对客户和交易的结果 |
|---|---|---|---|
| 继续调查 / 暂不执行动作 | 评估说明；结论可暂不下 | `investigating` | 不改变账户或订单，仅保存调查进展 |
| 要求客户补充资料 | 信息不足原因、具体资料清单 | `pending_customer` + `insufficient_information` | 创建客户可见的 `risk_investigation` 工单，发送站内通知，交易继续被拦截 |
| 挂起关联订单 | 评估说明、处置说明 | 通常保持 `investigating` | 暂停该笔订单，等待复核 |
| 拒绝关联订单 | 评估说明、处置说明 | 通常保持 `investigating` | 具体订单进入拒绝路径 |
| 限制客户交易 | 评估说明、处置说明 | 通常保持 `investigating` | 后续资金类操作由后端门禁拒绝；解除必须走专门权限动作 |
| 生成报告草稿 / 升级处理 / 创建内部调查 | 评估说明、处置说明 | 通常保持 `investigating` | 形成后续内部处置材料，不自动解除交易门禁 |
| 评估后通过并结案 | 评估依据、已与客户沟通、沟通记录 | `resolved` + `cleared_after_review` | 允许关闭预警，但保留全部审计证据 |

“确认风险”不能直接选择结案：必须继续调查，或至少完成限制交易、挂起/拒绝订单、报告草稿等真实动作。这样避免只填一个 `confirmed_risk` 就使预警消失、交易又恢复可执行的逻辑漏洞。

#### 客户是否真的能看到补件任务

当选择 `REQUEST_MATERIALS` 且填写必填的“信息不足原因”和“资料清单”后，后端会创建/复用关联工单，并向客户写入站内通知。客户登录后应在：

```text
客户中心 → 我的工单
```

看到类别为“风控调查”、状态为“待客户补充”的工单；工单正文显示风控人员的资料要求，客户可提交文字说明和 JPG/PNG/PDF 附件。前端成功提示也明确为“已创建客户可见的风控补件工单，并已发送站内通知”，而不是仅提示“风险预警已保存”。

客户补件后，工单变为 `in_progress` 并通知原风控处理人；风险预警本身仍停留在 `pending_customer`，交易门禁仍然有效。风控人员必须基于新材料回到预警工作台继续调查，并主动选择下一步处置或结案。这样避免“客户上传一个文件就自动通过风控”的错误自动化。

#### 第四步：什么时候限制交易，限制后系统如何拦截

当评估为确认风险，或规则/处置策略要求立即保护资金安全时，风控人员执行风险动作，例如：

```text
RESTRICT_CUSTOMER / 限制客户交易
HOLD_ORDER / 暂停待处理订单
REJECT_ORDER / 拒绝具体订单
CREATE_REPORT_DRAFT / 生成报告草稿
```

“限制交易”不是给客户打一个标签，而是写入客户当前有效风险限制状态，并进入每个资金操作的后端门禁：

```text
客户或工作人员提交订单、赎回、转账
  → Trading/Operation Service 查询 active risk restriction
  → restricted：拒绝提交或阻止状态推进
  → HOLD：订单保持暂停，不能结算
  → REJECT：订单进入拒绝终态
  → 不写余额、持仓和资金流水副作用
```

客户侧只读看到风险状态和限制原因；工作人员/风控侧可以看到预警、调查和可操作处置入口。即使前端被篡改、直接重放 API，也必须由后端门禁拒绝，保证限制不是“隐藏按钮式风控”。

#### 第五步：如何解除限制交易

解除限制必须与“关闭预警”分开：预警关闭只代表某一调查记录完成；解除限制意味着重新允许客户发生资金类业务，风险更高，不能由客户自己操作，也不能因为填写了“已解决”就自动解除。

推荐并已采用的受控流程：

```text
风控人员完成调查
  → 确认限制原因已消除或处置已完成
  → 填写解除依据、复核说明、关联预警
  → 在后台客户详情执行 RELEASE_RESTRICTION
  → 后端校验 risk:alert_action 等权限与客户资源范围
  → 更新 active restriction / customer risk status
  → 写 RiskAction（RELEASE_RESTRICTION）和 AuditLog
  → 客户状态从 restricted 回到 monitoring 或 normal
  → 后续新交易重新通过正常的适当性、余额、AML 和授权校验
```

解除后并不是“删除所有风险历史”：原 `RiskAlert`、Evidence、调查记录、限制动作和解除动作都必须保留，审计人员能够追溯“何时因何限制、谁批准解除、依据是什么”。客户也不应具备解除按钮，只能查看当前状态；这避免客户通过自助页面绕开调查和权限审批。

在当前系统演示中，风险状态可经历：

```text
normal → monitoring → restricted → monitoring → normal
```

其中 `monitoring` 表示风险事件已被关注但不一定禁止业务；`restricted` 才是交易门禁生效状态。解除限制后通常先回到 `monitoring`，是否最终回归 `normal` 取决于调查结论、后续观察和规则要求，而不应一概自动清零。

#### 第六步：结案、通知与审计复盘

只有当调查结论、客户沟通、实际处置和限制状态都满足规则要求时，预警才允许结束。结案时应保留：

```text
最终状态与评估结论
命中证据及窗口数据
调查过程和客户沟通
采取过的限制/暂停/拒绝/报告草稿动作
解除限制（如有）的理由、操作者和时间
关联工单、订单、资金流水、批次 ID
完整 AuditLog
```

通知采用按需原则：需要客户补材料、订单被暂停/拒绝、限制状态变化时可以发送站内通知；不能在调查初期把内部 AML 研判细节无差别展示给客户。审计人员具有只读查看边界，不能接单、修改调查或解除限制。

#### 可用于答辩的完整演示脚本

```text
1. 构造一笔/一组资金流水，使 RW-001 或 RW-002 命中；
2. 展示 RiskAlert：规则、客户、窗口、阈值和 Evidence；
3. 风控人员接单，进入 investigating；
4. 填写评估依据、客户沟通和处置方案；
5. 选择 RESTRICT_CUSTOMER，展示客户状态变为 restricted；
6. 用客户或工作人员再次尝试订单/赎回/转账，后端拒绝且无余额/持仓/流水副作用；
7. 补充材料或完成复核后，在后台客户详情执行 RELEASE_RESTRICTION；
8. 展示状态变为 monitoring/normal、审计记录新增解除动作；
9. 再次发起业务，仍经过正常适当性、授权、余额、AML 校验后才允许进入流程；
10. 展示预警、调查、限制、解除和交易请求的完整审计链路。
```
### 7.5 规则如何落地：实时路径与日批窗口路径

- **实时路径**：当前订单或资金流水到达时，按规则判断是否立即命中；适合需要及时限制、暂停订单或产生即时预警的场景。
- **日批路径**：按 UTC 窗口扫描客户全量资金流水；适合频繁小额、持续窗口、对手方集中等无法从单笔交易判断的模式。

当前已将 RW-001 至 RW-006 分批落地为以资金流水为证据的规则链路，并以边界测试验证核心阈值。例如：

```text
RW-001：单一客户单日累计现金交易达到阈值
RW-002：7 个自然日内小额转账笔数与累计金额共同达到阈值
RW-003 ～ RW-005：围绕历史入/出账、当前出账、时间窗口和对手方集中度的模式识别
RW-006：结合客户申报年收入的异常资金规模判断
```

规则开发中最重要的经验是：不能把“订单金额”误当作“现金交易事实”，也不能让不同规则的测试流水混在同一事务里互相触发。系统先补充 `MoneyFlow`、方向、对手方、窗口和客户年收入等必要字段，再将“当前触发流水”和“历史聚合流水”分离计算；每条规则均应有命中、不命中、阈值边界、同窗口重放和数据隔离测试。

### 7.6 AML 幂等与批次设计：为什么需要窗口键、去重键和 AmlBatchRun

AML 扫描会重复执行：人工补扫、定时任务重跑、Worker 重试、历史窗口回放都可能发生。若没有幂等设计，同一可疑行为会被反复创建预警、重复通知、重复限制客户。

因此系统分别定义：

```text
实时去重键：real_time_order:{order_id}:{rule_code}
日批预警去重键：customer + rule_code + UTC window_start + window_end
批次键：扫描范围 + 扫描天数 + UTC 窗口
```

`AmlBatchRun` 记录一次日批的批次状态、窗口、范围、统计和失败摘要。首次执行完成后，同一窗口重复运行复用已有批次；批次内按客户使用嵌套事务隔离，某个客户的数据异常不会让全批客户无法扫描；完成后写入 `daily_aml_scan` 审计。预警关联时不能只按数据库 `created_at` 查询，因为历史回放时预警创建时间可能晚于其业务窗口，必须以 Evidence 中的 `window_start/window_end` 作为关联依据。

### 7.7 风险动作怎样真正影响交易

风险模块的价值不在于“生成了 RiskAlert”，而在于风险状态会进入交易主链路：

```text
订单/赎回/转账提交前
  → 查询客户风险限制与活动风险处置
  → 必要时 HOLD_ORDER、REJECT_ORDER 或限制客户交易
  → 不允许状态继续推进
  → 不允许余额、持仓、资金流水产生错误副作用
```

解除限制也不能做成客户侧自助按钮。客户看板只读展示 `normal / monitoring / restricted` 等风险状态及原因；具有风险处置权限的后台人员在客户详情中执行 `RELEASE_RESTRICTION`，并写入审计。这样既能让客户理解当前限制，又避免客户自行解除风控门禁。

### 7.8 AML 当前边界与答辩表述

本项目已建立规则、实时/日批扫描、窗口证据、预警去重、调查处置、限制动作、批次记录、权限和审计的工程闭环。它是基于模拟数据和项目规则文档的教学/演示实现，**不等同于生产级金融机构 AML 系统，也不应宣称已覆盖全部监管规则或完成真实监管报送。**

后续增强包括：更完整的规则库与版本治理、迟到流水补扫/批次重算、批次观察页与错误详情、报告报送流程、多级审批、外部名单/制裁筛查，以及更严格的生产数据治理。

---

---

---

## 7.9 数据一致性保障：从单笔资金事务到异步最终收敛

本系统的数据一致性不是依赖某一个数据库锁、某一条前端校验或“尽量不出错”的约定，而是按照数据的重要性建立多层防线。核心结论是：

```text
资金、持仓、订单、授权、风险限制等关键业务事实：局部强一致
通知、知识导入、AML 批次、Worker、未来 Agent 等跨进程协作：整体最终一致
所有关键写入：状态机 + 权限/范围 + 幂等 + 事务 + 审计 + 回归测试共同保证
```

### 7.9.1 第一道防线：PostgreSQL 是唯一核心业务事实源

当前生产式业务主链路以 PostgreSQL 为唯一读写事实源。账户余额、冻结余额、订单、成交、持仓、资金流水、业务操作、风险预警、风险限制、工单和审计都不以 Redis、Milvus、MySQL 影子库或前端状态为准。

这样避免了金融核心流程中“多个库各自写成功一部分”的双主一致性风险：

```text
PostgreSQL：当前主事实库
MySQL：迁移/兼容/验证边界，不承担 HTTP 主交易写入
Redis：队列、缓存、Stream 协调，不承担资金事实
Milvus/pgvector：知识检索索引，不承担业务状态裁决
```

### 7.9.2 第二道防线：将不可拆分的业务事实放入同一事务

一个金融动作只有在所有相关事实同时写入后才算成功。系统通过 Service / Unit of Work 将相关仓储写入放在同一 PostgreSQL 事务内；任一步失败则提交失败，避免页面成功而事实只写一半。

| 业务动作 | 同一事务内必须同步完成的事实 |
|---|---|
| 客户订单确认/审核 | 订单状态、状态历史、冻结/结算资金、成交、持仓、审计（按业务阶段） |
| 赎回审核通过 | `BusinessOperation` 状态、持仓减少、账户余额增加、`redeem/in` 流水、审计 |
| 转账审核通过 | `BusinessOperation` 状态、源账户扣减、目标账户增加、`transfer/out` 与 `transfer/in` 两条流水、审计 |
| 风险限制 | 风险动作、账户 `risk_restriction_status`、预警限制状态、审计 |
| 风险客户补件 | `RiskAlert` 状态、唯一关联 `WorkOrder`、处理人、审计、站内通知数据 |
| 工单分派/处理 | 工单状态、`assigned_to`、处理记录、审计、站内通知数据 |

例如转账通过绝不是“先扣源账户、再异步给目标账户加钱”，而是在同一事务中完成双边余额和双边流水。若事务失败，两边都不提交；若事务成功，两边同时成为可审计事实。

### 7.9.3 第三道防线：状态机防止非法重放和跳跃

所有关键对象都不允许任意字符串更新状态。订单、赎回/转账业务操作、风险预警和工单均通过允许状态迁移控制动作时机：

```text
订单：pending_confirmation → pending_review → executed / rejected / cancelled
赎回/转账：pending_review → settled / rejected
风险预警：open → acknowledged → investigating / pending_customer / report_pending → resolved / dismissed
工单：open → assigned → in_progress → pending_customer → resolved → closed
```

状态机带来三类保护：

1. **重复审核保护**：已 `settled` / `rejected` 的业务操作不能再次结算；
2. **顺序保护**：客户补件只能在 `pending_customer` 时发生，风险限制解除必须先有合规复核结论；
3. **副作用保护**：状态不允许时，余额、持仓、流水、附件或审计不会产生伪造副作用。

### 7.9.4 第四道防线：幂等键 + 数据库唯一约束 + 冲突语义

网络超时、浏览器重复点击、Worker 重试和并发请求都会让同一请求被发送多次。系统将幂等视为业务正确性，而不只是前端体验。

```text
客户端：写操作生成并复用 idempotency_key
应用层：优先检查已有同键业务结果
数据库层：唯一约束兜底并发竞态
Service：映射为稳定 IDEMPOTENCY_CONFLICT / 状态冲突语义
前端：冲突后回读资源，不自动再次创建新业务
```

典型验证包括：同一订单/赎回/转账幂等键重复提交不产生第二笔业务；并发请求只有一个成功创建，其他请求返回冲突；重复审核不会重复扣款、重复入账或重复生成流水。

### 7.9.5 第五道防线：行锁与并发写入边界

对需要修改当前状态或资金的操作，Service/Repository 会在事务中读取目标资源并加锁，例如 `get_for_update`。Outbox Publisher 扫描待投递事件时使用：

```text
SELECT ... FOR UPDATE SKIP LOCKED
```

这使多个 Publisher/Worker 并行时不会同时抢占同一条 `pending` OutboxEvent；资金、订单、风险预警等状态更新也以事务内最新行状态为准，而不是相信请求到达时的旧页面数据。

行锁并非替代状态机和幂等，而是三者配合：

```text
行锁：缩小并发修改窗口
状态机：拒绝逻辑上已结束/不允许的动作
幂等键：识别同一业务请求的重放
唯一约束：兜底并发竞态
```

### 7.9.6 第六道防线：风险门禁在提交和审核阶段重复校验

风险结果可能在提交和审核之间变化，例如 AML 日批、实时规则或人工限制在订单等待审核期间出现。因此系统不只在前端提示或首次提交时检查，而是在实际执行前再次检查：

```text
订单审核前 → 预审核风险检测 + 未结案预警 / 限制状态检查
赎回/转账提交与审核前 → 账户限制、风险预警、授权、余额/持仓检查
风险阻断 → 409 RISK_CONTROL_BLOCKED
             不推进状态，不产生余额/持仓/流水副作用
```

这是一种“**检查—执行尽量靠近**”的控制策略，用于缩小风险状态变化导致的时间窗口。

### 7.9.7 第七道防线：Outbox 处理局部同步与整体异步之间的不一致

通知、Embedding、知识导入、AML Worker、未来 Agent 分析不应加入资金事务同步等待，但业务提交成功后又不能丢失后续事件。系统在同一事务中同时写入业务事实和 `OutboxEvent(pending)`：

```text
局部强一致事务
  → Order / BusinessOperation / RiskAlert / WorkOrder / AuditLog
  → OutboxEvent(pending)

提交成功后
  → Outbox Publisher 可靠投递 Redis Streams
  → Worker 消费、重试、ACK
  → 外围模块最终一致收敛
```

当前 `workers/outbox_worker.py` 对 pending 事件加锁、记录尝试次数、投递成功后标记 `published`，达到最大尝试次数后标记 `dead`。知识文档导入已实际使用该链路；AML 批次、通知和未来 Agent 都复用这一边界。

### 7.9.8 第八道防线：版本切换与派生数据避免半成品覆盖

并非所有一致性都是资金问题。知识库和资产趋势也有自己的“版本/快照一致性”策略：

```text
知识库：旧 active 版本继续服务 → 新版本 processing → 切分/Embedding 完整成功 → 原子激活新版本 → 旧版本 superseded
资产：历史 daily_valuation/交易快照不可变 → 同日 derived 快照可刷新 → 摘要与趋势末点使用同一轮估值
```

前者避免文档导入失败时搜索结果突然为空或新旧片段混用；后者避免页面总资产已刷新、资产曲线仍显示旧值的展示不一致。

### 7.9.9 第九道防线：审计、对账和测试是最终可证明性

一致性不能只靠代码“看起来正确”，还必须可证明、可追溯。系统对提交、审核、结算、风险动作、工单补件、附件、限制/解除等关键动作写 `AuditLog`；历史投顾订单资金异常曾通过订单、状态历史、账户、流水的只读对账发现，再按订单级幂等条件补齐缺失流水，避免盲目重复扣款。

回归测试不仅断言 HTTP 200，还验证：

```text
订单/业务状态是否正确
余额和冻结余额是否精确变化
持仓是否精确变化
双方流水是否存在且 customer_id 归属正确
重复请求/重复审核是否无第二次副作用
风险阻断时是否不产生资金和持仓变化
Outbox/AML 批次是否幂等复用
审计日志是否落库
```

### 7.9.10 当前能力边界

当前强项是 PostgreSQL 主库内的局部强一致、状态机、资金/持仓/流水同步更新、风险二次门禁、幂等和 Outbox 可靠异步基础。仍需持续增强的部分包括：

```text
事件消费者 receipt 表与 event_id + consumer_name 去重
Dead-letter 的可视化、人工重放和处理台
更完整的 Worker 失败重试/告警策略
跨外部系统的补偿与对账流程
AgentRun / AgentResult 的事件消费幂等和可审计持久化
```

答辩时不应宣称“所有分布式场景都 exactly once”。更准确的表述是：**核心金融事实在单库事务中保证强一致；异步协作通过 Outbox、幂等、重试和审计实现可追踪的最终一致；系统明确避免让未受控的 Agent、Redis 或双库写入成为资金事实源。**

---

## 8. RAG 与受控 Agent 架构：已实现能力与演进路线

项目中 RAG 后端不是“概念预留”，而是已经落地为知识文档、版本、分块、嵌入、异步导入、检索和问答所依赖的基础链路；但多 Agent 自动协作、GraphRAG 与可执行 Agent 仍按计划书和决策记录分阶段演进，不能把路线图表述为全部已上线能力。

### 8.1 当前已实现的 RAG 后端闭环

```text
业务资料 / 政策文档
  → 文档登记与版本管理
  → 文本切分为 chunk
  → Qwen Embedding 生成向量
  → PostgreSQL/pgvector 检索（Milvus 作为可替换/扩展路线）
  → 语义召回与结果返回
  → 知识问答、制度检索、业务解释辅助
```

文档不是直接“上传后就能回答”。系统为文档及其版本保留生命周期状态，导入请求通过 Outbox/Redis Stream 交给 Knowledge Worker 处理。Worker 负责切分、嵌入、写入向量和更新状态；应用日志记录 HTTP、Qwen、Redis、Outbox、Worker 等组件事件，Trace ID 用于串联一次请求和异步处理链路。

这套拆分的原因是 embedding 调用和批量入库具有外部依赖与耗时特征：若在 HTTP 请求里同步执行，接口会被模型调用阻塞，失败也难以重试；通过任务/事件化处理，可以记录任务状态、失败原因和重试次数。Embedding 服务发生偶发连接失败时，Provider 层采用有限重试并记录 `embedding_retry`，而不是让请求无边界阻塞。

### 8.1A 文档增量更新：为什么 RAG 最关键的不是“首次导入”，而是“变更后不丢旧知识、不重复花费”

知识库真正进入长期运行后，最常见的操作不是第一次上传文件，而是制度、产品说明、公司资料持续修订。若每次文件有一点变化都“删除所有旧向量 → 全文重新切块 → 全量重新 Embedding”，会带来三个问题：

```text
1. 成本和耗时随文档规模线性放大，少量修改也要重复调用全部 Embedding；
2. 重建期间可能出现旧版本不可查、新版本未完成的检索空窗；
3. 失败重试、重复事件和人工补导会制造重复 chunk 或错误覆盖当前版本。
```

因此文档增量更新的核心不是简单的 `UPDATE file`，而是以“**文档身份稳定、版本不可变、Chunk 可比较、激活原子切换、旧版本可回溯**”为原则。

#### 当前已落地的版本安全基线

当前知识模型已具备以下基础：

```text
KnowledgeDocument：稳定 document_key、来源路径、文档 content_hash、当前版本指针
KnowledgeDocumentVersion：按 document_id 递增的 version_no、版本 content_hash、状态
KnowledgeChunk：每个 chunk 的 content_hash、位置、向量、状态
检索约束：仅检索 document/version/chunk 都为 active 的数据
activate_version：新版本成功后激活，并将旧版本/旧 chunk 设为 superseded
```

这样做保证了导入过程即使失败，也不会把正在使用的 active 版本直接删掉；Worker 失败时文档/版本会标记 `failed` 并保留错误原因，后续可以定位、重试或重新投递。检索端只读取 active 文档、active 版本和 active chunk，避免同一文档的新旧版本同时召回、造成答案互相矛盾。

#### 目标增量流程：先 Diff，再只嵌入新增或变化的 chunk

项目复用 `simple_rag` 中“文档版本、Chunk Diff、增量 Embedding、软删除与恢复”的成熟思路，并适配为本项目的 PostgreSQL + pgvector + Outbox + Redis Streams 架构。目标处理流程如下：

```text
检测到同一 document_key 的新文件/新内容
  → 计算全文 content_hash
  → 与 current active version 比较
  ├─ hash 相同：视为无内容变化，幂等返回，不重复导入
  └─ hash 不同：创建 processing 新版本
        → 标准化文本并稳定切分
        → 计算每个 chunk 的 content_hash
        → 与当前 active version 做 Chunk Diff
             ├─ unchanged：复用已有向量/元数据，不重复 Embedding
             ├─ added：仅对新增 chunk 调用 Embedding
             ├─ changed：仅对变化 chunk 调用 Embedding
             └─ removed：旧 chunk 随旧版本 superseded/软删除，不再参与检索
        → 写入新版本 chunk
        → 全部成功后原子 activate_version
        → 当前版本指针切换，新版本可检索，旧版本保留追溯
```

这里 `content_hash` 用于判断“文档整体有没有变化”，`chunk_hash` 用于判断“变化精确发生在哪些片段”。两者缺一不可：只比较全文会导致任何微小变动都全量嵌入；只比较片段又无法明确版本是否完整、是否应切换当前版本。

#### 为什么不能仅按 chunk_index 判断是否变化

例如制度开头新增一段，后续所有片段的 `chunk_index` 都可能整体后移；如果仅凭位置认为“第 10 块还是第 10 块”，就可能错误复用不属于同一语义内容的向量。因此 Diff 至少需要以内容哈希为主、位置/标题路径等结构信息为辅。对于重复段落或章节整体移动的情况，后续可进一步使用“内容哈希 + 文档锚点/标题路径”的匹配策略，减少误匹配。

#### 增量更新与一致性：新版本不能半成品上线

增量不意味着边更新边暴露。正确状态机是：

```text
active(old version)
  → processing(new version)
  → active(new version) + superseded(old version)
```

只有当新增/变化 chunk 的向量都已成功写入、数量校验通过后，才允许切换 `current_version_id`。若 Embedding、解析或写入失败，则新版本标为 `failed`，继续由旧 active 版本服务；这样检索不会出现“文档已更新但只回答前半部分”的半成品结果。

#### 与 Outbox、Worker 和幂等的关系

更新事件应与业务事务一起写入 Outbox，再由 Redis Streams/Knowledge Worker 消费。因为消息可能至少一次投递，Worker 可能在调用 Embedding 后重试，所以需要以 `document_id + content_hash` 或版本标识建立导入幂等边界：同一版本不得重复创建；同一 chunk hash 的已有向量应被复用；重复消费不能造成多个 active 版本。

当前异步导入已具备 Outbox、Stream、Worker、失败状态与 Embedding 有限重试的主链路；增量 Diff/向量复用是该链路的重要后续强化方向。答辩时应准确表述为：**当前版本化和 active/superseded 切换已经保护检索一致性；基于 Chunk Diff 的“仅对新增/变化 chunk 嵌入、未变 chunk 复用”是已确定并复用成熟方案的增量优化目标，不能混称为已完全上线。**

#### 增量更新必须验证的事实

```text
相同内容重复导入：不创建重复版本、不重复调用 Embedding；
只改一个段落：仅变化/新增 chunk 进入 Embedding 队列；
删除一个段落：旧内容不再从 active 检索结果返回；
新版本中途失败：旧 active 版本仍可检索；
重复消息/Worker 重试：不会出现两个 active 版本或重复 chunk；
版本切换后：答案可返回 document_id、version_id、chunk_index，支持追溯来源。
```
### 8.2 RAG 为什么采用“关系主库 + 向量检索适配”的路线

RAG 与交易事实不是同一种数据：文档语义召回适合向量索引，客户、账户、订单、授权和风险状态则需要关系事务、一致性和可审计性。因此：

```text
PostgreSQL：业务事实源、文档元数据、版本/权限/生命周期
pgvector：当前文本语义检索的主落地路径
Milvus：规模化或专用向量索引的可替换扩展路线
Neo4j：未来关系/多跳问题的 GraphRAG 投影，不承担业务事实源
```

这种设计避免把客户交易事实复制进不可控的知识上下文，也避免为了“使用更多组件”而让检索架构过早复杂化。即便未来接入 Milvus 或 Neo4j，客户、产品、交易、风险与政策元数据仍以 PostgreSQL 为准；向量库和图数据库保存的是可重建索引或关系投影。

### 8.3 RAG 的权限与合规边界

不是所有本地资料都能被匿名问答直接引用。公司信息、产品资料和公开政策可以作为知识问答来源；用户测试数据、客户研判规则、客户画像、风险调查等内容必须按角色、场景和资源范围过滤。RAG 的职责是“检索证据、组织解释”，不是绕过 RBAC 的数据出口。

同样，RAG 输出不能直接成为：

```text
交易执行依据
权限判断依据
AML 命中结论
风险动作最终指令
```

模型可能产生幻觉、遗漏版本信息或无法证明推理过程；涉及金额、适当性、风险阈值、授权和状态变更时，必须回到规则引擎、确定性 Service 与数据库事实。

### 8.4 Agent 架构决策：Supervisor 受控编排，而不是自由聊天的多 Agent

计划书初期提出客服、投顾、风控、数据分析和业务操作五类 Agent，但架构决策没有采用“所有 Agent 互相自然语言转发”的方式。当前确定的目标架构为：

```text
用户请求
  → Supervisor（识别意图、选择能力、控制预算）
  ├─ CustomerServiceAgent：知识/政策证据
  ├─ AdvisorAgent：画像与产品适配分析
  ├─ RiskAgent：规则和风险证据解释
  ├─ AnalystAgent：只读统计与数据分析
  └─ OperationAgent：意图解析、参数抽取、操作草稿
  → Supervisor 校验结构化 AgentResult 并汇总
  → 返回解释、草稿或人工确认入口
```

领域 Agent 默认不直接互相调用内部方法，也不共享未经权限过滤的客户上下文。它们返回结构化 `AgentResult`，Supervisor 只把必要的前序结果作为只读上下文传给后续节点。这样可以控制上下文泄漏、递归循环、Token 成本、调试复杂度和工具越权。

### 8.5 LangChain、LangGraph、Tool 与确定性 Service 的职责

```text
LangChain：模型 Provider、Prompt、Retriever、Tool、ReAct 抽象
LangGraph：Supervisor、状态图、检查点、循环上限、人工确认节点
Service：权限、规则、事务、金额、状态机、错误映射
Repository/UoW：数据访问与事务边界
```

普通 ReAct Agent 仅挂载 Read / Analysis / Draft Tool：可查政策、查经授权的客户摘要、生成推荐说明、生成操作草稿。Execute Tool 不直接暴露给模型；任何申购、赎回、转账、风险限制等写操作，都必须在用户/工作人员确认后由确定性 Service 再次检查权限、授权、适当性、风险、额度、余额、幂等和状态，随后才允许提交事务。

这也是为什么“业务操作 Agent”不是自然语言一句话就直接扣款：Agent 只负责把自然语言变成受校验的参数和草稿，不能成为资金执行主体。

### 8.5A Agent 权限边界：为什么“会调用 Tool”不等于“有权执行业务”

Agent 架构中最重要的安全设计不是给模型更多 Tool，而是明确**模型、Agent、Tool、确定性 Service、数据库事务**之间的权限边界。系统不把 Agent 视为可自由行动的“数字员工”，而是将其限定为 Supervisor 管理下的只读查询器、分析器和草稿生成器。

原因很直接：LLM 输出可能误解意图、产生幻觉、被提示注入诱导，且不具备数据库事务、幂等、余额、授权、风险和审计的一致性保障。若 Agent 可以直接调用任意 API、执行 SQL 或改余额，模型的一次误判就可能变成真实资金或合规事故。

#### 三层权限模型

```text
L0：只读数据 Agent
L1：建议 / 草稿 Agent
L2：确定性业务 Service（不是自由 Agent）
```

| 层级 | 可以做什么 | 明确禁止什么 | 设置原因与收益 |
|---|---|---|---|
| L0 只读 | 查询经权限过滤的知识库、客户画像、持仓、产品、风险状态；执行受限只读查询；返回结构化分析 | 修改业务数据、执行交易、修改权限、删除审计、操作关键队列 | 将模型影响限制在“看和解释”，避免敏感事实越权或不可逆操作 |
| L1 建议/草稿 | 生成推荐草稿、风险处置建议、业务操作草稿、工单草稿；请求 Supervisor 调用下一步 Service | 直接提交申购/赎回/转账、修改余额/持仓/订单状态、绕过客户确认 | 保留智能辅助价值，同时把“建议”和“执行”分离，便于用户确认和审计 |
| L2 确定性 Service | 校验权限、资源范围、授权、适当性、风险、余额、限额、幂等和状态机；在 PostgreSQL 事务中写入业务事实；写审计和 Outbox | 不接受自由 Agent 直接绕过这些校验 | 关键业务具备可复现、可测试、可回滚、可追踪的确定性结果 |

因此只有 L2 可以改变核心业务状态，例如账户余额、冻结余额、持仓、订单状态、赎回/转账状态、风险限制和权限关系。Agent 即使能够理解“帮客户转账 10 万”，也只能输出结构化参数和草稿，不能成为资金执行主体。

#### Tool 不是 API 的直接镜像

系统不把所有后端 API 直接暴露为 Agent Tool，而是按风险分为：

```text
Read Tool：只读查询，如知识检索、产品查询、已授权客户摘要、风险状态查询
Analysis Tool：适当性计算、风险解释、受限只读统计
Draft Tool：推荐草稿、业务操作草稿、工单草稿
Execute Tool：真正改变事实的执行入口
```

普通 ReAct Agent 仅能挂载 `Read / Analysis / Draft Tool`。`Execute Tool` 不开放给自由 Agent：即使由 Supervisor 识别出执行意图，也必须进入人工/客户确认和确定性 Service 的独立流程。

在 Tool 调用前还必须校验：

```text
当前 Agent 是否在 Tool 白名单内
当前请求用户是否有对应业务权限
客户 ID 是否属于当前用户可访问范围
参数是否通过 Pydantic Schema
是否包含敏感数据、是否应脱敏
是否为只读操作、是否需要确认
```

这意味着“Agent 拿到了客户 ID”并不等于可以读取该客户的全部数据，更不等于能代表该客户操作账户。

#### Supervisor 与领域 Agent 的隔离

Supervisor 是唯一的编排入口：负责识别意图、获取当前身份/权限上下文、选择领域 Agent、设置超时和并发预算、验证 `AgentResult`，再决定是否展示建议、创建草稿或请求用户确认。

领域 Agent 之间默认不直接通信：

```text
禁止 Agent 直接调用另一个 Agent 的内部方法
禁止 Agent 直接修改另一个 Agent 的记忆
禁止 Agent 私自增加 Tool
禁止共享未经权限过滤的客户上下文
禁止通过 Redis/队列直接执行资金操作
禁止自然语言无限递归转发
```

它们只返回通过 Schema 校验的 `AgentResult`，例如包含 `summary`、`data`、`evidence`、`confidence`、`requires_human_confirmation`。Supervisor 只把必要、脱敏且经授权的前序结果作为后续 Agent 的只读上下文。好处是可以限制上下文泄露、Token 成本和循环调用，也能让每个结果有来源和证据。

#### 从自然语言到真实业务的受控执行链路

以“帮客户转账 10 万”为例：

```text
用户自然语言请求
  → Supervisor 识别 transfer_intent
  → OperationAgent 提取收款账户、金额等参数，生成草稿
  → Advisor/Risk Agent 仅返回授权范围、风险/适当性证据
  → Supervisor 展示草稿及风险提示
  → 客户/有权工作人员明确确认
  → TransferService 再次校验 RBAC、客户授权、额度、余额、风险限制、幂等和状态机
  → PostgreSQL 事务写入 BusinessOperation / MoneyFlow / AuditLog
  → Outbox 产生后续异步事件
  → 返回确定性结果
```

这里 Agent 的分析结果可用于解释和辅助决策，但不能替代最后一次 Service 校验。即使客户曾做过预授权，也不是无限授权：授权必须限定主体、动作、产品/账户范围、单笔额度、有效期和撤销状态；每一笔操作仍需通过风控、余额和幂等检查。

#### 当前实现状态与答辩边界

当前已经落地的是：后端 RBAC、资源数据范围、投顾/工作人员授权边界、客户确认、适当性/风险/余额/幂等/审计等确定性业务门禁，以及 RAG/知识检索基础链路。

Supervisor、多领域 Agent、LangGraph 状态图、GraphRAG、NL2SQL/NL2API 的完整生产化编排仍属于后续演进。答辩时应说明：**权限边界、Tool 分层和“Agent 不直接执行资金操作”的架构决策已明确并由现有业务 Service 实际遵守；完整多 Agent 自动编排不是当前已上线功能。** 这样既能展示设计深度，也不会把规划误称为已实现能力。

### 8.5B Agent 事件总线：业务事件驱动的受控协作，而不是 Agent 自由聊天

多 Agent 后续演进不采用“每个 Agent 订阅所有消息、互相自然语言聊天、自己决定执行动作”的模式，而采用：**确定性业务 Service 产生事实 → PostgreSQL Outbox 可靠记录 → Redis Streams 异步分发 → Worker/Supervisor 受控消费 → Agent 输出分析或草稿 → 人工/Service 决定是否执行**。

```text
Trading / Operation / Risk / WorkOrder / Knowledge Service
  → PostgreSQL 业务事实 + OutboxEvent（同一事务）
  → Outbox Publisher
  → Redis Streams
  ├─ Risk / AML Worker
  ├─ Notification Worker
  ├─ Knowledge Worker
  └─ Agent Orchestrator / Supervisor
       → 授权过滤后的最小上下文
       → CustomerService / Advisor / Risk / Analyst / Operation Agent
       → AgentResult / Draft / Escalation
       → 人工确认或确定性 Service
```

#### 一致性分层：局部强一致、整体最终一致，Outbox 是两者的桥梁

系统不笼统追求“所有模块全局强一致”，而是按业务影响范围划分一致性等级：

```text
局部关键业务：强一致
跨模块、跨进程、外部依赖协作：最终一致
Outbox：局部同步与整体异步之间的可靠桥梁
```

这里的“局部”指一个不可拆分的业务事务边界，并不表示它不重要。凡是直接改变资金、持仓、订单状态、风险限制或审计事实的动作，必须在 PostgreSQL 单事务内全部成功或全部回滚。

例如转账审核通过的强一致边界：

```text
BusinessOperation：pending_review → settled
源账户 available_balance 扣减
目标账户 available_balance 增加
写 transfer/out 流水
写 transfer/in 流水
写审核/结算 AuditLog
```

这几个步骤不能拆成异步任务，否则可能出现“源账户已扣钱但目标未到账”“状态已结算但没有流水”等金融错误。同样的局部强一致边界包括：

```text
订单审核：订单状态、成交、资金冻结/结算、持仓、审计
赎回审核：业务操作状态、持仓减少、账户入账、redeem/in 流水、审计
限制交易：风险动作、客户限制状态、审计
风险补件：RiskAlert 状态、关联 WorkOrder、审计、待投递事件
```

而通知、邮件、短信、Embedding、知识库切分、AML 日批、Agent 分析、报表刷新、外部系统同步等外围协作，可能耗时、依赖网络或模型服务、需要重试，不应持有资金事务锁或阻塞用户请求。这些处理采用整体最终一致：主业务先提交，异步消费者随后可靠完成外围工作。

以客户转账申请为例：

```text
同一 PostgreSQL 事务（局部强一致）
  → BusinessOperation(status=pending_review)
  → AuditLog(action=submit)
  → OutboxEvent(event_type=transfer.submitted, status=pending)

事务提交后（整体最终一致）
  → Outbox Publisher 发布 Redis Stream
  → 通知审核人员
  → 风控 Worker 执行规则扫描
  → Agent 生成风险解释或调查建议
  → 看板/统计异步刷新
```

Outbox 的作用不是把所有下游模块强行加入分布式事务，而是保证“业务事实提交成功”与“待投递事件被可靠记录”同步发生。即使 Redis、通知服务或模型服务暂时失败，核心申请不会被错误回滚；`OutboxEvent` 保留为 `pending`，Publisher/Worker 后续重试，外围模块最终收敛。

```text
             同一个 PostgreSQL 事务
┌─────────────────────────────────────────────────────┐
│  Order / BusinessOperation / RiskAlert / WorkOrder   │
│  AuditLog                                            │
│  OutboxEvent(status=pending)                         │
└─────────────────────────────────────────────────────┘
                         │ 事务提交后
                         ▼
             Outbox Publisher 可靠异步投递
                         ▼
                    Redis Streams
       ┌─────────────┼───────────────┐
       ▼             ▼               ▼
 风控/AML Worker  通知 Worker     Knowledge / Agent
       └──── 最终一致地完成外围协作 ────┘
```

不选择全局强一致的原因是：若转账请求必须同步等待通知、AML、Agent、报表或外部服务全部成功才能返回，任何外围故障都会拖垮核心业务，请求时间和锁持有时间会显著增加，也会放大锁冲突、重试和故障传播。系统选择的是：**资金和状态事实正确优先，外围协作可靠异步、可重试、可审计、最终收敛。**

答辩表述可概括为：

> 订单、赎回、转账和风险限制等核心事实在 PostgreSQL 单事务内做局部强一致；通知、知识导入、AML 批处理、Agent 分析和报表等跨组件协作通过 Outbox 与 Redis Streams 做整体最终一致。Outbox 在核心事务内记录待投递事件，保证业务成功后事件不丢失，再由 Worker 异步重试和消费，因此既避免了全局分布式事务的高耦合，又保证了关键金融事实的正确性。

#### 为什么事件总线以业务事实为主，而不是以 Agent 为主

事件总线的主题应当是“发生了什么业务事实”，而不是“哪个 Agent 想做什么”。因此使用：

```text
transfer.submitted
risk.alert.created
workorder.customer_supplemented
knowledge.document.ingested
```

而不是把主总线设计成：

```text
risk-agent-message
advisor-agent-request
agent-a-talks-to-agent-b
```

原因有五点：

1. **业务事实稳定，Agent 实现会变化。** 订单提交、转账结算、客户补件、风险限制等事件是领域事实，不会因为模型供应商、Prompt、Agent 数量或编排框架变化而改变；Agent 角色、Prompt、Tool 和模型则会持续演进。以业务事件为中心可以让 Worker、通知、审计、报表和未来 Agent 共享同一条长期稳定契约。

2. **业务事实可审计、可回放，Agent 推理不应成为事实源。** `transfer.settled` 可以回到 PostgreSQL 中验证余额、双边流水和审核记录；而“某 Agent 认为应该转账”只是建议。发生问题时，系统应能从业务事件重建状态、重放消费者，而不是依赖不可完全复现的自然语言上下文或模型输出。

3. **降低耦合，允许 Agent 随时替换或下线。** 通知 Worker、AML Worker、知识 Worker 不应依赖某个 Agent 是否在线。未来将 Qwen 换成其他模型、将单 Agent 改为 Supervisor、多 Agent，或暂时关闭 AI 功能时，订单、风险、工单和异步任务仍应照常运行。Agent 只是业务事件的一个可选消费者，不是系统主干。

4. **防止权限扩散和“Agent 自由行动”。** 如果总线按 Agent 消息组织，容易演变为 Agent A 把完整客户上下文发给 Agent B、Agent B 再调用执行接口。以业务事件为中心时，事件只携带最小业务 ID 和必要状态；Agent Orchestrator 统一按当前身份、Tool 白名单和资源范围构造上下文，避免未经授权的数据横向流动。

5. **避免循环、成本失控和错误放大。** Agent 对 Agent 的自由消息容易形成递归转发、重复分析和 Token 放大。业务事件通常具有明确的聚合 ID、幂等键、状态机和终态，消费者可以按 `event_id`/`idempotency_key` 去重，也可以清晰限定哪些事件需要 AI 分析、哪些只需要规则 Worker 或通知 Worker。

因此推荐分为两层：

```text
业务事实总线（主总线）
  → Order / Transfer / Risk / WorkOrder / Knowledge 等稳定领域事件
  → Worker、通知、审计、报表、Agent Orchestrator 都可按权限订阅

Agent 内部编排事件（次级总线或内部状态）
  → agent.requested / agent.analysis.completed / agent.draft.created
  → 仅用于编排、观测和结果追踪
  → 不能冒充订单、资金、风险限制等领域事实
```

这样既保留 Agent 协作的扩展性，又保证当 AI 不可用、模型输出错误或 Agent 架构重构时，核心金融业务仍以数据库事实、状态机和确定性 Service 为中心稳定运行。

#### 为什么事件必须从 Outbox 出发

如果业务 Service 在提交订单、转账、工单后直接 `publish` Redis，会出现双写不一致：数据库成功但消息失败，风险/通知永远不知道事实已发生；或者消息已发布、数据库事务却回滚，下游会处理一笔不存在的业务。

因此在同一 PostgreSQL 事务中同时写入业务事实和 `OutboxEvent`。事务成功后 Outbox Publisher 才将事件投递至 Redis Streams，并更新投递状态；失败可重试。这样事件总线通知的是已经落库的业务事实，不是模型猜测或前端临时状态。

#### 事件类型与命名边界

事件按业务事实域命名，不按某个 Agent 命名：

```text
订单：order.created / order.pending_review / order.executed / order.rejected
赎回：redeem.submitted / redeem.settled / redeem.rejected
转账：transfer.submitted / transfer.settled / transfer.rejected
风险：risk.alert.created / risk.alert.pending_customer / risk.restriction.applied / risk.restriction.released
工单：workorder.created / workorder.assigned / workorder.customer_supplemented / workorder.closed
知识：knowledge.document.ingestion_requested / knowledge.document.ingested / knowledge.document.failed
```

Agent 内部事件单独命名，例如：

```text
agent.requested / agent.route_selected / agent.analysis.completed
agent.draft.created / agent.escalation.required / agent.failed
```

例如 `transfer.settled` 表示资金业务事实已经结算；`agent.draft.created` 只表示生成草稿，绝不能被理解为已经转账。

#### 统一事件信封与追溯字段

所有事件使用统一 Event Envelope，至少包含：

```text
event_id / event_type / event_version / occurred_at
trace_id / correlation_id / causation_id
aggregate_type / aggregate_id
actor（发起人身份快照）
subject（customer_id、account_id 等资源边界）
payload（最小必要业务数据）
security（数据分级、允许消费者）
idempotency_key
```

`trace_id` 串联 HTTP、日志、Outbox 和 Worker；`correlation_id` 串联一次转账从申请到审核结算的完整闭环；`causation_id` 说明当前事件由哪个前序事件触发。消费者必须回到 PostgreSQL 主库读取最终事实，不把消息 Payload 当作唯一事实源。

#### Agent 在总线中的位置与权限边界

Agent 不应直接订阅全部敏感事件。正确路径是：

```text
Redis Stream
  → Agent Orchestrator
  → 根据 event_type、当前身份、权限和资源范围判断是否需要 Agent
  → 为 Agent 构造最小、脱敏、只读上下文
  → Agent 返回结构化 AgentResult
  → Supervisor 保存建议/草稿、创建待人工处理任务，或请求确定性 Service
```

不同 Agent 的典型职责：

| Agent | 可消费的事实事件 | 输出 | 禁止行为 |
|---|---|---|---|
| CustomerServiceAgent | 工单创建、待补件、客户补充 | 答复/补件说明草稿、知识证据 | 直接关闭工单、承诺收益 |
| AdvisorAgent | 画像更新、授权变化、产品更新 | 推荐解释、适当性草稿 | 直接下单、绕过客户确认 |
| RiskAgent | 待审核订单、赎回/转账申请、AML 批次完成 | 风险解释、处置建议、补件建议 | 直接限制账户或执行资金操作 |
| AnalystAgent | 日估值、AML 批次、报表请求 | 只读统计和报告草稿 | 任意 SQL、读取未授权客户明细 |
| OperationAgent | 自然语言操作意图 | 参数提取和业务草稿 | 扣款、修改持仓、直接提交结算 |

真正的限制交易、订单挂起/拒绝、余额更新和资金流水仍由 `RiskActionService`、`TradingService`、`OperationService` 等 L2 确定性 Service 执行。

#### 典型流：客户转账申请

```text
客户提交转账
  → TransferService 校验账户、余额、风险、幂等
  → 写 BusinessOperation(pending_review) + AuditLog + OutboxEvent(transfer.submitted)
  → Outbox Publisher 投递 Redis Stream
  → Risk Worker 执行确定性规则，必要时创建 RiskAlert
  → Notification Worker 通知审核人员
  → Agent Orchestrator（可选）让 RiskAgent 生成风险解释/调查建议
  → 风控/审核人员处理
  → 审核前 TransferService 再次校验风险、限制、余额、状态、幂等
  → 同一事务写双方余额、transfer/out、transfer/in、AuditLog、OutboxEvent(transfer.settled)
```

即使某个 Worker 或 Agent 已处理 `transfer.submitted`，最终审核结算前仍必须重新校验；事件消费用于辅助、通知和建议，不能替代交易主链路的最后一道门禁。

#### Redis Streams、幂等、重试与死信

第一版推荐保留有限的 Stream：

```text
stream:wealth:domain-events
stream:wealth:agent-events
stream:wealth:dead-letter
```

消费者组可包括：`risk-workers`、`notification-workers`、`knowledge-workers`、`agent-orchestrators`、`analytics-workers`。后续事件量增长后再按 trading/risk/workorder/knowledge 分流，避免初期每个 Agent 一个队列造成运维和排查复杂度膨胀。

Redis Streams 是至少一次投递，因此消费者处理顺序必须是：

```text
收到事件
  → 用 event_id / idempotency_key 检查是否已处理
  → 回读 PostgreSQL 验证业务事实与当前状态
  → 写消费结果 / AgentResult / 审计
  → 成功后 ACK
```

不能先 ACK 再调用模型或写库。失败时记录尝试次数、保留 pending、用 XCLAIM 重新分配消费者；超过最大重试次数后写入 dead-letter，人工决定重放、忽略或修复数据。

建议后续增加：

```text
event_consumer_receipt：事件消费者处理凭证
agent_run：Agent 调用、模型、Prompt、耗时、状态
agent_result：结构化分析/建议/草稿及 evidence/confidence
agent_tool_audit：Tool 调用、权限快照、脱敏输入输出、结果
```

这些表只记录 Agent 辅助过程，不能替代订单、资金流水、风险动作等业务事实表。

#### 数据最小化与安全

事件 Payload 不应携带完整身份证件、全量账户流水或完整聊天记录。事件只携带业务 ID、必要状态、规则编码、金额/金额区间和关联资源；Agent Orchestrator 再通过权限过滤后的 Service 读取完成任务所需的最小数据，并在必要时脱敏。这样可避免客服 Agent 获取 AML 调查细节、报表 Agent 获取完整客户资金明细，或投顾 Agent 读取未授权客户。

#### 当前实现状态：事件基础设施已落地，Agent 编排尚未接入

项目不是从零规划事件总线，后端已经具备可靠异步事件的核心链路：

```text
PostgreSQL 业务事实 + outbox_event
  → workers/outbox_worker.py（Outbox Publisher）
  → Redis Streams
  → Consumer Group / Pending Claim / ACK
  → Knowledge Worker 等异步消费者
```

已落地组件及职责如下：

| 已有组件 | 当前实现职责 | 关键设计价值 |
|---|---|---|
| `outbox_event` | 在知识文档、任务等业务事务中记录待投递事件 | 避免数据库事实和消息投递双写不一致 |
| `workers/outbox_worker.py` | 扫描 `pending` 事件，使用 `FOR UPDATE SKIP LOCKED` 锁定、增加尝试次数、发布后标记 `published`，失败后重试或标记 `dead` | 多 Worker 并发下避免重复抢占，并保留失败证据 |
| `app/tasks/streams.py` | Redis Streams 的 publish、Consumer Group、consume、claim pending、ACK 封装 | 具备至少一次投递、故障消息认领和消费确认基础 |
| `workers/knowledge_worker.py` | 消费 `stream:wealth:ingestion`，执行文本切分、Embedding、Chunk 写入和版本激活 | 已经是“事件发布 → 异步消费 → 业务结果回写”的真实运行闭环 |
| `AsyncTask` / `AuditLog` / Trace ID | 任务状态、审计和跨请求链路追踪 | 支持故障排查、回放和答辩证据 |

当前 Outbox Publisher 已按事件前缀路由：

```text
risk.*      → stream:wealth:risk
memory.*    → stream:wealth:memory
knowledge.* → stream:wealth:ingestion
其他事件     → stream:wealth:tasks
```

因此，当前阶段准确表述应为：**后端已经实现 Outbox + Redis Streams + Worker 的可靠事件基础设施，且知识库导入已实际使用；尚未接入的是 Agent Orchestrator、Supervisor、多领域 Agent、AgentResult 持久化和 Agent 专用消费回执。**

未来 Agent 不需要另起一套消息机制，而是复用现有事件基础：业务事实事件进入 Stream 后，由 Agent Orchestrator 判断是否需要 AI 分析，构造权限过滤后的最小上下文，调用只读/草稿 Agent，保存结构化结果；真正的资金、订单、风险限制动作仍回到确定性 Service。

答辩时可这样概括：

> 系统已经通过 PostgreSQL Outbox、Redis Streams、Consumer Group、Pending Claim 和 ACK 建立可靠异步处理能力，并已用于知识文档导入。后续多 Agent 将复用这条事实事件总线，而不是让 Agent 自由聊天或直接操作资金；Agent 只生成分析和草稿，核心业务仍由确定性 Service 在事务中执行。

### 8.6 GraphRAG 的定位与未来路由

GraphRAG 用于回答产品—风险等级—政策—客户画像之间的关系/多跳问题，例如“某风险等级客户适合哪些产品”“客户持仓的行业暴露与相关政策如何关联”。它不是固定地先于普通 RAG 执行：

```text
纯文本/政策问题 → 普通 RAG
关系/多跳问题 → GraphRAG
推荐/风险问题 → RAG/GraphRAG 提供证据 + 确定性适当性/规则服务裁决
开放复杂问题 → Supervisor 组合多个只读证据节点
```

不固定检索顺序的原因是：图谱实体识别失败时，不应该阻塞普通文本检索；普通问答强行走图谱也会增加延迟和维护成本。当前优先级仍是稳定现有文本 RAG、异步导入和权限边界；GraphRAG、Fan-out/Fan-in 多 Agent、高级 NL2SQL 和持久化检查点属于后续增强。

---
## 9. 投顾、赎回与转账：关键业务闭环

这一节专门说明“页面上有入口”与“后端形成可验证业务闭环”的差别。三个模块都遵循同一原则：**推荐或录入只是开始，真正的闭环必须包含身份与授权、确定性校验、状态流转、资金/持仓事实变化、流水与审计，以及重复请求和重复审核保护。**

### 8.1 投顾闭环：从推荐到客户确认、审核与结算

投顾不应被设计成可以绕开客户、直接执行资金操作的超级角色。系统将投顾链路拆成多个受控阶段：

```text
客户选择并授权投顾
  → 投顾仅查看“我的授权客户”
  → 读取客户画像、资产、持仓、风险等级
  → SuitabilityService 输出匹配/排除产品及原因
  → 投顾创建操作草稿（不扣款、不改持仓）
  → 客户确认或拒绝草稿
  → 确定性 TradingService 生成待审核订单
  → 有审核权限的人员审核通过/拒绝
  → 结算资金、更新持仓、写 MoneyFlow / OrderStatusHistory / AuditLog
  → 客户与后台查询结果
```

#### 为什么先做“授权 + 草稿”，而不是给投顾直接下单

早期缺口复盘发现，`review` 只能审核客户已创建并确认的订单，不能等同于“投顾代客交易”。若直接把下单能力交给投顾，会同时缺失客户授权、产品/金额边界、客户确认、撤销、操作主体审计等关键控制。

因此系统建立 `customer-advisor authorization`：授权不是一个简单的 `is_authorized=true`，而是至少约束：

```text
授权主体（客户—投顾）
允许动作（如 subscription / redeem / transfer）
产品范围
单笔额度
客户风险上限
有效期
撤销状态
```

创建草稿时校验 active 授权、动作、产品范围、单笔额度和风险上限；客户撤销授权后，投顾不再能在普通投顾接口中查看、推荐或继续创建草稿。跨投顾隔离回归覆盖了“授权前拒绝、授权后允许、其他投顾仍拒绝、撤销后再次拒绝”的边界。

#### 投顾订单为什么直接进入 `pending_review`

普通客户自助下单的路径是：创建 `pending_confirmation` → 客户确认 → `pending_review`。投顾草稿已经由客户在草稿环节确认，提交草稿后若仍让客户在订单页再次确认，会造成重复确认、后台待审单不可见等流程断裂。因此投顾确认后的订单单独进入 `pending_review`，但这**不等于自动执行**：仍必须通过审核、余额、适当性、风险门禁和资金结算。

#### 投顾资金结算踩坑：不能用“冻结余额是否大于零”猜订单来源

投顾订单不走普通客户确认时的冻结流程。早期审核结算沿用“从冻结余额扣款”的假设，导致投顾订单审核后出现 `frozen_balance` 为负、`available_balance` 未减少的问题。

改进后按正式订单来源区分结算路径：

```text
普通客户订单：确认时冻结 → 审核通过从冻结余额结算
投顾确认订单：未走冻结 → 审核通过从可用余额直接扣减
```

同时对拒绝和异常余额增加保护，并对历史已执行投顾订单执行只读对账：先检查订单、状态历史、账户余额和匹配出账流水，再按订单级幂等条件补齐确认缺失的流水；不允许“发现异常就批量补扣”，避免二次扣款。这一复盘的核心是：**资金结算分支必须依赖可审计的业务来源/状态，不应依赖偶然余额特征。**

### 8.2 赎回闭环：持仓约束、审核结算与反向资金流

赎回不能只把订单状态改成“成功”。它必须证明客户确有可赎回持仓，并在审核通过后同时完成资产与资金两侧事实更新：

```text
客户本人发起，或工作人员在有效授权范围内代办
  → 校验身份、权限、授权、持仓和幂等键
  → 创建待审核业务操作
  → 审核通过 / 拒绝
  → 通过：持仓减少、账户余额增加、生成 redeem/in 资金流水
  → 拒绝：余额和持仓不变
  → 写入提交、拒绝、结算审计
  → 客户/工作人员在允许范围内查询操作结果
```

这里的关键不是“赎回接口能返回 200”，而是精确验证：审核通过时余额增加、持仓减少、入账流水和审计都存在；审核拒绝时没有资金或持仓副作用；重复审核返回冲突而不是重复结算。工作人员代办还必须检查 active 授权，且工作人员只能查询自己代办、被授权或有管理范围的记录。

### 8.3 转账闭环：双边余额、两条流水、审核与高额确认

转账比单账户扣款更容易出现半成功：源账户扣了钱、目标账户没到账；或者审核重放导致双方重复变化。因此它被设计为审核状态机而不是立即改余额：

```text
客户发起 / 工作人员代办
  → 身份、权限、客户授权、账户归属、额度、余额、幂等校验
  → 高额操作客户确认（适用时）
  → 创建 pending_review 转账申请
  → 审核拒绝：状态终止，不产生资金流水
  → 审核通过：同一事务内源账户扣减、目标账户增加
  → 同时生成 transfer/out 与 transfer/in 两条流水
  → 写提交、审核/拒绝、结算三类审计
  → 双方按各自数据范围查询业务操作和流水
```

转账验收不仅验证状态码，还验证：提交幂等、越权拒绝、审核拒绝、审核通过、重复审核冲突；通过后双方余额精确变化，且存在两条归属正确的流水。实现中曾发现入账流水 `customer_id` 错绑为源账户客户，页面或审计查询会把收款事实显示给错误对象；因此修正为目标账户所属客户。这说明资金流水中的“业务类型正确”还不够，**资源归属也必须正确**，否则 RBAC 数据范围会被绕过或误判。

工作人员代客转账的边界同投顾一致：需要 active 授权、`transfer` 动作许可、客户确认、单笔额度和幂等校验；不能因为工作人员拥有 `transfer:submit_staff` 就无限制转移任何客户资金。

### 8.4 三个闭环的共同防线

| 控制点 | 投顾 | 赎回 | 转账 |
|---|---|---|---|
| 资源范围 | 仅已授权客户、投顾隔离 | 本人或有效代办授权 | 源账户归属、有效代办授权 |
| 业务前置校验 | 适当性、风险、授权边界 | 持仓、风险、授权 | 余额、额度、风险、授权 |
| 状态机 | 草稿→客户确认/拒绝→待审核→结算 | 待审核→通过/拒绝 | 待审核→通过/拒绝 |
| 防重复 | 草稿/订单幂等、重复确认保护 | 幂等键、重复审核冲突 | 幂等键、重复审核冲突 |
| 事实落库 | 订单、状态历史、持仓、流水 | 操作记录、持仓、入账流水 | 双账户余额、双边流水 |
| 可追溯 | 授权、推荐/草稿、审核审计 | 提交/拒绝/结算审计 | 提交/审核/结算审计 |

这些模块共同体现一个原则：**LLM、投顾人员或前端只能提出建议/请求；金额、状态、权限、适当性、资金和持仓必须由后端确定性服务在事务中裁决。**

---
## 10. 工单系统从 MVP 到闭环的迭代过程

## 10.1 第一阶段：先做 MVP 状态机与权限域

最初设计目标不是立刻做复杂 SLA、附件、通知，而是先解决：

```text
谁可以创建？
谁可以看？
谁可以分派？
谁可以处理？
谁可以关闭？
状态如何流转？
```

MVP 状态机：

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

### 初始角色边界

| 角色 | 创建 | 读取 | 分派 | 处理/关闭 |
|---|---:|---:|---:|---:|
| 客户 | 本人 | 本人 | 否 | 否 |
| 客服 | 是 | 是 | 否 | 是，但仅分派给本人 |
| 客户经理 | 是 | 是 | 是 | 是，但仅分派给本人 |
| 风控 | 是 | 是 | 是 | 是，但仅分派给本人 |
| 运营 | 是 | 是 | 是 | 是，但仅分派给本人 |
| 审计 | 否 | 是 | 否 | 否 |
| 超级管理员 | 系统级放行 | 系统级放行 | 系统级放行 | 系统级放行 |

## 10.2 第二阶段：证明“分派是真的分派”

现场首先提出的问题是：

> 分派到底是数据库写入，还是只改变了前端卡片？

因此补强验证：

```text
API 返回 assigned_to
状态变为 assigned
数据库 work_order.assigned_to 写入目标人员
审计日志产生 assign
重新读取仍能看到处理人
```

这一步的意义是把“界面演示”升级为“业务事实验证”。

## 10.3 第三阶段：处理权限收紧

初始实现中，拥有 `workorder:handle` 的人理论上可能看到处理按钮。随后明确规则：

```text
非超级管理员：只能处理 assigned_to == 自己 的工单
```

同时前端也收紧：

- 普通处理人员默认只加载 `scope=assigned`；
- 非本人分派工单不显示处理按钮；
- 后端仍保留 403 作为最终防线。

## 10.4 第四阶段：处理人如何知道被分派

发现分派虽然持久化了，但被分派人员没有主动感知，于是增加：

```text
notification 表
GET /notifications
单条已读 / 全部已读
后台顶部通知收件箱
```

事件联动：

```text
分派 → 通知处理人
待客户补充 → 通知客户
客户已补充 → 通知处理人
已解决 → 通知客户/创建人
已关闭 → 通知客户/创建人
```

## 10.5 第五阶段：待客户补充从“死状态”变成材料往返闭环

最初 `pending_customer` 只是状态变化与通知，客户无法提交内容。随后发现这不构成闭环，于是新增：

```text
work_order_update
GET /workorders/{id}/updates
POST /workorders/{id}/supplements
```

完整流程：

```text
工作人员写明补充要求
  → 写入 work_order_update
  → 客户查看要求
  → 客户提交补充文本
  → 写入 work_order_update
  → 工单回到 in_progress
  → 原处理人收到通知
  → 原处理人查看补充并继续处理
```

## 10.6 第六阶段：前端运行时错误与按钮事件复盘

在客户补充现场验收时，浏览器出现：

```text
loadUpdates is not defined
```

根因是一次文本修改后，模板和 `load()` 仍调用函数，但函数定义被意外删除。结果：

```text
组件初始化报错
→ 点击补充按钮无反应
→ 后端没有 /updates 或 /supplements 日志
→ 数据库当然没有记录
```

修复措施：

- 恢复 `loadUpdates`、`toggleUpdates`、`submitSupplement`；
- 对工单卡片按钮显式添加：

```html
<button type="button" @click.prevent.stop="...">
```

- 检查 Vite 实时源文件，确认开发服务器加载新代码；
- 在前端构建之外，增加运行时源码检查。

这次踩坑说明：

> “构建通过”并不等于“运行时所有模板路径都正确”。

## 10.7 第七阶段：筛选、分页、状态 UI

工单从少量演示数据进入多角色、多状态后，出现新问题：

```text
工单多了怎么找？
高优先级在哪里？
待客户补充在哪里？
工作人员如何只看自己的待办？
```

因此增加：

```text
GET /workorders?scope=&status=&priority=&category=&limit=&offset=
GET /workorders/summary
```

前端增加：

```text
我的待办 / 全部可见
待客户补充统计
高 / 紧急统计
状态、优先级、类型筛选
分页
```

状态 UI 从过度圆润的胶囊样式，改为带图标的方圆角标签：

```text
↻ 处理中
… 待客户补充
✓ 已解决
↳ 已分派
```

## 10.8 第八阶段：附件与认证下载

客户补充文本后，仍无法提交真正的交易凭证、截图和 PDF，因此新增：

```text
work_order_attachment
POST /workorders/{id}/attachments
GET /workorders/{id}/attachments
GET /workorders/{id}/attachments/{attachment_id}/download
```

### 附件安全规则

```text
允许：JPG / PNG / PDF
限制：默认 10 MB
存储：随机文件名
权限：按工单 read scope 校验
审计：upload_attachment
```

### 再次踩坑：工作人员下载附件 401

客户上传成功后，工作人员点击普通 `<a href>` 下载，日志出现：

```text
GET /attachments/{id}/download
401
```

根因：

```text
JWT 存在 sessionStorage
普通 <a href> 不会带 Authorization Header
```

修复：

```text
axios 请求下载接口
responseType = blob
自动携带 Bearer Token
创建 Blob URL
触发浏览器保存
```

这说明受保护文件下载不能简单使用静态链接，必须经过认证请求链路。

---

## 11. 关键踩坑、根因与工程改进

| 问题 | 根因 | 修复 | 工程教训 |
|---|---|---|---|
| 工单分派看似成功但担心未持久化 | 只看前端卡片/接口状态不足 | 加数据库字段、审计、重读断言 | 必须验证业务事实来源 |
| 超级管理员无法分派 | 前端使用手工 UUID，缺少处理人列表 | `/workorders/assignees` + 下拉框 | 后端能力必须有可操作 UI |
| assignees 查询 `MissingGreenlet` | 异步 ORM 下懒加载角色权限 | `selectinload(Role.permissions)` | Async ORM 不能依赖隐式懒加载 |
| 已解决连续 422 | 前端未提示说明必填 | 本地校验 + 状态相关 placeholder | 后端契约要有可理解 UI |
| 待客户补充无闭环 | 只有状态，没有客户提交入口 | 更新记录、补充 API、通知回流 | 状态必须对应真实业务动作 |
| 客户补充按钮无反应 | `loadUpdates` 等函数定义被删 | 恢复函数、运行时源码检查 | 编译通过不等于页面运行正确 |
| 前端按钮可能触发表单默认行为 | 卡片内按钮未显式声明类型 | `type="button"` + `.prevent.stop` | 嵌套 UI 需明确事件语义 |
| 工单多了难处理 | 全量卡片无筛选分页 | summary、筛选、分页、排序 | MVP 成功后要补可操作性 |
| 附件下载 401 | `<a>` 不携带 sessionStorage JWT | Axios blob 下载 | 受保护文件不能直接裸链 |
| 开发状态重置后持续 401 | 浏览器保存旧 refresh token | refresh 失败清 Token、跳登录 | 开发重置也要考虑前端体验 |
| 权限名称语义过宽 | `order:write` 等承担多职责 | 拆分细粒度权限码 | 最小权限要从命名开始 |

---

## 12. 测试策略与质量保障

## 12.1 测试原则

项目使用“分层验证 + 业务事实验证”的策略：

```text
语法/导入检查
  → API 契约回归
  → 数据库持久化检查
  → 权限拒绝检查
  → 前端生产构建
  → 浏览器现场验收
```

## 12.2 核心回归脚本

| 脚本/命令 | 覆盖范围 |
|---|---|
| `scripts/test_workorders_http.py` | 工单全生命周期、分派、权限、通知、客户补充、附件、下载、数据库持久化 |
| `scripts/test_role_permission_boundaries_http.py` | 角色权限边界 |
| `scripts/test_rbac_roles_seed_http.py` | 角色种子与权限矩阵 |
| `scripts/test_backoffice_role_surface_http.py` | 后台菜单/页面角色可见性 |
| `scripts/test_customer_frontend_auth_contract_http.py` | 客户前台认证契约 |
| `scripts/test_auth_refresh_rotation_http.py` | Refresh Token 轮换与旧 Token 重放拒绝 |
| `scripts/test_aml_*` | AML 规则、窗口、幂等与批次扫描 |
| `scripts/test_staff_transfer_http.py` | 工作人员代办转账、余额、流水、审计 |
| `npm run build` | 前端 TypeScript/Vue 生产构建 |

## 12.3 工单回归覆盖的业务事实

当前工单回归重点验证：

```text
客户创建与读取本人
客户不能处理
处理人列表正确
无处理权限的人不能被分派
分派持久化与分派审计
我的待办范围
非分派人员不能处理
待客户补充必填说明
客户看到工作人员补充要求
客户提交补充后回到处理中
处理人查看客户补充并收到通知
已解决必填说明
关闭前必须已解决
超级管理员例外处理
附件类型拒绝
客户上传附件
工作人员查看并下载附件
其他客户越权下载被拒绝
数据库附件与更新记录持久化
```

## 12.4 文档化规则

每次功能完成，至少更新：

```text
PLAN.md   做了什么、下一步是什么
TESTS.md  如何验证、结果是什么
ISSUES.md 踩了什么坑、根因和修复是什么
```

这解决了多轮开发和跨窗口协作中“记忆丢失、上下文断层”的问题。

---

## 13. 数据模型与核心实体关系

### 12.1 身份与权限

```text
User
  └─< user_roles >─ Role
                     └─< role_permissions >─ Permission

User
  └─ RefreshSession
```

### 12.2 客户与交易

```text
User(customer)
  ├─ CustomerProfile
  ├─ CustomerRiskAssessment
  ├─ Account
  ├─ CustomerHolding
  ├─ CustomerAssetSnapshot
  ├─ Order
  │   ├─ Trade
  │   └─ OrderStatusHistory
  ├─ MoneyFlow
  └─ BusinessOperation
```

### 12.3 风险与 AML

```text
RiskRule
  → RiskAlert
      → RiskInvestigation
      → RiskReportDraft
      → 交易限制 / 风险动作

AmlBatchRun
  → 批次执行记录、规则扫描与结果追溯
```

### 12.4 工单协同

```text
WorkOrder
  ├─ customer_id
  ├─ created_by
  ├─ assigned_to
  ├─ WorkOrderUpdate
  │    ├─ 工作人员补充要求
  │    └─ 客户补充内容
  ├─ WorkOrderAttachment
  │    └─ JPG / PNG / PDF 凭证
  ├─ Notification
  └─ AuditLog
```

---

## 14. 分阶段迭代路线

### P0：基础设施与最小可运行版本

```text
Docker Compose
PostgreSQL / Redis / Milvus
FastAPI 基础框架
Vue 前端骨架
健康检查
认证基础
```

### P1：客户、产品、订单基础闭环

```text
客户资料
产品管理
账户/订单/成交
基础资产与持仓
```

### P2：业务操作与审核

```text
工作人员代办
订单、赎回、转账审核
操作审计
幂等与资金状态约束
```

### P3：风险、AML 与客户画像

```text
风险测评
适当性
风险预警
AML 扫描
风险动作与交易限制
```

### P4：RBAC、数据范围与后台体验

```text
细粒度权限拆分
客户全量/授权范围
投顾授权边界
后台角色表面
审计只读边界
```

### P5：工单协同闭环

```text
工单状态机
分派与处理范围
站内通知
客户补充
附件凭证
筛选分页
状态 UI
```

### 当前阶段特点

项目不是按“先把所有页面做出来”推进，而是按：

```text
基础可运行
→ 主业务闭环
→ 权限可信
→ 风险可控
→ 协同可追踪
→ 体验可操作
```

逐步演进。

---

## 15. 当前能力边界与后续计划

### 13.1 已实现

- 客户/员工/管理员认证与 RBAC；
- 客户资料、风险画像、资产、持仓、产品；
- 订单、赎回、转账、代办、审核、审计；
- 风险预警、AML 扫描、交易限制与风险动作；
- 知识库文档与向量检索基础；
- 工单创建、分派、处理、客户补充、通知、附件、筛选分页；
- 站内通知与认证附件下载；
- 测试、迁移、开发环境重置与演示数据。

### 13.2 后续增强

以下是有价值但应明确为后续的能力：

1. **对象存储与病毒扫描**
   - 当前附件存本地目录；
   - 生产环境可迁移到 MinIO / OSS / S3；
   - 加入文件内容扫描、缩略图、预览。

2. **邮件、短信、移动推送**
   - 当前是站内通知；
   - 可通过 `OutboxEvent` 异步投递外部通知。

3. **SLA 与自动升级**
   - 超时工单提醒、升级分派、优先级自动调整。

4. **组织、团队、客户归属模型**
   - 当前已做分派和权限范围；
   - 后续可实现团队、营业部、客户经理客户池等更细边界。

5. **附件与补充记录强关联**
   - 当前附件关联工单；
   - 后续可让附件精确关联某条 `WorkOrderUpdate`，支持一次补充包含多份材料。

6. **更完整的浏览器 E2E 测试**
   - 当前以 ASGI HTTP 回归、构建和现场验收为主；
   - 后续可增加 Playwright/Cypress 自动化浏览器测试。

---

## 16. 答辩演示建议与常见问题

## 16.1 推荐演示顺序（约 8～12 分钟）

### 第一部分：系统全貌（1 分钟）

展示客户中心和工作人员后台，说明：

```text
同一系统覆盖客户服务、交易、风险、审核、工单协同和知识辅助。
```

### 第二部分：权限与数据范围（1～2 分钟）

使用两个工作人员账号说明：

```text
拥有处理权限不等于能处理所有工单。
非分派人员会被后端 403 拒绝。
```

### 第三部分：工单完整闭环（4 分钟）

建议按以下脚本演示：

```text
1. 客户创建工单；
2. 管理员/客户经理分派给客服；
3. 客服收到站内通知；
4. 客服改为待客户补充，并填写具体要求；
5. 客户查看补充要求，提交文字和 PDF；
6. 工单自动回到处理中；
7. 客服收到客户补充通知；
8. 客服下载 PDF，查看材料；
9. 客服填写解决说明，标记已解决；
10. 客户收到解决通知；
11. 客服关闭工单。
```

### 第四部分：风险和交易控制（2 分钟）

说明：

```text
系统不只是记录风险预警；风险状态会影响订单、赎回、转账等实际业务动作。
```

### 第五部分：工程化保障（1～2 分钟）

展示：

```text
迁移文件
测试脚本
PLAN / ISSUES / TESTS 文档
审计日志
```

强调项目的可维护性和可追溯性。

## 16.2 常见答辩问题与回答要点

### Q1：为什么不直接按角色控制，不做权限码？

**回答要点**：角色是人员分类，权限是操作能力。一个角色未来可能拆分职责；同一个权限也可能授予多个角色。权限码可以降低角色变化对接口逻辑的影响，并支持最小权限。

### Q2：为什么前端隐藏按钮还要后端再校验？

**回答要点**：前端不是安全边界。用户可以直接调用 API、修改浏览器状态或重放请求。因此后端必须同时校验权限和资源范围。

### Q3：如何证明分派和附件不是假功能？

**回答要点**：不仅看页面，还验证数据库字段、审计日志、重新读取、权限拒绝、文件落盘和认证下载。工单测试脚本覆盖这些事实。

### Q4：客户补充后为什么工单会回到处理中？

**回答要点**：`pending_customer` 表示当前等待客户动作。客户补充完成后，等待条件消失，工作应回到原处理人继续推进，因此状态转为 `in_progress`，而不是自动解决。

### Q5：为什么附件下载不直接暴露静态 URL？

**回答要点**：附件包含客户凭证，不能绕过权限。下载必须经过鉴权 API。前端采用带 JWT 的 axios blob 下载，避免普通链接丢失 Token。

### Q6：系统有哪些还没做？

**回答要点**：已实现的是站内通知、文本补充、附件上传下载。外部邮件/短信推送、SLA、对象存储、病毒扫描、组织客户池等是后续增强，不应混淆为当前已实现功能。

---

## 结语

本项目的核心价值不在于页面数量，而在于逐步建立了金融业务系统需要的可信机制：

```text
身份可信
权限可信
数据范围可信
状态流转可信
风险控制可信
审计追溯可信
客户协同可信
```

功能从客户、产品、交易等基础能力开始，经过权限拆分、风险门禁、审计闭环，再迭代到工单分派、通知、客户补充、附件凭证和下载认证。每一次踩坑都推动了架构从“可演示”走向“可验证、可追溯、可维护”。

这也是系统最终采用当前分层架构、状态机、细粒度权限、数据范围、审计日志、迁移和回归测试体系的根本原因。


#### 第五步 A：解除交易限制的状态机修复——先复核、再解除、后结案

早期实现曾将解除交易限制设为“风险预警必须已 `resolved` 或 `dismissed`”的前置条件；与此同时，风险预警在账户仍为 `restricted` 时又不允许直接结案。这会形成循环依赖：

```text
要解除限制 → 预警必须先结案
要结案 → 当前仍限制交易，必须先解除限制
```

现场 RW-001 大额交易预警验证暴露了该问题：风险人员执行 `RESTRICT_TRADING` 后，预警处于 `investigating`、账户处于 `restricted`，若没有“继续处置”入口或解除条件设计不当，风险处置闭环会卡死。

修复后的正确状态机为：

```text
RW-001 命中
  → 风控接单 / 调查
  → confirmed_risk + RESTRICT_TRADING
  → RiskAlert = investigating，Account = restricted
  → 后续复核材料、客户沟通和证据
  → assessment_result = cleared_after_review 或 false_positive
  → 填写 assessment_note
  → cleared_after_review 时必须填写 customer_consulted + customer_consultation_note
  → 客户管理详情执行 RELEASE_RESTRICTION
  → Account 风险限制解除，RiskAlert.restriction_status = none
  → 回风险预警工作台执行 resolved / dismissed 结案
```

`RELEASE_RESTRICTION` 不再要求预警先结案，而是校验：当前确实处于 `restricted`、复核结论为 `cleared_after_review` 或 `false_positive`、评估依据存在；若选择“评估后通过”，还必须已有客户沟通记录。这样既避免“先结案才能解除限制”的死锁，也避免没有复核依据就随意恢复客户交易资格。

前端同步要求：除 `resolved / dismissed` 外的预警都应保留“继续处置”入口；`RESTRICT_TRADING` 自动对应 `investigating + confirmed_risk`，并要求填写风险评估依据和限制原因。客户侧始终不能自助解除限制。


### 7.6A AML 调度落地与首次真实批次验证

AML 日批不是只保留一个手工脚本。项目新增独立常驻调度 Worker：`workers/aml_scheduler.py`，按 UTC 时钟和可配置轮询间隔触发 `DailyAmlScanService`。调度不放在 FastAPI 请求进程中，避免批量扫描、数据库聚合或失败重试拖慢订单、赎回、转账等在线业务。

配置项包括：

```text
AML_SCHEDULER_ENABLED
AML_SCHEDULER_POLL_SECONDS
AML_SCHEDULER_HOUR_UTC
AML_SCHEDULER_MINUTE_UTC
AML_SCHEDULER_DAYS
```

默认建议每日 UTC `01:10` 运行最近 7 天窗口，对应北京时间 `09:10`。即使 Scheduler 重启、同一分钟重复触发或人工补跑，`DailyAmlScanService` 都会按扫描范围、天数和 UTC 窗口构造稳定 `batch_key`，同窗口复用已有 `aml_batch_run`；这是定时任务可重跑、不会重复创建批次或重复产生窗口预警的基础。

首次真实扫描已经完成：

```text
batch_id: e0c7ea15-a040-4cdc-9248-9219956f24a0
status: completed
扫描窗口：2026-07-30T00:00:00Z ～ 2026-08-06T00:00:00Z
客户总数：3
成功：3
失败：0
新增预警：1
```

这证明系统不仅具备 AML 批次表和扫描代码，还已实际生成 `aml_batch_run` 运行证据及关联预警。风险预警后台的“最近 AML 扫描批次”可展示该记录；若未来某次 `completed` 但 `created_alerts=0`，表示扫描正常完成但窗口内没有命中规则，而不是扫描失败。

### 7.6B 客户补件如何回到风控复核队列

“要求客户补充资料”不能只停在通知或工单创建，否则客户完成补件后，风控人员仍要靠人工反复查询工单列表。系统以 `WorkOrder.risk_alert_id` 建立一对一关联；客户调用 `POST /workorders/{id}/supplements` 时，在同一个数据库事务中完成：锁定工单、校验客户归属和 `pending_customer` 状态、写入客户 `WorkOrderUpdate`、将工单改为 `in_progress`、锁定关联预警并将其由 `pending_customer` 改为 `investigating`、写入 `customer_material_submitted` 审计记录、通知原处理人员，最后一次提交事务。

`investigating` 只表示材料到达并重新进入人工核验队列，不等价于风险解除。订单审核门禁、账户交易限制、报告义务和最终结案仍必须由有权限的风控人员依据材料另行操作；这避免把客户输入错误地当成自动放行依据。

风险预警列表批量读取关联工单和每张工单最新客户更新，返回工单编号、状态及补件摘要。前端据此展示“等待客户补充”或“客户已补件，待风控复核”，并提供工单入口；批量查询也避免预警列表的 N+1 查询。真实 HTTP 回归同时固定限制解除顺序：缺少复核结论时解除交易限制必须失败，正确路径为“复核结论和证据 → 解除限制 → 风险预警结案”。

### 7.6C AML 调度为何作为独立 Compose Worker 部署

AML 日批具有长耗时、可重跑、不能阻塞在线请求的特性，因此不嵌入 FastAPI 进程。项目使用独立 `aml-scheduler` 容器运行 `workers.aml_scheduler`：它等待 PostgreSQL healthcheck 后启动，使用容器网络中的 `postgres:5432`，并通过 Compose `aml` profile 隔离。当前本地基础设施属于 `docker-compose.p0.yml` 的 `wealth-p0` 项目，必须显式执行 `docker compose -f docker-compose.p0.yml --profile aml up -d aml-scheduler`；默认基础设施启动不会触发扫描。

调度 Worker 每轮根据 UTC 时间判断是否命中配置 slot。成功完成的分钟被记为 `last_slot`，防止轮询频率高于一分钟时重复调用；若扫描抛异常，则不消费 slot，下一轮会重试。即使进程重启或重复调用，`DailyAmlScanService` 仍以扫描窗口构造稳定 `batch_key`，数据库唯一约束将最终批次幂等性放在服务层，而不是只依赖内存变量。这样形成“Worker 局部去重 + 数据库最终幂等”的两层保证。

该部署已经通过调度周期测试、P0 Compose 渲染、镜像构建和容器运行验收。验收日志已出现 `aml scheduler started`，说明 Worker 已成功加入现有数据库网络；验证后主动停止。配置时间到达后仍应核对 `aml_batch_run` 与风险预警后台批次记录，作为真实定时触发的持续运行证据。

### 7.6D Scheduler 心跳与“未到时间”不是“未运行”

仅有 `aml_batch_run` 不能说明 Scheduler 是否存活：没有新批次可能是尚未到触发时间，也可能是 Worker 已退出。因此新增单行 `aml_scheduler_state`，记录 `last_heartbeat_at`、最近扫描开始/结束时间、结果和错误摘要。Scheduler 每个轮询周期更新心跳；风险接口按 `max(3 * poll_seconds, 180 秒)` 判断心跳是否过期并返回 `running`。这使后台可区分在线等待、成功完成、扫描失败和 Worker 离线。

真实验收将 Scheduler 暂时设置到下一 UTC 分钟并以 5 秒轮询运行。`12:42:02Z` 自动执行 AML 扫描，复用已存在的稳定 `batch_key`，`created=False`；状态表随后记录 `completed` 和最新心跳。该证据同时证明“调度时钟触发”“服务幂等”和“状态可观测”三层都实际运行，而非只存在于设计文档。

### 7.6E 迟到流水为何使用独立补扫批次

已完成日批不能直接重跑同一 `batch_key`，否则会按幂等规则复用旧结果并遗漏迟到入账流水。系统为补扫生成带 `late_rescan:{source_batch_id}` 后缀的独立稳定键，并在新批次记录 `run_type=late_rescan` 和 `source_batch_id`。原批次的窗口、错误和审计不被覆盖；重复点击同一补扫又会复用该补扫批次。风险后台同时展示运行类型和来源批次，使日批、失败客户补跑和迟到流水补扫均可追溯。

### 4.14E 容器职责拆分：API 进程与 AML Worker 进程不共用入口

原通用 Dockerfile 的默认命令固定为 AML Scheduler，导致 Compose 虽然能构建应用镜像，却不能一键启动 HTTP API。现在拆分为 `Dockerfile.api` 和 `Dockerfile.aml-scheduler`：前者固定运行 `uvicorn app.main:app`，后者固定运行 `python -m workers.aml_scheduler`。Compose 默认启动 API，AML 使用 `aml` profile 显式启动。

这种拆分使在线 HTTP 生命周期、数据库/Redis 健康依赖与 AML 长任务隔离：API 重启不会隐式重启 AML 扫描，AML Worker 出错也不会占用 Web 请求进程。P0 与全量 Compose 都保持相同的服务边界；P0 用于当前 PostgreSQL+Redis 本地开发，全量环境额外包含 Milvus。

### 8.6 投顾闭环下一阶段：RAG 只提供证据，业务服务保持最终决策权

投顾闭环的第一原则是“建议”和“执行”分离。投顾/RAG 可以读取已授权客户的画像、资产、持仓、风险等级和产品知识，输出推荐理由、风险提示、适当性解释及引用证据；它只能写入投顾建议草稿。客户确认、订单创建、订单审批与实际成交仍分别由受控 API、状态机和风控门禁完成。

建议草稿必须保存生成时的客户画像摘要、产品版本、风险适配结论和 RAG 引用快照。若检索没有足够的权威资料，系统应显示“证据不足，无法生成可执行推荐”，而不是让模型补写事实。客户拒绝、授权撤销、账户限制、产品下架或风险不匹配都会使草稿失效或不可转单；即使客户已确认，订单审批前仍要重新执行风险控制。

### 8.7 RAG 双业务面：客服助手与投顾证据服务不能混为一个“万能 Agent”

RAG 知识底座应同时支撑客服与投顾，但两者的输入、权限和结果不同。客服 RAG 的目标是回答产品、流程、制度、风控资料补充和工单进度等问题；它只能读取面向该客户可见的公开知识与该客户自身的工单/通知上下文，低置信度、涉及个性化投资建议、资金操作、账户限制或投诉争议时必须转人工或创建工单。客服不能读取其他客户资料，不能执行交易、转账、赎回、解除冻结或风险结案。

投顾 Evidence Service 的目标不是聊天回答，而是为已授权客户的投顾建议提供产品、风险揭示、政策和适当性规则引用。它只返回经过分类白名单、版本过滤和阈值判断的 citation snapshot；若引用不足，草稿标记 `evidence_insufficient`，不能发送客户确认、更不能转订单。

两条业务面共用 `KnowledgeBase → Document → Version → Chunk → Embedding` 的版本化底座和 Outbox/Redis Streams 入库链路，但各自拥有独立的权限过滤、查询模板、最低证据阈值和审计事件。这样既避免重复建设向量库，也避免客服回答能力越权成交易 Agent。

### 8.8 从 simple_rag 迁移的原则与边界

`C:\simple_rag` 提供多格式解析、MinerU PDF、LangChain 中文切分、批量 embedding、Milvus dense/BM25/RRF、查询改写、子问题分解、缓存及完整踩坑记录，可作为当前项目的算法和工程参考。但当前财富管理项目保持 PostgreSQL 为事实源、Outbox + Redis Streams 为可靠异步机制、RBAC 为权限边界；不复制其 SQLite 任务状态、固定本机路径或将 LLM 输出直接绑定业务执行的做法。

当前优先迁移其成熟的解析、结构化切分、批量向量化、召回去重和质量阈值思想，并结合现有 `knowledge_document_version`、`knowledge_chunk.content_hash` 实现真正的 Chunk 差异更新：未变化 Chunk 复用既有向量，新增/变化 Chunk 才嵌入，删除 Chunk 标记失效，最后原子激活新版本。
