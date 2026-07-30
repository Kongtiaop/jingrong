# 文档版本与当前有效方案说明

> 更新日期：2026-07-24

## 当前有效文档

以下文档中的最新章节和 `docs/P0-P5_IMPLEMENTATION_PLAN.md v2.0` 为当前实施依据：

```text
docs/P0-P5_IMPLEMENTATION_PLAN.md
docs/ARCHITECTURE_DATABASE_DRAFT.md
docs/PLAN.md
```

## 已弃用的旧方案

此前文档中出现过以下旧方案，均不再作为当前实施依据：

- PostgreSQL 与 MySQL 并行作为主业务数据库
- Milvus 作为 P0-P4 必选向量数据库
- Prefect 作为任务编排和监控组件
- DeepSeek 与 Qwen 双模型供应商并行维护
- 无 ACK 的 Redis `BRPOP` 作为第一队列方案
- 没有明确 ReAct 预算的 Agent 循环
- 同步记忆无限等待 Qwen
- 未明确区分 Agent、Tool、Service、Repository 的职责

旧方案保留在历史追加章节中，仅用于追溯，不得直接复制实施。

## 当前有效技术基线

```text
Streamlit
FastAPI
PostgreSQL + pgvector
SQLAlchemy Async ORM + asyncpg
Redis Streams + Consumer Group
Qwen / DashScope
text-embedding-v4 / 1024 维
Supervisor + 受控多 Agent
受控 ReAct
确定性 Service 修改业务状态
Neo4j P5 可选
Milvus P5 可选
Prefect 移除
```

## 当前 P0-P5 入口

```text
P0：工程基线、依赖健康、安全边界
P1：PostgreSQL RAG 与客服 ReAct Agent
P2：客户画像、产品、持仓和只读分析
P3：Supervisor、受控多 Agent、Mock 业务操作
P4：风控、记忆、异步一致性和评测
P5：GraphRAG、Milvus 对比、高级 NL2SQL、性能和答辩
```

## 文档维护规则

以后如果技术方案发生变化：

1. 先更新 `docs/P0-P5_IMPLEMENTATION_PLAN.md`。
2. 在文档顶部更新版本和日期。
3. 在本文件的“已弃用的旧方案”中记录被替代的内容。
4. 再同步修改架构、数据库和 API 文档。
5. 不在旧章节中直接覆盖历史决策，避免无法追溯。

## LangChain / LangGraph 架构决策

### 结论

本项目正式加入并使用：

```text
LangChain：模型、Prompt、Tool、ReAct Agent 抽象
LangGraph：Supervisor、多 Agent 编排、状态图、检查点和循环控制
```

这是当前有效方案，不再把 Agent 执行循环作为完全手写框架。

### 使用边界

#### LangChain 负责

- Qwen Chat Model 适配
- PromptTemplate / ChatPromptTemplate
- `@tool` 和结构化 Tool Schema
- Tool Calling
- ReAct Agent 基础执行器
- 消息和模型结果标准化
- Retriever 接口
- PostgreSQL/pgvector Retriever 适配

#### LangGraph 负责

- Supervisor 状态图
- 领域 Agent 节点
- Agent 之间的受控转发
- Tool 节点
- Check 节点
- Reflect 节点
- 人工确认节点
- 循环次数和超时状态
- Fan-out/Fan-in 多 Agent 并行
- 状态持久化接口预留

### 推荐图结构

```text
START
  ↓
Supervisor
  ├── CustomerServiceAgent
  ├── AdvisorAgent
  ├── RiskAgent
  ├── AnalystAgent
  └── OperationAgent
          ↓
      CheckResult
          ↓
       Reflect
       ├── 继续调用 Tool
       ├── 转发其他 Agent
       ├── 请求人工确认
       └── Final
```

### LangGraph 状态约束

统一定义 `AgentState`：

```text
request_id
trace_id
user_id
customer_id
role
user_message
messages
selected_agents
agent_results
tool_calls
confirmed_memory_candidates
pending_confirmation
iteration_count
tool_call_count
token_budget
error
final_response
```

### ReAct 预算控制

LangGraph 节点必须在每次循环前检查：

```text
max_iterations = 4
max_tool_calls = 8
max_wall_time_ms
max_tokens
```

达到预算后进入 `budget_exceeded` 节点，不允许继续循环。

LangGraph 的 `recursion_limit` 只作为框架级保险，业务层仍必须自行统计 `iteration_count` 和 `tool_call_count`。

### Tool 分层

使用 LangChain Tool，但不允许 Tool 绕过业务层：

```text
LangChain Tool
    ↓
Application Service
    ↓
Repository
```

Tool 分类：

```text
Read Tool
Analysis Tool
Draft Tool
Execute Tool
```

普通 ReAct Agent 只挂载 Read / Analysis / Draft Tool。Execute Tool 只能由人工确认后的确定性 Service 调用。

### P0 使用范围

P0 只完成框架基线，不实现完整 Agent：

- 加入 LangChain、LangGraph 依赖
- 定义 LLM Provider 接口
- 定义 LangChain Qwen Chat Model 适配边界
- 定义基础 `AgentState` 和 `AgentResult` Schema
- 定义空的 Supervisor Graph 构建入口
- 增加 LangChain/LangGraph 版本和启动检查

P0 不做：

- 完整 ReAct Agent
- 业务 Tool
- 多 Agent 并行
- 记忆 Graph
- 交易 Graph

### P1-P5 使用范围

- P1：LangChain Retriever + 一个客服 ReAct Agent + LangGraph 最小客服图
- P2：Advisor/Analyst Agent 节点和只读 Tool
- P3：Supervisor Graph、Operation 草稿节点、人工确认节点、确定性 Service
- P4：Risk/Memory Worker 与 LangGraph 记忆提取图
- P5：Fan-out/Fan-in 多 Agent、GraphRAG 和高级 LangGraph 状态持久化

### 版本策略

LangChain 和 LangGraph 版本必须写入 `requirements.txt` 并通过集成测试锁定行为。后续升级时必须同步更新：

```text
docs/DECISIONS.md
docs/P0-P5_IMPLEMENTATION_PLAN.md
```

## 2026-07-28 LangChain / LangGraph 版本修订

负责人已明确：项目使用 LangChain 与 LangGraph **V1.0 版本线**，不使用此前的 0.3/0.2 版本约束。

当前依赖约束：

```text
langchain>=1.0,<2
langchain-core>=1.0,<2
langchain-community>=1.0,<2
langgraph>=1.0,<2
langchain-openai>=1.0,<2
```

后续安装验证后，再将实际可用的补丁版本写入锁定文件。代码实现必须以 V1.0 官方 API 为准，不得按照旧版 0.x 教程编写。

迁移原则：

- 不使用旧版 `langchain` 0.x/0.3 API 示例作为实现依据。
- 不使用旧版 LangGraph API 直接复制代码。
- P0 先做版本安装和导入检查。
- P1 再实现 LangChain V1.0 Retriever 与客服 Agent。
- P3 再实现 LangGraph V1.0 Supervisor、多 Agent 和人工确认节点。


## 2026-07-29 Agent 通信与 GraphRAG 检索策略决策

### 一、Agent 是否需要相互通信

当前推荐：**领域 Agent 默认不直接互相通信，由 Supervisor 负责调度和汇总。**

```text
用户请求
    ↓
Supervisor
    ├── 选择领域 Agent
    ├── 为 Agent 分配只读上下文和 Tool 白名单
    ├── 并行或串行调用 Agent
    ├── 校验 AgentResult
    ├── 处理冲突和缺失
    └── 生成最终回答/触发确定性 Service
```

领域 Agent 的默认定位：

```text
CustomerServiceAgent：知识和政策证据
AdvisorAgent：客户画像与产品适配分析
RiskAgent：风险规则和风险证据
AnalystAgent：只读统计和数据分析
OperationAgent：意图解析、参数抽取、操作草稿
```

Agent 返回结构化 `AgentResult`，而不是互相发送自然语言消息。Supervisor 将必要的前序结果作为只读上下文传给后续 Agent。

### 二、什么时候允许 Agent 间间接协作

允许两种受控协作：

#### 1. Supervisor 编排协作（默认）

```text
Supervisor → AdvisorAgent
Supervisor → RiskAgent
Supervisor 汇总 AdvisorResult + RiskResult
```

#### 2. 事件驱动协作（异步业务事件）

用于事实已经发生后的后续处理，不用于 Agent 自由聊天：

```text
transaction.created
    ↓
Risk Worker / RiskAgent

risk.alert.created
    ↓
AdvisorAgent 查询风险上下文
```

事件通过 Redis Streams 和 `async_task/outbox_event` 传递，Agent 不直接操作其他 Agent 的内部状态。

### 三、Agent 之间不允许的通信

禁止：

- Agent 直接调用另一个 Agent 的内部方法；
- Agent 直接修改另一个 Agent 的记忆；
- Agent 绕过 Supervisor 私自增加 Tool；
- Agent 通过自然语言无限递归转发；
- Agent 之间共享未经权限过滤的客户上下文；
- Agent 通过队列直接执行资金操作。

### 四、GraphRAG 的定位

Neo4j 不是 PostgreSQL + pgvector 的替代品。两者解决不同问题：

```text
PostgreSQL + pgvector：语义相似、文本证据、政策和产品说明
Neo4j：实体关系、路径、多跳关联、结构化关系约束
```

推荐采用 **Query Router + 双路检索 + 融合/重排**，而不是固定“先 Neo4j 再向量”或固定“先向量再 Neo4j”。

### 五、GraphRAG 路由策略

#### 路由 A：文本/政策问题

例如：

```text
这款产品的封闭期多久？
适当性管理有哪些要求？
```

流程：

```text
问题分类
  ↓
PostgreSQL FTS + pgvector
  ↓
文本证据
  ↓
Qwen 生成
```

Neo4j 不参与或只做轻量实体校验。

#### 路由 B：关系/多跳问题

例如：

```text
这个产品适合哪些风险等级的客户？
与新能源行业相关的产品有哪些？
客户 A 持有的产品属于哪些行业？
```

流程：

```text
实体识别/关系识别
  ↓
Neo4j 结构化 Cypher 查询
  ↓
得到实体、关系和候选 ID
  ↓
PostgreSQL + pgvector 检索对应文本证据
  ↓
融合关系事实和文本证据
  ↓
Qwen 生成
```

这里是“先图后文”，因为图谱结果能够缩小向量检索范围。

#### 路由 C：产品推荐/风险判断

例如：

```text
客户 A 适合哪些产品？
这个产品是否适合 C3 客户？
```

流程：

```text
PostgreSQL 业务事实和适当性规则
  + Neo4j 产品/风险关系
  + pgvector 产品和政策文本
  ↓
确定性规则先过滤
  ↓
图谱补充关系
  ↓
向量检索补充解释文本
  ↓
Qwen 生成说明
```

注意：风险等级和适当性判断不能只由 GraphRAG 或 LLM 决定，必须由确定性 Service/Rule Engine 给出。

#### 路由 D：开放式复杂问题

例如：

```text
请综合分析客户 A 的持仓、行业分布、风险等级和相关政策。
```

流程：

```text
Supervisor 拆分子问题
  ├── PostgreSQL 业务查询
  ├── Neo4j 关系查询
  └── pgvector 文本检索
          ↓
Fan-in Aggregator
          ↓
证据一致性检查
          ↓
Qwen 生成
```

### 六、为什么不固定一个检索顺序

固定“先 Neo4j”会导致：

- 纯政策问题无必要查询图谱；
- 图谱实体识别失败时阻塞普通 RAG；
- 图谱不完整时召回范围被错误缩小。

固定“先向量”会导致：

- 多跳关系问题容易召回语义相似但关系错误的文本；
- 客户-产品-行业路径无法可靠表达；
- 需要大量文本才能推断结构关系。

因此顺序由 Query Router 决定，默认原则：

```text
关系问题：图 → 文
文本问题：文
复杂问题：图 + 文并行
推荐/风控：业务事实/规则 → 图 → 文
```

### 七、Neo4j 最小数据范围

P5 先构建最小图谱，不做全量自动抽取：

节点：

```text
Customer
Product
RiskLevel
Industry
Policy
```

关系：

```text
Customer -[:HOLDS]-> Product
Product -[:SUITABLE_FOR]-> RiskLevel
Product -[:RELATED_TO]-> Industry
Policy -[:REGULATES]-> Product
Customer -[:INTERESTED_IN]-> Industry
```

Neo4j 只保存可重建的关系投影；PostgreSQL 仍是客户、产品、交易、风险和政策元数据的事实源。

### 八、当前实现优先级

- P1：只实现 PostgreSQL + pgvector 文本 RAG。
- P2：业务事实和固定查询 Tool。
- P3：Supervisor 根据意图选择 Agent；Agent 默认不直接通信。
- P4：风险/记忆使用事件和 Supervisor 上下文协作。
- P5：实现 Query Router、Neo4j 图后文、复杂问题图文并行。


## 2026-07-29 Agent 范式最小化决策

### 总结结论

当前项目不同时实现 ReAct、Plan-and-Execute、Reflexion、Self-Ask 四套复杂范式。采用最小充分集合：

```text
主范式：受控 ReAct
编排范式：Supervisor 路由 + 必要时有限 Fan-out/Fan-in
业务执行：确定性 Service
```

### 各范式取舍

| 范式 | 当前决策 | 使用场景 | 原因 |
|---|---|---|---|
| ReAct | 保留，主范式 | 客服、投顾、风控、分析 Agent 调用 Tool | 与当前 Tool Calling 和检查节点一致，实现成本低 |
| Plan-and-Execute | 不作为独立框架 | 仅在 Supervisor 对复杂问题做轻量任务拆分 | 避免每个请求都增加规划模型调用和延迟 |
| Reflexion | 暂不实现 | 不进入 P0-P4 默认链路 | Check 节点 + 失败重试已覆盖基础纠错需求，避免 Token 和循环膨胀 |
| Self-Ask | 暂不实现 | 不进入默认链路 | 由 Supervisor/Query Router 做问题分类和有限拆分即可 |
| Chain-of-Thought | 不暴露、不持久化 | 只允许模型内部生成，不作为系统协议 | 避免泄露内部推理和增加日志负担 |

### 推荐执行流

```text
用户请求
    ↓
QueryRouter / Supervisor
    ├── 简单问题：单 Agent ReAct → Tool → Check → Final
    ├── 复杂问题：Supervisor 轻量拆分 2-3 个子任务
    │       ├── Agent A ReAct
    │       ├── Agent B ReAct
    │       └── Agent C ReAct（必要时）
    │              ↓
    │         Aggregator Check
    └── 业务操作：Agent 解析/生成草稿 → 人工确认 → 确定性 Service
```

### 复杂度限制

- 默认不做 Plan-and-Execute 的独立 Planner Agent。
- 子任务最多 3 个，默认最多 2 个并行领域 Agent。
- 每个 Agent `max_iterations=4`、`max_tool_calls=8`。
- Supervisor 只做一次轻量拆分，不允许递归拆分。
- 检查失败优先重试当前 Tool 或降级，不启动 Reflexion Agent。
- Self-Ask 只作为未来 P5 实验开关，不进入主链路。

### LangGraph 映射

```text
START
  ↓
Supervisor / QueryRouter
  ├── direct_agent
  ├── bounded_parallel_agents
  └── operation_draft
          ↓
Check
  ├── retry_same_node（有限次数）
  ├── fallback
  ├── human_confirmation
  └── END
```

不单独创建：

```text
ReflexionGraph
SelfAskGraph
PlanExecuteGraph
```

### 例外场景

只有在后续评测证明当前受控 ReAct 无法满足以下问题时，再引入额外范式：

- 需要超过 3 个明确步骤的长流程；
- 子问题之间有强依赖关系；
- 当前 Agent 经常遗漏关键步骤；
- 复杂问题拆分准确率低于目标。

即使引入，也优先在 P5 做独立实验，不直接改变主链路。

## DECISION-20260729-001：讨论默认必须同步进入文档

### 决策

除非用户明确说明“本次讨论不录入”或类似意思，否则每次关于项目架构、业务、数据模型、实现方案、问题处理和下一步计划的讨论，都必须同步写入项目文档。

### 目的

确保切换新窗口、新会话或更换协作者后，仍然可以通过仓库文档恢复完整上下文，避免只依赖当前聊天记录。

### 执行规则

1. 讨论结束后必须更新至少一个合适的文档。
2. 架构或技术方案写入 `docs/DECISIONS.md`。
3. 总体计划、阶段状态和下一步写入 `docs/PLAN.md`。
4. 测试、联调和验证写入 `docs/TESTS.md`。
5. 问题、Bug、阻塞和解决过程写入 `docs/ISSUES.md`。
6. 如果一次讨论同时包含决策、计划和验证，应同步更新多个文档。
7. 文档记录中必须使用具体日期，避免只使用“今天”“刚才”等相对时间。
8. 用户明确要求“不录入”时，本次讨论不写入项目文档。

### 新窗口恢复要求

新窗口开始工作时，必须优先阅读：

```text
docs/README.md
docs/DECISIONS.md
docs/PLAN.md
docs/ISSUES.md
docs/TESTS.md
```

并以这些文档恢复当前项目状态、有效架构、已知问题、测试结果和下一步任务。

## DECISION-20260729-002：前端技术栈切换为 Vue 3 + Axios

### 决策

项目主前端采用：

```text
Vue 3 + Vite + Axios
```

Vue 3 负责页面和组件开发，Axios 负责调用 FastAPI 后端 HTTP API。

### 边界

- 前端不直接连接 PostgreSQL、Redis、pgvector 或其他后端基础设施。
- 前端通过 Axios 调用 FastAPI API。
- Trace ID、统一错误响应和鉴权请求头由 Axios 实例/拦截器统一处理。
- 业务状态和页面交互由 Vue 组件及状态管理负责。
- 后端继续保持 FastAPI、异步 Service、知识库和 Worker 架构，不因前端切换改变后端领域边界。

### 推荐目录

```text
frontend/
├── src/
│   ├── api/          # Axios 实例和 API 模块
│   ├── components/
│   ├── views/
│   ├── router/
│   ├── stores/
│   ├── types/
│   └── App.vue
├── vite.config.ts
└── package.json
```

### 后端配合要求

后端 API 需要保持：

```text
统一 JSON 响应
X-Trace-Id 响应头
明确 HTTP 状态码
OpenAPI 文档
CORS 配置
```

### 状态

已确认，后续前端实现以 Vue 3 + Axios 为准。

## DECISION-20260729-003：前端从 Streamlit 切换为 Vue 3 + Axios

### 决策

前端实际实现切换为：

```text
Vue 3 + Vite + TypeScript + Axios + Vue Router
```

原有 `frontend/streamlit_app.py` 已移除，不再作为主前端入口。

### 当前实现

- `src/api/http.ts`：Axios 实例、Base URL、超时、Trace ID 和错误处理。
- `src/api/knowledge.ts`：知识库搜索 API。
- `src/api/health.ts`：健康检查 API。
- `src/views/HomeView.vue`：后端状态概览。
- `src/views/KnowledgeView.vue`：知识库检索页面。
- `src/App.vue`：全局布局和导航。

### 约束

前端只通过 HTTP 调用 FastAPI，不直接访问数据库、Redis、向量库或 Worker。

## DECISION-20260729-004：多角色前台/后台与鉴权体系纳入后续架构

### 讨论结论

后续系统需要同时支持面向客户的前台、面向组织内部的后台，以及不同工作人员角色的工作台。前台产品展示和交易必须基于后端真实存在的产品数据；资产、持仓、交易和资料查看必须经过权限控制。系统需要超级管理员，并采用 Access Token + Refresh Token 鉴权。

### 角色范围

第一版建议至少包含：

```text
customer              普通客户
customer_service      客服/客户经理
product_manager       产品工作人员
risk_manager          风控工作人员
operations            运营/交易运营人员
auditor               审计/只读人员
super_admin           超级管理员
```

角色不等于权限。用户可以拥有一个或多个角色，最终权限由 RBAC 权限集合决定。

### 前台

客户前台至少包括：

- 产品展示：只展示后端状态为可售/上架的产品；
- 产品详情：风险等级、期限、起购金额、流动性、说明和适配提示；
- 风险测评和用户画像确认；
- 资产、账户、持仓和交易记录查看；
- 产品购买、赎回或其他交易申请；
- 订单状态和交易结果查看；
- 对话/智能问答；
- 个人资料和安全设置。

### 内部后台

后台根据工作人员角色显示不同工作台：

- 客服/客户经理：客户列表、客户资料、画像、风险测评、持仓和服务记录；
- 产品工作人员：产品 CRUD、产品状态、适配规则和产品知识文档；
- 风控人员：风险测评、风险预警、适当性检查和交易审核；
- 运营人员：订单、交易申请、业务状态和异常处理；
- 审计人员：只读查看客户、产品、交易、权限和审计记录；
- 超级管理员：用户、角色、权限、组织、系统配置和审计记录。

后台不能只靠前端隐藏按钮实现权限，后端每个受保护接口都必须执行权限校验。

### 鉴权

采用：

```text
登录
→ Access Token（短期）
→ Refresh Token（长期、可撤销）
→ Access Token 过期后刷新
→ Refresh Token 轮换
```

建议：

- Access Token 使用 JWT，短有效期；
- Refresh Token 使用随机不透明字符串或带版本的 JWT，服务端持久化哈希；
- Refresh Token 按设备/会话保存，支持撤销、过期和轮换；
- Refresh Token 不放在普通 localStorage；浏览器优先使用 HttpOnly、Secure、SameSite Cookie；
- Axios 响应 401 时只允许单次刷新并重放原请求，刷新失败则退出登录；
- 修改密码、注销设备、管理员禁用用户时撤销相关 Refresh Token；
- 密码只保存强哈希，不保存明文；
- 关键操作需要重新认证或二次确认。

### 数据隔离

客户只能访问自己的：

```text
个人资料
风险测评
资产
账户
持仓
订单
交易记录
对话记录
```

工作人员只能访问职责范围内的数据；超级管理员可以管理系统，但高风险操作仍应保留审计记录。

### 交易边界

前台交易不能直接修改持仓或账户余额。交易流程至少应为：

```text
创建交易申请
→ 适当性校验
→ 风险/限额校验
→ 待审核或自动通过
→ 执行交易（当前可先 Mock）
→ 更新订单状态
→ 更新持仓/资产快照
→ 记录审计事件
```

第一版可以使用 Mock 交易执行，但必须保留订单、状态和审计模型，不能用前端直接改资产。

### 安全与审计

后续需要：

```text
users
roles
permissions
user_roles
role_permissions
refresh_sessions
audit_logs
login_events
orders
trades
accounts
asset_snapshots
```

### 尚未确定

以下内容仍需业务确认：

1. 客户、客服、客户经理的组织归属和数据范围；
2. 工作人员是否可以跨组织查看客户；
3. 哪些交易允许自动通过，哪些必须人工审核；
4. 账户、资产和交易是否全部使用 Mock；
5. 超级管理员是否允许直接查看客户敏感资产明细；
6. 是否需要短信/邮箱/OTP 二次验证；
7. Refresh Token 是否支持多设备和单设备注销；
8. 前台是否需要注册，还是由后台创建客户账号。

## DECISION-20260729-005：密码加盐与开发测试账号展示边界

### 密码策略

密码必须使用带盐的密码哈希，不能直接保存明文，也不能使用裸 SHA-256 作为正式密码存储方案。正式实现采用 Argon2id 优先，bcrypt 作为兼容备选。现代密码哈希库会为每个密码生成随机盐并将盐参数编码在哈希字符串中，数据库无需单独保存明文盐。

### 测试账号策略

开发环境为便于演示，可以为每类角色提供固定测试账号，并在前端开发环境明确展示账号和密码。该能力必须有环境开关，生产环境关闭。

建议：

```text
DEMO_ACCOUNTS_ENABLED=true   # development only
DEMO_ACCOUNTS_ENABLED=false  # staging/production
```

前端只在 `import.meta.env.DEV` 且后端允许演示账号时显示测试账号面板。测试账号必须使用专用演示密码，不能复用真实密码；禁止将真实凭据、API Key 或生产账号写入前端代码、Git 或文档。

### 测试账号角色

开发环境可以提供：

```text
customer_demo
customer_service_demo
product_manager_demo
risk_manager_demo
operations_demo
auditor_demo
super_admin_demo
```

测试账号必须只连接构造数据和本地开发数据库，不能访问真实资产、真实交易或生产服务。

### 风险边界

前端显示明文测试密码只允许用于本地/开发演示，不是安全设计；任何部署到共享环境、测试环境或生产环境前都必须关闭，并轮换演示账号密码。

## DECISION-20260729-006：测试账号凭据放入本地 TXT，不在前端展示

### 决策

开发测试账号和密码不在前端页面显示，也不写入前端源代码；统一放入本地开发凭据文件，例如：

```text
docs/DEMO_ACCOUNTS.txt
```

该文件只用于本地开发联调，必须加入 `.gitignore`，不提交到版本库、不部署到服务器、不放入 Docker 镜像。仓库只保留脱敏模板：

```text
docs/DEMO_ACCOUNTS.example.txt
```

### 凭据规则

- 每类角色提供一个独立测试账号；
- 密码仍然使用 Argon2id/bcrypt 带盐哈希存入数据库；
- TXT 只保存开发人员登录时需要输入的明文测试密码；
- 测试账号只访问构造数据；
- 测试账号不能复用真实账号或生产密码；
- 共享环境和生产环境必须关闭或删除测试账号；
- 测试密码发生泄露时必须轮换。

### 结果

前端不需要增加测试账号展示逻辑，前端代码保持干净；开发人员从本地 TXT 获取账号密码后，通过正常登录页面完成登录。

## DECISION-20260729-007：客户/员工注册入口与公司码规则

### 注册类型

注册接口使用枚举区分：

```text
customer
employee
```

客户注册不需要公司码；员工注册必须提供后端配置的公司固定公司码。公司码只在后端校验，不能只依赖前端。

### 注册后的初始角色

- 客户注册后绑定 `customer` 角色。
- 员工注册后绑定 `employee_pending` 角色，状态为可登录但未分组。
- `employee_pending` 不授予业务管理权限。
- 后续由管理员将员工加入客服、产品、风控、运营或审计分组。

### 当前暂定

员工分组和管理员审批暂不实现为完整后台流程，先保留角色占位和权限边界。公司码通过 `EMPLOYEE_REGISTRATION_CODE` 配置。

