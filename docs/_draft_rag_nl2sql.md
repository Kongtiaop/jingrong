# RAG 知识库 + 受控 NL2SQL + 数据目录 —— 实现细节答辩简报

> 证据全部来自 `C:\Users\tx360\Desktop\wealth-manager\` 实际代码；每个结论均标注文件与行号。本简报刻意区分"真实实现"与"仅规划/未实现"的边界，这是答辩中最有说服力的部分。
> 生成方式：由代码分析员通读 14 个指定文件及关键支撑代码（worker、repository、QwenProvider、Settings、DataAnalysisAgent、目录仓库）后人工整理。

---

## 1. 【RAG 完整链路】文档 → 解析 → 切块 → Embedding → 存储 → 检索 → 引用/生成

### 1.1 入口与异步闭环（outbox 模式，真实运行）

- **上传**：`app/api/knowledge.py::upload_document`（L35）——只允许 `.txt/.md/.docx`（L19 `SUPPORTED_UPLOAD_SUFFIXES`，L45-46 校验）、≤20MB（L50-51）、TXT/MD 必须 UTF-8（L52-56）；文件落盘 `uploads/knowledge/{uuid4.hex}{suffix}`（L57-61）；对全文做 `sha256` 生成 `content_hash`（L64），`document_key = "upload:{hash}:{uuid12}"`（L65）；随后 `app/repositories/knowledge.py::create_document`（L37-46）在同一数据库事务里写 `KnowledgeDocument`（状态 `processing`）+ Outbox 事件 `knowledge.document_created`（L42-43，事务性发件箱，保证"业务写库与事件入队"原子一致）。接口返回 202 `pipeline_status="queued"`（L83-90）。L81-82 注释明确"outbox publisher 发送到 Redis，knowledge_worker 消费执行解析、切块并写 pgvector chunk"。
- **发布**：`workers/outbox_worker.py::OutboxPublisher.publish_once`（L31-65）——轮询 `pending` 事件（`with_for_update(skip_locked=True)`，L38），`attempt += 1`（L44），按事件类型路由（L94-109，`knowledge.* → stream:wealth:ingestion`，L99-100）发布到 Redis Stream，成功标 `published` 并回写 `redis_message_id`（L67-78），失败重试、超过 `task_max_attempts=3` 进 `dead`（L80-92）。
- **消费**：`workers/knowledge_worker.py::main`（L20-48）——`RedisStreams` 消费组 `claim_pending + consume_once`（L33-34），对每条消息调 `KnowledgeIngestionService.ingest_document(document_id)`（L40），最后 `ack`（L45）；异常打日志不中断循环。**这是一个"事件发布 → 异步消费 → 业务结果回写"的真实闭环**，docker-compose（`docker-compose.yml` L142 / `docker-compose.p0.yml` L150）声明了 `python -m workers.knowledge_worker`。

### 1.2 解析与切块（`app/services/knowledge_ingestion.py`）

- **解析**：`ingest_document`（L34）——`docx` 用 python-docx 逐段取 `paragraph.text` 拼 `\n`（L45-50，缺依赖则明确 `RuntimeError`）；其他按 UTF-8 读纯文本（L51-52）。`parser_type` 记录为 `docx/plain_text` 并写入版本（L54-56）。
- **切块**：`_chunks(text, *, size=1000, overlap=120)`（L21-26）——**字符级固定窗口滑动切分**：先对每行 `rstrip` 再整体 `strip`（L22-23）；`step = size - overlap = 880` 字符（L25）；每块 1000 字符、相邻重叠 120 字符（L26）。**注意是"字符窗口"，不是语义切块/标题感知切块**（`title_path` 在 ingestion 里写死为空字符串，L77）。
- **版本与哈希**：全文 `sha256` 作为版本 `content_hash`（L53）；每个 chunk 再算独立 `sha256`（L58）。

### 1.3 Embedding（`app/infrastructure/qwen.py::embed` L61-91）

- 调用 DashScope OpenAI 兼容端点（`app/core/settings.py` L31 `qwen_base_url=https://dashscope.aliyuncs.com/compatible-mode/v1`），`model = settings.qwen_embedding_model`（L77），**强制 `dimensions=1024`**（L79，`settings.embedding_dimension`；`settings.validate_p0` L83-84 校验必须等于 1024）。
- **模型名要如实说**：代码默认值（`app/core/settings.py` L33）是 `qwen3.7-text-embedding`；`.env.example` L16 声明的是 `QWEN_EMBEDDING_MODEL=text-embedding-v4`；实际 `.env` **没有**覆盖该项，所以当前运行态是默认 `qwen3.7-text-embedding`。两者都是 Qwen/DashScope 系 embedding，**1024 维是硬约束**（库表 `VECTOR(1024)`、Milvus `dim=1024`、`QwenProvider.embed` L89-90 校验返回维度，不符直接抛 `ValueError`）。答辩话术：**"Embedding 走 DashScope Qwen 系模型（text-embedding-v4 / qwen3.7-text-embedding 可配），统一 1024 维，维度不符直接抛错。"**
- 实现细节：**一次请求只嵌一条文本**（L66-68 注释：DashScope 对多输入会重置连接；逐条发送保证顺序确定，牺牲吞吐换确定性）；每条最多重试 3 次（L74-85）；返回条数必须等于入参（`knowledge_ingestion.py` L68-69）。ingestion 侧按 **batch=4** 分组调 embed（`knowledge_ingestion.py` L65-66）。

### 1.4 存储（pgvector）

- `app/models/knowledge.py::KnowledgeChunk.embedding = VECTOR(1024)`（L87），`KnowledgeDocumentVersion`（L52-72）记录版本，`KnowledgeDocument.current_version_id`（L37）指向当前版本。默认后端 `app/core/settings.py` L20 `vector_store_backend="pgvector"`，向量与业务数据**同库同事务**。

### 1.5 检索（混合检索，真实实现）

- **应用层适配**：`app/services/knowledge_retrieval.py::WealthKnowledgeRetriever.search`（L28-67）——给了 `query_embedding` 就走 `repository.search_hybrid`（L38-46，向量+全文），否则 `search_text`（L47-55，纯 ILIKE 兜底，pairs 打 1.0 分）；包装成 `KnowledgeCitation`（L10-19：chunk_id/document_id/version_id/content/score/title_path/page_number/metadata）。
- **混合检索本体**：`app/repositories/knowledge.py::search_hybrid`（L136-199）——
  - **文本腿**：PostgreSQL 全文检索 `to_tsvector('simple', content)` + `websearch_to_tsquery('simple', query)`，`ts_rank_cd` 排序（L158-169）；
  - **向量腿**：pgvector `cosine_distance(query_embedding)` 升序（L172-183）；
  - 两条腿各取 `candidate_k = max(top_k*4, 20)` 候选（L138），**RRF 融合**：`score += 1/(60+rank)`（L192-196，每条腿各自排名），合并去重（L185-187）按融合分降序取 `top_k`（L198-199）。
  - **所以答案是：混合检索（向量 + PG 内置全文检索），不是独立 BM25**；`simple` 配置下全文检索对中文是逐 token 匹配，实际靠向量腿扛语义。无 embedding 时降级 `search_text`（L201-214，`ILIKE %term%`，term≥2 字符，中文标点先替换成空格，L208）。
- **Top-K**：默认 `top_k=5`（retriever 与 CustomerRagService 均默认 5）。
- **置信度与拒答/转人工**（真实实现，链路如下）：
  - `app/services/customer_rag_service.py::CustomerRagService.answer`（L80-102）：问候语正则直接短路（L81-83，`greeting`）；`product_recommendation` 直接走实时库（L84-85，`database_recommendation`，不依赖缺失的产品 RAG 文档）；否则 `normalize_customer_query`（L51-70）做**同义词扩展**（"客服电话"→"客服热线 联系电话 联系方式"等，L57-64，提升中文召回），按意图选 `KnowledgeScope`（L87，`scope_for_customer_intent` L32-35）；8 秒超时嵌查询向量（L92，`asyncio.wait_for(..., timeout=self.embedding_timeout)`，`embedding_timeout=8.0` L74）；检索失败自动降级文本检索并标记 `evidence_status=embedding_degraded/text_fallback`（L95-99）。
  - **置信度过滤**：`useful = [item for item in citations if item.score >= self.min_score]`（L100），`min_score=0.0001`（L74 默认值）。**要能解释为什么阈值这么低**：混合检索的分数是 RRF 融合值（单腿最高约 1/61≈0.016），不是余弦相似度；0.0001 的作用是滤掉"两条腿都没召回、靠兜底路径凑进来的 0 分项"。真正触发拒答的是**没有任何 ≥ 阈值的引用**。
  - **拒答 → 转人工**：无有效引用 → `answer_mode="handoff"`、`evidence_status="low_confidence"`、`handoff_required=True`（L101-102），话术"当前知识库没有足够依据，建议转人工客服或创建工单"。`app/services/customer_task_executor.py`（L95-99）对 `low_confidence` **只做一次查询改写重试**，仍失败才转人工（L110 澄清话术"当前资料不足，可以补充问题或转人工客服"）。转人工实体动作在 `app/services/customer_rag_handoff.py::handoff`（L20）：建工单 + 建 `CustomerServiceSession(status=waiting_human, mode=human, handoff_reason=rag_low_confidence)`（L39）；`app/api/customer_rag.py`（L58-60）在 `handoff_required` 时触发。意图层 `app/services/customer_intent.py`（L85-86）对"人工/客服接入/转人工"关键词直接给 `human_handoff`（confidence .99）。
- **引用与生成**：`app/services/customer_rag_answer.py`——无引用/转人工 → 不生成（L20-21，`not_generated`）；把引用拼成 `[引用 N] 内容` 证据块（L24），系统提示词强制"只能根据引用回答、不得编造/承诺收益/执行交易、资料不足必须明说并建议转人工"（L26-28），`temperature=0.1`（L34）；回答模型未配置 → 提示信息（L22-23）；异常 → `generation_failed` 且保留"已保留检索依据并建议转人工"（L40-41）。
- **LangChain 适配（可选）**：`app/integrations/langchain_retriever.py`——若装了 langchain 才定义 `LangChainWealthRetriever(BaseRetriever)`（L7-14 的 try/except，L16-47），异步 `_aget_relevant_documents`（L26-47）调自己的 `WealthKnowledgeRetriever` 并转成 `Document`（把 chunk_id/document_id/version_id/score/title_path/page_number 放进 metadata）；同步方法直接抛错（L49-50）。**项目不依赖 LangChain 做检索，LangChain 只是可选外壳。**
- 管理端检索接口 `app/api/knowledge.py::search_knowledge`（L110-124）：返回 `retrieval_mode="hybrid-ftS-pgvector"`（L117），embedding 失败降级 `"text-ilike-fallback"`（L119-120）——模式可观测。

---

## 2. 【版本化/增量】真实实现 vs 规划

**结论：文档版本化 + chunk 级 embedding 复用是真实实现；文档级"内容相同跳过整篇"去重没有实现。**

证据链：

- **版本表**：`KnowledgeDocumentVersion`（`app/models/knowledge.py` L52-72）有 `version_no`（L57，`UniqueConstraint(document_id, version_no)` L71）、`content_hash`（L58）、`parser_type`（L59）、`status`（L60）、`chunk_count`（L61）、`activated_at`（L64）；`KnowledgeDocument.current_version_id`（L37）指向当前版本。
- **版本递增**：`create_version`（`app/repositories/knowledge.py` L105-113）取该文档最大 `version_no + 1`（L107-109）。
- **切换原子化**：`activate_version`（L122-127）——先把该文档**所有旧版本标 `superseded`**（L124），再把新版本标 `active` 并写 `chunk_count/activated_at`（L125），同时文档标 `active` 并更新 `current_version_id`（L126）。失败路径 `mark_document_failed`（L129-134）把文档与本次版本标 `failed`、记录截断 4000 字符的错误。
- **chunk 级增量（真实）**：`ingest_document`（`knowledge_ingestion.py` L61-70）——先 `active_chunk_embeddings_by_hash(document_id)` 取"当前激活版本里所有 active chunk 的 `content_hash → embedding`"（`knowledge.py` L91-104：JOIN 版本表，要求 chunk.status=active、version.status=active、embedding 非空）；新切块中 **hash 命中的直接复用旧向量，只对 hash 未命中的 chunk 调 embedding**（L62-70，`missing` 列表 + batch=4 嵌入）。返回 `IngestionResult(reused_chunks, embedded_chunks)` 两个计数（L85-92）。有专门的增量测试脚本 `scripts/test_knowledge_chunk_incremental.py`（L55-66，同一 doc-1 三次 ingest，用不同 embedding 实例，断言复用/新嵌计数）。
- **边界要说清楚（答辩加分点）**：这里的"增量"是**跳过 embedding 计算**（省最贵的 API 调用），但**仍然为每个新版本物理写入全套 chunk 行**（`save_chunks` L83、`knowledge.py` L115-120 插入全部 prepared chunks，只是向量来自复用池，L79 `reusable.get(chunk_hash) or embedded[chunk_hash]`），因为版本行必须完整可回溯。这是"embedding 级增量复用 + 全量版本落库"，不是"物理 chunk 行级增量保留"。
- **文档级去重未实现**：`ingest_document` 不检查"全文 hash 与上一版相同则跳过"——即使内容没变也会创建新版本（只是所有 chunk 都命中复用、`embedded_chunks=0`）。`document_key` 里虽然嵌了 `content_hash`（`app/api/knowledge.py` L65），但没有用它做跳过去重。

---

## 3. 【知识 Scope 隔离】

- **模型**：`app/common/knowledge_scope.py::KnowledgeScope`（L6-10，frozen dataclass）——三维度：`knowledge_base_ids`、`categories`、`permission_levels`；`allows()`（L20-22）是 AND 语义，**空元组 = 通配**。
- **预置档位**：
  - `public_customer()`（L13-14）：categories = `(company_info, company_business, faq, workflow, policy, risk_public)`，permission_levels = `(public, customer_visible)`——客户侧客服 RAG 用（`customer_rag_service.py::scope_for_customer_intent` L32-35）。
  - `internal_advisor()`（L16-18）：categories = `(company_business, policy, risk, suitability)`，permission_levels = `(public, internal, advisor)`——顾问内部档。
- **落地方式**：scope 不是应用层过滤，而是**下推成 SQL 行级过滤**（`knowledge.py::search_hybrid` L150-156 / `search_text` L204-207）：`knowledge_base_id IN (...)`、`category IN (...)`、`permission_level IN (...)`，叠加"chunk.status=active、doc.status=active、doc.deleted_at IS NULL、version.status=active"四重过滤（L140-145）。隔离维度落在 `KnowledgeDocument` 行属性上（`knowledge_base_id`、`category`、`permission_level`，`app/models/knowledge.py` L27/L34/L35），检索 JOIN 三层表（chunk→document→version）统一过滤。
- 检索只命中**激活版本**（version.status='active'），旧版本内容天然不可见——版本隔离与 scope 隔离叠加。

---

## 4. 【VectorStore Port / Adapter】

- **端口**：`app/ports/vector_store.py`——`VectorStore(Protocol)`（L19-22）仅三个方法 `upsert/search/delete`；数据契约 `VectorDocument(id, text, vector, metadata)`（L5-10）/ `VectorHit(id, score, text, metadata)`（L12-17）。
- **PgVectorStore**（`app/infrastructure/vector_store/pgvector.py`）：SQLAlchemy ORM 直连三张表（L5 导入 KnowledgeChunk/Document/Version）；`upsert`（L19-45）按 id get-or-create（新增写全字段 L26-38，更新只改 content/metadata_json/embedding/status L40-44）；`search`（L47-83）打分 `score = 1 - cosine_distance`（L67），JOIN document+version 并套 status/deleted 过滤（L53-59），支持 `filters: {knowledge_base_id, category}`（L62-66），`ORDER BY score DESC LIMIT top_k`（L68）；`delete`（L85-90）硬删 chunk 行。L10-14 文档字符串明说"PostgreSQL 是参考实现，故意与 Milvus 共用同一端口以便配置切换"。
- **MilvusVectorStore**（`app/infrastructure/vector_store/milvus.py`）：懒加载 `pymilvus.MilvusClient`（L7-11，默认 `http://127.0.0.1:19530`，L5）；`ensure_collection`（L12-20）建集合 `wealth_documents`（`id VARCHAR pk` + `vector FLOAT_VECTOR(1024)` + **AUTOINDEX/COSINE**，L17-19）；`upsert`（L21-23）写 `{id, vector, text, **metadata}` 并 flush；`search`（L24-31）的 filter 表达式**只允许白名单键**（L27-28：`knowledge_base_id/category/document_id/version_id/status`，其余过滤键被丢弃，防注入），`output_fields`（L29）取 text 与关键元数据；`delete`（L32-34）按 id 删。
- **Factory**（`app/infrastructure/vector_store/factory.py::create_vector_store` L6-16）：按 `settings.vector_store_backend` 小写分支：`pgvector → PgVectorStore(database)`（L8-9），`milvus → MilvusVectorStore(uri=settings.milvus_uri, collection_name=settings.milvus_collection, dimension=settings.embedding_dimension)`（L10-14），其他值抛 `ValueError`（L15）；`settings.validate_p0`（`app/core/settings.py` L75-76）只允许这两个值。
- **重要诚实点（答辩必讲）**：**当前在线检索路径并没有走 VectorStore 端口**——`search_hybrid`（`app/repositories/knowledge.py` L136）直接用 SQLAlchemy `VECTOR` 做 cosine + PG 全文 + RRF，因为混合检索必须与全文检索、权限过滤、版本过滤**同库 JOIN**，这正是 pgvector 的优势。端口抽象（Pg/Milvus 双实现 + factory 切换）是"可配置后端"的架构能力，Milvus adapter 是真实可运行实现，但生产语义检索在 pgvector 上。这样回答既展示架构能力又不夸大。

---

## 5. 【受控 NL2SQL】

### 5.1 策略层（`app/services/nl2sql_policy.py`）

常量（L5-9）：`MAX_SQL_LENGTH=20000`、`MAX_RESULT_ROWS=100`、`MAX_PARAMETER_COUNT=12`、`MAX_STRING_PARAMETER_LENGTH=256`、`MAX_DATE_RANGE_DAYS=366`。

- `SAFE_SELECT`（L11）：必须以 `SELECT` 或 `WITH ... SELECT` 开头。
- `BLOCKED_SQL`（L12）：禁止 `;` `--` `/* */` 以及 `insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|execute|call|pg_sleep|into`。
- `evaluate_query_safety(query)`（L23-31）：**入口只接受自然语言**——空查询拒绝（L26）；SQL-shaped 输入（命中 BLOCKED_SQL）→ `unsafe_sql_shaped_input`（L27-28）；**直接 SELECT 也被拒** → `free_sql_not_supported`（L29-30，"不允许自由 SQL"）。
- `validate_readonly_template(sql_template)`（L34-44）：长度 ≤20000（L36-37）、必须以 SELECT/WITH 开头（L38-39）、无阻断语法（L40-41）、无 `\x00`（L42-43）。
- `metric_code_for_query`（L47-52）：去掉空白转小写（L48）后按关键词表 `METRIC_KEYWORDS`（L14-20，如"待审核订单"→`pending_order_count`）映射指标码。
- `permission_allows`（L55-56）：超管或角色集合相交。
- `catalog_scope_allows`（L59-63）：SQL 引用的表/列必须是目录召回结果的子集——**"引用范围"校验**（返回 `catalog_table_scope_denied / catalog_column_scope_denied`）。

### 5.2 执行层（`app/services/data_analysis_agent.py::DataAnalysisAgent`）——三重校验真实存在

**第一重：模板白名单 + 结构只读校验**（`analyze_template_attempt` L259-301）：
1. 模板必须存在、`is_active`（L263-264）、`is_approved`（L265-266，**人工审核过的模板才可执行**）、角色匹配 `allowed_roles_json`（L267-268）；
2. `validate_template`（L269-271，= `validate_readonly_template`，L58-59）结构校验；
3. 失败原因全部结构化返回（`template_not_found/template_inactive/template_not_approved/role_denied/invalid_sql:*`，L261-271），可回显澄清话术。

**第二重：参数契约**（`bind_template_parameters` L116-175 + `_validate_parameters` L62-77）：
- 参数名必须匹配 `[A-Za-z_][A-Za-z0-9_]*`（L66）；数量 ≤12（L63）；字符串参数 ≤256 字符（L68-69）；整数 |值| ≤1,000,000（L70-71）；`limit` 必须 1..100（L72-73）；日期范围 ≤366 天（L74-76）。
- 参数 schema 来自模板 `metadata_json.parameters`（L118），分四类来源（L143-175）：`system_constant`（默认值，L148-149）、`system_now`（本月 1 日，L146-147）、`natural_language`（从 NL 正则抽取：`前N条` L130、`近N天` L131、`客户ID` L132-133）、`authorized_scope_only`（L150-161：**只接受已授权 subject 集合内的 ID；查询里出现授权范围外的客户标识直接 `unauthorized_subject`，范围内有多个 → `missing_authorized_subject` 要求澄清**——绝不把任意文本当客户 ID，L135-141 注释明确）。
- 可恢复的参数错误（超范围/缺授权主体）→ `clarification_for_parameter_reason`（L99-113）生成安全澄清话术，而不是硬拒绝。

**第三重：目录引用范围 + 动态目录白名单**（`validate_catalog_allowlist` L226-251）：
- `_extract_references`（L193-223）用正则从模板 SQL 提取被引用表/列：先剥掉 `:param`（L209）与字符串字面量（L210）再分词（L211-222），排除 SQL 关键字/表别名（L205-207、L218-222），避免把 `'open'` 这种字面量当列引用；
- 模板自身声明（`target_table_names_json/target_column_names_json`，L227-228）做静态白名单：SQL 引用 ⊆ 声明（L232-235）；
- 动态目录（从 DataCatalogService 召回结果注入）：引用表 ⊆ **active 且 queryable** 的目录表（L236-239）；模板角色策略与表角色策略必须相交（`_role_allowed_for_resource` L253-257，L240-242）；引用列 ⊆ **可查询且非敏感** 列（L243-250）。

**只读执行的最终形态**（L285-295）：`SELECT * FROM (<模板>) AS approved_template LIMIT :__result_limit`（L285）——模板被外层 LIMIT 包裹双重限行，`__result_limit=100`（L286）；参数全部走 SQLAlchemy **绑定参数**（`text(bounded_sql), params`，L288，无字符串拼接）；`fetchmany(100)`（L288）；**执行失败先 `session.rollback()` 再返回 `execution_failed`**（L289-295，注释 L292-293 明确解释：否则失败事务会污染后续审计写入，导致 `InFailedSQLTransactionError`）。成功返回 `AnalysisResult`（L296-301），`query_plan` 标记 `read_only=True, free_sql=False, validated=True`（L300）。

**指标快捷通道**（`analyze` L318-342）：NL → `metric_code_for_query`（L315-316）→ 每个指标带**独立权限位**（如 `pending_order_count` 需要 `order:review/operation:read_staff/audit:read` 之一，L329-330；`open_risk_alert_count` 需要 `risk:alert_read` 等，L326-327），客户角色直接拒绝（L319-320）；SQL 是**硬编码只读聚合**（ORM count/sum，L328/331/334/337/340-341），`query_plan` 标记 `read_only=True, free_sql=False`（L342）。指标目录在 `_metrics`（L306-312）。

### 5.3 数据目录（Data Catalog）

**表结构**（`app/models/data_catalog.py`）：
- `DataCatalogDomain`（L11-26）：`code` 唯一（L15）、permission_level（L18）、allowed_roles_json（L19）、is_active（L20）。
- `DataCatalogTable`（L28-58）：物理名唯一约束 `(database_name, schema_name, table_name)`（L33-35、L57）；业务字段 `display_name/description/business_purpose/grain_description(数据粒度)/usage_guidance(推荐使用)/non_usage_guidance(不应使用)/table_kind`（L36-42）；权限字段 `permission_level/allowed_roles_json`（L43-44）；`aliases_json`（别名检索，L45）；`is_queryable/is_active`（L46-47）；**`embedding VECTOR(1024)`**（L48）。
- `DataCatalogColumn`（L78-108）：`column_name`（表内唯一，L83、L107）、`display_name/description/business_meaning/data_type/semantic_type/unit`（L84-89）、`is_sensitive/is_queryable`（L94-95）、`allowed_aggregations_json`（**字段级允许的聚合**，L96）、`aliases_json/example_values_json`（L97-98）、**`embedding VECTOR(1024)`**（L99）。
- `DataCatalogRelation`（L111-130）：FK 边 `(from_table_id, from_column_name → to_table_id, to_column_name)` 唯一（L115-118、L129）、`cardinality/join_guidance/is_preferred`（L120-123）。
- `DataCatalogExampleQuery`（L133-152）：`question/normalized_question/intent_code`（L138-140）、`target_table_names_json/target_column_names_json`（声明白名单，L141-142）、**`sql_template`（受审模板本体，L143）**、`explanation`（L144）、`allowed_roles_json/is_approved/is_active`（L145-147）、**`embedding VECTOR(1024)`**（L148）。
- `DataCatalogRevision`（L61-76）：**审计修订表**——每次表/列更新追加 `snapshot_json`（L69，`revision_no` 唯一递增 L65/L75、`changed_by` L70、`change_note` L71），元数据变更可追溯（`app/repositories/data_catalog.py::_append_revision` L176-194，表/列更新处 L127/L158 调用）。

**目录 Embedding 化**（`app/services/data_catalog_embedding.py`）：
- `table_embedding_text`（L17-30）/`column_embedding_text`（L33-47）/`example_embedding_text`（L50-61）把中文字段拼成结构化文本（业务域/表名/显示名/别名/说明/业务用途/粒度/推荐使用/不应使用；字段加语义类型/单位；示例加标准问题/意图/目标表列/解释）——embedding 的是"业务语义描述"，不是原始 SQL。
- `DataCatalogEmbeddingService.embed_pending(limit=100)`（L69-96）：只处理 `embedding IS NULL` 的行（表 is_active、列 is_queryable、示例 is_active 且 is_approved，见 `app/repositories/data_catalog.py::catalog_embedding_rows` L234-271），逐条 embed（L83）后 `save_embeddings` 回写（L90，仓库 L273-283）；单条失败只计数不中断（L94-95，`failed`）。触发方式是 Outbox 事件 `data_catalog.embedding_requested`（`enqueue_embedding` L72-86）。

**目录检索为 NL2SQL 提供候选范围**（`app/services/data_catalog_service.py::DataCatalogService.search` L30-193）：
- 角色过滤贯穿全程：`_role_clause`（`app/repositories/data_catalog.py` L65-70）用 `allowed_roles_json`（JSONB）做 `contains([role])` 的 OR 过滤；**无角色 → `column == []` 即空集，天然无结果**（L68-69）。
- 检索策略：先文本（L36-41，ILIKE 命中表名/显示名/说明/业务用途/别名 JSON，基准分 0.55，`_query_terms` L21-26 做单字二元切分）；若该角色下有已嵌入向量（`has_embeddings`，L45，仓库 L285-301），8 秒超时嵌查询（L47）→ 表/示例走 pgvector cosine（L48-65，`score = max(0, 1-dist)`，融合分 `max(0.55, 0.45+sim*0.55)` L56/L65；仓库 `search_tables_vector` L302-321、`search_examples_vector` L346-364），列只在已召回表内再检索（L78、L81-93，`top_k*2, ≥6`；仓库 `search_columns_vector` L323-344）；`retrieval_mode` 明确标注 `catalog-text-first / catalog-hybrid-text-pgvector / catalog-text-fallback`（L42/L66/L67，可观测降级）。
- 结果 payload（L170-190）：表（含全部业务元数据 + 列明细：semantic_type、unit、is_sensitive、allowed_aggregations、aliases、example_values，L132-148）、示例（含 `sql_template`，L176-186）、关系（含 `join_guidance`，L152-168）、`role_codes`（L173）、`retrieval_mode`（L174）。
- **关键：`"nl2sql_ready": False`（L188），并附护栏声明（L189）**——"Catalog retrieval only. SQL generation and execution remain disabled until AST validation, row-scope enforcement, read-only execution, and auditing are implemented." 也就是说：**目录召回 + 模板执行是现状（DataAnalysisAgent 已执行 approved 模板）；"模型自由生成 SQL 并执行"被明确关闭**。这是本项目最诚实也最安全的边界，答辩时务必主动讲。
- 缓存：`PermissionScopedCache`（L32-35/L191-192，60s TTL）按"查询+top_k+域 + 权限上下文"缓存目录结果，命中时 `retrieval_mode` 追加 `+permission-cache`（L35）。`CatalogSearchScope.from_user`（L16-21）从用户角色 + 权限上下文构造。
- API：`app/api/data_catalog.py`（L72-75）`GET /data-catalog/search`；客服侧上下文帮助 `app/api/customer_service.py`（L236-242）也复用 `DataCatalogService`（无 embedding provider，仅缓存）。

---

## 6. 【设计取舍】为什么不让模型直接生成 SQL

代码里能直接引用的证据：
- `evaluate_query_safety` 对 SELECT-shaped 输入返回 `free_sql_not_supported`（`app/services/nl2sql_policy.py` L29-30）；
- `DataAnalysisAgent` 只执行 `is_approved` 模板且套目录白名单（`app/services/data_analysis_agent.py` L265-266、L281-283）；
- 目录服务硬编码 `nl2sql_ready=False` 并声明需 AST 校验/行级范围/只读执行/审计齐备才放开（`app/services/data_catalog_service.py` L188-189）；
- RAG 回答提示词明令禁止输出 SQL（`app/services/customer_rag_answer.py` L26-28）；
- Qwen 意图解析系统提示词同样禁止输出 SQL/工具调用（`app/infrastructure/qwen.py` L38-46，尤其 L46"不得输出 SQL、订单提交命令或工具调用"）。

理由归纳：
1. **安全可证明性**：模型自由生成 SQL 无法静态保证"只读 + 行级数据范围 + 无注入"。模板 + 白名单 + 绑定参数 + LIMIT 包裹 + 失败回滚，使每条执行路径都可被静态审查。
2. **口径一致与合规**：金融指标（"待审核订单数""在售产品数"）必须是**统一口径**。模板由人审核（`is_approved` + `DataCatalogRevision` 审计），自由生成的 SQL 会导致同一问题不同答案、口径漂移，审计无法解释。
3. **权限的确定性**：目录按角色召回（`allowed_roles_json` JSONB contains）、模板按角色放行、subject 只取授权范围——三层权限全部走确定性数据，不依赖模型自觉。
4. **成本与延迟**：模板参数化执行快、可缓存、失败原因结构化（`invalid_parameters/catalog_denied/execution_failed`，`data_analysis_agent.py` L277/L283/L295），可给用户明确澄清而不是模型胡编。
5. **可回退路径**：模板执行失败 → `rollback + execution_failed` 或指标通道 → 明确话术，而不是把错误 SQL 暴露给用户。
6. **演进路线诚实**：`nl2sql_ready=False` + 护栏声明说明团队清楚"放开生成"需要补齐 AST 校验、行级范围、只读执行、审计四件事——这是**阶段化交付**，不是能力缺失。

---

## 7. 【面试高频问答】6 问 6 答

**Q1：检索分数这么低（min_score=0.0001），置信度判断是不是形同虚设？**
A：不是。要理解分数语义：我们的混合检索分数是 **RRF 融合值**（`1/(60+rank)`，单腿最高约 0.016），不是余弦相似度，所以不能用 0.5、0.7 这类阈值。0.0001 的作用是滤掉"两条腿都没召回"的 0 分项；真正的拒答条件是没有**任何**命中阈值的引用（`app/services/customer_rag_service.py` L100-102），此时 `evidence_status=low_confidence` → 转人工建工单。而且检索前的 embedding 失败会显式降级（`embedding_degraded/text_fallback`，L95-99），再叠加意图层与执行层（`app/services/customer_task_executor.py` L95-99 只重试一次改写）的护栏，形成"检索引擎降级 + 无证据拒答 + 转人工"的完整兜底。

**Q2：混合检索具体怎么做的？**
A：在 `KnowledgeRepository.search_hybrid`（`app/repositories/knowledge.py` L136-199）里，文本腿用 PostgreSQL 全文检索 `to_tsvector('simple') + websearch_to_tsquery + ts_rank_cd`（L158-161），向量腿用 pgvector `cosine_distance`（L174），各取 `max(top_k*4, 20)` 候选（L138），然后用 **RRF（1/(60+rank)）** 融合排序取 top_k（L192-199）。之所以在同一 SQL 里做，是因为要同时 JOIN 文档、版本表做权限与激活版本过滤（`scope` 下推成 `IN` 条件，L150-156）。没有独立 BM25，文本腿就是 PG 内置 FTS；中文语义主要靠向量腿，文本腿补充精确词匹配。embedding 不可用或超时时自动降级 `ILIKE %term%`（`search_text`，L201-214），且 `retrieval_mode` 会在 API 响应里如实标注（`app/api/knowledge.py` L117/L120）。

**Q3：增量更新是真实的吗？怎么做的？**
A：真实的，但要讲清边界。每次上传/重跑 ingestion 会**新建版本**（`version_no` 递增，`app/repositories/knowledge.py` L105-113），`activate_version`（L122-127）把旧版本整体标 `superseded`、文档 `current_version_id` 指向新版，检索只 JOIN 激活版本——**版本切换原子、旧版可回溯**。增量体现在 chunk 层：先取当前激活版本所有 chunk 的 `content_hash → embedding`（`active_chunk_embeddings_by_hash`，L91-104），新切块 hash 命中直接复用向量，只对变化 chunk 调 embedding（`app/services/knowledge_ingestion.py` L61-70；`IngestionResult` 返回 `reused_chunks/embedded_chunks` 两个计数，L85-92，有专门测试脚本 `scripts/test_knowledge_chunk_incremental.py` 验证）。**注意**：我们省的是最贵的 embedding 调用，新版本仍会物理写入全套 chunk 行（`save_chunks` L115-120），保证版本完整。另外文档级"内容没变就跳过整篇"的去重**没有实现**——这也是我在评审里标出的待办。

**Q4：pgvector 和 Milvus 两个 adapter 怎么共存？为什么现在主要用 pgvector？**
A：`app/ports/vector_store.py`（L19-22）定义了 `VectorStore` Protocol（upsert/search/delete），pgvector 与 Milvus 都实现它（`app/infrastructure/vector_store/pgvector.py`、`milvus.py`），`factory.create_vector_store`（`app/infrastructure/vector_store/factory.py` L6-16）按 `VECTOR_STORE_BACKEND` 环境变量切换，settings 校验只允许这两个值（`app/core/settings.py` L75-76）。pgvector 的 `search` 会 JOIN 文档/版本表并过滤 status/deleted（L53-59），Milvus 的 `search` 把过滤键白名单化（只允许 knowledge_base_id/category/document_id/version_id/status，`milvus.py` L27-28）拼成 filter 表达式。**但要诚实**：当前在线混合检索路径直接走 pgvector SQL（`search_hybrid`），因为 RRF 融合 + 全文检索 + 角色/版本过滤需要与业务表同库 JOIN，这正是我们选 pgvector 的原因；Milvus adapter 是可配置的替代后端实现，属于架构上的可切换能力，不是当前生产路径。

**Q5：受控 NL2SQL 到底怎么防注入、保证只读？**
A：四层。①入口层 `evaluate_query_safety`（`app/services/nl2sql_policy.py` L23-31）：用户输入只能是自然语言，直接 SELECT 都拒（`free_sql_not_supported`），更别说 DROP/分号/注释；②模板层：只执行 `is_approved` 且角色匹配的模板（`app/services/data_analysis_agent.py` L265-268），`validate_readonly_template`（L34-44）强制 SELECT/WITH 开头、禁阻断语法；③目录层 `validate_catalog_allowlist`（L226-251）：从模板 SQL 正则提取引用表/列（剥掉参数和字符串字面量防止误判，L209-210），要求 ⊆ 模板声明白名单（L232-235）⊆ 目录动态白名单（active+queryable，且非敏感列，L236-250），角色策略还要与表策略相交（L240-242）；④执行层：SQLAlchemy **绑定参数**（无字符串拼接，L288）+ 外层 `LIMIT :__result_limit` 双重限行（L285-286）+ 失败先 `rollback` 再返回（L289-295）。参数契约还限制数量 ≤12、字符串 ≤256、limit 1..100、日期范围 ≤366 天（L62-77），`authorized_scope_only` 参数只接受授权 subject 集合内的 ID（L150-161）。

**Q6：数据目录是怎么为自然语言分析服务的？权限怎么隔离？**
A：目录把表/列/示例/关系做成可检索的元数据：中文描述文本（业务域/用途/粒度/别名/推荐使用）embedding 成 1024 维向量（`app/services/data_catalog_embedding.py` L17-61），检索时"文本 ILIKE + pgvector 余弦"混合召回并给 `retrieval_mode` 标注（`app/services/data_catalog_service.py` L36-67），命中表后再限表内检索列、拉关系与 join 指引（L78-108）。**权限隔离在数据层**：所有查询都带 `role_codes`，`_role_clause`（`app/repositories/data_catalog.py` L65-70）对 `allowed_roles_json` 做 JSONB `contains` 过滤，无角色直接空集；域、表、列、示例四级各有 `permission_level/allowed_roles_json`（`app/models/data_catalog.py`），列还分 `is_sensitive/is_queryable`（L94-95）与 `allowed_aggregations`（L96）。检索结果里附上示例的 `sql_template` 与声明白名单——这正好喂给 `DataAnalysisAgent` 做"目录召回 + 模板白名单 + 引用范围"三重校验。同时目录**不放开自由 SQL**（`nl2sql_ready=False`，`data_catalog_service.py` L188），生成与执行留到 AST 校验、行级范围、只读执行、审计四件套就绪后再开（L189）。

---

## 附：总体结论

- RAG 链路（上传 → outbox → worker → 解析切块 → Qwen 1024 维 embedding → pgvector → 混合检索 → RRF → 置信度拒答 → 转人工 → 引用生成）与文档版本化/chunk 级 embedding 复用是**真实运行闭环**；
- Scope 隔离是 SQL 下推行级过滤；
- VectorStore 端口双实现 + factory 可切换；
- NL2SQL 采用"目录召回 + 人工审核模板白名单 + 引用范围/参数契约/只读校验"的**受控执行**，且**明确关闭自由 SQL 生成**（`nl2sql_ready=False` + 护栏声明）；
- 面试时主动讲"哪些做了、哪些明确没做"（文档级哈希去重未实现、Milvus 非当前生产路径、自由 SQL 生成被显式禁用），比夸大更有说服力。
