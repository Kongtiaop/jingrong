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

## 2026-08-10：客服业务操作安全导航闭环

### 目标

客服识别到赎回、转账或下单时，只返回受控的业务办理入口，不在对话中直接执行原有业务 API。

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/customer_task_executor.py` | 将赎回、转账、下单的 `action_target` 收敛到白名单映射；保持 `needs_confirmation`、`executed=false` | 防止 Agent 生成任意导航地址，并明确操作仍需客户在业务页填写和确认。 |
| `frontend/src/views/CustomerRagView.vue` | “前往办理”按钮仅接受业务办理路径并跳转 | 保持客服气泡窗口不执行操作，只导航到现有客户业务入口。 |
| `frontend/src/views/CustomerBackofficeView.vue` | 读取 `tab=operations` 查询参数并激活“业务办理”标签；智能客服导航到 `/customer-center/service` | 让安全导航真正落到现有业务办理页面，同时保持客服会话入口一致。 |

### 关键行为

- 操作任务返回 `status=needs_confirmation`；
- `data.confirmation_required=true`；
- `data.executed=false`；
- `data.action_target` 只允许 `/customer-center?tab=operations&action=redeem|transfer|order`；
- 不传递操作 token、已确认状态、自动提交参数或可直接执行命令。

### 验证

已完成静态检查与前端构建验证准备；最终命令结果记录在 `docs/TESTS.md`。


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

## 2026-08-10：客服第一版范围冻结，研发重点转向意图识别

本轮无代码文件变更，仅记录产品范围决策。

客服模块冻结为最小可用闭环：公共队列、客服 claim、并发冲突保护、暂停/释放/结束、heartbeat 超时释放和消息闭环。暂不新增指定客服转接、技能组、复杂自动路由或多客服协作。

下一阶段优先开发意图识别、多意图任务拆分、实时业务事实查询、任务执行和 RAG/人工兜底衔接。

## 2026-08-10：意图任务统一整体状态与部分成功第一阶段完成

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

## 2026-08-10：一次澄清状态机与二次澄清人工兜底完成

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


## 2026-08-10：意图实体提取与精确业务查询第一阶段完成

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

## 2026-08-10：精确订单/工单查询端到端验收补齐

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `scripts/test_customer_entity_exact_http.py` | 新增同客户多订单/多工单和跨客户订单隔离测试 | 补齐此前实体参数链路只有纯函数/既有路由回归、缺少指定编号最终查询断言的测试缺口。 |

验证确认：Planner 提取的订单号/工单号实际经过 execute-plan、Tool、Query Router 并影响最终数据库结果。

## 2026-08-10：产品名称实体与同名产品澄清完成

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

## 2026-08-10：多意图任务级结果前端展示完成

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

## 2026-08-10：多意图任务结果浏览器验证

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/e2e/customer-service-live.spec.ts` | 新增客户多意图任务明细 E2E | 验证任务级结果不是只有后端 HTTP 契约，客户页面实际能够展示多个子任务。 |

验证结果：Chrome for Testing + `E2E_REAL_DATA=1` 下 6 项全部通过。

## 2026-08-10：查询与受控业务操作确认边界第一阶段完成

### 实现

- `business_operation` 不再与 `unknown`、产品推荐共用通用澄清结果；
- 识别到赎回、转账、下单时，任务状态固定为 `needs_confirmation`；
- 返回 `operation`、`confirmation_required=true`、`executed=false`；
- 客服对话不会直接创建订单、发起转账或执行赎回；
- 混合请求中，查询任务仍正常执行，操作任务单独要求通过受控业务页面确认；
- 聚合状态将 `needs_confirmation` 纳入未完成任务，因此查询成功 + 操作待确认返回 `partially_completed` 或 `needs_clarification`，绝不返回“操作已完成”。

### 当前边界

本阶段只建立 Agent 的安全确认边界，不新增业务操作提交 API，不自动创建业务草稿。后续如需真正从客服入口办理，应复用现有受控业务页面、权限、风控、审计和幂等机制，而不是让 Agent 直接写数据库。

## 2026-08-10：系统设计与演进文档补录

### 变更目标

将近期已实现但此前未完整进入答辩版系统设计文档的客服 Agent、多意图任务、人工接管、实体精确查询、受控业务操作导航、工单/附件/通知、AML Worker、Data Catalog 和工程维护门禁统一补录。

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| docs/DEFENSE_SYSTEM_DESIGN_AND_EVOLUTION.md | 新增“2026 年 8 月新增能力与维护闭环补录”章节，并更新文档日期与目录 | 让答辩文档与当前代码、测试和维护台账保持一致，明确已实现能力、权限边界和后续维护规则。 |

### 关键内容

- 主 Agent 统一编排、多任务执行和同一 conversation_id；
- 订单/工单/产品实体精确查询与跨客户隔离；
- 澄清复用、人工 Handoff、heartbeat 和断线恢复；
- RAG 引用、低置信度转人工和客服/投顾边界；
- 
eeds_confirmation 与白名单 ction_target；
- 工单、通知、附件和风控调查闭环；
- AML 补跑、迟到流水补扫、批次血缘和 Worker 可观测性；
- Data Catalog 和受控 NL2SQL 边界；
- 代码、测试、计划、问题和文档维护门禁。

## 2026-08-10：受控业务操作安全导航 E2E 测试补强

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| scripts/test_customer_controlled_operation_http.py | 增加 ction_target 白名单和敏感字段断言 | 证明客服只返回安全导航信息，不返回可执行 token。 |
| rontend/e2e/customer-service-live.spec.ts | 增加“前往办理”跳转及无 /operations/* 自动提交断言 | 证明客户点击客服按钮后进入既有业务入口，但不会在客服页面直接执行操作。 |

## 2026-08-10：受控业务操作验收结果补充

### 已完成

- 后端 ction_target 白名单断言；
- 
eeds_confirmation 和 xecuted=false 断言；
- 混合查询 + 操作任务回归；
- 前端“前往办理”跳转 E2E 用例；
- 客户中心 	ab=operations 自动激活逻辑；
- 前端生产构建通过。

### 当前阻塞

真实 Playwright 单测仍需要稳定的 Vite 开发服务器和当前 API 服务同时运行。HTTP 回归已经通过；浏览器 E2E 代码已补充但尚未获得稳定运行结果。

## 2026-08-10：通知中心与实时客服会话联动第一阶段

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| pp/services/notification_service.py | 新增客服会话通知创建辅助方法 | 复用现有 Notification 模型和用户范围。 |
| pp/api/customer_service.py | 客服 claim、客户消息、人工回复生成站内通知 | 建立人工会话关键事件的最小通知闭环。 |
| rontend/src/components/NotificationInbox.vue | 支持客服会话资源并统一显示通知中心 | 通知不再只支持工单。 |
| rontend/src/views/BackofficeShellView.vue | 员工点击客服通知进入实时客服工作台 | 缩短新消息到处理入口的路径。 |
| rontend/src/components/CustomerServiceBubble.vue | 预留客户会话通知入口扩展点 | 为下一阶段客户侧未读和会话自动打开做准备。 |

### 当前边界

通知只写入既有站内 Notification 表，不直接执行客服状态变更，不替代会话权限校验；点击通知后仍由实时客服页面和后端 API 重新校验当前坐席/客户范围。

## 2026-08-10：客服通知联动 HTTP 回归补齐

| 文件 | 变更 | 原因 |
|---|---|---|
| scripts/test_customer_service_messages_http.py | 增加客服通知创建、未读列表、标记已读和跨用户权限断言 | 将通知联动从静态代码检查提升为真实 API 回归。 |

验证确认：客服 claim、客户消息、人工回复均产生对应资源范围内通知；客户可以标记自己的通知已读，其他用户不能越权标记。

## 2026-08-10：中断后通知联动收尾

恢复工作后完成：

- 修正后台通知中心客服会话跳转事件绑定；
- 清理通知 API 无用导入；
- 扩展客服 Live E2E 的通知资源范围测试；
- 重新通过客服消息通知 HTTP 回归和前端生产构建。

## 2026-08-10：今日停工交接归档

本日已完成客服受控业务操作安全边界、通知中心与实时客服会话第一阶段联动、通知 HTTP 回归、后台通知跳转绑定及前端构建验证。

今日不再新增代码。剩余事项转入下一工作日：稳定 Playwright 环境、完成通知浏览器 E2E，并评估 Outbox/幂等投递。

## 2026-08-10：通知资源范围 Playwright 回归恢复

- 将 Vite 开发服务器明确绑定至 127.0.0.1:5173，解决先前仅监听 IPv6 ::1 导致 Playwright baseURL 无法连接的问题；
- customer service notification event is visible to the intended user only 已在 installed Chrome 下通过；
- 发现当前 8010 API 运行实例与工作区客服 claim 通知代码不同步：API 回归使用应用 lifespan 时通过，外部运行实例尚未返回 claim 通知。该部署一致性问题保留为下一步验证项。

## 2026-08-10：通知点击聚焦客服会话闭环

| 文件 | 变更 | 原因 |
|---|---|---|
| rontend/src/components/NotificationInbox.vue | 通知行增加稳定测试标识，客服会话通知发出独立事件 | 支持浏览器验收和资源类型分流。 |
| rontend/src/views/BackofficeShellView.vue | 客服通知点击写入 section=customer-service 与 customer_service_session_id | 在后台保持可追溯的导航上下文。 |
| rontend/src/views/CustomerServiceDeskView.vue | 读取路由会话 ID，待接入或已接入会话均可聚焦并加载消息 | 解决已 claim 会话不在待接入队列时无法打开的问题。 |
| rontend/e2e/customer-service-live.spec.ts | 新增通知点击、自动已读、URL 与会话聚焦 E2E | 将通知闭环从 HTTP 契约扩展到真实浏览器路径。 |

验证：P0 Docker API + installed Chrome 下新增 E2E 通过。

## 2026-08-10：通知幂等键与 Outbox 异步投递

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/models/notification.py` | 增加 `idempotency_key` 与接收人级唯一索引 | 防止重复消费或重试生成重复通知。 |
| `app/services/notification_service.py` | 客服通知改为创建 OutboxEvent | 使客服主事务不直接依赖通知投递成功。 |
| `app/api/customer_service.py` | claim、客户消息、人工回复生成稳定事件键 | 保留事件、接收人和会话资源的可追溯关系。 |
| `workers/outbox_worker.py` | notification 事件分流到 notifications Redis Stream | 复用已有 Outbox 发布机制。 |
| `app/services/notification_delivery.py`、`workers/notification_worker.py` | 新增通知消费与幂等创建 | 形成 Redis Stream 消费、唯一冲突去重和 ACK 闭环。 |
| `docker-compose.p0.yml` | 新增 notification-worker | 在 P0 Docker 环境运行异步通知消费者。 |
| `scripts/test_customer_notification_outbox_docker.py` | 新增真实 Docker 异步投递与幂等回归 | 验证 claim、消息、不同事件和每事件单通知。 |

### 验证

P0 Docker 的 API、Outbox Worker、Notification Worker、PostgreSQL 和 Redis 均正常运行；异步投递回归通过。


## 2026-08-10：Outbox dead-letter 观测与受控重试

| 文件 | 变更 | 原因 |
|---|---|---|
| pp/api/admin.py、pp/schemas/admin.py | 新增通知 Outbox 列表和 dead 重试 API | 给管理员提供失败可观测与受控恢复入口。 |
| rontend/src/views/OutboxEventsView.vue、rontend/src/api/admin.ts、rontend/src/views/BackofficeShellView.vue | 新增通知投递监控后台入口 | 展示状态、尝试次数、失败原因并允许重试 dead 事件。 |
| scripts/test_outbox_dead_letter_http.py | 注入发布失败并验证 dead、权限、重试、恢复投递 | 覆盖死信状态机和真实 Docker Worker 恢复链路。 |

验证：P0 Docker 环境和 HTTP 回归通过。

## 2026-08-10：客服 Agent 流式输出技术决策

本条为架构决策记录，尚未实现 WebSocket 流式代码。

- 客服主通道采用 WebSocket：支持双向消息、用户取消/补充、人工接管、心跳、任务进度和通知；
- SSE 保留为未来只读、低交互报告或后台长任务的输出适配器；
- 后续抽象传输无关的 Agent Stream Event，避免为 WebSocket 和 SSE 重复实现 Agent 编排逻辑；
- 现有 HTTP execute-plan 在迁移阶段继续保留，作为兼容与降级路径。


## 2026-08-10：客服 Agent WebSocket 前端时序与浏览器流式闭环

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/src/services/customer-agent-stream.ts` | WebSocket 客户端改为 Promise 化连接；请求发送等待连接打开，连接错误或提前关闭可被调用方明确处理；默认直连本地 API 8010 并按 API 协议选择 `ws/wss`。 | 消除 `open` 事件、引用赋值和请求发送之间的时序竞态，避免正常流式请求误降级 HTTP。 |
| `frontend/src/views/CustomerRagView.vue` | 连接超时与 Agent 执行生命周期分离；仅 `agent.final` 完成；`session.ready` 不触发错误或结束；仅异常连接状态触发 fallback。 | 保持一条 WebSocket 持续接收 chunk、任务进度和最终结构化结果。 |
| `frontend/vite.config.ts` | 固定本地开发监听地址并配置 API WebSocket 代理能力。 | 保留开发代理兼容性；流式客户端仍可在本地直连 API，避免代理升级异常导致误回退。 |
| `frontend/e2e/customer-service-live.spec.ts` | 通过页面原生 WebSocket 包装器记录 send/message，并断言 `agent.request`、`agent.output.chunk`、`agent.final`。 | Playwright 原生 websocket frame 观察在该环境不稳定，改为验证浏览器页面实际发送和接收的事件。 |

验证：独立 WebSocket Python 回归、前端生产构建、P0 Docker API 与 installed Chrome Playwright E2E 均通过。

## 2026-08-10：客服 Agent WebSocket 请求取消闭环

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/api/customer_service.py` | 按 `request_id` 管理活动 `asyncio.Task`；新增 `agent.cancel`、`agent.cancelled`、重复请求和非活动取消错误；断开连接时清理活动任务。 | 让 WebSocket 支持受控中止，而非仅支持单向流式输出。 |
| `frontend/src/views/CustomerRagView.vue` | 查询期间展示取消按钮和取消已发送状态；处理 `agent.cancelled` 并结束本次流式生命周期，不回退 HTTP。 | 让客户可见、可操作地停止慢查询。 |
| `scripts/test_customer_agent_websocket.py` | 扩展正常流式回归，新增可控慢执行请求的 `agent.cancel` / `agent.cancelled` 断言。 | 验证协议级取消与序列完整性。 |
| `frontend/e2e/customer-service-live.spec.ts` | 新增 installed Chrome 浏览器取消 E2E，并与正常流式 E2E 一并回归。 | 验证真实页面点击、客户端发送和服务端取消确认闭环。 |

验证：P0 Docker API、独立 WebSocket 回归、前端生产构建和 installed Chrome Playwright 两条 WebSocket 用例均通过。

## 2026-08-10：客服 Agent WebSocket 澄清补充闭环

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/api/customer_service.py` | 支持 `agent.amend`，将补充信息作为同一 conversation_id 下的新 request 执行。 | 让澄清后的客户输入继续进入实时 Agent 通道。 |
| `frontend/src/services/customer-agent-stream.ts` | 增加 `sendAmendment`，与普通 request 共用连接生命周期。 | 统一 WebSocket 发送协议，避免补充信息退回 HTTP。 |
| `frontend/src/views/CustomerRagView.vue` | 在 `needs_clarification` 状态展示补充输入，发送 amend 并处理新的流式结果。 | 让客户可在当前页面完成澄清闭环。 |
| `scripts/test_customer_agent_websocket.py` | 新增 amend 协议回归。 | 验证 amend final、取消和正常流式行为不互相回归。 |
| `frontend/e2e/customer-service-live.spec.ts` | 新增补充信息浏览器 E2E。 | 验证真实页面发送 `agent.amend`。 |

验证：P0 Docker API、独立 WebSocket 回归、Vite 构建和 installed Chrome 三条 WebSocket E2E 均通过。

## 2026-08-10：客服 Agent 实时进度与任务完成反馈

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/src/views/CustomerRagView.vue` | 消费 `agent.accepted`、`agent.progress`、`agent.task.completed`、`agent.cancelled` 和 `agent.final`，新增总体进度文本和 `customer-agent-progress` 标识。 | 让客户在最终结果前看到请求接收、规划、整理和任务完成阶段。 |
| `frontend/e2e/customer-service-live.spec.ts` | 新增实时进度浏览器 E2E，并与取消、补充、正常流式用例联跑。 | 验证真实页面的实时反馈没有破坏已有 WebSocket 交互。 |

验证：P0 Docker API、独立 WebSocket 回归、Vite 构建和 installed Chrome 四条客服 Agent WebSocket E2E 均通过。

## 2026-08-11：人工客服会话与消息实时生命周期闭环

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/customer_realtime.py` | 新增客户专属 Redis Pub/Sub 事件发布器。 | 将在线实时刷新与 Outbox/Redis Streams 的可靠通知职责分离。 |
| `app/api/customer_service.py` | 客服 claim、pause、release、resolve、customer-close、close 与人工回复提交成功后发布生命周期或人工消息事件；客户 Agent WebSocket 订阅 `customer-realtime:{customer_id}` 并 relay 到浏览器。 | 让人工会话事实变更可低延迟同步到在线客户，同时不影响事务主路径。 |
| `frontend/src/views/CustomerRagView.vue` | 消费人工接入、暂停、释放、解决、关闭和人工消息事件；执行消息去重、轮询启停与缓存清理。 | 从单纯轮询演进为实时优先、轮询补偿。 |
| `scripts/test_customer_realtime_lifecycle.py` | 新增客户 WebSocket 生命周期独立回归，并在运行前清理测试坐席残留活动会话。 | 验证 Pub/Sub relay 与单坐席业务约束在真实 P0 环境下协同工作。 |
| `frontend/e2e/customer-service-live.spec.ts` | 新增客户实时人工生命周期浏览器 E2E。 | 验证 claim/pause/resolve 能即时反映到客户页面。 |

验证：P0 Docker API、客户生命周期独立 WebSocket 回归、Customer Agent 协议回归、Vite 构建和 installed Chrome 五条 E2E 均通过。

## 2026-08-11：实时客服断线补偿与增量消息同步

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/src/api/customer-rag.ts` | 人工消息查询增加可选 `after` 参数。 | 断线恢复时只拉取最后一条消息之后的新记录。 |
| `frontend/src/views/CustomerRagView.vue` | 记录 `lastMessageId`，按消息 ID 去重，WebSocket error/close 触发恢复刷新，增加 `customer-agent-reconnecting` 状态。 | 降低全量轮询成本并明确展示实时通道恢复过程。 |
| `frontend/src/services/customer-agent-stream.ts` | 保持连接打开/关闭回调可被页面捕获。 | 为断线补偿和轮询降级提供生命周期触发点。 |

验证：Vite 构建、Agent WebSocket 回归、人工生命周期回归及 installed Chrome 五条 E2E 均通过。

## 2026-08-11：断线补偿实现复核与游标/事件序列化修正

复核上一轮断线补偿代码后补齐两处可靠性细节：

- `GET /customer-service/sessions/{session_id}/messages?after=` 不再直接对 UUID 字符串做大小比较；服务端先定位游标消息，再按 `(created_at, id)` 顺序返回其后的记录，避免 UUID v4 字典序与消息时间顺序不一致导致漏消息；
- 人工消息 Pub/Sub 事件使用 Pydantic JSON 模式序列化 `created_at`，避免 datetime 进入 `json.dumps` 导致人工回复提交失败。

同时扩展 `scripts/test_customer_service_messages_http.py`，验证人工消息 after 游标只返回后续消息、游标末尾返回空集，并保留原有权限、通知 Outbox 和客服主链路断言。

验证结果：消息通知 HTTP 回归通过，`incremental_after_cursor=true`；前端 Vite 构建和相关 Python 编译通过。

## 2026-08-11：客服封板与 P2 主线切换

客服模块已完成本阶段交付，后续只处理稳定性、安全性、数据一致性和回归问题；不继续扩展技能组、自动派单、SLA、语音、多渠道或复杂转接。

下一阶段进入 P2 财富管理只读闭环：客户画像、风险等级、账户/持仓、产品资料与适当性筛选。目标是让 Advisor/Risk 能基于客户真实事实和知识引用，生成受控、可解释、不可直接执行的产品匹配结果；申购、赎回和客户确认仍留待 P3。

## 2026-08-11：Agent Memory 设计选择

- 参考：`docs/TencentDB Agent Memory .md`。
- 结论：采纳 Gen2 结构化、时间化、版本化与证据链原则；不直接采用 Gen3 记忆图谱。
- 原因：当前已有 Redis 短期记忆和 PostgreSQL `CustomerMemory` 基础；财富管理场景更需要权威事实隔离、审计和确认机制，而不是更复杂的关系图谱。
- 风险控制：业务事实表始终高于记忆；模型提炼结果只能是候选；敏感内容必须确认；P2 只读业务闭环不被记忆改造阻塞。

## 2026-08-11：P2-1 客户产品适配中心（只读）

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/schemas/profile.py` | 扩展适配结果、客户上下文、风险提示与生成时间模型。 | 让适配结果可解释且可展示事实依据。 |
| `app/services/suitability_service.py` | 读取产品适当性规则，合并风险等级、客户类型、资产门槛和风险操作状态。 | 将已有基础推荐升级为确定性的 P2 只读闭环。 |
| `app/api/profile.py` | 客户和投顾适配接口返回统一增强结果。 | 保持客户自助和已授权投顾读取同一事实规则。 |
| `frontend/src/views/CustomerHomeView.vue` | 新增产品适配中心、风险提示和规则说明。 | 让客户看到系统依据，且明确不产生交易。 |
| `scripts/test_suitability_p2_http.py` | 新增匹配、排除、规则解释和只读断言。 | 防止适配读取链路意外产生订单或绕过规则。 |

验证：P2 适配 HTTP 回归 `pass=true`；前端 Vite 生产构建通过。

## 2026-08-11：客户登录白屏与公开入口可见性修复

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/src/views/CustomerBackofficeView.vue` | 补充 `useRoute` 导入。 | 修复客户中心初始化时 `useRoute is not defined` 导致的白屏。 |
| `frontend/src/views/LoginView.vue` | 客户登录后跳转 `/customer-center`，员工跳转 `/backoffice`。 | 让登录后直达角色对应工作台。 |
| `frontend/src/App.vue` | 已登录 Portal 页面继续显示全局顶部导航。 | 客户中心可回首页、产品展示并使用账户菜单，避免白屏时无出口。 |
| `frontend/e2e/customer-service.smoke.spec.ts` | 新增客户登录、客户中心渲染、首页/产品展示导航回归。 | 防止登录跳转和公开入口再次被破坏。 |

验证：前端 Vite 生产构建通过。浏览器用例需要在启动 API 与 Vite 开发服务后执行。

## 2026-08-11：转人工实时订阅解耦与统一 Agent 入口

| 文件 | 变更 | 原因 |
|---|---|---|
| `frontend/src/services/customer-agent-stream.ts` | 将一次性 Agent 请求流与人工客服实时订阅拆开；人工订阅支持退避自动重连。 | Agent 请求结束不再关闭人工会话的实时事件通道。 |
| `frontend/src/views/CustomerRagView.vue` | 人工会话 ID 出现后建立独立订阅；HTTP 增量补偿和 WebSocket 消息统一转换为页面消息；新增人工会话消息展示区。 | 修复转人工后反复 recovering 以及人工回复已收到但未渲染的问题。 |
| `frontend/src/components/AgentBubble.vue` | 新增统一 Agent 悬浮入口，当前适配客服路由。 | 为后续 Supervisor 路由到客服、投顾、风控、分析和操作 Agent 保留唯一入口。 |
| `frontend/src/App.vue`、`frontend/src/styles.css` | 挂载统一入口并补充组件样式。 | 不再把入口定位为客服专属气泡。 |
| `frontend/e2e/customer-service-live.spec.ts` | 新增 Agent 请求结束后人工订阅持续在线、人工回复可见且不显示持续恢复状态回归。 | 覆盖本次实际故障路径。 |

验证：重建 P0 Docker API 后，installed Chrome 专项浏览器回归通过（1 passed）；前端生产构建通过。

## 2026-08-11：客服遗留会话清理、状态校验与坐席窗口稳定化

### 问题与结论

发现早期回归遗留会话 `da9c9f18-0782-475c-9ba4-e9789ce13b4d` 已处于 `resolved`，但其会话 ID 仍可能通过浏览器缓存或通知路由进入实时客服工作台。该会话包含“Outbox 异步通知回归消息”等测试文案，导致坐席被错误聚焦到不可 claim 的旧会话，并出现 `session is not available for claim`。

### 已完成

- 超时 Worker 新增 `waiting_human` 自动过期：默认 30 分钟未接入的会话转为 `expired`，并写入审计；保留原有 90 秒无心跳 `human_active → paused` 处理；
- Docker Customer Service Timeout Worker 实际执行并将 18 条历史 `waiting_human` 会话转为 `expired`；
- 新增 `GET /customer-service/sessions/{session_id}`，前端以会话真实状态决定是否恢复或聚焦；
- 只允许 `waiting_human`、`paused`、`human_active` 会话进入客户恢复、坐席通知聚焦和接入逻辑；`resolved`、`closed`、`expired` 自动清除浏览器会话 ID / 路由会话 ID，并停止轮询和实时订阅；
- 坐席心跳返回 409 时不再无限显示“请重新接入”，而是停止心跳、清空无效选中会话并回到待接入队列；
- 坐席接入成功后自动打开专注的实时对话窗口，也可从工作台手动打开；
- 统一 Agent 气泡仅保留在客户侧，员工后台不再渲染该入口，避免与坐席工作台竞争会话上下文；
- 已删除旧测试会话及其关联消息、通知、Outbox 事件和审计记录。

### 运行验证

```text
customer_service_timeout_sweep paused_count=0 expired_count=18
customer_service_session waiting_human count: 18 → 0
前端 Vite production build: passed
```

## 2026-08-11：统一 Agent 入口与主路由元数据落地

### 已完成

- [x] 统一 Agent 气泡移除显式 intent 下拉框，用户只输入自然语言；
- [x] 新增 `POST /customer-service/sessions/agent/execute`，接受 `content`、`conversation_id`、`page_context` 和可选 `resource_id`；
- [x] 兼容保留 `execute-plan`，旧调用继续返回客服路由元数据；
- [x] `CustomerAgentExecutionService` 根据确定性 `CustomerIntentPlanner` 任务计划生成 `route` 与 `agents` 元数据；
- [x] 产品推荐标记为 `advisor_risk` / `AdvisorAgent + RiskAgent`；
- [x] 风险限制标记为 `risk` / `RiskAgent`；
- [x] 资产与持仓标记为 `analysis` / `AnalystAgent`；
- [x] 赎回、转账、下单标记为 `operation_risk` / `OperationAgent + RiskAgent`；
- [x] 所有路由仍由现有受控 Tool 和确定性 Service 执行，写操作保持 `needs_confirmation`，不因路由元数据直接执行；
- [x] 人工 handoff 状态不再阻断独立的只读 Agent 路由；同一聊天窗口中的人工消息仍由前端单独发送到 `customer_service_message`。

### 回归

`scripts/test_unified_agent_router_http.py` 已验证：

```text
advisor_risk=true
risk=true
analysis=true
operation_risk=true
no_direct_execution=true
pass=true
```

当前 `agents` 是路由元数据和执行边界声明，尚未代表多个独立 LLM 进程并行运行；后续 Supervisor 接入时复用此契约。

## 2026-08-12：权限前置的统一 Agent 能力过滤

### 已完成

- [x] 新增 `AgentCapabilityRegistry`，以用户角色、permission 与超级管理员标识确定可调用 Agent 白名单；
- [x] 统一入口先计算 `available_agents` / `denied_agents`，再接受主路由结果；
- [x] 客户仅可调用 `CustomerServiceAgent`；
- [x] 一般内部人员按已有读取权限获得 `AnalystAgent`；
- [x] 投顾角色获得 `AdvisorAgent`（以及内部分析能力）；
- [x] 风控权限获得 `RiskAgent`（以及内部分析能力）；
- [x] 运营/审核类权限获得 `OperationAgent`；
- [x] 管理审计权限获得 `AdminAgent`；
- [x] 超级管理员可用全部 Agent，但仍不绕过 Tool/Service 的资源级权限、审计、状态机和确认门禁；
- [x] 若主路由选择的 Agent 不在白名单，统一入口降级到当前可用客服说明能力或返回权限指引，不调用被拒绝 Agent。

### 验证

`scripts/test_unified_agent_router_http.py`：

```text
customer_pre_filter=true
advisor_pre_filter=true
risk_pre_filter=true
super_admin_all_agents=true
no_direct_execution=true
pass=true
```

Docker P0 API 已于 2026-08-12 重建并启动。

## 2026-08-12：DataAnalysisAgent 与 BusinessAgent 能力命名收敛

| 名称 | 适用对象 | 当前边界 | 后续实现 |
|---|---|---|---|
| `DataAnalysisAgent` | 所有内部人员，范围按权限收缩 | 当前仅作为权限前置与路由元数据；不得自由执行 SQL。 | 受控 NL2SQL：指标语义层、模板/AST 白名单、只读连接、行数/成本限制、脱敏和资源过滤。 |
| `BusinessAgent` | 运营、审核、受授权投顾/客户经理、超级管理员 | 仅生成草稿、参数提示、确认要求和流程导航。 | 对接转账、下单、赎回等确定性 Service；继续经过风控、幂等、审计和 Outbox。 |

客户只可使用 `CustomerServiceAgent`；客户业务请求由客服 Agent 返回受控导航或 `needs_confirmation`，不直连内部 `BusinessAgent`。

验证：`scripts/test_unified_agent_router_http.py` 通过，当前路由元数据已使用 `DataAnalysisAgent` / `BusinessAgent`。

## 2026-08-12：DataAnalysisAgent 受控指标查询

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/data_analysis_agent.py` | 新增固定指标识别与只读聚合执行器。 | 作为 NL2SQL 前的安全第一阶段，不执行自由 SQL。 |
| `app/api/customer_service.py` | 统一入口在权限过滤后优先调用 DataAnalysisAgent。 | 让内部人员的统计问题进入受控分析能力。 |
| `scripts/test_data_analysis_agent_http.py` | 新增风险、运营、客户权限与 query plan 回归。 | 验证内部分析可用、客户拒绝、自由 SQL 未开放。 |

验证：DataAnalysisAgent HTTP 回归通过；Docker P0 API 已重建。

## 2026-08-12：DataAnalysisAgent 受控自由 SQL 与图表路线

固定指标查询被定位为 DataAnalysisAgent 的第 0 阶段。项目最终采用“权限前置 + 数据目录语义白名单 + 强制资源过滤 + SELECT-only AST 校验 + 只读执行 + chart spec”的 NL2SQL 路线。图表支持指标卡、表格、柱状图、折线图和饼图；模型只能返回数据和声明式图表规格，不能返回可执行前端代码。

## 2026-08-12：数据目录批准查询模板与参数绑定首个切片

- [x] DataAnalysisAgent 增加批准模板执行骨架；
- [x] 只接受数据目录中 `is_active=true` 且 `is_approved=true` 的模板；
- [x] 模板执行前校验必须以 `SELECT` 或 `WITH ... SELECT` 开始；
- [x] 拒绝分号、多语句、DML/DDL、危险函数等明显危险 SQL；
- [x] 参数仅由应用层绑定，当前支持受控 `limit`、`month_start`、`start_date` 和明确 ID 提取；
- [x] 返回 `mode=approved_template`、模板 ID、来源表、参数名、只读和自由 SQL 标识；
- [x] 根据模板结果列生成声明式 `chart_spec`（当前为 table/bar/line 推断基础）；
- [x] 固定指标白名单仍作为模板未命中时的安全兜底。

当前仍未开放真正的模型自由 SQL 生成；下一阶段增加 SQL AST 解析、字段/表白名单、强制资源过滤、只读连接、超时/成本限制和结果行数限制。
---

## 2026-08-12：主 Agent 轻量 Plan、bounded ReAct 与目录驱动受控分析

### 目标

将多 Agent 架构明确为“权限前置的主 Agent 轻量 Plan + 子 Agent 分层执行”，并把数据目录召回、批准模板安全执行和知识类 bounded ReAct 接入真实链路。

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/customer_intent.py` | `IntentTask` 新增 `execution_mode`，知识类意图标记 `bounded_react`，其余任务保持 `controlled_workflow` | 让主计划显式表达推理型与执行型任务边界。 |
| `app/schemas/customer_service.py`、`app/api/customer_service.py` | 意图规划 API 返回 execution_mode | 前端、审计和 Supervisor 可观察计划执行范式。 |
| `app/services/customer_task_executor.py` | 知识类任务增加最多 3 轮/3 次调用的只读 bounded ReAct；仅 low_confidence 首次允许一次检索精炼 | 提升低风险知识问答的多跳检索能力，同时防止无限循环和越权工具调用。 |
| `app/services/customer_agent_execution.py` | 保存 plan execution modes；为 bounded ReAct 写入最小化审计 | 保留工具预算、结果和趋势可观测性，不记录私有推理或业务数据。 |
| `app/services/data_analysis_agent.py` | 批准模板状态/角色检查、SELECT-only、危险语法拦截、模板与目录表字段白名单、参数边界、100 行限制、受控拒绝原因 | 防止批准模板退化为简单字符串执行。 |
| `app/api/customer_service.py` | DataAnalysisAgent 执行前调用 `DataCatalogService.search`，将角色过滤后的文本/pgvector 目录召回结果作为动态表字段候选白名单；写入模板审计 | 让目录检索真正参与执行授权，而非仅用于后台搜索展示。 |
| `app/api/admin.py`、`scripts/test_data_analysis_agent_http.py` | 管理端审计列表支持 action 前缀/资源筛选；HTTP 脚本增加 DataAnalysisAgent 审计断言 | 提升受控分析执行的可追踪性。 |
| `tests/test_data_analysis_agent_security.py`、`tests/test_customer_task_executor_react.py`、`tests/test_customer_intent_execution_modes.py` | 新增模板安全、目录候选、bounded ReAct 与执行模式回归 | 固定关键安全边界与范式分类。 |
| `docs/DATA_ANALYSIS_AGENT_SECURITY_2026-08-12.md`、`docs/MULTI_AGENT_PARADIGM_AND_BOUNDED_REACT_2026-08-12.md` | 新增/扩展专项设计与安全说明 | 记录受控 SQL、目录检索、ReAct 边界和审计最小化原则。 |

### 关键行为

```text
主 Agent：权限过滤 → 轻量 Plan → execution_mode
知识类子任务：bounded_react（只读、工具白名单、预算、evidence、可降级）
执行类子任务：controlled_workflow（确认、状态机、审计）
数据分析：目录召回 → 批准模板 → 双白名单 → 参数/结果限制 → 审计
```

### 验证

本轮 Python 编译和指定文件 `git diff --check` 已通过；完整 pytest 与 Docker/HTTP 审计集成验证状态详见 `docs/TESTS.md`。


---

## 2026-08-12：NL2SQL 评估基准提升为 P2，记忆层接入降为 P3

### 目标

先建立能够衡量 NL2SQL 真实质量、安全性和可用性的统一尺度，再评估记忆层带来的个性化收益。

### 文档变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `docs/PLAN.md` | 新增 P2/P3 优先级调整；NL2SQL 六维评估基准提前，记忆层打通后置 | 没有基线就无法可靠判断记忆层优化是否有效。 |
| `docs/ISSUES.md` | 新增 `ISSUE-0049`，记录评估维度缺失和优先级调整 | 固化问题根因、结论和项目排序依据。 |
| `docs/TESTS.md` | 增加语法正确率、语义准确率、拒答率、澄清触发率、P95 延迟、安全事故数的定义和 P2 待办 | 将评估要求转成可执行测试契约。 |

### 当前结论

```text
P2：NL2SQL 专用评估基准与运行监控
P3：记忆层与 NL2SQL 打通
```

本次为文档/计划调整，未声称六维自动化运行器已经完成。


---

## 2026-08-12：P2 NL2SQL 六维最小离线评估基线

### 目标

把“先有尺子再优化”落为可运行的最小评估基线，避免在引入记忆层前只能依赖演示样例判断 DataAnalysisAgent 效果。

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/data_analysis_agent.py` | 新增 `evaluate_query_safety`，对直接 SELECT/危险 SQL-shaped 输入给出受控拒绝原因 | 让应用入口和评估基线共享同一类安全语义。 |
| `app/api/customer_service.py` | DataAnalysisAgent 对直接 SQL-shaped 请求返回 `refused`，并写入最小化拒绝审计 | 防止自由 SQL 输入回退到普通客服链路。 |
| `tests/nl2sql_eval_cases.json` | 新增 5 条离线评估用例 | 提供语法、语义、拒答、澄清和安全基线样本。 |
| `scripts/run_nl2sql_eval.py` | 新增六维离线评估运行器和 JSON 报告 | 输出语法正确率、语义准确率、拒答率、澄清触发率、P95 延迟、安全事故数。 |
| `tests/test_data_analysis_agent_security.py` | 增加直接 SQL-shaped 输入拒绝测试 | 固定自由 SQL 不可执行边界。 |

### 验证

`python scripts/run_nl2sql_eval.py` 已运行通过，5 条基线用例语法正确率与语义准确率均为 1.0，安全事故数为 0。真实 HTTP/数据库/权限/缓存评估尚未完成，详见 `TESTS.md`。


---

## 2026-08-13：NL2SQL 共享纯策略模块与权限/目录拒绝评估扩展

### 目标

消除离线评估与服务安全逻辑的镜像漂移，并把 P2 最小基线扩展到权限、目录范围和模板批准状态。

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/nl2sql_policy.py` | 新增无 ORM、数据库和应用启动依赖的纯策略模块 | 让服务、离线评估和后续 SQL IR 校验复用同一安全规则。 |
| `app/services/data_analysis_agent.py` | 迁移 SQL-shaped 输入安全、只读模板校验、常量和固定指标路由到共享策略 | 避免服务端与评估端规则漂移。 |
| `scripts/run_nl2sql_eval.py` | 动态加载共享纯策略模块；支持权限、目录范围、模板批准状态模拟拒绝 | 让离线环境不加载完整 FastAPI/pgvector，同时复用真实规则。 |
| `tests/nl2sql_eval_cases.json` | 从 5 条扩展为 8 条评估用例 | 纳入权限拒绝、目录范围拒绝、模板未批准拒绝。 |
| `tests/test_nl2sql_policy.py` | 新增共享策略单测 | 固定纯策略模块的关键安全契约。 |

### 验证

共享策略编译通过，离线评估 8 条用例全部符合期望，安全事故数为 0；完整 HTTP/数据库/缓存评估仍在 P2 范围内。


---

## 2026-08-12：PermissionScopedCache 与目录搜索缓存隔离

### 目标

修复权限上下文未覆盖缓存中间层的问题，避免 NL2SQL Schema/目录候选缓存发生跨用户或跨权限命中。

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/permission_scoped_cache.py` | 新增 `PermissionContext`、权限指纹和 JSON 缓存 facade | 将权限隔离从调用方手工拼 key 提升为抽象层强制约束。 |
| `app/services/data_catalog_service.py` | `CatalogSearchScope` 携带权限上下文；目录搜索支持权限感知缓存 | 防止不同用户的表/字段目录候选互相命中。 |
| `app/api/customer_service.py` | DataAnalysisAgent 目录检索接入 Redis 权限感知缓存 | 让真实 NL2SQL 执行链路使用安全缓存。 |
| `tests/test_permission_scoped_cache.py` | 新增跨用户、跨权限缓存隔离测试 | 固化 ISSUE-0048 的安全回归。 |
| `tests/test_data_catalog_permission_cache.py` | 新增目录搜索缓存按权限上下文隔离测试 | 验证目录检索缓存不会泄漏。 |

### 验证

静态编译和伪 Redis 隔离测试准备通过；真实 Redis 并发验证列入下一切片。


---

## 2026-08-12：权限缓存版本失效维度与 Redis 故障降级

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/permission_scoped_cache.py` | Redis get/set 异常降级为 cache miss/false；保留数据范围和目录策略版本作为 key 指纹维度 | 缓存故障不能改变授权结论，策略变化不能命中旧缓存。 |
| `tests/test_permission_scoped_cache.py` | 增加 backend 故障与上下文版本变化测试 | 固化安全缓存的可用性和失效契约。 |

### 验证

Python 编译通过。真实 Redis 并发和管理员权限变更后的事件化失效仍待下一 P2 切片完成。


---

## 2026-08-12：真实 Redis 权限缓存并发回归脚本

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `scripts/test_permission_cache_isolation_http.py` | 新增两个内部角色的并发 DataAnalysisAgent 请求、Redis key 扫描和明文权限检查 | 将 ISSUE-0048 从伪 Redis 单测推进到真实 HTTP/Redis 验证。 |
| `docs/PLAN.md`、`docs/ISSUES.md`、`docs/TESTS.md` | 记录脚本、预期结果和当前环境阻塞状态 | 防止把未执行的真实回归误报为通过。 |

### 验证

脚本 Python 编译通过；真实执行需在完整 Conda/Compose 环境完成。


---

## 2026-08-12：真实缓存隔离回归依赖诊断

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `scripts/test_permission_cache_isolation_http.py` | 捕获缺少 `pgvector` 的导入错误并输出明确 BLOCKED 信息 | 区分环境依赖阻塞与业务安全断言失败。 |
| `docs/PLAN.md`、`docs/ISSUES.md`、`docs/TESTS.md` | 记录真实回归尝试、错误、状态和下一步 | 保证集成测试状态不被误报。 |

### 验证

脚本编译通过；真实执行被缺少 `pgvector` 阻塞，未宣称 Redis/HTTP 回归通过。


---

## 2026-08-12：真实 Redis/HTTP 权限缓存隔离回归修复并通过

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `scripts/test_permission_cache_isolation_http.py` | 修正对统一 Agent 响应固定 `results[0]` 的假设；仅校验存在的 DataAnalysis 结果 | 使测试聚焦缓存隔离，而不误把角色路由差异判为失败。 |
| `docs/PLAN.md`、`docs/ISSUES.md`、`docs/TESTS.md` | 将真实回归从 BLOCKED 更新为 PASS，记录发现的测试断言问题 | 保持验证状态与实际运行一致。 |

### 验证

使用 `langchain_env` 真实运行通过：Redis 可达、4 个并发 HTTP 请求、2 个权限隔离 key、无明文权限，退出码 0。


---

## 2026-08-12：用户角色变更触发权限缓存版本失效

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/permission_scoped_cache.py` | 新增 Redis 权限上下文版本读取/递增；默认版本统一为 `0` | 使权限更新后的新请求天然使用新缓存 key。 |
| `app/services/data_catalog_service.py` | `CatalogSearchScope.from_user` 异步读取 Redis 版本 | 将事件化失效维度真正带入目录缓存 key。 |
| `app/api/customer_service.py` | 目录搜索调用异步权限上下文构造 | 让 DataAnalysisAgent 链路使用当前版本。 |
| `app/api/admin.py` | 用户角色更新后 bump 权限上下文版本；角色权限更新预留受影响用户 bump | 将 RBAC 写操作和缓存失效关联。 |
| `scripts/test_permission_cache_invalidation_http.py` | 新增用户角色更新后的真实 HTTP/Redis 失效回归 | 验证版本递增与新缓存 key 生成。 |
| `tests/test_permission_scoped_cache.py` | 更新版本递增契约测试 | 固定版本格式和指纹变化。 |

### 验证

真实回归通过：版本 `1 → 2`，目录缓存 key `2 → 3`，退出码 0。


---

## 2026-08-12：角色权限变更影响多用户的缓存失效真实回归

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `scripts/test_role_permission_cache_invalidation_http.py` | 用数据库 session 创建/清理临时第二风险用户，验证两个不同用户均被角色权限更新失效 | 避免依赖不存在的用户创建 API，也避免单用户重复登录造成伪多用户覆盖。 |
| `docs/PLAN.md`、`docs/ISSUES.md`、`docs/TESTS.md` | 记录真实多用户通过结果并关闭目录缓存范围的 ISSUE-0048 | 让安全治理状态与实际验证一致。 |

### 验证

真实 Redis/HTTP 回归通过；两个不同用户的版本分别从 `3 → 4`、`0 → 1`。临时用户在 finally 中删除。


---

## 2026-08-13：NL2SQL 真实 HTTP 六维安全评估运行器

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `scripts/run_nl2sql_http_eval.py` | 新增真实 HTTP 评估：请求执行、结果分类、P95、审计检查、安全事故汇总 | 将 P2 安全事故数从离线模拟提升为真实应用观测。 |
| `docs/PLAN.md`、`docs/ISSUES.md`、`docs/TESTS.md` | 记录首次真实基线、误判修复和未覆盖范围 | 防止将未执行或错误分类的指标作为发布结论。 |

### 验证

真实 HTTP 运行通过：5 条用例，语义准确率 1.0，P95 约 67.02ms，安全事故数 0。


---

## 2026-08-13：HTTP 评估加入未启用批准模板门禁

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `scripts/run_nl2sql_http_eval.py` | 临时停用一条 active/approved 模板、执行同题请求、恢复状态并输出 guardrail 结果 | 用真实统一入口验证批准模板状态变化不会被缓存或模板选择绕过。 |
| `docs/PLAN.md`、`docs/ISSUES.md`、`docs/TESTS.md` | 记录 2026-08-13 的真实门禁结果 | 保持实现、验证和台账一致。 |

### 验证

真实 HTTP 评估通过：停用模板未执行，安全事故数为 0。


---

## 2026-08-13：真实模板/目录门禁判定修复与敏感字段验证

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/data_analysis_agent.py` | 规范化模板声明中的 `schema.table` 与 `table.column` 名称 | 使目录白名单与 SQL 裸标识符比较一致。 |
| `scripts/run_nl2sql_http_eval.py` | 模板实际执行判定改为 `approved_template + template_id`；将四类 guardrail 失败计入安全事故数 | 防止固定指标/客服回退掩盖模板门禁问题。 |
| `tests/test_data_analysis_agent_security.py` | 新增限定名称白名单回归 | 固定目录模板声明兼容性。 |
| `docs/PLAN.md`、`docs/ISSUES.md`、`docs/TESTS.md` | 记录四类真实门禁 PASS | 保持 P2 安全证据完整。 |

### 验证

真实 HTTP 评估通过：inactive、unapproved、non-queryable table、sensitive column 均不执行目标 approved template；安全事故数 0。


---

## 2026-08-13：声明式批准模板参数 schema 与边界执行

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/data_analysis_agent.py` | 参数绑定读取 `metadata_json.parameters`，支持受控 integer/date_range/system_now/system_constant/authorized_scope_only | 从隐式固定参数升级为模板声明的、可评估的参数契约。 |
| `app/services/data_catalog_seed.py` | 三条批准模板补充用户范围、日期范围、月初、limit 参数 schema | 让目录模板可表达安全参数边界。 |
| `scripts/run_nl2sql_http_eval.py` | 增加超大 limit 和超长日期范围的真实 HTTP guardrail | 将参数边界纳入 P2 安全事故基线。 |
| `tests/test_data_analysis_agent_security.py` | 新增参数 schema、subject 授权边界回归 | 固定安全绑定行为。 |

### 验证

真实 HTTP 评估通过：`前10000条` 与 `过去365天` 均不执行目标模板，安全事故数 0。


---

## 2026-08-13：新增 Agent 功能与业务设计总览

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md` | 新增 Agent 功能、业务映射、执行范式、设计原因、安全边界和未完成事项总览 | 将分散在代码、计划、问题和专项文档中的 Agent 设计集中，便于业务沟通、答辩和后续维护。 |
| `docs/PLAN.md`、`docs/ISSUES.md`、`docs/TESTS.md` | 记录总览文档创建、原因和检查结果 | 保持项目四类台账同步。 |

### 验证

文档文件已创建，内容覆盖当前 Agent 体系和业务设计基线。


---

## 2026-08-13：确定性 authorized subject scope 接入 DataAnalysisAgent

### 文件变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `app/services/subject_scope.py` | 新增基于 `CustomerAdvisorAuthorization` 的有效期/状态/撤销过滤服务 | 将客户经理/投顾可见客户范围交给确定性后端，而非模型或用户输入。 |
| `app/services/data_analysis_agent.py` | `authorized_scope_only` 支持授权 subject 集合与显式 subject 校验 | 防止内部人员查询未分配客户。 |
| `app/api/customer_service.py` | 模板执行前加载 `SubjectScopeService` 并传入参数绑定 | 让真实 DataAnalysisAgent 执行链路使用客户授权范围。 |
| `tests/test_subject_scope.py` | 新增已分配/未分配/多客户未指定 subject 测试 | 固定客户数据范围安全契约。 |

### 验证

Python 编译通过；真实 CustomerAdvisorAuthorization HTTP 回归列为下一切片。

## 2026-08-13：授权客户范围版本化与真实回归

- `app/services/permission_scoped_cache.py`：新增 `bump_subject_scope_version`；`PermissionContext.from_user_cached` 在 Redis 存在顾问 subject scope 版本时将其拼入 `data_scope_version`。
- `app/api/profile.py`：客户新增或撤销 `CustomerAdvisorAuthorization` 成功提交后，递增对应顾问的 subject scope 版本。
- `app/services/data_analysis_agent.py`：兼容测试与内部受控调用的已授权 subject alias，保留明确未授权的 `customer-*` / `user-*` 标识以返回 `unauthorized_subject`，不把任意文本作为 subject。
- `scripts/test_subject_scope_http.py`：补充真实授权、未授权、撤销和版本键断言，并在 finally 清理临时数据。
- 验证：`langchain_env` 下相关单测 7 通过；真实 ASGI + PostgreSQL + Redis HTTP 回归通过。

## 2026-08-13：后台批量顾问授权范围管理

- `app/schemas/admin.py`：新增后台单条授权与批量更新请求 schema。
- `app/api/admin.py`：新增 `PUT /admin/advisor-authorizations/batch`，支持授权新增/更新、撤销、基础用户状态校验、事务审计，以及提交后的 subject scope version 失效。
- `scripts/test_subject_scope_http.py`：扩展为验证后台批量 grant/revoke 后的版本递增和统一 Agent 查询权限变化；清理逻辑支持多条临时授权。
- 验证：真实 HTTP 回归与 7 条相关单测通过。

## 2026-08-13：顾问授权领域服务

- 新增 `app/services/advisor_authorization_service.py`：集中授权的 grant/update、revoke、客户侧 revoke 和缓存范围失效。
- `app/api/profile.py`：客户自助授权与撤销改为调用领域服务。
- `app/api/admin.py`：后台批量授权与撤销改为调用领域服务，保留 API 层的用户校验、审计和事务边界。
- 新增 `tests/test_advisor_authorization_service.py`：验证重复顾问 ID 仅触发一次 subject scope 版本递增。
- 验证：8 条单测、真实 subject scope HTTP 回归均通过。

## 2026-08-13：授权范围失效 Outbox 化

- `AdvisorAuthorizationService.enqueue_scope_invalidation_events`：在授权变更事务内按顾问去重创建 `authorization.subject_scope_invalidate` 事件。
- `app/api/profile.py` 与 `app/api/admin.py`：不再在提交后直接修改 Redis，改为持久化授权范围失效事件。
- `workers/outbox_worker.py`：新增 `authorization.* → authorization` stream 路由。
- 新增 `workers/authorization_worker.py`：消费授权失效事件并安全递增 subject scope version。
- `scripts/test_subject_scope_http.py`：验证 Outbox 落库、发布、stream 事件及消费后的版本变化。
- `tests/test_advisor_authorization_service.py`：补充 Outbox 事件按顾问去重断言。

## 2026-08-13：authorization-worker 纳入 Docker Compose

- `docker-compose.p0.yml`：新增 `authorization-worker` 服务，使用与 Outbox/Notification Worker 相同的 API 镜像和数据库/Redis 依赖。
- `scripts/test_subject_scope_http.py`：兼容真实 authorization worker 已运行的场景，避免测试 consumer group 抢消费导致版本断言不稳定；无 worker 时仍保留临时 consumer fallback。
- Playwright：修复客服工作台 Modal 遮挡背景 compose、消息列表 strict mode 和异步通知轮询问题；真实数据 Chrome for Testing 15 项全部通过。

## 2026-08-13：authorization worker 失败处理第一切片

- `workers/authorization_worker.py`：抽取 `decode_value` 与 `handle_message`，未知事件安全忽略，处理异常不 ACK 以保留 pending claim 重试。
- 新增 `tests/test_authorization_worker.py`：覆盖字段解码、有效事件版本递增和未知事件忽略。
- 当前仅完成可测试的第一切片，真实 Redis 异常注入、dead-letter 与人工重放待后续实现。

## 2026-08-13：authorization worker 运维边界第一切片

- `app/api/admin.py`：通用 Outbox dead event retry 允许 `authorization.subject_scope_invalidate`，不再只允许 notification 事件。
- `tests/test_authorization_worker.py`：新增缺失 advisor_id 的失败断言；相关回归达到 12 条通过。
- 当前未新增授权专用 dead-letter，优先复用通用 Outbox dead/retry，待真实 Redis pending/XCLAIM 回归后再决定。

## 2026-08-13：authorization worker 真实 pending/XCLAIM 回归

- 新增 `scripts/test_authorization_worker_retry.py`：真实 Redis 验证授权事件未 ACK 时进入 pending、由第二 consumer `XCLAIM`、重试处理后 ACK 并清空 pending。
- 当前授权 Worker 的 Redis Streams 可靠消费闭环已由单测和真实 Redis 回归共同覆盖。

## 2026-08-13：授权可靠消费阶段验收与阶段切换

授权领域服务、Outbox、authorization-worker、Redis pending/XCLAIM 与真实 E2E/HTTP/单测证据链已完成。后续开发重点转入 P2 NL2SQL 产品化和评估门禁，不再继续添加授权 Worker 专用 dead-letter，除非监控/运营需求明确提出。

## 2026-08-13：P2 受控参数澄清阶段启动

开始将 DataAnalysisAgent 的参数越界/缺失响应从泛化安全拒绝升级为受控澄清；本阶段不改变 SQL 权限模型、不开放自由 SQL，也不扩大授权 subject scope。

## 2026-08-14：参数澄清与动态目录门禁顺序缺陷记录

发现批准模板的动态目录白名单在参数绑定前执行，导致可恢复参数错误被 `catalog_denied` 提前覆盖并回退到固定指标。已补充审计诊断与 SQL 字符串字面量/列引用单元修复；下一步调整参数绑定与动态目录执行门禁的顺序，保持 SQL 执行安全边界不变。

## 2026-08-14：受控参数澄清与目录门禁顺序修复

- `DataAnalysisAgent.analyze_template_attempt`：参数绑定与可恢复错误判断提前到动态目录白名单之前；SQL 执行前仍强制动态目录表/字段校验。
- `DataAnalysisAgent`：移除重复模板匹配实现，精确问题匹配优先；SQL 字符串字面量不再被误识别为列。
- `customer_service`：可恢复参数错误不再被固定指标兜底覆盖。
- `scripts/test_data_analysis_agent_http.py`：增加真实参数澄清回归并清理临时 probe 数据。

## 2026-08-14：Agent 共享记忆与长时运行方案评估归档

新增方案文档已完成架构评估并写入决策：业务方向作为当前优先级依据；共享记忆推迟到 P3 的最小工作记忆 MVP；长时恢复推迟到 P4，优先复用现有 AsyncTask、Outbox、Worker 和状态机。本轮不引入运行时代码变更。
## 2026-08-15 客户 RAG 混合检索主链路恢复

- `CustomerRagService` 支持注入 `QwenProvider`，查询默认生成 1024 维 query embedding，并将其传给检索器。
- 客户工具与 `/customer-rag/answer` 已接入 embedding provider；embedding 失败时降级到 text-only，并返回 `embedding_degraded` / `text_fallback` 状态。
- `KnowledgeRepository.search_hybrid` 改为独立 vector/text candidate lists + RRF（k=60）融合，并统一应用知识库、类别、权限和 active version 过滤。
- 新增“客户规模”回归测试，验证客户查询会传入 embedding（当前本地环境缺少 `pgvector` Python 依赖，pytest 收集阶段无法执行；已完成 py_compile 语法检查）。
- 增量更新现状：文档上传已投递 `knowledge.document_created`，默认 Docker Compose 已编排 outbox 与 knowledge worker，worker 已执行版本化、chunk embedding 复用和旧版本切换。仍缺少针对知识 ingestion 的专用失败重试/死信、状态查询和端到端验收，因此标记为“主流程已实装、运营闭环未完成”。
## 2026-08-15：RAG 增量更新状态查询与 Outbox 重试入口

- 新增 `GET /knowledge/documents/{document_id}/ingestion-status`，返回文档状态、当前版本、错误信息及最近 ingestion Outbox 事件。
- 管理员 Outbox dead event retry 允许 `knowledge.*` 事件，支持重新投递文档 ingestion。
- 增量更新主流程已有 Docker Compose worker；本轮补齐了可观测状态和人工重试入口，知识 worker 消费失败的专用 dead-letter/重试策略与真实端到端验收仍待完成。
## 2026-08-15：结构化 Qwen 意图分类调用接入（默认关闭）

- `QwenProvider.classify_intent` 使用 chat completion JSON 模式输出受限 StructuredIntent 候选。
- `CustomerAgentExecutionService` 增加可选 fallback 接口；仅当配置开启且显式注入服务时尝试调用。
- 失败时保留规则 planner，不影响既有执行路径。
- 新增 `LLM_INTENT_FALLBACK_ENABLED` 与超时配置；真实模型尚未默认接管业务执行。
## 2026-08-15：langchain_env 意图基线验证与规则解析修复

- 使用 `C:\Users\tx360\.conda\envs\langchain_env\python.exe` 验证意图相关测试，确认 `pgvector`、Pydantic 和 OpenAI 依赖可用。
- 修复复杂中文规则 baseline 的金额提取、购买表达、推荐/购买组合、多意图优先级和交易确认约束。
- 意图相关测试结果：`23 passed`。
- 真实 Qwen fallback 仍保持默认关闭，待后续使用真实 API key 执行灰度评测。
## 2026-08-15：结构化意图 fallback 灰度评测脚本

- 新增 `scripts/evaluate_intent_fallback.py`：离线验证规则 baseline，`--live` 调用真实 Qwen 并通过 Schema + Policy Resolver 校验。
- 评测只生成 JSON 报告，不接管业务执行；默认不访问网络。
- 当前 15 条复杂语料规则 baseline：`baseline_failures=0`。
## 2026-08-15：真实 Qwen StructuredIntent 灰度通过与安全收紧

- 修复 `QwenProvider` 与 `StructuredIntentProvider` 的接口适配，新增 `classify` 别名。
- `LLMIntentFallbackService` 对模型偶发的 list 字段对象形式做受限归一化，随后仍由 Pydantic Schema 校验。
- `IntentPolicyResolver` 收紧未知上下文：规则 `T0_UNKNOWN` 不得被 LLM 单独升级为交易工作流。
- 真实 Qwen 15 条复杂语料灰度结果 `live_failures=0`；相关安全/基线测试 `20 passed`。
- 业务开关仍默认关闭，灰度结果不等于允许模型接管执行。
## 2026-08-15：交易确认约束语义统一

- StructuredIntent 的交易约束由 `must_not_submit_without_confirmation` 更名为 `must_not_submit_without_verified_customer_confirmation`。
- 明确模型不能替代客户授权，但不会阻止客户完成前端确认并通过后端令牌/版本/身份/有效期/幂等校验后的确定性下单。
## 2026-08-15：统一 Agent StructuredIntent Shadow Mode

- 新增 `LLM_INTENT_SHADOW_ENABLED`，默认关闭。
- 启用后 `CustomerAgentExecutionService` 创建/使用 StructuredIntent fallback，仅写入 `customer_agent.intent_shadow_evaluated` 审计记录。
- 审计包含 rule、llm、resolved、错误与 `execution_changed=false`，不会改写最终计划。
- API/HTTP 与 WebSocket 入口均传递应用 settings；新增 Shadow Mode 单测，相关回归 `24 passed`。
## 2026-08-15：StructuredIntent Shadow Mode 聚合观察接口

- 新增 `GET /admin/intent-shadow/metrics`（`audit:read`），汇总 Shadow 审计的 template 差异、错误、模型交易候选、未知上下文交易阻断和确认约束记录。
- 返回最近观测元数据但不返回客户 query 原文。
- 新增接口合同测试；相关回归 `25 passed`。
## 2026-08-15：受控 StructuredIntent fallback allowlist

- 新增 `LLM_INTENT_FALLBACK_ALLOWLIST`；默认只配置 `T1_INFORMATION_QUERY,T2_PRODUCT_RECOMMENDATION,T7_HUMAN_HANDOFF`。
- `CustomerAgentExecutionService` 只将 allowlist 中且不需要澄清的低风险候选映射为计划；当前支持产品推荐和人工交接。
- 所有交易/草稿类模板继续保持规则路径；审计以 `execution_changed` 记录是否实际改变计划。
- 安全回归 `26 passed`。
## 2026-08-15：T1 信息查询受控 fallback

- T1 fallback 只映射 `company_info/company_business/faq/workflow/policy/risk_public`，统一走公开客服 RAG。
- 模型不能通过 T1 触发订单、工单、资产或账户私有事实查询；非法 primary intent 降级为 `faq`。
- 新增安全回归，相关测试 `28 passed`。
## 2026-08-15：StructuredIntent fallback 运行时保护

- 新增 `LLM_INTENT_FALLBACK_MAX_CONCURRENCY`（默认 4，运行时下限 1）并对模型调用应用 `asyncio.Semaphore`。
- Shadow/fallback 审计新增 `fallback_decision`，管理员 metrics recent 记录可见。
- 相关回归 `29 passed`。

 
## 2026-08-27: P2 baseline verification and documentation sync 
 
This continuation did not change runtime code. It revalidated the existing controlled analysis path and synchronized progress records. 
 
- Verified DataAnalysisAgent template/read-only/catalog/parameter guards and permission-scoped catalog cache. 
- Verified offline NL2SQL evaluation and complex Chinese intent baseline. 
- Updated docs/PLAN.md and docs/TESTS.md with the current checkpoint, results, and next implementation target.
 
## 2026-08-27: P2 frontend protocol inspection 
 
No runtime code changed. Confirmed the existing unified AgentBubble receives backend analysis result data and recorded the safe next implementation boundary in PLAN.md.
 
## 2026-08-27: P2 frontend analysis step checkpoint 
 
No runtime code change in this checkpoint. Existing AgentBubble was inspected and preserved; the next code step remains structured analysis rendering.
## 2026-08-27: P2 frontend analysis card 
Added frontend/src/components/AnalysisResultCard.vue. It renders backend-provided analysis data only, does not execute SQL, and exposes query-plan read-only/free-SQL guard metadata.
-- 
## 2026-08-28: P2 frontend analysis card attempt 
 
Reviewed the workspace and found AnalysisResultCard.vue was not present despite the previous progress note. No runtime integration was applied in this step; existing AgentBubble.vue was preserved pending recovery of the missing component source.
