# Bug 修复记录

### ISSUE-20260903-BM25-TX-POLLUTION
- 日期：2026-09-03
- 来源：原 `docs/BUG_LOG.md`（2026-09-14 合并，避免内容孤儿化）
- 标题：2026-09-03：BM25 关键词腿降级时整库检索崩溃（事务污染）

**现象**：把 `FULLTEXT_BACKEND=pg_search` 打开、但 pg_search 扩展未装/索引未建时，调用 `search_hybrid` 抛出
`sqlalchemy.exc.DBAPIError: InFailedSQLTransactionError`，检索直接失败而非降级回 tsvector。

**根因**：`_rank_text_pg_search` 里 `@@@` 原始 SQL 直接在 `search_hybrid` 的共享 session 上执行。
pg_search 扩展缺失导致该 SQL 报错，**把共享事务 abort**；随后 `_rank_text_branch` 想在同一个 session 上
降级跑 `_rank_text_tsvector`，因事务已 aborted → `InFailedSQLTransactionError`。即"降级"在共享事务里根本降不回去。

**修复**：pg_search 的 BM25 原始查询改用**独立连接**（`self.database.engine.connect()`）执行，并在连接上先 `rollback()`
清空挂起事务；出错只回滚该独立连接，不影响 `search_hybrid` 的共享 session，随后可干净降级回 tsvector。
`app/repositories/knowledge.py` `_rank_text_pg_search` / `_rank_text_branch`。

**验证**：`FULLTEXT_BACKEND=pg_search` + 扩展未装 → 检索返回结果（降级回 tsvector），日志有
`pg_search_fulltext_failed_falling_back_to_tsvector` 告警，不再抛 InFailedSQLTransactionError。
本地真实 DB 实测通过；相关 pytest 通过。

---


### ISSUE-20260903-BM25-CJK-WHOLE-SENTENCE-ZERO-HIT
- 日期：2026-09-03
- 来源：原 `docs/BUG_LOG.md`（2026-09-14 合并，避免内容孤儿化）
- 标题：2026-09-03：中文整句 query 用 BM25 检索 0 命中

**现象**：`content @@@ '什么是适当性管理'` 返回 0 行，而 `content @@@ '适当性'` 返回 12 行。

**根因**：BM25（pg_search）是**词级/子串精确检索**，不是语义检索。整句中文明不做切分会被当成一个"词"，
库里没有这么长的连续串 → 0 命中。这是 BM25 的正确行为，不是故障（与之前 PG `simple` 中文 ts_rank=0 是同类根因）。

**修复**：应用层 `_extract_pg_search_terms` 做中文拆词：按标点/空白切段 → 剥首尾停用词（什么/是/怎么…）→
长中文短语（≥5 字）生成字 bigram（重叠 2 字窗）→ 空格连接（pg_search 空格默认 OR）。整句 query 由此变成
`适当性 管理` 之类的可召回词。语义排序仍由向量腿承担，BM25 腿只管精确命中，两腿 RRF 融合。
`app/repositories/knowledge.py` `_extract_pg_search_terms` / `_rank_text_pg_search`。

**验证**：真实 DB：整句"什么是适当性管理"经代码路径（`search_hybrid` 带真实向量）返回 5 条正确文档
（适当性管理指南等）；本地 64 条评测集 keyword 腿 recall 96.1%。单测 `tests/test_pg_search_term_extractor.py` 覆盖。

---


### ISSUE-20260903-BM25-RRF-TOPK-TRUNCATION-ILLUSION
- 日期：2026-09-03
- 来源：原 `docs/BUG_LOG.md`（2026-09-14 合并，避免内容孤儿化）
- 标题：2026-09-03：三路召回评测发现"双路 recall 略低于纯向量"（RRF top_k 截断假象）

**现象**：64 条评测里个别 case（如 rag_03 有哪些牌照）vector=1.0、keyword=1.0，但 hybrid recall 只有 0.67；
整体 hybrid 96.1% 略低于 vector_only 99.2%。

**根因**：评测脚本里双路 RRF 后固定取 `fused[:5]`（top_k=5），候选池是 20。RRF 把不同来源的候选混进 top5，
可能挤掉"单路下本来能命中、但 RRF 排名靠后"的 gold 片段 → 表现为双路 recall 低于单路。这是**评测参数
（top_k 截断）造成的假象**，不是"双路更差"。真实生产 `search_hybrid` 的 RRF 行为与向量过滤逻辑不同。

**处理**：不据此判定"双路不如单路"。改为按 intent 分层对比（`scripts/compare_rag_dual_path_by_intent.py`），
避免样本不均衡掩盖结论。结论口径：向量腿在语义问答型语料上是主力（99%+）；关键词腿补精确词；双路 hit_rate
最高（100%），尤其补上 keyword 单独漏的 faq/company_info case。

**验证**：分层结果（64 条）：vector 99.2%/100%、keyword 96.1%/97%、hybrid 96.1%/100%；各 intent 分档输出见脚本。

---


### ISSUE-20260903-BM25-IMAGE-AND-TAG-PITFALLS
- 日期：2026-09-03
- 来源：原 `docs/BUG_LOG.md`（2026-09-14 合并，避免内容孤儿化）
- 标题：2026-09-03：镜像源拉不到 ParadeDB / 默认 tag 是 PG18 等环境坑（部署侧）

**现象与根因**：
1. `docker pull paradedb/pg_search:latest` 被本地 Docker 的 DaoCloud 镜像加速源返回 403（该源未同步该仓库）。
2. 换到 `docker.m.daocloud.io/paradedb/paradedb:latest` 能拉，但 `latest` 实际是 **PostgreSQL 18**，与现有 PG16 数据卷
   主版本不兼容（PG 数据目录主版本必须一致）。
3. ParadeDB 的 pg16 tag（`paradedb/paradedb:pg16` / `0.25.6-pg16` 等，同一 digest）= PG16.15 + vector 0.8.4 +
   pg_search 0.25.6，PGDATA 仍为 `/var/lib/postgresql/data`，与 pgvector 镜像一致 → 卷无需改路径，可无缝换引擎。

**解法**：选用并实测 `docker.m.daocloud.io/paradedb/paradedb:pg16`（PGDATA 同路径，卷兼容）。
容器名字冲突：旧 `wealth-postgres` 由 p0 compose 项目起，需用 `docker compose -f docker-compose.p0.yml` 重建，
而非本地 `docker-compose.yml`。

**验证**：两个 compose `config` 通过；服务器重建后 `knowledge_chunk` 数据（如本地 93 chunk）完好、`vector` 扩展保留；
pg_search 0.25.6 正常 CREATE EXTENSION。

---


### ISSUE-20260903-BM25-NEEDS-SHARED-PRELOAD
- 日期：2026-09-03
- 来源：原 `docs/BUG_LOG.md`（2026-09-14 合并，避免内容孤儿化）
- 标题：2026-09-03：pg_search 需要 shared_preload_libraries 才能用

**现象**：`CREATE EXTENSION pg_search` 报 `pg_search must be loaded via shared_preload_libraries`；
`content @@@ ...` 报 `operator does not exist` / `does not contain a USING bm25 index`。

**根因**：pg_search 需先在 `postgresql.conf` 的 `shared_preload_libraries` 加入 `pg_search` 并重启（该实例默认配置为空，
镜像 postgresql.conf.sample 里预设了但实际运行实例未生效）。

**解法**：`ALTER SYSTEM SET shared_preload_libraries = 'pg_search';` → `docker restart wealth-postgres` →
`CREATE EXTENSION pg_search` → `CREATE INDEX ... USING bm25(content) WITH (key_field='id')`。

**验证**：重启后 `SHOW shared_preload_libraries` = pg_search；pg_search 0.25.6 扩展 + BM25 索引建成功；
`content @@@ '适当性'` 命中。

---


### ISSUE-20260903-UNDERSTANDING-CONFIG-NEVER-LOADED
- 日期：2026-09-03
- 来源：原 `docs/BUG_LOG.md`（2026-09-14 合并，避免内容孤儿化）
- 标题：2026-09-03：理解层配置 config/understanding.yaml 运行时从未加载（静默回退默认）

**现象**：做"A′ 枚举动态 top_k"验证时发现，枚举 query（如"公司有哪几个业务线/有哪些牌照"）经
`get_top_k_for_query` 恒返回 5（代码默认），而不是 yaml 里配的 `top_k_enum: 10`；随后全链路检查确认
`understanding_config` 单例的 `_data` 在运行时一直为空（长度 0），难度分级路由到 qwen-turbo/max、灰度、
检索参数、以及本次新增的 `top_k_by_difficulty`/`top_k_enum`/C 兜底 **全部可能从未在生产生效**。

**根因**（两个叠加）：
1. **路径 bug（committed HEAD 就存在，非本次改动引入）**：`understanding_config.py` 里
   `_CONFIG_DIR = os.path.dirname(os.path.join(os.path.dirname(__file__), "..", "..", "config"))`
   多套了一层 `os.path.dirname()`，把末尾的 `config` 段剥掉，`_CONFIG_PATH` 解析成
   `<仓库根>/understanding.yaml`（**不存在**），而非真实的 `<仓库根>/config/understanding.yaml`。
   实测：`_CONFIG_PATH exists = False`，即使 `reload()` 也加载不到。
2. **运行时从不调用 `reload()`**：全仓库（routers/dependencies/services/tasks/lifespan/`__init__`）只有测试里调用
   `reload()`；模块级单例 `understanding_config = UnderstandingConfig()` 创建后 `_data` 恒为 `{}`，
   `get_*()` 全部走代码内置默认。实证：import 整个 `app.main` / `app.api` 后 `_data` 长度仍为 0。

之所以长期未被发现：多数代码默认值与 yaml 恰好一致（top_k=5 / rrf_k=60 / min_score=0.0001 等），
掩盖了"配置未加载、一直在用默认参数"。

**修复**（`ce122c2`）：
- `app/services/understanding_config.py`：去掉多余的 `os.path.dirname()`，`_CONFIG_DIR` 正确落到 `config/`。
- `app/main.py` lifespan 启动时调用 `understanding_config.reload()`，让难度路由/检索参数/灰度真正生效。
- 顺带落地 A′（枚举动态 top_k `get_top_k_for_query`，枚举 marker → `top_k_enum`，否则按难度 S/M/L）+ C 证据兜底。

**验证**：修复后正确路径下 `reload()` 成功（`_data` len=7，含 `top_k_enum`），枚举 query `公司有哪几个业务线`
等经真实配置返回 top_k=10、非枚举 query（客服电话/员工数）返回 5。新增回归守卫
`tests/test_rag_config.py::test_reload_loads_real_yaml_and_dynamic_top_k`，相关 19 项 pytest 通过；
全量 225 项 collect 无导入错误。

> ⚠️ 影响提醒：此修复让理解层配置**首次真正生效**。若线上曾依赖"代码默认值"，个别模型路由/检索参数行为
> 可能与修复前不同（默认值与 yaml 多数一致，预计影响很小），上线后建议跑一次 `compare_rag_dual_path_by_intent.py`
> 复核。


## 长链排查索引

本文记录了一次**跨 8 层缺陷**的完整排查。起始症状：
`你们公司的主营业务是什么` 两个 task 的答案重复。
最终发现该症状由**八层相互独立的缺陷**叠加而成，跨语料、索引、检索、意图、
编排、数据、生成七个层面。**单修任何一层都不足以解决**。

完整叙述（含中途弯路、被证伪的推断、方法论总结）见
`docs/长链排查全记录-八层缺陷.md`（`docs/` 不入库，故此处保留索引供追溯）。

| 层 | 缺陷 | 主要条目 |
|---|---|---|
| 1 语料 | 权限 `staff` 导致孤儿数据；`faq` 是测试残留；类目名错配 | `CATEGORY-PERMISSION-CORPUS-MISMATCH`、`CATEGORY-NARROWING-UNVIABLE`、`INTENT-CATEGORY-FILTER-NEVER-APPLIED` |
| 2 镜像 | Dockerfile 缺 COPY + `.dockerignore` 的 `*.md` 排除语料 | `CORPUS-MISSING-FROM-IMAGE` |
| 3 索引 | BM25 索引 `key_field` 未入字段列表 → **所有写入失败** | `PGSEARCH-BM25-KEY-FIELD-BLOCKS-INSERT` |
| 4 检索 | 各意图共用 scope → 检索同一批文档（且收窄须按意图分别决定） | `INTENT-EXPANSION-DOES-NOT-DIVERGE-RETRIEVAL` |
| 5 意图 | `policy` 唯一触发词是"政策" → 24 个 chunk 完全问不到；扩展后又过度触发 | `POLICY-INTENT-NEVER-TRIGGERED`、`POLICY-TRIGGER-OVERBROAD` |
| 6 数据 | `risk_restriction` 不读风险等级；状态机缺"一致性"维度 | `RISK-LEVEL-NOT-READ`、`STATUS-MACHINE-LACKS-CONSISTENCY-DIMENSION`、`NO-TASK-ANSWER-PASSING` |
| 7 编排 | graph 不遵守 `depends_on`、事实不传递；分层后又引入三个缺陷 | `GRAPH-MODEL-TOOL-IGNORES-DEPENDS-ON`、`GRAPH-DEPENDS-ON-FIXED-BY-LAYERING`、`AGENT-STATE-SILENTLY-DROPS-UNDECLARED-KEYS`、`GRAPH-PREEXEC-FACT-INTENTS-ATTEMPT-REVERTED` |
| 8 生成 | 制度 task 编造个人事实；事实到不了生成阶段 | `RULE-TASK-ASSERTS-PERSONAL-FACT`、`GRAPH-GENERATION-MISSING-PRIOR-FACTS` |

**跨层配套修复**（非某一层独有，但由本次排查引出）：
- `DEP-CONTEXT-POLLUTES-RETRIEVAL` —— 检索串与生成提示必须分离
- `RISK-RANK-RULES-DIVERGED` —— 等级比对口径收敛为单一实现
- `FAILURE-HINT-ENCOURAGES-RETRY` / `FAILURE-HINT-RETRY-FIX` —— 失败提示不再鼓励无效重试
- `ORDER-NO-PATTERN-MISSES-REAL-FORMAT` —— 单号正则漏识别真实格式
- `COVERAGE-BACKSTOP-SKIPPED-ON-BUDGET` —— 覆盖兜底须在所有退出路径生效
- `MODEL-DECISION-REASONING-DISCARDED` —— 模型自述理由此前被丢弃，导致根因不可观测

**方法论要点**（详见 `docs/DECISIONS.md`）：
先补观测再诊断；能跑的探测优于推理；单样本不下机制性结论；
打补丁打不动时质疑方案本身；"节点返回了某键" ≠ "状态里能读到该键"。

---

> 由 `docs/ISSUES.md` 整理抽取的**真 bug 修复记录**（含发现时间与解决方案）。未收录纯进度/决策/设计讨论条目。

- 共 249 条真 bug 修复记录。

### ISSUE-0003
- 日期：2026-07-28
- 分类：后端
**问题**：任务直接写 Redis 会导致数据库与队列不一致
早期 `TaskService` 在提交 PostgreSQL 任务后直接执行 Redis `XADD`，若 PostgreSQL 提交成功但 Redis 发布失败，会出现任务表存在但队列没有消息的问题。
**根因**：任务入库与 Redis 发布不在同一事务边界，两步之间没有一致性保障，Redis 发布失败时无补偿机制。
**解决**：采用 Outbox 模式：TaskService 只在 PostgreSQL 事务中同时写入 `async_task` 与 `outbox_event`；新增 `workers/outbox_worker.py` 消费 pending Outbox 并发布 Redis Streams；发布成功后将 `outbox_event` 置为 `published`；Task Worker 消费 Redis Streams 并 ACK。Worker 侧还增加了失败状态、Pending 恢复和 Dead Letter 路径。
**验证**：真实测试验证 `queued → published → success` 闭环通过。

### ISSUE-0004
- 日期：2026-07-28
- 分类：部署（环境 / 启动）
**问题**：8012 端口已被 P0 服务占用
执行 `uvicorn main:app --host 127.0.0.1 --port 8012 --reload` 报 `[WinError 10013]`（以一种访问权限不允许的方式做了一个访问套接字的尝试），`netstat` 显示 `127.0.0.1:8012` 已被 PID 40352 监听。
**根因**：8012 已被之前启动的本项目 P0 FastAPI 进程占用，并非 PostgreSQL、Redis 或 simple_rag 冲突。
**解决**：无需重复启动同一端口服务，直接访问现有服务；如需重启，先 `netstat -ano | Select-String ':8012'` 查 PID，`Stop-Process` 结束对应进程后再启动；不想停止旧服务时改用其他端口（如 8013）。
**验证**：8012 当前服务健康检查正常（`GET /api/v1/health` → status=ok）。

### ISSUE-0005
- 日期：2026-07-28
- 分类：部署（环境 / 配置）
**问题**：应用未自动读取 P0 数据库连接配置
FastAPI 启动报 `asyncpg.exceptions.InvalidPasswordError: 用户 "wealth" Password 认证失败`。
**根因**：PostgreSQL 容器实际账号密码为 `wealth / wealth_dev`，但项目根目录 `.env` 原本为空，应用没有自动读取 `.env.p0.example`；若用户未在当前会话手动设置 `DATABASE_URL`，应用使用默认连接配置导致密码不匹配。
**解决**：确定两套运行边界（Compose 用 `.env.p0.example`，本地 FastAPI 用根目录 `.env`），已创建并写入本地 `.env`：`DATABASE_URL=postgresql+asyncpg://wealth:wealth_dev@127.0.0.1:5433/wealth_manager`、`REDIS_URL=redis://127.0.0.1:6380/0`，并将 `.env` 保留在 `.gitignore` 中。
**验证**：用修正后的配置启动 FastAPI 到 8013，应用启动成功，`GET /api/v1/health → 200 OK`，PostgreSQL、pgvector、Redis 均 ok。

### ISSUE-0012
- 日期：2026-07-29
- 分类：部署（开发环境 / 基础设施）
**问题**：Docker Engine named pipe 权限异常
Docker Desktop 图形界面和进程已运行，但 `docker` CLI 无法访问 `dockerDesktopLinuxEngine` named pipe。
**根因**：`com.docker.service` 当时处于 `Stopped` 状态。
**解决**：启动 Docker Desktop Service，Docker Engine 访问恢复。
**验证**：`docker version` 能看到 Server/Engine 信息；PostgreSQL 和 Redis 容器均 healthy；PostgreSQL、pgvector、Alembic 和知识库表检查通过。

### ISSUE-0014
- 日期：2026-07-29
- 分类：后端（外部模型服务）
**问题**：DashScope Embedding 连接失败
使用真实文件 `金融政策/个人投资者适当性管理指南.md` 导入时，文本读取、Outbox 发布和 Redis Stream 消费均正常，但调用 Qwen `text-embedding-v4` 时报 `APIConnectionError: Connection error.`，文档被正确标记为 `failed`，未产生 chunk 和 active 版本。
**根因**：API Key 本身有效；根因是失效的代理环境变量和旧的 Embedding 模型配置。
**解决**：Qwen 客户端禁用环境代理，模型切换为 `qwen3.7-text-embedding`，并调整请求批量策略。
**验证**：现有金融政策文档成功导入，生成 12 个 1024 维 chunk，文档和版本均为 `active`。

### ISSUE-0016
- 日期：2026-07-29
- 分类：后端（可观测性）
**问题**：基础日志模块与 Trace Middleware 循环导入
FastAPI 启动时模块处于 partially initialized 状态。
**根因**：`trace.py` 导入 `app.common.logging`，而 `logging/config.py` 又导入 `trace.get_trace_id`，形成循环导入。
**解决**：日志配置模块改为只读取日志上下文，不再依赖 Trace Middleware；Trace ID 由 Trace Middleware 负责写入。
**验证**：`import app.main` 通过，相关模块编译通过。

### ISSUE-0017
- 日期：2026-07-29
- 分类：后端（可观测性）
**问题**：日志过滤器缺少日志上下文导入
独立启动 API 后，`GET /api/v1/health` 返回 500，日志过滤器报 `NameError: get_log_context is not defined`。
**解决**：在 `app/common/logging/config.py` 补充 `get_log_context` 导入。
**验证**：健康检查和知识库搜索 HTTP 验证均返回 200，Trace ID 正常返回。

### ISSUE-0018
- 日期：2026-07-29
- 分类：后端（外部服务可靠性）
**问题**：Worker Embedding 偶发连接失败
Knowledge Worker 执行 Embedding 时偶发连接失败，本次最终通过 SDK 重试成功（状态 MITIGATED）。
**解决**：`QwenProvider.embed` 增加每个文本最多 3 次应用层重试，并记录 `embedding_retry` 日志。
**验证**：现有金融文档成功导入 12 个 chunk，文档状态为 `active`，Redis 消息 ACK 成功。

### ISSUE-0021
- 日期：2026-07-29
- 分类：后端（异步 ORM）
**问题**：认证种子和登录关系懒加载触发 MissingGreenlet
**根因**：异步 SQLAlchemy 中访问未预加载的 `roles/permissions` 关系触发隐式 IO，导致 `MissingGreenlet`。
**解决**：种子逻辑显式加载关系，认证查询使用 `selectinload`，避免异步上下文中的隐式懒加载。
**验证**：认证种子、登录、Refresh Token 轮换和注销服务层联调全部通过。

### ISSUE-0024
- 日期：2026-07-29
- 分类：后端（鉴权）
**问题**：`/auth/me` selectinload 字符串路径错误
真实 HTTP 调用 `/auth/me` 返回 500，SQLAlchemy 报 `ArgumentError`，因为 `selectinload` 使用了字符串属性名。
**解决**：改为使用模型类属性：`selectinload(User.roles).selectinload(Role.permissions)`。
**验证**：重新执行客户注册、员工注册、登录、`/auth/me`、Refresh Token 轮换和 Logout，全部通过。

### ISSUE-0025
- 日期：2026-07-29
- 分类：后端（可观测性）
**问题**：日志此前仅输出控制台
**解决**：统一日志配置新增控制台、`logs/app.log` 和 `logs/error.log` 三个 Handler，并按天轮转；日志目录加入 `.gitignore`。
**验证**：日志目录和两个日志文件自动生成，日志内容成功写入。

### ISSUE-0027
- 日期：2026-07-30
- 分类：后端（注册）
**问题**：错误公司码被字段校验返回 422
错误公司码属于业务规则错误，不应因为输入长度不足而在 Pydantic 层返回 422。
**解决**：移除公司码最小长度约束，交由业务层统一返回 400。

### ISSUE-0028
- 日期：2026-07-30
- 分类：前端（鉴权）
**问题**：登录后顶部用户状态需要手动刷新
登录成功后顶部用户状态不会即时更新，需要手动刷新页面。
**根因**：登录页直接写入 sessionStorage，App 没有响应式认证状态。
**解决**：新增 `frontend/src/stores/auth.ts`，登录后立即请求 `/auth/me`，并通过 `auth:changed` 事件同步 App。
**验证**：前端构建通过。

### ISSUE-0029
- 日期：2026-07-30
- 分类：前端（鉴权体验）
**问题**：已登录页面初始化期间需要等待用户状态
页面刷新初始化期间会短暂出现错误的登录态或用户名称。
**解决**：全局认证 Store 增加 `initialized` 状态，页面启动时先完成 `/auth/me` 同步，再呈现登录态。

### ISSUE-0031
- 日期：2026-07-30
- 分类：前端（Vue）
**问题**：角色后台改造后前端空白页
**根因**：Vue 模板错误使用组合式 API 返回 ref 的 `.value`（App 和 Staff 模板仍残留 `auth.me.value`），导致运行时页面渲染失败；另外占位路由路径生成错误。
**解决**：模板改用 Vue ref 自动解包，修复认证初始化状态，并显式配置占位路由路径。
**验证**：前端构建通过。

### ISSUE-0032
- 日期：2026-07-30
- 分类：前端（路由）
**问题**：前台、客户操作区和工作人员后台边界混合
前一版将前台和后台放在同一导航，并强制首页登录。
**解决**：调整为公开前台、登录后客户操作区和独立工作人员后台入口。

### ISSUE-0039
- 日期：2026-07-30
- 分类：数据库
**问题**：开发热重载期间权限 seed 触发重复唯一键
开发服务重载时，`ensure_auth_seed` 在同一 Session 中先添加权限对象、随后查询下一个权限，SQLAlchemy autoflush 与重复启动竞争可能导致 `permissions.code` 唯一键冲突。
**根因**：seed 过程中先插入再查询且未及时 flush，配合 autoflush 与重复启动竞争，产生 `permissions.code` 唯一键冲突。
**解决**：改为每次新建权限后立即 flush，并同步更新已有权限名称，保证重复 seed 可重入。
**验证**：`python -m scripts.seed_demo_data` PASS。

### ISSUE-0040
- 日期：2026-07-30
- 分类：后端
**问题**：订单详情返回 ORM 历史对象导致 HTTP 500
订单详情响应将 SQLAlchemy `OrderStatusHistory` 对象直接传给 Pydantic，导致订单创建和订单详情接口返回 500。
**根因**：未将 ORM 对象转换为 Pydantic 响应模型就直接返回，序列化校验失败。
**解决**：改用 `OrderStatusHistoryResponse.model_validate(..., from_attributes=True)` 转换后再返回。
**验证**：重启后创建订单、订单详情、幂等重复提交和最终执行全部 PASS。

### ISSUE-0041
- 日期：2026-07-30
- 分类：鉴权
**问题**：customer:read 权限不能单独代表工作人员身份
客户角色也拥有 `customer:read`，但该权限原本直接用于工作人员客户列表和客户详情接口，造成客户可以访问工作人员范围的客户查询 API。
**根因**：接口仅按权限码放行，未校验角色身份，客户角色持有的 `customer:read` 被误用为工作人员身份凭据。
**解决**：新增 `require_staff_permission` 依赖，要求既具备权限且角色不是 `customer`；需 API 重启后完成真实 HTTP 复验。

### ISSUE-0042
- 日期：2026-07-30
- 分类：后端
**问题**：工作人员客户列表依赖参数名 `_user` 导致 FastAPI 误认为查询参数
工作人员客户列表、客户详情和客户订单接口使用 `_user` 参数但未使用 `Annotated`/明确 Depends 类型，FastAPI 将其识别为必填 query 参数 `user`，返回 422。
**根因**：依赖参数缺少 `Annotated`/Depends 类型标注，FastAPI 无法识别为依赖注入，误判为查询参数。
**解决**：改为显式 `user: User = Depends(require_staff_permission(...))`。

### ISSUE-0043
- 日期：2026-07-30
- 分类：鉴权
**问题**：客户与员工账户能力混用
员工和管理员通过通用交易接口拥有账户、资产和订单能力，顶部导航也将客户服务与员工后台混在一起。
**根因**：通用交易接口与导航未按 customer 角色做能力边界限制，导致员工/管理员与客户能力混用。
**解决**：增加 customer 角色限制并统一右上角下拉入口（基础能力边界已解决）；待重启后验证，并清理历史错误 seed 数据。

### ISSUE-0044
- 日期：2026-07-30
- 分类：数据库
**问题**：seed 为员工创建客户资产和交易账户
seed 流程未区分角色，会为员工账号也生成构造画像、资产、持仓和交易账户等本应属于客户的演示数据。
**根因**：`profile_seed`/`trading_seed` 缺少 customer 角色判断。
**解决**：为两个 seed 增加 customer 角色判断，开发环境 reset 后只有客户账号获得构造画像、资产、持仓和交易账户。

### ISSUE-0045
- 日期：2026-07-30
- 分类：后端
**问题**：异步 seed 访问 User.roles 触发 MissingGreenlet
`profile_seed` 和 `trading_seed` 查询用户后直接访问懒加载的 `user.roles`，在异步启动上下文中触发 `sqlalchemy.exc.MissingGreenlet`，导致 API 无法启动。
**根因**：异步上下文访问未预加载的 ORM 懒加载关系。
**解决**：使用 `selectinload(User.roles)` 预加载角色关系。
**验证**：`python -m scripts.seed_demo_data` PASS。

### ISSUE-0047
- 日期：2026-07-30
- 分类：后端
**问题**：staff customer endpoint 仍因默认值被解析为 query 参数
验证发现工作人员客户接口仍返回缺少 query 参数 `user` 的 422。
**根因**：`Annotated[User, Depends(...)]` 依赖放在带默认值的参数之后并设置了默认值，FastAPI 仍产生错误参数签名。
**解决**：将依赖参数移到普通 query 参数之前并移除默认值。

### ISSUE-0049
- 日期：2026-07-30
- 分类：后端
**问题**：管理员用户列表访问角色关系触发 MissingGreenlet
管理员用户列表在序列化 `u.roles` 时触发异步懒加载，返回 500。
**根因**：异步上下文直接序列化懒加载的角色/权限关系。
**解决**：查询中使用 `selectinload(User.roles)`；角色列表同步预加载 `Role.permissions`。

### ISSUE-0051
- 日期：2026-07-30
- 分类：部署
**问题**：前端直连 API 触发 CORS preflight 405
前端 Axios 被改为直连 `http://127.0.0.1:8010`，浏览器对带 Authorization/X-Trace-Id 的请求发送 OPTIONS 预检，而后端未配置 CORS，导致 `/api/v1/health` 等接口返回 405。
**根因**：后端缺少 CORS 配置，且前端绕过了 Vite 代理直连后端。
**解决**：同时配置 FastAPI CORSMiddleware，并恢复前端优先使用 Vite `/api` 代理。

### ISSUE-0052
- 日期：2026-07-30
- 分类：后端
**问题**：`/orders/me` 被动态订单详情路由抢先匹配
`GET /trading/orders/{order_id}` 注册在 `GET /trading/orders/me` 之前，Starlette 按路由注册顺序匹配时将 `me` 当成 `order_id`，随后订单归属查询返回 `order not found`（404）。
**根因**：动态路由注册顺序先于静态路由，静态路径被当作路径参数。
**解决**：将静态路由 `/orders/me` 移到动态路由 `/orders/{order_id}` 之前。

### ISSUE-0053
- 日期：2026-07-30
- 分类：鉴权
**问题**：超级管理员订单审核接口误返回 403
`pending_orders` 使用 `_user` 作为依赖参数名，当前 FastAPI 版本可能将其解析为普通 query 参数或导致权限依赖行为不符合预期。
**根因**：依赖参数命名与写法不当，导致权限依赖注入行为异常。
**解决**：改为明确的 `user: Annotated[User, Depends(require_permission("order:write"))]`；超级管理员经 `require_permission` 的 `is_super_admin` 快速通道应可访问。

### ISSUE-0054
- 日期：2026-07-30
- 分类：后端
**问题**：动态订单详情路由同时抢占 `/orders/pending`
`/orders/{order_id}` 虽已移到 `/orders/me` 后，但仍位于 `/orders/pending` 和 `/orders/customer/{user_id}` 之前。访问 `/orders/pending` 时 `pending` 被当成客户订单 ID，客户角色检查返回 `customer account required`，表现为超级管理员 403。
**根因**：动态详情路由注册顺序仍先于其余静态 `/orders/*` 路由。
**解决**：将动态详情路由移动到所有静态 `/orders/*` 路由之后。

### ISSUE-0055
- 日期：2026-07-31
- 分类：后端
**问题**：待审核订单分页参数缺少边界校验
`GET /api/v1/trading/orders/pending` 的 `limit`/`offset` 原先使用普通 `int` 参数，没有边界校验。实测 `offset=-1` 会进入 SQL 查询并导致 500，`limit=0` 虽不报错但不符合分页约定。
**根因**：分页参数缺少范围约束，非法值直接进入 SQL。
**解决**：改为 `limit=Query(20, ge=1, le=100)`、`offset=Query(0, ge=0)`。
**验证**：重启后接口对非法参数统一返回 422。

### ISSUE-0056
- 日期：2026-07-31
- 分类：后端
**问题**：并发幂等下单竞态导致重复订单和 500
并发请求使用同一 `idempotency_key` 时，多个事务可能同时查不到已有订单并插入新订单，导致重复订单；后续查询可能触发 `MultipleResultsFound` 并引起 500。
**根因**：幂等性仅靠应用层查询判断，缺少数据库层唯一约束，并发下存在查-插竞态。
**解决**：为 `(user_id, idempotency_key)` 增加非空唯一部分索引；启动建表时清理历史重复幂等订单（保留较早记录）；幂等查询改为按时间排序取首条；唯一约束竞争返回 409 而非 500。
**验证**：并发 5、每场景 10 请求通过；Smoke 测试通过。

### ISSUE-0057
- 日期：2026-07-31
- 分类：后端
**问题**：非法金额校验请求错误返回 500
订单接口收到负金额时，Pydantic 正确产生 `RequestValidationError`，但统一验证异常处理器直接把包含 `Decimal` 的错误上下文交给 `JSONResponse`，导致序列化失败并返回 500。
**根因**：错误响应中未对含 `Decimal` 等非 JSON 类型的上下文做编码。
**解决**：使用 FastAPI `jsonable_encoder` 统一编码验证错误和应用错误响应。
**验证**：非法金额稳定返回 422，错误响应结构统一；`tests/test_http_faults.py` 通过。

### ISSUE-0060
- 日期：2026-07-31
- 分类：后端
**问题**：用户角色筛选参数未实际参与数据库查询
前端已经传递 `role` 参数，但后端用户列表接口只读取关键词 `q`，未将 `role` 添加到 SQL 条件，角色筛选前后端表现为无效。
**根因**：后端查询条件遗漏角色过滤。
**解决**：补充 `User.roles.any(code=role.strip())` 查询条件。
**验证**：`role=customer` 仅返回 `customer_demo`；`role=product_manager` 仅返回 `product_manager_demo`；Smoke 测试通过。

### ISSUE-0064
- 日期：2026-07-31
- 分类：后端
**问题**：API 启动时 profile_seed 因历史重复持仓失败
`ensure_profile_seed` 使用 `scalar_one_or_none()` 查询客户持仓，但历史并发测试和演示数据可能产生同一客户多条持仓记录，导致 `MultipleResultsFound`，API 启动失败。
**根因**：历史数据存在同一客户多条持仓，单行查询假设不再成立。
**解决**：改为按持仓 ID 排序并取首条，保持种子过程幂等且兼容历史重复数据。
**验证**：开发环境重置、API 启动相关验证和 Smoke 测试通过。

### ISSUE-0066
- 日期：2026-07-31
- 分类：前端
**问题**：后台时间列过多导致表格拥挤
用户、产品、回收站等后台表格时间列过多，内容拥挤，影响查看与操作。
**解决**：表格改为核心字段 + 省略显示，完整字段通过详情弹窗查看；表格增加横向容器和固定操作列。
**验证**：前端构建及 Smoke 测试通过。

### ISSUE-0067
- 日期：2026-07-31
- 分类：数据库
**问题**：客户账户、资产快照、持仓和订单数据口径不一致
新建客户存在交易账户余额但没有资产快照；交易执行后持仓与账户变化未同步资产快照；`customer_demo` 的种子持仓没有对应订单和交易。
**解决**：统一资产汇总服务、交易后快照同步，并补齐 `customer_demo` 的历史订单/交易。
**验证**：状态更新为 RESOLVED；真实 HTTP Smoke、故障、并发测试全部通过。

### ISSUE-0068
- 日期：2026-07-31
- 分类：后端
**问题**：历史资产快照时间字段为空导致客户列表 500
资产汇总服务复用历史 `customer_asset_snapshot` 记录时，部分历史记录的 `snapshot_time` 和 `created_at` 为空，Pydantic 序列化失败，导致 `/profile/staff/customers` 返回 500。
**根因**：历史快照记录缺少时间字段值，序列化时校验失败。
**解决**：在派生资产服务中为新记录显式写入 UTC 时间，并为历史空字段补齐时间。

### ISSUE-0069
- 日期：2026-07-31
- 分类：后端
**问题**：profile_seed 对历史重复资产快照使用 scalar_one_or_none 导致启动失败
资产汇总会产生多条快照，旧的种子逻辑仍使用 `scalar_one_or_none()` 查询唯一资产快照，历史重复数据导致 API 启动失败。
**根因**：资产汇总可能产生多条快照，单行查询假设失效。
**解决**：改为按时间和 ID 倒序取最新一条，并同步加强持仓查询的重复数据兼容。

### ISSUE-0071：客户管理表格列拥挤
- 日期：2026-07-31
- 分类：前端（UI / 客户管理）
**问题**：客户管理表格列拥挤。客户层级列加入后，整体工作区较窄，表格列被挤压，影响可用性。
**解决**：增加表格最小宽度并支持横向滚动，筛选控件改为换行显示，并加宽客户详情弹窗。

### ISSUE-0078：企业认证通过但客户类型未变化
- 日期：2026-07-31
- 分类：后端（企业认证 / 数据一致性）
**问题**：审计记录显示企业认证已通过，但客户类型未变为企业客户。
**根因**：审核接口只在客户画像已存在时才更新类型；若申请客户没有客户画像，类型不会被更新。
**解决**：审核改为通过时幂等创建客户画像，并设置 `customer_type=enterprise`、`customer_tier=enterprise_standard`；直接执行数据库修复脚本修复 1 条已通过认证的用户记录（用户 ID：`c2b7f414-bbc9-4193-93d5-18ad2a52b6e2`）；审核接口增加"已通过"重复审核保护，避免审计成功但重复操作造成状态混乱。
**验证**：修复后该用户客户画像已设置为 `enterprise / enterprise_standard / enterprise_verified`。

### ISSUE-0085：产品客户类型字段未映射导致适配信息错误
- 日期：2026-07-31
- 分类：后端（产品适配 / ORM 映射）
**问题**：API 返回的产品客户类型均使用默认 individual，客户类型适配信息错误。
**根因**：演示产品 Seed 已写入客户类型，但 `Product` ORM 缺少 `target_customer_type` 字段，导致写入的客户类型未被映射返回。
**解决**：补充 `Product` 模型字段，更新 Seed，并同步适配规则。

### ISSUE-0086：订单测试暴露演示账号缺少正式风险测评
- 日期：2026-07-31
- 分类：后端（风控 / 业务体验）
**问题**：真实订单测试发现，未完成正式风险测评的客户即使购买低风险产品也会在确认阶段被拦截，与"未测评默认 C1、可购买最低风险产品"的规划不一致。
**根因**：未测评客户没有正式风险测评记录，按严格适当性策略在订单确认阶段被一律拦截。
**解决**：采用方案 B——未测评客户自动建立 C1 provisional 默认风险记录，并新增 C1 现金管理保本产品。
**验证**：真实订单验证通过：未测评客户可购买 C1 产品，C2 及以上产品要求正式测评。

### ISSUE-0087：MySQL 增量迁移脚本需要兼容已有探针表结构
- 日期：2026-07-31
- 分类：数据库（MySQL 迁移 / Schema 兼容）
**问题**：MySQL 隔离库迁移过程中先后出现 `description`、`source_type`、`currency` 字段缺失。
**根因**：隔离库已存在早期探针表，`CREATE TABLE IF NOT EXISTS` 不会为已有表补齐后续新增字段。
**解决**：在迁移脚本中增加字段增量补齐逻辑。
**验证**：重新执行迁移成功。

### ISSUE-0088：MySQL 对比库包含 probe 数据
- 日期：2026-07-31
- 分类：测试（迁移对比 / 数据隔离）
**问题**：MySQL 对比库中包含 `mysql_probe` 等探针数据，导致核心表数量比 PostgreSQL 多 1 条，影响一致性比较。
**根因**：探针数据残留于对比库，参与了正式迁移一致性比较。
**解决**：更新核心对比脚本，排除 `mysql_probe` 及其关联的画像、风评、账户数据；探针数据仍保留在隔离验证库中，不再参与正式迁移一致性判断。
**验证**：核心数量对比已通过，PostgreSQL 与 MySQL 的 users/profile/product/risk/account/orders 数量一致。

### ISSUE-0089：旧探针表结构与正式 ORM 字段不一致
- 日期：2026-08-01
- 分类：数据库（MySQL 迁移 / Schema 演进）
**问题**：MySQL 隔离库迁移时字段名和约束与当前 ORM 不完全一致，例如 `rule_key/rule_code`、风险规则、适配规则和用户权限字段。
**根因**：隔离库由早期探针创建，表结构与当前 ORM 不一致，仅建表无法修正已有表。
**解决**：迁移脚本支持对已有表增量补列，并补齐当前 16 张核心表的迁移映射。
**验证**：迁移已执行成功。

### ISSUE-0095：TradingService 抽象后回归脚本选择了不适配演示产品
- 日期：2026-08-01
- 分类：测试（交易回归脚本 / 适当性）
**问题**：抽象骨架完成后运行既有交易回归脚本失败：`customer_demo` 的 C1 风险等级被正确拦截，交易闭环无法走通。
**根因**：脚本按名称排序选择了企业专属或 C3 产品，与 C1 客户不适配——属测试数据选择问题，不是 TradingService 回归缺陷。
**解决**：将脚本改为选择 `target_customer_type=all` 的 C1 产品。
**验证**：复测交易闭环通过。

### ISSUE-0102：MySQL 测试库历史数据污染已解决
- 日期：2026-08-01
- 分类：测试（数据隔离 / 测试库清理）
**问题**：重新迁移后 MySQL 交易对比库仍额外残留 3 条历史测试订单（非本次 PostgreSQL 主库迁移产生），导致逐条与数量对比不一致。
**根因**：MySQL 隔离库缺少可重复的清理/重建流程，历史测试数据残留污染对比结果。
**解决**：新增 `scripts/reset_mysql_dev_state.py`，支持显式确认清理 MySQL 核心业务表、辅助表和 probe 表；执行清理后重新迁移 PostgreSQL 当前数据；后续每次 MySQL 开发测试前先执行该脚本，再执行迁移脚本。
**验证**：订单、成交和状态历史逐条对比通过，数量与 PostgreSQL 完全一致。

### ISSUE-0125：profile Smoke 画像可恢复，资产快照仍需精确清理
- 日期：2026-08-01
- 分类：测试（数据清理）
**问题**：profile/资产 HTTP Smoke 专项使用 `customer_demo` 写入测试画像字段和 `source_type=http_smoke` 的资产快照；画像已能恢复，但资产快照无法自动清理，可能污染演示数据。
**根因**：资产快照接口只有创建能力、没有删除能力，且 `GET /profile/me` 返回的是派生资产结果，无法可靠定位旧的手工资产快照。
**解决**：新增仅 development/dev/local 可用且需 `--yes` 确认的精确清理脚本，按快照中记录的 asset ID 删除测试资产。
**验证**：本轮清理脚本执行结果为 `deleted=1`。

### ISSUE-0127：企业认证 HTTP Smoke 发现 Repository 导入缺失并已修复
- 日期：2026-08-01
- 分类：后端（API / Repository）
**问题**：企业认证 API 已切换到 PostgreSQL Repository，但真实 HTTP 查询返回 500。
**根因**：`app/repositories/trading.py` 中新增的 `SqlAlchemyEnterpriseVerificationRepository` 使用 `CustomerEnterpriseVerification`，但文件导入列表遗漏该模型，触发 `NameError`。
**解决**：补充模型导入。
**验证**：企业认证真实 HTTP Smoke、重复提交、测试数据清理、交易回归和 HTTP Smoke 均通过。

### ISSUE-0134：staff customers 过滤总数与过滤结果不一致
- 日期：2026-08-01
- 分类：后端（API / 分页过滤）
**问题**：staff customers 列表先按 `q` 分页，再在应用层应用 tier/customer_type 过滤，导致返回的 items 已过滤但 total 仍为未过滤的客户总数，两者不一致。
**根因**：过滤在分页之后的应用层执行，未重新统计过滤后的总数。
**解决**：存在 tier 或 customer_type 过滤时，接口返回当前过滤结果的数量；无过滤条件时仍返回 Repository 查询总数。
**验证**：专项 HTTP 测试验证过滤结果和 total 均为 1，回归通过。

### ISSUE-0138：staff customers 存在 N+1 查询，已切换 PostgreSQL 批量读取
- 日期：2026-08-01
- 分类：后端（性能 / API）
**问题**：staff customers 路径逐客户读取画像、资产、持仓数量、风险和层级，客户数增加时数据库往返次数线性增长（N+1 查询）。
**根因**：列表读取按客户循环发起多条查询，未按客户集合做批量获取。
**解决**：新增 PostgreSQL `CustomerQueryRepository` 批量获取客户摘要，API 改用批量结果，客户层级计算不再在列表读取中持久化。
**验证**：专项与综合回归通过（MySQL 批量查询与 EXPLAIN/执行时间基线尚未完成，待后续补齐）。

### ISSUE-0143：MySQL 重置迁移后客户摘要双读恢复通过
- 日期：2026-08-01
- 分类：数据库（数据一致性 / MySQL 迁移）
**问题**：`customer_demo` 客户摘要双读不一致：PostgreSQL `investable_asset=680000`、`customer_tier=gold`，MySQL `investable_asset=364000`、`customer_tier=ordinary`；不能判定双读通过，也无法解除 MySQL validation-only。
**根因**：MySQL 隔离库不是 PostgreSQL 当前主库的干净快照：账户余额、第二持仓和历史订单/资产快照与 PostgreSQL 不一致，资产表还保留旧字段和新字段（隔离库过期/混合数据），并非字段映射差异。
**解决**：不对正式 PostgreSQL 数据做修改、也不直接修补 MySQL 业务行，避免掩盖迁移问题；重置 MySQL 隔离库，从 PostgreSQL 当前数据重新迁移并做结构兼容检查。
**验证**：重置并重新迁移后，客户摘要双读 `differences=[]`，之前 customer_demo 的账户、持仓、资产和历史订单差异不再出现。

### ISSUE-0158：客户摘要 PostgreSQL 额外往返和排序已优化
- 日期：2026-08-02
- 分类：后端（性能 / Repository / 索引）
**问题**：客户摘要 PostgreSQL 额外往返和排序已优化
PostgreSQL 客户摘要批量查询运行时计数为 6 次数据库往返，比 MySQL（5 次）多一次（此前按固定五段查询预算记录，低估了角色加载查询）；两端 users 基础查询均为全表扫描并按 `created_at` 排序（filesort），生产规模下存在性能风险。
**根因**：摘要查询通过 `selectinload(User.roles)` 额外加载了不参与 DTO 输出的角色关系，产生多余数据库往返；users、user_roles 表缺少相应复合索引，导致全表扫描与排序。
**解决**：移除不参与 DTO 输出的 `selectinload(User.roles)`，仅保留角色存在性过滤；新增 users `(status, created_at)` 索引及 user_roles `(role_id, user_id)` 索引。
**验证**：PostgreSQL 运行时往返从 6 降为 5，P95 为 `13.817ms`；PG EXPLAIN 使用 `ix_users_status_created_at` Index Scan。MySQL 仍为 5 次往返、P95 `13.491ms`，契约通过。（遗留：MySQL EXPLAIN 仍显示全表扫描和 filesort，由 ISSUE-0159 继续处理。）

### ISSUE-0159：MySQL 客户列表复合索引实验通过
- 日期：2026-08-02
- 分类：数据库（MySQL / 索引 / 迁移）
**问题**：MySQL 客户列表复合索引实验通过
承接 ISSUE-0158 遗留：MySQL EXPLAIN 仍显示全表扫描和 filesort，复合索引未被使用，查询计划劣于 PostgreSQL。
**根因**：MySQL 迁移脚本此前未创建对应复合索引，隔离库缺少 `ix_users_status_created_at`，执行计划无法走索引。
**解决**：迁移脚本增加 MySQL 复合索引 `ix_users_status_created_at` 的幂等创建；针对不存在的 `user_roles` 表安全跳过。
**验证**：MySQL EXPLAIN 使用该索引，计划从全表扫描 + filesort 变为 `type=ref` + `Using index`；性能契约中 MySQL P95 `12.867ms`、5 次往返，PG P95 `10.897ms`、5 次往返，均 PASS。

### ISSUE-0178：交易创建订单入口已接入统一数据库错误映射
- 日期：2026-08-02
- 分类：后端（错误语义 / 数据库异常映射）
**问题**：交易创建订单入口已接入统一数据库错误映射
交易创建订单入口此前局部直接捕获驱动层 `IntegrityError`，未走统一数据库异常映射，不同入口会返回不同 detail 文本，重复幂等键等错误语义不一致（承接 ISSUE-0177 遗留）。
**解决**：创建订单接口捕获 `IntegrityError` 后统一调用 `map_database_error`，重复幂等键返回 `IDEMPOTENCY_CONFLICT` 和 409。
**验证**：错误映射矩阵和影子库幂等契约均通过。（遗留：其他 API 入口仍有直接 `HTTPException` 文本，需按领域逐步统一。）

### ISSUE-0179：交易状态错误码统一第一阶段完成
- 日期：2026-08-02
- 分类：后端（错误语义 / 交易）
**问题**：交易状态错误码统一第一阶段完成
订单确认、取消、审核、拒绝等交易状态入口此前仍未接入统一错误映射，仍直接抛出 `HTTPException` 文本（ISSUE-0178 遗留范围），错误响应缺少稳定、一致的 code/status/detail 结构。
**解决**：上述入口共享 `err()` 映射，按 TradingError 文本归一为稳定 code/status/detail 结构。
**验证**：代码编译、数据库错误映射、影子库幂等和非法状态契约均 PASS。（遗留：尚未执行真实 HTTP 错误响应快照；管理员、画像、企业认证等其他领域待后续收敛。）

### ISSUE-0182
- 日期：2026-08-02
- 分类：测试
**问题**：交易状态冲突 HTTP 测试夹具选错库。此前将隔离订单写入 MySQL 影子库后调用 PostgreSQL 主库 API，导致返回 `ORDER_NOT_FOUND`；这是夹具库选择错误，不是业务逻辑错误。
**根因**：测试夹具库选择错误——隔离订单写入 MySQL 影子库，却对 PostgreSQL 主库发起确认请求。
**解决**：测试改为向 PostgreSQL 主库创建 `pending_review` 订单，再发起真实 confirm 请求。
**验证**：真实 confirm 请求返回 HTTP 409、`ORDER_STATE_CONFLICT`；测试后订单及关联数据已清理。

### ISSUE-0188
- 日期：2026-08-02
- 分类：测试（基础设施）
**问题**：Windows 上双库契约门禁执行不稳定；MySQL 影子库迁移后发生漂移，`profile_customer_type` 契约校验失败。
**根因**：门禁以 asyncio 子进程方式执行在 Windows 上不稳定；MySQL 影子库数据漂移导致契约与预期不一致。
**解决**：门禁由 asyncio 子进程改为同步 subprocess.run 执行；重新迁移 MySQL 影子库。
**验证**：门禁稳定执行，`profile_customer_type` 契约恢复，6 项门禁全部通过。

### ISSUE-0189
- 日期：2026-08-02
- 分类：测试
**问题**：管理员错误 HTTP 测试夹具不准确：未以真实当前管理员身份发起请求，导致"管理员不存在用户"与"自我删除限制"等错误场景无法返回预期 code/status。
**根因**：测试脚本没有先获取真实当前管理员 ID（夹具使用的管理员身份不正确）。
**解决**：测试脚本改为通过 `/auth/me` 获取真实当前管理员 ID 后再发起请求。
**验证**：管理员不存在用户、自我删除限制均返回预期 code/status，且未修改管理员账号。

### ISSUE-0199
- 日期：2026-08-03
- 分类：鉴权
**问题**：操作草稿创建时的授权边界校验存在缺陷，需要修复；验证需覆盖无授权、有效授权、单笔额度超限、撤销授权四类场景。
**解决**：修复操作草稿创建的授权边界校验。
**验证**：真实 HTTP 测试覆盖无授权、有效授权、单笔额度超限和撤销授权四个场景，结果 4/4 PASS，测试数据自动清理。

### ISSUE-0202
- 日期：2026-08-02
- 分类：后端
**问题**：客户确认草稿转订单接口首次测试时发现接口异常，原因是审计变量未定义。
**根因**：接口实现中审计变量未定义，导致处理时抛出异常。
**解决**：修复审计变量定义问题。
**验证**：接口通过回归——客户确认后的草稿可通过独立接口创建 `pending_confirmation` 订单，草稿变为 `submitted`，并写入审计。

### ISSUE-0211
- 日期：2026-08-02
- 分类：测试
**问题**：风险预警回归复跑时未命中预警：风险预警规则 R001 阈值为 50000，而测试脚本一次复跑使用了 5000。
**根因**：测试脚本使用的金额与规则阈值不同步（5000 低于阈值 50000），未触发预警。
**解决**：将测试金额恢复为 50000 后重新验证。
**验证**：重新验证 PASS，预警正常命中。

### ISSUE-0212
- 日期：2026-08-02
- 分类：前端
**问题**：投顾页面和路由已存在，但客户中心侧边栏缺少可见入口，导致用户无法发现"投顾服务"功能。
**根因**：客户中心侧边栏未提供投顾服务菜单项和入口按钮（页面与路由已有，导航缺失）。
**解决**：补充"投顾服务"菜单项和入口按钮。
**验证**：重新完成前端构建与后端回归。

### ISSUE-0227
- 日期：2026-08-03
- 分类：测试（风险规则）
**问题**：风险规则边界测试因当前流水重复计入与规则类型未隔离而互相污染
修正前当前流水会被重复计入，RW-001/RW-002 未充分按流水类型隔离，导致低于阈值的样例与不同规则夹具互相污染、出现误报。
**解决**：修正当前流水不应重复计入和规则类型隔离问题；边界测试改为每个用例使用独立事务执行。
**验证**：独立事务边界复测符合预期：RW-001 的 49,999/50,000、RW-002 的 19笔99,999/20笔100,000 均符合预期。

### ISSUE-0230
- 日期：2026-08-03
- 分类：后端（风险规则计算）
**问题**：风险规则计算存在重复计数
当前触发流水与历史流水未分离，同一笔流水会同时参与部分规则聚合，导致 RW-003 等规则样例误命中、多规则样例互相干扰，测试与生产场景都可能重复计数。
**解决**：重构风险规则计算：当前触发流水与历史流水分离，RW-003 只使用历史入账与当前出账，RW-004/RW-005 分别使用历史入账、历史出账和当前出账，避免重复计数。
**验证**：后端、RW-001 HTTP 和投顾回归已通过；RW-003 至 RW-006 专项测试需在修正后重新执行。

### ISSUE-0231
- 日期：2026-08-03
- 分类：后端（数据模型 / 风险规则）
**问题**：客户画像缺少年收入字段导致 RW-006 无法实现
`CustomerProfile` 没有 `annual_income` 字段，此前 RW-006 规则只能被代码明确跳过。
**解决**：在客户画像增加 `annual_income` 字段。
**验证**：RW-006 的命中、不命中和最低金额边界测试 3/3 PASS；RW-003 至 RW-005 仍需独立专项复测。

### ISSUE-0234
- 日期：2026-08-03
- 分类：后端（风控 / 幂等）
**问题**：RW-004 批量扫描重复执行返回已有对象
批量扫描首次命中成功，但重复调用时服务把已有预警对象放入返回列表，导致 `created=1` 的语义错误（非幂等）。
**解决**：修改 `_save_window_alert`：发现 24 小时内 open/acknowledged 的同规则预警时返回 `None`，扫描服务只统计新创建对象。

### ISSUE-0237
- 日期：2026-08-03
- 分类：后端（风控状态机）
**问题**：风险预警可过早结案
确认风险可以直接结案，评估后通过也无需保存完整依据，缺少先完成调查、报告或限制处置再结案的过程约束。
**解决**：修正风险预警状态机：确认风险不能直接结案，必须先完成调查、报告或限制处置；评估后通过必须保存完整评估依据和客户沟通。
**验证**：基础回归已通过；确认风险进入 investigating/report_pending 后再完成结案的完整验证待执行。

### ISSUE-0238
- 日期：2026-08-03
- 分类：前端（API 契约）
**问题**：前端旧"解决"按钮发送旧请求格式导致 422
**根因**：前端风险页面仍使用旧版 `riskApi.handle(id, status, note)`，管理员接单后点击"解决"只发送 `status=resolved`，没有评估结论、评估依据和客户沟通记录，后端按新状态机返回 422。
**解决**：更新前端 API 和页面：接单后必须进入"评估处理"表单，填写评估结论和必要证据后再提交。

### ISSUE-0245
- 日期：2026-08-03
- 分类：后端
**问题**：分页切换后投顾服务 operation-drafts 返回 500
**根因**：分页接口返回 `{items,total}` 后，`/profile/me/operation-drafts` 的 response_model 仍是 `ApiResponse[list[AdvisorDraftResponse]]`，触发 FastAPI ResponseValidationError；授权列表同样存在旧 response_model 和测试脚本旧数据结构问题。
**解决**：统一修复为分页响应模型和 `data.items` 读取。
**验证**：投顾 16/16 回归通过。

### ISSUE-0246
- 日期：2026-08-03
- 分类：后端
**问题**：顾问分页接口实际返回数组，导致客户无法选择顾问（500）
**根因**：`/profile/staff/advisors` 后端虽声明 `ApiResponse[dict]`，实际仍返回数组；客户投顾页面按 `data.items` 读取，触发 500。
**解决**：改为真实分页查询并返回 `{items,total}` 响应；同时修正授权列表分页实现。
**验证**：投顾回归恢复 16/16。

### ISSUE-0248
- 日期：2026-08-03
- 分类：后端（鉴权 / 投顾）
**问题**：投顾客户可见范围和操作流程违反授权先行原则
投顾工作台此前通过 `/profile/staff/customers` 获取全体客户，推荐接口和客户详情接口不校验当前投顾是否拥有该客户的 active 授权，存在非授权客户信息可见及"先推荐后授权"的问题，且超级管理员可在普通投顾草稿流程中绕过授权。
**解决**：投顾客户列表、客户详情、产品推荐和操作草稿创建均增加当前投顾 active 授权校验；移除超级管理员在普通投顾草稿流程中的授权绕过；客户投顾详情页补充返回客户中心和返回前台空间按钮。
**验证**：后端编译 PASS、前端生产构建 PASS；双投顾跨客户 HTTP 隔离测试、撤销授权后的草稿策略测试等待完成。

### ISSUE-0250
- 日期：2026-08-03
- 分类：后端（投顾订单）
**问题**：投顾确认后客户订单不可见及需重复确认
客户订单接口返回分页对象，但部分前端仍按数组读取；投顾确认后生成的订单仍为 `pending_confirmation`，导致客户需在订单页重复确认且管理员待审核列表看不到；员工投顾工作台重复显示返回按钮。
**解决**：投顾提交草稿直接生成 `pending_review`；客户订单页面改为读取 `items/total` 并取消确认按钮；员工投顾工作台删除重复返回按钮。
**验证**：投顾回归 16/16 PASS、跨客户隔离 9/9 PASS、前端构建 PASS。

### ISSUE-0251
- 日期：2026-08-03
- 分类：后端
**问题**：普通客户自助下单确认逻辑被投顾订单调整误影响
**根因**：通用订单创建方法被改为直接生成 `pending_review`，导致普通客户自己下单后无法在订单页面确认；订单列表 response_model 与实际分页返回不一致，客户读取订单接口 500。
**解决**：恢复普通下单 `pending_confirmation`；恢复订单页确认按钮；投顾提交路径单独迁移至 `pending_review`；统一订单列表返回 `{items,total}` 并修正 response_model。
**验证**：普通下单创建为 `pending_confirmation` 且可从 `/trading/orders/me` 读取；投顾完整回归和隔离回归通过。

### ISSUE-0252
- 日期：2026-08-03
- 分类：前端
**问题**：浏览器订单为空
**根因**：订单接口/页面曾在数组与 `{items,total}` 结构之间切换，前端读取不兼容；且本地服务可能未加载最新后端代码。
**解决**：前端订单和交易列表同时兼容数组及分页对象结构。
**验证**：当前 API 返回 `{items,total}`，普通客户直下单后可通过 HTTP 读取订单；重启后端、重新构建并刷新浏览器后的现场确认待执行。

### ISSUE-0253
- 日期：2026-08-03
- 分类：后端（授权 / 订单）
**问题**：投顾订单在客户订单页重复确认，授权关系持久化存疑
客户在投顾页面确认后，订单页仍显示确认入口；用户担心进入投顾页面会重新生成授权，退出后丢失权限。
**解决**：投顾提交订单后保持 `pending_review`；授权创建改为同一客户-投顾 active 关系复用更新，不再每次替换；授权数据来自数据库，登录退出不影响。

### ISSUE-0254
- 日期：2026-08-03
- 分类：后端（资金结算）
**问题**：投顾订单审核后未扣款并产生负冻结余额
**根因**：投顾订单绕过客户订单确认时未冻结资金，管理员审核仍直接减少冻结余额，导致 `frozen_balance` 为负数且 `available_balance` 未减少。
**解决**：审核通过时区分"已有冻结"和"投顾未冻结"两种路径：投顾订单从可用余额扣除，普通订单从冻结余额结算；拒绝和异常余额状态增加保护。
**验证**：投顾完整回归通过、后端编译通过；浏览器账户余额现场验收待执行。

### ISSUE-0256
- 日期：2026-08-03
- 分类：后端（资金结算）
**问题**：部分投顾订单执行后账户余额未扣除
部分投顾订单状态为 executed，但客户账户可用余额没有按订单金额减少。
**根因**：审核结算分支依赖冻结余额状态，无法可靠区分投顾订单和普通订单；投顾订单实际未走普通客户确认冻结路径。
**解决**：按 `advisor-draft:` 订单来源明确从可用余额扣款；普通订单要求冻结余额足额后再执行。

### ISSUE-0257
- 日期：2026-08-03
- 分类：数据库
**问题**：历史已执行投顾订单资金对账发现缺失出账流水。6 条已执行投顾订单中 1 条缺失出账流水，5 条已有匹配流水。
**解决**：对订单 O5C215CE175064205 按订单级幂等条件补扣 10000.00，并新增 wealth_order_reconciliation 出账流水。
**验证**：最终复核 6/6 订单均有金额匹配的出账流水；客户 hNW_demo 可用余额为 4000000.00、冻结余额为 0.00；无剩余异常。

### ISSUE-0261
- 日期：2026-08-03
- 分类：测试
**问题**：订单来源迁移后状态冲突测试夹具与 schema 不同步。orders.order_source 和 confirmation_mode 已改为非空字段，旧 HTTP 状态冲突脚本直接插入订单时未补字段，导致测试失败。
**解决**：测试夹具补齐 order_source 和 confirmation_mode 字段；清理逻辑补齐资金流水；重启 API 后完成状态矩阵和资金保护回归。
**验证**：状态冲突回归恢复 PASS；普通交易闭环 PASS；投顾完整回归 PASS；幂等和并发幂等 PASS；后端编译 PASS。

### ISSUE-0266
- 日期：2026-08-03
- 分类：数据库
**问题**：已执行普通订单缺少资金出账流水。全量检查发现 11 条已执行订单中 5 条普通订单缺少 cash/out 流水，但均已有唯一成交记录。
**解决**：按订单缺流水条件补记 reconciliation 流水并扣减对应账户可用余额；已有流水的订单未重复处理。
**验证**：复核 11/11 每笔一条成交、每笔一条出账流水、冻结余额非负，PASS。

### ISSUE-0279
- 日期：2026-08-03
- 分类：测试
**问题**：工作人员代办赎回测试断言错误：曾把操作人 ID 与客户 ID 直接比较，断言逻辑有误。
**解决**：改为验证操作人字段非空，并通过数据库确认实际工作人员 ID。
**验证**：授权提交、幂等、工作人员查询、审核结算、客户余额增加、客户查询均通过。

### ISSUE-0282
- 日期：2026-08-03
- 分类：后端
**问题**：转账申请审核状态机改造中修正数据归属错误：transfer/in 流水的 customer_id 未归属目标账户所属客户；且本地 API 未重启，新路由未生效。
**解决**：转账提交进入 pending_review、审核 settle/reject；修正 transfer/in 流水的 customer_id 为目标账户所属客户；重启本地 API 使 operations 转账审核路由出现在 OpenAPI。
**验证**：后端编译通过；通过/拒绝/重复审核及双方余额、两条流水、审计记录的精确断言待创建隔离目标账户后执行。

### ISSUE-0289
- 日期：2026-08-03
- 分类：测试
**问题**：P-B 阶段性整体回归发现脚本与接口不一致：test_staff_redeem_http.py 调用的 /operations/staff/redeem 在当前 OpenAPI 中缺失（返回 405）；转账相关脚本在共享演示账户上重复运行后，旧全局余额/流水断言受历史数据影响。
**解决**：恢复工作人员代办赎回接口；修复转账回归脚本的历史流水和余额基线污染。
**验证**：8 个核心 P-B 回归脚本全部 PASS；P-B 阶段性整体回归通过。

### ISSUE-0291
- 日期：2026-08-03
- 分类：后端
**问题**：GET /admin/enterprise-verifications 在空列表时返回 500。
**根因**：接口声明返回 ApiResponse[dict]，实际返回 list，触发 FastAPI ResponseValidationError。
**解决**：统一返回 EnterpriseVerificationListResponse(items, total)，并应用分页。
**验证**：四种状态筛选均返回 200；前端构建 PASS。

### ISSUE-0292
- 日期：2026-08-03
- 分类：前端
**问题**：后台不同子模块自带 margin/容器宽度，进入页面后标题和表格边界不一致；客户前端未读取后端已有的 latest_asset/holdings 快照，资产与持仓展示仍为占位。
**解决**：统一后台内容区直接子模块布局；前端接入真实资产和持仓展示。

### ISSUE-0294
- 日期：2026-08-03
- 分类：后端/数据库
**问题**：真实普通 C4 用户验收与客户资料历史字段兼容
新建用户 `asdasd` 未创建 CustomerProfile，购买个人产品确认时被拒绝；同时历史 PostgreSQL `customer_profile.investment_horizon` 非空，而新 ORM 使用 `investment_horizon_years`，新建 Profile 出现非空约束错误。
**解决**：保留 legacy 字段映射，补充 schema ensure 与 `0008_customer_profile_horizon_compat` 迁移，补齐 asdasd 的普通个人客户资料。
**验证**：asdasd C4 成功购买成长精选组合，订单、持仓、估值、资产快照和客户概要全部通过。

### ISSUE-0295
- 日期：2026-08-03
- 分类：后端
**问题**：日度估值未来模拟快照被概要读取覆盖
在 2026-08-04 的未来模拟日度估值后，2026-08-03 的客户概要读取会修改最新 daily_valuation 快照为 derived 并重算为当日金额。
**根因**：`latest_or_derived` 更新"最新一条快照"，未区分历史事件快照与当前日派生快照。
**解决**：历史快照不可变，概要只创建/更新当前 UTC 日期 `derived` 快照。
**验证**：asdasd 的 2026-08-04 `daily_valuation` 保持 200,012.50，同日重复任务幂等，趋势节点正确。

### ISSUE-0299
- 日期：2026-08-03
- 分类：后端（性能）
**问题**：后台客户管理与投顾模块因客户列表 N+1 读取/写入无法正常显示
后台客户管理接口虽返回 200，但在 5,005 条客户性能数据下耗时约 27–58 秒，页面长期处于"正在读取客户"；投顾工作台同样依赖该客户列表，因而无法正常加载。
**根因**：`GET /profile/staff/customers` 读取全部客户后，对每个客户调用 `CustomerTierService.calculate()`；该服务会重新估值并更新/创建派生资产快照，形成 N+1 查询与写入循环。
**解决**：列表接口改用批量查询已返回的画像和最新快照，通过纯函数 `calculate_customer_tier(customer_type, investable_asset)` 计算层级，读取路径不再持久化资产快照。
**验证**：客户列表 10 条分页 200/约 580ms；ordinary 筛选 200/约 470ms；投顾候选列表 200/约 14ms；前端生产构建通过。另确认客户管理菜单仅向 `customer:read` 权限用户展示，`operations_demo` 访问 403 属预期授权行为，不是显示缺陷。

### ISSUE-0300
- 日期：2026-08-03
- 分类：鉴权/后端
**问题**：性能优化误移除投顾授权过滤，导致全量客户可见回归
投顾工作台的"已授权客户"下拉框重新出现所有客户，违反 `DECISION-20260803-002` 的授权先行、最小客户可见范围要求。
**根因**：为修复 5,005 客户数据下的 N+1 估值/快照写入性能问题，`/profile/staff/customers` 被改为全量批量读取，但遗漏了原有 `CustomerAdvisorAuthorization` active/有效期过滤。
**解决**：先以当前登录投顾 ID 查询 active 且有效授权的客户集合；客户摘要批量读取后按该集合过滤；再执行纯内存层级计算、筛选与分页。无有效授权时返回空 `items`/`total=0`。同步修复：`/profile/staff/customer/{user_id}` 重新校验当前投顾与目标客户的 active 授权，无授权返回 `403 / ADVISOR_AUTHORIZATION_REQUIRED`。后续拆分运营客户管理与投顾授权客户 API，避免单一路由承担冲突语义。
**验证**：`scripts.test_advisor_isolation_http` 9/9 PASS，覆盖授权前列表不暴露、详情/推荐拒绝、授权后允许、跨客户隔离、撤销后隐藏和拒绝；前端生产构建 PASS。

### ISSUE-0302
- 日期：2026-08-03
- 分类：后端/鉴权
**问题**：客户管理与投顾客户列表接口职责冲突已拆分
`/profile/staff/customers` 曾同时承担后台全量客户管理与投顾授权客户范围两种冲突语义，性能优化时容易误移除授权过滤，权限修复时又会让客户管理只显示授权客户。
**解决**：保留 `/profile/staff/customers` 为 `customer:read` 的全量客户管理接口；新增 `/profile/staff/advisor-customers` 作为当前投顾有效授权客户接口；投顾工作台前端已切换到新接口。客户详情、推荐、草稿创建继续执行 active/有效期授权校验；授权客户列表在服务端根据当前用户查询授权集合，前端不参与权限裁决。
**验证**：全量客户 5005 条分页 HTTP PASS；授权客户 1 条 HTTP PASS；授权隔离 9/9 PASS；层级筛选 PASS；前端构建 PASS。

### ISSUE-0303
- 日期：2026-08-03
- 分类：后端
**问题**：管理端审计业务语义字段运行时响应缺失已修复
`GET /api/v1/admin/audit-logs?resource_type=business_operation` 返回的 `business_operation` 审计只有资源 UUID 和自由文本 detail，缺少前端业务审计视图依赖的客户名称/用户名、业务类型、业务状态、业务引用、产品名称等字段。
**根因**：`/admin/audit-logs` 仅解析操作人和 user 类型资源，未批量关联 `business_operation`、客户与产品。
**解决**：接口按当前页审计记录批量读取操作、客户和产品，稳定返回客户名称/用户名、业务类型、当前业务状态、业务引用、产品名称；转账资源名称改为可读目标账户，赎回改为产品名。回归脚本 `scripts/test_admin_audit_http.py` 现断言全部新增业务语义字段和实际业务数据，不再只检查分页外壳。
**验证**：`business_operation` HTTP 200，total=80；转账与赎回样本均可读；前端构建 PASS。

### ISSUE-0309
- 日期：2026-08-03
- 分类：后端（资金）
**问题**：风险 HOLD 后 REJECT 未解冻客户冻结资金
订单被 HOLD 进入 `risk_hold` 后，后续 REJECT 未返还冻结资金。
**根因**：`REJECT_ORDER` 仅在订单为 `pending_review` 时返还冻结资金；`HOLD_ORDER` 将订单改为 `risk_hold` 后，后续 REJECT 跳过资金返还。
**解决**：对 customer_direct 订单的 `pending_review`/`risk_hold` 两种状态均执行冻结余额一致性校验和资金返还；重复 REJECT 仍由状态机拒绝，避免重复解冻。
**验证**：HOLD 后 REJECT 订单变 rejected，余额/冻结余额恢复到订单确认前基线；重复 HOLD/REJECT 409；订单、赎回、转账风险门禁回归均 PASS。

### ISSUE-0311
- 日期：2026-08-04
- 分类：后端/鉴权
**问题**：客户级风险预警无法限制账户及风险操作状态不可见
客户级 AML 预警无法实际限制客户账户，且风险操作状态不可见。
**根因**：`RESTRICT_TRADING/RELEASE_RESTRICTION` 仅通过 `alert.order_id` 查订单账户；客户级 AML 预警没有订单 ID，动作返回冲突，无法实际限制客户。
**解决**：无订单预警时按 `alert.customer_id` 查询客户账户；新增风险操作状态派生和 profile/staff customer 响应字段；后台客户管理增加操作风控展示。
**验证**：客户状态 `normal → monitoring → restricted → monitoring` 真实 HTTP PASS；订单状态历史、资金闭环、订单/赎回/转账风险门禁回归 PASS；前端构建 PASS。

### ISSUE-0312
- 日期：2026-08-04
- 分类：鉴权/前端
**问题**：后台客户管理误用投顾授权详情接口导致超级管理员 403
超级管理员查看非授权客户详情也被拒绝。
**根因**：客户管理页面使用投顾详情路径 `/profile/staff/customer/{user_id}`，该路径正确要求 active 投顾授权，导致超级管理员查看非授权客户详情也被拒绝。
**解决**：新增后台客户管理详情路径 `/profile/staff/customers/{user_id}`，要求 `customer:read`；客户管理前端切换为 `managedCustomer()`；投顾详情接口保持原有严格授权。
**验证**：编译与前端构建通过；管理员/权限不足/投顾越权的 HTTP 契约回归待补。

### ISSUE-0313
- 日期：2026-08-04
- 分类：鉴权/后端
**问题**：后台客户详情接口的工作人员边界校验与 HTTP 契约已完成
新后台客户详情接口最初复用 `require_permission("customer:read")`，无法明确排除 customer 角色；尝试以 `Depends(require_permission(code))` 嵌套动态依赖又使 FastAPI/OpenAPI 解析错误，将 `user` 当作缺失查询参数并返回 422。
**根因**：`require_permission("customer:read")` 只能判断权限码，不能明确排除 customer 角色；嵌套动态依赖的写法导致 FastAPI/OpenAPI 解析错误。
**解决**：`require_staff_permission` 改为直接依赖 `current_user`，统一执行超级管理员放行、权限码检查与 customer 角色排除；`/profile/staff/customers/{user_id}` 使用该依赖。
**验证**：新增 `scripts/test_managed_customer_detail_permissions_http.py`；管理员详情 200、客户身份 403、投顾专用接口未授权 403 且返回 `ADVISOR_AUTHORIZATION_REQUIRED`，全部 PASS。

### ISSUE-0315
- 日期：2026-08-03
- 分类：后端/前端
**问题**：解除交易限制入口与客户可见风险状态已完成闭环
风险预警页既承担调查处置又直接承担客户账户解除限制，职责混杂；客户虽然可以从 `/profile/me` 获得风险操作状态，但客户后台没有明确展示，无法知晓限制与风险关注的差异。
**解决**：移除风险预警页的解除限制入口和处置选项；在后台客户管理详情中基于 `risk_operation.latest_alert_id` 执行 `RELEASE_RESTRICTION`；客户看板新增只读风险状态卡片，展示状态、规则、等级和限制原因，并说明客户不能自行解除。
**验证**：新增 `scripts/test_customer_risk_frontend_contract_http.py`，确认 `restricted` 在客户概要和后台详情中一致可见，后台解除后变为 `monitoring/none`；客户详情权限、风险状态 `normal → monitoring → restricted → monitoring → normal`、风险动作矩阵、后端编译和前端构建全部 PASS。

### ISSUE-0316
- 日期：2026-08-04
- 分类：后端
**问题**：RW-001/RW-002 实时与日批证据、去重边界不一致
RW-001/RW-002 仅在实时订单评估中存在，客户窗口日批扫描未覆盖；实时 `_save_alert()` 没有明确幂等契约，日批去重依赖"近 24 小时未关闭预警"，无法表示实际扫描窗口，也不利于稳定回放。
**解决**：日批扫描补充 RW-001/RW-002；实时预警增加 `real_time_order:{order_id}:{rule_code}` 去重键，日批预警增加客户、规则、UTC 窗口起止组成的去重键；证据统一写入 `scan_type`、窗口/阈值/统计数据；重复实时评估不产生空预警返回。
**验证**：`test_aml_realtime_rules_idempotency.py` 与 `test_aml_window_rules_idempotency.py` 均 PASS；原有 `test_risk_alert_http.py`、`test_risk_action_matrix_http.py`、后端编译和前端生产构建均 PASS。

### ISSUE-0318
- 日期：2026-08-04
- 分类：后端
**问题**：AML 批次关联预警错误使用数据库创建时间且跨窗口测试语义不明确
批次关联接口原以 `RiskAlert.created_at` 限制范围，未来模拟窗口/历史回放的预警虽然证据属于该批次窗口，但创建时间是任务实际执行时刻，导致关联列表为空；测试也错误期待前一天的现金流在下一窗口再次触发 RW-001。
**解决**：批次关联仅匹配 `customer_window` 预警证据中的 `window_start/window_end`；测试固定 RW-001 的日窗口语义、RW-002 的 7 日持续窗口语义，以及新窗口新增现金流时 RW-001 的新建条件。
**验证**：真实 RW-001/RW-002 批次关联返回 2 条；管理员 200、客户 403、缺失批次 `404/AML_BATCH_NOT_FOUND`；实时/日批幂等、批次运行、后端编译和前端构建均 PASS。

### ISSUE-0322
- 日期：2026-08-04
- 分类：鉴权
**问题**：客服可见投顾工作台且业务操作接口仅按非客户角色放行
客服角色可见投顾工作台；业务操作待审核/审核/工作人员代办接口只排除 customer 角色，未统一要求 `order:write`，存在越权访问风险。
**根因**：前端把 `customer:read` 同时作为客户管理和投顾工作台显示条件；后端投顾接口复用宽泛的 `staff_customer_user`；业务操作工作人员接口只排除 customer 角色。
**解决**：新增 `advisor_user`，投顾工作台相关接口统一要求明确投顾角色或超级管理员；前端投顾菜单按投顾角色显示；业务操作工作人员接口统一改为 `require_staff_permission("order:write")`。
**验证**：新增 `scripts/test_role_permission_boundaries_http.py`；客服投顾/业务操作 403、运营待审核 200、客户 403、超级管理员投顾 200；既有工作人员业务窗口和投顾授权隔离回归、后端编译、前端构建通过。

### ISSUE-0323
- 日期：2026-08-04
- 分类：鉴权
**问题**：业务操作审计接口仍可被客服手工访问
前端虽隐藏业务操作菜单，但客服可绕过菜单直接请求业务操作审计接口。
**根因**：`/operations/audit-logs` 和工作人员操作详情仍使用通用 `current_user`，未要求工作人员权限。
**解决**：业务操作审计和工作人员操作详情统一要求 `order:write` 工作人员权限；客户本人 `/operations/me` 保持独立；投顾相关接口统一使用 `advisor_user`。
**验证**：角色边界测试确认客服业务操作审计 403、投顾 403、待审核 403；运营待审核 200、超级管理员投顾 200；前端构建通过。

### ISSUE-0324
- 日期：2026-08-04
- 分类：鉴权
**问题**：客户角色可通过 customer:read 访问工作人员客户列表
客户角色可手工访问工作人员客户管理列表；前端菜单隐藏不能替代服务端边界。
**根因**：`GET /profile/staff/customers` 使用通用 `require_permission("customer:read")`，客户角色也拥有该读取权限。
**解决**：改用 `require_staff_permission("customer:read")`，明确排除 customer 角色，同时保留客服等工作人员的客户管理访问。
**验证**：新增 `scripts/test_backoffice_role_surface_http.py`，客户访问客户列表 403、客服 200、运营 403、管理员 200；其余后台表面接口矩阵、既有权限回归、编译和前端构建通过。

### ISSUE-0326
- 日期：2026-08-04
- 分类：前端/鉴权
**问题**：开发状态重置后浏览器旧 refresh token 导致前台连续 401
`/auth/me`、`/profile/me`、资产历史、本人业务、账户、本人订单均返回 401，前端 refresh 也返回 401。
**根因**：`reset_dev_state` 清理了服务端 refresh session，浏览器仍保留旧 access/refresh token；这是认证会话失效，不是 RBAC 403。
**解决**：前端 HTTP 拦截器在 refresh 失败时清理 sessionStorage 凭据并触发 `auth:changed`，受保护页面回到登录入口。
**验证**：重新登录 customer_demo 后前台个人接口全部 200；无效 refresh token 返回 401；前端生产构建 PASS。

### ISSUE-0327
- 日期：2026-08-04
- 分类：前端
**问题**：认证失效后门户隐藏顶部栏导致无法返回登录/产品页
客户中心页面在旧 Token 失效后仍停留在门户路由；门户布局隐藏全局顶部栏，且未登录事件同步没有触发受保护路由跳转，用户无法看到登录入口或产品导航。
**根因**：`App.vue` 的 `sync()` 在 Token 已被拦截器清理后只设置 initialized，没有处理当前路由的 `requiresAuth`；`header` 又无条件排除 portal。
**解决**：无 Token 且当前路由需要认证时 replace 到 `/login`；未登录门户状态显示顶部栏。
**验证**：前台认证契约 PASS，前端构建 PASS；需浏览器硬刷新/重新登录完成现场验收。

# BUG_LOG part_9（docs/ISSUES.md 第 4254–4723 行）

### ISSUE-0329
- 日期：2026-08-04
- 分类：前端/后端
**问题**：超级管理员工单分派缺少处理人选择入口
超级管理员拥有分派权限，但前端没有处理人列表，只显示手工 UUID 输入，现场表现为无法分派。
**根因**：后端分派 API 已允许超级管理员，但前端没有实现 assignee 查询与选择。
**解决**：新增 `/workorders/assignees` 接口，前端改为工作人员下拉选择处理人。
**验证**：超级管理员分派 200；客户读取 assignees 403；工单 ASGI 回归和前端构建通过。

### ISSUE-0331
- 日期：2026-08-04
- 分类：后端/前端
**问题**：工单处理权限与分派人员感知
分派是真实数据库写入（`work_order.assigned_to`、审计 `assign` 事件）；非超级管理员只能处理自己 `assigned_to` 的工单，客服、客户经理、风控、运营具备处理权限，客户、理财顾问、产品经理、审计不具备处理权限。但处理人被分派后没有任何站内提醒，只能被动查看，感知缺失；处理边界上还存在处理人列表未预加载、非法状态可跳转、解决说明可不填、前端未按 `scope=assigned` 过滤、非本人处理按钮未隐藏等问题。
**解决**：处理人列表 ORM 预加载、限制非法状态跳转、解决说明必填、前端按 `scope=assigned` 过滤并隐藏非本人处理按钮；随后新增 `notification` 表、`/notifications` API 和后台通知收件箱，分派时处理人收到未读工单通知，客户也会收到工单已解决和已关闭反馈（状态：RESOLVED，站内通知闭环完成；邮件/短信/移动推送为后续增强）。
**验证**：处理人可以读取并标记自己的通知；其他用户标记同一通知返回 403；客户收到已解决/已关闭反馈。

### ISSUE-0332
- 日期：2026-08-04
- 分类：前端
**问题**：工单"已解决/待客户补充"空反馈导致连续 422
工作人员在"已解决/待客户补充"状态下提交空反馈时后端连续返回 422，界面无提示。
**根因**：后端需要结构化反馈说明（所选状态下该字段必填），但前端字段提示为通用"处理说明"，用户不知道必填。
**解决**：前端提交前提示并阻止空反馈；`pending_customer` 和 `resolved` 均要求反馈说明；已解决工单仅显示关闭操作。
**验证**：工单端到端回归和前端构建 PASS。

### ISSUE-0333
- 日期：2026-08-04
- 分类：后端/前端
**问题**：待客户补充流程缺少客户提交和工作人员回看闭环
工单置为"待客户补充"后，缺少客户提交补充内容、工作人员回看的完整闭环。
**根因**：此前只实现 `pending_customer` 状态与通知，没有客户补充内容表、提交 API、回流状态或工作人员查看入口。
**解决**：新增 `work_order_update` 补充记录、客户补充提交 API、补充记录展示，状态回到 `in_progress`，处理人收到通知，并做严格客户范围控制。
**验证**：端到端回归覆盖材料往返和权限边界，全部 PASS。

### ISSUE-0334
- 日期：2026-08-04
- 分类：前端
**问题**：客户补充提交后缺少可见确认与记录入口提示
客户提交补充材料后界面没有明确反馈，造成"按钮没有反应、没有记录"的现场误解。
**根因**：客户待补充时补充记录默认收起，提交成功后状态转为处理中且表单隐藏，缺少可见确认。
**解决**：待补充状态自动展开工作人员要求；提交后即时刷新记录并显示状态回流提示；加载/空记录状态明确展示。
**验证**：HTTP 工单闭环和前端构建 PASS。

### ISSUE-0335
- 日期：2026-08-04
- 分类：前端
**问题**：怀疑客户补充按钮未绑定（事件绑定防护）
现场"客户补充按钮没反应"。
**根因**：复核运行中 API 和日志证明补充接口可用，正确客户请求可得到 201，"没反应"不能归因于后端路由缺失；按钮未显式声明 `type`、未阻止默认 submit 与事件冒泡，可能造成干扰。
**解决**：所有工单卡片按钮明确 `type="button"` 并使用 `@click.prevent.stop`，排除默认 submit 和冒泡干扰。
**验证**：工单 HTTP 回归与前端构建 PASS。

### ISSUE-0336
- 日期：2026-08-04
- 分类：前端
**问题**：WorkOrdersView 报 `loadUpdates is not defined`
组件初始化中断，点击补充按钮不会发送网络请求。
**根因**：模板及 `load()` 保留函数调用，但 `loadUpdates`、`toggleUpdates`、`submitSupplement` 三个函数定义被一次失败的文本替换删除。
**解决**：恢复三个函数定义，写入后校验定义数量；确认 Vite 实时提供新源码。
**验证**：前端构建、Vite 源码检查和工单端到端回归全部 PASS。

### ISSUE-0337
- 日期：2026-08-04
- 分类：前端
**问题**：工单数量增长后缺少待办筛选与状态视觉区分
工作台默认堆叠全部卡片，无法快速定位高优先级或待客户补充工单；状态标签过度圆润，处理中/已解决视觉辨识不足。
**解决**：新增待办汇总、范围/状态/优先级/类型筛选、分页和优先级排序；状态改为带图标的方圆角语义标签。
**验证**：后端筛选汇总回归、前端构建全部 PASS。

### ISSUE-0338
- 日期：2026-08-05
- 分类：后端/前端
**问题**：客户补充缺少附件/凭证上传
客户补充材料缺少附件/凭证上传能力。
**解决**：新增附件表、上传/列表/下载 API、JPG/PNG/PDF 类型校验、10 MB 限制、工单权限校验和前端上传入口。
**验证**：附件上传/下载、越权、数据库持久化和全量工单回归 PASS。

### ISSUE-0339
- 日期：2026-08-05
- 分类：前端
**问题**：工作人员工单附件下载返回 401
工作人员点击下载工单附件时返回 401。
**根因**：前端使用 `<a href>` 访问受保护下载接口，浏览器不会向该请求附加 sessionStorage 中的 JWT。
**解决**：改为 axios blob 下载并触发本地保存；工作人员自动加载附件列表。
**验证**：工作人员附件列表与下载 HTTP 回归 PASS，前端构建 PASS。

### ISSUE-0340
- 日期：2026-08-05
- 分类：后端
**问题**：资产趋势未随实时持仓估值同步
高净值客户页面的总资产、持仓市值和浮动收益已增长，但"资产趋势"折线仍停留在旧快照。
**根因**：摘要接口会刷新当天 `derived` 资产快照，历史接口只读快照记录；前端并行访问两个接口时产生读模型不一致。
**解决**：历史接口在查询序列前调用 `AssetSummaryService.latest_or_derived()` 并提交，再返回有序快照；仅刷新当天可变 `derived` 节点，不修改 `daily_valuation` 和业务事件历史节点。
**验证**：估值脚本通过真实 ASGI 历史接口确认 `history_includes_current_total=true`；后端编译和前端构建均 PASS。

### ISSUE-0341
- 日期：2026-08-05
- 分类：后端
**问题**：风险预警待处理时订单仍可直接审核通过
订单已被规则检测出风险、风险预警/调查尚未处理时，运营人员仍可点击"通过并执行"。
**根因**：旧门禁仅查询人工已标记为 `confirmed_risk` 的预警；规则实时生成的 `open` 预警尚无人工评估结果，因此不构成阻断；同时订单规则评估发生在交易执行后，时序错误。
**解决**：订单审批前执行预审核风险检测；金额达到 50,000 元时创建幂等 RW-001 `open` 预警；所有未结案预警均阻断审批，返回 `409 RISK_CONTROL_BLOCKED`；预警先持久化、确保风控人员可见后再返回阻断。
**验证**：`scripts/test_risk_control_closure_http.py` 覆盖"检测出的 open 预警阻断审核"、既有 confirmed-risk 阻断、限制交易、解除限制与资金状态回归，PASS。

### ISSUE-0342
- 日期：2026-08-05
- 分类：后端/前端
**问题**：风险预警处理 422 无前端原因提示，要求资料未真正触达客户
风险人员处理预警时 `POST /risk-alerts/{id}/handle` 返回 422，界面缺少具体原因；"要求补充材料"此前只记录动作，客户没有可提交的入口。
**根因**：风险处理表单默认尝试结案，但"评估后通过"需评估依据、客户沟通和沟通记录；前端未做本地校验和错误映射；`REQUEST_MATERIALS` 分支原本为空操作，没有接入工单闭环。
**解决**：风险处理页面增加处理状态和规则提示，明确展示后端校验消息；选择 `REQUEST_MATERIALS` 时创建幂等、客户可见的 `risk_investigation` 工单，置为 `pending_customer`，给客户发送站内通知；客户复用既有工单补充文字/附件能力。
**验证**：风险门禁回归覆盖 409 拦截、资料补充工单创建且状态为 `pending_customer`、客户级限制与解除，PASS；前端构建 PASS。

### ISSUE-0330
- 日期：2026-08-05
- 分类：前端（风控界面）
**问题**：风险预警评估界面字段平铺导致处置语义不清，客户补件可见性需要明确
风险处理窗口同时展示处理状态、评估结论、评估说明、客户沟通、沟通记录、实际处置和处置说明，操作员难以判断哪些字段必须填写，可能选择语义冲突的组合；`REQUEST_MATERIALS` 之后客户在哪里看到补件、补件后是否自动解除订单门禁不明确，现场容易误以为"风险工单没有返回给客户"。
**解决**：风险处置界面改为先选择"本次处置"，按场景显示字段：`REQUEST_MATERIALS` 自动设置 `pending_customer` + `insufficient_information`，只显示信息不足原因和资料清单；继续调查、挂起/拒绝订单、限制交易、报告草稿、升级处理显示调查/处置依据；只有"评估后通过并结案"才显示并强制客户沟通勾选与沟通记录；前端成功信息明确提示已创建客户可见的风控补件工单并发送站内通知；客户入口为"客户中心 → 我的工单"（类别 `risk_investigation`、状态 `pending_customer`），补充后工单转 `in_progress` 并通知原风控人员；RiskAlert 继续保持 `pending_customer`，交易门禁不自动解除。该改造为场景化界面第一阶段，仍待增强：风险预警列表展示关联工单号、补件摘要和直接查看入口；客户补充后将 RiskAlert 从 `pending_customer` 自动回推 `investigating`。

### ISSUE-0331
- 日期：2026-08-05
- 分类：后端（风控状态机）
**问题**：风险限制解除要求预警先结案，与受限状态禁止结案形成循环依赖
RW-001 预警执行 `RESTRICT_TRADING` 后，账户变为 `restricted`、预警保持 `investigating`。旧 `RELEASE_RESTRICTION` 要求 alert 已 `resolved/dismissed`；而风险处理接口在 restriction 仍为 `restricted` 时又拒绝 `resolved`，操作员无法解除限制或完成结案。
**根因**："风险调查结论""账户交易限制""预警最终结案"是三个不同状态，但旧实现把解除动作错误依赖于最终结案状态，造成状态机环形依赖；前端又曾只在 `acknowledged` 显示评估入口，进一步放大受限预警无法继续处理的问题。
**解决**：`RELEASE_RESTRICTION` 改为要求当前 alert 为 `restricted`、复核结论为 `cleared_after_review` 或 `false_positive`、评估依据非空（`cleared_after_review` 额外要求已与客户沟通并记录沟通内容）；解除成功后恢复账户 `active`、限制状态 `none`，但预警不自动删除，由风控人员随后执行 `resolved/dismissed`，保留完整限制和解除审计；风险列表未结案状态保持"继续处置"入口，限制交易时自动形成 `investigating + confirmed_risk` 并要求填写限制依据。正确闭环：confirmed_risk + RESTRICT_TRADING → investigating/restricted → cleared_after_review 或 false_positive（复核证据齐全）→ RELEASE_RESTRICTION → restriction_status=none → resolved/dismissed。
**验证**：状态为代码修复完成（RESOLVED）；完整 HTTP/浏览器回归待执行（待验证：限制、复核、解除、结案顺序；无复核结论/无评估依据/无客户沟通记录时不得解除；浏览器现场确认"继续处置""解除交易限制"和结案提示一致）。

### ISSUE-0332
- 日期：2026-08-05
- 分类：后端/部署（AML 调度）
**问题**：AML 日批仅有手工脚本，缺少常驻调度入口和首次运行证据
`DailyAmlScanService`、`aml_batch_run` 与手工脚本已存在，但没有常驻调度入口，风险后台没有批次记录时无法区分"未执行扫描"和"扫描完成但未命中"。
**解决**：新增 `workers/aml_scheduler.py`，使用 UTC 小时/分钟、轮询间隔和扫描天数配置触发日批；不在 API 请求进程中执行，避免长时间扫描阻塞业务接口；复用 `DailyAmlScanService.batch_key` 幂等边界，同窗口重复运行返回已有批次；`scripts/run_daily_aml_scan.py` 增加 `--repeat` 支持固定窗口幂等演示；`.env.example` 与 `Settings` 增加 `AML_SCHEDULER_*` 配置及范围校验。
**验证**：首次真实运行 batch status=completed，客户 3/3 成功、失败 0、新增预警 1（扫描窗口 2026-07-30T00:00:00Z ～ 2026-08-06T00:00:00Z）。

### ISSUE-0343
- 日期：2026-08-05
- 分类：后端/前端（风控工单闭环）
**问题**：风险预警列表缺少客户补件上下文，补件后复核待办不明显
客户提交风险补件后工单转为 `in_progress`，但风险预警列表没有关联工单编号、客户补件摘要或"待风控复核"提示，处理人员需要在两个后台页面之间手工比对，容易遗漏后续核验。
**解决**：客户补件事务内将关联预警由 `pending_customer` 回推为 `investigating`，并保留审计和原处理人通知；风险列表批量关联 `work_order` 和最近客户 `work_order_update`，避免 N+1 查询；响应和前端新增工单编号、工单状态、补件摘要与查看工单入口；不自动解除订单门禁、交易限制或结案，仍要求风控人员复核并作出显式处置。
**验证**：真实 HTTP 回归确认客户补件后 `RiskAlert=investigating`、`WorkOrder=in_progress`，风险列表可返回关联工单和补件正文；限制交易仍严格要求"复核 → 解冻 → 结案"。

### ISSUE-0344
- 日期：2026-08-05
- 分类：部署
**问题**：AML Scheduler 缺少可部署的常驻运行入口
虽然已有 `workers/aml_scheduler.py`，但基础 Compose 只管理 PostgreSQL、Redis、Milvus 和迁移 MySQL，没有应用 Worker 镜像或正式启动命令。
**解决**：新增可复用应用 `Dockerfile`，以 Python 3.12 构建并安装项目依赖；新增 `aml-scheduler` Compose 服务，使用独立 `aml` profile，默认不自动运行；服务等待 PostgreSQL 健康后启动，并使用容器内 `postgres:5432` 数据库地址；提取单次调度周期：成功 slot 去重，失败不消费 slot，下一轮轮询可重试；新增 `scripts/test_aml_scheduler_cycle.py` 覆盖准点执行、同 slot 去重和异常重试。
**验证**：运行验收已通过：Worker 复用现有 `wealth-p0` PostgreSQL 容器，日志输出 `aml scheduler started` 后进入轮询；验收后已停止该 Worker，未产生额外扫描批次。

### ISSUE-0345
- 日期：2026-08-05
- 分类：后端（可观测性）
**问题**：AML Scheduler 无持久心跳，后台无法判断 Worker 是否存活
Worker 是否存活无从判断，无法区分"未到扫描时间但 Worker 在线"和"Worker 已停止"。
**解决**：新增 `aml_scheduler_state` 与 `/risk-alerts/scheduler-status`；Worker 每轮轮询持久化心跳，扫描开始、成功或失败时记录状态和时间；API 以 `max(3 * poll_seconds, 180)` 秒作为过期阈值，返回 `running`。
**验证**：真实定时验收在 `12:42Z` 自动触发，批次幂等复用且状态表为 `completed`；前端生产构建仍受本机 Vite `spawn EPERM` 影响，待环境权限恢复后复验。

### ISSUE-0346
- 日期：未标注
- 分类：后端
**问题**：AML 部分失败缺少保留原证据的定向补跑入口
批次中部分客户失败时缺少定向补跑入口，且需保留原扫描证据。
**解决**：失败客户从原批次 `error_summary` 提取，使用独立客户范围批次补跑；原批次、错误摘要和审计不被覆盖（状态：RESOLVED 后端接口，前端操作入口待实现）。
**验证**：HTTP 回归：客户补跑返回 403；管理员首次补跑 `created=true`，重复补跑 `created=false`。

### ISSUE-0348
- 日期：未标注
- 分类：后端
**问题**：已完成 AML 窗口无法补扫迟到入账流水
已完成的扫描窗口无法对迟到入账流水补扫。
**解决**：新增独立 `late_rescan` 批次、来源批次血缘和稳定补扫键；不覆盖原扫描证据（状态：RESOLVED，后端与 HTTP 回归完成）。

### ISSUE-0347
- 日期：未标注
- 分类：前端
**问题**：风险预警"查看工单"跳转到前台
从风险预警查看关联工单时错误跳转到前台页面。
**根因**：`/workorders` 不是独立 Vue 路由，工单是 `/backoffice` 壳层内的 `workorders` 标签页；硬编码 `RouterLink` 命中失败后回落前台。
**解决**：风险预警改为跳转 `/backoffice?section=workorders`；后台壳层监听 `section` 参数并调用既有内部 `select`。
**验证**：`npm run build` PASS；现场验收：风险管理入口已可打开并定位关联工单，PASS。

### ISSUE-0349
- 日期：2026-08-06
- 分类：部署
**问题**：Windows Docker Desktop BuildKit 在中文工作区路径下构建双镜像失败
默认 BuildKit 构建 API 与 AML Scheduler 两个镜像时，Docker Desktop 命名管道报错：`x-docker-expose-session-sharedkey contains value with non-printable ASCII characters`。
**解决**：临时方案（WORKAROUND）：设置 `DOCKER_BUILDKIT=0` 后使用传统构建器构建 `api`、`aml-scheduler` 两镜像成功；后续需升级 Docker Desktop 或将工作区移至纯 ASCII 路径后复验 BuildKit。
**验证**：关闭 BuildKit 后传统构建器可成功构建两个镜像；该问题不影响容器运行语义。

### ISSUE-0351
- 日期：2026-08-06
- 分类：后端（RAG/性能）
**问题**：RAG 旧入库对文档小改动执行全量 Embedding
旧 `KnowledgeIngestionService` 每次新版本均对所有 Chunk 调用 Embedding，文档小改动也触发全量嵌入，成本与性能开销大。
**解决**：按 `content_hash` 从上一 active 版本复用 pgvector embedding，仅对新增/变化 Chunk 批量重嵌入；新版本仍独立保存并原子激活，保留审计回溯能力（状态：RESOLVED）。

### ISSUE-0358：KnowledgeScope 引入 app.services 聚合导入造成循环依赖
- 日期：2026-08-06
- 分类：后端（RAG / Python 模块边界）
**问题**：KnowledgeScope 引入 app.services 聚合导入造成循环依赖
Repository 模块引入知识库 Scope 时触发服务层聚合包初始化，形成部分初始化循环，导致模块无法正常加载。
**根因**：Repository 导入 `app.services.knowledge_scope` 时会先执行 `app/services/__init__.py`，该文件主动导入 `KnowledgeIngestionService`；入库服务又依赖 `KnowledgeRepository`，形成部分初始化循环。
**解决**：新增无业务依赖的值对象位置 `app/common/knowledge_scope.py`；`app/services/knowledge_scope.py` 保留兼容导出；Repository 和 Retriever 改从 `app.common.knowledge_scope` 导入，Scope 逻辑与 Service 聚合导入解耦。

### ISSUE-0360：客服 RAG 低置信度转工单接入时路由文件括号不完整
- 日期：2026-08-06
- 分类：后端（客服 RAG / API 集成）
**问题**：客服 RAG 低置信度转工单接入时路由文件括号不完整
自动替换 `customer_rag.py` 时缺少关闭括号，导致 API 文件无法导入，客服 RAG 路由不可用。
**根因**：自动生成/替换的路由代码缺少关闭括号，产生 Python 语法错误。
**解决**：重写为明确的路由实现，并在低置信度时调用 `CustomerRagHandoffService` 创建或复用 `customer_service` 工单，返回 `work_order_id`、`ticket_no`、`work_order_created`；同时为 `WorkOrder` 增加 `context_json`，保存客服 RAG 原问题、意图、证据状态、查询哈希和候选引用上下文，不自动结案。

### ISSUE-0361：客服 RAG HTTP 回归暴露 Retriever Scope 签名未实际透传
- 日期：2026-08-06
- 分类：后端（RAG 检索 / API 集成 / 容器部署）
**问题**：客服 RAG HTTP 回归暴露 Retriever Scope 签名未实际透传
首次 API 容器未重建时路由返回 404；重建后路由可加载，但 `CustomerRagService` 传入 `scope` 时 `WealthKnowledgeRetriever.search()` 仍是旧签名，导致 HTTP 500。
**根因**：此前文本替换只改了调用处，未真正修改函数签名和 Repository 的两条检索路径，Scope 参数没有实际透传。
**解决**：完整重写 `knowledge_retrieval.py`，明确在 `search()` 签名及 `search_hybrid`/`search_text` 调用中透传 `KnowledgeScope`，并重建 Compose API 容器。
**验证**：低置信度工单真实 HTTP 回归通过。

### ISSUE-0362：Docker 重命名后旧 Compose 工作区与 AML 语法错误造成运行版本不一致
- 日期：2026-08-06
- 分类：部署（Docker / Compose / 容器）
**问题**：Docker 重命名后旧 Compose 工作区与 AML 语法错误造成运行版本不一致
文件重命名后，Docker 之前创建的 `wealth-p0` 容器标签仍指向旧的 `W:\docker-compose.p0.yml` 工作区，运行容器可能不是当前代码版本；同时 AML Scheduler 镜像内加载 `app/services/daily_aml_scan_service.py` 时发现一行混入字面量 `` `n ``，导致 `SyntaxError`，容器无法启动。
**根因**：文件重命名后旧容器标签仍指向旧工作区 Compose 配置，未用当前工作区重建；AML Scheduler 源码混入字面量 `` `n `` 造成 Python 语法错误。
**解决**：使用当前工作区 `C:\Users\tx360\Desktop\wealth-manager` 下的 `docker-compose.p0.yml` 重新构建并 recreate API、Outbox Worker、Knowledge Worker 和 AML Scheduler；修复 AML Scheduler 的 Python 语法错误并重建 `Dockerfile.aml-scheduler` 镜像；全量 Compose 的迁移服务从 `mysql` 改名为 `mysql-migration`，改为显式 profile、`restart: "no"`、带 `start_period` 的健康检查，并接入独立 `wealth-migration` 网络；旧无 Compose 标签的 MySQL 容器改名为 `wealth-mysql-migration-legacy` 保留原数据，不删除卷。
**验证**：API `/api/v1/health` 返回 HTTP 200；PostgreSQL、pgvector、Redis、Qwen/Embedding 健康检查通过；Outbox、Knowledge Worker、AML Scheduler 均保持运行；MySQL migration healthy（端口 3307）；迁移探针 MySQL 8.0.46 连接、建表、插入、读取均通过；两套 Compose 配置及两个 Dockerfile 构建通过。

### ISSUE-0364：Catalog 向量化不应因外部 Embedding 波动阻塞实时目录检索
- 日期：2026-08-06
- 分类：后端（RAG 向量化 / 异步任务 / 可用性）
**问题**：Catalog 向量化不应因外部 Embedding 波动阻塞实时目录检索
DashScope OpenAI 兼容 Embedding 端点存在偶发连接重试：若 Catalog Worker 把所有目录对象作为单一事务批处理，单个请求失败会让整个任务无法写回；若 Catalog Search 每次强制等待查询向量，也会使实时 API 超时。
**根因**：向量化采用全批事务、检索每次同步等待外部向量请求的设计，使外部 Embedding 端点的偶发连接重试直接阻塞整批写回与实时 API。
**解决**：Catalog Embedding 改为单条成功即写回，避免整批回滚；通过 Outbox/Redis 独立 Worker 执行，API 只负责入队；Search 仅在已存在可访问 Catalog 向量时尝试向量查询并设置 8 秒超时，超时/异常时立即返回权限过滤后的文本检索结果；所有可查询目录对象完成向量化后再启用 `catalog-hybrid-text-pgvector`。
**验证**：Outbox 发布 `data_catalog.embedding_requested` 至 `stream:wealth:catalog`；Worker 消费并完成 67 个目录对象向量化；"客户资产趋势" 真实 HTTP 检索返回 `catalog-hybrid-text-pgvector` 并召回 `customer_asset_snapshot`；API health 仍为 HTTP 200；非查询字段未嵌入。

### ISSUE-0365：客服简单问候被低置信度路径错误转人工并创建无效工单
- 日期：2026-08-07
- 分类：后端（客服 RAG / 用户体验 / 工单治理）
**问题**：客服简单问候被低置信度路径错误转人工并创建无效工单
`CustomerRagService` 原先对所有非产品推荐问题直接进行知识库检索；问候、感谢、身份询问通常没有知识库 Chunk 命中，"你好""你是谁""谢谢"等输入会进入 `low_confidence`，继而创建或复用客服工单，产生无效工单并打扰人工。
**根因**：缺少问候/闲聊分流，问候类输入走通用知识检索后因无命中被判定为低置信度。
**解决**：新增基于规范化文本的问候/闲聊分流：命中问候、客服身份询问、感谢或结束语时直接返回固定安全话术，结果为 `answer_mode=greeting`、`evidence_status=not_required`，不调用 Retriever、Embedding、回答模型或 Handoff Service。
**验证**：客户账号真实调用 `/api/v1/customer-rag/answer` 输入"你好"返回 HTTP 200，响应约 35ms；响应无 citation、无工单、`handoff_required=false`；API 日志未出现 Embedding 调用。

### ISSUE-0366：前端 Vite 代理未运行导致页面统一显示 Network Error
- 日期：2026-08-07
- 分类：前端（运行环境 / Vite 代理）
**问题**：前端 Vite 代理未运行导致页面统一显示 Network Error
浏览器页面统一显示 `Network Error`，用户数据读取、登录后的 `/auth/me`、知识库文档读取和上传均不可用。
**根因**：Docker API 容器、PostgreSQL、Redis 均正常，`http://127.0.0.1:8010/api/v1/health` 返回 HTTP 200，且 API 日志没有收到浏览器请求；前端 Axios 使用相对地址 `/api/v1`，依赖 Vite 的 `/api → http://127.0.0.1:8010` 代理；当 Vite 没有监听 `127.0.0.1:5173` 时，浏览器无法将相对请求转发给 Docker API，因此所有 API 调用统一表现为 Network Error。
**解决**：从 `frontend` 目录启动 `npm run dev -- --host 127.0.0.1` 恢复 Vite 代理；并在 `DEVELOPMENT_WORKFLOW.md` 增加 Vite 启动和 Network Error 快速排查命令作为预防（若 API 8010 正常而页面报 Network Error，优先检查 5173 Vite 服务和访问地址）。
**验证**：`http://127.0.0.1:5173/` 与 `http://127.0.0.1:5173/api/v1/health` 均返回 HTTP 200；经 Vite 代理调用 `/auth/login`、`/auth/me`、`/knowledge/documents` 均返回 HTTP 200；知识库文档列表成功返回 4 条记录。

### ISSUE-0367：知识上传状态静态且客服 RAG 只依赖整句相似度造成常见问法漏召回
- 日期：2026-08-07
- 分类：前端 + 后端（知识库上传 / 客服 RAG 检索）
**问题**：知识上传状态静态且客服 RAG 只依赖整句相似度造成常见问法漏召回
知识上传后页面只在上传完成时读取一次列表，文档仍处于 `processing` 时管理员必须手动刷新才知道是否已向量化；客服 RAG 以用户原始整句检索，"客户规模"可命中，而"客户规模多大""公司名字是什么"等口语问法因与文档表达不同被误判为低置信度。
**根因**：前端上传状态只读取一次、无自动轮询；检索只做整句相似度匹配，未对常见口语问法做归一化。
**解决**：前端对存在 `processing` 文档的列表每 3 秒自动轮询，并显示处理数量、最近刷新时间和阶段说明；客服服务在检索前做受控规则式归一化（公司名字扩展为公司名称/公司全称/公司叫什么，客户规模扩展为客户数量/服务客户数量等）；知识库文本检索改为按归一化后的多个关键词 OR 匹配，混合检索的词法部分使用 web search query；保持 Scope、低置信度工单、产品推荐和问候分流边界不变。
**验证**：真实客户 HTTP 调用"客户规模多大""客户规模""公司名字是什么"均返回 `rag_answer` 和有效引用；上传页面修改通过 Vite 生产构建。

### ISSUE-0373
- 日期：2026-08-07
- 分类：数据库
**问题**：数据库迁移记录与实际 schema 不一致导致 heartbeat 字段不可用
模型和 API 已包含 `agent_last_heartbeat_at`，但运行中的 PostgreSQL 缺少该列，创建客服会话时出现 `column "agent_last_heartbeat_at" of relation "customer_service_session" does not exist`；执行标准 `alembic upgrade head` 时发现数据库 `alembic_version` 停在 `0009_aml_batch`，而 0010-0020 对应表已存在。
**根因**：0010-0020 对应表已由运行时初始化直接落库，但 Alembic 版本记录没有跟上，迁移记录落后于实际 schema，导致 heartbeat 列未按迁移链补齐；首次迁移又因 `work_order` 已存在而失败。
**解决**：核对数据库表结构与迁移链，确认 0010-0020 已落库后，将 Alembic 版本记录按实际 schema 对齐到 `0020_customer_memory`，确认 heartbeat 列/索引存在后完成版本记录到 `0021_customer_service_heartbeat`，并执行 heartbeat HTTP 回归和真实超时 sweep 回归。
**验证**：`alembic current` 为 `0021_customer_service_heartbeat (head)`；claim、heartbeat、fresh session 不被 sweep 暂停通过；过期 heartbeat 会将 `human_active` 转为 `paused`、清空 `assigned_agent_id`，客服可重新 claim；临时测试数据已清理。

### ISSUE-0374
- 日期：2026-08-07
- 分类：后端（客服 Agent / Worker）
**问题**：客服 heartbeat 超时此前只有管理员手动 sweep，缺少后台自动执行
heartbeat 超时 API 已存在，但没有管理员主动调用 `sweep-timeouts` 时，过期 `human_active` 会话不会自动释放坐席。
**根因**：超时释放只提供手动调用的 API，缺少常驻后台自动执行，过期会话无法自动释放。
**解决**：抽取 `CustomerServiceTimeoutService` 统一 API 与 Worker 的 sweep 行为；新增 `workers.customer_service_timeout_worker` 默认每 30 秒运行一次；增加 `CUSTOMER_SERVICE_TIMEOUT_WORKER_ENABLED`、`CUSTOMER_SERVICE_TIMEOUT_WORKER_POLL_SECONDS`、`CUSTOMER_SERVICE_HEARTBEAT_TIMEOUT_SECONDS` 配置；Docker Compose 使用 `customer_service` profile，避免默认启动引入额外后台任务。
**验证**：Service 回归通过，Compose Worker 已成功启动并输出首轮 sweep 日志。

### ISSUE-0376
- 日期：2026-08-07
- 分类：前端
**问题**：前端断线与 Worker 自动暂停状态反馈缺失
客服 heartbeat 或客户人工消息轮询失败时，页面原先只显示通用错误，无法区分"连接恢复中"和"会话已被 Worker 暂停"。
**根因**：页面只有通用错误提示，没有连接状态与会话状态（`paused`）的区分与提示。
**解决**：客服工作台增加连接状态；heartbeat 失败后重新读取消息/会话状态；会话已变为 `paused` 时提示客服重新接入；客户侧人工消息轮询失败时显示"连接恢复中"。
**验证**：前端生产构建通过。

### ISSUE-0382
- 日期：2026-08-08
- 分类：后端（API 契约）
**问题**：意图任务整体状态需要统一，避免 API 层使用非标准状态
原有 `execute-plan` 在 API 层直接计算 `succeeded`/`partial`，与规划中的 `completed`/`partially_completed` 等稳定契约不一致，前端难以可靠判断整体结果。
**根因**：聚合逻辑散落在 API 层，直接产出与既定契约不一致的非标准状态。
**解决**：将聚合逻辑收敛到 `CustomerTaskExecutor.summarize`，统一返回 completed、partially_completed、needs_clarification、handoff、failed、blocked_by_human，并保留任务级结果。
**验证**：纯函数聚合测试、HTTP 多任务执行测试和客服 heartbeat 回归通过。

### ISSUE-0383
- 日期：2026-08-08
- 分类：后端（意图识别 / 澄清 / 记忆）
**问题**：澄清轮次未受控，可能导致重复追问或上下文丢失
原有短期记忆只保存 `clarification` 和 `missing_parameter`，没有明确澄清轮次及待处理任务，客户补充信息后容易重新从零规划，无法稳定限制澄清次数。
**根因**：短期记忆缺少澄清轮次计数与待处理任务，补充后原始上下文无法复用。
**解决**：增加 `clarification_count` 和 `pending_intent_plan`，首次澄清保存待处理任务；补充后复用原始问题与当前回答；第二次仍无法识别时直接创建人工 handoff，并清理 pending 状态。
**验证**：HTTP 回归通过，覆盖澄清复用和二次澄清人工兜底。

### ISSUE-0384
- 日期：2026-08-08
- 分类：后端（实体提取 / 业务事实查询）
**问题**：明确订单/工单编号未进入任务参数，查询可能退化为最近记录
用户明确提供订单号或工单号时，原有任务虽然识别出业务意图，但没有把编号传给 Query Router，可能仍按最近记录返回，无法保证回答的是用户指定对象。
**根因**：任务只识别业务意图，未把明确的订单号/工单号作为参数透传给查询路由。
**解决**：增加订单号/工单号确定性提取，并将 arguments 从 Planner 传递到 Tool 和 Query Router；查询继续限制在当前客户数据范围内。
**验证**：实体纯函数测试和 Query Router HTTP 回归通过。

### ISSUE-0386
- 日期：2026-08-08
- 分类：后端（Tool 错误映射）
**问题**：同名产品结果未正确传递为任务级澄清
Query Router 已返回 `evidence_status=needs_clarification`，但 Tool 层未将该状态转换为失败/歧义错误，导致 Executor 可能返回错误的成功状态。
**根因**：产品 Tool 层缺少对歧义结果的错误映射，澄清状态在 Tool 边界丢失。
**解决**：对产品 Tool 增加 `ambiguous_product` 映射，Executor 将 `ambiguous_product` 转换为 `needs_clarification`，保留 Query Router 提供的候选产品数据和澄清建议。
**验证**：产品实体提取、同名产品端到端、订单/工单精确查询、既有 Query Router 和任务执行回归均通过。

### ISSUE-0387
- 日期：2026-08-08
- 分类：前端
**问题**：多意图执行结果前端只展示聚合回答，缺少任务级可解释性
后端已经返回每个任务的状态和结果，但客户页面主要展示一段聚合答案，无法清楚区分订单、资产、风险限制等子任务的完成情况。
**根因**：前端未消费后端已有的任务级状态与结果，只渲染聚合回答。
**解决**：客户智能客服页面增加整体状态、任务明细、任务级澄清和错误展示，继续复用现有 API 契约。
**验证**：前端生产构建和任务执行 HTTP 回归通过；浏览器视觉 E2E 列为下一次前端专项回归。

### ISSUE-0389
- 日期：2026-08-08
- 分类：后端（业务操作 / 风控 / 确认边界）
**问题**：混合查询与操作请求可能把受控操作误表述为已完成
`business_operation` 原先只返回通用澄清，无法在任务结果中明确表达"操作未执行、需要最终确认"；当请求同时包含查询和赎回等操作时，前端可能难以区分查询完成与操作状态。
**根因**：受控操作缺少独立的"待确认、未执行"结果表达，整体聚合可能将未执行的操作认定为完成。
**解决**：为 `business_operation` 引入 `needs_confirmation`，返回操作名称、`confirmation_required=true` 与 `executed=false`；整体聚合不会将该操作认定为完成；客服 Agent 不直接执行赎回、转账或下单。
**验证**：混合订单查询与赎回的 HTTP 回归通过，现有多任务和澄清回归继续通过。

### ISSUE-0390
- 日期：2026-08-08
- 分类：测试
**问题**：受控业务操作 E2E 初次失败的测试数据与页面断言修正
初次新增 E2E 使用"请帮我赎回这笔投资"，该表达被当前产品推荐/适当性规则识别为需要补充对象，未进入 `needs_confirmation`，因此没有显示"前往办理"；另外客户中心页面的"业务办理"是动态 h1，不是固定语义 heading 选择器。
**根因**：测试输入与页面选择器不匹配——测试表达不被当前 Planner 明确支持，动态标题被当作固定语义 heading 断言；不改变业务安全边界。
**解决**：测试改用当前 Planner 明确支持的"请帮我赎回"，并使用 h1 文本断言客户中心"业务办理"标签。

### ISSUE-0393
- 日期：2026-08-10
- 分类：前端/后端
**问题**：已接入客服会话不在 waiting_human 队列，通知点击无法直接聚焦
后台客服工作台列表默认只查询 `waiting_human`，而客户消息通知通常指向已 `human_active` 的会话。点击通知后虽然进入客服页面，但找不到目标会话，无法展示会话头和消息。
**根因**：工作台只按 `waiting_human` 队列定位会话，通知路由又没有保留/利用会话标识去聚焦已接入会话。
**解决**：通知导航保留 `customer_service_session_id`；工作台优先从队列选择目标，会话不在队列时使用当前已授权消息接口确认会话可访问并构造聚焦上下文，再加载消息；通知点击仍先执行通知已读更新，后端消息接口继续校验坐席范围。
**验证**：Docker P0 API 与 installed Chrome Playwright E2E 通过，覆盖通知自动已读、URL 会话 ID 和目标会话聚焦。

### ISSUE-0394
- 日期：2026-08-10
- 分类：后端（客服通知 / Outbox / 幂等）
**问题**：客服通知同步写入会耦合主事务，重复消费可能形成重复通知
客服 claim、客户消息和人工回复原先直接创建 Notification。通知表写入异常会与客服主事务耦合；引入 Worker 或重试后，若没有稳定事件键，也可能产生重复通知。
**根因**：通知创建与客服主事务同链路同步写入，且缺少稳定的幂等事件键。
**解决**：客服主事务改为同事务创建 `notification.customer_service` OutboxEvent，Outbox Worker 发布到 notifications Stream，Notification Worker 按 `user_id` 与 `idempotency_key` 创建通知；重复消费、唯一冲突和 Worker 重试不会重复插入，通知失败也不回滚客服主状态。
**验证**：真实 P0 Docker 环境下 Outbox 发布、Redis Stream 消费、Notification ACK、claim/客户消息通知和不同事件通知均通过回归。

### ISSUE-0395
- 日期：2026-08-10
- 分类：后端（通知可靠性 / Outbox / 运维治理）
**问题**：通知 Outbox dead 事件缺少后台观察与受控恢复入口
Outbox Worker 可以将连续失败事件标为 dead，但管理员此前无法查看失败原因、判断影响范围或受控重试，导致通知投递故障只能依赖数据库手工操作。
**根因**：dead 事件缺少管理端查看与受控重试入口。
**解决**：新增仅限 `audit:read` 权限的通知 Outbox 列表和 dead 重试接口；后台提供通知投递监控页面。仅 notification 类型且状态为 dead 的事件可以重试；重试重置 pending、attempt、错误信息和发布时间，并写入 AuditLog。
**验证**：强制发布失败后事件进入 dead；管理员可见并 retry；普通客户 403；重复 retry 409；真实 Docker Workers 随后可恢复投递通知。

### ISSUE-0397
- 日期：2026-08-10
- 分类：后端/前端（WebSocket 请求控制）
**问题**：WebSocket 请求无法取消，客户等待期间只能关闭页面或等待 HTTP 回退
首版 WebSocket 已能返回 chunk 和 final，但单个执行请求未由 `request_id` 管理。客户在慢查询期间无法取消；关闭页面会丢失会话，而继续等待也会占用服务端执行资源。
**根因**：服务端没有按 `request_id` 跟踪执行任务，前端也没有取消通道。
**解决**：后端对每个 `agent.request` 创建并追踪独立 `asyncio.Task`；新增 `agent.cancel`，按 `request_id` 取消活动任务并返回 `agent.cancelled`；取消事件发送通过 `asyncio.shield` 保护，避免任务取消同时中断确认事件。前端在流式请求期间显示"取消查询"，发送取消后展示稳定状态；`agent.cancelled` 结束本次流式生命周期且不会触发 HTTP fallback。重复 request id 和非活动请求取消均返回明确协议错误。
**验证**：P0 Docker API 的独立 WebSocket 回归覆盖正常流式和取消；installed Chrome Playwright 覆盖点击取消、取消状态、`agent.cancelled` 与正常 chunk/final 流程，均通过。

### ISSUE-0398
- 日期：2026-08-10
- 分类：后端/前端（WebSocket 澄清补充）
**问题**：客服 Agent 澄清后的补充信息无法在同一实时会话继续执行
客服 Agent 返回 `needs_clarification` 后，客户只能重新发起普通请求；前端没有明确的补充输入入口，无法保证补充信息与原 `conversation_id` 绑定，也无法在同一 WebSocket 实时通道继续接收结果。
**根因**：实时通道缺少在原会话内继续补充输入并复用的协议与前端入口。
**解决**：新增 `agent.amend` 事件，后端在同一 WebSocket `conversation_id` 下复用 Agent 执行入口；前端在 `needs_clarification` 结果下展示补充信息输入，使用新的 request_id 发送 amend，并继续接收 chunk、任务结果和 final。HTTP execute-plan 仍保留为异常降级路径。
**验证**：独立 WebSocket 回归覆盖正常请求、`agent.amend` final 和 `agent.cancelled`；installed Chrome Playwright 覆盖补充信息输入、`agent.amend` 发送，并与取消/正常流式用例联跑通过。

### ISSUE-0399
- 日期：2026-08-10
- 分类：前端（WebSocket 可观测性）
**问题**：客服 Agent 多任务执行阶段在前端不可见
WebSocket 后端已经发送 `agent.accepted`、`agent.progress` 和 `agent.task.completed`，但客户页面原先只累积 chunk 并在 final 后展示任务列表。客户无法知道请求是否已接收、当前正在规划还是正在整理回复，也无法感知多任务逐项完成。
**根因**：前端没有消费 accepted/progress/task.completed 等过程事件，只渲染最终结果。
**解决**：前端新增总体进度状态和展示文本，消费 accepted、progress、task.completed、cancelled、final 事件。页面新增 `customer-agent-progress` 测试标识，实时展示"已收到您的问题""正在识别问题并准备查询""正在整理回复""已完成一项任务""回复已生成"等阶段，不改变后端业务门禁和 HTTP 降级边界。
**验证**：installed Chrome Playwright 实时进度 E2E 与取消、澄清补充、正常流式三条 WebSocket E2E 联跑通过；独立 Python WebSocket 回归、Vite 构建和 Python 编译通过。

### ISSUE-0400
- 日期：2026-08-11
- 分类：后端/前端（实时客服 / WebSocket / Redis Pub/Sub）
**问题**：人工客服会话状态只能依赖轮询，客户无法实时感知暂停、释放与结束
客户侧此前只能在 AI 返回人工会话 ID 后按 2.5 秒轮询会话消息。坐席 claim、pause、release、resolve、close 等状态已经由 HTTP 状态机正确处理，但客户无法立即看到人工接入、暂停、重新等待或服务结束；人工回复也需要等待下一轮轮询。
**根因**：客户侧只依赖固定周期轮询，缺少会话状态与人工消息的实时推送通道。
**解决**：PostgreSQL 仍是会话与消息事实源，HTTP 仍负责受控事务提交；在提交成功后发布客户专属 `customer-realtime:{customer_id}` Redis Pub/Sub 事件。客户 Agent WebSocket 在完成鉴权后订阅该频道并 relay `human.session.claimed/paused/released/resolved/closed` 与 `human.message.received`。前端即时更新人工生命周期、按消息 ID 去重追加人工回复，并对 paused/released 保留轮询补偿；resolved/closed 时停止轮询并清除会话缓存。
**验证**：P0 Docker 生命周期独立回归通过 claim、pause、resolve、close 实时事件；installed Chrome 浏览器 E2E 通过客户页面即时显示 human_active、paused、resolved 并清除人工会话控件；五条客服 Agent/人工客服 WebSocket E2E、前端构建、独立协议回归和 Python 编译均通过。

### ISSUE-0401
- 日期：2026-08-11
- 分类：前端/后端（实时客服 / 断线恢复 / 增量同步）
**问题**：人工消息断线后需要全量轮询，缺少最后消息 ID 增量补偿
客户 WebSocket 断开或实时事件暂时不可用后，前端只能依赖固定周期轮询并重复读取全部人工消息。消息量增长后会增加请求和重复合并成本，也缺少明确的"正在恢复并补齐消息"状态。
**根因**：断线补偿没有基于最后消息 ID 的增量同步，恢复时重复拉取并合并全部消息。
**解决**：客户页面记录最后一条人工消息 ID；人工消息轮询和断线恢复使用 `messages?after={last_message_id}` 增量查询，按消息 ID 去重追加；WebSocket error/close 时进入 recovering 状态并触发一次补偿刷新，原有轮询继续作为最终降级路径。新增 `customer-agent-reconnecting` 状态标识，避免将短暂断线误显示为业务失败。
**验证**：前端构建通过；人工生命周期和 Agent WebSocket 既有五条 installed Chrome E2E 联跑通过；独立 Agent WebSocket 和人工生命周期回归通过。

### ISSUE-0050
- 日期：2026-08-12
- 分类：测试（NL2SQL 评估 / 测试基础设施）
**问题**：离线评估运行器若直接导入完整应用会受可选基础设施依赖阻断
最小评估脚本直接导入 `app.services.data_analysis_agent` 时，Python 会加载应用入口及 `pgvector` 等完整运行时依赖；在不具备完整环境的评估机器上，离线基准无法启动。
**解决**：首个离线基准将固定指标路由与 SQL-shaped 请求安全规则以镜像常量形式内置，并明确该运行器用于 P2 基线；真实 HTTP/数据库执行评估留待后续扩展；同时记录镜像常量未来可能漂移的风险，后续抽取不依赖 ORM/应用启动的共享 `nl2sql_policy` 纯模块，供服务和评估运行器共同引用。
**验证**：`python scripts/run_nl2sql_eval.py` 输出六维报告且安全事故数为 0。

### ISSUE-0051
- 日期：2026-08-12
- 分类：后端（NL2SQL / 评估 / 架构一致性）
**问题**：离线评估与服务安全规则镜像实现会产生漂移风险
P2 最小评估运行器最初复制了 SQL 形态识别和固定指标关键词，服务规则变更后评估可能无法代表真实行为。
**根因**：安全规则尚未从 ORM/应用依赖中分离为可独立导入的纯策略模块。
**解决**：新增 `app/services/nl2sql_policy.py`，集中只读模板校验、SQL-shaped 请求拒绝、指标关键词路由、权限角色判断及目录范围判断；DataAnalysisAgent 与离线评估运行器共同使用该模块，消除镜像漂移。
**验证**：8 条离线用例运行通过；共享策略单测覆盖 SQL 拒绝、指标路由、角色和目录范围判断。

### ISSUE-0048
- 日期：2026-08-12
- 分类：后端（NL2SQL / 缓存隔离 / 数据权限）
**问题**：并发 NL2SQL 请求下 Schema 缓存与权限上下文错乱
Schema 缓存 key 如果不绑定权限上下文，用户 A 可能命中用户 B 的缓存，从而看到 B 有权限而 A 无权限的数据表或字段；该问题不仅是 key 漏加 `user_id`，还说明权限模型此前没有覆盖所有中间状态存储层，目录搜索等权限敏感缓存同样存在串用风险。
**根因**：权限敏感缓存的 key 未纳入包含用户、角色、权限、租户、数据范围与目录策略版本的权限上下文，导致缓存可能在用户/权限上下文之间串用。
**解决**：新增 `PermissionContext` 与 `PermissionScopedCache` 抽象，缓存 key 使用权限上下文指纹且不暴露明文用户 ID/角色/权限，数据目录搜索缓存接入该抽象；`PermissionScopedCache.get_json` 失败统一返回 miss、`set_json` 失败返回 false，缓存 backend（Redis）故障时降级执行原始带权限过滤的检索，授权结果不依赖缓存健康状态；用户角色更新与角色权限更新提交后递增 Redis `wm:permission-context-version:{user_id}`，目录搜索异步读取该版本并入 PermissionContext 指纹实现缓存失效；补充真实 Redis/HTTP 并发隔离与多用户失效回归脚本。
**验证**：伪 Redis 故障测试确认读取不返回缓存值、写入不抛异常；真实 Redis/HTTP 回归通过（Redis 可达、4 个并发请求、2 个权限隔离 key、无明文权限，退出码 0）；角色更新后缓存上下文版本 1→2、目录缓存 key 2→3；临时 `risk_manager` 用户验证角色权限更新影响两个不同用户，版本分别 3→4 与 0→1。目录搜索缓存范围已 RESOLVED；Schema/Few-shot/枚举值缓存迁移作为后续治理规范扩展，不阻塞关闭。

# 段内 Bug 修复记录（ISSUES.md 6128–6469）

### ISSUE-20260829-RECOMMEND-PURCHASE-RENDERING
- 日期：2026-08-29
- 分类：前端 / Agent 路由
**问题**：复合"推荐并申购"请求的后端响应已包含推荐匹配结果，但前端 E2E 仍不渲染 agent 推荐卡片（agent-recommendations 未展示）。
**根因**：前端购买快捷方式绕过了统一 Agent，复合短语没有经过统一 Agent 路由处理。
**解决**：将复合短语路由到统一 Agent（composite phrases 统一走 unified Agent）。
**验证**：完整浏览器回归通过（full browser regression passed）。

### ISSUE-20260829-FUNCTION-CALLING-INTENT
- 日期：2026-08-29
- 分类：后端 / Agent 意图理解 / LLM 集成
**问题**：LLM 意图识别原用"固定 JSON 解析"（模型输出文本 → `json.loads`），脆弱且对不上 `StructuredIntent` 的 Literal 模板枚举/StructuredEntity 实体结构，导致大量 `error`（模型把原 query 当 template、实体直接当字符串）。
**根因**：固定 JSON 解析方式脆弱，模型自由输出与结构化 Schema 不匹配；后续实测还发现 Function Calling 的 `arguments` 并不保证合法 JSON（长 few-shot 提示词下偶发截断/畸形），"API 保证合法 JSON"的假设不成立。
**解决**：改为 Function Calling（`tools` + `tool_choice` 原生结构化输出）；`LLMIntentFallbackService` 增加模板容错（`canonical_template` 按意图派生）+ 实体字符串→`{value,certainty}` 容错 + `IntentNormalizer` 中文关键词映射。后续修正：加 `max_tokens=2048` + `_repair_json` + 加固重试 + LLM 解析失败走规则规划器兜底；certainty 数字/非法枚举归一化防 schema 崩溃。
**验证**：LLM 意图完全准确率 0% → 52-58%；规则路径仍 28%；安全澄清率 72-83%；后续修正后总体基线 60.9% → 68.8%。

### ISSUE-20260829-PROMPT-LENGTH-TRUNCATION
- 日期：2026-08-29
- 分类：后端 / LLM 意图解析
**问题**：LLM tool-call 输出被截断，导致意图解析首次成功率受限。
**根因**（实证）：意图解析 system prompt 含约 17 个冗长 few-shot JSON 示例，教模型输出冗长 tool-call `arguments`，撞上未文档化的工具调用参数内部缓冲上限（实测失败串在值中途戛然而止、长度仅约 370–680 字符，`max_tokens=2048` 仍截断）；`finish_reason` 恒为 `None`，无法用于截断检测。
**解决**：few-shot 改为 9 个紧凑短示例（只写 intent/task_type/template 等关键字段），schema 不变；并移除临时诊断探针代码。
**验证**：首次解析成功 92.2% → 100%（截断消灭）；总体基线 68.8% → 70.3%。

### ISSUE-20260829-MODEL-ROUTER-STRING-COMPARE
- 日期：2026-08-29
- 分类：后端 / 模型路由
**问题**：难度模型路由用字符串比较（`"M" <= "S"` 在字母序为 True），导致中等难度误路由到小模型 qwen-turbo。
**根因**：`resolve_model` 以字符串比较判断难度等级。
**解决**：改为按等级整数比较（S=1/M=2/L=3），`tests/test_model_router.py` 覆盖。
**验证**：多意图→qwen-plus、对抗→qwen-max 正确路由。

### ISSUE-20260829-FLYWHEEL-CONFIG-POLLUTION
- 日期：2026-08-29
- 分类：后端 / 测试隔离
**问题**：测试里改配置会污染其他测试。
**根因**：`UnderstandingConfig` 是共享单例，测试修改配置影响全局状态。
**解决**：`CustomerRagService` 支持注入独立 config；测试用 fixture/注入，不污染单例。

### ISSUE-20260829-RAG-RETRIEVAL-INCIDENT
- 日期：2026-08-29
- 分类：后端 / RAG 检索 / 数据库
**问题**：RAG 检索命中率 25%，适当性/风险类查询永远检不到，且无报错。
**根因**（四层二分定位）：
1. 中文全文检索用 PG `simple` 配置不切词 → 文本腿噪声拖垮 RRF → `search_hybrid` 只留 `ts_rank>0` 真命中；
2. 存储向量与查询向量 embedding 模型不兼容（同 1024 维不同模型，cosine≈0）→ 需重嵌入并加模型守卫（入库记 `embedding_model`、复用前校验）；
3. `document.status=failed` 静默排除整篇（有 12 个激活+向量 chunk 却被 excluded）→ 用 `fix_rag_knowledge_meta` 恢复 active。
**解决**：用 `scripts/reembed_rag_chunks.py` 重嵌入、`scripts/fix_rag_knowledge_meta.py` 修复文档状态、`debug_rag_*` 系列工具定位。
**验证**：检索命中率 25%→81.2%（重嵌入）；修复 status 后达 **100%**（fragment_recall 96.9%、evidence 100%）。

### ISSUE-20260831-RAG-HANDOFF-SIMILARITY-GATE
- 日期：2026-08-31
- 分类：后端 / RAG / 相似度门控
**问题**：RAG 拒答/转人工失效——无关查询（如"帮我查天气"）也返回 `rag_answer` + `evidence_status=sufficient`，相似度门控不触发。
**根因**：`CustomerRagService.answer` 用 `normalize_customer_query(query, intent)` 的膨胀后 query 做 embedding；对 company_info 会强制追加"公司名称/公司介绍/联系方式/客户数量"，把无关查询的向量相似度从 0.26 抬到 0.55+，导致 `min_similarity` 门控失效。
**解决**：门控/向量排序改用【原始 query】的相似度，膨胀 query 仅用于文本排序；配套：`min_similarity`（默认 0.55）进理解层配置、embedding 模型统一 `text-embedding-v4`（settings/.env.example/docker 一致）、结构感知切块（`chunking.mode=structure`，按标题/表格/段落切，size=400）并配置化、重入库/重嵌入脚本支持本地路径兜底且 metadata 记 `embedding_model`。
**验证**：`test_customer_rag_handoff_http` PASS（first_created/duplicate_reused 全 true）；`run_rag_eval` 100% hit / 94.8% recall；handoff 恢复、召回保持 100%。

### ISSUE-20260829-EMBEDDING-MODEL-GUARD
- 日期：2026-08-29
- 分类：后端 / RAG / embedding 模型管理
**问题**：RAG 命中率 25%，维度一致（1024=1024）、零报错，但相关查询检不到。
**根因**：embedding 模型线上悄然变化（配置名没变、上游模型/版本变了）→ 旧向量与新查询向量不在同一向量空间；同维度≠同模型，cosine≈0，向量检索退化成随机。指纹：维度匹配 + cosine≈0 + 相关查询检不到。
**解决**（模型守卫）：① 入库给 chunk 的 `metadata_json` 记录 `embedding_model`；② 增量入库复用旧向量前校验模型一致，不一致强制重嵌入（`app/repositories/knowledge.py` + `knowledge_ingestion.py`）；③ `scripts/reembed_rag_chunks.py --apply` 一键重嵌入。
**验证**：命中率 25%→81.2%（重嵌入）→ **100%**（修复 status 后）。

### ISSUE-20260901-MEMORY-PERSIST-SILENT-FAIL
- 日期：2026-09-01
- 分类：后端 / 记忆持久化 / 静默失败
**问题**：大规模 HTTP 冒烟发现 `risk_c5_demo` 说"我比较激进"后，`customer_memory` 里没有 `risk_preference`。
**根因**（两层）：
1. `execute_unified_agent` 里 DataAnalysisAgent 会先拦截部分 query（走 analysis/clarification 分支），未落到统一 agent `execute()`，导致偏好句不落库；
2. `customer_memory_event.conversation_id` 列是 `VARCHAR(36)`，带前缀的 conversation_id（如 `dbg-<uuid>`）超过 36 → `remember()` 抛 `StringDataRightTruncationError`，且被 `except: pass` 静默吞掉，偏好/活跃产品都不落库。
**解决**：① `app/api/customer_service.py` 在 `execute_unified_agent` 最前统一调用 `extract_preference_statement` + `remember` 持久化偏好，不受 Agent 路由影响；② `app/models/memory.py` 把 `CustomerMemoryEvent.conversation_id` 改为 `String(128)`，`app/db/schema.py` 加 `ALTER TABLE customer_memory_event ALTER COLUMN conversation_id TYPE VARCHAR(128)`。
**验证**：修复后 HTTP 冒烟 12/12 PASS，`risk_c5_demo` 偏好 + 推荐 `preference_note` 生效；全量 pytest 171 passed。

### ISSUE-20260901-RISK-ALERT-CLEARED-DEADLOCK
- 日期：2026-09-01
- 分类：前端 / 风险处置 / 死路
**问题**：风险预警处置里"评估后通过"无法推进解除风控（死路）。
**根因**：后端 `risk.py:213` 只要 `cleared_after_review` 就要求客户沟通+沟通记录+评估依据（无论处理状态），但前端 `RiskAlertsView.vue` 只在 `status==='resolved'` 才显示这些字段；而受限制预警不能直接结案（`risk.py:197`）→ 存不了"评估后通过"→ 无法解除。
**解决**：`RiskAlertsView.vue` 客户沟通字段显示条件改为 `cleared_after_review`（任意状态），submit 校验对齐；受限预警选"评估后通过"时显示引导（去客户管理详情页解除交易限制）。
**验证**：前端 `npm run build` 通过。

### ISSUE-20260902-NGINX-UPSTREAM-STALE-IP-502
- 日期：2026-09-02
- 分类：部署 / 前端 nginx / 502
**问题**：访问站点/登录返回 `502 Bad Gateway`。直接 `curl 127.0.0.1:8010/api/v1/health` 返回 200（API 健康），但走 frontend nginx 的 `curl 127.0.0.1/api/v1/health` 返回 502；frontend 日志反复 `connect() failed (111: Connection refused) while connecting to upstream ... http://172.19.0.9:8000`。
**根因**：`frontend/nginx.conf` 的 `location /api/ { proxy_pass http://api:8000; }` 在 nginx 启动时把 `api` 解析成当时容器 IP 并缓存；CI/CD 每次推送重建 api 容器换新 IP，而 frontend 容器未随之重建 → nginx 一直连旧 IP → Connection refused → 502。
**解决**：`frontend/nginx.conf` 改为运行时解析——`/api/` 内加 `resolver 127.0.0.11 valid=10s ipv6=off;` 并用变量 `set $api_upstream http://api:8000;` + `proxy_pass $api_upstream;`，借助 Docker 内嵌 DNS 每 10s 刷新 `api`，不再缓存旧 IP（commit `f64028d`）；立即恢复命令：`docker compose -f docker-compose.p0.yml up -d --force-recreate frontend`。
**验证**：`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1/api/v1/health` 由 502 → 200。

### ISSUE-20260914-LANGGRAPH-NOT-WIRED
- 日期：2026-09-14
- 分类：Agent / LangGraph / 接线
**问题**：`app/agents/graph.py` 的 LangGraph Supervisor 图已实现、可编译、有单测，但**没有任何 API 路由或 worker 引用它**（全仓 `from app.agents` 无业务命中，仅 `app/agents/__init__.py` 自引用），生产实际执行的是确定性管线 `CustomerAgentExecutionService.execute`。文档 `docs/系统接线现状与接线后预测.md` 的 N1/N2/N4 条目亦已过期（长期记忆其实已接线、`langgraph-checkpoint-postgres 3.1.2` 其实已安装）。
**根因**：图与管线**共享同一套业务 handler，但图缺少管线的 4 个持久化副作用**，直接切流会导致功能回归，因此当时只能"图与管线共存"：
1. `clarification_count` 不落 Redis → 跨轮澄清永远停在 1，第 2 轮转人工**永不触发**；
2. `handoff_node` 只返回话术，不建 `WorkOrder`/`CustomerServiceSession` → 前端拿不到 `ticket_no`；
3. 短时记忆不回写；
4. 情景记忆 `memory.summary_extract` 不入队。
另有一个隐蔽缺陷：`AgentState` **没有 `clarification` 字段**，而 LangGraph 会**静默丢弃**节点返回的未知键（不报错）→ 澄清话术从未进入 state，`persist_node` 无法判断 `missing_parameter`。同类问题：`react_step_node` 硬取 `plan.tasks[0]`，多意图时后续 task 被丢弃；而若改成"遍历但不置位 `react_result`"则条件边不收敛、**实测挂死**。
**解决**：三开关渐进式接线（默认全关，行为与接入前一致）：
- `AGENT_GRAPH_ENABLED`（图作权威）/ `AGENT_GRAPH_SHADOW_ENABLED`（影子双跑对照）/ `AGENT_CHECKPOINT_ENABLED`（Postgres 断点续跑，`thread_id=conversation_id`）；
- `graph.py` 新增 `persist_node` 补齐上述 4 个副作用，`handoff_node` 接 `CustomerRagHandoffService` 真实建单，`intent_plan_node` 补短/长/共享记忆装载与澄清补答拼接，`execute_node` 补 `conversation_summary` 注入；
- `AgentState` 补 `clarification` 等字段；`react_step_node` 改为按 `react_results` 长度推进多 task（`AgentState` 新增 `react_results`），`generate_node` 对每个命中 task 各生成一次；
- 新增 `app/services/graph_agent_runner.py`（state ⇄ 响应映射 + 影子对照，异常全部吞掉不穿透请求）与 `scripts/compare_graph_vs_pipeline.py`（切流前双跑验收）；
- `docker-compose.yml` 与 `docker-compose.p0.yml` **都**需透传开关（容器 environment 是白名单，只改一个会静默失效）。
**验证**：`tests 263 passed`（新增 30 条图接线测试）；容器内 `grep -c persist_node app/agents/graph.py` = 6；双跑对照 `FLUSHDB` 后由 7/14 → 11/14（剩余差异见 ISSUE-20260914-CLARIFICATION-COUNT-BLEED 与措辞差异）。注：影子/真图默认关，**生产仍未切流**，需跑完对照再开。

### ISSUE-20260914-CLARIFICATION-COUNT-BLEED
- 日期：2026-09-14
- 分类：Agent / 记忆 / 测试工具
**问题**：图版 vs 管线版双跑对照中，"什么是双录""现金管理保本计划在售吗""这个那个"等**无关**查询在图里被判成 `handoff`（直接转人工），而管线判成 `needs_clarification`，一致率仅 7/14。
**根因**：`clarification_count` 从 Redis 短时记忆读取（`graph.py` 与管线同逻辑），而对照脚本对两条路径使用**不同 conversation_id**（`-pipe`/`-graph` 后缀）→ 两条路径读到不同的 Redis key、计数状态不同步。更关键的是**同一路径跨 query 复用计数**：前一批查询把计数写脏后，后续任何澄清问句都会因 `clarification_count + 1 >= 2` 直接转人工，**跳过澄清**。属对照工具的设计缺陷，非路由逻辑错误。
**解决**：定位手段为对照前执行 `redis-cli FLUSHDB` 清空短时记忆。**根治已完成**：对照脚本现内置两层隔离——每条 query 前 `clear_short_term()` 清两条路径的 key、每次运行用带随机后缀的 `run_id`（见 ISSUE-20260914-COMPARE-SCRIPT-STATEFUL-RERUN），不再需要人工 FLUSHDB。
**验证**：`FLUSHDB` 后同一批 query 一致率 **7/14 → 11/14**，预测的 4 条全部从 `handoff` 翻回 `needs_clarification`，证实为计数污染而非路由错误。

### ISSUE-20260914-INTENT-FALLBACK-FACT-DOWNGRADE
- 日期：2026-09-14
- 分类：Agent / 意图识别 / 线上缺陷
**问题**：线上（`docker-compose.p0.yml` 默认 `LLM_INTENT_FALLBACK_ENABLED=true`）用户问"查一下我的持仓""我的工单进展如何""我的资产有多少"，被降级成 `faq` → 答复"当前知识库没有足够依据，建议转人工客服"并创建工单；而**真实持仓/工单数据就在库里**，正确路径 `asset_summary`/`workorder_status` 从未被执行。双跑对照中图给出正确数据（"总资产 302,361.52 元"）而管线给出转人工，即**图是对的、管线是坏的**。
**根因**：**不是阈值问题**——`IntentPolicyResolver.resolve` 的高置信保护（`confidence >= 0.9` → 返回 `rule_intent`）**确实生效**（实测 `asset_summary` 0.92、`workorder_status` 0.95、`greeting` 0.99 均返回规则结果）。问题在于保护结果**下一步被丢弃**：`_apply_structured_intent` 只读 `resolved.template`，不看原始意图，用 `primary_intent` 重建 plan；而 `_structured_from_rule_plan` 用映射表把 **16 种意图压成 4 个模板桶**（`asset_summary`/`workorder_status`/`risk_restriction`/`greeting` 全部落 `T1_INFORMATION_QUERY`），信息在映射那一步就丢失，之后无论怎么保护都无法恢复。链路：`asset_summary(0.92)` → `_structured_from_rule_plan` 压成 `T1` → resolver 保护（但 `primary_intent` 仍是 `asset_summary`）→ `_apply_structured_intent` 只看 `T1` → `safe_intent = "faq"` → 覆盖成功。此外该分支 `safe_intent = requested if requested in public_intents else "faq"` 的设计本身允许事实类意图落成 faq。
**解决**：不靠扩充映射表（16 意图 × N 模板不可持续，规划器每加意图就要同步改），改为**让原始意图穿越整个流程**：
1. `_structured_from_rule_plan` 保留 `primary_intent` 原始意图并带 `secondary_intents`；
2. `_apply_structured_intent` 增加 `if intent.source == "rule": return plan`——resolver 判定规则胜出时**原样返回 plan**，绝不用有损模板重建；
3. 新增业务事实类保护：`T1_INFORMATION_QUERY` 不得覆盖 `business_fact` 路由意图（`BUSINESS_FACT_INTENTS` = asset_summary / workorder_status / order_status / risk_restriction / product_status）。
三条性质同时满足：高置信事实类不再被降级；规则 `unknown` 时 LLM 兜底**仍生效**（推荐/转人工/信息查询）；`unknown` → 交易草稿仍被双重拦截（安全红线未破）。
**验证**：新增 10 个回归测试，覆盖"事实类不被降级""兜底仍生效""交易模板永不应用""primary_intent 保真"；撤销两处保护后 5 个测试确认失败（证明测试有效）。`tests 263 passed`。

### ISSUE-20260914-PSYCOPG-NO-LIBPQ-IN-CONTAINER
- 日期：2026-09-14
- 分类：依赖 / 容器 / 断点续跑
**问题**：容器内 `import langgraph.checkpoint.postgres` 抛 `ImportError: no pq wrapper available`（`couldn't import psycopg 'python' implementation: libpq library not found`），`AGENT_CHECKPOINT_ENABLED=true` 一旦开启即失败。
**根因**：`requirements.txt` 只声明裸 `psycopg>=3.3,<4`，而基础镜像 `python:3.12-slim` **没有 libpq**（`Dockerfile.api` 未 `apt-get install libpq-dev`）。宿主 conda 环境恰好有 libpq，本地 `import` 正常 → 典型"**本地过、容器挂**"，本机测试无法发现。
**解决**：`requirements.txt` 改为 `psycopg[binary]>=3.3,<4`（`psycopg-binary` 是自带 libpq 的预编译 wheel，无需 gcc/libpq-dev，不增加镜像构建复杂度）。同时把 `app/main.py` 的 checkpointer 初始化失败日志由 `warning` 提升为 **`error`**——开关开着却没生效属"静默降级"，warning 会让运维误以为断点续跑已启用。
**验证**：容器内 `python -c "import langgraph.checkpoint.postgres, psycopg_binary"` → `checkpoint-postgres OK`。

### ISSUE-20260914-SCRIPT-SYSPATH-IN-CONTAINER
- 日期：2026-09-14
- 分类：工具脚本 / 容器
**问题**：容器内执行 `docker compose exec api python scripts/compare_graph_vs_pipeline.py` 报 `ModuleNotFoundError: No module named 'app'`。
**根因**：`python scripts/x.py` 只会把 `scripts/` 放进 `sys.path`，仓库根目录不在其中；容器 `WORKDIR=/app` 且未设 `PYTHONPATH`，故 `import app` 失败。宿主靠 `$env:PYTHONPATH="."` 掩盖了该问题——与 ISSUE-20260914-PSYCOPG-NO-LIBPQ-IN-CONTAINER 同属"本地过、容器挂"。仓库既有 225 个脚本中仅 39 个做了 `sys.path` 处理，惯用写法是 `PYTHONPATH=. python scripts/x.py`。
**解决**：脚本内显式把仓库根目录插入 `sys.path`（`_ROOT = Path(__file__).resolve().parent.parent`），本地与容器内均可直接运行，无需再记 `PYTHONPATH`。
**验证**：本地 `python scripts/compare_graph_vs_pipeline.py --help` 在未设 `PYTHONPATH` 时 rc=0。

### ISSUE-20260914-COMPOSE-CONTAINER-NAME-VOLUME-CONFLICT
- 日期：2026-09-14
- 分类：部署 / Compose / 环境
**问题**：本地 `docker compose up -d --build` 报 `Conflict. The container name "/wealth-postgres" is already in use`，服务起不来（但镜像已构建成功）。
**根因**：`docker-compose.yml`（`name: wealth-manager`）与 `docker-compose.p0.yml`（`name: wealth-p0`）**硬编码了相同的 `container_name: wealth-postgres` / `wealth-redis`**，而 `container_name` 是**全局唯一**、不受 project name 隔离 → 两套 compose 互相抢名字。同时两者**卷名不同**（`docker-compose.yml` 用前缀默认卷 `wealth-manager_wealth_pgdata`；`docker-compose.p0.yml` 用 `name: wealth_pgdata` 固定卷名），因此混用时还可能连到**空卷**看到空库。此前本地曾混跑两套 compose，留下 `wealth-p0-*` 与 `wealth-postgres`/`wealth-redis` 残留容器（均已 Exited）。
**解决**：清掉残留容器后统一用 `docker compose -f docker-compose.p0.yml up -d`（数据在固定卷 `wealth_pgdata`，`docker inspect wealth-postgres` 确认挂载即此卷；删容器不丢数据）。**根治已完成**：`docker-compose.p0.yml` 的 `container_name` 已移除（见 ISSUE-20260914-COMPOSE-CONTAINER-NAME-FIXED），改用自动命名 `wealth-p0-postgres-1`，两套 compose 现可并存互不干扰。
**验证**：`docker compose -f docker-compose.p0.yml ps` 全部 11 个服务 Up，postgres/redis 均 healthy，API 8010 端口 health=ok。

### ISSUE-20260914-INTENT-TEMPLATE-LOSSY-CHANNEL
- 日期：2026-09-14
- 分类：Agent / 意图识别 / 架构
**问题**：承接 ISSUE-20260914-INTENT-FALLBACK-FACT-DOWNGRADE 的**根因分析**——为什么"加了 LLM 判断长尾，精准度反而更低"。实测发现：**即使 LLM 判对了意图，结果一样会被毁掉**。修复前同一条链路对 `LLM primary_intent=asset_summary` 的输出是 `faq`。
**根因**：三层叠加的有损链路。
1. **输出带宽瓶颈**：LLM 输出的 `template` 只有 **11 个值**（T0–T10），而系统有 **16 种意图**。`asset_summary`/`workorder_status`/`risk_restriction`/`greeting`/`company_info` **5 种完全不同的业务意图全部塌缩成同一个 `T1_INFORMATION_QUERY`**。模型区分得再准，输出时也被压扁。
2. **下游拿模板当身份用**：`_apply_structured_intent` 只看 `template` 分支；T1 分支把 `primary_intent` 塞进只允许 6 个公开知识意图的 `public_intents` 白名单，**不在里面就 `else "faq"` 强制降级**。`asset_summary` 不在白名单 → 变 `faq`。**这是"静默降级"：把未知当已知处理，不报错、不澄清。**
3. **few-shot 在教模型犯错**：统计 system prompt 的 39 个 few-shot，template 分布为 `T1`×10、`T6`×8、`T2`×5、`T4`×5、`T5`×4、`T8`×4、`T3`×2、`T7`×1；其中多个示例**明确示范 `{"primary_intent":"asset_summary","template":"T1_INFORMATION_QUERY"}`**（如"我不赎回，就查下收益""我的钱还能提出来不"）——前门教模型"事实类意图配 T1"，后门看到 T1 就按信息查询处理。
**完整链路**：长尾 query（"查一下我的持仓"）→ 规则识别正确（asset_summary, 0.92）→ **LLM 也识别正确**（asset_summary）→ 但输出压成 `T1` → 下游按 T1 走信息查询分支、白名单不认 asset_summary → 强制改写 `faq` → 用户得到"资料不足，建议转人工"，而数据就在库里。**规则能答对、LLM 也能判对，是中间的模板翻译层把答案毁了。** 这解释了为何关掉 LLM 兜底一致率反而上升（4/14 → 7/14）：关掉即绕开这条有损链路，规则的真实意图得以直达执行。
**解决**：已修（见 ISSUE-20260914-INTENT-FALLBACK-FACT-DOWNGRADE / INTENT-T1-MULTI-INTENT-COLLAPSE）：`source=="rule"` 时原样返回 plan 绕开模板回写；事实类意图禁止被 T1 覆盖。**根因未除，待办三项**：① 让 `primary_intent` 成为一等公民、`template` 仅作辅助提示，不再用 template 决定分支；② 改 prompt few-shot，事实类意图不再示范配 `T1`（需重新评测意图识别准确率）；③ **去掉 `public_intents` 白名单的 `else "faq"` 默认分支**——改为保留原意图或要求澄清，消除静默降级（纯代码改动、风险最低，建议优先）。
**验证**：实测 `LLM primary_intent=asset_summary` + `template=T1_INFORMATION_QUERY` → 修复前输出 `faq`、修复后输出 `asset_summary`。

### ISSUE-20260914-INTENT-T1-MULTI-INTENT-COLLAPSE
- 日期：2026-09-14
- 分类：Agent / 意图识别 / 线上缺陷
**问题**：双跑对照（`LLM_INTENT_FALLBACK_ENABLED=true`）中"你们公司的主营业务是什么"：planner 产出 `['company_info','company_business']` **2 个 task**，而管线实际 `task_intents=['company_info']` **只剩 1 个**，用户少拿一路答案。
**根因**：`_apply_structured_intent` 的 `T1_INFORMATION_QUERY` 分支**重建 plan 且只保留 `primary_intent`**（`return IntentPlan(..., tasks=[task])`），多意图 task 被直接丢弃。实测该 query 原始 2 个 task 经此分支后变成 `['faq']`（不仅丢一个，连 `company_info` 也没了）。与 ISSUE-20260914-INTENT-FALLBACK-FACT-DOWNGRADE 同源：**用有损的模板桶回写 plan**。
**解决**：T1 分支增加保全条件——当规则已识别多个只读知识 task 且全部属于 `public_intents` 时**原样返回 plan**，不重建。单 task 的 unknown 查询仍可被兜底成 `faq`（保住模型兜底能力）。
**验证**：`原始 ['company_info','company_business'] → 应用后 ['company_info','company_business']`。新增回归测试 `test_multi_knowledge_intent_not_collapsed_by_t1_fallback` 与 `test_t1_fallback_still_applies_to_single_unknown_query`；`tests 267 passed`。

### ISSUE-20260914-REACT-SEEN-SIGNATURE-CROSS-TASK
- 日期：2026-09-14
- 分类：Agent / LangGraph / 多意图
**问题**：图侧只读 ReAct 循环的早停哈希 `seen_signatures` **跨 task 共用**，而多意图 task 常共用同一句 query（"公司业务"同时命中 `company_info` + `company_business`，两个 task 的 `query` 文本完全相同）→ `_result_signature` 相同 → 第二个 task 立刻被判 `repeated=True`（"结果重复、无进展"）而**提前早停**。
**根因**：早停机制（`hashlib.sha256` 对 citations+evidence_status+answer 做规范化哈希，重复即判定进入局部最优）是为**单 task 内多轮改写**设计的，未考虑多 task 场景；`seen_signatures` 作为扁平列表在 task 之间共享，语义上把"换个意图再检索"误判成"同一轮无进展"。
**解决**：早停哈希**按 task 隔离**——`AgentState` 新增 `seen_signatures_by_task: dict`（`{task_index: [sig,...]}`），`react_step_node` 用当前 task index 取独立集合判重；`state.seen_signatures` 保留扁平列表供审计。
**验证**：新增回归测试 `test_same_query_across_tasks_does_not_false_early_stop`（断言两 task 均 succeeded、`overall_status=completed`、`needs_handoff=False`）与 `test_per_task_signatures_tracked_separately`（断言 `seen_signatures_by_task` 键为 `{"0","1"}`）。
**说明**：本条是排查 `partially_completed` 差异时的**预防性修复**——真实观测到的那次 `partially_completed` 主因是 `company_business` 检索确实弱（`low_confidence`），非哈希误判；但共用哈希确为隐患，一并修掉。

### ISSUE-20260914-COMPLETED-STATUS-MASKED-BY-DROPPED-TASK
- 日期：2026-09-14
- 分类：Agent / 可观测性 / 状态机
**问题**：双跑对照中"你们公司的主营业务是什么"出现**反直觉现象**：管线报 `completed`，图报 `partially_completed`，看起来图更差。实际相反——管线是**因为丢了一个 task** 才显得"全都成功"。
**根因**：`CustomerTaskExecutor.summarize` 的判定依赖 task 数量——`succeeded == len(results)` → `completed`。管线被 ISSUE-20260914-INTENT-T1-MULTI-INTENT-COLLAPSE 丢掉 `company_business` 后只剩 1 个 task 且成功，于是得到 `completed`；图保留 2 个 task，其中 `company_business` 检索弱（`low_confidence`）→ `partially_completed`。**即"少做一件事"反而让状态更好看，`overall_status` 无法区分"做完了"与"少做了"。**
**验证**：实测 `summarize([succeeded, needs_clarification]) = partially_completed`（管线自身的函数）；修复多意图丢 task 后两边输入一致，输出必然一致。
**影响与建议**：若监控/告警依赖 `overall_status`，存在**掩盖真实退化**的风险——丢 task 的批次会伪装成完全成功。建议补一个独立指标（如 `planned_task_count` vs `executed_task_count`）用于比对，或让 `summarize` 感知"计划 task 数"而非仅看 results 长度。**本条为观察记录，尚未实施改动。**

### ISSUE-20260914-GRAPH-MISSING-PREFERENCE-CHAIN
- 日期：2026-09-14
- 分类：Agent / LangGraph / 功能等价性
**问题**：梳理"确定性管线 vs LangGraph 图"差异时发现，**图缺少管线的两条偏好链路**，切流会造成功能退化（非等价替换）：
1. **偏好持久化**：管线用 `extract_preference_statement()` 从用户自述抽取偏好并 `remember()` 写长时记忆（`customer_agent_execution.py:307-313`）；图**无此调用**（`graph.py` 中 `preference` 仅出现在 `LONG_TERM_READ_KEYS` 读取白名单，无写入路径）。
2. **纯偏好短路**：管线对"只自述偏好、未要求推荐/购买"的消息走 `pure_preference_statement()`，先落记忆并回复确认、不直接推产品清单（`customer_agent_execution.py:327-337`）；图无此分支。
**根因**：接线补副作用时补齐了澄清计数、转人工建单、短时记忆回写、情景记忆入队共 4 项，**遗漏了偏好链路这 2 项**。
**影响**：用户说"我偏好稳健"时，图不会写入长时记忆 → 后续会话/推荐无法复用该偏好；同一句话两条路径答复策略也不同（管线先确认、图直接推荐）。**因此"图优于管线"仅成立于意图识别维度**，完整看是"意图识别更稳、偏好链路缺失"。
**解决**：**已完成**。在 `intent_plan_node` 补齐两条链路：
1. **偏好持久化**：`extract_preference_statement()` 抽取自述偏好 → `CustomerMemoryService.remember()` 写长时记忆 + `SharedMemoryService.remember(scope="shared", memory_type="observation")` 供投顾/推荐等其它 Agent 复用。与管线一致地加 `state.is_customer` 守卫——**staff/advisor 代客分析不得短路、不得把员工自述当客户偏好落记忆**。
2. **纯偏好短路**：复用 `pure_preference_statement()`（与管线**同一个函数**，保证判定等价），命中时置 `preference_short_circuit=True` 并由新增条件边 `intent_plan_node → persist_node` **跳过 route/execute**（不执行任何任务、不推产品清单）；`persist_node` 对短路分支单独写短时记忆——**`clarification_count` 归零**、清空 `pending_intent_plan`/`handoff`，避免把"确认偏好"误记成一次失败澄清而累积到转人工。
   新增 `AgentState.preference_short_circuit` / `preference_recorded` 字段。
**踩坑记录**：① 初版把 shared 写入与私有写入放在**同一个 try** 内，私有失败不会连带跳过 shared，但契约失败会——已拆成两个 try；② 初版短路条件漏了 `is_customer` 守卫，导致员工代客分析被误短路（回归测试抓到后修复）。
**验证**：新增 6 个回归测试（偏好落库、短路不执行、计数归零、带请求标记不短路、shared 观察写入、员工不短路不落客户记忆）；用"撤销 is_customer 守卫"确认测试会失败。`tests 273 passed`。**注**：`pure_preference_statement` 的 `比较` 标记会误命中"我比较保守"（"比较"当"比较产品"）——属**既有**问题且图如实继承，保证等价，不在本次范围。
**验证**：`grep -c "pure_preference_statement\|extract_preference_statement" app/agents/graph.py` → 0；管线对应命中见上述行号。

### ISSUE-20260914-COMPARE-SCRIPT-STATEFUL-RERUN
- 日期：2026-09-14
- 分类：工具脚本 / 测试可信度
**问题**：`scripts/compare_graph_vs_pipeline.py` 的 `--prefix` 默认值固定为 `"dualrun"`，导致**每次运行复用同一批 `conversation_id`**（`dualrun-7-graph` 等）→ 上一轮的短时记忆（转人工工单号、`clarification_count`、`active_product`）被下一轮继承。**观测证据**：相隔多轮、中间重建过镜像的两次运行，转人工工单号完全相同（`WO202609140445419AB4A3`）——正常应每次新生成。此前靠人工 `redis-cli FLUSHDB` 掩盖了该问题。
**根因**：脚本设计时只考虑了"同一轮内两条路径要隔离"（分别用 `-pipe`/`-graph` 后缀），**未考虑"跨轮次运行之间也要隔离"**。`FLUSHDB` 是外部手工补救而非脚本保证，一旦忘记执行，对照结果就不可复现、且可能把残留状态误判成编排差异（与 ISSUE-20260914-CLARIFICATION-COUNT-BLEED 同源，但那条只暴露了同一轮内跨 query 的污染，本条暴露的是跨运行污染）。
**解决**：脚本内建两层隔离——① 每条 query 执行前调 `CustomerMemoryService.clear_short_term()` 清掉 `-pipe`/`-graph` 两个 key，保证两条路径初始状态一致（免手动 FLUSHDB）；② 每次运行生成 `run_id = f"{prefix}-{uuid4().hex[:8]}"`，彻底避免跨运行残留。
**验证**：`python scripts/compare_graph_vs_pipeline.py --help` 在未设 `PYTHONPATH` 下 rc=0；`tests 267 passed`；`docker compose config` 两份均通过。

### ISSUE-20260914-INTENT-FALLBACK-DEFAULT-LAYERING
- 日期：2026-09-14
- 分类：文档 / 配置 / 认知偏差
**问题**：多份文档（`答辩面试实现细节手册.md`、`_draft_agents.md`、`面试速记卡.md`、`面试问答-与叶绍康相关.md`、`系统接线现状与接线后预测.md` 等 6 份）均表述"**LLM 回退默认关闭**"，读者会据此认为线上是纯规则路径。**实际线上是开启的**——排查 ISSUE-20260914-INTENT-FALLBACK-FACT-DOWNGRADE 时正是被这一表述误导，一度把生产降级缺陷当成"只有 shadow 模式、不影响执行"而未优先处理。
**根因**：**默认值分三层，文档只写了其中一层**：
| 层 | 值 |
|---|---|
| `app/core/settings.py` 代码默认 | `False` |
| `docker-compose.p0.yml:69`（生产/CI 部署，实际生效） | `${LLM_INTENT_FALLBACK_ENABLED:-true}` → **开启** |
| 本地 `.env` | 视配置 |

文档只引用 `settings.py` 的 `False`，**漏了 compose 的覆盖**。而 `.github/workflows/ci-cd.yml` 部署走的正是 `docker-compose.p0.yml`，所以"代码默认"与"线上实际"不一致。
**解决**：6 份文档统一改为区分三层的准确表述（"代码默认关 / p0 compose 生产默认开"），并在 `系统接线现状与接线后预测.md` 的 N10 条目补记该开关开启后暴露的降级缺陷链接。`docs/` 按 `.gitignore` 不入库，故为本地修正。
**验证**：`Select-String docs/*.md -Pattern "默认关闭"` 逐条复核，确认剩余命中均为已修正的三层说明或与 LLM 回退无关（如图的三开关、断点续跑）。
**通用教训**：**排查"某能力是否生效"时，必须追到部署清单（compose）的最终值，不能只看代码默认值**——本项目 `docker-compose.yml` 与 `docker-compose.p0.yml` 的 environment 是白名单且带 `${VAR:-default}` 覆盖，代码默认与线上实际可以完全不同（同类问题亦见于三开关是否透传，见 ISSUE-20260914-LANGGRAPH-NOT-WIRED）。

### ISSUE-20260914-COMPOSE-CONTAINER-NAME-FIXED
- 日期：2026-09-14
- 分类：部署 / Compose
**问题**：落实 ISSUE-20260914-COMPOSE-CONTAINER-NAME-VOLUME-CONFLICT 中记录的"根治方向（未做）"——去掉 `docker-compose.p0.yml` 的 `container_name`，使本地与生产两套 compose 可并存，不再互相抢 `wealth-postgres`/`wealth-redis` 名字。
**根因**：`container_name` 全局唯一、不受 `name:`（project）隔离，而 `docker-compose.yml`（project `wealth-manager`）与 `docker-compose.p0.yml`（project `wealth-p0`）**硬编码了同一组容器名**；同时两者卷名不同（前者用前缀默认卷 `wealth-manager_wealth_pgdata`，后者用 `name: wealth_pgdata` 固定卷），混用时既冲突又可能连到空卷。
**解决**：删除 `docker-compose.p0.yml` 中 postgres/redis 的 `container_name`，交由 Compose 自动命名 `wealth-p0-postgres-1`/`wealth-p0-redis-1`——与 CI 已在使用的 `wealth-p0-api-1` 命名风格一致。**卷不受影响**：`volumes: wealth_pgdata: name: wealth_pgdata` 是显式固定名，与容器名无关，数据保留。
**风险核对**：确认 `.github/workflows/ci-cd.yml` 仅引用 `wealth-p0-api-1`（第 85 行 `docker logs`）与端口健康检查（`curl 127.0.0.1:8010/api/v1/health`），**不依赖 postgres/redis 容器名**，故改动不影响 CD。
**升级注意**：已部署环境执行 `up -d` 后 postgres/redis 会换新容器名，旧容器变为孤儿；建议 `docker rm -f wealth-postgres wealth-redis` 清理，**数据在固定卷 `wealth_pgdata` 中不受影响**。
**验证**：`docker compose -f docker-compose.p0.yml config --quiet` 通过；改动后 `container_name` 仅存在于注释中。

### ISSUE-20260914-PURE-PREFERENCE-COMPARE-FALSE-POSITIVE
- 日期：2026-09-14
- 分类：Agent / 意图识别 / 既有缺陷（未修）
- 严重程度：P2
- 状态：OPEN（已定位，未修；需独立评估后再动）
**问题**：`pure_preference_statement()` 的请求标记正则 `_PURE_PREF_REQUEST_MARKERS` 含裸词 `比较`，导致**程度副词"比较"被误判成"要比较产品"**，使纯偏好自述无法短路。实测：

| 输入 | 抽取偏好 | `pure` | 期望 |
|---|---|---|---|
| `我比较保守` | `('risk_preference','conservative')` | **False** ❌ | True |
| `我比较稳健` | `('risk_preference','balanced')` | **False** ❌ | True |
| `我比较喜欢稳健的` | `('risk_preference','balanced')` | **False** ❌ | True |
| `我偏好稳健型` | `('risk_preference','balanced')` | True ✅ | True |

正则全文：`(推荐|申购|购买|买入|下单|适合|想要买|给我推|帮我推|推荐什么|有哪些产品|看看产品|筛选|比较|搭配|组合)`——`比较` 本意是"比较产品"（筛选意图），但中文里"我比较X"是**最常见的程度副词用法**，误命中率高。
**根因**：单字/双字裸词匹配未考虑中文语法歧义。"比较"作为动词（对比产品）与副词（程度上）同形，纯关键词匹配无法区分；且**该 regex 只做"是否含请求词"的判断，不含位置/词性守卫**。
**影响**：**两条路径同样受影响**（管线与图都调这个函数）。
- 用户说"我比较保守"时**不会短路**，直接进入规划器 → 大概率落 `unknown` 或误判 → 用户得到澄清追问，而系统其实已正确抽取到偏好。
- 这是**常见的自然表达**，不是长尾：中文用户自述风险偏好时"我比较保守/我比较稳健"极为常见。
- 数据飞轮角度：这类 query 会持续产生"偏好已识别却仍追问"的坏体验，且容易在评测集里被漏掉（因意图本身抽取正确，错在短路判定）。
**解法方向（未实施）**：
1. **最小改动**：把裸 `比较` 换成更具体的动词搭配，如 `比较.{0,4}(产品|理财|基金|方案)` 或直接移除（筛选意图已有 `筛选`/`有哪些产品` 等更明确标记覆盖）。
2. **加词性守卫**：`比较` 后若紧跟程度形容词（保守/稳健/激进/平衡）则不算请求标记。
3. 需回归评测：移除后要确认"帮我比较一下这几个产品"这类**真筛选**请求仍能正常进入推荐流程。
**为何本次未修**：本任务目标是"图与管线等价"（ISSUE-20260914-GRAPH-MISSING-PREFERENCE-CHAIN），修此正则会让两条路径**行为同时改变**，属于独立议题，且需评测集验证。图当前**如实继承**该行为，恰好保证等价性可验证。
**验证**：`extract_preference_statement` + `pure_preference_statement` 直接调用复现（见上表）。

### ISSUE-20260914-AMBIGUOUS-JUDGE-NOT-WIRED
- 日期：2026-09-14
- 分类：RAG / 配置与实现脱节
- 严重程度：P1
**问题**：`config/understanding.yaml` 声明 `judge_ambiguous: true`（中间带用 LLM-judge 判证据充分性），但 `customer_tools.py` 构造 `CustomerRagService` 时**未传 `ambiguous_judge`**，judge 从未被调用。容器内实测确认：`ambiguous_judge = None`。
**根因**：**配置声明与实现接线脱节**。`CustomerRagService.__init__` 的 `ambiguous_judge=None` 是默认值，`answer()` 内的判断是 `if thresholds.get("judge_ambiguous") and self.ambiguous_judge is not None and ...`——**两个条件都要满足**，而配置为 `true`、对象为 `None`，故整个分支静默跳过，**降级为死阈值**。现成的 `build_answerability_judge()`（`customer_rag_answer.py:23`，用 qwen-max + temperature=0）**存在但无任何调用方**。
**实际影响**：分层证据门控退化为单阈值。配置 `reject=0.35 / answer=0.65 / min_similarity=0.55`，中间带 `0.35~0.65` 本应由 judge 判定，实际走 `best_sim >= 0.55 ? answer : reject`。例如 `best_sim=0.53` 本可经 judge 判为可作答，却因低于 0.55 被硬拒转人工。**配置里写的能力从未生效。**
**解决**：`customer_tools.py` 检索知识工具分支接上 judge——复用同一个 `QwenProvider`（避免重复构造），`CustomerRagService(retriever, embedding_provider=provider, ambiguous_judge=build_answerability_judge(provider))`。judge 内部异常按"不可作答"处理（保守拒答），保持原有安全取向。
**验证**：新增 2 个回归测试——`test_customer_read_tools_wires_ambiguous_judge`（用 Spy 捕获构造参数，断言 `ambiguous_judge is not None`）+ `test_judge_ambiguous_config_is_true_so_wiring_matters`（钉住配置前提）。**用"撤销接线"确认首个测试会失败**。`tests 275 passed`。
**通用教训**：`decide_evidence` 这类纯函数单测全绿，**完全无法发现"接线缺失"**——能力已实现、配置已声明、单测通过，但调用方没传参。同类问题本项目已出现多次（ISSUE-20260914-INTENT-FALLBACK-DEFAULT-LAYERING 的 compose 覆盖、ISSUE-20260914-LANGGRAPH-NOT-WIRED 的三开关未透传）。**共性：配置/文档层与实际生效层之间没有校验**。建议后续为关键开关增加启动期自检（如 `judge_ambiguous=true` 但 `ambiguous_judge is None` 时打 warning）。

### ISSUE-20260914-RAG-EVIDENCE-THRESHOLD-MARGIN
- 日期：2026-09-14
- 分类：RAG / 可复现性
- 严重程度：P2
- 状态：OPEN（已定位，未修；需设计决策）
**问题**：双跑对照中 `你们公司的主营业务是什么` 两次运行结论不同（一次 `partially_completed`+转人工、一次 `completed`），同一代码同一 query 不可复现。容器内实测定位到根因：**该 query 的 `best_sim` 落在阈值边缘**。

```
原始 query  -> embedding_degraded  best_sim=None
改写后 query -> sufficient         best_sim=0.5667
min_similarity 阈值 = 0.55
余量 = 0.0167  ← 仅 1.67%
```
**根因**：图的 ReAct 循环含 `_REWRITE_POOL` 改写重试（`tool_calls=2` 即证据）。改写后文本的向量相似度**恰好落在 0.55 上方 1.67%**，检索结果的微小波动（RRF 排序微变、多召回/少召回一条）即可让 `best_sim` 跌破阈值 → `low_confidence` → 该 task `needs_clarification` → `summarize` 判 `partially_completed` + `requires_handoff=True`。而**同一轮中另一 task 已成功生成答案**，于是出现"既给出详细答案、又转人工"的表象。
**重要澄清（推翻一版误判）**：`embedding_degraded`（`best_sim=None`）**不会**导致拒答——`decide_evidence` 对 `best_sim is None` 明确 `return "answer"`（注释："没有相似度信号，不按相似度拒"）。故 embedding 降级与本次不一致**无因果**，`low_confidence` 只能来自 `answer()` 中 `if not useful`（检索 0 条或全低于 `min_score=0.0001`）。**初始推断曾把两者关联，经实测证伪。**
**解法方向（未实施，需设计决策）**：
1. 阈值加滞回/置信带（如 `0.50~0.60` 视为不确定，走 judge 或降级为"部分作答"而非直接拒）；
2. RAG 判定改为**确定性可复现**（固定 embedding、固定 top_k、记录 `best_sim` 到审计以便事后归因）；
3. **修正 `needs_handoff` 判定粒度**：单个 task 失败即整体转人工过于激进——已有可用答案时应返回"部分作答 + 提示"，而非同时给答案又转人工（自相矛盾的用户体验）。

> **更新（同日）**：抖动根因已定位为**检索层排序不确定**（三处 `order_by`/`sort` 无 tie-breaker），已修复并单列 ISSUE-20260914-RAG-RETRIEVAL-NONDETERMINISTIC-SORT。上述第 2 条（确定性）已落地；第 1、3 条仍开放。
> 另注：本条中"阈值余量仅 1.67%"是**脆弱性**，与"排序不确定"是**两个独立因素**——前者是设计余量不足，后者是工程缺陷。修复后者不解决前者。
**验证**：容器内调用 `CustomerRagService.answer` 对原文/改写文本分别取 `best_sim`（见上）；`ambiguous_judge=None` 与 `thresholds` 亦同轮实测。

### ISSUE-20260914-SUMMARIZE-DUPLICATE-FILLER
- 日期：2026-09-14
- 分类：Agent / 答案聚合 / 体验
- 严重程度：P3
- 状态：OPEN（已定位，未修）
**问题**：多意图多 task 同时低置信失败时，`CustomerTaskExecutor.summarize` 把每个 task 的占位答案**原样拼接**，用户看到重复文案。实测复现（`你们公司的主营业务是什么`，两个 task 均 `needs_clarification`）：

```
answer = '当前资料不足。\n当前资料不足。\n下一步：当前资料不足，可以补充问题或转人工客服。'
         ^^^^^^^^^^^^^^ 重复两次
```
**根因**：`summarize` 的聚合是无条件拼接——
```python
lines = [item.answer for item in results if item.answer]
if clarification: lines.append(f"下一步：{clarification}")
return status, clarification, "\n".join(lines) or "当前没有可返回的结果。"
```
每个低置信 task 的 `answer` 都是同一句兜底文案 `"当前资料不足。"`（`react_step_node` / `_bounded_react_knowledge` 的固定占位），多 task 时即出现 N 次重复。**未做去重，也未区分"实体答案"与"占位文案"。**
**影响**：纯体验瑕疵，不改变 `overall_status`/`requires_handoff` 判定，**两条路径同受影响**（共用 `summarize`，故不是图与管线的差异项）。但在"多意图 + 全部低置信"这一常见场景下用户观感明显变差。
**解法方向（未实施）**：① 聚合时按 answer 去重；② 占位文案不参与拼接（仅在无任何实体答案时统一给一次）；③ 引入 `TaskExecutionResult.is_placeholder` 标记以区分实体/兜底。
**关联**：与 ISSUE-20260914-COMPLETED-STATUS-MASKED-BY-DROPPED-TASK 同族——**`summarize` 对多 task 的聚合语义过于粗糙**：既会让"丢 task"伪装成 `completed`，也会让占位文案重复拼接。建议一并重构聚合层。
**验证**：直接调用 `CustomerTaskExecutor.summarize` 传入两个 `needs_clarification` + 相同占位 answer 的 task，复现上述重复串。

### ISSUE-20260914-REACT-BUDGET-AND-PLACEHOLDER-DIVERGENCE
- 日期：2026-09-14
- 分类：Agent / 图与管线行为差异 / 可观测性
- 严重程度：P3
- 状态：OPEN（已定位，未修）
**背景**：为定位 `你们公司的主营业务是什么` 的路径差异，新增 task 级明细探针 `scripts/inspect_query_dual.py`（原 `compare_graph_vs_pipeline.py` 只比对聚合字段，看不到"哪个 task 失败、为什么失败"）。探针输出**推翻了此前的假设**。
**被推翻的假设**：曾推测"图丢掉了 `company_info` 已成功的答案、只有 `needs_handoff` 粒度过粗"。
**实测事实**（同一 query、同一次运行）：

| | pipeline | graph |
|---|---|---|
| task 集合与状态 | `company_info=needs_clarification`、`company_business=needs_clarification` | **完全相同** |
| tool_calls | t1=2、t2=2 | t1=**4**、t2=**5** |
| answer 占位文案 | `当前知识库没有足够依据，建议转人工客服或创建工单。`（`CustomerRagService` 的 handoff 文案）| `当前资料不足。`（`react_step_node` 硬编码占位）|

即：**task 级完全一致，两个 task 都失败**，图**没有**丢答案。此前"图丢答案"的推断**错误**，已证伪。差异只剩两处**表面差异**：
1. **ReAct 工具预算不同**：管线 `CustomerTaskExecutor.MAX_REACT_ITERATIONS=20`，图 `max_iterations=4`（`AGENT_GRAPH_MAX_ITERATIONS`）。图重试更多次（4/5 vs 2/2）——注意图的计数含跨 task 累计展示，但上限确实更低。**同一 query 下两条路径的检索尝试次数不同**，属编排参数未对齐。
2. **兜底占位文案不同源**：管线直接用 RAG 的 handoff message，图用自己硬编码的 `"当前资料不足。"`。同一语义两种文案，导致 `answer` 字段字符串比对必然 DIFF，且用户体验不一致（管线文案更有指导性）。
**另一项重要观察（不确定性仍在）**：该 query 在**对照运行**中是 `pipeline=partially_completed`，在**本次探针**中是 `pipeline=needs_clarification`——**同一 query、同一代码、同一容器，pipeline 结论两次不同**。印证 ISSUE-20260914-RAG-EVIDENCE-THRESHOLD-MARGIN 的边缘抖动（`best_sim≈0.5667` vs 阈值 `0.55`）：抖动发生在**管线侧**，说明问题**不限于图**，是 RAG 判定层本身不可复现。
**解法方向（未实施）**：
1. 对齐 ReAct 预算：图 `max_iterations` 与管线 `MAX_REACT_ITERATIONS` 取同一配置源，否则"图 vs 管线"对照本身不公平；
2. 统一兜底占位文案：图复用 RAG 的 handoff message，避免同义不同文；
3. 优先级最高的仍是 RAG 阈值抖动（见上条 ISSUE）——它让**任何**双跑对照都无法稳定复现，是评估可信度的根因。
**价值**：本条的探针脚本是排查此类差异的**必要基础设施**——没有 task 级可见性，就会像本次一样凭聚合字段做出错误推断。

### ISSUE-20260914-RAG-RETRIEVAL-NONDETERMINISTIC-SORT
- 日期：2026-09-14
- 分类：RAG / 检索层 / 可复现性
- 严重程度：P1
**问题**：ISSUE-20260914-RAG-EVIDENCE-THRESHOLD-MARGIN 中"同一 query 两次结论不同"的**根因定位**——不是 embedding、不是 LLM，而是**检索层的排序不确定**。
**排查过程（逐步排除）**：
1. **排除 embedding**：同一文本连续 4 次 `QwenProvider.embed()`，SHA256 **完全一致**（逐元素相等）→ embedding 确定性无问题。
2. **排除 top_k 动态性**：`get_top_k_for_query()` 按 `_ENUM_MARKERS` 与难度分档取值，对同一 query 是**确定性**的。
3. **定位到排序**：`app/repositories/knowledge.py` 三处排序**均无 tie-breaker**——
   - `_rank_text_tsvector`：`.order_by(text_rank.desc())`
   - 向量腿：`.order_by(vector_distance.asc())`
   - RRF 融合：`fused.sort(key=lambda item: item[1], reverse=True)`

**为什么会导致 best_sim 抖动**：RRF 分数**大量并列**（代码注释自陈"相关/无关都约 0.016"）。并列时顺序取决于 dict 插入顺序 → 来自 DB 返回顺序 → 而 SQL **无第二排序键**时，PostgreSQL 对等值行的返回顺序**不保证稳定**（受执行计划、并行度、页布局影响）。`fused[:top_k]` 截断后进入 `useful` 的 chunk 集合因此可能不同 → `best_sim = max(vector_similarity)` 随之变化 → 在 `min_similarity=0.55` 边缘翻转 `succeeded`/`low_confidence`。实测该 query `best_sim=0.5667`，**距阈值仅 0.0167**，极易翻转。
**解决**：三处排序补齐确定性 tie-breaker（统一用 `KnowledgeChunk.id` 升序）：
```python
.order_by(text_rank.desc(), KnowledgeChunk.id.asc())
.order_by(vector_distance.asc(), KnowledgeChunk.id.asc())
fused.sort(key=lambda item: (-item[1], str(item[0].id)))
```
**影响面**：这是**两条路径共用的检索层**，故修复后管线与图的判定同时变得可复现——解释了为何此前观察到抖动发生在**管线侧**而非图侧。
**验证**：`tests 279 passed`；新增 `scripts/check_rag_determinism.py`——对同一 query 连续检索 N 次，比对 `evidence_status`/`best_sim`/命中集合顺序的签名哈希，全一致则判定可复现（退出码 0）。**该脚本需在容器/有 DB 环境执行**，本地无法验证（本次仅完成静态修复 + 单测通过）。
**残留风险**：tie-breaker 只保证**顺序确定**，不消除 `best_sim` 本身贴近阈值的脆弱性——`0.5667 vs 0.55` 的余量问题仍需通过滞回带或 judge 策略处理（见 ISSUE-20260914-RAG-EVIDENCE-THRESHOLD-MARGIN）。

### ISSUE-20260914-MODEL-DRIVEN-READONLY-TOOL-SELECTION
- 日期：2026-09-14
- 分类：Agent / 能力增强（需求方明确要求）
- 严重程度：P1（能力缺口）
**问题（此前现状）**：系统自称"ReAct"，但**工具选择是硬编码的**，模型不参与——
- `graph.py:365`：`react_tool.execute(name="search_customer_knowledge", ...)` 工具名写死；
- `customer_task_executor.py:19`：`TOOL_BY_INTENT` 是"意图→工具"静态字典；
- 改写用 `_REWRITE_POOL` 两条固定模板硬试（实测同一 query 6 次调用里 4 次是重复无效改写）。
即："会循环"但**不会自主选工具、不会自主改写、不会判断该不该续调**。属"确定性编排 + 有界重试"，**不是 Agent 自主调度**。
**需求**：**只读查询**交由模型自主选择工具调用；**资金/风控保持确定性路径**（这条边界不变）。
**方案**：新增 `app/services/customer_model_tools.py`，复用既有 `QwenProvider.request_tool_call`（`tool_choice="auto"`，`qwen.py:379`，此前仅投顾侧用过）：
- `CUSTOMER_READ_TOOL_SCHEMAS`：8 个**只读**工具的 OpenAI function schema，含面向模型的 description（模型据此选择）；
- `CustomerModelToolLoop.run()`：有界循环——模型输出 `{name, arguments}` → 执行 → 结果回灌 → 再问，直到模型不再调用或用尽 `max_rounds`；
- 图中新增 `model_tool_node`，经 `_route_after_plan` 优先分流；拿到结果 → `summarize_node`，未拿到 → **回退确定性 `react_step_node`**（双保险，不空转）。
**五项安全边界（硬约束，非提示词约束）**：
1. **写操作进不来**：工具集只含只读查询；`_ALLOWED_TOOL_NAMES` 硬白名单二次校验，模型即使返回 `create_order` 也只得到 `tool_not_allowed` 且**不触达工具层**；
2. **不能越权指定客户**：`customer_id` 由服务端注入，**忽略**模型参数中的 `customer_id`（SubjectScope 仍由工具内部强制）；
3. **有界循环**：`max_rounds` 上限（默认 4），用尽即 `budget_exhausted`；
4. **降级安全**：provider 不可用/模型不调工具 → 返回 `stopped_reason` 而非异常，由图回退确定性路径；
5. **全程审计**：每轮落 `AuditLog(action="customer_agent.tool_call")`，含 `selection="model"`、`read_only=true`。
**开关**：`AGENT_MODEL_TOOL_SELECTION_ENABLED`（默认 false → 行为与之前完全一致）、`AGENT_MODEL_TOOL_MAX_ROUNDS`；已透传 `docker-compose.yml` / `docker-compose.p0.yml` / `.env.example`。
**验证**：新增 `tests/test_customer_model_tools.py` 共 20 个测试，重点覆盖安全边界——写操作被拒、越权参数被忽略、预算用尽、模型不可用降级、每轮审计、schema 只读性与白名单一致性。**用"撤销白名单守卫 + 撤销服务端注入"确认测试会失败**。`tests 299 passed`。
**已知取舍（需观察）**：`advisor_ask_agent.py:8` 记录了历史教训——多轮 `request_tool_call` 会让点名产品句**白等数百毫秒到数秒**。本方案以 `max_rounds=4` + "事实类工具一次成功即结束"缓解，但**延迟增加是固有代价**，建议开启后实测 P95。

### ISSUE-20260914-MODEL-TOOL-FORCED-EARLY-STOP
- 日期：2026-09-14
- 分类：Agent / 模型工具调度 / 多意图
- 严重程度：P1
**问题**：模型自主工具调度上线后实测（`inspect_query_dual.py --query "我有没有风险限制"`）发现**图比管线少一个 task**：
```
pipeline task: [('risk_restriction','succeeded'), ('risk_public','succeeded')]   ← 2 个
graph    task: [('risk_restriction','succeeded')]                              ← 1 个
```
规则规划器明明产出 2 个 task（`risk_restriction` 事实 + `risk_public` 知识解释），但图只得到事实那一半，**用户拿不到风险制度的解释**。
**根因（两个叠加的设计缺陷，均为本次新引入）**：
1. **强制早停**：`CustomerModelToolLoop.run()` 中写了"事实类工具（客户数据）拿到结果即可结束；知识检索允许继续补充"→ `if tool_name != "search_customer_knowledge": break`。模型只调了事实工具就被强制 `break`，**没有机会调第二个工具**。这个"优化"是我为控制延迟加的，但它**剥夺了模型的自主决策权**——恰好违背了本次改造的初衷（把"该不该续调"交给模型）。
2. **绕过 plan**：`model_tool_node` 把**整个 query** 丢给模型自由选工具，**没有把规则规划出的意图列表作为提示传入**，模型不知道"这个问题还有第二个方面要覆盖"。
**解决**：
1. **移除强制早停**：拿到结果后**一律把结果回灌给模型**，由模型自己决定是否继续；只有模型不再调用（`no_tool_call`）或用尽预算才结束。同时把 system prompt 第 5 条改为明确要求"**多意图问题要逐个覆盖**（如'我有没有风险限制'既需事实限制也需制度解释）"。
2. **传入 planned_intents**：`run()` 新增 `planned_intents` 参数，`model_tool_node` 从 `_rebuild_plan(state.plan)` 取 `[t.intent for t in plan.tasks]` 传入，作为"需覆盖哪些方面"的**提示**（非硬约束，模型仍自主决定调什么）；每轮回灌后再追问"上述方面是否都已覆盖"。
3. **顺带修意图标注**：知识类工具的结果 `intent` 此前固定取工具名（`search_customer_knowledge`），导致多意图语义丢失；改为优先取模型传入的 `intent` 参数（`company_info`/`policy`/`risk_public`…）。
**验证**：`tests/test_customer_model_tools.py` 新增/修正 3 个测试——多意图可连续覆盖两类工具且两个 task 都保留、planned_intents 确实作为提示传入、事实结果必须回灌（断言 `len(provider.calls)==2`）。`tests 301 passed`。
**教训**：为"省延迟"而替模型做的每一步决定，都要问一句"这是不是我该替它决定的？"。本次的 `break` 表面上省了一次 LLM 往返，代价却是**功能性缺失**（用户少拿一半答案），且因为 `overall_status` 仍是 `completed`，**监控完全看不出来**——与 ISSUE-20260914-COMPLETED-STATUS-MASKED-BY-DROPPED-TASK 同族。

### ISSUE-20260914-MODEL-EARLY-STOP-NEEDS-RULE-BACKSTOP
- 日期：2026-09-14
- 分类：Agent / 模型工具调度 / 提示词不可靠
- 严重程度：P1
**问题**：修复 ISSUE-20260914-MODEL-TOOL-FORCED-EARLY-STOP（移除强制 `break` + 传入 `planned_intents` + 提示词要求"多意图逐个覆盖"）**后重新实测，问题依然存在**：
```
pipeline task: [('risk_restriction','succeeded'), ('risk_public','needs_clarification')]  ← 2 个
graph    task: [('risk_restriction','succeeded')]                                        ← 仍只有 1 个
graph stopped: 模型拿到事实工具结果后主动返回 None（不是被强制 break）
```
即：**不是代码在拦，是模型自己决定收手**。提示词已明确要求覆盖多意图、也把 `planned_intents` 传了进去，模型仍提前停止。
**根因**：**提示词约束不可靠**。`request_tool_call` 的 `tool_choice="auto"` 意味着"不调工具"始终是模型的合法选项；一旦它认为"已有事实足以回答"，就会结束，无论提示词怎么强调要覆盖多个方面。**"要求模型做某事"≠"保证模型做某事"**——这与本项目一贯的"能用接口/规则保证，就别只靠提示词"原则一致。
**解决**：把"是否覆盖完整"从**提示词层**上移到**规则层**，新增覆盖兜底：
- 新增 `_INTENT_TO_TOOL` 映射（意图 → 覆盖它所需的工具）与 `_uncovered_intents(planned, results)`：按已成功结果里的 `intent`/`data.tool` 比对，算出尚未覆盖的计划意图；
- 模型返回 None 时**先判覆盖**：若仍有未覆盖意图且预算未用尽，则把"【系统提示】以下方面尚未取得依据，请调用相应工具补齐：X、Y"追加进上下文并 `continue`，**强制多给一轮**；
- 预算用尽仍有缺口时，`stopped_reason="intent_uncovered"`（**显式标记，避免静默**，便于监控发现"模型没收手但也没覆盖全"的情形）。
- 知识类意图（company_info/company_business/policy/risk_public/...）共用 `search_customer_knowledge`，故按"该工具是否被成功调用过"判定覆盖，**不重复追问**；`greeting`/`human_handoff` 等非只读意图不参与兜底（避免无谓 LLM 轮）。
**验证**：新增 6 个测试——模型提前收手被兜底追问至覆盖完整、兜底消息确实含未覆盖意图名、预算用尽报 `intent_uncovered`、已覆盖时不额外追问（省一轮调用）、非只读意图不触发兜底、同一工具覆盖多个知识意图不重复追问。**用"撤销兜底条件"确认前两个测试会失败**。`tests 307 passed`。
**设计取舍**：这是"**模型自主决策 + 规则兜底校验**"的分层——模型仍可自由选工具、自由改写（这是本次改造的目标），但**"计划里的方面是否都拿到了依据"由规则校验并在缺失时强制补齐**。既保住灵活性，又不把完整性寄托在模型自觉上。

### ISSUE-20260914-PROMPT-BACKSTOP-INEFFECTIVE-NEED-DETERMINISTIC-FILL
- 日期：2026-09-14
- 分类：Agent / 模型工具调度 / 提示词边界
- 严重程度：P1
**问题**：上一提交加入"覆盖兜底"后**重新实测，图仍只有 1 个 task**。本地逐步复现后确认：**兜底逻辑确实被触发了**（模型被追问，共产生 4 次 `request_tool_call`），但**模型连续多轮仍坚持返回 `None`**，最终 `stopped_reason="intent_uncovered"`、只有 `risk_restriction` 一个结果。即：**把"以下方面尚未取得依据，请调用相应工具补齐：risk_public"明确写进对话后，模型依然不调第二个工具。**
**根因**：**提示词不具备强制力**。`tool_choice="auto"` 语义下"不调工具"是模型的合法选择；当模型已判定"事实足以回答"时，再多轮追问也不改变其判断。这与上一轮"模型提前收手"是**同一根因的第二层表现**——第一次以为是代码在拦（已修），第二次以为是提示词不够强（**判断错误**），实际是**提示词本身无法承担强制作用**。
**解决**：把兜底从"**再问模型一次**"改为"**规则层确定性补齐**"——新增 `_fill_uncovered()`，当模型停止且仍有未覆盖意图时，**直接按 `_INTENT_TO_TOOL` 映射执行缺失的工具**，不再征求模型同意：
- 补齐调用同样走 `_ALLOWED_TOOL_NAMES` 硬白名单（内部路径不绕过安全边界）；
- 补齐结果标记 `execution_mode="rule_backstop"`、审计 `filled_by="rule_backstop"`，**与模型自主调用的结果可区分**，便于观测"模型选了哪些、规则补了哪些"；
- 主路径不受影响：模型仍自主决定调什么工具、怎么改写、是否继续；规则只在**模型已经停止**的收尾阶段补漏。
**边界澄清（重要）**：这不是"用规则取代模型"，而是**职责分离**——模型负责"主路径怎么查"（选工具/改写/排序，保持自主），规则负责"别漏掉计划里的方面"（完整性）。提示词适合表达意图与偏好，**不适合承担正确性/完整性保证**（已同步写入 `docs/DECISIONS.md` 的架构决策）。
**验证**：更新/新增测试——模型被追问后仍不配合时规则补齐依然生效、补齐结果标记 `rule_backstop`、补齐审计含 `filled_by` 标记、`_INTENT_TO_TOOL` 映射全部落在白名单内。`tests 309 passed`。
**教训（两次踩坑的完整链条）**：① 第一版：代码 `break` 强制早停 → 移除；② 第二版：提示词追问 → **实测无效，是判断错误**；③ 第三版：规则确定性补齐 → 生效。**在"模型是否配合"这件事上，任何依赖提示词强度的方案都应默认不可靠，直接上规则校验。**

### ISSUE-20260914-JUDGE-REJECTS-BORDERLINE-EVIDENCE
- 日期：2026-09-14
- 分类：RAG / 分层证据门控 / 接线上线后的真实影响
- 严重程度：P1
**问题**："我有没有风险限制" 的 `risk_public` 子意图，图（`model_tool_selection`）连续 3 轮调用 `search_customer_knowledge` **全部 `success=False`**，最终 `stopped_reason=budget_exhausted`、只有一个 task；而管线（`bounded_react`）第 2 次即成功。**同样的问题、同样的知识库，两条路径结论相反。**
**排查过程（这次终于用对了方法：先打真实中间值，再改代码）**：
1. 新增 `scripts/diagnose_model_tool_loop.py` 打印真实执行中间值，**直接推翻此前假设**——模型并非"不肯调第二个工具"，而是**主动调了 3 次且全部失败**。此前三轮"修复"（移除 break / 提示词追问 / 规则补齐）**修的都是不存在的问题**。
2. 新增 `scripts/compare_rewrite_effectiveness.py` 对 5 种改写风格做检索对照，并以 `ambiguous_judge` 开关为**唯一变量**做两组实验，得到决定性结果：

| 改写风格 | judge=False（仅阈值） | judge=True（生产路径） | 结论 |
|---|---|---|---|
| 原文（无改写） | `low_confidence` best_sim=**None** n=0 | 同 | **原文检索 0 命中，必须改写** |
| 管线固定改写 | `sufficient` 0.5610 | `sufficient` 0.5610 | 一致 |
| 模型改写·场景化 | `low_confidence` None n=0 | 同 | **场景化改写检索不到** |
| **模型改写·问句式** | **`sufficient` 0.5559** | **`low_confidence` None** | **★ 仅因 judge 而翻转** |
| 泛化术语 | `sufficient` 0.6349 | `sufficient` 0.6347 | 一致 |

**根因（两条叠加）**：
1. **原文与场景化改写检索不到**：`"我有没有风险限制"` 对 `risk_public` 是 **0 命中**，必须依赖改写；而模型的"场景化改写"（如 `客户存在待处理风险记录时的含义及后续处理流程`）**偏离知识库的制度条文表述**，同样 0 命中。容器里模型第 1 次改写正是这一风格 → 失败。
2. **judge 误杀边缘 evidence（关键）**：模型第 2 次改用问句式（`0.5559`），**无 judge 时 `sufficient`、有 judge 时 `low_confidence`**——因为 `0.5559` 落在 ambiguous 带（`0.35~0.65`），按配置交给 LLM-judge 裁决，judge 判 `no` 即 reject。即 **`ISSUE-20260914-AMBIGUOUS-JUDGE-NOT-WIRED` 的接线修复，改变了一线召回行为**：judge 让部分"相关但相似度低"的引用被拒。
**影响面**：judge 已在生产生效（`AGENT_*` 未开也一样，因 `customer_tools.py` 是共享检索路径），**所有相似度落在 0.35~0.65 的检索都会多一道 LLM 裁决**。这既提高准确性（拒绝无关引用），也降低召回（可能误杀相关但表述不同的引用）。
**待定的取舍（需业务判断）**：judge 提示词目前是"**引用足以回答**才 yes"，标准偏严；可考虑改为"**引用是否包含可用于回答的依据**"（放宽），或对 0.55~0.65 的高分区段不启用 judge（仅对 0.35~0.55 低分区启用），或保持现状但接受这部分召回损失。**由 `scripts/diagnose_ambiguous_judge.py` 打印 judge 原始回答与引用内容后判定。**
**关键教训（比 bug 本身更重要）**：
- 我连续 4 轮判断错误，共同原因是**用"本地模拟我认为的场景"代替"读取真实运行的中间值"**。直到写了诊断脚本打印真实值，才一轮定位到"检索 0 命中"。
- **修 bug 前必须先在生产/容器内观测到该 bug 的中间值**；没有观测就动手，等于在修想象中的问题。
- 即使拿到正确数据，我仍犯了一次推理错误：把"场景化改写失败"**泛化成"模型改写都不行"**，而问句式实际成功了。**不能从单个样本泛化。**

### ISSUE-20260914-JUDGE-MISJUDGES-RELEVANT-EVIDENCE
- 日期：2026-09-14
- 分类：RAG / LLM-judge / 判定质量
- 严重程度：P1
**问题**：`ISSUE-20260914-JUDGE-REJECTS-BORDERLINE-EVIDENCE` 定位到"judge 翻转了 `best_sim=0.5559` 的判定"后，需判定 judge 是**合理拒绝**还是**误杀**。新增 `scripts/diagnose_ambiguous_judge.py` 打印 judge 原始回答 + 实际引用内容，结论：**误杀**。
**实测证据**（问题为"我有没有风险限制"，intent=`risk_public`）：

| 改写风格 | best_sim | judge 原始回答 | 引用实际内容 |
|---|---|---|---|
| 管线固定改写 | 0.5610 | **yes** | 风险测评评分区间/负债题 |
| 模型·问句式 | 0.5558 | **no** | ①`C4投资者购买R5产品需签风险揭示书，单只不超总资产10%；机构客户特别豁免…` ②`发生以下情形须提前风险重新评估：购买超出当前风险等级产品；大额资金变动…` |
| 泛化术语 | 0.6347 | **yes** | ①`…须提前风险重新评估…`（**与上行引用②几乎相同内容**）②`# 个人投资者适当性管理指南 文件编号…` |

**根因（两层）**：
1. **judge 判定标准过严**：提示词要求"引用**足以回答**才 yes"。而 `judge(query, contexts)` 的 `query` 传入的是**检索用的改写文本**（`customer_rag_service.py` 第 168 行），该文本可能已被模型收窄成某个子问题（如"是否影响交易权限"）；引用却是按**用户原始意图**召回的。judge 严格按字面判"这不是该问题的答案" → `no`。**用户真实意图是"我有没有风险限制"，这些引用确实是依据。**
2. **判定不一致**：同一批"须提前风险重新评估"的内容，在问句式那次判 `no`、在泛化术语那次判 `yes`——**说明 judge 对改写措辞高度敏感，属不稳定判定**，不适合无条件介入整个 ambiguous 带。
**解决（两处，均可回滚）**：
1. **放宽 judge 提示词**（`customer_rag_answer.py`）：判定标准从"引用**足以回答**"改为"**引用是否包含与问题相关的制度依据**"，并明确"检索问题可能只是用户疑虑的某个侧面，只要引用对理解该主题有帮助就算相关""宁可从宽：信息不完整但方向相关 → yes"；仅当引用与该问题**完全无关**时才 `no`。
2. **收窄 judge 介入区间**（`config/understanding.yaml` 新增 `judge_reject_similarity` / `judge_answer_similarity`，默认值等于原 `reject`/`answer` 以保持向后兼容；本次配置为 `0.35`/`0.55`）：judge 只管**低分区 0.35~0.55**（拦截明显无关引用），`0.55~0.65` 直接放行。**这样 `0.5559` 不再经过 judge，恢复 `sufficient`。**
**验证**：新增 3 个测试——judge 上界必须窄于 answer 上界、高分区（0.5559）**不调用 judge** 且不转人工、低分区（0.45）仍调用 judge 并可拒答；**用"把 judge 带还原为完整 ambiguous 带"确认前两个会失败**。`tests 312 passed`。
**影响**：judge 仍在生产生效（`customer_tools.py` 共享检索路径），但介入面收窄——保留"拦截明显无关"能力，去掉"误杀边缘相关"副作用。**若后续发现低分区误杀，可继续调 `judge_answer_similarity` 或回退 `judge_ambiguous: false`。**

### ISSUE-20260914-MODEL-TOOL-SKIPS-ANSWER-GENERATION
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 答案生成
- 严重程度：P1
**问题**：judge 修复后 `risk_public` 终于检索成功，但**图给出的答案是占位串**：
```
graph  task-2 answer = '可基于客服知识库引用生成回答。'      ← 占位，非真实答案
pipeline task-2 answer = '根据您提出的问题…所有投资者均受法定和制度性风险限制约束…'  ← 真实生成
```
用户会看到"可基于客服知识库引用生成回答。"这种**系统内部提示语**，而非实际答案。
**根因**：图的边把 `model_tool_node` **直连 `summarize_node`**，**跳过了 `generate_node`**：
```python
g.add_conditional_edges("model_tool_node", self._route_after_model_tool, {
    "summarize_node": "summarize_node", "react_step_node": "react_step_node"})   # ← 缺 generate_node
```
`search_customer_knowledge` 返回的是 `CustomerRagService` 的 `message` 字段（"可基于客服知识库引用生成回答。"），**真正的 LLM 答案生成在 `CustomerRagAnswerService.generate`**（管线 `_bounded_react_knowledge` 第 144 行有调用）。模型工具轮没有接这一步，于是把占位串当成了最终答案。
**解决**：`_route_after_model_tool` 改为返回 `generate_node`，图边也改为 `model_tool_node → generate_node → summarize_node`（与 `react_done_node → generate_node` 一致）。`generate_node` 已支持多 task（此前已修），对每个命中引用的结果各生成一次。
**验证**：`model_tool_node` 出边确认为 `('model_tool_node','generate_node')`；全量 `tests 316 passed`。**容器内需再跑 `inspect_query_dual` 确认 answer 不再是占位串。**

### ISSUE-20260914-MODEL-TOOL-DUPLICATE-INTENT-RESULT
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 结果去重
- 严重程度：P2
**问题**：图出现**两个同 intent 的 task**：
```
[graph] task=model-tool-2 intent=risk_public   answer='可基于客服知识库引用生成回答。'
[graph] task=model-tool-3 intent=risk_public   answer='可基于客服知识库引用生成回答。'
判定: graph = [risk_restriction, risk_public, risk_public]   ← 比 pipeline 多一个
```
**根因**：知识类工具成功后结果**回灌**模型（这是为多意图覆盖所做的正确设计，见 `MODEL-TOOL-FORCED-EARLY-STOP`），但模型会因此认为"还需补充"而**再调一次同 intent、不同 query 的检索**。而结果收集处**按轮次追加**、未按 intent 去重，于是同一意图产出两个 task；下游 `summarize` 拼接出重复答案。
**解决**：结果收集时按 `intent` 去重——若该 intent 已有成功结果，则**只更新已有条目**（答案取最新、data 合并），不追加新条目；`outcome.rounds` 仍保留每轮记账以供审计（调用次数不被抹掉）。
**验证**：新增 4 个测试——同 intent 重复成功不追加（`rounds` 仍为 2 以保留审计）、去重时答案取最新、不同 intent 不被误合并、事实类工具同样去重。`tests 316 passed`。

### ISSUE-20260914-MODEL-TOOL-MISROUTES-NON-RAG-INTENTS
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 工具语义边界
- 严重程度：P1
**问题**：全量对照由 12/14 退化到 8/14，其中 `推荐适合我的产品` 图判 `needs_clarification` 而管线 `completed`：
```
graph answer = '产品推荐使用实时 Product 数据和适当性服务，不依赖当前缺失的产品 RAG 文档。'
```
这句是 `CustomerRagService.answer()` 对 `intent == "product_recommendation"` 的**短路返回**（`customer_rag_service.py:116`），**并非产品推荐结果**。
**根因**：**工具语义边界被模型跨越**。`search_customer_knowledge` 的 schema 允许模型传任意 `intent` 参数（枚举里含 `product_recommendation`？实际枚举为 company_info/company_business/faq/workflow/policy/risk_public，但模型仍可能传其他值或系统未拦截），而**产品推荐在管线里由 `SuitabilityService`（实时 Product + 适当性）处理，根本不走 RAG**。我的模型工具轮把"选工具"完全交给模型，却**没有约束"哪些意图不能由该工具处理"**，于是模型用知识检索工具去答推荐类问题，拿到短路占位串当作答案。
**同类风险**：`greeting`（"你好"）、`human_handoff`（"我想转人工客服"）、`business_operation` 也都有各自专用路径，不应交给 `search_customer_knowledge`。实测对照中这三条也确实出现差异。
**解法方向（未实施）**：① 在 `search_customer_knowledge` 的 schema/enum 中**硬限制 intent 取值**只允许知识类（company_info/company_business/faq/workflow/policy/risk_public），并在工具执行层校验（与 `_ALLOWED_TOOL_NAMES` 同级的硬门禁）；② 对**非只读知识意图**（product_recommendation/greeting/human_handoff/business_operation）**不进入模型工具轮**，直接走原有确定性分支（`_route_after_plan` 增加意图级前置判断）；③ 或在工具返回 `answer_mode == "database_recommendation"` 等"非 RAG 短路"时拒绝作为答案采纳。**倾向 ①+②**：既封闭越界可能，又保住"模型自主选工具"的初衷（模型只在**知识类与事实类只读**工具间自主选择）。

### ISSUE-20260914-MODEL-TOOL-LLM-CALL-AMPLIFICATION
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 配额与延迟
- 严重程度：P1
**问题**：全量对照出现 `llm.usage model=qwen-plus status=failed duration_ms=95760`（**单次调用耗时 95.7 秒后失败**），且 `你好` 一条图返回 `'当前回答服务暂时不可用，已保留检索依据并建议转人工客服。'`（`generation_status=generation_failed`，见 `customer_rag_answer.py:95-96`）。**即 LLM 调用被拖垮/限流。**
**根因**：**模型工具轮显著放大单请求的 LLM 调用次数**：
```
graph 路径（"我有没有风险限制"，2 个 task）：
  1 意图分类(LLMIntentFallback) + 2~4 模型工具轮 + N 次答案生成  → 4~6 次
pipeline 路径：
  1 意图分类 + (可选 RAG judge) + N 次答案生成                    → 2~3 次
```
全量对照 = 14 条 × 2 路径 ≈ **28 个请求**，叠加后触发限流/超时；`request_tool_call` 未显式加超时（`qwen.py` 的 `chat_completions` 走 httpx 客户端超时），失败后又降级为占位文案，**使得对照结果不可信**（既有真实差异，也有资源耗尽的假差异）。
**影响**：① 对照/评测不可复现（资源耗尽引入假 DIFF）；② **生产风险**：`--workers 4` 下并发用户会把 LLM 配额打满，`advisor_ask_agent.py:8` 记录的历史延迟问题会重现且更严重。
**解法方向（未实施，需实测决定）**：① **限流/并发保护**：`request_tool_call` 加显式超时 + 全局信号量（参考 `llm_intent_fallback_max_concurrency` 的既有做法）；② **减少轮数**：明确"事实类工具成功后如无未覆盖意图即结束"，避免为"补充"而多调一轮（当前已按 intent 去重，但仍会多调一次）；③ **只在必要时启用模型工具轮**：如仅规则低置信/多意图时使用，简单 query 走确定性路径（可显著降低调用量）；④ **对照脚本限速**：跑对照时加间隔或降并发，避免自造限流。
**验证**：以 `inspect_query_dual`/对照时的 `llm.usage` 日志统计单条 query 的 LLM 调用次数与耗时（`status=failed` 出现即资源不足）。

### ISSUE-20260914-LLM-SDK-RETRY-AMPLIFIES-TIMEOUT
- 日期：2026-09-14
- 分类：LLM 基础设施 / 超时与重试
- 严重程度：P1
**问题**：`ISSUE-20260914-MODEL-TOOL-LLM-CALL-AMPLIFICATION` 的根因之一被定位——实测 `llm.usage model=qwen-plus status=failed duration_ms=95760`（**95.7 秒**），远超配置的 `qwen_chat_timeout_seconds=30`。
**根因**：**openai SDK 默认 `max_retries=2`**，而 `chat_completions` 传入的 30s 超时是**单次尝试**的超时，SDK 会自动重试 → `30s × (1 + 2) = 90s+`，与观测到的 95.7s 吻合（差额为重试间隔与连接耗时）。即：**配置写了 30s，实际最坏 90s+**，调用方以为"有超时保护"实则没有。
**解决（三项保护一起上）**：
1. **显式限制 SDK 重试**：`AsyncOpenAI(..., max_retries=settings.qwen_max_retries)`，默认 `1`（新增配置项）；最坏延迟降为 `30s × 2 = 60s`。
2. **工具轮显式更短超时**：`request_tool_call` 传入 `qwen_tool_call_timeout_seconds`（默认 `15s`，新增配置项），此前它**未传 timeout**，会落到 httpx 的 120s 默认值。工具轮是辅助决策且一请求多轮，必须比普通对话更短。
3. **进程级并发闸门**：`QwenProvider._semaphore()` 按 `qwen_max_concurrency`（默认 `8`）缓存 `asyncio.Semaphore`，在 `chat_completions` 内 `async with sem` 包裹真实调用，限制在途 LLM 调用数；配置 `<=0` 表示不限流。做法与既有 `CustomerAgentExecutionService._fallback_semaphores` 一致。
**顺带修复**：加信号量时引入 `asyncio` 但**忘记 import**——新测试立刻以 `NameError` 暴露（若进生产会在**首次 LLM 调用**即崩溃）。已补 `import asyncio`。**这是"写测试"直接拦下一个生产级崩溃的实例。**
**验证**：新增 `tests/test_llm_resource_guard.py` 共 8 个测试，钉住：SDK 重试被显式限制且 client 实际采用、信号量按 limit 复用同一实例、信号量确实限制并发峰值（8 任务并发上限 2）、`chat_completions` 源码确实经过信号量、工具轮超时短于普通对话、`request_tool_call` 显式传 timeout、**最坏延迟有界**（`超时 × (1+重试)` 普通 ≤60s、工具轮 ≤30s）。**用"还原重试/超时/信号量"确认 4 个测试会失败**。`tests 324 passed`。
**影响**：对照/评测结果重新可信（不再混入资源耗尽的假 DIFF）；生产 `--workers 4` 下的配额打满风险受控。配置已透传 `docker-compose.yml` / `docker-compose.p0.yml`（`QWEN_MAX_RETRIES` / `QWEN_MAX_CONCURRENCY` / `QWEN_TOOL_CALL_TIMEOUT_SECONDS`）。**注意：`MODEL-TOOL-MISROUTES-NON-RAG-INTENTS` 与调用量放大本身（④只在必要时启用）仍未解决。**

### ISSUE-20260914-MODEL-TOOL-INTENT-GATE-IMPLEMENTED
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 意图级门禁
- 严重程度：P1
**问题**：落实 `ISSUE-20260914-MODEL-TOOL-MISROUTES-NON-RAG-INTENTS` 的修复方案 ①+②。此前 `_route_after_plan` 只判断"开关是否开启"，**对所有意图**都放行到 `model_tool_node`，于是：
- `你好`（greeting）→ 模型调 `search_customer_knowledge` → `CustomerRagService` 返回 greeting 短路文案 → 再经 `generate_node` 让 LLM "基于该文案生成" → **生成失败时用户看到"当前回答服务暂时不可用，已保留检索依据并建议转人工客服。"**（对照中 `你好` 的 graph 侧正是此文案）
- `推荐适合我的产品`（product_recommendation）→ RAG 短路返回 `"产品推荐使用实时 Product 数据和适当性服务，不依赖当前缺失的产品 RAG 文档。"` 被当成推荐答案；而**真正的推荐应由 `SuitabilityService`（实时 Product + 适当性）完成**
- `我想转人工客服`（human_handoff）→ 本应直接转人工，却先做了一轮知识检索并生成一段"未找到转人工路径"的长文
**根因**：模型工具轮**缺少意图级门禁**——把"模型自主选工具"错误地扩大到了**所有**查询，包括那些**已有专用确定性路径**的意图。工具语义边界未被约束。
**解决（两层门禁）**：
1. **路由层**（`graph.py`）：新增 `MODEL_TOOL_INTENTS` 白名单（只含只读查询类：`asset_summary`/`order_status`/`workorder_status`/`risk_restriction`/`product_status` + 知识类 `company_info`/`company_business`/`faq`/`workflow`/`policy`/`risk_public`）与 `_model_tool_applicable(plan)`；`_route_after_plan` 改为**必须同时满足"开关开启"且"意图适用"**才进模型工具轮，否则回退确定性分支（`react_step_node`/`execute_node`）。`product_recommendation`/`greeting`/`human_handoff`/`business_operation`/`unknown` 一律排除。
2. **工具层**（`customer_model_tools.py`）：新增 `_KNOWLEDGE_INTENTS` 白名单，`search_customer_knowledge` 收到**非知识类 intent** 时强制回落为 `faq`，防止模型绕过路由层直接构造越界调用。
**验证**：新增测试——非知识类 intent 被强制回落 faq（5 个参数化）、合法知识 intent 原样透传（6 个参数化）、白名单不含专用路径意图、专用路径意图**端到端路由**不得进入模型工具轮（3 个参数化，断言实际 `_route_after_plan` 决策而非仅白名单函数）、`unknown` 不进模型工具轮、只读查询意图保留模型调度。**用"移除意图门禁"确认路由层 3 个测试会失败**（首次写法只测白名单函数，撤销路由条件后仍通过——已改为测实际路由决策，避免假绿）。`tests 346 passed`。
**效果**：`你好`/`推荐适合我的产品`/`我想转人工客服` 回归各自确定性路径，与管线语义一致；`查我的持仓`/`我有没有风险限制`/`公司主营业务` 等**只读查询仍享受模型自主工具调度**（本次改造的目标得以保留）。

### ISSUE-20260914-TOOL-FAILURE-NOT-FEED-BACK
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 失败反馈与预算浪费
- 严重程度：P1
**问题**：诊断 `"我的订单 ORD-123456 状态"` 显示模型**选对了工具**但循环空转：
```
[round 1] get_my_order_status  {'query': 'ORD-123456'}  -> False
[round 2] get_my_order_status  {'query': 'ORD-123456'}  -> False   ← 参数完全相同
[round 3] get_my_order_status  {'query': 'ORD-123456'}  -> False
[round 4] get_my_order_status  {'query': 'ORD-123456'}  -> False
stopped_reason = budget_exhausted      results = []
```
**4 轮全部传入完全相同的参数、4 次全失败、无任何结果**，纯浪费预算与 LLM 配额。
**根因（两点）**：
1. **失败信息回灌不足**：旧实现只写 `f"[{tool}] 未取得结果（{error}）"`，**未回灌已试参数、也未给出可执行的调整建议**，模型无法据此改变策略，只能重复同一调用。
2. **缺重复调用闸门**：确定性工具用**完全相同的参数**调用必然得到相同结果，但循环未拦截，直到 `budget_exhausted`。
**对照影响**：该 query 因此 `requires_handoff=True`（工具失败 → `low_confidence` → 转人工），而管线正确返回 `needs_clarification`+"请补充订单信息"、不转人工。**图把"订单查不到"误判成"需要转人工"。**
**解决**：
1. **失败反馈结构化**：回灌 `error` 具体值 + **已试参数** + 按 error 类型给出的可执行建议（`fact_not_found`→"未查到该对象，请换更明确的查询对象或不要重复相同参数"；`low_confidence`→"请改写 query 换个说法，若已改写两次以上仍失败请停止"；`route_mismatch`→"该工具与意图不匹配，请改用其他工具" 等）。
2. **重复调用闸门**：对 `(tool, arguments)` 做规范化 key（`json.dumps(sort_keys=True)`），**完全相同的重复调用直接拦截**、不再执行、写审计 `error="duplicate_call_skipped"`、并把"该调用已试过且结果相同"告知模型后结束循环。**参数不同的重试（如换 query 改写）仍允许。**
**验证**：新增/更新测试——相同调用只执行一次且 `stopped_reason=duplicate_call_skipped`、被拦截的重复调用留审计、成功后再发同一调用也被拦截、**参数不同的重试不被拦截**（防止闸门过严）、失败反馈含 error/已试参数/调整建议。**用"撤销闸门与覆盖修复"确认 2 个测试会失败**。`tests 350 passed`。

### ISSUE-20260914-COVERAGE-FALSE-POSITIVE
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 覆盖判定
- 严重程度：P1
**问题**：诊断 `"你们公司的主营业务是什么"` 显示模型 **4 轮全部使用 `intent=company_info`**，`company_business` **从未被检索**：
```
[round 1] search_customer_knowledge {'intent': 'company_info', 'query': '公司主营业务'}
[round 2] search_customer_knowledge {'intent': 'company_info', ...}
[round 3] ... [round 4] ...  （均为 company_info）
事后算未覆盖意图 = []      ← 误判"全部覆盖"
```
对照中 `task_intents: pipeline=['company_info','company_business']` vs `graph=['company_info']`——图少一个 task。
**根因**：`_uncovered_intents()` 的覆盖判定用了**两级兜底**：先看结果里的 `intent`，若未命中再看 `data.tool` 是否被调用过。而 `company_info` 与 `company_business` **共用同一个工具** `search_customer_knowledge`，于是"调过该工具"被误判为"两个意图都已覆盖"，`pending=[]` → **规则兜底不触发** → 缺的意图既没被模型检索、也没被规则补齐。
**解决**：覆盖判定**只按结果里的 `intent` 字段**，**移除 `data.tool` 兜底**。共用工具的不同意图必须各自被检索过才算覆盖；`greeting`/`human_handoff`/`business_operation` 等映射为 `None` 的非只读意图仍跳过（不参与兜底）。
**验证**：新增测试 `test_knowledge_intents_sharing_one_tool_are_still_tracked_individually`——模型只检索 `company_info` 时，`company_business` 必须被识别为未覆盖并被补齐。**用"退回工具兜底"确认该测试会失败**。`tests 350 passed`。
**教训**：**"共用资源的意图"不能用资源维度判覆盖**——`company_info` 与 `company_business` 语义不同但共用检索工具，按工具粒度判定必然假阳性。凡"多对一"映射，覆盖判定必须落在**细粒度（intent）**上。

### ISSUE-20260914-NO-PARTIAL-STATE
- 日期：2026-09-14
- 分类：Agent / 状态语义 / 用户体验
- 严重程度：P2（语义失真，非功能故障）
**问题**：`开户流程是怎样的` 出现"答案内容好但状态失真"：图的答案**自述资料不足**却报 `completed`：
```
pipeline: needs_clarification + requires_handoff=True   "当前知识库没有足够依据，建议转人工客服…"
graph:    completed + requires_handoff=False
          "根据您提供的资料，引用内容中未包含开户流程的具体步骤…资料主要聚焦于：投资者分类/适当性管理/风险等级划分…"
```
用户看到"资料不足"的答案，却被系统标记为**已完成**。
**排查澄清（推翻了我此前的两个错误假设）**：
1. 我曾以为这是"图丢掉了已有答案"→ 实测图**保留了**答案且有 5 条引用。
2. 我曾以为是我上一轮"收窄 judge 区间"的副作用（放行了不相关引用）→ 实测 `workflow` 的原文 `best_sim=0.5043` **落在 judge 区间 [0.35,0.55) 内，judge 判了 yes**；两种模式对照显示 judge=False 时原文是 `low_confidence`、judge=True 时变 `sufficient`——**judge 主动认为引用包含相关制度依据**。
3. 即：**这不是 bug，是真实的中间态**——检索到了相关制度文档（引用确实相关），但文档中没有"开户流程步骤"这一具体答案。生成时 LLM 如实说明了缺口。
**根因**：**状态机缺少中间态**。`TaskExecutionResult.status` 只有 `succeeded`/`failed`/`needs_clarification` 等，没有"答了但不完整"。于是 `summarize` 只能判 `completed`，**无法区分"完整作答"与"答了但资料不足"**——与 `ISSUE-20260914-COMPLETED-STATUS-MASKED-BY-DROPPED-TASK` 同族（`overall_status` 无法表达真实完成度）。
**解决**：
1. `CustomerRagAnswer` 新增 `insufficient` 字段，`generate()` 依据**提示词已有要求**（"如果引用不足以回答，必须明确说资料不足"）结构化识别答案自述缺口（`answer_reports_insufficient()`，宁可漏判不误判）。
2. `generate_node` 在 `insufficient=True` 时把 task 标为 **`partially_answered`**，并补一句 clarification（"当前资料未完全覆盖该问题，可补充更具体的信息或转人工客服"）。
3. `summarize` 新增 `partially_answered` 判定：**全部 task 都已作答（无失败/待澄清）但答案自述不完整** → `partially_answered`（区别于 `partially_completed`="部分 task 失败"）。同时把 `partially_answered` 计入"已作答"，使"半成功 + 有失败"正确落到 `partially_completed`（而非误落 `needs_clarification`——该 bug 由新测试当场抓出）。
**验证**：新增 6 个测试——自述缺口识别、完整答案不误判、全半成功→`partially_answered`、全成功→`completed`、半成功+失败→`partially_completed`、**中间态必须区别于 `needs_clarification`（前者有答案、后者没有）**。`tests 356 passed`。
**影响**：`overall_status` 现可表达三档完成度（`completed` / `partially_answered` / `partially_completed`），前端与 `ApiResponse` 均按字符串透传（`overall_status: str` 无枚举约束），无需改契约。
**待观察**：`partially_answered` 是新增状态，前端目前只做通用展示；若要做差异化 UI（如"部分回答"标签 + 追问入口），需前端配合。

### ISSUE-20260914-FAILURE-HINT-MISLEADS-TOOL-SWITCH
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 提示词副作用
- 严重程度：P2
**问题**：`我的订单 ORD-123456 状态` 的图产出 **3 个 task**（管线只有 1 个）：
```
[graph] task=model-tool-1 intent=order_status       needs_clarification  fact_not_found
[graph] task=model-tool-2 intent=workorder_status   needs_clarification  fact_not_found   ← 多出
[graph] task=model-tool-3 intent=faq                ...                                  ← 多出
```
诊断显示模型**每轮失败都换一个工具**：
```
round1 get_my_order_status        (计划内)  -> False
round2 get_my_workorder_status    (换工具)  -> False
round3 search_customer_knowledge  (再换)    -> False
round4 search_customer_knowledge  (改写)    -> False
```
**根因**：**我上一轮为修复"重复调用"写的失败提示语有歧义**。原文案：
> `fact_not_found` → "未查到该对象（可能是订单号/工单号不存在或不属于当前客户），**请换一个更明确的查询对象**，或不要再重复相同参数。"

模型把"**换一个查询对象**"理解成"**换一个工具试试**"，于是从订单 → 工单 → 知识库，**逐步游走到与用户问题无关的工具**（查订单失败后去查知识库"订单查不到怎么办"）。`workorder` 与 `order` 工具描述本就相近（"用户问我的订单/我的工单时使用"），进一步放大了误判。
**这是我引入的副作用**——上一轮修复解决了"空转重复"，却制造了"盲目换工具"。两次都出在**失败反馈的措辞**上。
**解决（两处）**：
1. **失败提示明确禁止换工具**：各 error 类型一律加上"**请勿改调其他工具**"，并指明正确动作是"**在本工具内**换更明确的参数 / 改写 query / 直接停止（交由下游提示补充或转人工）"；`route_mismatch` 明确要求"改回计划内的工具"。
2. **system prompt 新增第 7 条**：**"工具调用失败时留在同一意图内**：换参数或改写 query 重试；**不要改调与用户问题无关的其他工具**（例如查订单失败后不要改查工单或知识库），反复失败就停止"。
**验证**：新增 3 个测试——失败回灌含"不要改调/请勿改调"、system prompt 含该规则、`low_confidence` 提示同样不鼓励换工具。**用"退回旧文案"确认前两个测试会失败**。`tests 359 passed`；`我的订单` 的 `requires_handoff` 已与管线一致（False）、clarification 文案一致。
**教训**：**给模型的失败反馈是"行为指令"，措辞必须同时说明「该做什么」与「不该做什么」**。只给正向建议（"换个更明确的"）会把模型推向未预期的方向——尤其当它面对的是"工具选择"这种开放决策时。

### ISSUE-20260914-REACT-FALLBACK-IGNORES-INTENT
- 日期：2026-09-14
- 分类：Agent / 确定性 ReAct / 工具选择
- 严重程度：P1
**问题**：`我的订单 ORD-123456 状态` 在模型工具轮超时回退到确定性 ReAct 后，**用知识库查订单跑了 20 轮全失败**：
```
llm.tool_round model=qwen-plus error=APITimeoutError     ← 模型轮超时（16.8s > tool timeout 15s）
[graph] task=t1 intent=order_status
        tool_calls=20  execution_mode=bounded_react
        call[1..20] tool=search_customer_knowledge success=False error=low_confidence
        -> low_confidence -> requires_handoff=True         ← 误转人工
pipeline: tool=get_my_order_status 一次即得 fact_not_found -> needs_clarification, handoff=False
```
**根因**：`react_step_node` **硬编码** `name="search_customer_knowledge"`，**不按 task 意图选工具**：
```python
result = await self.react_tool.execute(
    name="search_customer_knowledge", ...)   # ← 无论 task.intent 是什么都用知识检索
```
于是**事实类意图**（`order_status`/`asset_summary`/`workorder_status`/`risk_restriction`/`product_status`）在确定性回退路径上被当作知识查询。而管线有 `TOOL_BY_INTENT` 映射（`customer_task_executor.py:19`），同一意图走 `get_my_order_status` 一次即得 `fact_not_found` → `needs_clarification`、**不转人工**。
**这是长期存在的问题**，此前被其它失败掩盖（模型工具轮正常时不会走到该节点），本次因 `APITimeoutError` 回退才暴露。**20 轮全失败**说明它还浪费了大量工具调用。
**解决（两处）**：
1. **按意图选工具**：`react_step_node` 改用 `TOOL_BY_INTENT.get(task.intent, "search_customer_knowledge")`——事实类意图走对应事实工具，知识类仍走知识检索（与管线 `_execute_one` 的映射同源，从 `customer_task_executor` 导入该常量，避免两处漂移）。
2. **失败文案按工具类型区分**：`is_fact_tool = result.error in {"fact_not_found","ambiguous_product"}` 时用"请补充更明确的查询对象或订单/产品信息。"，否则用知识检索的 handoff 文案。此前一律用知识检索文案，导致"订单查不到"被表述成"知识库没有依据 + 转人工"。
**验证**：新增 `tests/test_react_intent_tool_selection.py` 共 12 个测试——9 个参数化验证各意图选对工具（5 个事实类 + 4 个知识类）、事实失败用补充信息文案、知识失败仍用 handoff 文案、事实失败不产生 `low_confidence` 转人工信号。**用"退回硬编码"确认 5 个事实类测试会失败**。`tests 371 passed`。
**附带发现**：`llm.tool_round ... APITimeoutError`（16.8s）说明资源保护生效（工具轮超时 15s 起效，未拖到 90s），但也说明**模型工具轮单轮 15s 仍可能超时**——Qwen 在 `tool_choice=auto` 下（需生成工具调用 JSON）比普通对话慢。**待观察是否需要放宽工具轮超时或改为更快的模型。**

### ISSUE-20260914-TIER-S-ROUTES-TO-UNAUTHORIZED-MODEL
- 日期：2026-09-14
- 分类：配置 / 模型授权 / 静默降级
- 严重程度：P1
**问题**：为给工具轮换更快模型，实测各模型可用性时发现 **`qwen-turbo` 在账号下无权限**：
```
llm.usage model=qwen-turbo status=failed duration_ms=1320
llm.tool_round model=qwen-turbo error=PermissionDeniedError
```
而 `config/understanding.yaml` 的模型路由把**简单 query（tier S）**指向它：
```yaml
- {max: "S", model: "qwen-turbo"}    # ← 无权限
- {max: "M", model: "qwen-plus"}
- {max: "L", model: "qwen-max"}
```
**实测影响面**（对照用的 8 条 query 中 **6 条**路由到 `qwen-turbo`）：
```
你好 / 查一下我的持仓 / 我有没有风险限制 / 我的订单 ORD-123456 状态 /
推荐适合我的产品 / 开户流程是怎样的        → 全部 tier-S → qwen-turbo（无权限）
你们公司的主营业务是什么 / 什么是双录       → tier-M → qwen-plus（正常）
```
**后果**：
1. **LLM 意图兜底对简单 query 从未真正生效**——`classify_intent` 调用失败 → 被 `customer_agent_execution` 的 `except` 吞掉 → 静默回退规则规划器。这解释了此前"LLM 兜底开着却观察不到效果"。
2. **每次白耗一次失败调用**（约 1.3s + 配额）。
3. **生产风险**：`docker-compose.p0.yml` 默认 `LLM_INTENT_FALLBACK_ENABLED:-true`，**线上很可能一直在静默失败**（本地 `.env` 设为 false 故未触发，掩盖了问题）。
**模型可用性实测结果**（同轮）：
| 模型 | 结果 |
|---|---|
| `qwen-turbo` | ❌ PermissionDeniedError |
| `qwen-plus` | ✅ 正常返回工具调用 |
| `qwen-max` | ✅ |
| `qwen3-max` | ❌ PermissionDeniedError |
| `qwen-flash` | ✅ **（新发现可用，轻量快速）** |
| `qwen2.5-7b-instruct` | ❌ PermissionDeniedError |
**解决（待实施，需业务确认）**：把 tier-S 路由改为**已开通的模型**（`qwen-flash` 定位为轻量快速，适合简单 query）；并建议增加**启动期/首次调用期的模型可用性自检**，避免"配置指向无权限模型"这类问题继续静默。**若换模型，需先实测其意图识别准确率不低于原预期。**
**同类模式**：这是本项目第 N 次「配置声明与实现实际脱节」——与 `AMBIGUOUS-JUDGE-NOT-WIRED`（配置 judge_ambiguous=true 但未接线）、`INTENT-FALLBACK-DEFAULT-LAYERING`（文档说默认关、compose 默认开）同族。**共性：配置层没有校验机制，错误只在运行时静默降级。**

### ISSUE-20260914-MODEL-LATENCY-DATA-REFUTES-TIMEOUT-THEORY
- 日期：2026-09-14
- 分类：LLM 基础设施 / 延迟归因
- 严重程度：P2（澄清，非 bug）
**问题**：此前把工具轮的 `APITimeoutError`（16.8s）归因为"`qwen-plus` 在 `tool_choice=auto` 下太慢"。新增 `scripts/compare_tool_model_latency.py` 实测三模型 × 8 条 query 的延迟与工具选择匹配率，**该归因被数据推翻**：

| 模型 | avg | p95 | 工具选择匹配 |
|---|---|---|---|
| `qwen-flash` | **904ms** | 1092ms | **8/8** |
| `qwen-plus` | 1274ms | 1398ms | 8/8 |
| `qwen-max` | 1489ms | 1589ms | 8/8 |

**三者 p95 最大仅 1.6s，远低于 15s 超时** —— 单独调用根本不会超时。**故 16.8s 超时不是"模型慢"，而是环境因素**，候选：完整流程中的并发调用打满配额、工具轮 prompt 更长（8 个 schema + planned_block + context_lines）、或对照脚本连跑 28 个请求触发限流排队。**该归因待进一步定位（本条只负责澄清"不是模型慢"）。**
**已落地（数据支持的结论）**：
1. **工具轮模型改用 `qwen-flash`**：实测最快（904ms，比 plus 快 29%）且**工具选择 8/8 全对**——工具选择属"结构化决策"，无需强模型。新增配置 `qwen_tool_model`。
2. **tier-S 路由从 `qwen-turbo` 改为 `qwen-flash`**：修复 `ISSUE-20260914-TIER-S-ROUTES-TO-UNAUTHORIZED-MODEL`（turbo 在本账号 `PermissionDeniedError`，导致简单 query 的 LLM 意图兜底从未真正生效）。
**验证**：新增 `tests/test_model_config_guard.py` 共 5 个测试——**禁止把已知无权限模型写进路由配置**、默认模型须可用、tier 清单不得超过已确认可用集合、工具轮模型须可用、**工具轮超时须显著高于实测 p95**（避免正常调用被误判超时）。**用"把 tier-S 退回 qwen-turbo"确认 2 个测试会失败**。`tests 376 passed`。
**附带价值**：`compare_tool_model_latency.py` 把"模型选型"从**主观判断**变成**可复现的数据对比**（延迟 + 工具选择匹配率双指标），后续换模型/换账号可复用。

### ISSUE-20260914-FAILED-VS-CLARIFICATION-MISMATCH
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 失败语义
- 严重程度：P2
**问题**：`开户流程是怎样的`（知识类 `workflow`）两侧状态标记不一致：
```
pipeline task = needs_clarification (error=low_confidence)  execution_mode=bounded_react
graph    task = failed               (error=low_confidence)  execution_mode=model_tool_selection
```
两边**都已转人工**（`requires_handoff=True`、同一 `ticket_no`），仅状态标记不同。但 `failed` 会污染 `overall_status`（`needs_clarification` vs `failed`），并在对照中产生假差异。
**根因**：`customer_model_tools` 的失败映射**只覆盖事实类错误**：
```python
needs_clarification = error_code in {"fact_not_found", "ambiguous_product"}   # 缺 low_confidence
```
于是**知识检索的 `low_confidence` 落到 `failed`**。而管线两处都把 `low_confidence` 视为 `needs_clarification`（`_execute_one` 与 `_bounded_react_knowledge`）。这是我在做「失败语义映射」修复时的**遗漏**——当时只考虑了事实工具的场景，漏掉了知识检索。
**解决（两处）**：
1. **补齐映射**：`needs_clarification` 加入 `low_confidence`；澄清文案按错误类型区分（`fact_not_found`/`ambiguous_product` → "请补充更明确的查询对象或订单/产品信息。"；`low_confidence` → "当前资料不足，可以补充问题或转人工客服。"）。
2. **常量去重**：`_FALLBACK_CLARIFICATION` 原只在 `graph.py` 定义，本模块又用到却未定义（运行到该分支会 `NameError`——**被新测试当场抓出**）。已在 `customer_model_tools.py` 定义同名常量并**用测试钉住与 `graph.py` 逐字一致**，避免同一语义两种文案。
**验证**：新增 `tests/test_model_tool_failure_semantics.py` 共 7 个测试——三类可恢复错误都映射为 `needs_clarification`、`low_confidence` 用知识类文案、`fact_not_found` 用事实类文案、**非可恢复错误仍是 `failed`**（不过度放宽）、两处常量逐字一致。**用"把 low_confidence 退回 failed"确认测试会失败**。`tests 383 passed`。

### ISSUE-20260914-MODEL-CALLS-OFFPLAN-TOOL
- 日期：2026-09-14
- 分类：Agent / 模型工具轮 / 范围约束
- 严重程度：P1
**问题**：`我的工单进展如何` **稳定复现**（两次探针完全一致）：
```
[graph] task=model-tool-1 intent=workorder_status          succeeded
[graph] task=model-tool-2 intent=explain_workorder_status  needs_clarification (low_confidence)
=> overall_status=partially_completed, requires_handoff=True
answer = '您最近的工单 WO… 当前状态为 open。\n当前知识库没有足够依据，建议转人工客服…'
```
用户**已拿到工单状态**，却被回复"没有依据、已转人工"——**自相矛盾**。管线只查 `workorder_status`，`completed`、不转人工。
**根因**：**模型多调了计划外工具**。计划只有 `workorder_status`，模型拿到状态后**自行决定**再调 `explain_workorder_status` 去"解释状态含义"，而该工具走知识检索、知识库无流程资料 → `low_confidence` → 额外 task 失败 → **污染整体状态并触发转人工**。
**关键判断**：`explain_*` 这类工具**本应由规划器通过「多意图 + depends_on」触发**（`CustomerTaskExecutor._execute_one:243` 的条件是 `item.depends_on and item.intent in {workflow,faq,policy}`），**不该由模型临时决定**。用户只问状态、没问流程，这次扩展既非必需、又注定失败。
**替代方案的权衡**：曾考虑"允许模型扩展，但越界调用的失败不污染状态"。**否决理由**：越界调用若**成功**，会产生计划外的额外开销与不可预测输出；且绕过了确定性规划器——规划结果才是"该覆盖哪些方面"的权威来源。
**解决**：新增**工具范围约束**——模型只能调用 `planned_intents` 经 `_INTENT_TO_TOOL` 映射出的工具集（`_tools_for_intents()`）；越界调用**不执行**、记审计 `error="tool_out_of_plan_scope"`、`stopped_reason` 同值。**未传 `planned_intents` 时不约束**（保持向后兼容）。模型仍**完全自主**决定：用计划内哪个工具、传什么参数、是否续调、如何改写——只是不能越出计划范围。
**验证**：新增 `tests/test_model_tool_scope_constraint.py` 共 6 个测试——越界工具被拦截（不执行、不产结果、留审计、`stopped_reason` 正确）、工单 query 只产出 1 个成功 task（与管线一致）、**多意图计划时两个计划内工具都可用**（不误伤正常多意图）、无计划时不约束、生成温度为 0。**用"移除范围约束"确认 2 个测试会失败**。`tests 389 passed`。
**踩坑**：第一版写 `outcome.stopped_reason = outcome.stopped_reason or "tool_out_of_plan_scope"`，但 `ModelToolOutcome.stopped_reason` 的**默认值是 `"no_tool_call"`（非空）**，`or` 永远保留默认值 → 真实原因丢失。**被新测试当场抓出**，改为直接赋值。

### ISSUE-20260914-GENERATION-TEMPERATURE-INSTABILITY
- 日期：2026-09-14
- 分类：RAG / 答案生成 / 可复现性与合规
- 严重程度：P1
**问题**：同一 query 两次生成出现**事实相反**的断言。`我有没有风险限制`：
```
第一次（全量对照）: '**是的，您有风险限制。**'              ← 断言受限
第二次（探针）    : '我无法直接判断您个人是否设有风险限制'   ← 不确定
```
**关键事实**：`get_my_risk_restriction` 工具返回的是「**您的账户当前未显示风险限制**，但存在一条待处理风险记录」——**第一次的断言与事实相反**。
**根因**：`CustomerRagAnswerService.generate()` 的 `temperature=0.1`（非 0）。该任务是"**基于引用复述事实**"，**不需要创作性随机**；0.1 的温度足以让模型偶发地把"未显示限制"写成"有风险限制"。
**影响**：① **合规风险**——无依据时对客户账户状态下断言，可能引发客户误解/投诉；② **对照与评测不可复现**——本次排查中我一度据此判"图有合规问题"，实为坏样本，**浪费了一轮判断**。
**解决**：`temperature` 0.1 → **0.0**。项目内已有先例（judge 调用即为 0.0），说明"事实复述类任务用 0 温度"已是共识。
**验证**：新增测试断言 `generate()` 源码含 `temperature=0.0` 且不含 `temperature=0.1`（源码级钉住，防止回退）。`tests 389 passed`。
**连带修正**：我在本条之前基于该坏样本做出的两个判断**已更正**——「图有合规问题」不成立；「我有没有风险限制 这条图错」应改为「图更准确」（其 `partially_answered` 标注正确反映了"答了但没答全"）。**教训：单次样本不足以判定 LLM 输出缺陷，必须先排除随机性。**

> **已上升为架构决策**：本条与 `ISSUE-20260914-MODEL-TOOL-FORCED-EARLY-STOP` 的教训已写入 `docs/DECISIONS.md` 的「2026-09-14：只读查询采用"模型自主决策 + 规则兜底校验"分层；禁止用提示词代替规则保证」，作为**适用于本项目所有 Agent 能力的边界原则**（提示词不承担正确性/完整性保证；"少做了却仍报成功"必须有显式标记）。`docs/` 按 `.gitignore` 不入库，故该决策文档仅存本地。

### ISSUE-20260914-MODEL-DECISION-REASONING-DISCARDED
- 日期：2026-09-14
- 分类：Agent 可观测性 / 模型调度
- 严重程度：P1（不是答案错误，而是**根因不可观测**）
**现象**：`我的工单进展如何` 稳定复现"模型多调一个计划外工具"（`explain_workorder_status`）导致自相矛盾，
但**为什么模型要调它**无从判断——只能从"它失败了、结果错了"反推原因，反复猜测。
**根因**：`QwenProvider.request_tool_call` **丢弃了 `message.content`**。
模型发起 tool_call 时通常会**同时给出一段文本**说明理由，而该函数只回传
`{"name", "arguments"}`，`content` 直接丢弃；`ToolRound` 也没有承载理由的字段。
结果：**模型的自述理由在产生的那一刻就被丢掉了**，磁盘上（`audit_logs`）只有工具名与参数，
`data` 里只有结果。所谓"模型就是爱多调一个工具吧"——**这句话当时是没有证据支撑的猜测**，
因为唯一能证伪它的数据从未被保存。
**为什么这是真 bug 而不只是"缺个日志"**：
1. 本问题的修复方向**完全取决于理由**——若模型是"有意补充制度解释"（合理），应扩计划；
   若是"把'解释状态'误判为独立意图"（越界），才该加范围约束。
   没有理由，就无法区分这两种情况，只能选一个方向试。
2. 它使**同类问题的每一轮排查都要重跑真实链路**，成本高且不可回看历史。
3. 与既有教训同源：`ISSUE-20260914-GENERATION-TEMPERATURE-INSTABILITY` 的"**没排除随机性前不得判定 LLM 缺陷**"
   是"先取证据再下结论"；本条是"**连取证据的通道都没有**"——比前者更前一步。
**修复**：
1. `qwen.py`：`request_tool_call` 返回 `reasoning`（`message.content`），**不参与控制流**，解析失败为零值，不影响主流程。
2. `customer_model_tools.py`：`ToolRound` 增 `reasoning` 字段；新增 `ModelToolOutcome.decisions`
   ——记录**每一轮模型的原始决策**（含**被拦下**的越界/重复调用，标注 `outcome=pending/executed/rejected_off_plan/rejected_duplicate/rejected_not_allowed`）。
   与 `rounds` 的关键区别：`rounds` 只记**已执行**的调用，看不到"模型想调但被拦下"的动作。
3. `_audit()` 落 `reasoning` 到 `audit_logs.detail`，线上可回看。
4. `graph.py` 写入 `model_tool_decisions`；`graph_agent_runner.py` 把
   `{rounds, decisions, stopped_reason}` 作为 `data.model_tool_trace` **随结果带出**
   ——此前轨迹只存在图状态里，API 与排查脚本都看不到。仅模型工具轮跑过时才附加，不污染确定性路径。
5. `inspect_query_dual.py` 打印 `decision[..] tool/outcome/args/reasoning`，一次运行即可判定性质。
**验证**：新增 4 条测试（`tests/test_model_tool_scope_constraint.py`）：越界决策必须留下理由、
理由必须落审计、**缺 `reasoning` 时不得影响控制流**（模型可能只回 tool_call 不带文本）、
响应必须带出轨迹且**确定性路径不得被塞入空轨迹**。`tests 393 passed`（389 → 393）。
**方法论教训**：**"模型就是爱这样"不是结论，是缺数据的症状。**
在断言某个模型行为倾向之前，先确认该行为的**动机证据是否被保存**；
若观测通道本身被丢弃，任何"这就是模型天性、改不好"的判断都是**不可证伪的猜测**，
且极易滑向"加限制"这种掩盖而非解释的处置方式。正确顺序是：**先补观测 → 看真实理由 → 再定约束**。

### ISSUE-20260914-COVERAGE-BACKSTOP-SKIPPED-ON-BUDGET
- 日期：2026-09-14
- 分类：Agent 编排 / 多意图覆盖
- 严重程度：P1（**漏答且静默**：用户少拿一路答案，状态仍报 completed）
**现象**：`你们公司的主营业务是什么` 图只产出 1 个 task，管线 2 个：
```
task_intents: pipeline = ['company_info', 'company_business']
              graph    = ['company_info']
```
两条路径**都从同一个 planner 出发**（图 `graph.py:166 _plan` / 管线 `customer_agent_execution.py:338`），
实测 `CustomerIntentPlanner.plan()` 对该 query 返回的原始计划就是 **2 个 task**
（`['company_info','company_business']`）。所以 task 是在**规划之后**丢的。
**根因**：`_fill_uncovered()`（"别漏"的规则兜底）**只挂在"模型主动停止"分支**（`if not decision`）。
若模型不主动停、一路调到预算耗尽或触发重复调用闸门，就会走 `budget_exhausted` /
`duplicate_call_skipped` 出口——而这两个出口**完全不检查** `_uncovered_intents`，兜底被**静默跳过**：
```
[graph] task=model-tool-1 intent=company_info  succeeded
（company_business 从未检索）
=> 图上只剩 1 个 task，但 overall_status 仍报 completed
```
**为什么"只留个标记"不算修好**：原有一处测试断言此场景 `stopped_reason == "intent_uncovered"`——
即"预算耗尽就放弃，只记下漏了这件事"。但兜底的意义是**别漏**，不是**把漏了记下来**。
只记原因的版本会让 codebase 里长期存在一条"看起来有兜底、实际不生效"的路径，
这正是本次现象能长期潜伏的原因。
**为什么容易漏掉**：兜底被写在"模型主动停止"这个**偶然条件**下。
模型停不停是概率性的，把它当触发条件，等于让"别漏"变成"看运气"。
**修复**：把覆盖检查提到**循环外的统一收尾**，对**所有**退出路径生效，
并排除"越界被拦"这一已明确终止的情形（`tool_out_of_plan_scope` 时不补，
避免"模型越界 → 兜底又替它查一遍"的语义混乱）。补齐成功则记
`stopped_reason="rule_backstop_filled"`，使"主循环是被兜底收的尾"在观测上可见。
**验证**（A/B 对真实 planner，非构造样本）：
```
修复前: stopped_reason=duplicate_call_skipped  results=['company_info']                       ← 漏
修复后: stopped_reason=rule_backstop_filled    results=['company_info','company_business']    ← 补齐
```
新增/改写 3 条测试：预算耗尽路径必须补齐、已全覆盖时**不得**多调（省一轮 LLM）、
未传计划时不得触发。另有 1 条旧测试此前断言的是**错误行为**（`intent_uncovered`），已改写为断言**结果被真的补上**。`tests 396 passed`。
**方法论教训**：**兜底不能挂在"对方恰好停下"这种偶然条件上。**
任何"别漏"的保证必须建立在**必然执行**的收尾路径上（循环外统一收尾），
否则它会以"代码里有、实际不生效"的形态长期潜伏——
比"没有兜底"更危险，因为它会让人误以为已经安全。

### ISSUE-20260914-MULTI-KNOWLEDGE-INTENT-NO-INCREMENT
- 日期：2026-09-14
- 分类：RAG / 意图规划 / 信息增量
- 严重程度：P3（**结论已更正**：原判"无增量"错误）
- 状态：**已撤回错误结论**，本条保留作为方法论反面案例

**原始（错误）结论**：基于**单个样本**观察到 company_info 与 company_business
命中完全相同的 5 个 chunk（Jaccard=1.000、各自独有=0），据此判定
"拆 task 不带来信息增量，只是把同一份资料复述两遍"。

**更正后的实测结论**（容器内复跑，同一 query 同一库）：
```
chunk 重叠(Jaccard) = 0.667   仅 company_info 独有=1  仅 company_business 独有=1  共有=4
=> INCREMENTAL：各自检索到独有证据（4 共有 / 1 独有）
```
即**拆分确实带来独有证据**，只是增量较小（4 共有 + 各 1 独有）。
"拆 task 无增量"的判断**不成立**。

**为什么两次结果不同（这才是真问题）**：
同一 query、同一知识库，两次检索的 chunk 集合**本身就在变**：
```
第一次: 4a40b4c1, 77a23af6, 93738fbe, caf20d8d, e0f73a21
第二次: 4a40b4c1, 77a23af6, caf20d8d, e0f73a21, f069230e   ← 93738fbe 被 f069230e 取代
```
`candidate_k = max(top_k*4, 20)`，第 20 名的 chunk 恰好落在**边界**上，
向量检索的微小浮点/服务端差异就会导致边界外/内的 chunk 互换。
于是"重叠率"这种**基于集合的指标本身不稳定**，用单次采样它去判断架构价值，必然得出错误结论。

**方法论教训（本条的核心价值）**：
1. **集合类指标（重叠率/覆盖率）必须多次采样**，单次结果不能作为架构判断依据——
   因为检索候选集在边界上抖动，一次采样可能恰好命中"完全重合"或"完全不相交"两个极端。
2. 这是我在本议题上**第三次**因单样本下错结论（前两次：误判"模型改写更差"、
   误判"图有合规问题"）。三次的共同形态都是：**用一个样本的差异去推断机制性结论**。
   正确做法是：先多次采样建立分布，再看分布是否支持结论。
3. **仍待决**：company_info / company_business 的 scope 逐字相同、
   基础 query 相同（仅尾部扩展词不同），是否应拆成两个 task 仍需产品判断；
   但依据**不再是"无增量"**（该依据已失效），而是"增量是否值得多一次检索与生成成本"。

### ISSUE-20260914-JUDGE-BAND-REJECTS-RELEVANT-EVIDENCE
- 日期：2026-09-14
- 分类：RAG 证据门控
- 严重程度：P1（**拒答本可作答的问题**）
**现象**：`你们公司的主营业务是什么` 检索**成功命中 5 条公司资料**
（相似度 0.5046 / 0.4901 / 0.4794 / 0.4677 / 0.4486，内容确实是公司业务介绍），
但 RAG 判定 `low_confidence` → `handoff_required=True` → 回答"当前知识库没有足够依据，建议转人工客服"。
**根因**：`best_sim = 0.5046` 落在 **judge 区间 `[judge_reject=0.35, judge_answer=0.65)`**，
于是调用 LLM-judge 裁决；实测 **judge 返回 `False`**（判为不可作答）→ 拒答。
**为什么这是 bug 而不是保守设计**：
1. 命中的 chunk **内容就是公司业务介绍**（`# XX科技有限公司 企业信息` / `## 一、公司基本信息`），
   对"你们公司的主营业务是什么"是**直接相关**的，judge 判 `no` 是**误判**。
2. 更关键：同一批 chunk 在**容器内实测却是 `sufficient`**（管线与图两边都答出来了）。
   同一 query、同一知识库，本地判拒答、容器判可答 —— **判定结果不可复现**。
**为什么不可复现（重要）**：`CustomerRagService.answer` 的**门控嵌入用原始 query**，
而**检索用 `normalized_query`**（见其实现注释：这是刻意设计，防止扩展词抬高无关查询相似度）。
于是"门控算的相似度"与"检索命中的证据"**来自两个不同的嵌入**，
两者之间的边界很窄（本例 0.5046 vs 阈值区间），任何一侧微小变动都会翻转结论。
叠加 judge 是 LLM 调用（即便 temperature=0，仍受模型版本/服务端影响），
最终表现为**同一 query 的作答能力在两个环境间不稳定**。
**关键认识**：`judge_ambiguous` 这条链路现在的语义是
"**用 LLM 裁决来修正一个本身就不稳定的相似度带**"——它把一个数值抖动问题
变成了"数值抖动 + LLM 误判"的复合问题，**误差是叠加而不是抵消**。
**待决**（未自行修改阈值，因为阈值调整需要基于批量数据而非单条样本）：
- 方向 A：让门控与检索用**同一个嵌入**（消除两侧口径差），使 `best_sim` 与命中的证据自洽；
- 方向 B：judge 区间收窄或改为"judge 只在**无任何 chunk 命中**时介入"，即先信检索结果；
- 方向 C：接受拒答但**提高召回**（扩展检索的 `top_k` / 放低 `min_score`），让 best_sim 抬出 judge 带。
**方法论教训**：**同一 query 在两个环境的判定不一致时，先怀疑"两侧用了不同的输入"，而不是"模型不稳定"。**
本例的门控嵌入与检索嵌入不同源，是这种不一致的结构性来源。
**验证工具**：`scripts/diagnose_multi_intent_value.py` 已打印
`min_score / min_similarity / thresholds / best_sim / 门控归属`，可直接定位落在哪个带。

### ISSUE-20260914-INTENT-CATEGORY-FILTER-NEVER-APPLIED
- 日期：2026-09-14
- 分类：RAG / 检索 / 意图路由
- 严重程度：**P0**（意图规划的两个 task 实际检索**同一批文档**，第二个 task 名不副实）
**现象**：`你们公司的主营业务是什么` 的 `company_business` task，
检索回来的 **5 条全部是 `company_info` 类目文档**，一条 `company_business` 都没有。
扩大 `top_k=25` 复验：
```
company_business intent, top_k=25 → {'company_info': 21, 'policy': 4}
第一个 company_business 的排名 = 无（前 25 名里一个都没有）
前 10 名: 全是 company_info
```
而库中实际存在 **39 个 `company_business` chunk**（全部类目：company_business 39 / company_info 28 / policy 24 / faq 2）。
**根因**：`CustomerRagService` 传给 `WealthKnowledgeRetriever.search()` 的只有 `scope`，
**从未传 `category`**。而 `KnowledgeRepository.search_hybrid` 的类目过滤是：
```python
if category:                                   # ← 需要显式传 category 才生效
    filters.append(KnowledgeDocument.category == category)
if scope and scope.categories:
    filters.append(KnowledgeDocument.category.in_(scope.categories))   # ← 这里放行 6 个类目
```
`scope_for_customer_intent()` 对**所有公开知识类意图**返回同一个含 6 类目的 scope
（company_info / company_business / faq / workflow / policy / risk_public），
于是**类目过滤等价于不过滤**——intent 到类目的收敛**从未发生**。
`intent` 对检索的唯一实际影响退化为：`normalize_customer_query` 追加的几个关键词。
**为什么这是 P0**：
1. **意图规划形同虚设**：计划出 `company_info + company_business` 两个 task，
   但两者检索的是同一批文档池，只靠尾部扩展词区分。
   用户以为"两路证据"，实际是**同一路**。
2. 它**同时解释了**此前多个困惑：
   - 为什么两个 intent 的 chunk 集合高度重合（Jaccard 0.667~1.000）——因为本就不分区；
   - 为什么答案内容重复（"基金销售 + 保险经纪"说两遍）——因为证据同源；
   - 为什么 `company_business` 这个 task 的答案与 `company_info` 几乎一样。
3. **修正了此前的错误归因**：我曾判定"拆 task 无信息增量"（已撤回）与
   "多意图拆分冗余"，真正的原因是**类目没生效**，而不是"拆分本身没价值"。
   一旦类目生效，两个 intent 会检索**不同文档**，拆分才可能有真实增量。
**未改动的原因**：让 `category` 生效会**改变检索结果与答案内容**，
属于检索行为变更，需先确认产品预期（每个 intent 是否应严格限定在自己类目内，
还是类目只是"优先"而非"过滤"）。且需配批量评测，不能凭单条样本。
**验证手段**：`scripts/diagnose_multi_intent_value.py` 已打印每个 intent 的
chunk 集合、相似度与门控归属，可扩展为打印**每条命中的类目**以直观暴露本问题。

### ISSUE-20260914-RETRIEVAL-SET-JITTER-HYPOTHESIS-REFUTED
- 日期：2026-09-14
- 分类：RAG / 方法论
- 严重程度：P3（**假设被证伪**，记录以避免重复走弯路）
**背景**：观察到同一 query 在**本地**与**容器**两次运行返回不同的 chunk 集合
（`93738fbe` 被 `f069230e` 取代），据此提出假设：
"`candidate_k = max(top_k*4, 20)` 使第 20 名落在边界，向量检索微小差异导致集合抖动，
故集合类指标不稳定、单次采样不可信。"
**证伪**：新增 `scripts/diagnose_retrieval_stability.py`，对同一 query 重复采样 8 次：
```
不同 chunk 集合数 = 1（8/8 完全相同的 ids）
best_sim: min=0.5045 max=0.5046 极差=0.0001
落在 judge 带 [0.35,0.65) 的次数 = 8/8
```
**检索在本环境内是稳定的**，不存在边界抖动。故"抖动"假设**不成立**，
本地/容器两次集合不同的原因**另有他因**（且实测两边的 `best_sim` 一致：0.5045 vs 0.5046）。
**方法论价值**：
- 提出假设后**先设计证伪实验**，而不是照着假设去改代码——本次避免了
  一次"为了修复不存在的抖动"而改 `candidate_k` 的无用改动。
- 但**教训仍在**：上一轮基于单样本判定"拆 task 无增量"确实错了；
  正确的纠正方式是**多次采样**（本脚本），而不是换成另一个单样本。
- 结论：集合指标的稳定性**必须实测**，既不能假定它稳定，也不能假定它抖动。

### ISSUE-20260914-CATEGORY-NARROWING-UNVIABLE
- 日期：2026-09-14
- 分类：RAG / 检索策略
- 严重程度：P1（**方案被数据否决**：按类目过滤会让 40% 用例直接答不出）
**背景**：`ISSUE-20260914-INTENT-CATEGORY-FILTER-NEVER-APPLIED` 发现 intent 到类目的收敛未生效
（`CustomerRagService` 从不传 `category`），当时设想的修法是"让类目生效（过滤）"。
**批量探针结果**（`scripts/probe_intent_category_convergence.py`，5 个意图 10 条用例，容器实测）：
```
intent           query              现状类目分布           自身命中  best    收窄后类目        best2
company_info     你们公司叫什么名字      {'company_info':5}    5/5    0.5631  {'company_info':5} 0.5631
company_info     公司客户规模有多大      {'company_info':5}    5/5    0.5750  {'company_info':5} 0.5750
company_business 你们公司的主营业务是什么 {'company_info':5}    0/5    0.5045  {}                 -
company_business 公司提供哪些服务       {'company_info':5}    0/5    0.5227  {}                 -
policy           什么是双录            {'policy':3,'company_info':2} 3/5 0.6247 {'policy':5}   0.6247
policy           风险等级怎么划分        {'policy':5}         5/5    0.7183  {'policy':5}      0.7183
workflow         开户流程是怎样的        {'company_info':3,'policy':2} 0/5 0.4192 {}              -
workflow         怎么修改绑定银行卡      {'company_info':3,'policy':2} 0/5 0.3582 {}              -
faq              客服电话是多少         {'company_info':5}    0/5    0.4927  {'faq':2}       0.1547
faq              怎么联系你们           {'company_info':5}    0/5    0.5638  {'faq':2}       0.2485

汇总：
  company_business 现状自身命中 0/10 (0%)    收窄后有命中 0/2   语料 39 chunks
  company_info     现状自身命中 10/10 (100%) 收窄后有命中 2/2   语料 28 chunks
  faq              现状自身命中 0/10 (0%)    收窄后有命中 2/2   语料  2 chunks
  policy           现状自身命中 8/10 (80%)   收窄后有命中 2/2   语料 24 chunks
  workflow         现状自身命中 0/10 (0%)    收窄后有命中 0/2   语料  0 chunks
现状检索空 0/10  ->  收窄后检索空 **4/10**
```
**结论一：按类目"过滤"不可行**。收窄后 40% 用例检索为空（答不出），比现状更差。
**结论二：`workflow` 类目语料为 0**。`KnowledgeScope.public_customer()` 声明允许
6 个类目（company_info / company_business / faq / workflow / policy / risk_public），
但语料中**只有 4 类**（company_business 39 / company_info 28 / policy 24 / faq 2）。
即 scope **声明了语料中不存在的类目** —— 任何映射到 workflow / risk_public 的意图，
在收窄方案下必然为空。这是"配置声明与数据脱节"，属独立问题。
**结论三：`faq` 收窄后相似度暴跌（0.49→0.15、0.56→0.25）**。
说明"客服电话/怎么联系"这类问题，**真正相关的证据在 company_info 里，不在 faq 里**。
即：**类目划分与用户问题语义并不对应**——`faq` 类目下那 2 个 chunk 与这些问题无关。
**结论四：`company_business` 有 39 chunks 却收窄后为空**，值得单独查。
若类目过滤真生效，39 个候选不可能全落空。可能原因（待 `check_scope_corpus_alignment.py` 确认）：
其文档的 `permission_level` 不在 scope 允许的 (public, customer_visible) 内，
或文档/版本 status 非 active —— 即这批内容**对客户侧本来就不可见**。
**重要修正**：此前我把"答案重复、两个 intent 命中同一批 chunk"归因于
"类目没生效导致不分区"。现在看**更根本的原因是类目划分本身与语义不对应**：
即便让类目生效，`company_business` 会查空、`faq` 会查到不相关内容。
**因此"让 category 生效"不是修复，而是引入新故障。**
**未改动**：本轮不改检索行为。下一步应先查清 `company_business` 39 chunks 为何不可见
（权限/状态/类目值），再重新评估"意图与类目的映射关系是否需要重新设计"。
**方法论**：这是"**先量化再决策**"生效的一次——若直接按最初设想去改，
会引入 40% 的答不出率，而且要等到线上才发现。

### ISSUE-20260914-CATEGORY-PERMISSION-CORPUS-MISMATCH
- 日期：2026-09-14
- 分类：RAG / 知识库治理（**根因条目**）
- 严重程度：P0
**背景**：承接 `INTENT-CATEGORY-FILTER-NEVER-APPLIED`（intent 从不传 category）与
`CATEGORY-NARROWING-UNVIABLE`（按类目收窄会让 40% 用例检索为空）。
`scripts/check_scope_corpus_alignment.py` 的容器实测终于给出三方对账结果：
```
scope 声明类目 = company_info, company_business, faq, policy, risk_public, workflow
scope 允许权限 = public, customer_visible

语料实际（类目 / 权限 / chunk 数）:
  company_business   pl=staff    39
  company_info       pl=public   28
  policy             pl=public   24
  faq                pl=public    2
  business           pl=public    0   <- 类目名与意图名不符（'business' vs 'company_business'）
  workflow           （不存在）        <- scope 声明了但没有
  risk_public        （不存在）        <- scope 声明了但没有
```
**根因：三套命名/取值互不匹配，各自独立出错**
1. **权限不匹配（P0 主因）**：`company_business` 的 39 个 chunk 权限是 **`staff`**，
   而 `public_customer()` 只允许 `public` / `customer_visible` → **被权限过滤，不是被类目过滤**。
   用 `KnowledgeScope.allows()` 逐条验证：
```
category          permission  客户可见  顾问可见  语料
company_business  staff       False     False    39   <- 两种 scope 都取不到
company_info      public      True      False    28
policy            public      True      True     24
faq               public      True      False     2
business          public      False     False     0
```
   `staff` **不在任何一种 scope 的 permission_levels 里**
   （`public_customer` 允许 public/customer_visible；`internal_advisor` 允许 public/internal/advisor）。
   → 这 39 个 chunk 是**孤儿内容**：**没有任何 scope 能检索到它们**。
2. **类目名不匹配**：语料里存在类目 `business`（0 chunk），而代码/scope/意图用的是 `company_business`。
   两个名字并存，说明导入时类目值是**手填**的，未与代码枚举对齐。
3. **scope 声明了不存在的类目**：`workflow`、`risk_public` 被 `public_customer()` 声明允许，
   但语料中**完全没有**这两类文档。映射到它们的意图在收窄方案下必然检索为空。
**内容语义佐证（样例）**：
- `company_business` 的样例是"**XX科技有限公司 高净值客户服务规范**…本规范为**内部服务标准文件**"
  → 它本就是**内部/员工**材料，权限标为 `staff` 是**正确的**。错的是**意图把它当成客户可见的公司业务资料**。
- `faq` 的样例是 `# Upload contract`（**上传契约测试文档**）
  → 这 2 条是**测试残留**，不是真实 FAQ。故"客服电话/怎么联系"在 faq 里查到的是无关内容、
  相似度暴跌（0.4927→0.1547）——**根因是 faq 类目下装的是测试数据**。
**修正此前的判断**：
- 曾判定"意图识别对了，只是映射到资料有问题"——**方向对，但精确说法应为**：
  意图**识别**正确；**映射链条（意图→类目→权限→语料）三处取值不一致**，
  且其中"公司业务"这一类客户侧**根本没有对应语料**。
- 曾判定"多知识意图拆分无信息增量"——**错误**（已撤回）。真实情况是
  `company_info` 与"本应回答业务问题的语料"之间没有可用映射，
  导致两个意图都退回到同一批 company_info 文档。
**因此"让 category 生效"与"补语料"都不是正确修法**：
- 让 category 生效 → `company_business` 因权限仍取不到（收窄后为空，已实测 4/10 空）；
- 只补语料 → 若新文档权限仍标 `staff`、类目仍手填 `business`，一样取不到。
**正确修法的方向（待产品确认，本轮未改代码）**：
1. 明确"公司业务"这类问题**应由哪些客户可见文档回答**；若无，则**导入客户版本文档**
   并统一类目名为 `company_business`、权限为 `public`；
2. 清理 `faq` 下的测试文档（`Upload contract`），或修正其类目；
3. 让 `public_customer()` 声明的类目与**语料实际存在的类目**对齐（去掉 workflow/risk_public，
   或补上对应文档）；
4. 权限取值需要**枚举化校验**（`staff` 这种既有值不在任何 scope 中，应在导入时即报错）。
**方法论**：本次是"**逐层对账**"的胜利——从"类目没生效"→"收窄不可行"→"权限不匹配"→
"孤儿内容 + 测试残留"，每一步都用真实数据推进，没有停在第一个看似合理的解释上。
若在第一层就动手改 `category` 传参，会引入 40% 答不出率并掩盖真正的权限问题。

### ISSUE-20260914-PGSEARCH-BM25-KEY-FIELD-BLOCKS-INSERT
- 日期：2026-09-14
- 分类：RAG / 检索基础设施（**P0：所有知识库写入失败**）
- 严重程度：P0
**现象**：在 ParadeDB 容器内导入客户语料时，`INSERT INTO knowledge_chunk` 报
```
asyncpg.exceptions.InternalServerError: No key field defined.
```
**影响面远超本次导入**：最小写入（仅 id/document_id/version_id/chunk_index/content 等几列、
不带 embedding）**同样失败**，裸 INSERT（无 RETURNING）也失败 ——
即**该表上任何写入都不行**，包括后台 worker 与上传接口的文档入库路径。
**诊断过程（三次推断，前两次被数据推翻，记录以避免重复走弯路）**：
1. *推断一*：配置为 tsvector、BM25 索引是遗留物，不用的索引在阻拦写入。
   **证伪**：`FULLTEXT_BACKEND = pg_search`，索引确实在用。
2. *推断二*：索引由旧版 DDL 创建，与 0.25.6 元数据不兼容。
   **证伪**：`reloptions = ['key_field=id']` 定义正确，且 BM25 **查询可用**
   （`content @@@ '公司'` 命中）——若 key_field 无法解析，查询同样会失败。
3. *最终定位*：**读写不对称**——读侧正常、写侧失败。索引元数据与运行中的
   扩展版本不一致（索引经扩展升级后未重建），导致写路径无法解析 key_field。
**环境事实**：
```
pg_search 0.25.6 / vector 0.8.5 / PostgreSQL 16（paradedb/paradedb:pg16 镜像）
索引: CREATE INDEX idx_knowledge_chunk_content_bm25 ON knowledge_chunk
        USING bm25 (content) WITH (key_field=id)
reloptions: ['key_field=id']
查询: 正常        写入: 失败（裸 INSERT 亦失败）
```
**处置**：用户决定**保留 BM25**（不用 tsvector 回退）。新增
`scripts/rebuild_bm25_index.py`，安全重建：
- 默认 dry-run；`--apply` 才执行 DDL
- 提供 `--reindex-only`（REINDEX，最轻量，优先尝试）
- `--apply` 走**先建临时索引 → 写入探测 → 成功才替换旧索引**的顺序，
  任一步失败即中止且**不删除原索引**，避免把检索能力一起弄坏
- 重建前后各做一次读写探测，用**结果**证明修好（而非假设）
**方法论教训（本轮最有价值的一条）**：
  **"写入失败"不等于"索引坏了"。** 本次读侧一直正常，仅写侧失败 ——
  若按"索引损坏"直接 DROP+CREATE，属于用重型操作去修一个尚未定性的问题。
  正确顺序是**先做读写分离探测**（`diagnose_bm25_index_health.py` 与
  `diagnose_pg_search_insert_forms.py` 的作用），把"整个索引不可用"与
  "仅写入路径不可用"区分开，再决定用 REINDEX 还是重建。
  另外：两次推断都是"听起来合理"的解释，但都被一个**具体探测**推翻——
  在基础设施问题上，可执行的探测比推理更可靠。
**★ 最终根因（临时表对照实验证明，非推断）**：
```
A bm25 (id, content) WITH (key_field='id')   建索引OK 写入OK   <== 唯一可行形态
B bm25 (content)    WITH (key_field='id')   建索引OK 写入FAIL <== 库里现有形态（故障源）
C bm25 (id, content) 无 WITH                建索引FAIL
D bm25 (content)    无 WITH                 建索引FAIL
```
**`key_field` 指名的列必须出现在字段列表中**（`bm25 (id, content)`），
只写在 `WITH` 里不够（`bm25 (content) WITH (key_field='id')`）。
后者能建索引、能查询（`@@@` 走读路径），但**任何 INSERT 都报 `No key field defined`** ——
这正是"读写不对称"的来源。
**修复**：`scripts/rebuild_bm25_index.py` 按 A 形态重建。
过程中发现 ParadeDB 限制**一张表只能有一个 bm25 索引**
（`a relation may only have one ParadeDB index`），
故"先建新索引再切换"在架构上不可能，替换只能 DROP 后 CREATE；
最终实现为**单事务 DROP+CREATE+写入探测**，失败整体回滚、旧索引自动恢复。
另把该索引纳入 `migrations/versions/0025_knowledge_bm25_key_field.py` 版本管理。

**修复后实测（容器内，导入客户语料后）**：
```
语料现状: company_info 49 chunks / company_business 40 / policy 24 / faq 21   全部 pl=public
intent            query                命中  best     判定
company_info      你们公司叫什么名字        5   0.6246  sufficient
company_info      客服电话是多少          5   0.6416  sufficient
company_business  你们公司的主营业务是什么    5   0.6366  sufficient   <== 修复前 0 命中
company_business  公司提供哪些服务         5   0.5901  sufficient
faq               怎么联系你们           5   0.7067  sufficient
faq               基金申购后多久确认        5   0.6350  sufficient
```
`company_business` 从"0 命中、必然转人工"变为 `sufficient`，
即最初 `你们公司的主营业务是什么` 答案重复/无依据的**语料结构根因已消除**。

**自己引入的一个 bug（一并记录）**：`probe_insert` 原实现无论成败都调用
`session.rollback()`；而重建场景下 DDL 与探测在同一事务，该 rollback 会把刚执行的
DROP+CREATE **一起撤销**，脚本却报告"写入探测成功" —— **假成功**。
改为 `use_savepoint=True`（SAVEPOINT 只回滚探测本身），并用 sqlite 事务对照实验验证：
外层 DDL 保留、探测行未落库；同时验证无脑 rollback 确实会撤销外层 DDL（反例成立）。
**教训**：诊断/修复脚本自身的正确性也要验证，尤其涉及事务边界时——
"看起来修好了"与"真的修好了"在事务语义下可以差很远。

**附带发现**：`scripts/enable_pg_search_bm25.sql` 的建索引语句**整体被注释**，
仓库内 migrations 与 docker/postgres/init 都没有创建 BM25 索引；
即该索引是**手工创建**的、未纳入版本管理 —— 这也是它与扩展版本脱节、
且无人察觉的原因。建议后续把建索引 SQL 纳入 migration 或初始化脚本。

### ISSUE-20260914-INTENT-EXPANSION-DOES-NOT-DIVERGE-RETRIEVAL
- 日期：2026-09-14
- 分类：RAG / 意图规划 / 检索
- 严重程度：P3（**不是 bug，是"冗余设计"**：机制正常但无实际区分力）
**现象**：语料结构问题修复后重跑，`company_info` 与 `company_business` 两个 intent 的
检索结果**仍逐字节相同**（chunk_ids、similarities、命中类目分布全部一致，Jaccard=1.000）：
```
[company_info]     best=0.6366  chunk_ids=[0231b635…, 32ac72d4…, 6d860602…, a528cff4…, d77866fc…]
[company_business] best=0.6366  chunk_ids=[0231b635…, 32ac72d4…, 6d860602…, a528cff4…, d77866fc…]
两条 similarities 也完全相同 = [0.6366, 0.5482, 0.5317, 0.5244, 0.5122]
命中类目分布 = {'company_business': 2, 'company_info': 2, 'faq': 1}（两者相同）
```
**注意：这与修复前的"相同"性质完全不同。** 修复前是因为 `company_business` 权限为 staff
（0 命中、两路都退回到 company_info）；现在是**四类语料都可见、确实召回了混合类目**，
只是两个 intent 召回了**同一批**。
**根因（逐层定位，非推断）**：
1. 两个 intent 的唯一差异在 `normalize_customer_query` 的**尾部扩展词**：
```
company_info     → "…公司名称 公司介绍 联系方式 客户数量"
company_business → "…公司业务 公司服务 服务范围 客户"
```
2. 但 `CustomerRagService` 的**向量腿嵌入的是原始 query**（`embed([query])`），
   不是 normalized —— 故**向量腿对两个 intent 必然完全相同**。
3. 文本腿（pg_search BM25）虽有差异，但拆词后**共同前缀占绝大多数**：
```
共同词 = 贵公 公司 司主 主营 营业 业业 业务（7 个，占 q1 全部词的 64%）
q1 独有 = 公司名称 公司介绍 联系方式 客户数量
q2 独有 = 公司业务 公司服务 服务范围 客户
```
   差异词都是**两类文档中都大量出现的通用业务词**，不足以改变 BM25 排序。
4. 于是"向量腿完全相同 + 文本腿排序趋同" → 融合结果相同。
**结论**：这不是故障，而是**意图设计层面的冗余**——
针对"你们公司的主营业务是什么"这类问题，company_info 与 company_business
在**当前语料**下并不构成两个可区分的检索意图。
（此前一度判为"无信息增量"，后被证伪；现在有了明确机制解释：
 不是"语料不足"，也不是"类目没生效"，而是**这两个意图的检索特征本就高度重合**。）
**此前绕过的弯路（记录）**：
  - 曾判"拆 task 无信息增量"（单样本，已撤回）
  - 曾判"类目没生效导致不分区"（当时确实存在，但修复后仍相同，故非本现象之因）
  - 曾推测"中文下文本腿为空导致扩展词失效" —— **证伪**：
    `FULLTEXT_BACKEND=pg_search` 走 `_rank_text_pg_search`（有中文 bigram 拆词），
    文本腿非空。该推断错在只看了 tsvector 分支的注释就下结论。
**待决（产品语义，未自行改）**：是否应把 company_info / company_business 合并为一个意图，
或让二者检索**不同 scope**（如 company_business 限定 category），
或接受现状（两个 task 产出相同证据，靠生成层给出不同侧重）。
本轮不改代码：这属于意图体系设计，需产品判断。
**顺带确认（本轮修复已生效的证据）**：语料四类全部 pl=public
（company_info 49 / company_business 40 / policy 24 / faq 21），
六个检索探针全部 `sufficient`，`company_business` 由 0 命中变为 0.6366。

### ISSUE-20260914-CORPUS-MISSING-FROM-IMAGE
- 日期：2026-09-14
- 分类：构建 / 工程
- 严重程度：P2（运行期报错，易被误判为脚本 bug）
**现象**：容器内执行 `scripts/import_customer_corpus.py --dry-run` 报四个语料文件
"文件不存在"，但文件在仓库里确实存在。
**根因（两处叠加，任一存在即失败）**：
1. `Dockerfile.api` 只 COPY `app/workers/scripts/migrations`，**没有 COPY knowledge_corpus**
   —— 所以脚本本身能跑（在 scripts 里），但它要读的数据不在镜像内。
2. `.dockerignore` 含 `*.md`，会把四个 `.md` 语料**全部排除**；
   即便补了 COPY 也拷不进去，必须用 `!knowledge_corpus` 与 `!knowledge_corpus/**`
   **两条例外**重新包含（仅写目录名无法让目录内文件生效），且顺序须在 `*.md` 之后。
**修复**：Dockerfile.api 增 `COPY knowledge_corpus ./knowledge_corpus`；
.dockerignore 增上述两条例外。并在导入脚本中，当同名文件全缺失时追加提示，
直接指出"镜像未包含该目录"及检查项，避免下次把构建问题误当成脚本 bug。
**验证**：新增 `tests/test_corpus_image_wiring.py`（4 条）固定该配置：
COPY 存在、两条例外齐备、例外顺序在 `*.md` 之后、导入计划中的文件真实存在。
并**验证测试能失败**：回退修复后 3 failed，恢复后 4 passed。
**教训**：与 ISSUE-20260914-PGSEARCH-BM25-KEY-FIELD-BLOCKS-INSERT 同属
"本地跑得好、容器里缺东西"——两者都说明**容器内的真实验证不可替代**。
另注：`*.md` 这条排除规则本意是"别把文档塞进镜像"，
但它同样排除了**作为数据用的** markdown，这是规则过宽的典型副作用。

### ISSUE-20260914-ORDER-CASE-TESTS-NOT-FOUND-NOT-QUERY
- 日期：2026-09-14
- 分类：测试数据 / 对照用例
- 严重程度：P3（用例误导，非功能缺陷）
**现象**：`compare_graph_vs_pipeline.py` 的固定用例"我的订单 ORD-123456 状态"
在本轮由 OK 变为 DIFF，graph 比 pipeline 多出一句"您当前没有可查询的订单记录。"，
一度怀疑是收窄改动引入的回归。
**查证（真实数据）**：
```
orders 表 9 行，order_no 形如 O98E346E6347D4BCB（O + 15 位十六进制，**无 ORD- 前缀**）
  精确匹配 ORD-123456 = 0
  全库含 "123456" 的 order_no = 0
  work_order.ticket_no 含 "123456" = 0
  **demo 客户（f3e3200b…）名下订单 = 0 条**（现有订单属 3a3f9a87… / 8886f88a…）
```
**结论**：`ORD-123456` **确实不存在**，且 demo 客户名下**没有任何订单**，
故 `fact_not_found` 是**正确**行为。该用例从设计之初就注定走"查不到"分支——
**它测的是降级行为，不是订单查询功能**。
**差异性质**：非回归。graph 走模型循环（4 轮），pipeline 走确定性
`CustomerQueryRouter`；两者 `overall_status` / `clarification` / `requires_handoff`
**完全一致**，仅 task 的 answer 文本不同，且 **graph 更清楚**
（告知"查过了、是没有"，而 pipeline 只说"请补充信息"）。
**待办建议**：
1. 补一条**用真实单号**的用例（需先给 demo 客户造订单），否则"订单查询"路径始终未被覆盖；
2. 或把该用例的 query 语义改为明确"不存在"，避免被误读为查询功能有问题。
**教训**：**用例的 query 必须与被测能力对齐**。这条用例名为"查询订单状态"，
实际长期在测"查不到时的降级"，因而既掩盖了查询路径未被覆盖，
又会在降级文案变化时产生看似异常的 DIFF。

### ISSUE-20260914-FAILURE-HINT-ENCOURAGES-RETRY
- 日期：2026-09-14
- 分类：Agent 工具循环 / 提示词
- 严重程度：P2（浪费 LLM 调用；且是既有问题的另一面）
**现象**：`我的订单 ORD-123456 状态` 模型工具轮轨迹：
```
decision[r1] get_my_order_status  args={'query': '订单 ORD-123456 状态'}   executed
decision[r2] get_my_order_status  args={'query': 'ORD-123456'}            executed
decision[r3] get_my_order_status  args={'query': '订单号 ORD-123456'}      executed
decision[r4] rejected_duplicate    args={'query': 'ORD-123456'}   ← 与 r2 重复，被闸门拦下
stopped_reason = duplicate_call_skipped
```
模型在**单号确实不存在**的情况下，连续 3 次改写参数写法后**退回重复**，2 轮完全无效。
**根因**：`fact_not_found` 的失败提示写到：
> "未查到该对象（单号可能不存在或不属于当前客户）。**请勿改调其他工具**；
>  可在本工具内**换一个更明确的查询参数**，或直接停止（让下游提示用户补充/转人工）。"

**"换一个更明确的查询参数"这句在鼓励重试** —— 但本场景的问题**不在参数明确度**，
而在"该单号就是不存在"。对确定性查询工具，单号已完整给出仍查不到时，
**改写写法不可能改变结果**（历史已记录同一工具同参数重复调用必然同结果，
见 ISSUE-20260914-TOOL-FAILURE-NOT-FEED-BACK 的重复调用闸门）。
**与既有问题的关系**：ISSUE-20260914-FAILURE-HINT-MISLEADS-TOOL-SWITCH 记录的是
提示**误导模型换工具**；本条是提示**误导模型换参数写法**——同一处文案的两面。
**建议修法（未改，待定）**：把提示改为明确语义，例如
"若已给出完整单号仍查不到，说明该单号不存在；**不要改写参数重试**，直接停止"。
预期收益：省去 2 轮无效 LLM 调用。
**注意**：需与"参数确实不明确"的场景区分（如"查我的订单"未给单号，
此时**应当**提示补充单号），故不能简单删掉"换参数"这句，而应按错误类型分文案。

### ISSUE-20260914-CLARIFICATION-EMPTY-TASK-ANSWER
- 日期：2026-09-14
- 分类：客服应答 / 用户体验
- 严重程度：P2（用户无法区分"没有"与"没查"）
**现象**：`needs_clarification` 场景下，pipeline 的 task.answer **为空字符串**：
```
[pipeline] task=t1 intent=order_status status=needs_clarification error=fact_not_found
           answer=''                    ← 空
           clarification='请补充更明确的查询对象或订单/产品信息。'
[graph]    task=model-tool-1 ... answer='您当前没有可查询的订单记录。'   ← 有内容
```
最终用户看到的 pipeline 回答只有"下一步：请补充更明确的查询对象或订单/产品信息。"
**用户无法判断**是"系统查过了、确实没有"，还是"系统根本没查、需要我补信息"。
**根因**：`CustomerQueryRouter` 在 `fact_not_found` 时返回 `answer=""`（或未透传），
task 层只带澄清文案，未附"已查询但未命中"的事实说明。
**与既有条目的关系**：ISSUE-20260914-FAILED-VS-CLARIFICATION-MISMATCH 已统一了
"失败 → needs_clarification"的**状态语义**；本条是**文案/信息量**层面的缺口——
状态对了，但告诉用户的信息不足。
**待决（产品语义，未改）**：是否让 pipeline 与 graph 对齐？
若对齐，应是 **pipeline 补上"未查询到记录"这句**（而不是 graph 去掉），
因为 graph 的表达对用户更友好。但这会改变对外可见的回答文本，故需产品确认。

### ISSUE-20260914-POLICY-INTENT-NEVER-TRIGGERED
- 日期：2026-09-14
- 分类：意图识别 / 知识覆盖
- 严重程度：P1（**24 个 chunk 的合规资料用户问不到**）
**现象**：构建 scope 收窄实验时需按意图准备探针问句，逐个用 `CustomerIntentPlanner`
反查 31 条候选，结果 **`policy` 意图无任何问句可触发**：
```
"什么是双录"             -> ['unknown']
"什么是投资者适当性管理"   -> ['unknown']
"风险等级怎么划分"        -> ['faq', 'risk_public']
```
**影响**：`policy` 类目下有 **24 个 chunk**（《个人投资者适当性管理指南》等），
但用户问合规/适当性类问题**走不到这批资料**——不是检索问题，是**意图识别规则的覆盖缺口**。
连带：双录、适当性、反洗钱等客服高频问题均无法命中该意图。
**同期发现的意图触发面问题**（同一轮反查所得）：
```
company_info    14 条问句可触发（触发面最宽）
company_business 3 条 / faq 4 条
workflow         1 条     <- 仅 1 条
risk_public      1 条     <- 仅 1 条
policy           0 条     <- 完全无
unknown          "客服电话是多少""开户需要什么材料""什么是基金定投"
                 "什么是业绩比较基准""基金和理财产品有什么区别" 等大量自然问句
```
即意图规则的触发面**远窄于预期**，且 `unknown` 吸收了大量本应被识别的问句。
**待办（未改）**：
1. 查 `customer_intent.py` 中 `policy` 的关键词/模式是否配置或过于狭窄；
2. 复核 `unknown` 吸收的问句是否应归属某意图（尤其"客服电话""开户材料"这类高频问题）；
3. 补测试固定"各意图至少能被 N 条典型问句触发"，避免规则被改窄而无人察觉。
**教训**：**意图从不触发，比意图映射错误更隐蔽** ——
映射错会表现为答非所问（有输出、可发现），从不触发则表现为"走到别的意图"或"unknown"，
看上去像识别能力不足，实则是规则覆盖缺口。

### ISSUE-20260914-STATUS-MACHINE-LACKS-CONSISTENCY-DIMENSION
- 日期：2026-09-14
- 分类：状态机 / 答案质量 / 架构
- 严重程度：P1（**已知矛盾无法被现有任何状态表达**）
**现象**：`我有没有风险限制` 的答案自相矛盾：
```
您的账户当前未显示风险限制，但存在一条待处理风险记录。
✅ 是的，您有风险限制。
```
前半句来自 `get_my_risk_restriction`（**事实**：账户未显示限制），
后半句来自 `search_customer_knowledge`（**制度**：C1–C5 会形成限制）。
两句各自都对，拼在一起就成了矛盾。而 `overall_status` 报 **`completed`**。
**根因（维度缺失，非判定错误）**：
`CustomerTaskExecutor.summarize()` 的输入只有每个 task 的 `status` 字段：
```
succeeded / partially_answered / needs_clarification / failed / blocked
关键分支：if succeeded == len(results): status = "completed"
```
两个 task **确实各自都成功了**（查到了事实、也查到了制度），
故必然判为 `completed`。实测复现：
```
results = [TaskExecutionResult(intent="risk_restriction", status="succeeded", answer="您的账户当前未显示风险限制"),
           TaskExecutionResult(intent="risk_public",      status="succeeded", answer="是的，您有风险限制")]
CustomerTaskExecutor.summarize(results) -> status="completed"
aggregate answer = '您的账户当前未显示风险限制\n是的，您有风险限制'
```
**所以不是"状态机判错了"，而是"它根本没有在看这件事"**：
现有维度是 **[完成度]**，缺少 **[一致性]**。
矛盾发生在**把多个 task 的答案拼成一段时**，而拼接结果不进入任何判定。
**与既有条目的关系**：
- `ISSUE-20260914-NO-PARTIAL-STATE` 补的是"答了但没答全"（`partially_answered`）——仍是**完成度**维度；
- `ISSUE-20260914-COMPLETED-STATUS-MASKED-BY-DROPPED-TASK` 补的是 task 丢失——也是**完成度**；
- 本条是**第三个正交维度**：完成度满分、但内容自相矛盾。
三者叠加说明：单靠 `overall_status` 一个字段，**无法同时表达完成度与自洽性**。
**为什么这次不能靠"再加一个状态值"解决**（关键判断）：
`partially_answered` 之所以可行，是因为"答案自述资料不足"可由**生成文本自证**
（grep 固定标记即可判定）。而"自相矛盾"需要**跨 task 的语义比对**——
没有廉价、确定的判据。若用 LLM 判定，则引入与我们一直在规避的同类问题
（judge 误判、不可复现、成本）。**故不应草率新增状态值。**
**待决方向（未实现，需产品与架构共同确认）**：
1. **降权**：多 task 拼接时，若两类证据**语义角色不同**（事实 vs 制度），
   不并排陈述，而是结构化分节（"您的账户现状" / "制度说明"），从**生成层**消除矛盾；
2. **标注来源**：回答中显式标注每条结论的依据类型，让用户能分辨"你的账户"与"制度规定"；
3. **不新增状态**：`overall_status` 保持表达完成度，一致性作为**离线评测指标**
   （如定期抽检多 task 答案的自洽性），而不是塞进在线状态机。
**教训**：**一个字段承担不了两个正交维度。**
每当"状态看起来是对的、但答案明显不对"时，要先问"状态机有没有在看这件事"，
而不是急着加一个状态值——加值只能覆盖**它能测量的**那一类问题。

### ISSUE-20260914-ORDER-NO-PATTERN-MISSES-REAL-FORMAT
- 日期：2026-09-14
- 分类：意图识别 / 实体抽取
- 严重程度：P1（用户粘贴**真实单号**时识别不出，查询与提示同时失效）
**现象**：修 `fact_not_found` 提示词时需要一个判据——"标识符是否已给出"，
以便区分"该对象不存在（应停止）"与"未给单号（应提示补充）"。
于是检查既有 `extract_entities`，发现它**认不出生产真实单号**：
```
"我的订单 O98E346E6347D4BCB 状态"  -> {}      # 真实格式，识别不出
"O98E346E6347D4BCB"              -> {}
"我的订单 ORD-123456 状态"         -> {'order_no': 'ORD-123456'}   # 反而认得这个
```
**根因**：`customer_intent.py` 的 order_no 正则只认 `ORD-` 前缀或"订单号:XXX"显式写法，
而生产实际单号由 `trading_service.py` 生成：`f"O{uuid4().hex[:16].upper()}"`
即 **`O` + 16 位十六进制**（实查 orders 表：`O98E346E6347D4BCB`、`OA0BA915024484DE8`、
`O3ABCAF6FE53C4DA1` …），**与该正则完全不匹配**。
更讽刺的是：`ORD-123456` 这个**不存在**的格式能被识别，
而用户从 APP 复制的**真实**单号反而识别不了。
**影响**：
1. `extract_entities` 的 `order_no` 在这类问句上恒为空 → 依赖它的下游拿不到单号；
2. 本轮新增的"标识符是否已给出"判据会误判为"未给出"，从而给出**错误的提示**
   （让模型以为用户没给单号、应继续追问，而用户其实已经给了）；
3. 这同时解释了 `ORDER-CASE-TESTS-NOT-FOUND-NOT-QUERY` 中
   "订单用例长期只测到查不到"的部分原因——**用例单号本身就不是真实格式**。
**修复**：正则补上 `O[A-F0-9]{12,20}`（兼容大小写，因 `re.IGNORECASE`），保留原有形态。
**验证**：
```
真实格式 7/7 识别：O98E346E6347D4BCB / O6CD80C29474147D5 / O3ABCAF6FE53C4DA1
                  / ORD-123456 / "订单号: ABC123456" / WO-999
误匹配 0/11：你好 / 我的资产有多少 / OpenAccount / OOO / OK / 怎么看 …
```
**教训**：**测试数据用了"看起来像"的格式，掩盖了真实格式不被支持。**
`ORD-123456` 是人为编造的（带有 `ORD-` 前缀），生产却用 `O`+十六进制；
若用例当初就用真实单号，这个问题会在第一次跑通时暴露。
**这与 `ORDER-CASE-TESTS-NOT-FOUND-NOT-QUERY` 是同一教训的两种表现**：
用例与被测能力的真实形态脱节，导致既测不到功能、又掩盖缺陷。

### ISSUE-20260914-FAILURE-HINT-RETRY-FIX
- 日期：2026-09-14
- 分类：Agent 工具循环 / 提示词
- 严重程度：P2（浪费 LLM 调用）
**承接** `ISSUE-20260914-FAILURE-HINT-ENCOURAGES-RETRY`（该条记录了问题本身）。
**修复**：`fact_not_found` 的提示按"**标识符是否已给出**"分两种文案：
- 已给出 → "该对象不存在，**不要改写参数重试**；**请勿改调其他工具**；直接停止"
- 未给出 → "**请勿改调其他工具**；若用户尚未提供明确标识符，可停止并让下游提示补充"
判据复用既有的 `extract_entities`（而非自造正则，避免与既有口径不一致）。
**两条约束缺一不可**（这是本修复踩到的坑）：
初版"已给出"分支只写了"不要改写参数重试"，**漏掉了"请勿改调其他工具"**，
立即被既有测试 `test_failure_hint_forbids_switching_tools` 抓到。
即：① 禁止换工具（防 `FAILURE-HINT-MISLEADS-TOOL-SWITCH` 的回归）
    与 ② 不再鼓励重试（本条目标）必须同时满足。
**另一个被测试抓到的点**：`test_failed_tool_result_is_recorded_as_clarification`
原本断言提示须含 "重复" 或 "换" 字样 —— 那是在**固定旧的鼓励重试文案**，
与该测试自身声明的意图（"验证失败结果被保留"）无关。
已改为断言新语义，并**新增一条互补测试**固定"未给出标识符时不得禁止重试"，
避免一刀切改文案时误伤另一种场景。
**两处"测试固定了错误行为"的再确认**（本议题第 3、4 次）：
- `test_unknown_intent_does_not_enter_model_tool`：用"什么是双录"当 unknown 样例（已修）
- `test_failed_tool_result_is_recorded_as_clarification`：断言旧提示文案（已修）
**教训**：测试里对**文案字面**的断言，容易在文案本应改进时变成阻力；
断言应尽量指向**语义**（"必须禁止换工具"）而非具体措辞，
本修复即改为断言两条关键语义句同时存在。

### ISSUE-20260914-GRAPH-MODEL-TOOL-IGNORES-DEPENDS-ON
- 日期：2026-09-14
- 分类：Agent 编排 / 图与管线不一致
- 严重程度：P1（**graph 侧依赖与事实传递完全失效**，已知会答错）
**现象**：`我有没有风险限制` 用 `risk_c4_demo`（等级 C4）实测：
```
[pipeline] 您的风险承受能力等级为 C4 → 您可投资 R1 至 R4；不可购买 R5   ✅ 正确
[graph]    任务 t1 的答案里明明有 'C4'
           但 t2 输出 '当前引用中未包含您的具体风险评估得分或类型，因此无法确认'  ❌ 错误
```
同一 query、同一客户，**pipeline 对、graph 错**。
**根因（已读码确认，非推断）**：`SupervisorGraph.model_tool_node` 把整个 plan
压成**一串意图名**交给模型循环，**没有任何按 task 逐条执行的循环**：
```python
outcome = await loop.run(
    query=state.combined_query or state.user_message,
    planned_intents=[t.intent for t in plan.tasks],   # 只取 intent 名，丢了 depends_on
)
```
而 `CustomerModelToolLoop.run` 仅把它**拼进提示词**（"该问题经规则分析需要覆盖以下方面"）。
故：
  - `depends_on` 对 graph **完全不可见**（既不参与调度，也不传答案）
  - pipeline 的 `_dependency_context`（确定性注入前序事实）**在 graph 侧根本没被调用**
  - 后果：模型只能"自己记住"前序事实。C1 那次碰巧把 C1 拼进了检索 query 所以答对，
    C4 那次没拼所以答错 —— **结果取决于运气，而非机制**。
**两条路径的行为对照**：
```
                      pipeline                      graph
  执行单位             每个 task 一次 _execute_one     一次循环产出所有 task
  depends_on          参与调度门控 + 注入答案          完全不可见
  _dependency_context 生效                          未调用
```
**分层执行的轮次成本（实测推导，用于定预算）**：
C1 与 C4 的真实轨迹均为 4 轮、`stopped_reason=budget_exhausted`：
```
  r1 = get_my_risk_restriction（事实，1 轮）
  r2~r4 = search_customer_knowledge（制度，3 轮，且仍撞上限）
```
已读码确认：**每个工具调用都占 1 轮 LLM**（即使是 `get_my_risk_restriction`
这类确定性工具，也必须由模型先决定调用，见 customer_model_tools.py:389/506）。
故若改为按依赖分层串行：
```
  层1 事实（确定性工具）  ≈ 1 轮
  层2 制度（知识检索）    >= 3 轮且实测不足 → 建议 4 轮
  合计 ≈ 5 轮 > 现状 4 轮
```
**三个选项（待决，本轮未改 graph）**：
- **A 每层独立预算**（总 5 轮）：各层都给够，LLM 调用增加约 25%
- **B 总预算不变(4) 按层均分**（各 2 轮）：制度层 2 轮，**可能比现状更差**
- **C 让事实层不占 LLM 轮次**：确定性工具按计划直接调用、不经模型决策。
  这正是 pipeline `_execute_one` 的做法（确定性工具不调 LLM）。
  若能成立，则"事实层"零 LLM 成本，制度层可拿全部 4 轮 —— **总成本不变且行为更好**。
**建议**：优先验证 C 的可行性。C 的实质是"**确定性工具不该由模型决定要不要调**"——
本例中 `risk_restriction` 的调用是计划里既定的事实（规划器已识别该意图），
让模型再决定一次既浪费轮次、又引入"忘记调用/调错参数"的风险（C4 的失败正属后者）。
**注意**：本轮改动**未触及 graph**（graph 仍为影子路径未切流），
故该缺口不影响当前 pipeline 的正确性；但**切流前必须解决**，否则 C4 类问题会直接暴露。

### ISSUE-20260914-GRAPH-DEPENDS-ON-FIXED-BY-LAYERING
- 日期：2026-09-14
- 分类：Agent 编排 / 图与管线一致性
- 严重程度：P1（修复）
**承接** `ISSUE-20260914-GRAPH-MODEL-TOOL-IGNORES-DEPENDS-ON`（缺口定位）与
`ISSUE-20260914-GRAPH-PREEXEC-FACT-INTENTS-ATTEMPT-REVERTED`（错误落点的回退）。
**修复落点改为正确的位置**：不在 `CustomerModelToolLoop` 内做"确定性工具预执行"
（那与"模型自主调度"的职责边界冲突，且与既有测试假设不兼容，已回退），
而是**在 `SupervisorGraph.model_tool_node` 按 `depends_on` 分层**。

实现要点：
1. `_task_layers(tasks)`：按 `depends_on` 拓扑分层，同层并行、层间串行。
   循环依赖/悬空依赖时把剩余 task 一并放入最后一层 —— **宁可退化为并行也不丢 task**
   （并有测试固定"不丢 task"）。
2. 逐层调用模型循环，`planned_intents` 只传**本层**意图（此前传全部）。
   这样 `depends_on` 第一次真正参与了调度。
3. 层间传递：把之前各层已产出的答案经 **`prior_facts`** 传给下一层
   （`run()` 新增参数），文案与 `CustomerTaskExecutor._dependency_context` 一致。
4. **事实与检索串严格分离**：`prior_facts` 只进入提示块，`query` 始终保持用户原问句。
   这是对 `ISSUE-20260914-DEP-CONTEXT-POLLUTES-RETRIEVAL` 教训的直接应用 ——
   把事实拼进 query 会被当作检索内容，实测 best_sim 0.4643→0.3753。
5. 单层失败不拖垮整体；某层无任何结果则停止后续层（后续层依赖的正是这些事实）。

**实测验证（假 loop 捕获各层参数，端到端）**：
```
层1: planned_intents=['risk_restriction']  query='我有没有风险限制'  prior_facts=(空)
层2: planned_intents=['risk_public']       query='我有没有风险限制'  prior_facts='【前序查询结果…】
                                                                     [risk_restriction] …等级为 **C4**。'
```
即：**第 2 层拿到了第 1 层的 C4，且 query 未被污染** —— C4 用例的失败条件已消除。
分层结果（真实规划器）：
```
"我有没有风险限制"          -> 2 层（risk_restriction -> risk_public）
"我的订单 … 处理流程"       -> 2 层（order_status -> workflow）
"你们公司的主营业务是什么"   -> 1 层（两个知识意图无依赖，保持并行）
```
**与 pipeline 的对照**：此前 pipeline 正确、graph 错误；现在 graph 也按依赖串行并传递事实，
两条路径的**机制**对齐（此前只有 pipeline 有传递）。
**新增测试** `tests/test_graph_task_layering.py`（7 条）：分层顺序、无依赖单层、
循环依赖不丢 task、第 2 层收到 C4、事实不污染 query、单层无前序事实、各层结果合并。
`tests 442 passed`（原 435 + 7），**既有测试零改动** —— 印证"落点选对后不再需要打补丁"。
**方法论印证**：上一轮在同一功能上连打三版补丁都打破既有约束，回退并换落点后
**一次通过且无需改任何既有测试**。这正是"补丁打不动时应质疑方案本身"的正面例证。

### ISSUE-20260914-RISK-LEVEL-NOT-READ
- 日期：2026-09-14
- 分类：RAG / 风险数据
- 严重程度：P1（制度 task 拿不到等级 → 只能编造个人事实）
**现象**：`我有没有风险限制` 的答案自相矛盾——"您的账户当前未显示风险限制"
与"是的，您有风险限制"并存，而 `overall_status` 报 `completed`。
**根因**：`CustomerQueryRouter._risk_restriction` 只读
`account.risk_restriction_status`（二进制 `none`/`restricted`），
**从不读** `customer_risk_assessment.risk_level`（C1–C5）。
于是下游 `risk_public`（制度说明）拿不到客户等级，却要回答"我有没有限制"，
只能编造。二者是**两个不同概念**：前者是"账户是否被风控冻结"，
后者是"你的风险承受等级"，用户问"我有没有风险限制"时两者都相关。
**修复**：补读风险等级，并按测评状态区分措辞：
`active`（已定级）/ `provisional`（未完成正式测评，结果可能调整）/ 缺失。
**验证**：实测 `risk_c1~c5_demo` 五个客户等级全部正确读出。
**记录缺口**：本条目在代码注释中被引用多时，但此前**未在本文留下独立条目**
（提交信息引用了不存在的 ID），现补录。

### ISSUE-20260914-RULE-TASK-ASSERTS-PERSONAL-FACT
- 日期：2026-09-14
- 分类：生成 / 合规
- 严重程度：P1（对客户账户状态做无依据断言）
**现象**（C4 客户，graph）：
```
您的账户当前未显示风险限制，……      <- 事实（另一 task 查库所得）
是的，您有风险限制。                 <- 制度 task 顺着问句断言  ✗ 自相矛盾
若您是 C2… 若您是 C4…               <- 举假设例子却不提客户真实等级
```
**根因**：`policy` / `risk_public` 检索的是**制度文本**，**无从知道客户个人状态**，
但其 query 是用户原问句（"我有没有风险限制"），模型便顺着问句断言个人事实。
**关键判断：不靠改写检索 query 解决**。实测把"我有没有风险限制"改成
"风险限制的制度规定"，best_sim 从 **0.5820 掉到 0.5242**（跌破 sufficient）——
改写丢掉了原问句的语境词。故只在**生成提示**加角色约束，检索串保持不变。
**修复**：`customer_rag_answer.generate()` 对 `_RULE_INTENTS = {policy, risk_public}`
追加角色约束：只陈述制度如何规定、**绝不要**用"您有/您没有…"断言个人事实、
**不要**用"若您是 C2…"假设例子替代结论。
**验证**：实测"是的，您有风险限制"消失，语气由个人断言改为制度陈述。
新增 `tests/test_rule_task_role_constraint.py`（4 条）。

### ISSUE-20260914-DEP-CONTEXT-POLLUTES-RETRIEVAL
- 日期：2026-09-14
- 分类：RAG / 检索
- 严重程度：P1（**我自己引入的回归**）
**现象**：实现 task 答案传递（`_dependency_context`）后，把前序事实**拼进了
`item.query`**。但 `query` 在知识类 task 里是**检索串**——
`_bounded_react_knowledge` 取 `item.query.strip()` 后直接传给
`search_customer_knowledge`。于是"【前序查询结果（请基于这些事实作答，不要臆造）】"
这段**指令文本本身被送去知识库检索**。
**实测影响**：`'工单的处理政策是什么'` 的 best_sim **0.4643 → 0.3753**
（-19%，逼近 `reject=0.35`）；检索串长度 10 → 82。
**影响面**：不止新增的 `risk_public`，**既有的 `workflow`/`faq`/`policy` 依赖场景
同样中招**（枚举出 6 个 (task,依赖) 组合），即对既有行为的回归。
**修复**：新增 `IntentTask.dependency_context` **独立字段**——
检索用 `query`（纯问句），生成用 `gen_query = query + dependency_context`。职责分离。
**教训**：**检索串与生成提示必须分离**。这是本议题反复出现的一类错误的两面：
既要防止"不该加的加进检索串"（本条），也要防止"该保留的从检索串里拿掉"
（见 `RULE-TASK-ASSERTS-PERSONAL-FACT` 里否决的改写方案）。

### ISSUE-20260914-GRAPH-GENERATION-MISSING-PRIOR-FACTS
- 日期：2026-09-14
- 分类：Agent 编排 / 生成
- 严重程度：P1（事实传到了提示层、却没到生成层）
**现象**：graph 的制度 task 通篇讲通用规则（"C1投资者仅可购买R1产品…"），
**完全没提客户真实等级 C4**，还自认"资料未完全覆盖"（`partially_answered`）。
而 pipeline 同场景正确："C4 等级客户可直接购买 R1 至 R4"。
**根因**：分层事实只进了 `model_tool_node` 的**提示层**（`prior_facts`，
用于指导选工具/改写 query），而 `generate_node` 对所有 task 一律用
`state.react_query or state.user_message`，**看不到**前一层查到的事实。
pipeline 之所以对，是因为 `_bounded_react_knowledge` 会把
`item.dependency_context` 附到生成 query 上；graph 侧此前没有对应处理。
（另确认：`CustomerModelToolLoop` **完全不调用答案生成器**，
只返回原始工具结果，生成发生在后面的 `generate_node` —— 故事实必须**跨节点传递**。）
**修复**：
- `_tag_layer_results` 把本层 `prior_facts` 写入每条结果的 `data.prior_facts`。
  **放 `data` 而非 AgentState 顶层键**：`task_results` 是自由形态的 dict 列表，
  其内部键不会被 LangGraph 过滤（吸取 `AGENT-STATE-SILENTLY-DROPS-UNDECLARED-KEYS` 的教训）。
- `generate_node` 读取并并入**生成** query。**只影响生成、不影响检索**。
**验证**：实测 `risk_public` 的生成 query 变为
`'我有没有风险限制\n\n【前序查询结果…】\n[risk_restriction] …等级为 **C4**。'`；
端到端 graph 输出"C4 进取型投资者可购买 R1 至 R4（引用 1 第35条）"，
`overall_status` 由 `partially_answered` 变为 `completed`（与 pipeline 一致）。

### ISSUE-20260914-AGENT-STATE-SILENTLY-DROPS-UNDECLARED-KEYS
- 日期：2026-09-14
- 分类：LangGraph / 状态管理
- 严重程度：P1（**静默失效，不报错不告警**）
**现象**：分层执行后，`L1` 只有 1 次真实调用却显示 `rounds=5`，
且两个 task 的 decision 序列**完全相同** —— 说明它们都拿到了"合并后的整体轨迹"，
按层附加逻辑没生效。task_id 前缀与停止原因标注均已生效，仅轨迹仍混叠。
**根因**：`model_tool_layer_traces` **未在 `AgentState` 中声明**。
LangGraph 只把 State **已声明字段**并入状态，**未声明的顶层键被静默丢弃**。
于是 runner 的 `by_layer` 恒为空，每个 task 都走 fallback。
**为何单元测试没抓到（关键教训）**：既有测试是"直接调用 `model_tool_node`
并把返回的 patch **手工合并**进响应"，**绕过了真实图的状态归并** ——
而状态的归并规则（哪些键保留）正是被测对象。所以键"看起来"存在、测试通过，
线上链路里却已被丢弃。
> **"节点返回了某键" ≠ "状态里能读到该键"。**
**修复与加固**：
- `AgentState` 增加该字段。
- 新增"用**真实 StateGraph** 跑一遍并断言键仍在最终状态"的测试。
- 新增"节点返回的每个键都必须已在 AgentState 声明"的防复发断言。
- **验证测试能失败**：删掉该字段后 2 failed，恢复后通过。
**同时暴露的工程教训**：这个"轨迹混叠"缺陷我**修了两次**——
第一次改了 runner 逻辑并加测试（通过但实际无效），第二次才发现真因。
原因是**验证手段与被验证链路不一致**，与"本地跑得好、容器里不行"同类。

### ISSUE-20260914-POLICY-TRIGGER-OVERBROAD
- 日期：2026-09-14
- 分类：意图识别
- 严重程度：P2（误分类导致正确答案丢失）
**背景**：修复 `POLICY-INTENT-NEVER-TRIGGERED` 时按"该意图承载的内容"扩展触发词，
但**第一版**加了裸词 `"规定"`，导致**过度触发**：
```
"开户有什么规定" -> ['policy']    <- 用户问的是【流程】！
```
用户缺"流程"二字使 `workflow` 未触发，而 policy 被"规定"拉走。
这属**误分类**，比"多一个 task"更糟——**正确答案丢失**。
**第二版**收紧为限定搭配（监管规定/管理规定/法律法规/制度规定），
又**过度收紧**：`"投资有什么规定"` 退化成 `['unknown']`、`"公司有什么规定"` 丢掉 policy。
**第三版（最终）**：
- policy 补制度域搭配：投资规定 / 投资有什么规定 / 有什么规定 / 规定有哪些
- workflow 补齐流程类说法：开户 / 办理 / 申请 / 需要什么材料 / 需要哪些材料
结果：
```
"开户有什么规定" -> ['policy', 'workflow']   同时命中（收集式匹配，正确）
"投资有什么规定" -> ['policy']
"赎回有什么规定" -> ['business_operation', 'policy']
```
**教训**：触发词是**精确度与召回率的权衡**，往往一次改不到位，
需要**双向验证**：既验"该命中的命中"，也验"不该命中的不命中"。
新增 6 条测试（3 参数化 + 3 断言）固定该平衡。

### ISSUE-20260914-RISK-RANK-RULES-DIVERGED
- 日期：2026-09-14
- 分类：架构 / 一致性
- 严重程度：P2（多处口径分叉，有安全语义差异）
**现象**：同一套"风险等级排名 + 比较"在 **4 处各写一遍，且默认值已经不一致**：
```
suitability_service.py   LEVEL / _level()    缺失默认 1
trading_service.py       内联 dict           缺失默认 1（下单硬门禁）
api/admin.py             内联 dict           **缺失默认 0**（据此统计 missing_risk）
api/profile.py           内联 risk_order     产品缺失 99 / 上限缺失 0（fail-closed）
```
**风险**：将来在回答侧再写一份（如"你能买什么"的推导），会出现
"回答说能买、下单却被拒"这类口径不一致。
**修复**：收敛到 `suitability_service` 的 `risk_rank()` / `is_suitable()` 作为唯一权威实现；
其余位置改为引用。`risk_rank` 对缺失/未知返回 **0**（区别于历史 `_level` 的默认 1），
使调用方能区分"缺失"与"最低等级"——这是 admin 统计 `missing_risk`
与门禁拒绝都需要的语义。
**保留 `profile` 的 fail-closed 语义**：原为产品缺失→99、上限缺失→0，
转换时若误用 `risk_rank(p) or 1` 会变成 fail-open（**放行未知产品**），属安全回归。
故保留 `or 99` 并加测试固定。
**验证**：全仓已无重复的内联等级字典（仅剩权威定义一处）；
新增 7 条测试，含"不得再内联字典"与"fail-closed 不得被改成 or 1"的断言。

### ISSUE-20260914-NO-TASK-ANSWER-PASSING
- 日期：2026-09-14
- 分类：Agent 编排
- 严重程度：P1（"用事实套规则"在系统里做不了）
**现象**：`depends_on` 此前只做**执行顺序门控**
（`ready = all(dep in results for dep in item.depends_on)`），
最多在 `workflow/faq/policy` 分支读一下依赖的 `intent` 用来选工具名，
**从不把前序 task 的答案传下去**。
**后果**：像"用客户风险等级（事实）套制度规则（知识）"这类**必须依赖前序结论**的任务
无法实现——制度 task 拿不到等级，只能空谈规则甚至编造
（见 `RISK-LEVEL-NOT-READ`、`RULE-TASK-ASSERTS-PERSONAL-FACT`）。
**与既有硬编码写法的区别**：`business_operation` 分支确实用了前序内容，
但那是"遍历 results 按 intent 找"的**隐式耦合**，与 `depends_on` 声明无关。
**修复**：新增 `CustomerTaskExecutor._dependency_context()`，
按 `depends_on` **显式声明**注入前序答案：
```
【前序查询结果（请基于这些事实作答，不要臆造）】
[risk_restriction] 您的账户当前未显示风险限制。您的风险承受能力等级为 **C4**。
```
规划器同时为 `risk_public` 声明依赖 `risk_restriction`。
**配套教训**：传递时**必须与检索串分离**——初版把它拼进 `query` 导致检索质量下降
（见 `DEP-CONTEXT-POLLUTES-RETRIEVAL`）。

### ISSUE-20260914-TOOL-FAILURE-DROPPED
- 日期：2026-09-14
- 分类：Agent 工具循环
- 严重程度：P2（失败语义丢失，导致误转人工）
**现象**：旧实现下，工具调用失败时**不产出 task 结果**，只把失败原因回灌给模型。
**后果**：`outcome.results` 为空 → `_route_after_model_tool` 判定"没拿到结果"
→ 回退到确定性 ReAct（对 `order_status` 却走知识检索）→ 检索不到 →
`low_confidence` → **误转人工**。
实测：`我的订单 ORD-123456 状态` 的 graph `requires_handoff=True`，而 pipeline 为 `False`。
**修复**：失败结果**保留为 task 结果**，语义与管线对齐：
`fact_not_found` / `ambiguous_product` / `low_confidence` → `needs_clarification`
（附"请补充更明确的查询对象或订单/产品信息"），其他 → `failed`。
**验证**：`tests/test_customer_model_tools.py` 有对应断言（失败结果须保留、
状态须为 `needs_clarification`、不得标 `succeeded`）。

### ISSUE-20260914-GATE-EMBEDDING-MISMATCH
- 日期：2026-09-14
- 分类：RAG 证据门控
- 严重程度：P2（口径二义，跨环境判定翻转）
**现象**：同一 query 在本地判 `low_confidence`、容器内判 `sufficient`，
一度怀疑是镜像/数据不一致。
**根因（排查后澄清）**：`CustomerRagService.answer` 中
**门控相似度取自向量腿**（`query_embedding` 来自**原始 query**），
而**检索串**用的是 `normalized_query`（含意图扩展词）。
即门控与"实际返回的证据"来自**两条不同的口径**，边界很窄，微小差异即翻转。
**实测澄清（重要）**：本地与容器的 `best_sim` 实际一致（0.5045 vs 0.5046，仅浮点噪声），
真正的差异来自**知识库数据不同**（本地 5433 vs 容器库），而非代码。
**处置**：在 `customer_rag_service.py` 明确注释了三条口径
（向量腿用原始 query / 文本腿用 normalized / 门控只采信向量腿），
消除"门控到底算哪个串"的二义。
**教训**：**同一 query 在两个环境判定不一致时，先怀疑"两侧输入不同"，
而不是"模型不稳定"。** 本例两侧输入确实不同（库不同），代码口径也确有二义。

### ISSUE-20260914-MODEL-REWRITE-WORSE-THAN-TEMPLATE
- 日期：2026-09-14
- 分类：Agent 工具循环 / 方法论
- 严重程度：P3（**结论已被部分推翻**，保留作反面案例）
**原始结论**：曾判断"模型自主改写 query 不如原有的 2 条固定模板"，
据此倾向保留硬编码模板。
**证伪**：后续用 `scripts/compare_rewrite_effectiveness.py` 扩大样本后发现，
**问题式改写实际上是成功的**，原判断源于**单样本**观察（且当时未排除生成随机性）。
**保留原因**：这是本议题上"单样本下机制性结论"的典型错误之一
（另两次：误判"多知识意图拆分无增量"、误判"图有合规问题"），
三次的共同形态都是**用一个样本的差异去推断机制**。
**教训**：**模型改写是否更差，必须用多样本对比**；
且应先排除随机性（如温度）再判定 LLM 输出缺陷。

### ISSUE-20260914-GRAPH-PREEXEC-FACT-INTENTS-ATTEMPT-REVERTED

### ISSUE-20260914-GRAPH-PREEXEC-FACT-INTENTS-ATTEMPT-REVERTED
- 日期：2026-09-14
- 分类：Agent 编排 / 工程决策
- 严重程度：P3（**尝试未采纳**，记录以免重复走弯路）
**背景**：阶段2 要让 graph 的 `model_tool_node` 遵守 `depends_on`（见
`ISSUE-20260914-GRAPH-MODEL-TOOL-IGNORES-DEPENDS-ON`）。定预算时先分析轮次成本：
```
C1/C4 真实轨迹均 4 轮、budget_exhausted：
  r1 = get_my_risk_restriction（事实，1 轮）
  r2~r4 = search_customer_knowledge（制度，3 轮，仍不够）
```
已读码确认**每个工具调用都占 1 轮 LLM**（即使确定性工具也要模型先决定调用）。
故提出方案 C：**让确定性事实工具"零轮次"** —— 按计划直接执行，不经模型决策。
理论上同时解决两件事：① 释放 1 轮预算给知识检索；② 事实**必然**进入上下文，
不再依赖"模型自己记住"（C4 失败正因它没记住）。

**尝试过程（三版，均被测试逐版击退）**：
1. **预执行 + 精确匹配去重**：在 `run()` 开头按 `planned_intents` 直接执行事实工具。
   失败：预执行用原始 query、模型传自己的 `arguments`，`(工具,参数)` 精确匹配拦不住，
   同一工具被执行两次（`test_offplan_tool_is_blocked` 等抓到）。
2. **改为按工具名拦截 + `break`**：
   失败：`break` 会**跳过 `tool_out_of_plan_scope` 检查** —— 即"为省一次重复调用"
   而**削弱了既有安全校验**（越界调用不再被判定）。这是不可接受的回归。
3. **改为 `continue`（不 break）+ 补记停止原因**：
   失败：`continue` 跳过循环末尾的预算判定，导致空转到 `budget_exhausted`；
   为修这个又在两处加条件判断，越补越绕。
4. **最后一试：把预执行过的工具从 `available_tools` 中移除**（从源头不给选项）：
   失败：与测试结构冲突更严重（6 个失败）——既有用例**故意**让模型调用计划内事实工具
   来验证"越界/重复"语义，移除选项使这些路径无法被触发。

**结论：回退全部改动**（`customer_model_tools.py` 与受影响测试均还原，tests 回到 435 passed）。
**为什么回退而非继续打补丁（判断依据）**：
- 连续三版各自打破不同的既有约束（去重失效 / 安全校验被跳过 / 用例结构冲突），
  说明**问题不在实现细节，而在方案与现有结构不契合**；
- 修补过程中我为了一个**停止原因标签**反复加条件判断，这是"设计不对"的典型信号；
- 该优化**并非必需**：graph 当前是影子路径未切流，pipeline 侧（含 `_dependency_context`）
  已能正确处理 C1/C4。为未切流的路径引入跨 5 个测试的语义变更，收益与风险不成比例。

**保留的价值（下次做时的起点）**：
- 轮次成本已量化：事实层 1 轮 / 知识层 >=3 轮；`max_rounds=4` 对"事实+知识"场景**本就不够**；
- 已确认每个工具调用占 1 轮 LLM（含确定性工具）；
- 方案 C 的**动机仍然成立**（确定性工具不该由模型决定是否调用），只是**落点**应重新选：
  更可能正确的做法是**在 `model_tool_node` 层按 `depends_on` 分层**（层间用
  `_dependency_context` 传递），而不是在 `CustomerModelToolLoop` 内部做预执行 ——
  后者与"模型自主调度"的职责边界冲突，且与既有测试假设不兼容。

**方法论教训**（本轮最有价值的一条）：
**当同一个功能需要反复打补丁才能不破坏既有测试时，应停下来质疑方案本身，而不是继续补。**
本次三版补丁分别打破了三类不同的既有约束，这是"方案与结构不契合"的强信号。
正确动作是**回退并重新选落点**，而非在一个不合适的层级上持续叠加条件分支。

---

### ISSUE-20260915-SUBAGENT-ENVELOPE-NOT-UNWRAPPED
- 日期：2026-09-15
- 标题：只读子代理并行接线后**每次决策都静默降级为串行**（工具调用包装体未剥壳）

**现象**：接线 `run_read_only_fanout` 后，用真实 Qwen 跑 3 个用例，
**三次全部** `source=fallback_serial`、子任务数恒为 1。
但输出**看起来完全正常**：`overall_status=succeeded`、有融合答案、无异常日志。

**定位过程（关键：不猜，直接看返回）**：
1. 先怀疑模型不具备判断力 —— 于是写了独立探针 `dispatch_read_tasks` 单工具，
   直接打 LLM：**8 个用例命中 7 个**，唯一未命中是"最简单用例模型返回 None",
   而非判断错误。**模型侧被排除**。
2. 再怀疑接线错误 —— 在诊断脚本里同时打印"直接调 `request_tool_call` 的原始返回"
   与"`planner.plan` 的结果"：
   - 原始返回：`{'name': 'dispatch_read_tasks', 'arguments': {'mode': 'parallel', 'tasks': [...]}}` ✅
   - `plan()` 结果：`source=fallback_serial`, tasks=1 ❌
   同一个调用，外层正确、内层错误 → 问题在**解析层**。
3. 用最小用例复现：`parse_fanout_decision(raw)` 里 `raw.get("tasks")` → `None`，
   而 `raw["arguments"]["tasks"]` 才是真实参数。

**根因**：`QwenProvider.request_tool_call` 返回的是**包装体**
`{"name", "arguments", "reasoning"}`，而工具的真实参数在 `arguments` 内层。
`parse_fanout_decision` 初版只认裸参数，`plan()` 又把包装体直接传入
→ `tasks` 恒为 `None` → `tasks=[]` → 走 `fallback_serial` 分支。
**每一次决策都静默降级，模型根本没被采纳。**

**为什么这个 bug 特别危险（本条目最重要的部分）**：
- 它是**静默**的：不报错、不抛异常、日志无异常，输出形态与正常路径**一模一样**；
- 它是**看起来合理**的：降级路径本身是设计好的兜底（"模型不可用 → 串行单任务"），
  所以"每次都降级"表现得像"模型这次又没调工具"，而非故障；
- **全套 454 个既有测试都没抓到**——因为 fake provider 直接返回**裸 dict**，
  绕过了真实 provider 的包装体。测试与真实调用形态不一致 = 测了个假路径。

**修复**：`parse_fanout_decision` 开头统一剥壳（两形态都支持）：
```python
if isinstance(args.get("arguments"), dict):
    args = args["arguments"]
```
并**修正测试 fake 的形状**，让它返回与真实 provider 一致的包装体
（`{"name","arguments","reasoning"}`），使这类 bug 今后无法再逃过测试。

**验证**：
- 新增回归用例 `test_parse_unwraps_tool_call_envelope` / `test_parse_envelope_and_bare_arguments_agree`；
- **反向验证测试有效性**：临时删掉剥壳代码 → 5 个用例失败；恢复 → 全过。
  （证明测试真的覆盖了该缺陷，而非"恰好通过"）
- 真实端到端 3 用例全部 `source=model`：并行 2 子任务 / 串行 2 子任务(带依赖) / 单任务 1 子任务。

**方法论教训（第三类"静默降级"缺陷，与前两次同源）**：
1. **测试 fake 的形状必须与真实依赖一致**。fake 返回裸 dict、真实返回包装体，
   等于测试了一条生产上不存在的路径 —— 这类"测试通过但功能全废"最危险。
2. **兜底路径要有"被触发了"的可见信号**。降级本身是对的，但"降级率 100%"
   必须能被一眼看出。本模块的 `source` 字段正是为此存在，
   且已加进测试断言（`assert res.fanout_source == "model"`）。
3. **"模型不行"与"代码没接上"必须能被区分**。区分方法：先做**独立探针**直接打 LLM
   （排除模型侧），再对比"原始返回 vs 处理结果"（定位代码侧）。
   本次靠这两步在 3 次工具调用内定位完毕，没有靠猜。

---

### ISSUE-20260915-SUBAGENT-TIMEOUT-KILLS-KNOWLEDGE-TASK
- 日期：2026-09-15
- 标题：子代理超时预算 8s 与"检索+生成"真实耗时(11.5s)不匹配，知识类子任务被静默丢弃

**现象**：并行 2 子任务时，资产子任务 `success`，知识子任务 `partial` 且 **summary 为空**；
`overall_status` 降为 `partial`。表观看像"检索/模型故障"。

**定位**：分别测"直接调 runner"与"经 executor 调 runner"：
- 直接调 `runner(task)`：`status=success`，耗时 **11485ms**，答案完整；
- 经 `SubagentExecutor`：`status=partial`，`error='subagent_timeout'`，耗时 **8015ms**。
即 runner 本身没问题，是 `per_task_timeout_s=8.0` 把它砍了。

**根因**：`SubagentExecutor` 初版 `per_task_timeout_s=8.0` 是按"**一次工具调用**"设计的，
但接线后知识类子任务的真实工作量 = **检索(向量+BM25 双路) + 一次答案生成 LLM 调用**
= 实测 11.5s。阈值与工作量的差距不是"偶尔超时"，而是**必然超时**。
且超时后子任务结果被降级为空，**用户少一路答案却只看到"部分未完成"**。

**修复**：
- 默认值改为 `DEFAULT_PER_TASK_TIMEOUT_S = 20.0`（最慢子任务 + 余量），
  `DEFAULT_GLOBAL_TIMEOUT_S = 45.0`（必须 ≥ 单任务超时，否则串行时最后一个必被砍）；
- 两者都可由 `run_parallel_read_only` 参数覆盖（便于按环境调参，而非写死）；
- 新增 3 个用例锁死不变式：单任务超时 ≥15s、全局 ≥ 单任务、可覆盖。

**验证**：真实端到端 3 用例全部 `succeeded`，知识类子任务返回**真实内容**
（如"XX科技是持牌综合金融服务机构（引用2第5条）…"），不再是占位串或空摘要。

**顺带修复的第二个缺陷（同源）**：`ReadOnlySubagentRunner` 初版**不接 answer_generator**，
而 `CustomerReadTools` 对知识类返回的是 `answer="可基于客服知识库引用生成回答。"`
（**占位串**）+ 真实 `citations`。不接生成 → 融合答案里知识类子任务永远只有那句占位串，
`status=success` 看着正常、**实际没有任何内容**。现已接入 `CustomerRagAnswerService.generate`，
并加 3 个用例（生成成功 / 无 generator 退回原文案 / 生成失败不拖垮子任务）。

**方法论教训**：
**"状态对、内容空"比"报错"危险得多**。`partial`/`success` 这类状态字段只能说明流程走完，
不能证明产出了内容。凡是有"占位串"设计的接口，接线时必须确认
**占位串是否已被真实内容替换**，否则会得到一个"永远成功、永远无内容"的路径。

---

### ISSUE-20260915-SUBAGENT-SERIAL-ORDER-NOT-GUARANTEED
- 日期：2026-09-15
- 标题：串行模式只限流不排序，有依赖的子任务拿不到前序事实

**现象（读码发现，非线上）**：`SubagentExecutor` 用
`asyncio.gather` + `Semaphore(max_concurrent)`。串行模式下 `max_concurrent=1`
只保证"一次跑一个"，**不保证谁先跑**——按 `gather` 协程创建顺序抢信号量。
而模型返回的子任务列表**可能倒序**（实测模型把"解释风险制度"排在"查风险限制"之前，
并用 `depends_on=[0]` 表达依赖）。

**后果**：若依赖任务排在后面，后序子任务先执行 → 拿不到前序事实 → 退化为
"模型碰巧记住才正确"，**正是分层修复前 graph 的旧缺陷**（同一类问题在不同层的重演）。

**修复**：
- 新增 `order_subtasks_by_dependency()`：按 `depends_on` 拓扑排序，
  并把依赖**下标重新映射为 task_id**（排序后下标会错位，这是最易写错的一步）；
- 串行模式下先排序再下发；执行前把**该任务直接依赖的**前序结论拼成 `prior_facts` 注入
  （只给依赖的那部分，不是所有已完成任务的结论）；
- **并行模式不注入**：并行子任务按定义互不依赖，注入"别人的结果"会引入非确定性
  （谁先跑完谁先进上下文 → 结果不可复现），已加用例锁死该行为。

**验证**：`test_serial_dependency_order_is_respected_and_facts_flow_forward` 用
**倒序**输入 + 记录型 runner，断言"依赖任务先执行"且"后序任务确实收到前序结论"；
`test_order_subtasks_handles_cycle_without_dropping_tasks` 保证循环依赖不丢任务。
真实端到端串行用例中，sub-2 收到的前序事实为"您的风险承受能力等级为 C3"，
并据此正确解释了 **C3** 的制度依据（即"用事实套规则"成为机制而非运气）。

---

### ISSUE-20260915-SUBAGENT-FANOUT-PERF-MEASURED
- 日期：2026-09-15
- 标题：受控子代理并行的实测延迟收益（设计文档 §10 验收项）

**背景**：设计文档 §10 要求"可并行场景总延迟显著低于串行（有量化对比数据）"。
此前 P0 步骤 3（接入 1-2 个只读场景）未完成，故无任何实测数据。

**测量方法**：3 个**相互独立**的知识检索子任务（含向量+BM25 双路，真实工作量），
同一 runner，分别以 `max_concurrent=1`（串行）与 `=3`（并行）执行，各取 3 轮最优值。
**先踩过一个坑**：最初用资产/订单类查询测，耗时仅 0~15ms（无实际工作量），
测不出并发差异（甚至出现除零）—— **基准测试必须用有真实耗时的工作负载**。

**实测结果**：
| 模式 | 耗时 |
|---|---|
| 串行 max_concurrent=1 | 8969 ms |
| 并行 max_concurrent=3 | 2469 ms |
| **收益** | **降低 72.5%（3.63x 加速）** |

**结论**：并行收益显著，且与子任务数近似线性（3 个独立任务 → 接近 3x），
说明后端（DB 连接池/Qwen 配额）在此并发度下**不是瓶颈**。
并发上限仍由 `Semaphore` 受控（生产默认 2），不会无限放大。

**边界重申**：本层**只读**。资金/订单/风控结论/任何写操作永不进此层——
capability 白名单是 enum（模型物理上无法编造写能力），executor 侧有第二道校验，
且已加端到端用例 `test_fanout_never_exposes_write_capabilities_end_to_end`
验证"模型返回 create_order/transfer_money 时不会发生任何一次对应调用"。

---

### ISSUE-20260915-RISK-LEVEL-VOCAB-MISMATCH-R-AND-C
- 日期：2026-09-15
- 标题：知识库语料用 R1–R5，而数据模型是 C1–C5 —— 答案里混用两套词汇

**现象**（真实问答复现，非推断）：
- 问"产品风险等级怎么划分" → 答"金融产品的风险等级共分为 **R1 至 R5 五个等级**"
- 问"C4客户能买什么等级的产品" → 答"**C4** 进取型投资者可购买 **R1至R4** 风险等级的金融产品"

**为什么是缺陷（而不只是"叫法不同"）**：
答案在**同一句话里混用了两套词汇**——`C4` 来自数据库（客户等级），`R1–R4` 来自语料，
而 **`R1–R5` 在整个数据模型中根本不存在**：
```
product.risk_level            实测分布: C1(4) C2(6) C3(4) C4(4) C5(3)   ← 共 22 条
customer_risk_assessment.risk_level  实测:  C1 C2 C3 C4 C5
```
即系统**用一套不存在的标签向用户解释自己的规则**。用户若据此去 App 里找"R3 产品"，找不到。

**根因**：语料（`knowledge_corpus/*-客户版.md`）是**手写**的客户版文档，
撰写时用的是行业通用的 R1–R5 说法；而代码/DB 落地时用的是 C1–C5。
两者从未对齐，且**没有任何校验会在导入时发现**——语料导入只校验类别与权限，
不校验其中的业务术语是否与数据字典一致。

**影响面**（实测计数）：
| 文件 | R1–R5 出现次数 |
|---|---|
| `产品服务手册-客户版.md` | 18 |
| `高频问答对-客户版.md` | 21 |
| `公司业务介绍-客户版.md` | 3 |
| **合计** | **42** |

**为什么之前没被发现**：检索只看语义相似度，`R1–R5` 与 `C1–C5` 语义完全一致
（都表示"风险等级第 N 档"），向量距离很近 → 检索正常命中、`evidence_status=sufficient`、
答案通顺且有引用。**所有质量指标都是绿的**，只有人肉读答案才能发现术语是错的。
这与"状态对、内容错"是同一类问题：**流程指标无法发现语义错误**。

**状态**：**未修复**（本次仅定位与量化）。修复需：
① 统一术语口径（建议全部改成 DB 实际使用的 C1–C5，或在 product 表增加 R 级映射）；
② 改语料后**重新导入**（`scripts/import_customer_corpus.py`）并重建 BM25 索引；
③ 加一条**导入期校验**：语料中出现的风险等级词汇必须在数据字典内
（防止同类问题再次发生——"能靠校验保证的，不要只靠人工校对"）。
④ 需同步检查 `docs/BUSINESS_KNOWLEDGE_*.md` 与前端展示文案是否也有 R 级说法。

**方法论教训**：
**"检索命中 + 有引用 + 答案通顺"不等于"答案正确"。**
本次是用户问"还有哪些没做到"时，我逐条核对遗留清单**主动去读真实答案**才发现的；
若只看 `evidence_status` 或评测的相似度指标，这个问题会一直存在。
**遗留清单不能只标"待办"，要么复现到用户可见层，要么注明"仅静态观察"。**

---

---

### ISSUE-20260915-SUBAGENT-NO-DEDUP-DUPLICATE-DATA-AND-KNOWLEDGE
- 日期：2026-09-15
- 分类：Agent / 子代理并行 / 答案聚合
- 严重程度：P2
- 状态：**FIXED**
- 关联：**ISSUE-20260914-SUMMARIZE-DUPLICATE-FILLER（同族，该缺陷已在本模块犯过一次）**

**发现方式**：接线完成后，用户直接指出"我们真的有对内容去重吗？同一份数据被多个子代理读、
同一份知识被多次检索、还有汇总时重复"。**这是用户提出的质疑，不是我自己发现的**——
而我此前汇报时只说了"延迟降低 72.5%"，**完全没有检查去重**。
教训见文末。

**复现（改代码前先跑探针，三个问题逐一确认）**：

| # | 问题 | 实测结果 |
|---|---|---|
| 1 | 融合不按内容去重 | 两个子代理命中同一 chunk、答案相同 → 输出 `[sub-1] 公司主营财富管理…\n[sub-2] 公司主营财富管理…`，**同一句出现 2 次** |
| 2 | 同一 capability 被拆两次 | 模型把"我的总资产"与"我的持仓数量"都映射到 `customer_asset_summary` → `get_my_asset_summary` **被调用 2 次**、答案里"总资产 3,000,000.00 元"**出现 2 次** |
| 3 | 失败方可操作提示被丢弃 | `_merge` 只拼接 `status=="success"` 的 summary → `fact_not_found` 的"请补充订单号"**整个丢失**，用户只看到"（部分子任务未完成）" |
| 4 | 同一知识被多次检索 | 3 个近义 query（"公司主营业务是什么"/"你们公司主要做什么业务"/"公司有哪些业务线"）共返回 **15 条引用，去重后仅 8 条 → 重复率 47%** |

**问题 3 的特别说明（比"重复"更严重）**：
占位文案重复（`SUMMARIZE-DUPLICATE-FILLER`）是**观感问题**；
而"失败提示被丢弃"是**可用性问题**——用户看到"部分子任务未完成"却**不知道要补充什么**。
两个缺陷方向相反：同一个"无条件拼接/过滤"的粗糙聚合，既会造成重复，也会造成丢失。
**故修复不能只做"去重"，必须同时保证"不丢失"**。

**修复**：

1. **内容级去重**（`SubagentExecutor._merge`）：新增 `_content_key()` 归一化
   （去空白、去标点、去"（引用N）"标注、统一大小写），按归一化键去重，
   保留置信度最高者。**刻意不做语义级聚类**——融合层要求确定性（设计文档 §5.5），
   向量聚类会引入阈值调参与不确定性。
   **关键约束**：归一化**必须保留数字与字母**，否则"可购买 R1"与"可购买 R2"
   会被误合并 —— 那是**用去重引入正确性缺陷**。已加用例
   `test_merge_does_not_conflate_risk_levels` 锁死。

2. **事实类 capability 折叠**（`parse_fanout_decision`）：同一事实能力出现多次时折叠为一个。
   **关键设计判断：只折叠"确定性事实工具"，不折叠 `customer_rag_knowledge`**：
   - 事实工具（资产/订单/工单/风险限制）返回**只取决于 customer_id**、与 query 无关
     → 查两次必然同一份数据，折叠无信息损失；
   - 知识检索返回**取决于 query**："公司业务"与"开户流程"虽同属一个 capability，
     但检索的是**不同知识**，折叠会**丢答案**。
   笼统地"按 capability 去重"会把两个不同的知识问题合并成一个——
   **比重复本身更糟**。已加用例 `test_parse_does_not_fold_knowledge_capabilities`。

3. **失败提示保留**：失败/部分失败子任务若带有面向用户的提示，以"待补充"段落单独呈现；
   纯技术错误（`subagent_timeout`/`capability_not_mapped`）**不进**用户可见文案
   （只留在 `results[].error` 供排查），避免把技术细节泄漏给用户。

4. **重复率可观测**：新增 `SubagentMergeResult.dedup`
   （`dropped_duplicate_summaries` / `duplicate_citation_refs` / `unique_citations` / `pending_hints`）。
   **不静默去重**——"重复被去掉多少"必须能被量化追踪，否则本层退化时无人知晓。

**修复过程中自己踩的坑（已记录）**：
`depends_on` 重映射第一版**按旧下标 enumerate** 组织结果，产出与 `tasks` **不等长**的
`depends_on`（实测 `[[], [], [0]]` 对应 2 个 task）。下游按下标取值就读到错位/缺失的依赖。
被 `test_folding_remaps_depends_on_correctly` **当场抓到**（这就是先写测试的价值）。
修正为按**新下标**构建（`new_deps: list[set[int]]`）。

**验证**：
- 新增 12 个用例（去重 6 + 折叠 4 + 提示保留 2），全部通过；
- **反向验证测试有效性**：关闭内容去重与 capability 折叠 → **5 个用例失败**；恢复 → 45 个全过。
- 全量 `tests`：**499 passed**（+12）。
- 引用重复率已可量化（探针实测 47%），修复后同一份数据只读一次。

**方法论教训（本轮最重要的一条）**：
**"性能指标达标"不等于"功能做对了"。**
我在接线后汇报了"延迟降低 72.5%"，但那只是**一个维度的测量**；
去重、正确性、失败降级这些**同样是交付标准**，我却没测——
是用户凭直觉问出来的，不是我用探针发现的。
**交付一个"层"时，应把该层的质量维度逐条列出来验证**（正确性/去重/降级/可观测），
而不是测一个最亮眼的指标就宣布完成。
这与本项目此前的教训一脉相承：**能靠校验保证的，不要只靠人工核对**——
去重率、重复引用数现已进入 `dedup` 统计，属于"可被持续观测"而非"靠人记得检查"。

---

---

### ISSUE-20260915-JUDGE-FAILURE-MASQUERADES-AS-REJECT
- 日期：2026-09-15
- 分类：RAG / 证据门控 / 可靠性
- 严重程度：**P1**
- 状态：**FIXED**

**现象**（真实端到端测试发现）：同一问句"你们公司主营业务是什么"**时好时坏**——
上一轮测得 `success` 并给出真实答案，下一轮测得 `low_confidence` +
"当前知识库没有足够依据，建议转人工客服或创建工单"。

**排查过程（三个假设，前两个被自己的探针证伪）**：
| 假设 | 验证方法 | 结论 |
|---|---|---|
| H1 检索不稳定 | 同一 query 连跑 3 次比对 sims | **证伪**：`[0.7001, 0.5783, 0.5082, 0.5072, 0.5061]` 三次完全相同，检索是确定性的 |
| H2 模型改写措辞导致检索命中不同 | 4 种改写各自测 best_sim | **证伪**：0.6693/0.6954/0.7001/0.7104，全部 `sufficient` |
| H3 门控在阈值边界抖动 | 测得 best_sim 落在 judge 区间 + 模拟 judge 失败 | **证实** |

**根因**：`best_sim` 门控边界是 0.65，而该 query 的 best_sim 随措辞在 **0.53~0.71 之间浮动**
（实测 10 种措辞中 **8 种**落在 `[0.35, 0.65)` → 需要 LLM judge 裁决）。
落在该区间时，答案**取决于一次额外的 LLM 调用**；而 `customer_rag_service.py` 旧实现：

```python
except Exception:      # judge 失败不阻塞，按不通过处理
    judge_result = False        # ← 把"没跑成"当成"判为证据不足"
```
→ `decide_evidence` 返回 `reject` → `low_confidence` → 用户被转人工。
**即：限流/超时/网络抖动被伪装成"知识库没有依据"**，而该 query 的证据其实充分
（best_sim=0.5971、5 条引用、judge 正常时 **8/8** 返回 True）。

**严重程度定为 P1 的理由**：这不是体验问题，而是**把"我们的组件坏了"误报成"数据不存在"**。
用户得到的是**错误的事实性结论**（"没有依据"），并因此被转人工；
而同类误报在本项目已出现过（`fact_not_found` 与"对象不存在"混同）。
**组件故障不应改变业务结论。**

**修复**：把"judge 判为不足"与"judge 没跑成"区分开。
- `customer_rag_service.py`：judge 异常时置 `judge_failed=True`（**不再置 False**）；
- `rag_eval_metrics.decide_evidence()`：新增 `judge_failed` 参数，
  为真时**忽略 judge 结论**，退回确定性阈值兜底（`fallback`/`reject`）。
  即：**故障只影响"是否用上了 judge 这个增强"，不影响判定本身**。

**验证**：
- 新增 7 个用例（`tests/test_judge_failure_not_reject.py`）：故障不 reject / 真 False 仍 reject /
  真 True 仍 answer / 故障+低相似度仍 reject / 明显场景不受影响 / 无 judge 与故障一致；
- **反向验证**：同一 `best_sim=0.5971` 下，旧组合（`judge_result=False`）→ `reject`，
  新组合（`judge_failed=True`）→ `answer`，而 `judge_result=False` 且 `judge_failed=False`
  仍为 `reject`（**证明修复改变了行为且没有放水**）；
- 真实端到端：3 个用例全部 `succeeded`，无 `handoff_notices`；
- 全量 `tests`：**510 passed**。

**方法论教训**：
1. **"同一输入结果不同"必须先证明输入真的相同。** 我最初以为"检索不稳定"，
   但探针显示检索完全确定 —— 真正的变量是**模型改写的措辞**（输入其实每次都不同）。
   若不上探针直接改检索层，就会改错地方。
2. **区间型门控 + LLM 裁决 = 引入了对 LLM 可用性的强依赖。**
   本例中 8/10 的措辞都要过 judge，等于把"能不能回答"押在一次额外调用上。
   凡是"用 LLM 做路由/裁决"的地方，都必须明确回答：**裁决器本身失败时走哪条路？**
   默认答案应是"退回确定性路径"，而不是"当作裁决为否"。
3. **异常兜底的方向要按业务语义选，不能一律保守。** "保守"在这里恰恰导致
   用户拿到错误的否定结论；正确做法是**降级到确定性的等价路径**。

---

### ISSUE-20260915-HANDOFF-NOTICE-POLLUTES-SUCCESSFUL-ANSWER
- 日期：2026-09-15
- 分类：Agent / 答案聚合 / 体验
- 严重程度：P3
- 状态：**FIXED**
- 关联：ISSUE-20260915-SUBAGENT-NO-DEDUP-DUPLICATE-DATA-AND-KNOWLEDGE（同一次端到端测试发现）

**现象**（真实端到端）：融合答案里出现
```
[sub-1] 您最近一次资产快照的总资产为 3,000,000.00 元，当前展示 1 条有效持仓。
当前知识库没有足够依据，建议转人工客服或创建工单。      ← 紧跟其后
```
资产答案本身**完全正确**，但紧跟一句整体降级声明，用户会以为**整个回答都失败了**。

**根因**：上一轮为修复"失败提示被丢弃"（`ISSUE-20260915-SUBAGENT-NO-DEDUP-...` 的第 3 点），
把所有失败子任务的 summary 一律当作"提示"塞进正文 —— **用力过猛**：
没有区分两类语义完全不同的文案。

| 类型 | 来源 error | 含义 | 正确处置 |
|---|---|---|---|
| **可操作提示** | `fact_not_found` / `ambiguous_product` | "补充什么就能重试" | **保留在正文** |
| **降级声明** | `low_confidence` | "知识库无依据，整体转人工" | **结构化表达**（`requires_handoff`），不混进正文 |

**修复**：按 **error 码**（结构性，而非文本匹配——文案会变）分类：
- 可操作提示 → 保留在正文；
- 降级声明 → 只在**没有任何成功答案**时才作为主文案给出，否则仅置
  `SubagentMergeResult.requires_handoff=True`（新增字段）+ `dedup.handoff_notices` 计数；
- 技术错误（超时/未映射）→ 不进用户可见文案，只在 `results[].error` 留痕。

**验证**：新增 4 个用例（降级声明不污染正文 / 无成功答案时仍显示 / 可操作提示仍保留 /
两者共存时各走各的）；真实端到端 `handoff_notices=0`、正文干净；全量 510 passed。

**方法论教训**：
**"保留失败信息"不等于"把所有失败文案都贴给用户"。**
修复"信息丢失"时很容易过度补偿成"信息污染"。正确做法是按**语义类型**分流，
并优先用**结构化字段**承载"状态判定"，正文只承载"给用户看的内容"。

---

---

### ISSUE-20260915-RISK-LEVEL-VOCAB-MISMATCH-R-AND-C（已修复）
- 日期：2026-09-15
- 分类：知识库语料 / 数据一致性
- 严重程度：P2
- 状态：**FIXED**（本条为原条目的修复记录，原条目保留在下文以存排查过程）

**修复结果**：语料统一为 **C1–C5**（与数据模型一致），R1–R5 已全部消除；
新增**导入期校验**拦截同类问题；`policy` 文档来源从失效外部路径改为仓库内文件。

**关键发现：语料有**两个来源**，只改一个不够**（这是本次修复中最重要的一步）：
| 来源 | 文件 | 类目 | chunk | 首次修复覆盖？ |
|---|---|---|---|---|
| `knowledge_corpus/` | 4 个客户版文件 | company_info / company_business / faq | 80 | ✅ 42 处 |
| **`金融政策/`** | 3 个政策文件 | **policy** | 24 | ❌ **一开始漏了** |

第一次改完 `knowledge_corpus/` 后复验，**4 个用例里仍有 2 个输出 R 级**——
因为"C4 能买什么"命中的是 `policy` 类目的《个人投资者适当性管理指南》匹配矩阵，
而它在另一个目录。**若不做"改完立即复验"这一步，会误报"已修复"。**

**第二个坑：policy 文档的 source_path 指向一个已不存在的外部目录**：
```
C:\Users\tx360\Desktop\金融 - 副本\金融政策\个人投资者适当性管理指南.md   ← 目录已删除
```
后果：① 该文档无法重新 ingest（FileNotFoundError）；
② **我修正仓库内同名文件对线上毫无影响**；
③ 语料不在版本库内，无法审计。
处理：把 source_path 改指向仓库内 `金融政策/个人投资者适当性管理指南.md`，
再重新 ingest（37 chunks，全部重新向量化）。

**修复内容**：
1. 语料改名：`knowledge_corpus/` 42 处 + `金融政策/` 30 处，R1–R5 → C1–C5（纯改名，1:1）；
2. **插入期校验** `validate_risk_level_vocabulary()`：用正则
   `(?<![A-Za-z0-9])R[1-5](?![0-9A-Za-z])` 精确匹配等级写法，
   **不误伤** `R10` / `R&D` / `R2D2` / 英文词（已加 7 个用例验证）；
3. 校验接入 `import_customer_corpus.py`，**同时覆盖两个语料目录**
   （只校验 `knowledge_corpus/` 会漏掉一半语料——这正是第一次修复漏掉的原因）；
4. policy 文档 source_path 指向仓库内，语料从此可版本化、可复现导入。

**验证**：
- 改后 4 个用例**全部 0 个 R 级**，"C4 能买什么"现在答
  "可购买风险等级为 **C1 至 C4** 的产品；C5 需签署揭示书且持仓不超总资产 10%"——
  **与 DB 规则完全一致**（DB: C4 客户 → C1–C4 产品）；
- 7 个词汇校验用例 + 全量 **517 passed**（+7）；
- 回归探针全部通过；两个语料目录 R 级残留 **0**。

**方法论教训（第三条，与另外两条同类）**：
**改完必须立即在"用户可见层"复验，而不是看代码/文件改没改。**
本次若只确认"42 处已替换完"，就会宣称修复完成——而用户看到的仍是 `R1至R4`。
**"文件改了" ≠ "问题解决了"**，中间还隔着：语料来源是否正确目录、
是否需要重新 ingest、是否有第二个数据源。
这与本项目此前教训一脉相承（"状态对、内容空"、"检索命中不等于答案正确"）。

---

---

### ISSUE-20260915-LLM-INJECTS-R-LEVELS-FROM-PRIOR
- 日期：2026-09-15
- 分类：RAG / 生成阶段 / 术语一致性
- 严重程度：P3
- 状态：**OPEN（已定位，未修）**
- 关联：`ISSUE-20260915-RISK-LEVEL-VOCAB-MISMATCH-R-AND-C`（语料侧，**已修复**）

**现象**：语料统一为 C1–C5 且**当前生效 chunk 中含 R 级的数量为 0**（已核验），
但生成阶段的答案**仍偶发出现 R1–R5**。实测 4 轮：

| 轮次 | 含 R 级用例数 | 具体 |
|---|---|---|
| 1 | 2/5 | "什么是投资者适当性管理" → R1,R5；"我有没有风险限制" → R1 |
| 2 | 1/5 | "什么是投资者适当性管理" → R1,R5 |
| 3 | **0/5** | — |
| 4 | **0/5** | — |

**已排除的原因**（逐条验证，不是推断）：
- ❌ **语料残留**：`SELECT ... WHERE c.content ~ 'R[1-5]' AND c.status='active' AND v.status='active'` → **0 条**；
- ❌ **陈旧 chunk 命中**：曾发现 31 个 `superseded` 版本的 chunk 仍标记 `status='active'`（总 61 个），
  已清理为 `superseded`。但检索侧**本来就**用 `KnowledgeDocumentVersion.status == 'active'` 过滤
  （`repositories/knowledge.py:107/159/359`），故它们**从未影响答案**——这一点已用
  "引用中含 R 级的 chunk 数 = 0/5" 直接验证。**清理属数据卫生，不是修复该现象**（须如实区分）。
- ❌ **检索带进来的**：答案里出现 R 级的那些轮次，其 citations **0 条**含 R 级。

**根因（定位结论）**：**模型自身的先验知识**。
`R1–R5` 是行业通用的产品风险等级表述（券商普遍用 R 表示产品、C 表示客户），
而本项目实际只用一套 C1–C5。模型在生成时**用行业习惯"纠正"了检索到的事实**——
例如答出"产品亦按风险由低到高划分为 **R1至R5** 等级（与 C1—C5 对应）"，
即它**明知 C1–C5 却主动补上了 R 级说法**。

**性质与影响**：
- **偶发、非确定性**（同一问句 4 轮里 2 轮出现），故**无法靠"再跑一次"发现**；
- 用户可能被告知"R1 至 R5"，而 App 中不存在该编号 → 与已修复的语料缺陷**表现相同**、
  **根因完全不同**（一个是数据错，一个是模型先验污染）；
- 影响面小于语料缺陷（内容主体正确，只是夹带一个多余术语）。

**修复方向（未实施，需先量化）**：
1. **生成提示词约束**：在 `customer_rag_answer` 的生成提示中明确
   "本系统的风险等级统一用 C1–C5 表述，**不要使用 R1–R5**"。
   注意：提示词是**软约束**（本项目已有多次"提示词无法强制模型"的教训），
   故应配**输出校验兜底**。
2. **输出后校验（推荐主路径）**：生成后做确定性检查——
   若答案含 `R[1-5]` 而引用中无 R 级 → 判定为模型先验污染，
   要么**自动改写为 C 级**（1:1 映射，语义等价），要么**重生成一次**。
   这与 `ISSUE-20260915-JUDGE-FAILURE-MASQUERADES-AS-REJECT` 的教训一致：
   **能靠确定性校验兜住的，不要只靠提示词**。
3. 需先量化发生率（当前样本 n=4，2/5 与 0/5 差异大，样本不足）再决定投入。

**方法论教训**：
**"同一现象"可能有完全不同的根因，修掉一个不代表修掉全部。**
语料侧修复后我一度以为问题已解决；是**连续多轮复验 + 逐条排除**（chunk 查询、引用内容比对）
才把"数据源错误"与"模型先验污染"区分开。
若只跑一轮看到 0/5 就收工，会漏掉这个仍然存在的缺陷；
若只跑一轮看到 2/5 就继续改语料，则会改错地方。

---

---

### ISSUE-20260915-JWT-DECODE-DUPLICATED
- 日期：2026-09-15
- 分类：安全 / 认证 / 代码重复
- 严重程度：**P2（潜在权限漏洞）**
- 状态：**FIXED**

**问题**：HTTP 与 WebSocket 两条认证通道**各写了一遍** JWT 解析与校验：
- `current_user`（HTTP，原 `auth.py:15`）
- `websocket_current_user`（WS，原 `auth.py:74`）

两份实现完全相同（`jwt.decode` + `payload["type"] != "access"` + 按 sub 查启用用户），
但**没有任何机制保证它们保持一致**。

**为什么是 P2 而不是"代码整洁"问题**：
任何一处收紧校验、另一处没跟上，就形成**可绕过的权限通道**。例如将来加
token 版本/黑名单/签发者（iss/aud）校验，若只改 HTTP 侧：
→ **HTTP 拒绝、WebSocket 仍放行**。
而两条路径从不互相报错，**普通功能测试不会失败**——这是最典型的"静默权限分叉"。

**修复**：抽出单一实现，两侧只在**失败形态**上不同：
```python
def _decode_access_token(token) -> dict:      # 解析 + type 校验；失败返回 {}
async def _load_active_user(app, subject)     # 按 sub 查 status=active 用户（含预加载）
```
- `current_user`：失败 → `HTTPException(401)`
- `websocket_current_user`：失败 → `None`（WS 无法返回 HTTP 状态码）

**验证**：
- 新增 `tests/test_auth_channel_consistency.py`（8 个用例）：合法 token 通过 /
  refresh token 被拒 / 缺 type 被拒 / 签名错 / 过期 / 垃圾串；
- **两条结构性护栏**：断言 `auth.py` 中 `jwt.decode` **只出现 1 次**、
  `selectinload(User.roles)` **只出现 1 次**。若将来有人再抄一份，用例立即失败；
- **反向验证**：故意在 ws 侧注入一份重复的 `jwt.decode`（实测 2 处）→
  `test_both_channels_share_one_implementation` **失败**；恢复后 8 个全过。
  （证明护栏真的有效，不是"恰好通过"）

**方法论教训**：
**"重复代码"的风险等级取决于它守的是什么。**
重复一段格式化逻辑是整洁问题；重复一段**鉴权逻辑**是安全问题——
因为分叉后的两侧**不会互相报错**，缺陷只在被利用时才暴露。
对这种地方，除了合并实现，还应加**结构性用例**防止再次分叉
（普通行为测试测不出"两份实现"这个事实）。

---

### ISSUE-20260915-NO-GATEWAY-GUARDS
- 日期：2026-09-15
- 分类：网关保护 / 可用性 / 安全
- 严重程度：P2
- 状态：**FIXED**

**问题**：应用层完全没有网关级护栏——无限流、无全局超时、无请求体大小限制、无 GZip。
（差距清单 C 类项，此前只记录未做。）

**关键发现**：`app/common/rate_limit/redis_limiter.py` 的 `RedisRateLimiter`
**早已实现但从未挂载**（全仓 0 个调用点）——与 `common/dependencies/request.py`
的三个 DI 辅助函数是**同一种病**：**实现了、导出了、没人用**。
故本次是**接线**而非重写。

**修复**：新增 `app/common/middleware/request_guard.py`，并挂到最外层：

| 护栏 | 配置项 | 默认 | 超限 |
|---|---|---|---|
| 请求体大小 | `REQUEST_MAX_BODY_BYTES` | 12MB | 413 |
| 全局超时 | `REQUEST_TIMEOUT_SECONDS` | 120s | 504 |
| 按 IP 限流 | `RATE_LIMIT_REQUESTS` | **0=关** | 429 |
| GZip | `GZIP_MIN_SIZE_BYTES` | 1024 | 自动 |
| CORS 白名单 | `CORS_ALLOW_ORIGINS` | localhost×2 | — |

**用纯 ASGI 而非 `BaseHTTPMiddleware`**：后者**只处理 HTTP**，
而本项目最需要保护的实时 Agent 通道是 WebSocket。纯 ASGI 可同时覆盖两者，
故限流对 ws 也生效（ws 超限回 `websocket.close(1013)`）。

**三个关键设计判断（每个都有实测）**：
1. **限流器故障必须"放行"而非"拒绝"**：Redis 抖动时保守拒绝会让**整个服务不可用**。
   限流是保护措施，不是业务前置条件。实测：限流器抛异常时请求仍 200。
2. **健康检查豁免限流**：否则探活被 429，编排系统误判服务不健康。
   实测 `RATE_LIMIT_REQUESTS=1` 时 `/ping` 429、`/api/v1/health` 仍 200。
3. **限流默认关闭**：开启会改变既有压测/回归行为（并发测试出现 429），故默认 0。

**顺带修复**：CORS 白名单此前**硬编码**在 `main.py`（生产换域名必须改代码），
改为 `CORS_ALLOW_ORIGINS` 配置驱动，并加用例断言其与 settings 一致。

**验证**：`tests/test_request_guard.py` 16 个用例，每条护栏都验证
**"生效"与"不误伤"两个方向**（只测"能拦"会漏掉误拦）；全量 `541 passed`。
**踩坑记录**：GZip 一度看似"客户端没声明也压缩"，实为 `TestClient` 默认注入
`Accept-Encoding` 的**测试假象**；用 `Accept-Encoding: identity` 复测确认行为正确。

**方法论教训**：
1. **保护性设施的降级方向要按"失效后果"选**：限流/熔断这类应 **fail-open**
   （放行），而鉴权/资金这类应 **fail-closed**（拒绝）。选错方向会把
   "局部故障"放大成"整体不可用"。
2. **"已实现但未接线"是本项目的重复模式**（长期记忆、子代理层、限流器、DI 辅助函数）。
   排查"某能力缺失"时应先 grep 是否**已有实现没挂上**，而不是直接新写。

---

### ISSUE-20260918-TRACE-PANEL-NEVER-RENDERS
- 日期：2026-09-18
- 标题：链路追踪面板做完了，但页面上永远不显示

**现象**：对话内链路追踪面板（`AgentTracePanel.vue`）代码完成、单测通过、前端构建通过，
但用户在智能客服页提问后**什么都看不到**，反馈"没看到哪里有思维链和审计页面"。

**根因**：面板读取的 `data.model_tool_trace` **只在 `app/services/graph_agent_runner.py` 注入**，
而该 runner 仅在 `agent_graph_enabled=True` 时被 API 使用；图内的 `model_tool` 节点
又受 `agent_model_tool_selection_enabled` 控制（`graph.py:350`）。
这两个开关**默认都是 `false`**（`app/core/settings.py:68,82`），
于是默认走确定性管线 → 产出的是另一套 `data.tool_calls`
（结构仅 tool/iteration/success/error/evidence_status，**无思维链、无耗时、无 token**）
→ 面板 `v-if="rounds.length"` 恒为假 → 永不渲染。

实测（`POST /customer-service/sessions/agent/execute`，query="我有没有风险限制"）：
两个 task 均无 `model_tool_trace`；`risk_public` 的 `execution_mode=bounded_react`，
链路数据在 `tool_calls` 里。

**修复**：`docker-compose.p0.yml` 两个开关默认值改 `true`（保留 `${VAR:-true}` 便于回滚）；
`docs/OBSERVABILITY_IMPLEMENTATION_PLAN.md` 3.5 补记该前置条件。
修复后实测 `model_tool_trace` 正常产出，含 `layer` 分层、耗时、token。

**方法论教训（重要）**：
1. **"代码正确"≠"运行时走得到"**。本次单测锁住了字段契约（558 passed），
   却没验证"这条代码路径是否真的被执行"——而默认开关可以让整条链路永不运行。
   验收清单里那条"发起一次会触发工具调用的问答"恰恰最关键，当时未执行。
2. **接入新能力前先确认目标路径的默认状态**，不要假设"写好了就会跑"。
3. 同一语义存在**两条互不相通的实现**时（此处 `tool_calls` vs `model_tool_trace`），
   消费方必须明确对接的是哪一条，否则会出现"数据明明有、界面就是空"的假象。

---

### ISSUE-20260918-REASONING-ALWAYS-EMPTY
- 日期：2026-09-18
- 标题：思维链正文恒为空——取值通道选错，且 prompt 从未要求该字段

**现象**：链路面板的「调用理由」（`ToolRound.reasoning`）**永远为空字符串**，
而这正是用户最想看的"思维链"本体。

**排查过程（先证伪了一个省事的假设）**：
- 假设一：prompt 没要求模型给理由 → 在 `_TOOL_LOOP_SYSTEM` 加第 9 条
  「调用前先简述判断依据」后**实测仍为空**，排除。
- 真因：`app/infrastructure/qwen.py:461` 把 `reasoning` 取自 tool_call **同帧的
  `message.content`**。而 OpenAI 兼容接口在返回 `tool_calls` 时该字段**恒为空**
  （DashScope 实测如此）——接口行为，prompt 无法改变。

**修复**：把 `reasoning` 声明为 **tool schema 的必填参数**
（`_inject_reasoning_param(CUSTOMER_READ_TOOL_SCHEMAS)`），模型发起调用时填进
`arguments.reasoning`；代码取出后**立即 pop 剥离**，兜底才回退到 `content` 路径。

剥离是必须的，否则引入两个**静默**故障：
1. 工具收到一个它不认识的 `reasoning` 参数（污染入参）；
2. 重复调用闸门按 `(工具, 参数)` 判重，而每轮理由措辞略有不同
   → 真正相同的调用不再被判重，**闸门失效**。

**验证**：query="你们公司的主营业务是什么" 实测：
```
#1 search_customer_knowledge ok=True  reasoning=查询公司基本信息，确认主营业务范围以回答用户问题
#2 search_customer_knowledge ok=True  reasoning=为了获取公司基本信息…需查询知识库中的公司信息类别
#3 search_customer_knowledge ok=False duplicate_call_skipped
   reasoning=…需调用知识库中 company_info 类别进行检索
```
`#3` 被正确判重，**反证剥离干净**：若未剥离，它与 `#2` 理由措辞不同、参数不同，会躲过闸门。

**方法论教训**：
1. **依赖第三方接口"顺带提供的字段"前，先验证它在目标调用路径上真的有值**，
   不能只看字段存在就认为拿得到。声明为必填参数才是可靠通道。
2. **先排除"最省事的假设"再往下走**——本次正是先证伪了"prompt 没写"，
   才没在错误方向上继续堆 prompt。

---

### ISSUE-20260918-WHY-RECOMMEND-RETURNS-LIST
- 日期：2026-09-18
- 标题：问「为什么推荐」得到同一份清单——疑问词在意图判定中完全失效

**现象**：用户问「为什么推荐这些产品给我」，回答与「给我推荐」**一字不差**，
都是同一份产品清单；用户想要的是推荐依据。

实测：`route=customer_recommendation`、`intent=product_recommendation`、无 `model_tool_trace`。

**根因**：`app/services/customer_intent.py:141` 触发词表里
`("product_recommendation", "推荐", "suitability", .88)` ——
**只要句中出现「推荐」二字就把整句判成产品推荐**，疑问词（为什么/依据/凭什么）不参与判定。
且 `product_recommendation` 的执行分支本就没有"解释理由"的产出。

**修复**：
1. 新增 `_EXPLAIN_RECOMMEND_RE` 识别「解释词 ↔ 推荐/产品」组合，
   在 `plan()` 中**先于** `_BUY_ELIGIBILITY_RE` 与模式表判定，命中即返回新意图
   `recommendation_explanation`。正则必须**双向**匹配——第一版只写了「解释词在前」，
   实测「这些产品是怎么选出来的」漏判成 `faq`（"这些产品"在解释词之前）。
2. 执行侧抽出 `_build_recommendation()`，**推荐与解释共用同一份适当性结果**；
   新增纯函数 `explain_recommendation()` 输出依据。
   共用是有意的：否则解释里写的规则会与实际筛选规则**漂移**，用户按解释核对反而对不上。
3. 连带修复：解释文本曾**重复一遍产品清单**——`suitability_service.py:169,172` 的
   `preference_note` 本身就是「产品清单+理由」的浓缩，拼在已列产品的解释后面即复读。已移除。

**验证**：输出含风险等级、可投资产、两条硬性筛选条件、逐条匹配理由、被排除数量。
测试 `tests/test_recommendation_explanation.py` 8 例（含"不误伤正常推荐请求"
与"字段缺失降级"）；全量 570 passed。

**方法论教训**：
1. **单关键词触发词表处理不了"疑问词 + 关键词"的组合语义**。
   凡"问法不同但都含同一个名词"的场景（为什么推荐/怎么推荐/凭什么推荐），
   都需显式组合规则，否则疑问词被吞掉。
2. 规则要**双向**覆盖词序——中文问句的语序比想象的灵活。

---

### ISSUE-20260918-REPEATED-SAME-INTENT-LOOP
- 日期：2026-09-18
- 标题：同一意图换措辞反复检索，直到预算耗尽

**现象**：query=「公司客户规模」连续 4 次调用 `search_customer_knowledge`，
措辞各不相同（"公司客户规模、客户数量或客户群体范围" / "贵公司客户规模有多大？" /
"公司客户规模" / "公司客户规模、客户数量、服务客户总数等公开信息"），
**命中的是同一批知识块（2~5 条）**，却一路调到 `budget_exhausted`。
总耗时 28668ms、6651 tokens。答案正确，但多出的 3 次调用纯属浪费。

**根因**：源码本有三条「阻止重复」路径，其中两条已带明确提示：
- `tool_out_of_plan_scope`（越界）→ 提示 + break
- `duplicate_call_skipped`（参数完全相同）→ 提示 + break

唯独「**同一意图已成功过**」（`existing is not None` 分支）只把
`result.answer[:600]` **再回灌一遍**就 `continue`。
模型据此看到的是"我拿到了一批资料"，而非"我拿到的是**刚才那批**"，于是继续改写问法重试。

**修复**：
1. 该分支改为明确告知：意图已有结果、本次返回的是**同一批资料**、
   改写问法不会让检索结果变得不同、已能回答就**直接结束本轮的调用**。
2. prompt 第 6 条同步补充同一规则（软引导 + 硬提示双保险）。

**验证**：同一 query 实测调用次数 **4 → 2**，第二次 `stopped=duplicate_call_skipped`。
（单次采样，LLM 有随机性，不可断言稳定为 2；但机制上"改写措辞"与"原样重复"
两条路现在都有拦截。）全量 572 passed。

**方法论教训**：
- **回灌内容 ≠ 回灌状态**。把原始结果再喂一遍，模型无法感知"这是重复"；
  必须显式说明**它的行为状态**（你重复了、原因是什么、下一步该做什么），
  否则模型会一直在原地优化那个它认为"还没做好"的动作。

---

### ISSUE-20260918-REDACT-DEAD-CODE-AND-AUDIT-PII（勘查完成，未修复）
- 日期：2026-09-18
- 标题：脱敏工具是死代码；审计记录留存用户输入原文

**现象（勘查结论）**：
1. `app/common/security/redaction.py` 仅 11 行（`redact()` 保留头 3 尾 4 + `audit_context()`），
   **全仓库零调用——是死代码**。有工具，没人用。
   （注：`data_analysis_agent.py` 曾被关键词误命中，实为注释里的英文单词 masks。）
2. `audit_logs` 中 `customer_agent.tool_call` 的 detail 实测包含
   `arguments.query` = **用户输入原文**。若客户输入含手机号/身份证号，会原样落库，
   而审计可被有 `audit:read` 权限的员工经 `/trace` 接口回溯查看。

**同时确认的三处「干净」**（避免过度设计）：
- 日志中 `contact_phone`、`risk_level=`、`total_asset=` **零匹配**；
  无 `logger` 打印 content/query/payload 的调用
- `password_hash` 不在任何 schema / api 中，不会返回前端
- 模型输出预览已摘要化（`output_preview` 只出结构描述）

⚠️ 但上述"干净"目前**靠开发者自觉，没有兜底机制**——这是要做通用脱敏能力的核心理由。

**拟定方案（待确认范围）**：统一 `app/common/security/pii.py`，在数据流出边界应用——
① 日志层 `logging.Filter` 全局兜底（零侵入）；② 审计写库前对自由文本脱敏；
③ 收编死代码 `redact`，补手机号/身份证/银行卡/邮箱/姓名规则。

**排查教训（误报）**：初查用 `1[3-9][0-9]{9}` 扫日志命中大量 11 位数字，
差点判定为"手机号泄漏"。实际是 **Redis Stream 的 message_id**
（`1786612747148-0`，时间戳-序号格式）被截取命中。
→ **纯长度/前缀正则识别 PII 误报率极高**，必须带边界约束（前后非数字），
并优先结合**字段名**判断而非只扫内容；设计脱敏规则时须一并防误报（脱敏过度同样有害）。

---

### ISSUE-20260923-EVIDENCE-JUDGED-BY-ANSWER-WORDING
- 日期：2026-09-23
- 标题：答案已完整给出数据，却被判「资料不足」

**现象**：query=「公司客户规模」，答案完整给出注册用户 862万、有效客户 473万、
累计服务 615万、月活 328万，末尾附一句
「如需了解更详细的客户结构…当前引用资料未提供，建议联系人工客服」，
却被标为 `partially_answered`，界面出现「需要补充信息 / 当前资料未完全覆盖该问题」。
用户反馈原话：**「居然还说资料不够吗」**。

**根因**：`app/services/customer_rag_answer.py` 的状态判定**完全依赖扫描生成文本的关键词**：

```python
_INSUFFICIENT_MARKERS = ("资料不足", "资料中未", "资料未", "引用中未", ...,
                         "未包含", "未提供", "未明确", "未提及", "没有足够依据", "未找到")
def answer_reports_insufficient(text): return any(m in text for m in _INSUFFICIENT_MARKERS)
```

而模型在**礼貌性补充说明**里同样会用到这些词。那句「引用资料未提供」说的是
「你追问的附加信息没覆盖」，与「你问的问题没答上」是两回事——字符串匹配分不清。
（设计初衷见源码注释：prompt 要求模型"引用不足就说资料不足"，故用固定表述做结构化识别。
但模型不会严格沿用约定措辞，也无法区分指代对象。）

**修复**（提交 5264034）：
1. 生成 prompt 要求模型在末尾单独输出 `[[evidence:sufficient]]` /
   `[[evidence:insufficient]]`，并明确「标记只反映**用户所问的问题**是否答得上；
   问题已答清、只是附带说明别的信息未覆盖，仍应标 sufficient」
2. 新增 `split_evidence_flag()` 解析并**剥离**标记——内部约定不得泄漏给客户
3. 未输出标记时回退旧的关键词匹配；**回退通道保持宽口径**
   （金融场景漏判比误判更糟：误判只是多一句提示，漏判会让"其实没答上"被标成已完成）
4. 新增 `insufficient_source` 字段并透出到 task data
   （`explicit`=模型标记 / `keyword`=关键词回退）

**验证**：实测 `insufficient_source='explicit'`，答案中无 `[[evidence:...]]` 残留。
**但状态仍为 `partially_answered`**——模型给出的理由是
「以上均为个人客户统计，引用资料中未提供企业/机构客户数量」，
即它把「公司客户」理解为可能指企业客户，认为自己没答全。

→ 这是**模型的主动判断**，与修复前的字符串误判性质不同。
修复目标（把"机械猜测"换成"模型显式表态"、并使判定可追溯）已达成；
模型在该问句上的保守属于另一问题，需单独决定是否调生成尺度。

**方法论教训**：
1. **凡是要机器读的结论，都不要从自然语言措辞里反推**——应让生成方显式表态。
   本项目已因此修过两次：`reasoning`（改用 tool schema 必填参数）、
   本条（改用 evidence 标记）。同一类问题的第三次出现值得警惕，
   新写"从文本推断状态"的代码前应先自问能否改成显式通道。
2. **判定通道必须带"来源"字段**。本次若不透出 `insufficient_source`，
   就无法区分「模型认为没答全」与「措辞撞上标记词」——两者处置完全不同
   （前者调生成尺度，后者改匹配规则），只能靠猜。
3. 双通道（显式优先 + 关键词回退）设计时，**回退通道的松紧要按失效后果定**：
   此处保持宽口径（宁误判不漏判），因为漏判在金融场景后果更重。

**后续处置（同日，提交 27d4342）**：

核实前端显示条件时发现决定性事实——`AgentBubble.vue` 的判断是
`v-if="overall_status==='needs_clarification' || clarification"`，
即**只要 clarification 非空就弹「需要补充信息」，与状态位无关**。
故取"方案 C"：`graph.py` 的 `insufficient` 分支**只降级状态、不再自动设 clarification**。
状态位保留为机器信号（审计/统计可用），提示不再打断用户。

实测 6 个 case：
| Query | overall_status | 提示 |
|---|---|---|
| 公司客户规模有多大 | completed | 无（原误报场景） |
| 开户流程是怎样的 | completed | 无 |
| **我的订单状态（缺单号）** | **needs_clarification** | **有** ← 防误伤 |
| 我有没有风险限制 | completed | 无 |
| 你们公司的主营业务是什么 | completed | 无 |
| 什么是双录 | completed | 无 |

第 3 条证明改动**没有波及** `needs_clarification` 分支。

⚠️ **同时纠正一个中途的错误方向**：曾考虑改用检索层 `evidence_status` 作为判据，
但该字段的语义是「检索门控未拒绝」——`reject` 会直接转人工、**不进生成**，
故在生成阶段它几乎恒为 `sufficient`，拿它当判据等于让该状态永不触发。
**教训：使用一个信号前先确认它的确切语义与产生条件，不要只看字段名**。

**附注**：管线路径本就不产生 `partially_answered`（仅图路径有），
故本次改动只影响图路径；两条路径在"半成功"表达上的差异是切图的既有影响。

---

### ISSUE-20260923-CONVERSATION-TURN-ORDER
- 日期：2026-09-23
- 标题：会话历史里同一轮的问与答顺序错乱

**现象**：新增会话持久化后，实测读回的历史消息顺序错误：
```
[user] 公司客户规模有多大
[assistant] 根据最新公开资料…        ← 第 1 轮，正确
[assistant] "双录"是…                ← 第 2 轮的回答排到了提问前面
[user] 什么是双录
```

**根因**：`agent_conversation_message.created_at` 用 `server_default=func.now()`，
而 **PostgreSQL 的 `now()` 在同一事务内返回同一时刻**（事务开始时间）。
同一轮的问与答在**一次事务**里 `add_all` 插入 → 两条时间戳**完全相同**
→ 排序 `ORDER BY created_at, id` 退化为按 UUID 随机比大小。

**修复**：落库时显式指定时间戳，assistant 比 user 晚 1ms。
不选"加自增序号列"是因为那需要额外维护会话内计数；
1ms 偏移既保证顺序，又不改变"按时间浏览"的语义。

**验证**：修复后实测两轮问答顺序完全正确。

**方法论教训**：
1. **`now()` 是事务级的，不是语句级的**。凡在一次事务内插入"有先后含义"的多行，
   都不能指望 `server_default=func.now()` 给出有区分度的时间戳。
2. 这类缺陷**只在多轮场景下暴露**——单轮（一问一答）测试完全看不出问题。
   验证与时序相关的功能时，样本必须覆盖**两轮以上**。

---

### ISSUE-20260923-ALEMBIC-VERSION-DRIFT（环境问题，未根治）
- 日期：2026-09-23
- 标题：alembic 版本记录落后于实际 schema，导致迁移中断

**现象**：执行 `alembic upgrade head` 失败：
```
asyncpg.exceptions.DuplicateColumnError: column "scope" of relation "customer_memory" already exists
[SQL: ALTER TABLE customer_memory ADD COLUMN scope VARCHAR(32) DEFAULT 'private' NOT NULL]
```

**诊断**：
- `alembic current` → `0023_bad_case`（版本记录）
- `alembic heads` → `0026_agent_conversation_message`
- 但实际 schema **已含 0024 的全部变更**：`scope` / `agent_type` 两列，
  `ix_customer_memory_scope` / `ix_customer_memory_agent_type`，
  以及重建后的 `uq_customer_memory_active_key` 索引**全部存在**

即 DDL 已经生效，只是 alembic 版本表没跟上。

**处置**：逐项核对 0024 的变更确已应用后，`alembic stamp 0024_shared_memory`
补上版本记录，再 `upgrade head` 升级至 0026。

**未根治**：没有查清"DDL 是怎么在 alembic 之外被应用的"。
可疑方向：`docker/postgres/init` 下的初始化脚本，或某次手工执行未回写版本。
本项目此前也有"两套 schema 初始化并存"的记录，疑为同源。
**遗留风险**：只要脱节存在，后续任何新迁移都可能再次中断。

---

### ISSUE-20260923-HANDOFF-SILENT-BUILD

- 日期：2026-09-23
- 标题：转人工改了「建议 + 确认」，但兜底分支仍在静默建单

**现象**：用户实测反馈——**「卡片确实出现了，但是还是不行啊，就是逻辑上还是直接转了人工然后就卡片就没了」**。
确认卡片能渲染，但只要走到某条路径，工单与人工会话仍被直接创建。

**根因**：`app/agents/graph.py` `persist_node` 里的一段判据是**兜底**而非判据：

```python
low_conf = any(r.error == "low_confidence" for r in results)
if low_conf:
    patch.update({"handoff_suggested": True, ...})   # 只建议
else:
    # 注释写的是"用户主动要求转人工"
    handoff = await CustomerRagHandoffService(...).handoff(...)   # 直接建单
```

`else` 把「**没有** low_confidence」当成了「用户主动要求转人工」。任何其他来源的
`needs_handoff`（`plan.requires_handoff`、`handoff_node` 已判过的澄清耗尽）都会落到
这一支被静默建单——`handoff_node` 自己也有一个同构的 else 分支，会被这里二次放大。

实测 query=「转人工」的响应：
```
overall_status=handoff   handoff_suggested=False        ← 前端因此永不弹卡片
work_order_id / ticket_no / customer_service_session_id 全部已生成
answer=「可以为您转接人工客服。下一步：是否现在转接人工客服？已为您转接人工客服，工单号：WO…」
```
最后一句尤其荒谬：**同一段话里刚问完「是否现在转接」，又写「已为您转接」**——
系统自己问自己答，用户没有任何选择权。

**修复**（提交 7164973）：
1. 新增模块级纯函数 `_handoff_reason(plan, low_confidence)`，按**具体程度**给显式判据，
   不再有兜底分支：`customer_requested`（plan 里有 human_handoff 任务）→
   `low_confidence` → `clarification_exhausted`（两者皆否）
2. 判定上移到 `summarize_node`（纯计算、不依赖数据库，行为可被单测直接锁住），
   `handoff_node` 同样只建议；`persist_node` 的建单逻辑整体移除
3. **全图只剩一个建单入口**：`POST /conversations/{id}/handoff-confirm`

**连带修复**：
- 确认接口原先用固定串 `"用户确认转人工"` 当 query，而工单按 `sha256(intent:query)`
  去重 → 同一客户每次确认都命中同一条工单（实测复用了 2026-08-14 的旧单号）。
  改为取该会话最后一条用户消息，工单描述因此是真实提问原文。
- 前端卡片原先只依赖内存里的 `result`，刷新即消失——**用户看到过建议却再也找不到
  确认入口**。改为写 `sessionStorage` 并在挂载时恢复。

**验证（实机，两端对账）**：
| 场景 | handoff_suggested | reason | 工单/会话 |
|---|---|---|---|
| 转人工 | True | customer_requested | **全为 None** |
| 请介绍一下贵公司的太空采矿业务 | True | low_confidence | **全为 None** |

数据库对账：人工会话 144→**144**、工单 99→**99**（修复前每问一次都会 +1）。
随后点确认 → 工单 `WO20260923075021ADF851`（描述=用户真实提问原文）+ 会话
`waiting_human`，总数 145/100（只 +1）。

**方法论教训**：
1. **`if A else B` 里的 `else` 若没有独立判据，就是兜底**——它会把所有未预料到的
   输入都归到 B。写这种分支前必须能回答"B 的判据是什么"，答不上来就说明 B 是错的。
2. **同一副作用有多个可达入口时，改动几乎必然漏掉一条**。本次上一轮只改了两处，
   实际有四处（`handoff_node` 两条分支 + `persist_node` + 管线路径）。正确的收口方式是
   把判定集中、把执行收敛到**唯一入口**，而不是逐个改调用点。
3. 前端只依赖内存态渲染的交互（卡片、确认框），**刷新即失**，用户会看到"提示过但找不到入口"。
   凡是要用户**动作**的提示，都要持久化。

**遗留（未修）**：管线路径 `app/services/customer_agent_execution.py:528,539`
**仍是直接建单**（澄清耗尽 / needs_handoff 两处）。当前图开关为 true，该路径不执行，
故本次未一并改。但**一旦把 `AGENT_GRAPH_ENABLED` 回退为 false，静默建单就会复现**——
回退前必须先改这两处。

---

### ISSUE-20260923-HANDOFF-ASKS-UNANSWERABLE-QUESTION

- 日期：2026-09-23
- 标题：转人工反问「是否现在转接人工客服？」，而系统处理不了这个回答

**现象**（用户实测四轮对话原文）：
```
用户：转人工
系统：可以为您转接人工客服。
      下一步：是否现在转接人工客服？          ← 系统问了
用户：是的
系统：您想查询哪一类信息，例如订单、工单、资产、风险限制或公司业务？   ← 完全答非所问
用户：转人工 / 是
系统：为避免反复追问，建议转接人工客服。
```
用户的观感是"我明明答了是，它却在乱问"。

**根因**：`app/services/customer_task_executor.py:289`
```python
if item.intent == "human_handoff":
    return TaskExecutionResult(item.task_id, item.intent, "needs_clarification",
                               answer="可以为您转接人工客服。",
                               clarification="是否现在转接人工客服？")
```
这是项目原本的「两步式转人工」：第一步反问、第二步用户确认。
但**第二步从未实现**——用户答"是的"会被当成一条**全新提问**重新走意图识别，
落到未知意图的兜底澄清（"您想查询哪一类信息…"）；同时 `clarification_count`
被推高，两次之后升级成 `clarification_exhausted`，于是有了最后那句"为避免反复追问"。

即：**系统问了一句它自己无法处理的话**。这不是措辞问题——它向用户承诺了一个不存在的后续。

**修复**（提交 92e2cce）：
- `human_handoff` 任务不再产出 `clarification`，状态由 `needs_clarification` 改为
  `succeeded`（如实：这一轮确实完成了它的职责），答案改为
  「可以为您转接人工客服，请在下方确认。」
- 确认动作交给前端卡片（`handoff_suggested` → 按钮 → 确认接口）。
  那是**可执行**的动作；文本反问不是。
- 原则：**不问做不到的问题**。

**实测修复后**（同一四轮）：
| 轮 | 输入 | overall_status | clarification | 工单/会话 |
|---|---|---|---|---|
| 1 | 转人工 | handoff | **None** | 全为 None |
| 3 | 转人工 | handoff | **None** | 全为 None |

「需要补充信息」块与「下一步：…」行均消失，卡片成为唯一确认入口。

**已知残留（未修）**：用户**打字**「是的」而不点卡片时，仍会被当作新提问。
系统现在不再邀请用户打字回答，但用户若执意打字则无效。
**未做"识别肯定词"的兜底**——本项目已三次因"从自然语言猜状态"出错
（reasoning 恒空、evidence 误判、本次），不该再添一处。

**方法论教训**：
1. **反问前先自问"这个回答我处理得了吗"**。产出 clarification 意味着向用户
   索取信息，那必须有一条**能消费它**的路径；否则就是承诺了不存在的后续。
   本轮是"问了做不到"，上一轮（HANDOFF-SILENT-BUILD）是"没问就做了"——
   一对互补的失效模式，同源于**交互与执行两侧没有对齐**。
2. **同一个功能的两个方向要一起检查**：确认流程既要防"没确认就执行"，
   也要防"问了确认却不执行"。修完一半时很容易以为已经完成。

---

### ISSUE-20260923-STALE-INDEX-HTML-CACHE

- 日期：2026-09-23
- 标题：后端与产物都已更新，页面却纹丝不动——index.html 没有缓存指令

**现象**：转人工的确认卡片在页面上不出现。但：
- 后端接口实测返回 `handoff_suggested=True`；
- **WS 载荷也验证正确**（用容器内 `websockets` 直连页面所用通道，
  `agent.final.result` 含 `handoff_suggested=True`、`clarification=None`）；
- 前端构建产物里确实含有卡片代码（bundle 中 `handoff_suggested` / `handoff-suggest` /
  `确认转接` 均命中）。

三项全对，功能却不出现。

**根因**：`frontend/nginx.conf` 里 **index.html 没有任何 `Cache-Control`**。

```
$ curl -sI http://localhost/
HTTP/1.1 200 OK
Last-Modified: Wed, 23 Sep 2026 07:49:16 GMT
ETag: "6ab3847c-16c"          ← 只有 ETag，无 Cache-Control

$ curl -sI http://localhost/assets/index-r8fR-1tW.js
Cache-Control: max-age=604800 ← 资源有，入口没有
```

无缓存指令时浏览器按**启发式规则**自行决定时长（依据 Last-Modified）。
于是新构建虽换了产物文件名（`index-<hash>.js`），浏览器仍用**旧 index.html**，
指向旧 bundle → 页面表现成"代码改了却毫无变化"。

带哈希的资源长缓存是对的（内容变即换名），**唯独 index.html 是"指向当前产物的那张表"，
必须每次校验**。原配置恰恰漏了这一点。

**修复**（提交 a75f051）：
- 新增 `location = /index.html` → `Cache-Control: no-cache, must-revalidate`
- `location /` 兜底同样 no-cache
- assets 保持 `max-age=604800` 不变

**验证**：`/` 与 `/customer-center/service`（SPA 回退路径）均返回
`no-cache, must-revalidate`；assets 仍长缓存。

**方法论教训**：
1. **"改了没生效"要先分清是三层中的哪一层**：后端行为 / 构建产物 / 浏览器所载资源。
   逐层证伪比反复改代码有效——本次正是先证明"后端对、产物对"，
   才把范围压缩到"浏览器加载了什么"。
2. **SPA 的 index.html 必须显式声明不可缓存**，这是部署级常识，但漏了极难自查：
   现象像代码问题，实则与代码无关。凡"新功能在容器里验证通过、在浏览器里不出现"，
   先查响应头。

**⚠️ 结论更正（同日稍后）**：本条**不是**当时"确认卡片不出现"的原因。
真正原因是**卡片加在了错误的组件里**——客户实际使用全局悬浮助手
`AgentBubble.vue`，而卡片只加在智能客服页 `CustomerRagView.vue`
（见 `ISSUE-20260923-HANDOFF-CARD-IN-WRONG-COMPONENT`）。
本条是排查过程中发现的**另一个独立真实缺陷**（硬刷新后仍可能加载旧产物），
值得独立修复与保留，但**排查记录里必须标明它解释错了症状**——
否则后人读到会误以为"缓存查过了，那卡片问题就解决了"。
教训叠加：**"三层证伪"证明的范围（后端对、产物对、浏览器加载对）并不自动指向根因，
它只排除了这三层；如果三层的对象本身就选错了（卡片根本不在用户所在的组件里），
再严密的证伪也到不了答案。**


---

---

### ISSUE-20260923-HANDOFF-CARD-IN-WRONG-COMPONENT

- 日期：2026-09-23
- 标题：转人工确认卡片加在了错误的组件里——客户实际用的是全局悬浮助手

**现象**：后端行为已按前两条修好（只建议不建单、不再反问无法回答的问题），
接口与 WS 载荷都验证正确，但用户始终**在页面上找不到确认按钮**：
> 「可以为您转接人工客服，请在下方确认。卡片反而没了啊，没看到前端的按钮啊」

**根因**：客户侧有**两个**聊天入口，我只改了一个。

| 组件 | 位置 | 是否加了卡片 |
|---|---|---|
| `views/CustomerRagView.vue` | 智能客服页 `/customer-center/service` | 上一轮加的 |
| `components/AgentBubble.vue` | **全局悬浮「智能助手」，挂在 App.vue，客户登录后所有页面可见** | **漏了——而客户用的正是这个** |

AgentBubble 里没有确认卡片，只有一块标题为「需要补充信息」的 `agent-clarification` 块。
**用户把那一块当成了「卡片」**；而上一轮我把 `clarification` 去掉后它就消失了
→ 于是表现为「卡片反而没了」，真正的确认按钮则从未在该窗口出现过。

**这也解释了为什么连续三轮「后端全部验证通过、页面却毫无变化」**：
行为改对了，但承载该交互的组件不是用户所在的那个。

**修复**（提交 2891add）：在 AgentBubble 的助手消息内**就地渲染**确认卡片，
对齐既有的交易草稿卡模式（就地渲染 + 按钮 + 按消息记录已处理态），不另造交互范式：
- `[确认转接]` → `POST /conversations/{id}/handoff-confirm` → 写人工会话、
  启动轮询、追加「已为您转接，工单号：X」
- `[继续对话]` → 标记该条消息 `handoff_resolved`
- 措辞按 `handoff_reason` 区分

两个入口共用同一个 `conversation_id`（同在 sessionStorage 的
`customer_service_conversation_id`），故确认接口对两边都成立。

**方法论教训（本轮最贵的一条）**：
1. **「三层证伪」只排除那三层，不自动指向根因**。我证明了后端对、产物对、
   浏览器加载也对，但三层的**对象选错了**——卡片根本不在用户所在的组件里。
   证伪再严密，也到不了真正的答案。
2. **前端出现「后端都对但界面无变化」时，先问一句：用户此刻用的是哪个入口/组件。**
   不要假设「页面」就是唯一的承载者。本项目客户侧同时存在页面视图与全局悬浮窗，
   员工侧还有控制台视图——同一能力常有多个承载组件。
3. **改交互能力时要先查该能力的全部承载方**（`AgentTracePanel` 也同时被
   AgentBubble 与 CustomerRagView 使用，是同类结构）。这与
   `ISSUE-20260923-HANDOFF-SILENT-BUILD` 的第 2 条教训同源：
   **同一能力/副作用有多个可达入口时，只改一处几乎必然漏**——
   只是那次漏在后端调用点，这次漏在前端组件。

---

### ISSUE-20260924-CUSTOMER-OWN-DATA-DENIED

- 日期：2026-09-24
- 标题：客户查自己的资产/持仓/余额，被回一句「当前身份不具备该领域助手权限」

**现象**（用户反馈"本人应该能查询本人的所有内容"）。实测三条全部如此：
```
我的持仓有哪些  → 当前身份不具备该领域助手权限，已由客服助手为您提供受控说明或下一步指引。
我的资产有多少  → 同上
我的账户余额    → 同上
```

**根因：接错线**。`app/services/customer_agent_execution.py`：
```python
AGENT_ROUTE_BY_INTENT = { ..., "asset_summary": "analysis", ... }
AGENTS_BY_ROUTE       = { ..., "analysis": ["DataAnalysisAgent"], ... }   # 员工侧
```
`asset_summary` 被指派给**员工侧数据分析 Agent**（需 `DataAnalysisAgent` 权限），
而面向客户的只读工具 `get_my_asset_summary` **早已实现**（资产快照 + 前 10 条持仓）。

更值得记的是失效链条——图里的模型工具轮其实已经把数据取到了，
却在 `customer_service.py` 的 Agent 权限守卫处被处置：
```python
selected = [agent for agent in result.agents if agent in available_agents]
if not selected:
    result.answer = "当前身份不具备该领域助手权限，…"
    result.results = []          # ← 把已经取到的数据一并丢弃
```
**权限守卫本身没错，错的是喂给它的路由**：守卫按「路由 → Agent → 权限」判定，
而路由取自一张与实现脱节的映射表，于是既取到了数据又全部丢掉。

**修复**（提交 5f5259a）：
1. `asset_summary` → `customer_service`。
   员工侧不受影响：员工的持仓查询走 NL2SQL 模板分支并在其之前早退，
   且 `/agent/execute` 对员工不进入 `_run_customer_agent`。
2. `_asset_summary` 的 answer **补上持仓明细**（原先只说"当前展示 N 条有效持仓"）。
   必须写进 answer 而非只放 `data`——模型工具轮喂给模型的只有 `answer` 文本
   （`context_lines.append(f"[{tool_name}] {result.answer[:600]}")`），
   `data` 不参与生成，只给条数等于没给。
3. 补触发词 `交易记录 / 交易明细 / 成交记录 / 订单记录` → `order_status`
   （原先只认"订单"，「我最近的交易记录」落到未知意图）。
   `CustomerQueryRouter._ORDER` 本就含"交易"，故只补触发词即可对齐，不必改路由。

**验证**：
| 查询 | 修复前 | 修复后 |
|---|---|---|
| 我的持仓有哪些 | 权限拒绝 | 列出 2 条持仓（含市值/盈亏） |
| 我的资产有多少 | 权限拒绝 | 总资产 + 现金余额 + 持仓 |
| 我的账户余额 | 权限拒绝 | 同上 |
| 我最近的交易记录 | 未知意图 | order_status（该客户确无订单，如实回答） |

**方法论教训**：
1. **权限守卫只会如实执行，它挡下的往往是自己人的错误接线。**
   见到"权限不足"先别急着改权限配置，先问：这个请求**本该**由哪个 Agent/工具处理？
   本例中客户的资产查询本就有专用只读工具，权限配置反而是对的。
2. **"取到了数据但用户看不见"要同时看两面**：一是路由/权限是否把结果丢了；
   二是**给模型看的上下文里到底有没有数据**。
   只修前者会得到"状态正确但答案没内容"。
3. **错误话术可能来自与故障无关的层**。此例的错误文案（权限）与真实问题
   （映射表过时）没有直接关系，顺着话术排查会一直查权限配置而找不到症结。

---

### ISSUE-20260924-BUILD-BEFORE-EDIT-FINISHED（操作失误，自查）

- 日期：2026-09-24
- 标题：镜像构建在代码改完之前启动，导致新接口在容器里 404

**现象**：新加的 `GET /customer-service/conversations` **本地路由自检通过**
（显式遍历 `api_router` 能拿到该路径），线上却返回 **HTTP 404**。

**根因**：**构建是在继续编辑代码期间启动的**。镜像产出的是"启动构建那一刻"的
代码快照，之后写入的改动自然不在其中。同一轮里先启动构建、再添加接口，
于是镜像里没有这个接口。

**教训：先改完所有代码，再启动构建。**
本项目 api 镜像含 pip 依赖层，构建常见 5–10 分钟，"边构建边改"很诱人，
但它会产出一个**看起来成功、内容却是旧的**镜像——比构建失败更难发现
（构建日志全绿、健康检查也过）。
**判定线索：本地自检通过 + 线上 404，优先怀疑"镜像比代码旧"**，而不是路由写错。
自查动作：`git status` 确认改动都已落地再 build；构建期间不要再动被测代码。

---

### ISSUE-20260924-WASTED-ROUND-ON-UNREACHABLE-TOOL（未修，待决策）

- 日期：2026-09-24
- 标题：模型每轮都白尝试一次它不可能调用的工具（约 1.2s / 1739 tokens）

**现象**（用户实测「我最近的交易记录」，并质疑"这个第二次工具该被拦截嘛"）：
```
第 1 层 · 2 次工具调用 · 1 次被拦截 · 3235ms · 3394 tokens
#1 get_my_order_status     成功  决策 2021ms  1604↑ 51↓
#2 explain_order_status    被拦截 决策 1210ms  1666↑ 73↓   ← 白花一轮
   输出未执行：该工具不在本次计划范围内
停止原因：tool_out_of_plan_scope
```

**先纠正一个问题定性**：这条**不是**"工具永远调不到"的缺陷。
`explain_*` 在**模型侧不可达是刻意设计**——它们只能由规划器通过
「多意图 + depends_on」触发（`customer_intent.py` 产出依赖任务 →
`customer_task_executor.py` 的 `item.depends_on and item.intent in {workflow,faq,policy}` 分支
确定性地调它们）。原因见 `tests/test_model_tool_scope_constraint.py` 顶部记录的
2026-09-14 事故：放开给模型临时决定时，知识库缺料 → explain 返回 `low_confidence`
→ 整体状态被污染成 `partially_completed + requires_handoff`，用户**已拿到事实**
却被回复"没有足够依据、建议转人工客服"，自相矛盾。
（我曾误判为缺陷并试图"配对放开"，5 个既有测试因此失败——**测试挡住了我**。）

**真正的问题在这里**：既然不允许模型调用这两个工具，
**就不该把它们的工具定义发给模型**。当前 `_tools_for_intents` 已经算出了
"本次计划允许的工具集"，但这个集合只用于**事后拦截**，没有用于**裁剪发给模型的 schema**。
于是模型每轮都看得见、每轮都可能去试，试了必被拒——决策 token 与延迟照付。

**证据（同一轮）**：被拦那轮 1210ms / 1739 tokens，占总耗时 37%、总 token 51%。
且这不是偶发——查工单、查订单这两类最常规的事实查询都会走到同一条路。

**拟定修法（未实施，属行为变更）**：
在工具轮里用 `_tools_for_intents(planned_intents)` 裁剪传给 provider 的
`tools` 参数（`planned_intents` 为空时保持不裁剪，向后兼容）。
`tool_out_of_plan_scope` 守卫保留为兜底——正常情况下不应再触发。

**为何未擅自实施**：这会改变模型可见的工具面（模型不再能"试探"计划外的工具），
属行为变更；而本项目在工具可见性上已有一处刻意设计（见上），
不宜由我单方面再动一次。

**方法论教训**：
1. **既有测试是"前人踩坑的记录"。** 本次正是在测试失败后去读文件头，
   才看到 2026-09-14 的事故背景。若图省事直接把测试改成符合自己改动的样子，
   就等于拆掉了一个真实事故的防护。
2. **"看起来像遗漏"的设计，先找它为什么这样。** 两个工具在模型侧不可达
   极像疏漏，实则有明确出处与事故依据。
