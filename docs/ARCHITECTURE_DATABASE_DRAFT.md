# 智能财富管家系统整体架构与数据库设计前置方案

> 文档状态：方案草案（待负责人评审）  
> 编写日期：2026-07-24  
> 关联计划：`docs/PLAN.md`

## 1. 本轮结论

### 1.1 技术选型方向

- 前端：Streamlit
- 后端：FastAPI
- 编程模型：应用层统一使用 `async/await`
- ORM：SQLAlchemy 2.x Async ORM
- 主业务数据库：PostgreSQL 或 MySQL 二选一，禁止同一业务数据默认双写
- Redis：缓存、会话、任务队列、事件通知
- Milvus：文档和记忆的向量检索
- Neo4j：可选 GraphRAG，不作为 MVP 的硬依赖
- 主模型：暂定 DeepSeek
- Embedding：阿里云 DashScope `text-embedding-v4`，维度通过配置固定，当前参考工程配置为 1024
- 部署：Docker + WSL2，暂不创建 Compose，先完成架构和表设计

### 1.2 推荐的数据库决策

建议将 **PostgreSQL 作为主业务数据库**，原因：

1. 与现有 `C:/pg_prefect` 的异步 SQLAlchemy、`asyncpg`、PostgreSQL/pgvector 方案可以直接复用。
2. PostgreSQL 对 JSONB、全文检索、事务、约束和复杂查询支持较好。
3. 如果后续需要临时将向量从 Milvus 回退到 pgvector，迁移路径更顺。
4. 能减少 MySQL、PostgreSQL 双数据库维护和数据一致性问题。

考虑到团队中 MySQL 使用者更多，建议采用以下折中方案：

- 第一版代码通过 SQLAlchemy ORM 编写，不在业务层写 PostgreSQL 专属 SQL。
- 先提供 PostgreSQL 迁移和初始化方案。
- 同时保持 MySQL 兼容边界：避免 PostgreSQL 专属类型扩散到全部模型。
- 如果团队评估后决定 MySQL，主要业务表可以迁移，RAG 文档元数据和向量仍由 Milvus 管理。
- 不建议 PostgreSQL 与 MySQL 同时作为主库。

### 1.3 推荐的 Agent 决策

建议采用：

> **强 Agent + Tool Calling 为主，有限的多 Agent 协作作为扩展。**

不建议一开始做五个完全自治、互相调用的 Agent。更适合当前项目的是：

```text
一个统一编排 Agent / Supervisor
    |
    +-- 客服场景 Tool 集
    +-- 客户画像与投顾 Tool 集
    +-- 数据分析 Tool 集
    +-- 业务操作 Tool 集
    +-- 风控 Tool 集
    +-- 记忆和知识库 Tool 集
```

只有在业务边界和权限差异明显时，才将工具集包装成领域 Agent，例如：

- CustomerServiceAgent：知识问答、产品说明、政策咨询
- AdvisorAgent：画像分析、适当性筛选、产品解释
- RiskAgent：规则评估、预警查询、风险说明
- AnalystAgent：只读 NL2SQL
- OperationAgent：申购、赎回、工单等受控操作

但这些 Agent 通过统一 Supervisor 调度，不允许互相无限递归调用。

## 2. 检查结果：现有项目能否复用

已检查：

- `C:/pg_prefect`
- `C:/simple_rag`

结论：**可以复用，而且复用价值较高，但建议“抽取核心能力后迁移”，不要直接复制整个项目。**

### 2.1 `C:/pg_prefect` 可复用内容

`C:/pg_prefect` 已经具备较成熟的文档导入链路：

- PostgreSQL + pgvector 数据结构
- SQLAlchemy 2.x Async ORM
- `asyncpg`
- 文档注册与版本管理
- 文档内容 Hash
- 文档版本切换
- Chunk 版本关联
- 软删除
- Embedding 缓存
- 文档状态和同步运行记录
- FastAPI ingestion service
- Prefect 调度
- Docker 化服务

尤其值得复用的模块：

```text
pipeline.py
├── AsyncEngine / AsyncSession 设计
├── 文档注册与版本切换
├── Chunk Hash 复用
├── Embedding 缓存逻辑
├── 文档状态机
└── PostgreSQL 初始化表设计
```

但是 `pipeline.py` 目前更偏“完整 ingestion pipeline”，迁移到财富管家后要拆成：

```text
document_service
├── parser
├── chunker
├── embedding_service
├── vector_index_service
└── document_version_repository
```

不建议把 Prefect 直接作为所有后台任务的基础。Redis 队列更适合承载实时业务任务，Prefect 可以保留给：

- 全量知识库重建
- 每日/每周记忆维护
- 批量数据同步
- 失败任务重跑

### 2.2 `C:/simple_rag` 可复用内容

`C:/simple_rag` 已具备较完整的 RAG 查询实现：

- Qwen / DashScope OpenAI 兼容调用
- AsyncOpenAI
- `text-embedding-v4` 配置
- Redis Embedding 缓存
- Milvus 向量集合
- Dense + BM25 混合检索
- RRF 重排
- TopK 自适应
- 检索去重
- 检索耗时统计
- 查询规划和问题路由
- Redis `BRPOP` 异步队列
- 异步 ingestion worker
- Milvus 阻塞 SDK 通过 `asyncio.to_thread` 调用

可重点复用：

```text
core/qwen_client.py
core/retrieval.py
core/retrieval_policy.py
core/query_planner.py
core/planner_runtime.py
rag_core/until/text_splitter.py
db/ingest.py
workers/ingestion_worker.py
```

### 2.3 复用时需要调整的地方

现有两个项目不是完全异步到底：

- Milvus Python SDK 主要是同步客户端，需要继续使用 `asyncio.to_thread` 隔离阻塞调用，或者后续替换成异步兼容客户端。
- Streamlit 本身是同步运行模型，前端调用异步后端时建议通过 HTTP 客户端，不要在 Streamlit 内部直接管理复杂事件循环。
- `C:/simple_rag` 中部分 Redis 客户端和数据库实现需要统一到 `redis.asyncio` 和 SQLAlchemy Async ORM。
- `C:/pg_prefect` 的 Pipeline 与 `C:/simple_rag` 的 Milvus 代码存在职责重叠，需要合并成一套接口。
- `C:/simple_rag` 目前使用 SQLite 保存任务状态，正式项目应迁移为 PostgreSQL/MySQL 的任务表，Redis 只负责队列传递。

## 3. 目标整体架构

```text
                         ┌─────────────────────┐
                         │ Streamlit Frontend  │
                         │ 对话/画像/风控/管理 │
                         └──────────┬──────────┘
                                    │ HTTP / SSE
                         ┌──────────▼──────────┐
                         │ FastAPI API Gateway │
                         │ 鉴权/限流/响应模型  │
                         └──────────┬──────────┘
                                    │
              ┌─────────────────────▼─────────────────────┐
              │ Application Orchestrator / Supervisor     │
              │ 意图识别、策略、权限、Tool Calling、审计 │
              └──────┬───────────┬───────────┬─────────────┘
                     │           │           │
        ┌────────────▼───┐ ┌─────▼────────┐ ┌▼──────────────┐
        │ Knowledge Tools │ │ Data Tools  │ │ Operation     │
        │ RAG/GraphRAG    │ │ Profile/SQL │ │ Tools         │
        └────────────┬───┘ └─────┬────────┘ └────┬───────────┘
                     │           │               │
              ┌──────▼───────────▼───────────────▼──────┐
              │ Domain Services                         │
              │ customer/product/risk/memory/transaction│
              └──────┬───────────┬───────────────┬──────┘
                     │           │               │
       ┌─────────────▼──┐ ┌──────▼─────┐ ┌───────▼────────┐
       │ PostgreSQL/MySQL│ │ Redis      │ │ Milvus         │
       │ 业务主数据/审计 │ │ Queue/Event│ │ 向量检索       │
       └─────────────────┘ └────────────┘ └────────────────┘
                                     │
                              ┌──────▼──────┐
                              │ Neo4j 可选  │
                              │ GraphRAG    │
                              └─────────────┘
```

## 4. 代码异步边界

### 4.1 必须异步的部分

- FastAPI 路由
- SQLAlchemy Async ORM
- Redis 连接、队列生产和消费
- LLM 请求
- Embedding 请求
- 文档导入 Worker
- 业务服务和 Repository
- 事件处理器

### 4.2 允许通过线程隔离的同步 SDK

Milvus 当前 Python SDK 很多操作仍然是同步调用。统一封装：

```python
result = await asyncio.to_thread(
    milvus_client.hybrid_search,
    collection_name=collection_name,
    reqs=reqs,
    ranker=ranker,
)
```

禁止在 FastAPI 请求协程里直接执行可能长时间阻塞的 Milvus、文件解析或模型同步调用。

### 4.3 Streamlit 边界

Streamlit 是展示层，不要求内部所有代码都是异步。推荐：

```text
Streamlit
    └── httpx.AsyncClient 或同步 httpx Client
            └── FastAPI 异步 API
```

真正的异步业务逻辑放在 FastAPI 和 Worker 中。

## 5. Redis 队列设计

建议不要只用普通 `LPUSH/BRPOP`，第一版可以采用 Redis Streams，或者先用可靠队列封装。

### 5.1 推荐选择

- 简单任务：Redis Streams + Consumer Group
- 必须快速落地时：Redis List + processing list + ack/retry
- 不建议只使用无确认的 `BRPOP` 承载交易和重要风控任务

### 5.2 队列分类

```text
stream:wealth:ingestion       # 文档解析、切块、Embedding、入库
stream:wealth:risk            # 交易风控评估
stream:wealth:memory           # 记忆提取和维护
stream:wealth:notification     # 风险预警和系统通知
stream:wealth:analytics        # NL2SQL 或报表任务
```

### 5.3 事件与任务的区分

任务：必须被某个 Worker 完成，有状态、重试和幂等要求。

```text
knowledge.ingest
risk.evaluate_transaction
memory.extract
analytics.execute_readonly_sql
```

事件：表示事实已经发生，可以被多个消费者订阅。

```text
transaction.created
risk.alert.created
memory.updated
knowledge.version_activated
```

推荐使用：

- Redis Streams：任务队列
- Redis Pub/Sub 或 Streams：实时通知
- PostgreSQL/MySQL：最终任务状态和审计记录

### 5.4 任务消息基本格式

```json
{
  "task_id": "uuid",
  "task_type": "risk.evaluate_transaction",
  "aggregate_type": "transaction",
  "aggregate_id": "uuid",
  "payload": {},
  "attempt": 0,
  "trace_id": "uuid",
  "created_at": "2026-07-24T00:00:00Z"
}
```

### 5.5 可靠性要求

- 消费组 ACK
- Pending 列表巡检
- 最大重试次数
- 指数退避
- Dead Letter Stream
- 任务幂等键
- 任务状态落库
- 关键业务事件使用 Outbox 表，避免数据库提交成功但事件发布失败

## 6. Agent 与 Tool Calling 方案

### 6.1 为什么不建议一开始做纯多 Agent

五个完全自治 Agent 会增加：

- 路由复杂度
- Prompt 管理复杂度
- Agent 间上下文传递成本
- 调试难度
- 重复调用 LLM 的成本
- 权限和审计边界
- 循环调用和错误传播风险

当前项目目标是可控、可演示、可答辩，不是构建完全自治的 Agent 社会。

### 6.2 推荐模式：Supervisor + Domain Tools

```text
用户输入
  ↓
规则路由 / 轻量意图分类
  ↓
Supervisor
  ├── 加载角色和客户上下文
  ├── 判断是否需要知识检索
  ├── 判断是否需要数据库查询
  ├── 判断是否需要用户确认
  ├── 选择允许的 Tool 集
  └── 调用 DeepSeek 生成解释
```

工具示例：

```text
search_knowledge
search_qa_pairs
get_customer_profile
get_customer_risk
get_holdings
match_suitable_products
calculate_risk_alerts
generate_readonly_sql
execute_readonly_sql
create_purchase_draft
confirm_purchase
create_redemption_draft
confirm_redemption
recall_memory
save_memory
```

### 6.3 领域 Agent 的使用方式

领域 Agent 不是完全独立服务，而是受 Supervisor 管理的策略模块：

```python
class DomainAgent:
    name: str
    allowed_tools: set[str]
    system_prompt: str
    require_confirmation: bool
```

例如：

```text
客服 Agent：只允许知识检索、FAQ、会话记忆
投顾 Agent：允许画像读取、风险读取、产品匹配
风控 Agent：允许规则评估、预警查询、工单创建
分析 Agent：只允许只读 SQL
操作 Agent：只能创建草稿和执行确认后的操作
```

### 6.4 什么时候再拆成真正多 Agent

满足以下条件之一再拆：

1. 某个领域需要独立上下文和独立记忆。
2. 某个领域需要独立队列和独立 Worker。
3. 某个领域需要独立权限或审批链。
4. 某个领域的 Prompt、Tool 和评测集已经稳定。
5. 多 Agent 协作能明显改善业务结果，而不是为了形式上的“多 Agent”。

## 7. 数据库分层设计

### 7.1 主业务库

PostgreSQL / MySQL 负责：

- 用户、角色和权限
- 客户画像
- 产品
- 风险评估
- 交易和持仓
- 风控预警
- 会话归档
- 记忆结构化元数据
- 任务状态
- 审计日志
- Outbox 事件

### 7.2 Redis 负责

- 会话短期缓存
- 热门画像缓存
- Embedding 缓存
- 队列和消费状态
- 幂等锁
- 限流
- 流式输出临时状态

Redis 不作为最终事实数据库。

### 7.3 Milvus 负责

- 文档 Chunk 向量
- FAQ 向量
- 记忆向量
- 未来产品描述或政策条款向量

Milvus 中保存内容和检索元数据；最终状态、版本、权限和审计信息仍以主业务库为准。

### 7.4 Neo4j 负责

- 客户、产品、行业、风险等级、政策之间的关系
- 关系路径查询
- GraphRAG 增强

Neo4j 中的数据应当可以从 PostgreSQL/MySQL 和文档知识库重建，不应成为唯一事实来源。

## 8. 业务数据库表设计

以下设计以 PostgreSQL 为优先，同时尽量保持 MySQL 可迁移。

### 8.1 约定

- 主键统一使用 UUID，便于异步任务、跨服务和数据导入
- 金额使用 `NUMERIC(20, 2)`，禁止使用浮点数
- 时间统一存储 UTC `TIMESTAMPTZ`；前端按北京时间展示
- 状态字段使用字符串或枚举约束，避免数据库强绑定复杂枚举
- 所有核心表包含 `created_at`、`updated_at`
- 涉及隐私的身份证、手机号、银行卡号只保存脱敏值或加密值
- 交易、审计、风控记录原则上不物理删除

### 8.2 系统用户与权限

#### `sys_user`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 用户主键 |
| username | VARCHAR(64) | 登录名 |
| display_name | VARCHAR(100) | 显示名称 |
| user_type | VARCHAR(32) | customer/advisor/risk/analyst/admin |
| phone_masked | VARCHAR(32) | 脱敏手机号 |
| password_hash | VARCHAR(255) | Mock 登录密码摘要，可选 |
| status | VARCHAR(20) | active/disabled |
| last_login_at | TIMESTAMPTZ | 最近登录时间 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

#### `sys_role`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 角色主键 |
| code | VARCHAR(64) | 角色编码 |
| name | VARCHAR(100) | 角色名称 |
| permissions_json | JSON/JSONB | 权限列表 |
| created_at | TIMESTAMPTZ | 创建时间 |

#### `sys_user_role`

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | UUID | 用户 ID |
| role_id | UUID | 角色 ID |
| created_at | TIMESTAMPTZ | 创建时间 |

### 8.3 客户域

#### `fin_customer_profile`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 客户画像主键 |
| user_id | UUID | 关联 sys_user |
| customer_no | VARCHAR(64) | 客户编号 |
| customer_type | VARCHAR(32) | retail/high_net_worth/corporate |
| real_name_masked | VARCHAR(100) | 脱敏姓名 |
| age | SMALLINT | 年龄 |
| occupation | VARCHAR(100) | 职业 |
| annual_income_min | NUMERIC(20,2) | 年收入下限 |
| annual_income_max | NUMERIC(20,2) | 年收入上限 |
| total_assets | NUMERIC(20,2) | 总资产 |
| liquid_assets | NUMERIC(20,2) | 流动资产 |
| investment_experience_years | NUMERIC(5,2) | 投资经验 |
| liquidity_need | VARCHAR(32) | low/medium/high |
| preferred_holding_period | VARCHAR(32) | short/medium/long |
| investment_preference_json | JSON/JSONB | 投资偏好 |
| data_source | VARCHAR(32) | mock/import/dialogue |
| profile_version | INTEGER | 画像版本 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

索引建议：

```text
unique(customer_no)
index(customer_type)
index(total_assets)
index(updated_at)
```

#### `fin_risk_assessment`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 评估记录主键 |
| customer_id | UUID | 客户 ID |
| assessment_type | VARCHAR(32) | questionnaire/ai/manual |
| risk_level | VARCHAR(32) | conservative/steady/balanced/aggressive |
| score | NUMERIC(8,2) | 风险评分 |
| answers_json | JSON/JSONB | 问卷答案 |
| evaluator_id | UUID | 评估人，可为空 |
| valid_from | TIMESTAMPTZ | 生效时间 |
| valid_until | TIMESTAMPTZ | 失效时间 |
| status | VARCHAR(20) | active/expired/revoked |
| created_at | TIMESTAMPTZ | 创建时间 |

索引建议：

```text
index(customer_id, status)
index(risk_level)
```

### 8.4 产品域

#### `fin_product`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 产品主键 |
| product_code | VARCHAR(64) | 产品代码 |
| product_name | VARCHAR(200) | 产品名称 |
| product_type | VARCHAR(64) | money/bond/mixed/stock/other |
| issuer_name | VARCHAR(200) | 发行机构 |
| risk_level | VARCHAR(32) | 产品风险等级 |
| min_purchase_amount | NUMERIC(20,2) | 最低申购金额 |
| max_purchase_amount | NUMERIC(20,2) | 最高申购金额 |
| expected_return_min | NUMERIC(8,4) | 预期收益下限，仅展示用 |
| expected_return_max | NUMERIC(8,4) | 预期收益上限，仅展示用 |
| holding_period_days | INTEGER | 建议/锁定期限 |
| liquidity_level | VARCHAR(32) | low/medium/high |
| suitable_customer_types | JSON/JSONB | 适合客户类型 |
| description | TEXT | 产品说明 |
| risk_disclosure | TEXT | 风险揭示 |
| status | VARCHAR(20) | draft/active/inactive |
| effective_from | TIMESTAMPTZ | 生效时间 |
| effective_until | TIMESTAMPTZ | 失效时间 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

索引建议：

```text
unique(product_code)
index(product_type, risk_level, status)
```

### 8.5 交易与持仓域

#### `fin_account`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 账户主键 |
| customer_id | UUID | 客户 ID |
| account_no_masked | VARCHAR(64) | 脱敏账户号 |
| currency | VARCHAR(16) | CNY |
| available_balance | NUMERIC(20,2) | 可用余额 |
| total_balance | NUMERIC(20,2) | 总余额 |
| status | VARCHAR(20) | active/frozen/closed |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

#### `fin_transaction`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 交易主键 |
| transaction_no | VARCHAR(64) | 交易流水号 |
| customer_id | UUID | 客户 ID |
| account_id | UUID | 账户 ID |
| product_id | UUID | 产品 ID，可为空 |
| transaction_type | VARCHAR(32) | purchase/redeem/transfer |
| amount | NUMERIC(20,2) | 交易金额 |
| units | NUMERIC(24,8) | 份额，可为空 |
| submitted_at | TIMESTAMPTZ | 提交时间 |
| confirmed_at | TIMESTAMPTZ | 确认时间 |
| status | VARCHAR(32) | draft/pending/success/failed/cancelled |
| idempotency_key | VARCHAR(128) | 幂等键 |
| source | VARCHAR(32) | user/agent/admin |
| request_snapshot_json | JSON/JSONB | 请求快照 |
| failure_reason | TEXT | 失败原因 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

约束建议：

```text
unique(transaction_no)
unique(idempotency_key)
index(customer_id, created_at)
index(status, created_at)
```

#### `fin_holdings`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 持仓主键 |
| customer_id | UUID | 客户 ID |
| account_id | UUID | 账户 ID |
| product_id | UUID | 产品 ID |
| units | NUMERIC(24,8) | 持有份额 |
| cost_amount | NUMERIC(20,2) | 成本金额 |
| current_value | NUMERIC(20,2) | 当前市值 |
| profit_loss | NUMERIC(20,2) | 浮动盈亏 |
| valuation_at | TIMESTAMPTZ | 估值时间 |
| status | VARCHAR(20) | active/closed |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

建议唯一约束：

```text
unique(customer_id, account_id, product_id)
```

### 8.6 风控域

#### `risk_rule`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 规则主键 |
| rule_code | VARCHAR(64) | 规则编码 |
| rule_name | VARCHAR(200) | 规则名称 |
| rule_type | VARCHAR(64) | transaction/profile/suitability/aml |
| risk_level | VARCHAR(32) | low/medium/high/critical |
| expression_json | JSON/JSONB | 可配置规则表达式 |
| description | TEXT | 规则描述 |
| status | VARCHAR(20) | active/inactive |
| version | INTEGER | 规则版本 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

#### `fin_risk_alert`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 预警主键 |
| alert_no | VARCHAR(64) | 预警编号 |
| customer_id | UUID | 客户 ID |
| transaction_id | UUID | 关联交易，可为空 |
| rule_id | UUID | 命中规则 |
| risk_level | VARCHAR(32) | 风险等级 |
| alert_type | VARCHAR(64) | aml/large_transaction/frequent/suitability |
| detail_json | JSON/JSONB | 命中详情 |
| status | VARCHAR(32) | open/processing/resolved/ignored |
| assigned_to | UUID | 处理人 |
| resolved_at | TIMESTAMPTZ | 解决时间 |
| resolution_note | TEXT | 处理说明 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

索引建议：

```text
unique(alert_no)
index(status, risk_level, created_at)
index(customer_id, created_at)
```

### 8.7 知识库域

#### `knowledge_document`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 文档主键 |
| document_key | VARCHAR(1000) | 文档逻辑键 |
| file_name | VARCHAR(255) | 文件名 |
| source_path | VARCHAR(1000) | 来源路径 |
| category | VARCHAR(64) | company/business/policy/risk_rule |
| content_hash | VARCHAR(64) | 文件哈希 |
| current_version | INTEGER | 当前版本 |
| status | VARCHAR(32) | processing/active/failed/deleted |
| permission_level | VARCHAR(32) | public/internal/risk_only |
| metadata_json | JSON/JSONB | 文档元数据 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

#### `knowledge_document_version`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 版本主键 |
| document_id | UUID | 文档 ID |
| version_no | INTEGER | 版本号 |
| content_hash | VARCHAR(64) | 版本哈希 |
| parser_type | VARCHAR(32) | markdown/txt/docx/mineru |
| status | VARCHAR(32) | processing/active/superseded/failed |
| chunk_count | INTEGER | Chunk 数量 |
| activated_at | TIMESTAMPTZ | 激活时间 |
| created_at | TIMESTAMPTZ | 创建时间 |

#### `knowledge_chunk`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | Chunk 主键 |
| document_id | UUID | 文档 ID |
| version_id | UUID | 版本 ID |
| chunk_index | INTEGER | Chunk 序号 |
| content_hash | VARCHAR(64) | 内容哈希 |
| content_preview | TEXT | 内容摘要或完整内容 |
| title_path | VARCHAR(1000) | 标题层级 |
| page_number | INTEGER | 页码 |
| milvus_collection | VARCHAR(128) | Milvus 集合 |
| milvus_pk | VARCHAR(128) | Milvus 主键 |
| status | VARCHAR(20) | active/deleted |
| created_at | TIMESTAMPTZ | 创建时间 |

Milvus 存完整 Chunk 内容和向量；主库保存版本、权限、状态和定位信息。

### 8.8 对话与记忆域

#### `conversation_session`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 会话主键 |
| user_id | UUID | 发起用户 |
| customer_id | UUID | 关联客户，可为空 |
| agent_mode | VARCHAR(64) | customer_service/advisor/risk/analyst/operation |
| title | VARCHAR(200) | 会话标题 |
| status | VARCHAR(20) | active/archived |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

#### `conversation_message`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 消息主键 |
| session_id | UUID | 会话 ID |
| role | VARCHAR(20) | user/assistant/tool/system |
| content | TEXT | 消息内容 |
| tool_name | VARCHAR(100) | 工具名称 |
| tool_call_id | VARCHAR(128) | 工具调用 ID |
| token_count | INTEGER | Token 数量 |
| metadata_json | JSON/JSONB | 消息元数据 |
| created_at | TIMESTAMPTZ | 创建时间 |

#### `memory_unit`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 记忆主键 |
| customer_id | UUID | 客户 ID |
| memory_type | VARCHAR(32) | fact/opinion/inference/event |
| subject | VARCHAR(200) | 记忆主题 |
| content | TEXT | 记忆内容 |
| normalized_value_json | JSON/JSONB | 结构化值 |
| source_type | VARCHAR(32) | questionnaire/dialogue/behavior/import |
| source_message_id | UUID | 来源消息 |
| confidence | NUMERIC(5,4) | 置信度 |
| evidence_count | INTEGER | 证据数量 |
| conflict_count | INTEGER | 冲突数量 |
| last_confirmed_at | TIMESTAMPTZ | 最近确认时间 |
| last_recalled_at | TIMESTAMPTZ | 最近召回时间 |
| recall_count | INTEGER | 召回次数 |
| status | VARCHAR(20) | active/demoted/forgotten/deleted |
| milvus_collection | VARCHAR(128) | 向量集合 |
| milvus_pk | VARCHAR(128) | 向量主键 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

### 8.9 任务、审计与事件域

#### `async_task`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 任务主键 |
| task_type | VARCHAR(100) | 任务类型 |
| aggregate_type | VARCHAR(64) | 业务聚合类型 |
| aggregate_id | UUID | 业务对象 ID |
| idempotency_key | VARCHAR(128) | 幂等键 |
| status | VARCHAR(32) | queued/running/success/failed/dead |
| attempt | INTEGER | 当前重试次数 |
| max_attempts | INTEGER | 最大重试次数 |
| payload_json | JSON/JSONB | 任务参数 |
| result_json | JSON/JSONB | 执行结果 |
| error_message | TEXT | 错误信息 |
| available_at | TIMESTAMPTZ | 可执行时间 |
| started_at | TIMESTAMPTZ | 开始时间 |
| finished_at | TIMESTAMPTZ | 完成时间 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

#### `audit_log`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 日志主键 |
| trace_id | VARCHAR(128) | 请求链路 ID |
| actor_user_id | UUID | 操作人 |
| customer_id | UUID | 关联客户 |
| action | VARCHAR(100) | 操作名称 |
| resource_type | VARCHAR(64) | 资源类型 |
| resource_id | UUID | 资源 ID |
| request_json | JSON/JSONB | 请求摘要 |
| response_json | JSON/JSONB | 响应摘要 |
| success | BOOLEAN | 是否成功 |
| error_message | TEXT | 错误信息 |
| created_at | TIMESTAMPTZ | 创建时间 |

#### `outbox_event`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 事件主键 |
| event_type | VARCHAR(100) | 事件类型 |
| aggregate_type | VARCHAR(64) | 聚合类型 |
| aggregate_id | UUID | 聚合 ID |
| payload_json | JSON/JSONB | 事件数据 |
| status | VARCHAR(20) | pending/published/failed |
| attempt | INTEGER | 发布次数 |
| published_at | TIMESTAMPTZ | 发布时间 |
| created_at | TIMESTAMPTZ | 创建时间 |

## 9. 业务主库选择的落地策略

### 方案 A：PostgreSQL 主库（推荐）

```text
PostgreSQL
├── 业务表
├── 任务和审计表
├── 知识库元数据
├── JSONB 字段
└── 可选 pgvector 兜底

Milvus
└── 向量和混合检索
```

适合负责人希望长期演进、异步 ORM 和数据约束较强的方案。

### 方案 B：MySQL 主库

```text
MySQL
├── 业务表
├── 任务和审计表
└── 知识库元数据

Milvus
└── 向量和混合检索
```

适合团队熟悉度优先、快速交付和后续维护人员主要使用 MySQL 的方案。

### 不建议方案：PostgreSQL + MySQL 双主

除非明确设计数据同步、冲突处理和故障切换，否则不建议双写。双写会把项目从业务系统变成数据同步项目。

## 10. 迁移现有 RAG 的建议目录

目标项目中建议抽取成独立模块：

```text
app/
├── knowledge/
│   ├── parser.py                 # simple_rag / rag_core/parser.py
│   ├── chunker.py                # simple_rag / text_splitter.py
│   ├── embedding.py              # simple_rag / qwen_client.py
│   ├── milvus_store.py           # simple_rag / retrieval.py + vdb_init_milvus.py
│   ├── ingestion_service.py      # pg_prefect / ingestion_service.py
│   ├── document_repository.py    # pg_prefect / document/version/chunk 模型
│   └── retrieval_service.py      # 混合检索、RRF、去重、过滤
├── queue/
│   ├── streams.py                # Redis Streams 封装
│   ├── workers.py                # simple_rag worker 演进
│   └── task_registry.py
└── llm/
    ├── base.py
    ├── deepseek.py
    └── dashscope.py
```

迁移原则：

1. 先写目标项目自己的接口和测试。
2. 再把现有实现适配到接口后迁入。
3. 不直接依赖 `C:/simple_rag` 或 `C:/pg_prefect` 的路径。
4. 保留来源和迁移记录，方便后续维护。
5. RAG 的检索质量实验和回归测试一并迁移，不只迁代码。

## 11. 第一版建议的核心表范围

不要一开始实现所有表。MVP 先落地：

```text
sys_user
fin_customer_profile
fin_risk_assessment
fin_product
fin_account
fin_transaction
fin_holdings
risk_rule
fin_risk_alert
knowledge_document
knowledge_document_version
knowledge_chunk
conversation_session
conversation_message
async_task
audit_log
outbox_event
```

记忆表 `memory_unit` 可以在客服对话稳定后加入，但表结构现在可以先纳入设计。

## 12. 本轮待负责人确认的问题

1. PostgreSQL 是否作为第一版主库？如果团队最终选 MySQL，是否接受 ORM 层保持数据库无关？
2. `text-embedding-v4` 的向量维度最终以当前项目配置的 1024 为准，还是以实际 API 返回维度为准并在启动时校验？
3. Redis 队列是否统一采用 Redis Streams + Consumer Group，而不是无 ACK 的 `BRPOP`？
4. 是否接受“Supervisor + Tool Calling”为第一版 Agent 架构，后续按领域拆出 Agent？
5. `C:/pg_prefect` 中 Prefect 是否仅用于批处理调度，不进入实时请求链路？
6. MySQL 使用者是否需要参与 ORM 层和数据库迁移设计，以降低后续切换成本？


## 12.1 负责人最新决策修订

### Prefect

Prefect 从当前主架构中移除。任务调度和状态管理由以下组件承担：

```text
Redis 队列：任务投递和消费
PostgreSQL：任务状态、重试次数、审计记录
Worker：任务处理
```

Prefect 不再作为第一版依赖，仅在未来出现复杂 DAG、批处理编排或任务可观测性需求时重新评估。

### 主库

PostgreSQL 确定为第一版主业务数据库。MySQL 只作为团队熟悉度较高时的迁移备选，不参与默认双写。

### Embedding

固定使用：

```text
Provider: DashScope
Model: text-embedding-v4
Dimension: 1024
```

启动、写入和检索三个环节都必须校验 1024 维。

### Redis 队列

第一方案为 Redis Streams + Consumer Group，提供：

- ACK
- Pending 检查
- 重试
- Dead Letter Stream
- 幂等键
- 任务状态落 PostgreSQL

如果团队实现难度较高，降级为 Redis List + processing list + ACK/重试，但业务层统一使用 `Queue` 抽象，不绑定底层实现。

### Agent

第一版推荐 Supervisor + Tool Calling。

多 Agent 并行保留为可选方案，用于满足项目对多 Agent 协作的展示或评审要求。可选实现采用 Router + Fan-out/Fan-in + Aggregator，但不允许并行 Agent 直接执行资金操作。


## 13. 最终推荐：Supervisor + 受控多 Agent

确认采用以下模式：

```text
Supervisor
    ├── CustomerServiceAgent
    ├── AdvisorAgent
    ├── RiskAgent
    ├── AnalystAgent
    └── OperationAgent
```

这些 Agent 默认只负责读取数据、计算分析、检索知识、生成建议和创建草稿。它们返回统一的 `AgentResult`，由 Supervisor 汇总、校验和决定下一步。

只有确定性业务 Service 可以修改核心业务状态。包括交易、账户、持仓、风控状态和权限等修改操作，都必须经过后端 Service、数据库事务、幂等校验和确认机制。

### Agent 权限分层

```text
L0：只读数据和分析
L1：建议和操作草稿
L2：确定性业务 Service
```

L2 不属于自由 Agent，不能由模型绕过调用。

### 受控多 Agent 并行

Supervisor 按场景选择 Agent 并行执行，不能默认调用所有 Agent：

- 产品推荐：Advisor + Risk + CustomerService
- 产品政策咨询：CustomerService
- 交易前检查：Operation + Risk + Advisor
- 资产分析：Advisor + Analyst

Agent 输出必须是结构化 Schema，包含：

```text
agent_name
status
summary
data
evidence
confidence
requires_human_confirmation
```

这一模式既满足多 Agent 协作要求，又避免 Agent 直接修改账务、持仓和交易数据。


## 14. ReAct Agent、统一 Qwen 与记忆模块建议

### 14.1 模型统一

第一版统一使用 Qwen / DashScope：

```text
Chat Model：Qwen 系列
Embedding：text-embedding-v4
Dimension：1024
```

仍然保留 Provider 抽象，但第一版只实现一个 DashScope Provider，避免同时维护两家模型供应商。

### 14.2 ReAct Agent

每个领域 Agent 使用受控 ReAct 循环：

```text
规划 → 调用 Tool → 获取 Observation → 检查 → 反思 → 结构化返回
```

不保存或展示完整隐藏思维链，只记录计划摘要、Tool 调用摘要、检查结果、反思结论摘要和最终结果。

### 14.3 API 与 Tool 的关系

推荐：

```text
Agent Tool → Application Service → Repository / External Adapter
```

API 接口和 Agent Tool 不直接等同。Agent 只能调用白名单 Tool；Tool 负责参数、权限、审计、脱敏、限流、幂等和 Schema 校验。

Tool 分为：

```text
Read Tool
Analysis Tool
Draft Tool
Execute Tool
```

普通 ReAct Agent 只开放前三类，Execute Tool 由确定性业务 Service 控制。

### 14.4 记忆模块定位

当前《记忆架构设计.html》的方向是正确的，但落地时应增加候选记忆和验证状态机：

```text
candidate → pending_review → active
                      ↘ rejected
active → superseded / conflicted / demoted / forgotten / deleted
```

记忆分为：

- 系统事实
- 用户明确陈述
- 模型推断

交易、余额、持仓、正式风评等事实必须以业务表为准，不由记忆模块覆盖。

### 14.5 记忆第一阶段

优先实现：

1. Redis 短期会话记忆
2. 用户明确偏好提取
3. 事实/观点分类
4. 记忆候选验证
5. 基础置信度
6. 结构化和向量混合召回
7. 冲突检测
8. 用户删除记忆

复杂遗忘曲线、跨客户记忆、元记忆和全历史实时重算暂缓。

### 14.6 记忆任务异步化

对话主链路优先返回，记忆提取通过 Redis Streams 异步处理：

```text
conversation.completed
    ↓
memory.extract
    ↓
Memory Worker
    ↓
PostgreSQL
    ↓
memory.embed
    ↓
Milvus
```

用户明确要求记住、修改关键画像或确认关键风险信息时，可以走同步确认流程。


## 15. 向量数据库调整：PostgreSQL + pgvector 优先

### 15.1 最终推荐

第一版采用：

```text
PostgreSQL + pgvector
```

Milvus 不再是 P0-P4 的必选组件，调整为 P5 可选扩展。

### 15.2 数据分工

```text
PostgreSQL
├── 业务事实
├── 文档元数据和版本
├── 文档 Chunk
├── 全文检索字段
├── 记忆结构化数据
└── 1024 维向量

Redis
├── 短期会话
├── 缓存
└── Streams 任务队列

Neo4j（可选）
└── GraphRAG 关系数据
```

### 15.3 设计变更

`knowledge_chunk` 增加：

```text
content
search_vector
embedding vector(1024)
```

`memory_unit` 增加：

```text
content
search_vector
embedding vector(1024)
```

不再要求第一版保存 `milvus_collection` 和 `milvus_pk`。如未来接入 Milvus，再增加外部向量索引映射字段或独立映射表。

### 15.4 兼容性

Repository 层定义统一接口：

```python
class VectorSearchRepository(Protocol):
    async def search_documents(...): ...
    async def search_memories(...): ...
```

第一版实现：

```text
PgVectorSearchRepository
```

未来可增加：

```text
MilvusSearchRepository
```

业务层不感知具体向量数据库。


## 16. 风险预案与执行细节

### ReAct 循环预算

`AgentContext` 必须包含迭代、Tool 调用、Token 和耗时预算。默认单 Agent：

```text
max_iterations=4
max_tool_calls=8
```

达到任一预算时安全停止，不把完整隐藏思维链返回前端或写入普通日志。

### 记忆同步确认通道

普通对话的记忆提取异步执行；用户明确要求记住、修改关键画像或当前请求立即依赖新信息时，同步完成候选提取、校验、PostgreSQL 提交和 Redis 热缓存更新。

画像读取优先级：

```text
当前请求已确认信息 > Redis 热缓存 > PostgreSQL 结构化数据 > 低置信度向量记忆
```

### GraphRAG 最小演示

Neo4j 仍是可选组件，但可在 P3/P4 后实现最小“两步走”：PostgreSQL + pgvector 检索文本，Neo4j 补充关系，交给 Qwen 解释。Neo4j 不可用时自动退化为普通 RAG。

### Embedding Smoke Check

FastAPI 启动阶段默认调用一次 `text-embedding-v4("hello world")` 并校验 1024 维；失败时服务标记为 `not_ready`，不开放完整业务路由。

### 层间职责

```text
Repository：SQLAlchemy 查询和持久化
Service：业务规则、事务、权限、幂等和状态变更
Tool：Schema、权限、参数转换、Service 调用和结果格式化
```

Agent 不得直接访问 Repository，Tool 不得直接拼接 SQL、修改数据库或操作 Redis 队列。

### 演示数据重置

M1 完成后立即增加 `scripts/reset_demo_data.py`，清理交易、持仓、预警、会话和记忆，保留知识库和系统账号，并重新导入客户 A、客户 B、产品和风评。


## 17. 记忆与异步任务分类清单

| 分类 | 任务类型 | 示例 | 处理方式 | 原因 |
|---|---|---|---|---|
| 用户明确偏好 | `memory.confirmed_preference` | “我喜欢稳健型”“记住我偏好债券” | 同步 | 影响本轮后续回答 |
| 用户明确事实 | `memory.confirmed_fact` | “我今年35岁”“我是公务员” | 同步 | 影响画像匹配 |
| 用户明确修改 | `memory.preference_update` | “我不喜欢股票了” | 同步 | 需要立刻生效 |
| 历史行为推断 | `memory.infer_from_history` | 从对话历史提取“可能偏好短期” | 异步 | 不影响当前回答 |
| 交易行为分析 | `risk.analyze_behavior` | 用户最近频繁交易并生成风控标记 | 异步 | 不需要阻塞当前请求 |
| 对话摘要 | `conversation.summarize` | 总结本轮对话要点 | 异步 | 供未来使用 |
| 情绪分析 | `conversation.analyze_sentiment` | 用户当前情绪焦虑 | 异步 | 非关键决策 |
| 全量画像重算 | `profile.recalculate` | 每周/每天画像校准 | 异步 | 批处理任务 |

原则：

```text
明确表达并影响当前回答 → 同步
历史推断、分析、摘要、批量校准 → 异步
```


## 18. 同步记忆超时与降级

同步记忆不允许无限等待 Qwen。默认配置：

```text
MEMORY_SYNC_TIMEOUT_MS=2500
```

超时后：

1. 保留用户原始表达；
2. 将 `memory.extract` 任务投递到 `stream:wealth:memory`；
3. 当前请求使用临时的 `confirmed_memory_candidates`；
4. 提示用户“已收到您的偏好，我正在后台处理”；
5. 后台成功后更新 PostgreSQL 和 Redis 热缓存。

未完成持久化的候选必须标记：

```text
source=current_request
persisted=false
```

并且使用幂等键，避免同步超时重试和异步降级重复创建记忆任务。

