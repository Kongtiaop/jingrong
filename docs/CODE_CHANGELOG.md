# 代码变更台账（CODE_CHANGELOG）

> 用途：记录“改了哪些代码、为什么改、如何验证”。
>
> - `PLAN.md` 记录计划与下一步；
> - `ISSUES.md` 记录问题、根因与修复状态；
> - `TESTS.md` 记录命令、断言和结果；
> - 本文只记录落到文件层面的实现变更，便于答辩、交接和回溯。
>
> 本文从 **2026-08-06** 开始持续维护；更早期的完整过程请结合 Git 历史、`PLAN.md`、`ISSUES.md` 和 `TESTS.md` 阅读。

---

## 2026-08-06：Docker API / AML Worker 职责拆分

### 目标

让数据库、Redis、API 可以通过 Compose 默认启动；AML Scheduler 保持独立、按 profile 显式启动，避免在线 API 与长任务共用同一容器入口。

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `Dockerfile` | 删除 | 旧文件默认启动 AML Scheduler，无法表达 API 容器职责。 |
| `Dockerfile.api` | 新增 | 固定入口 `uvicorn app.main:app --host 0.0.0.0 --port 8000`。 |
| `Dockerfile.aml-scheduler` | 新增 | 固定入口 `python -m workers.aml_scheduler`。 |
| `docker-compose.yml` | 修改 | 新增默认 `api` 服务；AML 服务改用 `Dockerfile.aml-scheduler`。 |
| `docker-compose.p0.yml` | 修改 | P0 环境新增 `api` 服务并接入 `wealth-p0` 网络。 |
| `docs/DEVELOPMENT_WORKFLOW.md` | 修改 | 记录一键启动 API/基础设施与 AML Worker 的命令。 |

### 验证

- 两套 Compose YAML 渲染通过；
- 关闭 BuildKit 后，`wealth-p0-api` 与 `wealth-p0-aml-scheduler` 镜像均构建成功；
- Windows Docker Desktop BuildKit 的中文路径管道问题见 `ISSUES.md` 的 `ISSUE-0349`。

---

## 2026-08-06：AML 批次补跑、迟到流水补扫与血缘展示

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/models/trading.py` | 扩展 AML 批次运行类型、来源批次、Scheduler 状态等模型 | 区分日批、失败客户补跑、迟到流水补扫并保留血缘。 |
| `app/services/daily_aml_scan_service.py` | 支持补跑/补扫稳定键及运行类型 | 保证重试幂等且不覆盖原批次。 |
| `app/api/risk.py` | 新增批次重试、迟到流水补扫、Scheduler 状态接口 | 让风控人员可操作并观察 AML 运行。 |
| `workers/aml_scheduler.py` | 心跳、扫描状态持久化 | 区分“未到扫描时间”和“Worker 已停止”。 |
| `frontend/src/views/RiskAlertsView.vue` | 批次补跑、补扫、运行类型/来源展示 | 提供 AML 后台操作入口和血缘可见性。 |
| `scripts/test_aml_*` | 新增/扩展 HTTP 与调度回归 | 固定幂等、权限、血缘和定时触发行为。 |

### 验证

详见 `TESTS.md` 的 AML Scheduler、失败补跑、迟到流水补扫相关章节。

---

## 2026-08-06：风险预警与工单导航闭环

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/src/views/RiskAlertsView.vue` | 风险预警关联工单跳转到 `/backoffice?section=workorders&work_order_id=...` | 工单不是独立 `/workorders` 路由。 |
| `frontend/src/views/BackofficeShellView.vue` | 读取 query 参数并激活工单标签 | 支持风险页跨模块导航。 |
| `frontend/src/views/WorkOrdersView.vue` | `focusWorkOrderId` watcher 首次执行 | 首次进入工单页时也能定位关联工单。 |

### 验证

风险管理“查看工单”浏览器现场验收已通过，详见 `TESTS.md`。

---

## 维护规则

每完成一个可交付步骤，追加：目标、改动文件、关键行为、验证证据和关联文档编号。不要在本文复制大段代码；代码细节以文件路径和 Git diff 为准。

## 2026-08-06：RAG 参考实现对照与客服/投顾双业务面规划

### 变更目标

将 `C:\simple_rag` 的成熟 RAG 解析、增量、混合检索和踩坑经验迁移到当前财富管理项目，同时明确 RAG 既服务客服，也服务投顾，但不能成为越权执行 Agent。

### 设计文档变更

| 文件 | 变更 |
|---|---|
| `docs/PLAN.md` | 新增 RAG 双业务面、Chunk 增量更新和实施顺序。 |
| `docs/DEFENSE_SYSTEM_DESIGN_AND_EVOLUTION.md` | 新增客服 RAG / 投顾 Evidence Service 边界，以及 simple_rag 迁移原则。 |

### 关键边界

- 客服 RAG：回答、引用、转人工/工单；不执行资金或风险操作；
- 投顾 RAG：只返回证据快照给建议草稿；证据不足不可转客户确认；
- Agent：只起草，不直接交易、转账、赎回、解限或结案；
- 事实源：继续使用 PostgreSQL + Outbox + Redis Streams，而不是迁移 simple_rag 的 SQLite 状态库。

## 2026-08-06：RAG Chunk 级向量复用

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/repositories/knowledge.py` | 新增 `active_chunk_embeddings_by_hash()` | 从当前 active 版本读取 `content_hash → embedding` 映射。 |
| `app/services/knowledge_ingestion.py` | 改为按 Chunk hash 复用向量 | 内容不变不再重新调用 Embedding；仅新增/变化 Chunk 重嵌入。 |
| `scripts/test_knowledge_chunk_incremental.py` | 新增内存仓储/假 Embedding 回归 | 验证首次全嵌入、重复全复用、局部变更局部嵌入。 |

### 验证结果

```text
首次入库：4 chunks，reused=0，embedded=4
内容不变：4 chunks，reused=4，embedded=0
局部变更：4 chunks，reused=3，embedded=1
```

## 2026-08-06：Chunk 边界重组不强行复用旧向量

本轮讨论补充了 Chunk 增量更新的安全边界：局部文本或向量相似只能用于候选分析，不能证明新 Chunk 与某个旧 Chunk 具有相同的语义边界。新 Chunk 若混合旧 A/旧 B 内容，必须重新 Embedding；只有稳定身份与完整 hash 匹配，或明确判定为内容移动，才允许复用向量。

客服检索可以使用相似度进行召回排序；投顾证据链必须额外保存完整引用和版本，不得因为高相似度跳过重新嵌入。

## 2026-08-06：LangChain 适配边界与 simple_rag 复用决策

### 设计结论

当前项目不重写已有 RAG 底座。LangChain 作为 `integrations/langchain` 适配/编排层，调用现有知识仓储和业务 Service；`C:\simple_rag` 的成熟解析、切分、增量、检索和测试思想按当前 PostgreSQL、Outbox/Redis Streams、RBAC、工单、投顾和风控体系重新适配。

### 业务隔离

客服 RAG 与投顾 Evidence Service 共用文档/版本/Chunk/向量底座，但拥有不同的权限过滤、引用阈值和输出状态；客服可转工单，投顾可写建议证据快照，二者都不能直接执行资金或风险操作。

## 2026-08-06：修正客服 RAG 与产品推荐边界

本轮明确客服 Agent 仍然是 RAG 使用方。暂时绕过 RAG 的只有“产品推荐决策”这一意图，因为当前缺少完整、权威、版本化的产品文档；公司信息、公司业务、FAQ、流程、金融政策和资料规则仍通过客服 RAG 回答。

产品推荐使用实时 Product 数据、客户画像和 SuitabilityService；未来通过 `ProductEvidenceProvider` 可选补充产品文档引用，不改变实时 Product 事实和硬规则。

## 2026-08-06：确立客服/投顾产品推荐与 RAG 的当前边界

### 代码与职责决策

当前推荐不依赖产品 RAG：客服和投顾均使用实时 Product 数据与 SuitabilityService；客服其他知识咨询仍使用 RAG。未来通过 `ProductEvidenceProvider` 接入产品文档证据，不重写推荐服务。

### 下一步代码

新增 `WealthKnowledgeRetriever` 适配层，内部调用当前 `KnowledgeRepository.search_hybrid()`，为客服 RAG 和未来投顾 Evidence Service 提供统一检索接口；该适配层不绕过 RBAC、业务 API、订单和风控服务。

## 2026-08-06：WealthKnowledgeRetriever 与可选 LangChain 适配层

### 代码变更

| 文件 | 变更 |
|---|---|
| `app/services/knowledge_retrieval.py` | 提供统一 `KnowledgeCitation` 和应用层 `WealthKnowledgeRetriever`。 |
| `app/integrations/langchain/langchain_retriever.py` | 新增可选 `LangChainWealthRetriever`；LangChain 未安装时不影响核心检索。 |
| `scripts/test_wealth_knowledge_retriever.py` | 新增文本检索、混合检索和引用元数据测试。 |

### 边界

LangChain 包装当前应用 Retriever，不替换 PostgreSQL/pgvector、权限过滤、版本和业务 Service。

## 2026-08-06：知识库权限 Scope 贯穿检索

本轮将 `KnowledgeScope` 接入当前知识仓储和 Retriever：knowledge_base、category、permission_level 由后端 Scope 强制过滤，客服/投顾业务分别生成 Scope，前端不能伪造内部权限。该实现借鉴 simple_rag 的 metadata filter 思想，但继续使用当前 PostgreSQL/pgvector 与成熟业务权限体系。

## 2026-08-06：KnowledgeScope 模块边界修复

| 文件 | 变更 |
|---|---|
| `app/common/knowledge_scope.py` | 新增无业务依赖的 Scope 值对象。 |
| `app/services/knowledge_scope.py` | 改为兼容导出，避免旧导入路径破坏。 |
| `app/repositories/knowledge.py` | 改从 common 层导入 Scope，消除循环依赖。 |
| `app/services/knowledge_retrieval.py` | 同步使用 common Scope。 |
| `scripts/test_knowledge_scope.py` | Scope 隔离和模块导入回归通过。 |

## 2026-08-06：客服 RAG Service 初始接入

| 文件 | 变更 |
|---|---|
| `app/services/customer_rag_service.py` | 新增客服意图 Scope、最低分门槛、引用结果和转人工状态。 |
| `app/services/knowledge_retrieval.py` | Retriever 支持透传 KnowledgeScope。 |
| `scripts/test_customer_rag_service.py` | 验证公司业务 RAG 与产品数据库推荐边界。 |

## 2026-08-06：客服 RAG API 接入

| 文件 | 变更 |
|---|---|
| `app/schemas/knowledge.py` | 新增客服 RAG 请求/响应契约。 |
| `app/api/customer_rag.py` | 新增 `/api/v1/customer-rag/answer`，返回引用、证据状态和转人工标记。 |
| `app/api/__init__.py` | 注册客服 RAG 路由。 |

当前 API 是检索结果层，不直接调用 LLM 生成文本；后续 Chain 只负责自然语言组织，不改变 Scope、Product 或业务执行边界。

## 2026-08-06：客服 RAG 低置信度转人工工单

| 文件 | 变更 |
|---|---|
| `app/models/workorder.py` | 新增 `context_json`，保存 RAG 转人工上下文。 |
| `app/schemas/workorder.py` | 工单响应返回上下文。 |
| `app/services/customer_rag_handoff.py` | 新增创建/复用客服工单、通知和幂等键逻辑。 |
| `app/api/customer_rag.py` | 低置信度时返回工单号和是否新建。 |
| `app/schemas/knowledge.py` | 客服 RAG 响应增加工单字段。 |
| `scripts/test_customer_rag_handoff_contract.py` | 查询规范化幂等键契约。 |

## 2026-08-06：客服 RAG 低置信度工单真实闭环

| 文件 | 变更 |
|---|---|
| `app/services/knowledge_retrieval.py` | 完整修复 Scope 参数签名与 Repository 透传。 |
| `scripts/test_customer_rag_handoff_http.py` | 新增真实 HTTP 工单创建、复用、上下文、更新和产品边界回归。 |
| `docker-compose.p0.yml` 运行的 API 容器 | 重建以加载客服 RAG 路由及最新 Retriever。 |

真实回归全部通过。

## 2026-08-06：客服 RAG 引用回答层

| 文件 | 变更 |
|---|---|
| `app/services/customer_rag_answer.py` | 新增只基于引用生成中文客服回答的 Provider，模型不可用时安全降级。 |
| `app/api/customer_rag.py` | 返回 `answer` 与 `generation_status`。 |
| `app/schemas/knowledge.py` | 客服 RAG 响应增加回答字段。 |
| `scripts/test_customer_rag_answer.py` | 覆盖低置信度和模型不可用降级。 |

产品推荐路径保持 Product + SuitabilityService，不经过该回答层。

## 2026-08-06：客户智能客服前端入口

| 文件 | 变更 |
|---|---|
| `frontend/src/api/customer-rag.ts` | 新增客服 RAG API 客户端。 |
| `frontend/src/views/CustomerRagView.vue` | 新增客户客服问答页面，展示回答、引用和转人工工单号。 |
| `frontend/src/main.ts` | 注册 `/customer-center/service` 路由。 |
| `frontend/src/views/CustomerBackofficeView.vue` | 客户中心增加“智能客服”导航和快捷入口。 |

产品推荐意图未暴露在客服页面选项中，仍由现有产品/适当性投资流程处理。

## 2026-08-06：全局智能客服气泡入口

| 文件 | 变更 |
|---|---|
| `frontend/src/styles.css` | 新增全局客服气泡、展开面板、消息、引用、移动端和动画样式。 |
| `docs/PLAN.md` | 记录当前入口现状、验证结果和下一步。 |

`App.vue` 已在 `RouterView` 外挂载 `CustomerServiceBubble`，因此路由切换时保持全局入口；当前显示范围仍是已登录客户。客服页面 `/customer-center/service`、客户中心导航和 RAG API 已存在。本次 `npm run build` 因 Windows 沙箱 Vite `spawn EPERM` 未完成，需在允许子进程的环境重跑。

## 2026-08-06：客服气泡入口构建验收

| 项目 | 结果 |
|---|---|
| `npm run build` | PASS，生产构建成功。 |
| `vue-tsc --noEmit` | 工具链未完成：临时 `vue-tsc@3.3.9` 与当前 TypeScript exports 不兼容，报 `ERR_PACKAGE_PATH_NOT_EXPORTED`。 |
| 当前结论 | 全局客服气泡代码已通过 Vite 生产构建，进入运行中 API 和浏览器验收。 |

构建仅有既有 `auth.ts` 动态/静态导入分包提示，不影响构建结果。

## 2026-08-06：客服现状核查——工单、闲聊和大模型

真实调用运行中的 `/api/v1/customer-rag/answer` 发现：`你好`、`你是谁`、公司客服电话等问题均因低于 `min_score=0.45` 进入转人工并创建工单；产品推荐意图不创建工单，但当前返回的是边界提示。

结论：系统不是每句话无条件创建工单，而是当前知识库未命中时统一转人工；尚未实现闲聊节点。代码中已有 Qwen Provider 和回答层，但低置信度会在生成前直接返回固定文案，且运行 API 响应未返回 `answer/generation_status`，需重启/重建 API 加载最新代码后再验证模型是否实际调用。

下一步：先实现 greeting 闲聊分流，再修复运行版本并做 Qwen 回答层真实 HTTP 回归。

## 2026-08-06：管理员知识库上传与自动向量化 Pipeline

| 文件 | 变更 |
|---|---|
| `app/api/knowledge.py` | 新增管理员文档上传、文档列表接口；限制 TXT/MD/DOCX、20MB 和 UTF-8 文本编码。 |
| `app/repositories/knowledge.py` | 文档创建返回 Outbox 事件 ID；新增文档列表查询。 |
| `app/services/knowledge_ingestion.py` | 增加 DOCX 解析，并将 parser 类型写入版本/Chunk 元数据。 |
| `frontend/src/views/KnowledgeBaseView.vue` | 新增后台上传、分类/可见范围选择、Pipeline 说明和处理状态列表。 |
| `frontend/src/api/knowledge.ts` | 新增 multipart 上传与文档状态 API 客户端。 |
| `frontend/src/views/BackofficeShellView.vue` | 增加“知识库管理”后台菜单。 |
| `docker-compose*.yml` | 增加 Outbox/Knowledge Worker 和上传文件共享卷。 |
| `requirements.txt` | 增加 `python-docx`。 |
| `scripts/test_knowledge_admin_upload_contract.py` | 新增上传排队、列表可见和格式拒绝的 HTTP 回归脚本。 |

前端生产构建和 Python 语法检查均通过。运行中容器仍是旧版本，待 Docker 环境解决 BuildKit header 错误并重建 API/Worker 后完成真实向量化验收。

## 2026-08-06：知识治理、检索 Scope 与未来数据目录/NL2SQL 架构共识

新增 `docs/KNOWLEDGE_AND_DATA_CATALOG_ARCHITECTURE.md`，完整记录本轮关于以下主题的架构讨论与分阶段计划：

- 当前知识库中“逻辑库、资料分类、文档元数据、检索 Scope”的区别；
- 新类型资料（特别是产品信息文档）的上传、审核、可见性和检索边界；
- 统一上传入库抽象：`KnowledgeDomain`、`KnowledgeIngestionCommand`、`KnowledgeDocumentUploadService`；
- 客服、投顾、合规等不同检索方的 Scope 路由；
- 产品推荐仍必须由 Product + SuitabilityService 决定，文档 RAG 只提供解释和证据；
- 独立的结构化数据目录：`data_catalog_table`、`data_catalog_column`、`data_catalog_relation`、`data_catalog_example_query`；
- 以数据目录为权威、以向量索引为候选召回的未来 NL2SQL 受控流程；
- 当前、近期、中期和远期实施顺序。

该文档明确：当前阶段保持“同一套知识基础设施 + 逻辑库/分类/权限隔离”，不立即拆分物理向量库；NL2SQL 仅作为未来受控能力，普通客服不得直接访问内部数据目录或执行 SQL。

## 2026-08-06：Docker BuildKit / Compose 运行环境修复

| 项目 | 结果 |
|---|---|
| 中文路径 BuildKit gRPC header 错误 | 通过 `subst W:` 映射为 ASCII 路径规避，验证有效。 |
| API 镜像构建 | PASS。 |
| API / Outbox / Knowledge Worker | PASS，均在运行。 |
| Worker DNS | 修复 Compose 网络配置后 PASS，可解析 `postgres`、`redis`。 |
| Qwen/Embedding | Compose 注入配置后 PASS，`text-embedding-v4` 返回 1024 维向量。 |
| 上传回归 | `test_knowledge_admin_upload_contract.py` PASS。 |
| 异步入库 | 文档由 `processing` 变为 `active`，Worker 日志显示 embedding 完成。 |
| 向量检索 | `/knowledge/search` PASS，能召回上传文档。 |

本次未删除 PostgreSQL、Redis、Milvus 数据卷。日常操作应从 ASCII 映射盘符 `W:\` 执行 Compose。

## 2026-08-06：Docker 当前工作区重建、AML 修复与 MySQL migration 整理

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/daily_aml_scan_service.py` | 修复 AML 扫描循环中混入字面量 `` `n `` 的语法错误 | 让 `Dockerfile.aml-scheduler` 构建后的 Worker 能正常导入并启动 |
| `docker-compose.yml` | 将 `mysql` 服务改名为 `mysql-migration`；关闭自动重启；增强健康检查；增加 `wealth-migration` 独立网络 | 明确 MySQL 仅为迁移/兼容验证库，避免与 PostgreSQL 主业务事实库混淆 |
| `docs/PLAN.md` | 记录本轮交付和后续执行顺序 | 保持计划台账同步 |
| `docs/ISSUES.md` | 新增 ISSUE-0362，记录旧 Compose 工作区、AML 语法错误和迁移库冲突 | 保持问题根因与闭环可追溯 |
| `docs/TESTS.md` | 追加 Docker、AML、MySQL migration 实际命令与结果 | 保持测试证据同步 |

### 验证

- `Dockerfile.api` 和 `Dockerfile.aml-scheduler` 构建成功；
- API、Outbox Worker、Knowledge Worker、AML Scheduler 均运行；
- API health HTTP 200；
- MySQL migration healthy，3307 端口可用；
- `scripts.mysql_migration_probe` PASS。
## 2026-08-06：Data Catalog 第一阶段（未来 NL2SQL 的受控检索地基）

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/models/data_catalog.py` | 新增领域、表、字段、关系、示例问题五类目录模型及 pgvector 字段 | 将业务 Schema 语义从普通文档知识库中独立出来 |
| `migrations/versions/0017_data_catalog.py` | 新增 Catalog 表、索引和 HNSW 向量索引 | 提供可迁移的 PostgreSQL 目录底座 |
| `app/services/data_catalog_seed.py` | 新增核心表、字段、关系和典型问题的幂等种子 | 首期可验证相似表筛选和最小 Schema Context |
| `app/repositories/data_catalog.py` | 新增按角色过滤的中文关键词/别名目录检索 | 在向量化前先保证语义目录和权限边界可用 |
| `app/services/data_catalog_service.py` | 组合表、字段、关系、示例问题并显式关闭 SQL 执行 | 输出未来 NL2SQL 所需上下文而不触发 SQL |
| `app/api/data_catalog.py`、`app/schemas/data_catalog.py` | 新增内部 Catalog 检索 API 与响应契约 | 提供后台/后续 Agent 的受控入口 |
| `app/main.py`、`app/db/schema.py`、`migrations/env.py` | 注册模型、启动种子和 HNSW 兼容索引 | 保证本地启动和迁移链路一致 |
| `scripts/test_data_catalog_contract.py`、`scripts/test_data_catalog_http.py`、`scripts/seed_data_catalog.py` | 新增服务契约、HTTP 权限回归和手动种子脚本 | 留存可复现验证入口 |

### 关键边界

- 这是 **数据目录检索**，不是 NL2SQL 执行器；
- 当前返回 `nl2sql_ready=false`；
- 只允许工作人员管理员权限读取目录，普通客户被拒绝；
- 向量字段和 HNSW 索引已创建，但异步 Embedding 召回、SQL 生成和 SQL 执行均未开启；
- PostgreSQL 继续是唯一业务事实库，MySQL migration 不进入 Catalog/NL2SQL 主链路。

### 验证

- Catalog 模型/迁移/服务语法：PASS；
- 目录种子：PASS；
- 管理员 HTTP 检索 `客户资产趋势`：召回 `customer_asset_snapshot` 和 `net_asset`；
- 普通客户访问目录 API：403；
- API 容器重建后健康检查：PASS。
## 2026-08-06：Data Catalog 第二阶段——治理后台、版本记录、异步向量化与混合检索

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/models/data_catalog.py`、`migrations/versions/0018_data_catalog_revisions.py` | 新增目录修订快照表 | 表/字段语义可审计、可回溯 |
| `app/repositories/data_catalog.py` | 增加治理更新、修订记录、Embedding 队列、pgvector 查询与混合检索支撑 | 同时满足后台治理和未来最小 Schema Context 召回 |
| `app/api/data_catalog.py`、`app/schemas/data_catalog.py` | 新增表/字段维护、版本读取、异步向量化触发 API | 建立受权限保护的治理入口 |
| `app/services/data_catalog_embedding.py`、`workers/data_catalog_worker.py` | 新增单条写回的 Catalog Embedding Worker | 外部 Embedding 抖动时避免整批回滚 |
| `workers/outbox_worker.py`、`docker-compose*.yml` | 新增 `data_catalog.* → catalog stream` 路由及 Worker 服务 | 复用 Outbox/Redis 异步基础设施 |
| `app/services/data_catalog_service.py` | 新增关键词+pgvector 混合检索、8 秒可选向量超时与文本降级 | 保持目录检索的实时可用性 |
| `frontend/src/views/DataCatalogView.vue`、`frontend/src/api/data-catalog.ts`、`BackofficeShellView.vue`、`styles.css` | 新增目录治理页面、版本提示、向量化触发按钮和后台入口 | 让目录元数据不再只能靠代码种子维护 |

### 关键边界

- 目录向量化只覆盖可查询表、可查询字段和批准示例问题；
- `audit_logs` 等不可查询字段不进入向量召回；
- 混合检索只是未来 NL2SQL 的 Schema Context 选择器；
- 仍未开放 SQL 生成和执行。

### 验证

- 后台治理 HTTP 回归 PASS；
- Outbox → Redis → Catalog Worker → pgvector PASS；
- 外部 Embedding 可用时检索返回 `catalog-hybrid-text-pgvector`；网络波动时在 8 秒内降级为 `catalog-text-fallback` 并保持正确表召回；
- API health PASS。
## 2026-08-07：客服 RAG 问候/闲聊分流，停止推进 NL2SQL

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/customer_rag_service.py` | 新增问候、身份询问、感谢/结束语识别与固定安全话术 | 防止简单闲聊因低置信度产生无效工单 |
| `scripts/test_customer_rag_service.py` | 增加问候绕过 Retriever 的服务契约 | 保证闲聊不进入知识检索 |
| `scripts/test_customer_rag_greeting_http.py` | 新增客户真实 HTTP 回归 | 验证无 citation、无 handoff、无工单 |
| `docs/PLAN.md` | 明确 Data Catalog 仅保留为运行基础，暂停 NL2SQL | 与当前开发方向保持一致 |

### 验证

- 客户输入“你好”返回 `answer_mode=greeting`；
- 不创建工单、不调用 Retriever/Embedding；
- API HTTP 200，约 35ms；
- 正式知识问答和产品推荐边界未改动。
## 2026-08-07：前端 Vite 代理运行规范补充

| 文件 | 变更 | 原因 |
|---|---|---|
| `docs/DEVELOPMENT_WORKFLOW.md` | 新增前端 Vite 启动、访问地址、代理健康检查和 Network Error 排查说明 | 避免将前端代理未启动误判为 Docker API 故障 |
| `docs/ISSUES.md` | 新增 ISSUE-0366 | 记录登录、用户读取、上传统一 Network Error 的根因和恢复步骤 |
| `docs/TESTS.md` | 追加 Vite 代理、登录、用户读取和知识库列表真实验证 | 留存可复现运行证据 |
## 2026-08-07：后台工作台尺度与 Data Catalog 字段对齐优化

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/src/styles.css` | 增加后台桌面工作台尺度、侧栏、内容区、表格、Catalog 左右栅格、三列表单、字段行和响应式断点覆盖 | 减少页面留白，扩大后台可用画布并修正治理页字段/按钮不齐 |

### 验证

- `npm run build` PASS；
- 生产构建包含更新后的 CSS；
- 后续仍需浏览器逐页巡检，确认具体业务页的数据密度与对齐效果。
## 2026-08-07：知识上传自动跟踪与客服 RAG 常见问法召回增强

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/src/views/KnowledgeBaseView.vue` | 增加 processing 文档自动轮询、实时处理提示、最近刷新时间、文档阶段说明 | 改善上传后必须手动刷新才能看到向量化结果的问题 |
| `frontend/src/styles.css` | 增加知识库自动跟踪状态样式 | 提高 Pipeline 状态可见性 |
| `app/services/customer_rag_service.py` | 增加公司名称、客户规模、联系方式、公司业务等规则式查询归一化 | 解决口语问法与文档表达不同导致的低置信度漏召回 |
| `app/repositories/knowledge.py` | 改进词法检索查询和多关键词匹配 | 给向量检索增加可控关键词补偿 |
| `scripts/test_customer_rag_service.py` | 增加归一化契约 | 防止后续回退为原始整句检索 |

### 验证

- 三种真实客户问法均成功返回引用回答；
- 上传页自动状态跟踪通过前端生产构建；
- 低置信度、Scope、产品推荐和问候边界未改变。
## 2026-08-07：客服 Agent MVP 验收归档与 RAG/业务事实边界讨论立项

本轮无业务代码改动。

- 客服 Agent MVP 浏览器验收结论已归档：问候分流、知识引用、低置信度转人工、工单可见性和上传 Pipeline 可演示；
- 新增 `ISSUE-0368`：记录 RAG 文档与后端业务事实同时存在时的权威来源、路由优先级和冲突处理问题；
- 在形成架构决策前，不将产品推荐、实时产品状态、订单/账户/风险/工单事实交给普通 RAG 决策。

---

## 2026-08-07：客服受控 Query Router、自动意图、多任务执行与记忆基础

### 目标

将客户客服入口从“前端手选单一 intent + 单次 RAG”逐步升级为：实时业务事实优先、规则优先自动意图识别、多意图任务执行、有限澄清和 Redis/PG 分层记忆。

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/customer_query_router.py` | 新增客户本人事实路由 | 订单、工单、资产、风险限制和产品状态必须优先读取受权限保护的实时数据。 |
| `app/api/customer_rag.py` | 新增 `/customer-rag/route` | 为旧客服入口提供事实查询契约与 RAG 回退标识。 |
| `app/services/customer_intent.py` | 新增 `CustomerIntentPlanner` | 支持规则优先的自动识别、多个任务拆解、明确转人工和未知问题澄清。 |
| `app/services/customer_task_executor.py` | 新增任务执行器 | 让独立只读任务并行、依赖任务串行，并返回逐任务状态。 |
| `app/services/customer_tools.py` | 新增受控 Read / Explanation Tool 外观 | 不让 Agent 直接访问 Repository 或原样暴露业务 API。 |
| `app/services/memory_service.py` | 新增短期记忆和长期记忆服务 | Redis 保存当前上下文；PG 保留确认事实和记忆事件。 |
| `app/models/memory.py`、`migrations/versions/0020_customer_memory.py` | 新增长期记忆模型和迁移 | 为后续补参、多轮澄清与审计留档建立持久化基础。 |
| `app/schemas/customer_service.py` | 扩展任务、执行结果、消息 Schema | 固定前后端和 Agent 之间的结构化契约。 |
| `scripts/test_customer_*` | 新增意图、任务、记忆、Query Router 和 RAG Explanation HTTP 回归 | 覆盖客户数据范围、部分成功、澄清和工具契约。 |
| `frontend/src/api/customer-rag.ts` | 扩展客服任务、消息 API 客户端 | 客户端统一调用执行计划和实时会话接口。 |
| `frontend/src/views/CustomerRagView.vue`、`frontend/src/components/CustomerServiceBubble.vue` | 切换至 `execute-plan` | 前端保存 `conversation_id`，展示任务级结果、澄清和 handoff。 |

### 关键行为

```text
客户问题
  → IntentPlan
  → Read Tool / Explanation Tool
  → TaskResult 聚合
  → Redis 短期状态
  → 低置信度时转人工
```

默认只允许一次自动澄清；无法继续时返回已完成部分、最小澄清问题或人工兜底，不进行无限多轮检索。

### 验证

- `test_customer_query_router_http.py`：客户数据范围与 RAG 回退通过；
- `test_customer_intent_planner.py`：多意图、人工和澄清通过；
- `test_customer_memory_intent_http.py`：短期上下文和 Read Tool 契约通过；
- `test_customer_task_execution_http.py`：多任务执行和一次澄清通过；
- `test_customer_rag_explanation_tool.py`：RAG Explanation Tool 路径通过。

---

## 2026-08-07：实时人工客服接管、多人分派与消息闭环

### 目标

将原有异步 `WorkOrder` 转人工补齐为可接入、可占用、可退出的实时人工客服会话；明确 AI 暂停和多客服并发边界。

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/models/customer_service.py` | 新增会话和消息 ORM | 工单与实时会话分离；保存坐席、状态、消息发送者。 |
| `migrations/versions/0019_customer_service_session.py` | 新增会话/消息表及部分唯一索引 | 保证同一客服同一时刻最多一个 `human_active` 会话。 |
| `app/api/customer_service.py` | 新增会话、claim、assign、暂停、转交、结束、关闭和消息 API | 固化客服接入、退出、消息归属和权限边界。 |
| `app/services/auth_seed.py` | 新增 `customer_service:accept` / `customer_service:assign` | 将实时客服接入与普通工单权限分离。 |
| `app/services/customer_rag_handoff.py` | 扩展为 WorkOrder + CustomerServiceSession 协调入口 | RAG 低置信度和客户主动人工请求统一创建/复用工单及等待会话。 |
| `app/api/customer_service.py` 的 `execute-plan` | 增加活动人工会话门禁 | `waiting_human`、`human_active`、`paused` 时 AI 不再自动执行。 |
| `scripts/test_customer_service_session_http.py` | 新增会话并发/退出回归 | 验证 claim、暂停、关闭、客户退出和并发单赢家。 |
| `scripts/test_customer_service_messages_http.py` | 新增消息和 AI 暂停回归 | 验证客户/客服消息权限、接入后回复和 AI 门禁。 |
| `frontend/src/views/CustomerServiceDeskView.vue` | 新增客服工作台 | 提供待接入队列、claim、轮询、回复、暂停、转交、结束。 |
| `frontend/src/views/BackofficeShellView.vue` | 增加实时客服菜单入口 | 仅对实时客服接入权限可见。 |
| `frontend/src/views/CustomerRagView.vue`、`frontend/src/components/CustomerServiceBubble.vue` | 增加人工状态、消息轮询、客户结束入口 | 客户在同一入口看到人工接待进度和消息。 |
| `frontend/src/styles.css` | 增加客服工作台/消息 UI 样式 | 支持桌面双栏和移动端回退布局。 |

### 关键行为

```text
RAG 低置信度 / 客户请求人工
  → WorkOrder（异步任务）
  + CustomerServiceSession(waiting_human)
  → 客服 claim
  → human_active
  → AI 暂停
  → 客户 / 客服消息 API + 2.5 秒轮询
  → pause / release / resolve / close
```

### 验证

- `test_customer_service_session_http.py`：会话创建、claim、坐席占用、暂停、恢复、关闭、并发 claim 通过；
- `test_customer_service_messages_http.py`：消息归属、客服接入后回复、客户读取和 AI 暂停门禁通过；
- `npm run build`：客服工作台和客户客服页面生产构建通过。

---

## 2026-08-07：Playwright、异步 Factory 与异常/恢复测试基础

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/package.json`、`frontend/package-lock.json` | 增加 `@playwright/test` 与 E2E 命令 | 建立浏览器级测试入口。 |
| `frontend/playwright.config.ts` | 新增官方浏览器双项目配置 | 使用 Chrome for Testing 与本机 Google Chrome channel。 |
| `frontend/e2e/*.spec.ts` | 新增登录和双 BrowserContext smoke | 为客户/客服双端接管 E2E 奠基。 |
| `tests/factories/users.py` | 新增异步用户 Factory | 减少集成测试构造账号的重复代码。 |
| `tests/factories/customer_service.py` | 新增实时客服会话/消息 Factory | 统一创建等待会话和消息夹具。 |
| `tests/chaos/test_memory_faults.py` | 新增 Redis 故障注入测试 | 验证短期记忆不静默吞异常。 |
| `tests/chaos/test_fault_injection_contract.py` | 新增数据库故障注入测试 | 验证 Tool 层不吞数据库异常。 |
| `tests/chaos/test_recovery_contract.py` | 新增 Redis 瞬时故障恢复测试 | 验证故障后短期记忆服务可恢复。 |

### 验证

- `pytest tests/chaos -q`：3 passed；
- `npm run build`：PASS；
- `npm run e2e -- --list`：已发现 2 个文件、2 个官方浏览器项目、4 个测试。

完整客户/客服浏览器 E2E 仍待独立前后端服务和可清理的 E2E 测试数据环境。


## 2026-08-07：客服 Agent heartbeat 迁移与超时 sweep 回归

- 执行数据库迁移时发现数据库 `alembic_version` 停留在 `0009_aml_batch`，但 0010-0020 对应表已由既有运行时初始化创建；直接 `alembic upgrade head` 会在 `work_order` 等已存在表上重复建表。
- 已按现有数据库实际结构将迁移记录对齐到 `0020_customer_memory`，确认 `agent_last_heartbeat_at` 列和索引已经存在后，将版本记录完成到 `0021_customer_service_heartbeat`。
- 当前数据库 Alembic 状态：`0021_customer_service_heartbeat (head)`。
- 已验证新 ORM/API 可正常访问 heartbeat 字段。
- 新增真实超时测试：创建 `human_active` 会话，手动将 heartbeat 设置为过去时间，调用 `sweep-timeouts`，确认会话变为 `paused`、`assigned_agent_id` 清空、heartbeat 清空，随后客服可以重新 claim。

## 2026-08-07：客服 heartbeat 迁移问题处理完成

根因不是业务代码，而是数据库迁移记录与运行时已创建 schema 不一致：数据库版本停留在 0009，但后续表已存在。处理方式是基于现有 schema 对齐 Alembic 版本，并只执行缺失的 heartbeat 迁移结构；未删除任何业务表或测试数据。

关联文件：

- `migrations/versions/0021_customer_service_heartbeat.py`
- `scripts/test_customer_service_heartbeat_http.py`
- `scripts/test_customer_service_heartbeat_timeout_http.py`
- `docs/TESTS.md`
- `docs/ISSUES.md`
- `docs/PLAN.md`

当前状态：已解决。后续可继续补充定时任务自动调用 sweep，以及断线/网络故障恢复测试。

## 2026-08-07：客服 heartbeat 超时后台 Worker 接入

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/customer_service_timeout.py` | 新增可复用超时 sweep Service | API 和后台 Worker 共享同一暂停、释放坐席、清空 heartbeat、审计逻辑。 |
| `app/api/customer_service.py` | `sweep-timeouts` 改用 Service | 避免 API 与 Worker 两套超时规则漂移。 |
| `workers/customer_service_timeout_worker.py` | 新增后台轮询 Worker | 定期扫描过期人工会话，无需人工调用管理员 API。 |
| `app/core/settings.py` | 新增 Worker 开关、轮询间隔和 heartbeat 超时配置 | 支持按环境控制后台任务。 |
| `.env.example` | 增加客服超时 Worker 配置示例 | 明确本地启动参数。 |
| `docker-compose.p0.yml` | 新增 `customer-service-timeout-worker`，profile 为 `customer_service` | 与 API/AML Scheduler 职责分离，可显式启停。 |
| `scripts/test_customer_service_timeout_worker.py` | 新增 Worker Service 集成回归 | 验证 Worker 执行 sweep 后会话暂停并释放坐席。 |

### 运行方式

```powershell
docker compose -f docker-compose.p0.yml --profile customer_service up -d --build customer-service-timeout-worker
docker compose -f docker-compose.p0.yml logs -f customer-service-timeout-worker
```

默认每 30 秒执行一次，heartbeat 超时默认为 90 秒。

## 2026-08-07：客服 heartbeat Worker 真实自动超时与重启恢复完成

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `scripts/test_customer_service_worker_realtime.py` | 新增真实等待 35 秒的 Worker 自动 sweep 测试 | 验证不是直接调用 Service，而是由运行中的 Docker Worker 自动暂停过期会话。 |
| `docker-compose.p0.yml` | 保持客服超时 Worker 独立 profile | 允许单独重启和观察恢复。 |

### 验证

真实测试确认：过期 heartbeat 会话被 Worker 自动暂停并释放坐席；重启容器后 Worker 能继续启动并执行 sweep。

## 2026-08-07：前端 heartbeat 断线/自动暂停状态提示补强

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/src/views/CustomerServiceDeskView.vue` | 增加连接状态 `connected/recovering/paused`，heartbeat 失败后刷新会话并提示自动暂停 | 让客服明确区分网络恢复中与 Worker 已暂停。 |
| `frontend/src/views/CustomerRagView.vue` | 增加人工会话轮询连接状态提示 | 客户侧消息轮询失败时显示恢复中，而不是静默吞错。 |

### 验证

`npm run build` 通过。沙箱内首次构建因 Vite 子进程 `spawn EPERM` 失败，使用授权环境重试后成功。

## 2026-08-07：客服断线恢复浏览器 E2E

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/e2e/customer-service-live.spec.ts` | 增加 heartbeat API 失败注入的浏览器测试 | 验证客服工作台在断线后仍能维持页面状态并恢复轮询/heartbeat。 |

验证结果：Chrome for Testing 真实数据模式 4 项全部通过。

## 2026-08-07：客服浏览器 offline/online 恢复 E2E

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/e2e/customer-service-live.spec.ts` | 增加浏览器上下文 offline 模式测试 | 真实模拟浏览器断网、恢复网络，并验证客服页面恢复。 |

验证：Chrome for Testing + `E2E_REAL_DATA=1` 下 5 项全部通过；前端生产构建通过。

## 2026-08-07：客服实时接管与 heartbeat 阶段性完成归档

### 归档结论

本阶段已完成客服实时接管、heartbeat、自动超时 Worker、断线恢复和浏览器 E2E 验证。当前代码已具备进入下一项业务功能开发的条件。

### 关键验证

- 数据库版本：`0021_customer_service_heartbeat (head)`；
- 真实 Worker 能自动暂停过期人工会话；
- Worker 重启后能继续运行；
- 客服端 heartbeat/API 断线与浏览器 offline/online 恢复测试通过；
- Chrome for Testing 真实数据模式：5 项通过；
- 前端生产构建通过。

### 后续非阻塞事项

页面自动暂停后的纯页面重新 claim、深度故障注入、通知联动和迁移基线治理，待下一专项或发布前门禁处理。

## 2026-08-07：下一阶段通知联动方向立项

本轮无代码文件变更，记录产品方向评估结果。

决定优先开发“通知中心与实时客服会话联动”，原因是现有 Notification、CustomerServiceSession、CustomerServiceMessage 和 WorkOrder 基础已具备，补齐事件通知可以直接形成客户—客服—会话—工单闭环。

推荐事件范围：

- customer_service_session_created；
- customer_service_session_claimed；
- customer_service_message_created；
- customer_service_session_paused/released/resolved/closed；
- customer_service_session_timeout_paused。

后续实现时必须同步记录通知幂等、权限范围、跳转目标、未读计数和 HTTP/Playwright 验证。

## 2026-08-08：客服第一版范围冻结，研发重点转向意图识别

本轮无代码文件变更，仅记录产品范围决策。

客服模块冻结为最小可用闭环：公共队列、客服 claim、并发冲突保护、暂停/释放/结束、heartbeat 超时释放和消息闭环。暂不新增指定客服转接、技能组、复杂自动路由或多客服协作。

下一阶段优先开发意图识别、多意图任务拆分、实时业务事实查询、任务执行和 RAG/人工兜底衔接。

## 2026-08-08：意图任务统一整体状态与部分成功第一阶段完成

### 实现

- 在 `CustomerTaskExecutor` 增加统一 `summarize` 聚合函数；
- `execute-plan` 改为使用统一聚合结果，不再在 API 层散落计算；
- 固定当前整体状态：`completed`、`partially_completed`、`needs_clarification`、`handoff`、`failed`、`blocked_by_human`；
- 全部任务成功返回 `completed`；
- 至少一个成功且存在澄清/失败返回 `partially_completed`；
- 只有澄清任务返回 `needs_clarification`；
- 任务失败返回 `failed`；
- 计划明确要求人工时返回 `handoff`；
- 聚合响应保留各任务结果和首个澄清建议。

### 验证

新增 `scripts/test_customer_task_summary.py`，覆盖 completed、partial success、clarification、failed 和 handoff 五类聚合状态。HTTP 多任务执行回归继续通过。

### 后续

一次澄清的短期记忆字段已有基础（`original_query`、`clarification`、`missing_parameter`），下一步继续补充澄清次数和 pending task/intent 的严格复用，避免第二轮澄清重新从零规划。

## 2026-08-08：一次澄清状态机与二次澄清人工兜底完成

### 实现

- `execute-plan` 短期记忆增加 `clarification_count`；
- 保存 `pending_intent_plan.tasks`，记录第一次澄清时的待处理任务；
- 客户补充信息时复用原始问题与 pending 任务上下文，不再完全从零开始；
- 第一次无法识别返回 `needs_clarification`；
- 第二次仍无法识别时自动进入 `handoff`；
- 澄清成功后清空 pending plan，避免旧任务污染后续问题；
- 原有人工会话门禁、任务执行和整体状态契约保持不变。

### 验证

扩展 `scripts/test_customer_task_execution_http.py`，覆盖：

- 多任务执行；
- 首次澄清；
- 补充信息后的澄清复用；
- 第二次仍无法识别后转人工。


## 2026-08-08：意图实体提取与精确业务查询第一阶段完成

### 实现

- 在 `CustomerIntentPlanner` 增加显式实体提取；
- 当前提取 `order_no`、`ticket_no`；
- 实体写入 `IntentTask.arguments`；
- `CustomerTaskExecutor` 将任务 arguments 传递给 `CustomerReadTools`；
- `CustomerReadTools` 将实体传递给 `CustomerQueryRouter`；
- 订单查询在当前客户数据范围内优先按明确 `order_no` 过滤；
- 工单查询在当前客户数据范围内优先按明确 `ticket_no` 过滤；
- 未提供明确实体时保留原有“最近记录”查询行为；
- 不改变跨客户数据隔离规则。

### 当前边界

产品名称仍由产品状态路由按现有名称匹配逻辑处理，下一步可将其显式写入 `arguments.product_name`，并增加同名/多匹配澄清。

## 2026-08-08：精确订单/工单查询端到端验收补齐

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `scripts/test_customer_entity_exact_http.py` | 新增同客户多订单/多工单和跨客户订单隔离测试 | 补齐此前实体参数链路只有纯函数/既有路由回归、缺少指定编号最终查询断言的测试缺口。 |

验证确认：Planner 提取的订单号/工单号实际经过 execute-plan、Tool、Query Router 并影响最终数据库结果。

## 2026-08-08：产品名称实体与同名产品澄清完成

### 实现

- Planner 增加 `product_name` 实体提取；
- 产品名称通过 `IntentTask.arguments` 传递到 Tool 和 Query Router；
- Query Router 使用显式产品名称进行匹配；
- 唯一匹配返回实时产品状态；
- 多个同名产品返回 `evidence_status=needs_clarification`，任务层转换为 `needs_clarification`；
- 澄清内容要求补充产品类型、风险等级或产品编号；
- 未匹配产品仍返回产品名称澄清；
- 订单/工单实体精确查询及跨客户隔离保持不变。

### 修复过程

首次同名产品端到端测试失败，原因是 Query Router 的 `needs_clarification` 没有被 CustomerReadTools 映射为失败结果，Executor 因此可能把产品结果当作成功。已补充 `ambiguous_product` 错误映射并重新验证。

## 2026-08-08：多意图任务级结果前端展示完成

### 实现

- `CustomerRagView.vue` 现在展示 `overall_status`，不再只显示 RAG 的 `evidence_status`；
- 增加任务明细区域 `customer-task-results`；
- 每个任务展示 intent、任务状态、回答、澄清信息和错误信息；
- 整体澄清信息单独展示“下一步”；
- 保留实时事实卡片、引用和人工会话区域；
- 前端只消费既有 `CustomerTaskExecutionResponse`，未改变后端 API 契约。

### 目标

让客户能区分：

```text
整体完成
部分完成
需要补充信息
已转人工
任务失败
```

并能看到订单、资产、风险限制等子任务分别的结果。

## 2026-08-08：多意图任务结果浏览器验证

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/e2e/customer-service-live.spec.ts` | 新增客户多意图任务明细 E2E | 验证任务级结果不是只有后端 HTTP 契约，客户页面实际能够展示多个子任务。 |

验证结果：Chrome for Testing + `E2E_REAL_DATA=1` 下 6 项全部通过。

## 2026-08-08：查询与受控业务操作确认边界第一阶段完成

### 实现

- `business_operation` 不再与 `unknown`、产品推荐共用通用澄清结果；
- 识别到赎回、转账、下单时，任务状态固定为 `needs_confirmation`；
- 返回 `operation`、`confirmation_required=true`、`executed=false`；
- 客服对话不会直接创建订单、发起转账或执行赎回；
- 混合请求中，查询任务仍正常执行，操作任务单独要求通过受控业务页面确认；
- 聚合状态将 `needs_confirmation` 纳入未完成任务，因此查询成功 + 操作待确认返回 `partially_completed` 或 `needs_clarification`，绝不返回“操作已完成”。

### 当前边界

本阶段只建立 Agent 的安全确认边界，不新增业务操作提交 API，不自动创建业务草稿。后续如需真正从客服入口办理，应复用现有受控业务页面、权限、风控、审计和幂等机制，而不是让 Agent 直接写数据库。
