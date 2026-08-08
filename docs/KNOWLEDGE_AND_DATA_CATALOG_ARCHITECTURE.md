# 知识治理、数据目录与未来检索架构讨论记录

> 讨论日期：2026-08-06  
> 状态：架构共识已形成；除已实现的管理员文档上传 Pipeline 外，其余内容为后续演进设计，不代表已经上线。

## 1. 当前知识库实现与边界

当前项目已实现一个基于 PostgreSQL / pgvector 的知识库底座，关键实体包括：

```text
KnowledgeBase
KnowledgeDocument
KnowledgeDocumentVersion
KnowledgeChunk
OutboxEvent
```

已实现的管理员上传路径：

```text
管理员后台上传 TXT / MD / DOCX
  → 保存至 uploads/knowledge
  → 创建 KnowledgeDocument（processing）
  → 写入 knowledge.document_created Outbox 事件
  → outbox-worker 发布到 Redis ingestion Stream
  → knowledge-worker 解析、切分、Embedding
  → 写入 knowledge_chunk.embedding（pgvector）
  → 文档状态 active / failed
```

当前上传界面的“公司信息、公司业务、FAQ、服务流程、金融政策、风险资料”等选项是 `KnowledgeDocument.category`，不是物理上彼此独立的向量库。当前文档默认进入同一个逻辑默认库（`default`），再按分类、可见范围和调用方 Scope 控制检索。

## 2. 当前客服 RAG 的真实行为与待补齐能力

客服 RAG 当前主要是“公共知识检索 + 低置信度转人工”的骨架：

```text
客户问题
  → 客服意图/分类
  → 公共知识范围检索
  → 命中足够引用：基于引用回答
  → 未达到置信度阈值：创建或复用转人工工单
```

当前 `CustomerRagService` 的低置信度阈值为 `min_score=0.45`。因此并非每句话无条件创建工单，但在知识库没有足够命中时，很多问题都会走转人工路径。

经 2026-08-06 真实 HTTP 验证：`你好`、`你是谁`、公司客服电话等问题都因低置信度进入工单；产品推荐意图不创建客服工单，但当前仅返回“产品推荐必须使用实时 Product 数据与 SuitabilityService”的边界提示。

当前待补齐：

1. **闲聊/问候分流**：`你好`、`你是谁`、感谢、结束语等不应检索、不应创建工单，应使用固定安全话术。
2. **运行环境版本同步**：工作区已有 Qwen 回答层代码，但运行 API 曾未返回 `answer` / `generation_status`，需重建运行服务确认最新代码加载。
3. **命中知识后的真实回答验证**：只有存在足够引用时才允许调用回答模型；低置信度时应直接转人工，不让模型编造。
4. **业务接口路由**：客户账户、订单、操作进度等问题未来应路由到受权限控制的业务 API，而不是全部落入知识库或工单。

## 3. 产品推荐和产品文档的边界

产品推荐不能由普通文档 RAG 决定。当前和未来都必须保持：

```text
产品是否可售、实时状态、风险等级、最低金额、收益率、适当性
  = Product 数据库事实 + SuitabilityService + 账户/风控边界

产品文档 RAG
  = 产品说明、风险揭示、费率/流动性条款解释、引用证据
```

文档不能覆盖数据库中的实时业务事实，也不能绕过适当性判断、交易确认、账户限制或人工审核。

## 4. 知识库、分类、元数据与 Scope 的分层

后续采用以下四层概念：

```text
知识域（KnowledgeDomain）
  → 决定谁可上传、默认权限、审核策略、允许哪些分类。

逻辑知识库（KnowledgeBase）
  → 决定资料属于哪个大的业务治理边界。

分类（Category）
  → 决定同一逻辑库中的具体资料类型。

文档元数据（Metadata）
  → 决定具体文档关联对象、版本、生效期、审核状态、可见性等。
```

建议的未来逻辑知识库：

| 逻辑库代码 | 用途 | 典型检索方 |
|---|---|---|
| `customer_public` | 公司信息、业务、FAQ、流程、客户可见政策/风险资料 | 智能客服、客户中心 |
| `product_evidence` | 产品说明书、风险揭示、费率、流动性、产品条款证据 | 投顾、产品经理；客户仅限审核范围 |
| `advisor_internal` | 投顾内部话术、培训、服务方法 | 投顾、管理人员 |
| `compliance_internal` | AML、合规制度、内部风控手册 | 风控、合规、审计 |

当前不必为每个逻辑库建立不同 PostgreSQL 实例、不同 pgvector 集群、不同 Worker 或不同表；可以先共用现有知识库表和向量索引，仅通过 `knowledge_base_id`、`category`、`permission_level`、元数据和 Scope 实现逻辑隔离。

只有出现强合规隔离、多租户物理隔离、量级/性能差异、不同 Embedding 模型、独立备份/审计或独立部署要求时，再考虑物理拆库。

## 5. 新资料类型的上传策略

新类型资料（如产品信息文档）不能随意塞入现有分类，也不需要立刻新建物理库。

### 第一阶段：新增分类

产品文档可先在对应逻辑域下增加分类，例如：

```text
product_overview
prospectus
risk_disclosure
fee_rule
liquidity_rule
performance_note
```

并在上传时指定合适的可见范围，通常默认员工/投顾可见，而不是上传即客户可见。

### 第二阶段：产品证据治理

当产品资料规模、版本和审核要求提高后，资料需至少具有如下元数据：

```json
{
  "product_id": "...",
  "product_code": "...",
  "document_kind": "prospectus",
  "approval_status": "approved",
  "effective_date": "2026-08-06",
  "expires_at": null,
  "customer_visible": false,
  "source_system": "admin_upload"
}
```

届时可将产品资料纳入 `product_evidence` 逻辑库，并加入审核后生效机制：

```text
上传 → 切分/向量化 → pending_review → 审核通过 → active
```

## 6. 放入（Ingestion）应做的轻量抽象

随着不同知识域出现，上传入库不能长期只依赖自由字符串：

```text
file + category + permission_level
```

建议后续增加以下轻量抽象，而不是马上构建复杂多租户知识平台：

### 6.1 KnowledgeDomain 配置

集中定义：

```text
域代码
逻辑库代码
允许分类
可用可见范围
上传者角色/权限
默认审核状态
是否允许自动激活
```

例如：

```text
customer_public：允许 faq/workflow/policy，默认 public，自动激活。
product_evidence：允许 prospectus/risk_disclosure，默认 staff，需审核后激活。
```

### 6.2 统一上传命令和服务

建议统一模型：

```python
KnowledgeIngestionCommand(
    domain="customer_public",
    category="faq",
    file=..., 
    permission_level="public",
    metadata={...},
)
```

统一由 `KnowledgeDocumentUploadService` 负责：

- 上传者权限校验；
- Domain / Category 合法性校验；
- 文件存储与 hash；
- 文档与版本元数据；
- Outbox 事件；
- 审计日志；
- 队列状态返回。

管理员后台、产品系统同步、批量脚本、未来 CMS 接入都应复用同一服务。

### 6.3 检索端只依赖 Scope

检索方不关心文件如何上传，只声明自己的受控检索范围：

```python
KnowledgeScope(
    knowledge_base_ids=(...),
    categories=(...),
    permission_levels=(...),
    metadata_filters={...},
)
```

例如：

```text
客服：customer_public + public + FAQ/流程/政策等。
投顾：product_evidence + advisor/staff + 指定产品 + 已审核且有效文档。
合规：compliance_internal + compliance 角色。
```

## 7. 新分类/新库的检索接入原则

新增分类后，底层向量化、切分、pgvector 检索不需要重写。

### 后台人工检索

后台检索只需显式传：

```text
knowledge_base_id + category
```

即可在相应范围内搜索，主要是前端筛选项和权限控制的扩展。

### 正式业务模块

客服、投顾、合规等业务模块应使用固定且集中配置的 Scope，不能全库盲搜。建议在统一位置定义：

```text
CUSTOMER_PUBLIC_SCOPE
PRODUCT_EVIDENCE_SCOPE
ADVISOR_INTERNAL_SCOPE
COMPLIANCE_INTERNAL_SCOPE
```

### 未来意图动态路由

成熟后可实现：

```text
自然语言问题
  → 业务/意图识别
  → 路由到业务 API 或对应知识 Scope
  → 检索引用
  → 受控回答/人工转接
```

但“产品推荐”仍不应由 RAG 决策，而应路由至 Product + SuitabilityService。

## 8. 结构化数据目录与未来 NL2SQL

除非结构化文档知识外，未来应单独建设**数据目录（Data Catalog）**，为大量业务表的治理、检索和 NL2SQL 候选召回提供基础。

数据目录不能只放在普通文档知识库中，也不应把所有字段塞在一张 JSON 大表中。建议以结构化目录作为权威事实，并可选建立独立向量索引用于召回。

### 8.1 表级元数据

建议实体：

```text
data_catalog_table
```

建议字段包括：

```text
数据库/schema/真实表名/中文名/业务域/表用途/行粒度/主键
状态/数据分级/是否含 PII/是否含金融敏感数据/负责团队
更新频率/来源系统/可读角色/nl2sql_enabled/query_guidance
创建与更新时间
```

表级描述应说明“每行代表什么”“适合用于什么”“不能用于什么”。

### 8.2 字段级元数据

建议实体：

```text
data_catalog_column
```

建议字段包括：

```text
表目录 ID/真实字段名/中文名/数据类型/是否可空/主外键
关联表字段/业务描述/语义类型/枚举映射/单位/脱敏示例
PII/敏感性/是否可过滤/是否可展示/NL2SQL 查询注意事项/展示顺序
```

例如字段说明必须能表达：客户风险等级不能替代产品风险等级；金额单位、状态枚举、时间字段口径等。

### 8.3 关系元数据

建议实体：

```text
data_catalog_relation
```

记录经过审核的 Join 关系：

```text
来源表/来源字段/目标表/目标字段
关系类型/Join 条件/业务说明/Join 指引
是否允许 NL2SQL 自动使用/风险等级/是否已验证
```

NL2SQL 不应自由猜 Join，只应从已审核关系中选择路径。

### 8.4 示例问题和审核 SQL

可选实体：

```text
data_catalog_example_query
```

保存经过审核的：

```text
自然语言问题/意图/目标表和字段/过滤条件/SQL 模板/所需权限/风险等级
```

用于测试集、受控 few-shot 和查询规划辅助，不能替代权限校验。

## 9. 数据目录与向量召回的关系

推荐方案：

```text
DataCatalogTable / Column / Relation 作为权威元数据
  → 生成 search_text 摘要
  → 存 embedding / 建独立 catalog 检索索引
  → NL2SQL 先召回候选表、字段、关系
```

向量索引只负责“找到可能相关的表、字段、关系”，不负责授权或直接执行 SQL。

不建议将内部数据目录摘要直接混入客户公共客服知识库；若需要复用当前知识 Pipeline，可建立独立 catalog 逻辑知识库/索引，并保持结构化元数据为源头事实。

## 10. 未来 NL2SQL 的受控流程

未来 NL2SQL 推荐采用：

```text
自然语言问题
  → 身份、角色、数据权限、业务意图判断
  → 召回候选表/字段/关系（向量 + 关键词 + 业务域过滤）
  → 用结构化数据目录确认表用途、字段口径、敏感性和已审核 Join
  → 生成受控 SQL
  → SQL AST 校验、白名单校验、行级权限、脱敏、LIMIT、成本限制
  → 只读执行与结果审计
```

最低安全要求：

- 默认仅允许 `SELECT`；
- 表、字段、关系必须白名单；
- 强制 `LIMIT` 和查询超时/成本限制；
- 强制客户、组织、角色等行级权限条件；
- 禁止模型自行猜测未审核 Join；
- 对 PII、金融敏感字段执行脱敏或拒绝；
- 记录问题、候选元数据、生成 SQL、执行者、结果摘要和审计事件。

普通客户客服 RAG 永远不应直接访问内部数据目录或执行 NL2SQL。

## 11. 分阶段路线

### 当前已实现

- 管理员知识文档上传界面和 API；
- TXT / MD / DOCX 解析入口；
- Outbox → Redis → Worker → pgvector 的自动入库 Pipeline 配置；
- 文档状态列表；
- 客服 RAG 的公共知识、低置信度工单和回答层代码骨架。

### 近期应做

1. 修复 Docker BuildKit 环境并重建 API / outbox-worker / knowledge-worker；
2. 真实验证上传文档从 `processing` 到 `active`；
3. 确认 `knowledge_chunk.embedding` 写入并能被检索；
4. 用上传的客服资料验证客服 RAG 命中、引用、Qwen 回答/安全降级；
5. 增加闲聊/问候分流，避免简单寒暄创建工单；
6. 将上传分类配置集中化，并增加“其他/自定义分类”的受控入口或先增加产品文档分类。

### 中期应做

1. 引入 `KnowledgeDomain`、命名逻辑库和统一 `KnowledgeDocumentUploadService`；
2. 将 `default` 演进为 `customer_public`；
3. 为产品资料建立 `product_evidence` 逻辑库、产品关联元数据和审核生效状态；
4. 为客服、投顾、合规建立集中定义的检索 Scope；
5. 建立最小 `data_catalog_table`、`data_catalog_column`、`data_catalog_relation` 数据目录骨架，并从 ORM/schema 自动初始化基础结构；
6. 增加后台只读“数据目录”页面，用于维护中文描述、敏感等级、查询指引和已审核关系。

### 远期应做

1. 为数据目录建立独立向量召回索引；
2. 建立受控 NL2SQL Planner 与 SQL AST/权限/成本校验；
3. 增加审核示例问题与 SQL 模板；
4. 在存在强隔离、规模或合规需求时，再评估逻辑库的物理拆分。

## 12. 客服/投顾的多来源路由与权威来源（2026-08-07 已确认）

知识文档、实时业务表、规则服务和操作接口可能描述同一业务主题，但职责不同。系统不采用“RAG 与数据库二选一”或“由模型自行选择来源”，而采用：

```text
事实 → 业务 API / PostgreSQL
决策 → 适当性、授权、风控、账户等业务服务
解释 → RAG 文档与引用
操作 → 受控业务 API、确认、状态机和审计
```

### 12.1 纯知识、纯事实和混合问题

| 类型 | 示例 | 首要来源 | 返回策略 |
|---|---|---|---|
| 纯知识 | 公司名称、客户规模、公司业务、FAQ、服务流程、公开政策 | RAG | 引用回答；不足时转人工 |
| 纯事实 | 订单状态、账户余额、资产、持仓、工单状态、产品实时可售状态 | 业务 API | 直接返回实时事实；不让 RAG 推断 |
| 事实+解释 | 为什么订单审核中、为什么不能赎回、为什么需要补件 | 业务 API + RAG | 先事实，再用 RAG 解释规则/下一步 |
| 受控建议/操作 | 产品推荐、下单、赎回、转账、风险处置 | 业务服务/API | 业务服务决定；RAG 只提供条款与证据 |

### 12.2 冲突与体验

实时数据优先于文档，已审核生效文档优先于普通文档，模型生成内容最低。业务事实必须先返回；RAG 解释慢、失败或无依据时，只降级解释，不得隐藏或延迟已授权的实时事实。

因此，后续 Router 先使用轻量规则/实体识别分流：问候直接回复、订单/账户/工单等走业务 API、公司/流程/FAQ 等走 RAG、原因类问题先查事实再决定是否补 RAG。这样既避免错误，也避免每个问题都触发 Embedding/模型调用。

> 本节为已确认的产品与架构边界；具体业务 API Router 仍待单独实现和回归，不在本轮直接改造客服现有接口。