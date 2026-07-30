# P0-P5 最新实施方案（v2）

> 文档状态：当前有效方案，待负责人最终确认后执行  
> 版本：v2.0  
> 更新日期：2026-07-24  
> 旧版本：本文件历史内容以及此前 PLAN.md 中的早期 P0-P5 描述均视为弃用，仅用于追溯，不作为实施依据。

## 1. 当前已冻结的技术决策

| 领域 | 当前方案 | 明确不采用/暂缓 |
|---|---|---|
| 前端 | Streamlit | 暂不使用 Vue/Gradio 作为主前端 |
| 后端 | FastAPI | - |
| 主数据库 | PostgreSQL | 不做 PostgreSQL + MySQL 双主双写 |
| ORM | SQLAlchemy 2.x Async ORM + asyncpg | 不使用同步 ORM 作为业务主路径 |
| 向量检索 | PostgreSQL + pgvector | Milvus 延后到 P5，不是第一版依赖 |
| 全文检索 | PostgreSQL FTS，必要时 pg_trgm | 不先引入额外搜索服务 |
| 缓存/队列 | Redis + Redis Streams + Consumer Group | 不使用 Prefect；不以无 ACK 的 BRPOP 作为第一方案 |
| 模型 | Qwen / DashScope 统一 | 暂不同时维护 DeepSeek Provider |
| Embedding | Qwen `text-embedding-v4` | 固定 1024 维并校验 |
| Agent | Supervisor + 受控多 Agent；每个领域 Agent 使用受控 ReAct | 不做自由自治 Agent 网络 |
| 业务写入 | 确定性 Application Service | Agent 不直接改核心业务数据 |
| 图谱 | Neo4j P5 可选 | 不阻塞 P0-P4 |
| 调度 | Redis Worker + PostgreSQL `async_task` | Prefect 移除 |

## 2. 分级定义

| 阶段 | 缓急 | 目标 |
|---|---|---|
| P0 | 阻塞级，必须先做 | 工程基线、依赖健康、安全和任务基础设施 |
| P1 | 核心级，MVP 必须做 | PostgreSQL RAG + 客服 ReAct Agent |
| P2 | 重要级，第一轮业务闭环 | 客户画像、产品匹配、持仓和只读分析 |
| P3 | 业务级，完成受控操作 | Supervisor、多 Agent、Mock 交易、确认机制 |
| P4 | 高难度核心，项目重点 | 风控预警、记忆模块、异步一致性和评测 |
| P5 | 增强级，可选 | Neo4j GraphRAG、Milvus 对比、高级 NL2SQL、性能和答辩 |

依赖关系：

```text
P0 → P1 → P2 → P3 → P4 → P5
```

P5 的任何能力都不能反向阻塞 P0-P4。

## 3. 全局实现约束

### 3.1 异步边界

- FastAPI 路由、Service、Repository、LLM 调用、Redis、Worker 使用 `async/await`。
- SQLAlchemy 使用 Async ORM。
- Streamlit 只作为 HTTP/SSE 客户端，不承担后端业务编排。
- 同步 SDK 或阻塞文件解析必须通过 `asyncio.to_thread` 隔离。

### 3.2 分层约束

```text
API
  ↓
Supervisor / Agent
  ↓
Tool
  ↓
Application Service
  ↓
Repository
  ↓
PostgreSQL / Redis / 外部适配器
```

- Repository：只负责数据访问。
- Service：负责业务规则、事务、权限、幂等、状态变更。
- Tool：负责 Schema、权限、参数转换、Service 调用、结果格式化。
- Agent：不能直接访问 Repository、拼接 SQL 或操作 Redis 队列。

### 3.3 Agent 安全约束

每个 Agent 使用受控 ReAct：

```text
Plan → Tool Call → Observation → Check → Reflect → AgentResult
```

默认预算：

```text
max_iterations = 4
max_tool_calls = 8
MEMORY_SYNC_TIMEOUT_MS = 2500
```

还必须有：单 Agent 超时、全局超时、Token 预算、重复调用检测、Tool 白名单。

不保存或展示完整隐藏思维链，只记录摘要和审计信息。

### 3.4 数据事实优先级

```text
业务事实表 > 当前请求已确认信息 > Redis 热缓存 > 有效结构化记忆 > 低置信度推断记忆
```

交易、余额、持仓、正式风评和风险等级不能由模型推断覆盖。

## 4. P0：工程基线、依赖健康与安全边界

### 缓急

**P0 / 阻塞级 / 必须最先完成**

### 必做内容

#### 4.1 工程入口

- FastAPI 最小入口和 `/api/v1/health`
- Streamlit 最小首页和后端连接
- 配置加载、`.env.example`、日志和 Trace ID
- 统一响应格式和异常处理
- API 版本、时区、UUID、金额精度规范

#### 4.2 PostgreSQL

- 安装/启用 `vector` 扩展
- SQLAlchemy 2.x Async ORM
- `asyncpg` 连接池
- Alembic 异步迁移
- 数据库健康检查
- 业务数据库最小迁移

#### 4.3 Redis Streams

- `redis.asyncio`
- Stream 和 Consumer Group 创建
- Producer
- Consumer
- ACK
- Pending 消息检查
- 最大重试次数
- Dead Letter Stream
- `task_id`、`idempotency_key`

#### 4.4 Qwen

- DashScope AsyncOpenAI Provider
- Chat 调用
- Embedding 调用
- 超时、重试和错误分类
- 启动 Smoke Check：调用 `text-embedding-v4("hello world")`
- 校验向量长度为 1024

#### 4.5 基础安全

- Mock JWT
- customer/advisor/risk/analyst/admin 角色
- Tool 权限白名单
- 脱敏工具
- 审计日志基础表
- 禁止 API Key 进入代码和日志

### P0 交付物

```text
FastAPI /health
Streamlit 首页
PostgreSQL Async ORM 基线
Redis Streams 基线
Qwen Provider
Embedding 1024 校验
基础迁移
配置模板
```

### P0 验收

- API、前端可启动。
- PostgreSQL、Redis 可连接。
- pgvector 扩展可用。
- Qwen Chat 和 Embedding 可配置调用。
- Embedding 不是 1024 维时服务进入 `not_ready`。
- 测试任务可入 Stream、消费、ACK、落 `async_task`。
- 请求有 Trace ID，敏感数据不出现在普通日志。

### P0 明确不做

```text
Milvus
Neo4j
Prefect
五类完整 Agent
真实交易
复杂记忆
高级 NL2SQL
```

## 5. P1：PostgreSQL RAG 与客服 ReAct Agent

### 缓急

**P1 / 核心级 / MVP 必须完成**

### 必做内容

#### 5.1 知识库导入

复用：

- `C:/pg_prefect` 的文档 Hash、版本、状态、Chunk 和 Async ORM 设计。
- `C:/simple_rag` 的解析、切分、Qwen Embedding、缓存和评测思路。

导入范围：

```text
公司信息/
公司业务/
金融政策/
```

#### 5.2 PostgreSQL + pgvector 检索

`knowledge_chunk` 保存：

```text
content
search_vector
title_path
embedding vector(1024)
```

检索：

```text
Qwen Embedding
  + pgvector cosine search
  + PostgreSQL FTS
  + 应用层加权/RRF
  + 去重和权限过滤
```

#### 5.3 客服 Agent

使用受控 ReAct，仅开放：

```text
search_knowledge
search_qa_pairs
get_conversation_context
```

返回：

```text
answer
sources
confidence
limitations
```

#### 5.4 前端

- 对话窗口
- 角色/客户选择
- 来源展示
- 会话清空
- 错误和降级提示

### P1 验收

- 资料可以导入 PostgreSQL。
- 重复导入具有幂等性和版本控制。
- 检索返回来源和标题。
- 无相关资料时不编造答案。
- 客服 Agent 无法调用写库和交易 Tool。
- 公司、产品、政策三类问题可演示。

### P1 立即追加

完成 P1 后立刻创建：

```text
scripts/reset_demo_data.py
```

支持：

```text
--dry-run
--confirm
```

保留知识库和系统账号，重置演示业务数据。

## 6. P2：客户画像、产品、持仓和只读分析

### 缓急

**P2 / 重要级 / 第一轮业务闭环**

### 必做内容

核心数据：

```text
sys_user
fin_customer_profile
fin_risk_assessment
fin_product
fin_account
fin_holdings
fin_transaction
conversation_session
conversation_message
```

功能：

- 导入客户 A/B 和产品 Mock 数据。
- 查询客户画像、资产、风险等级和持仓。
- 根据客户风险等级和产品等级做适当性筛选。
- AdvisorAgent 生成带依据的产品匹配解释。
- 初版 AnalystAgent 只调用固定白名单统计 Tool。

只读统计先支持：

```text
客户数量
客户资产统计
产品列表
客户持仓
风险等级分布
预警数量
```

暂不开放任意 NL2SQL。高级 NL2SQL 放 P5。

### P2 Agent Tool

```text
get_customer_profile
get_customer_risk
get_holdings
list_products
match_suitable_products
search_knowledge
```

### P2 验收

- 客户 A/B 上下文隔离。
- 产品推荐有适当性过滤。
- 低置信度和资料不足有提示。
- 只读 Tool 不能修改数据库。
- 不同角色的数据访问范围正确。

## 7. P3：Supervisor、受控多 Agent 与 Mock 业务操作

### 缓急

**P3 / 业务级 / 复杂闭环**

### 必做内容

#### 7.1 Supervisor

负责：

- 意图识别
- Agent 选择
- 上下文拼装
- Tool 白名单分配
- 并行预算和超时
- AgentResult 校验
- 结果聚合和冲突处理
- 最终回答

#### 7.2 五类领域 Agent

```text
CustomerServiceAgent
AdvisorAgent
RiskAgent
AnalystAgent
OperationAgent
```

每个 Agent 都是受控 ReAct，不是自由自治体。

#### 7.3 多 Agent 使用范围

仅在需要时并行：

```text
产品推荐：Advisor + Risk + CustomerService
交易前检查：Operation + Risk + Advisor
资产分析：Advisor + Analyst
```

#### 7.4 Mock 操作

先做：

```text
create_purchase_draft
confirm_purchase
create_redemption_draft
confirm_redemption
create_work_order
```

流程：

```text
用户请求
  ↓
OperationAgent 解析
  ↓
草稿
  ↓
Advisor/Risk 检查
  ↓
用户确认
  ↓
PurchaseService / RedemptionService
  ↓
PostgreSQL 事务
  ↓
Outbox Event
  ↓
Redis Stream
```

只有确定性 Service 可以修改交易、账户、持仓和余额。

### P3 验收

- 每个 Agent 只能使用自己的 Tool 白名单。
- 多 Agent 返回结构化 `AgentResult`。
- Agent 超时有降级。
- 交易必须二次确认。
- 幂等键阻止重复交易。
- 全链路有审计。

## 8. P4：风控与记忆核心

### 缓急

**P4 / 高难度核心 / 项目重点**

### 8.1 风控必做

规则：

```text
LargeTransactionRule
FrequentTransactionRule
SuitabilityRule
BalanceRule
TradingTimeRule
AMLRule
```

流程：

```text
transaction.created
  ↓
risk.evaluate_transaction
  ↓
Risk Worker
  ↓
fin_risk_alert
```

### 8.2 记忆必做

记忆类型：

```text
system_fact
user_statement
user_preference
model_inference
event_memory
```

状态：

```text
candidate
pending_review
active
superseded
conflicted
demoted
forgotten
deleted
rejected
```

普通对话：

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
PostgreSQL pgvector
```

用户明确偏好、明确事实、明确修改：

```text
同步提取和保存
MEMORY_SYNC_TIMEOUT_MS=2500
```

同步超时：

```text
保留原始表达
  ↓
创建 memory.extract 任务
  ↓
stream:wealth:memory
  ↓
当前请求使用 confirmed_memory_candidates
  ↓
提示用户后台处理
```

当前请求临时记忆必须标记：

```text
source=current_request
persisted=false
```

记忆读取优先级：

```text
当前请求已确认信息
  > Redis 热缓存
  > PostgreSQL 有效结构化记忆
  > pgvector 低置信度推断记忆
```

置信度由规则计算，不完全相信 LLM：

```text
source_weight
× recency_factor
× evidence_factor
× consistency_factor
× confirmation_factor
```

业务事实优先于记忆推断。

### P4 记忆验收

- 普通记忆异步，不阻塞对话。
- 明确偏好同步，超时不丢输入。
- 用户明确否认后旧记忆进入冲突/替代。
- 低置信度记忆不能决定适当性。
- 支持记忆删除和审计。
- 风控 Worker 能生成预警。
- 有事实、观点、推断、冲突和删除测试集。

## 9. P5：增强能力和答辩优化

### 缓急

**P5 / 增强级 / 可选，不阻塞 P0-P4**

### 可选内容

#### 9.1 Neo4j GraphRAG

最小演示：

```text
PostgreSQL + pgvector 检索产品文本
  ↓
Neo4j 查询产品关联行业/风险等级/同类产品
  ↓
合并后交给 Qwen
```

Neo4j 不可用则退化为普通 RAG。

#### 9.2 Milvus 对比扩展

只有在数据规模或并发检索证明 PostgreSQL 成为瓶颈时才引入 Milvus。业务层通过 `VectorSearchRepository` 抽象，不改变上层 API。

#### 9.3 高级 NL2SQL

- 动态 Schema 裁剪
- 只读数据库账号
- 表/字段白名单
- SQL AST 检查
- 强制 LIMIT
- 查询审计

#### 9.4 高级多 Agent

- Router + Fan-out/Fan-in
- Aggregator
- 结果冲突处理
- 独立 Agent 评测
- 全局超时和缓存

#### 9.5 SSE、质量和性能

- SSE 流式输出
- RAG 评测
- Agent Tool 成功率
- 记忆召回准确率
- 风控规则覆盖率
- API P95
- Qwen 失败降级

### P5 验收

- 每项增强能力可以关闭。
- Neo4j/Milvus 不可用不影响 P0-P4。
- 多 Agent 部分失败有降级。
- 演示数据可以一键重置。
- GraphRAG 和普通 RAG 有对比场景。

## 10. 异步任务位置和任务清单

### 10.1 目录

```text
app/tasks/
  schemas.py
  types.py
  publisher.py
  handlers/

workers/
  runner.py
  registry.py
  outbox_worker.py
  ingestion_worker.py
  memory_worker.py
  risk_worker.py
  analytics_worker.py
```

### 10.2 任务分类

| 分类 | 任务类型 | 处理方式 |
|---|---|---|
| 明确偏好/事实/修改 | `memory.confirmed_preference` / `memory.confirmed_fact` / `memory.preference_update` | 同步；Qwen 2.5 秒超时后降级异步 |
| 历史行为推断 | `memory.infer_from_history` | 异步 |
| 交易行为分析 | `risk.analyze_behavior` | 异步 |
| 对话摘要 | `conversation.summarize` | 异步 |
| 情绪分析 | `conversation.analyze_sentiment` | 异步 |
| 全量画像重算 | `profile.recalculate` | 异步 |
| 文档导入 | `knowledge.ingest` | 异步 |
| 记忆向量化 | `memory.embed` | 异步 |
| 交易风控 | `risk.evaluate_transaction` | 异步；核心交易写入仍同步完成 |

### 10.3 Worker 原则

- 任务生产：API/Service 写 `async_task` 和 `outbox_event`。
- Outbox Worker 投递 Redis Stream。
- 业务 Worker 消费、处理、ACK。
- 成功/失败状态写 PostgreSQL。
- 超过重试次数进入 Dead Letter Stream。
- 任务必须幂等。

## 11. 总验收路线

```text
M0：P0 完成，基础可用
M1：P1 完成，知识问答可用，并创建 reset_demo_data.py
M2：P2 完成，客户业务查询可用
M3：P3 完成，受控 Mock 操作可用
M4：P4 完成，风控和记忆可用
M5：P5 完成，增强和答辩优化可用
```

## 12. 评审后执行顺序

1. 冻结本文档。
2. 根据本文档创建 `docs/API.md`、`docs/DATABASE.md`、`docs/ARCHITECTURE.md`。
3. 创建 P0 代码骨架和最小迁移。
4. 先跑通 PostgreSQL、Redis Streams、Qwen 和 pgvector。
5. 再进入 P1，不提前开发 Neo4j、Milvus 或复杂多 Agent。


## LangChain / LangGraph 更新

当前 Agent 方案加入 LangChain 和 LangGraph：

```text
LangChain：模型、Prompt、Tool、ReAct、Retriever
LangGraph：Supervisor、多 Agent、状态图、Check、Reflect、人工确认和循环预算
```

P0 只完成依赖和状态 Schema 基线；完整客服 Agent 从 P1 开始，Supervisor 和多 Agent 编排从 P3 开始。


## LangChain / LangGraph 版本要求修订

当前有效版本要求：

```text
LangChain V1.0 版本线
LangGraph V1.0 版本线
```

`requirements.txt` 已从旧的 0.3/0.2 下限改为：

```text
langchain>=1.0,<2
langchain-core>=1.0,<2
langchain-community>=1.0,<2
langgraph>=1.0,<2
langchain-openai>=1.0,<2
```

P0 必须增加版本导入检查，后续代码只按 V1.0 API 编写。

