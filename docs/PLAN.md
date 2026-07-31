# 智能财富管家系统实施计划

> 文档状态：草案（待评估）  
> 创建日期：2026-07-24  
> 项目定位：基于 FastAPI + Streamlit 的智能财富管家演示系统

## 1. 计划目标

在现有需求文档、功能设计文档、记忆架构设计和金融业务资料的基础上，先建设一个可运行、可演示、可扩展的 MVP，再逐步补齐多 Agent、知识图谱、风险预警和记忆体系。

本计划不直接连接真实金融交易系统，所有客户、产品、交易和风控数据均使用脱敏或 Mock 数据。涉及申购、赎回、转账等操作时，必须经过后端校验和用户确认，仅执行模拟业务。

## 2. 已确定的技术边界

### 2.1 前后端

- 前端：Streamlit
- 后端：FastAPI
- API 风格：RESTful，必要时提供 SSE 流式对话接口
- 认证：第一阶段使用 Mock JWT 或开发身份选择，后续再完善权限模型

### 2.2 数据与基础设施

- PostgreSQL：优先作为主业务数据库
- MySQL：保留兼容能力；需要确认是作为既有业务库、对比数据库，还是暂不启用
- Redis：会话上下文、缓存、短期记忆、事件通知
- Milvus：知识库和长期记忆的向量检索
- Neo4j：可选，第二阶段以后用于 GraphRAG 和客户-产品关系图谱
- Docker / WSL2：用于本地运行基础设施和统一开发环境

### 2.3 模型

- 阿里云模型：作为候选主模型
- DeepSeek：作为候选主模型或备用模型
- Embedding：需要单独确定使用阿里云、DeepSeek 生态中的可用模型，或本地中文 Embedding 模型
- 模型调用必须通过统一适配层，避免业务代码直接绑定某一家模型服务

## 3. 总体架构目标

```text
Streamlit 前端
    |
    | HTTP / SSE
    v
FastAPI API 层
    |
    +-- Agent 路由与编排
    |     +-- 客服 Agent
    |     +-- 投顾 Agent
    |     +-- 风控 Agent
    |     +-- 数据分析 Agent
    |     +-- 业务操作 Agent
    |
    +-- 业务服务层
    |     +-- 客户画像
    |     +-- 产品与持仓
    |     +-- 交易模拟
    |     +-- 风控规则
    |     +-- 记忆管理
    |     +-- 知识库管理
    |
    +-- 基础设施适配层
          +-- PostgreSQL / MySQL
          +-- Redis
          +-- Milvus
          +-- Neo4j（可选）
          +-- 阿里云 / DeepSeek 模型
```

架构原则：

1. 先模块化单体，暂不拆分微服务。
2. LLM 只负责理解、规划和生成解释；客户、金额、权限、适当性、风控和交易状态由后端确定性代码负责。
3. 所有外部基础设施通过 Repository 或 Adapter 封装。
4. Agent、Tool、Service、Repository 分层，避免把逻辑全部写在 API 路由中。
5. 先完成一条端到端链路，再扩展功能。

## 4. 分阶段实施计划

## Phase 0：项目基线与环境确认

### 目标

确认开发环境、数据库角色、模型调用方式和项目目录，建立后续协作规范。

### 工作内容

- 建立 `docs/` 文档目录
- 统一文档位置：
  - `docs/PLAN.md`：实施计划
  - `docs/ISSUE.md`：问题、Bug、反馈和待决策事项
  - `docs/API.md`：API 接口文档
  - `docs/DATABASE.md`：数据库设计
  - `docs/ARCHITECTURE.md`：架构说明
- 确认 Python 版本和依赖管理方式
- 确认 Docker Compose 是否统一启动 Redis、Milvus、PostgreSQL、MySQL、Neo4j
- 编写 `.env.example`
- 建立 FastAPI 和 Streamlit 最小启动入口
- 建立健康检查接口

### 交付标准

- 后端可以启动并访问 `/health`
- 前端可以启动并展示系统首页
- Docker 基础设施启动方式明确
- 所有敏感配置只通过环境变量传入

## Phase 1：基础骨架与最小可运行链路

### 目标

实现“用户提问 → 后端处理 → 模型回答 → 前端展示”的完整链路。

### 工作内容

- 创建 FastAPI 项目目录
- 创建 Streamlit 页面框架
- 实现统一 API 响应格式
- 实现日志、异常处理和配置管理
- 实现模型适配层：
  - `AliyunLLMAdapter`
  - `DeepSeekLLMAdapter`
  - `MockLLMAdapter`
- 实现基础对话接口：
  - 普通问答
  - 会话 ID
  - 历史消息读取
  - 可选流式输出
- Redis 保存短期会话记录
- 前端实现：
  - 用户身份选择
  - 会话窗口
  - 模型选择
  - 清空会话

### 交付标准

- 可以在 Streamlit 中向系统提问
- 后端能根据配置切换阿里云、DeepSeek、Mock 模型
- 对话历史可以保存和恢复
- 模型不可用时有清晰错误信息或降级方案

## Phase 2：知识库与 RAG 客服 Agent

### 目标

将现有公司信息、业务资料和金融政策接入知识库，实现有依据的金融问答。

### 工作内容

- 设计知识文档元数据
- 编写 Markdown / TXT 文档导入脚本
- 文档清洗、分段和标题层级保留
- 生成 Embedding
- 写入 Milvus
- 实现向量检索
- 实现来源引用和检索片段展示
- 接入客服 Agent
- 增加回答约束：
  - 不虚构政策内容
  - 不把产品介绍等同于收益承诺
  - 对不确定内容进行提示
  - 涉及投资建议时提示风险和适当性

### 首批资料范围

```text
公司信息/
公司业务/
金融政策/
```

用户测试数据和用户研判规则先不作为公开客服知识直接返回，需要单独做权限和场景控制。

### 交付标准

- 现有资料可以一键导入知识库
- 可以按分类、文件名和标题检索
- 回答能够展示知识来源
- 客服 Agent 可以回答公司、产品、政策和服务流程问题

## Phase 3：客户画像、产品匹配与持仓查询

### 目标

实现基于客户数据的个性化问答和产品筛选。

### 工作内容

- 优先使用 PostgreSQL 建立业务主库
- 建立核心数据表：
  - 用户表
  - 客户画像表
  - 理财产品表
  - 风险评估表
  - 持仓表
  - 交易表
- 导入客户 A、客户 B 测试数据
- 实现客户画像查询 API
- 实现风险等级查询
- 实现产品适当性筛选
- 实现持仓查询
- 实现投顾助手 Agent
- 将客户结构化数据和知识库结果拼接给模型

### 交付标准

- 可以选择客户身份进入系统
- 能展示客户资产、风险等级、持仓和偏好
- 系统能够解释某产品为什么适合或不适合客户
- 产品推荐必须经过后端适当性筛选

## Phase 4：Mock 业务操作与风控预警

### 目标

完成“咨询 → 确认 → 模拟业务操作 → 风控检查 → 结果反馈”的闭环。

### 工作内容

- 实现模拟申购
- 实现模拟赎回
- 实现模拟转账或业务工单
- 实现二次确认机制
- 实现权限校验
- 实现风险等级校验
- 实现基础风控规则：
  - 大额交易
  - 频繁交易
  - 超风险等级购买
  - 余额不足
  - 非交易时间提交
- 保存风险预警
- Redis 发布风险事件
- 实现风控 Agent 页面
- 记录操作审计日志

### 交付标准

- 用户不能绕过确认直接执行模拟交易
- 交易必须产生流水、持仓变化或工单记录
- 风险规则命中后生成预警
- 风控页面可以查看预警状态和命中规则

## Phase 5：记忆体系

### 目标

实现客户短期记忆、长期记忆和基础记忆治理能力。

### 工作内容

- 设计 `memory_unit` 数据结构
- 从对话中提取客户事实和观点
- 区分：
  - 客观事实
  - 主观偏好
  - 行为推断
- 计算记忆初始置信度
- 实现记忆召回
- 处理记忆冲突
- 使用 Milvus 做语义召回，PostgreSQL 做结构化存储
- Redis 保存短期上下文
- 实现人工确认和删除记忆
- 设计定时置信度校准任务

### 交付标准

- 用户明确表达的偏好可以在后续对话中被使用
- 系统能够标记低置信度记忆
- 出现冲突时不会直接覆盖旧数据
- 支持删除或停用指定记忆

## Phase 6：数据分析 Agent

### 目标

实现内部员工使用自然语言查询业务数据的能力。

### 工作内容

- 只允许生成只读 SQL
- 建立允许访问的表和字段白名单
- 动态提供相关数据库 Schema
- 加入 SQL 语法和危险操作检查
- 自动增加结果行数限制
- 记录生成 SQL 和执行审计
- 将查询结果转成自然语言解释
- 支持简单统计：
  - 客户数量
  - 资产规模
  - 产品销售情况
  - 风险预警数量
  - 客户持仓情况

### 交付标准

- 禁止 INSERT、UPDATE、DELETE、DROP 等修改语句
- SQL 执行失败时不会暴露数据库敏感信息
- 只允许访问白名单表和字段
- 结果可解释且可以追溯原始 SQL

## Phase 7：Neo4j GraphRAG（可选）

### 目标

在 RAG 基础上补充客户、产品、风险等级、政策之间的关系查询。

### 工作内容

- 确定 Neo4j 是否作为本项目必选组件
- 设计节点和关系
- 从产品资料和规则资料抽取图谱数据
- 实现 Cypher 查询工具
- 对 Cypher 做只读限制
- 将图谱结果和向量检索结果融合
- 增加 GraphRAG 对比演示

### 交付标准

- Neo4j 不可用时，系统仍能使用普通 RAG
- 图谱结果能够展示关系路径
- 不允许模型直接执行任意写入 Cypher
- 能完成至少一个 GraphRAG 答辩演示场景

## Phase 8：系统集成、测试与答辩演示

### 目标

把各模块组合成稳定的演示系统，并准备完整演示脚本。

### 工作内容

- 统一前端页面和导航
- 完善角色权限
- 增加接口错误提示
- 增加基础单元测试和接口测试
- 制作 Docker 启动说明
- 编写数据初始化脚本
- 编写知识库初始化脚本
- 准备演示数据重置脚本
- 准备答辩演示流程

### 推荐演示流程

1. 客户 A 登录并咨询产品
2. 系统读取客户画像并推荐合适产品
3. 客户发起模拟申购
4. 系统要求二次确认
5. 风控规则检查并产生结果
6. 持仓发生变化
7. 风控专员查看风险预警
8. 内部员工使用自然语言查询数据
9. 展示知识库来源和客户记忆
10. 可选展示 GraphRAG

## 5. 推荐的初始代码目录

```text
项目根目录/
├── app/
├── frontend/
├── scripts/
├── sql/
├── tests/
├── prompts/
├── data/
├── docs/
│   ├── PLAN.md
│   ├── ISSUE.md
│   ├── API.md
│   ├── DATABASE.md
│   └── ARCHITECTURE.md
├── docker-compose.yml
├── .env.example
├── requirements.txt
└── README.md
```

本次暂不创建上述代码目录，只先建立计划文档；待计划确认后再开始搭建项目骨架。

## 6. 关键技术决策待确认

### 6.1 PostgreSQL 和 MySQL 的关系

建议方案：

- PostgreSQL 作为本项目主业务数据库
- MySQL 暂不接入业务链路，或作为兼容/对比数据库
- 不建议同一份业务数据同时写入 PostgreSQL 和 MySQL

需要确认：MySQL 是否已有数据或既定表结构？

### 6.2 模型主备策略

建议方案：

- 首选模型：根据实际 API 额度和稳定性选择阿里云或 DeepSeek
- 备用模型：另一家模型
- 开发测试：Mock 模型
- 通过统一 `LLMProvider` 接口切换

### 6.3 Neo4j 使用时机

建议先作为可选组件：

- MVP 阶段不依赖 Neo4j
- RAG、客户画像和风控跑通后再接入
- Neo4j 停止或不可用时，不影响普通问答和交易模拟

### 6.4 Embedding 方案

必须尽早确认 Embedding 模型，因为 Milvus 集合的向量维度一旦确定，后续不能随意混用。

建议至少统一以下配置：

```text
EMBEDDING_PROVIDER
EMBEDDING_MODEL
EMBEDDING_DIMENSION
CHUNK_SIZE
CHUNK_OVERLAP
```

## 7. 第一轮完成后的验收清单

- [ ] `docs/PLAN.md` 已确认
- [ ] FastAPI 后端可以启动
- [ ] Streamlit 前端可以启动
- [ ] PostgreSQL 连接正常
- [ ] Redis 连接正常
- [ ] Milvus 连接正常或有明确降级方案
- [ ] 阿里云 / DeepSeek 至少一个模型可调用
- [ ] 文档可以导入知识库
- [ ] 客服问答可以运行
- [ ] 客户 A、客户 B 可以加载
- [ ] 模拟交易可以执行
- [ ] 风控预警可以产生
- [ ] 关键操作有日志和审计记录

## 8. 当前建议

现阶段建议按照以下顺序继续：

1. 先确认本计划，不急于写业务代码。
2. 确认 PostgreSQL 与 MySQL 的职责。
3. 确认阿里云与 DeepSeek 的主备关系。
4. 确认是否已有 Docker Compose 或需要新建。
5. 确认 Embedding 模型。
6. 计划确认后，再创建项目代码骨架和 `docker-compose.yml`。

# 计划补充：RAG 复用、异步架构与 Agent 决策

## 2026-07-24 负责人反馈

- 前端确定为 Streamlit
- 后端确定为 FastAPI
- 基础设施可使用 Redis、Milvus、PostgreSQL、MySQL，Neo4j 可选
- 使用 Docker 和 WSL2，但尚未创建 docker-compose.yml
- 主模型倾向 DeepSeek
- Embedding 主要使用阿里云 DashScope `text-embedding-v4`
- 负责人倾向 Redis 队列和全链路异步，包括异步 ORM
- 暂不创建代码骨架，先评审整体架构和数据库设计
- 需要检查并复用 `C:/pg_prefect` 与 `C:/simple_rag`
- 需要评估多 Agent 与强 Agent + Tool Calling 的取舍

## 评估结论

当前推荐：

1. PostgreSQL 作为主业务数据库候选，MySQL 作为兼容方案，不做双主双写。
2. Redis Streams + Consumer Group 作为可靠任务队列候选，Redis Pub/Sub 只用于非关键实时通知。
3. DeepSeek 通过统一 LLM Provider 接口接入。
4. DashScope `text-embedding-v4` 通过统一 Embedding Provider 接入，向量维度以实际 API 响应和启动校验为准。
5. RAG 能力优先复用 `C:/simple_rag` 的混合检索、RRF、去重、缓存和测试思路。
6. 文档版本、状态、Chunk 复用 `C:/pg_prefect` 的 PostgreSQL Async ORM 和版本化设计。
7. 第一版使用 Supervisor + 强 Agent + Tool Calling；领域 Agent 作为受控策略模块，不做完全自治多 Agent。

详细方案见：

- `docs/ARCHITECTURE_DATABASE_DRAFT.md`
- `docs/PLAN.md`

# 计划补充：架构决策更新（2026-07-24）

## 负责人最新意见

- Prefect 暂时移出本项目主架构，优先级很低，不纳入 MVP 和实时链路。
- `text-embedding-v4` 向量维度固定为 1024，并在启动时和写入/检索时进行维度校验。
- Redis 队列第一方案采用生产者-消费者模式，优先考虑 Redis Streams + Consumer Group。
- 团队能力不足时提供通用降级队列方案，保证项目可以先跑起来。
- PostgreSQL 作为主业务数据库。
- 第一版采用 Supervisor + Tool Calling。
- 多 Agent 并行作为可选方案保留，待进一步讨论后决定是否落地。

## 当前技术决策状态

| 领域 | 第一方案 | 降级/备选方案 | 状态 |
|---|---|---|---|
| 主业务数据库 | PostgreSQL + SQLAlchemy Async ORM | MySQL + SQLAlchemy Async ORM | 已确认 PostgreSQL 优先 |
| 向量数据库 | Milvus | 暂不替换；必要时使用 PostgreSQL/pgvector 兜底检索 | Milvus 保留 |
| Embedding | DashScope `text-embedding-v4`，1024 维 | 暂不切换模型，只保留 Provider 接口 | 已确认 |
| 任务队列 | Redis Streams + Consumer Group | Redis List + BRPOPLPUSH/processing list + ACK/重试 | 第一方案确定 |
| 实时通知 | Redis Streams 或 Pub/Sub | 直接查询数据库状态 | 非关键链路可降级 |
| Agent | Supervisor + Tool Calling | 规则路由 + 固定 Service 调用 | 第一版确定 |
| 多 Agent | 并行领域 Agent / Fan-out-Fan-in | 不启用，使用 Supervisor 单 Agent | 可选方案 |
| 批处理监控 | 普通 Worker + 数据库任务状态 | 后续再接调度平台 | Prefect 暂不使用 |

## Prefect 处理结论

本项目暂时移除 Prefect，理由：

1. 当前目标是业务闭环和答辩演示，不是任务编排平台建设。
2. Prefect Server、Worker、部署和监控会增加基础设施复杂度。
3. Redis 队列已经能够满足文档导入、风控处理、记忆提取等任务的生产者-消费者需求。
4. 任务状态、错误、重试、审计直接落 PostgreSQL 更直观。

后续只有在以下情况出现时，才重新评估 Prefect 或其他调度平台：

- 需要大量定时数据管道；
- 需要复杂 DAG；
- 需要可视化任务依赖和历史运行；
- Redis Worker + 数据库任务状态已经无法满足管理需求。

## Redis 队列第一方案

### 生产者

FastAPI 业务接口或后台服务创建任务：

1. 在 PostgreSQL 写入 `async_task`，状态为 `queued`。
2. 在同一事务中写入 `outbox_event`，确保业务数据和事件记录一致。
3. Outbox Publisher 将任务投递到 Redis Stream。
4. Worker 消费任务并更新任务状态。

### 消费者

Worker 使用 Consumer Group：

1. 读取消息。
2. 根据 `task_type` 分发处理器。
3. 执行幂等检查。
4. 成功后 ACK，并将任务更新为 `success`。
5. 失败后增加 `attempt`。
6. 达到最大重试次数后进入 Dead Letter Stream，并将任务更新为 `dead`。

### 推荐 Stream

```text
stream:wealth:ingestion
stream:wealth:risk
stream:wealth:memory
stream:wealth:analytics
stream:wealth:notification
stream:wealth:dead-letter
```

## Redis 队列降级方案

如果团队暂时不会 Redis Streams，可以提供统一队列接口，底层使用 Redis List：

```text
生产者：LPUSH queue:{name} message
消费者：BRPOPLPUSH queue:{name} processing:{worker} 0
成功：LREM processing:{worker} 1 message
失败：记录 retry_count，重新 LPUSH；超限进入 dead-letter
```

最低要求：

- 必须有 processing 队列，不能单纯 BRPOP 后丢弃消息。
- 必须有任务 ID 和幂等键。
- 必须把任务状态写入 PostgreSQL。
- 必须有重试次数上限。
- 必须有失败任务查询接口。

这样可以先用简单实现，后续再把底层替换成 Streams，不改业务层代码。

## Agent 方案更新

### 方案 A：第一版推荐——Supervisor + Tool Calling

```text
用户请求
  ↓
Supervisor
  ├── 识别意图
  ├── 读取角色和客户上下文
  ├── 选择允许的工具
  ├── 调用工具
  ├── 判断是否需要确认
  └── 生成最终回答
```

适合第一阶段，因为：

- 路径清晰；
- 容易调试；
- Tool 权限容易限制；
- 数据库和资金操作更可控；
- LLM 调用次数和延迟更容易控制。

### 方案 B：可选——多 Agent 并行

如果项目要求体现“多 Agent 协作”，可以采用受控的并行 Fan-out-Fan-in：

```text
用户请求
  ↓
Supervisor / Router
  ├── 并行调用 CustomerServiceAgent
  ├── 并行调用 AdvisorAgent
  ├── 并行调用 RiskAgent
  └── 并行调用 AnalystAgent
          ↓
     Aggregator / Judge
          ↓
       最终回答
```

注意：不是所有请求都同时调用全部 Agent。应先由 Router 根据意图选择参与者，例如：

```text
产品推荐：AdvisorAgent + RiskAgent
产品政策咨询：CustomerServiceAgent
交易前检查：OperationAgent + RiskAgent
客户资产分析：AdvisorAgent + AnalystAgent
```

并行模式的额外要求：

- 每个 Agent 有独立 Tool 白名单。
- 设置总超时和单 Agent 超时。
- Agent 结果必须带来源、置信度和错误状态。
- Aggregator 不得无条件采信单个 Agent 结论。
- 资金操作不能因为并行 Agent 建议就直接执行，必须回到确定性 Service 和确认流程。
- 对相同数据查询进行缓存，避免并行放大数据库和模型压力。

### 方案 C：降级——规则路由 + 固定 Service

当模型不可用或团队暂时不实现 Agent 编排时：

```text
关键词/规则路由
  ↓
固定业务 Service
  ↓
RAG 或数据库查询
  ↓
模板化回答
```

该方案不具备完整 Agent 能力，但可以保证演示系统可运行。

## Embedding 维度校验

`text-embedding-v4` 固定使用 1024 维。需要在三个位置校验：

1. 应用启动时：检查配置维度为 1024。
2. 写入 Milvus 前：检查每条向量长度为 1024。
3. 查询 Milvus 前：检查 Query 向量长度为 1024。

建议配置：

```text
EMBEDDING_PROVIDER=dashscope
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSION=1024
```

如果 API 实际返回维度不为 1024，应立即失败并记录明确错误，禁止把错误维度写入集合。

## 文档和目录调整建议

由于 Prefect 被移除，后续目标目录不再包含 Prefect Server 和 Prefect Worker：

```text
项目根目录/
├── app/
├── frontend/
├── workers/
│   ├── ingestion_worker.py
│   ├── risk_worker.py
│   └── memory_worker.py
├── scripts/
├── sql/
├── tests/
├── prompts/
├── data/
├── docs/
└── docker-compose.yml
```

`docker-compose.yml` 后续只需要编排：

```text
api
frontend
worker-ingestion
worker-risk
worker-memory
postgres
redis
milvus 及其依赖
```

Neo4j 作为可选 Profile，不默认启动。


## 2026-07-24 架构决策确认：Supervisor + 受控多 Agent

### 已确认

- PostgreSQL 作为第一版主业务数据库。
- Redis Streams + Consumer Group 作为第一版任务队列实现。
- 第一版采用 Supervisor + 多 Agent 的组合模式。

### 推荐的多 Agent 角色

多 Agent 不作为多个可以自由行动的自治主体，而是作为 Supervisor 管理下的领域分析器/数据提供器：

```text
用户请求
    ↓
Supervisor
    ├── 意图识别
    ├── 用户身份与权限判断
    ├── 任务拆解
    ├── 并行调用一个或多个领域 Agent
    ├── 汇总和校验 Agent 返回结果
    ├── 调用确定性业务 Service
    └── 生成最终响应
```

建议领域 Agent：

```text
CustomerServiceAgent   # 公司、产品、政策知识检索
AdvisorAgent           # 客户画像、产品适配、持仓分析
RiskAgent              # 风险规则计算、预警查询、合规检查
AnalystAgent           # 只读数据分析和统计
OperationAgent         # 解析业务意图，但不直接执行资金操作
```

### Agent 的权限分级

#### L0：只读数据 Agent

允许：

- 查询知识库
- 查询客户画像
- 查询持仓
- 查询风险评估
- 查询产品
- 执行受限的只读 SQL
- 返回结构化分析结果

禁止：

- 修改业务数据
- 发送不可撤销通知
- 执行交易
- 修改权限
- 直接操作 Redis 队列中的关键业务任务

#### L1：建议/草稿 Agent

允许：

- 生成产品推荐草稿
- 生成风险处置建议
- 生成业务操作草稿
- 生成工单草稿
- 请求 Supervisor 发起后续确定性 Service

禁止：

- 直接提交申购、赎回、转账
- 直接修改持仓、余额和交易状态
- 绕过用户确认

#### L2：确定性业务 Service

这一层不属于自由 Agent，而是普通后端业务服务：

- 校验权限
- 校验客户状态
- 校验风险等级和产品适当性
- 校验余额和交易金额
- 校验幂等键
- 写入 PostgreSQL 事务
- 创建 Outbox 事件
- 投递 Redis Stream 任务
- 执行确认后的 Mock 交易

只有 L2 Service 可以改变核心业务状态。

### 关键原则

> 其它 Agent 可以负责“查数据、算结果、给建议、生成草稿”，但不能直接改变核心业务状态。

推荐将 Agent 输出统一为结构化结果，而不是让 Agent 直接调用任意 API：

```json
{
  "agent_name": "RiskAgent",
  "status": "success",
  "summary": "交易命中大额交易规则",
  "data": {
    "risk_level": "medium",
    "matched_rules": ["R001"]
  },
  "evidence": [
    {"source_type": "risk_rule", "source_id": "R001"}
  ],
  "confidence": 0.92,
  "requires_human_confirmation": false
}
```

Supervisor 只允许消费经过 Schema 校验的 AgentResult，不直接信任自然语言文本。

### 多 Agent 并行策略

不是所有请求都调用全部 Agent，而是根据场景动态选择：

```text
产品推荐：AdvisorAgent + RiskAgent + CustomerServiceAgent
产品政策咨询：CustomerServiceAgent
交易前检查：OperationAgent + RiskAgent + AdvisorAgent
客户资产分析：AdvisorAgent + AnalystAgent
风控预警处置：RiskAgent + AdvisorAgent
```

Supervisor 对并行调用设置：

- 单 Agent 超时
- 总体超时
- 最大并行 Agent 数
- 失败 Agent 是否允许降级
- 结果合并规则
- 结果冲突处理

### 多 Agent 不应负责的事情

以下操作不能由 Agent 直接完成：

```text
修改客户余额
修改持仓
改变交易状态
绕过二次确认
删除审计日志
修改风险规则
执行任意 SQL
执行任意 Cypher
```

### 推荐请求流程示例：模拟申购

```text
用户：帮我购买 10 万元某理财产品
    ↓
Supervisor 识别为 purchase_intent
    ↓
OperationAgent 解析产品和金额，返回操作草稿
    ↓
AdvisorAgent 返回客户适配性分析
    ↓
RiskAgent 返回风险检查结果
    ↓
Supervisor 汇总结果并展示确认信息
    ↓
用户明确确认
    ↓
PurchaseService 执行确定性校验
    ↓
PostgreSQL 事务写入交易/持仓
    ↓
Outbox Publisher 投递 transaction.created
    ↓
Risk Worker 消费并执行后续风控
    ↓
返回交易结果
```

### 结论

可以采用 **Supervisor + 多 Agent**，但多 Agent 应被定位为：

```text
只读查询器
分析器
建议生成器
草稿生成器
```

真正改变业务状态的能力必须集中在确定性 Service，并通过 PostgreSQL 事务、幂等校验和 Redis Streams 任务机制执行。


## 2026-07-24 记忆模块专项建议与模型统一

### 模型统一

项目模型统一使用 Qwen / DashScope，暂不同时维护 DeepSeek 和阿里云两套模型供应商适配。

建议：

```text
Chat Model: Qwen 系列
Embedding: text-embedding-v4
Embedding Dimension: 1024
```

LLM 和 Embedding 仍通过 Provider 接口隔离，便于未来替换，但第一版只实现 DashScope Provider。

### ReAct Agent 约束

每个领域 Agent 使用受控 ReAct 执行循环：

```text
Thought/Plan（内部状态，不直接暴露）
    ↓
Action：选择 Tool
    ↓
Observation：读取 Tool 返回
    ↓
Check：校验结果和权限
    ↓
Reflect：判断是否需要补查、纠错或停止
    ↓
Final：返回结构化 AgentResult
```

不建议将完整隐藏思维链保存或展示给用户。系统只记录：

- 计划摘要
- Tool 调用名称和参数摘要
- Tool 返回摘要
- 校验结果
- 反思结论摘要
- 最终结构化结果

### ReAct 的三类检查

#### 1. 工具调用前检查

- 当前 Agent 是否拥有该 Tool 权限
- 参数是否符合 Pydantic Schema
- 客户 ID 是否与当前身份匹配
- 是否涉及敏感数据
- 是否属于只读操作
- 是否需要用户确认
- 是否超过调用次数、预算和超时

#### 2. 工具调用后检查

- 返回结构是否符合 Schema
- 数据是否为空、过期或冲突
- 查询结果是否超过限制
- 结果是否有来源和时间
- 风险规则是否完整执行
- 是否出现权限越界或异常数据

#### 3. 最终输出前反思

- 是否回答了用户真实问题
- 是否引用了证据
- 是否混淆事实、推断和建议
- 是否给出了未经验证的收益或承诺
- 是否需要人工确认
- 是否需要降级为“信息不足，请补充资料”

### API 封装为 Tool 的建议

API 不应直接等同于 Tool。建议分三层：

```text
Agent Tool
    ↓
Application Service
    ↓
Repository / External Adapter
```

Agent 只能调用白名单 Tool，Tool 再调用内部 Service。这样可以在 Tool 层统一做：

- 参数校验
- 权限校验
- 审计
- 超时
- 限流
- 脱敏
- 幂等
- 结果 Schema 化

工具类型建议：

```text
Read Tool：查询知识、画像、产品、持仓、风险
Analysis Tool：产品匹配、风险计算、只读 SQL
Draft Tool：生成交易草稿、工单草稿、回答草稿
Execute Tool：只暴露给确定性 Service，不直接给自由 Agent
```

第一版原则：

> Agent 可以调用 Read / Analysis / Draft Tool；Execute Tool 不直接开放给普通 ReAct Agent。

### ReAct Agent 统一接口

建议每个 Agent 都实现统一接口：

```python
async def run(context: AgentContext) -> AgentResult:
    ...
```

`AgentContext` 至少包含：

```text
request_id
trace_id
user_id
customer_id
role
conversation_id
user_message
short_term_memory
recalled_memories
allowed_tools
budget
```

`AgentResult` 至少包含：

```text
agent_name
status
summary
data
evidence
tool_calls
confidence
requires_confirmation
next_action
errors
```

### 记忆模块判断

现有《记忆架构设计.html》的方向是正确的，尤其是：

- 短期、中期、长期分层
- 客观事实与主观观点分离
- 记忆置信度
- 冲突检测
- 时间衰减
- 召回重排
- 记忆验证和遗忘

但实际落地时，记忆模块不应一开始做成“全自动、全量、强行写入”。建议采用 **候选记忆 + 验证 + 生效** 的状态机。

### 推荐记忆流水线

```text
对话/行为/交易
    ↓
Memory Candidate 提取
    ↓
主客观分类
    ↓
敏感信息和权限检查
    ↓
与已有记忆匹配
    ↓
冲突检测
    ↓
置信度计算
    ↓
自动生效 / 待确认 / 拒绝
    ↓
结构化存储 + 向量索引
    ↓
场景召回
    ↓
召回后再次校验
```

### 记忆状态建议

```text
candidate       # 刚提取，尚未生效
pending_review  # 需要用户或人工确认
active          # 当前有效
superseded      # 被新事实替代
conflicted      # 存在冲突，暂不作为强事实
demoted         # 低置信度降权
forgotten       # 逻辑遗忘
deleted         # 按用户请求删除或脱敏
```

### 记忆分级建议

#### A 类：系统事实

来源：交易、账户、正式风评、用户明确修改。

特点：

- 主要由系统数据确认
- 不依赖 LLM 自由推断
- 高置信度
- 不应被普通对话直接覆盖

例如：

```text
客户风险等级=C3
客户持有某产品 10000 份
账户余额=100000 元
```

#### B 类：用户明确陈述

来源：用户直接表达。

例如：

```text
我偏好稳健型产品
我未来半年可能需要用钱
我不喜欢长期锁定产品
```

这类可以自动生成候选记忆，但涉及关键投资偏好时建议标记为待确认或中等置信度。

#### C 类：模型推断

来源：对话分析、行为推断。

例如：

```text
用户可能偏好短期产品
用户可能对新能源行业感兴趣
```

只能作为低置信度建议，不能直接参与强约束适当性判断。

### 关键建议：事实记忆与画像记忆分离

当前记忆设计建议进一步拆开：

```text
事实记忆：发生过什么、系统确认的属性
偏好记忆：用户表达的倾向
推断记忆：模型根据行为推测的倾向
画像快照：在某个时间点汇总后的客户画像
```

不要把所有信息都塞进一个 `memory_unit` 后直接混用。

推荐关系：

```text
memory_unit
    ↓ 提炼/验证
customer_preference
    ↓ 汇总
customer_profile_snapshot
```

其中交易、风险评估和持仓仍以业务表为事实来源，不能由记忆模块覆盖。

### 记忆置信度建议

置信度不能只由 LLM 给出，建议由规则计算：

```text
confidence =
    source_weight
  × recency_factor
  × evidence_factor
  × consistency_factor
  × confirmation_factor
```

示例来源权重：

```text
正式风评/系统交易记录：0.95
用户明确确认：0.90
用户直接陈述：0.70
对话提取：0.55
行为推断：0.35
```

同时设置硬约束：

- 关键风险等级不能只来自推断记忆
- 交易行为以交易表为准
- 用户明确否认后，旧记忆必须进入冲突或替代流程
- 低置信度记忆只能用于辅助回答，不能用于强制决策

### 记忆召回建议

召回不建议只依赖 Milvus 相似度。推荐混合召回：

```text
结构化过滤
    + 关键词/字段检索
    + Milvus 向量检索
    + 场景权重
    + 置信度
    + 新鲜度
    + 冲突惩罚
```

可采用类似以下重排思路：

```text
final_score =
    0.30 * semantic_score
  + 0.20 * confidence
  + 0.15 * recency
  + 0.15 * scenario_match
  + 0.10 * evidence_strength
  + 0.10 * confirmation_score
  - conflict_penalty
```

具体权重需要通过测试集校准，不应直接当成最终生产参数。

### 记忆写入建议

不要每轮对话都同步调用多个 LLM 做完整记忆提取。推荐：

```text
用户对话完成
    ↓
主流程先返回回答
    ↓
Redis Streams 投递 memory.extract 任务
    ↓
Memory Worker 异步提取候选记忆
    ↓
校验、冲突检测、置信度计算
    ↓
PostgreSQL 写入
    ↓
需要向量化时投递 memory.embed 任务
```

只有以下情况可以同步处理：

- 用户明确说“记住这个”
- 用户明确修改风险偏好或联系方式
- 当前回答必须依赖刚刚确认的新信息

### 用户删除记忆

“请忘记我说过的话”不能简单删除所有数据。建议区分：

- 可删除的对话提取记忆：逻辑删除并移除向量
- 交易、风评、审计等法定或业务必要记录：保留最小必要数据并限制访问
- 用户偏好：进入 deleted 状态，禁止召回
- 删除动作本身记录审计日志

### 记忆模块第一阶段范围

第一阶段不要实现所有复杂算法，先实现：

1. Redis 短期会话记忆
2. 用户明确偏好提取
3. 事实/观点分类
4. `candidate → active/pending_review` 状态机
5. 基础置信度
6. 结构化召回
7. Milvus 向量召回
8. 冲突检测
9. 用户删除记忆

暂缓：

- 复杂遗忘曲线拟合
- 跨客户关联记忆
- 完全自动画像覆盖
- 复杂元记忆
- 全量历史对话实时重算

### 记忆模块的评测集

必须建立独立测试集，至少覆盖：

- 客观事实提取
- 主观偏好提取
- 模型推断误判
- 新旧偏好冲突
- 用户明确否认
- 用户删除记忆
- 交易事实和对话事实冲突
- 低置信度记忆不应影响推荐
- 多轮对话召回正确记忆

记忆模块没有测试集就无法判断“记住了”是否真的有用。


## P0-P5 分级计划已创建

已根据 `需求文档.html`、`功能设计文档.html`、`记忆架构设计.html` 和 `开发引导.md`，创建：

```text
docs/P0-P5_IMPLEMENTATION_PLAN.md
```

该文档将原需求中的 Phase 1-5 转换为按缓急等级的 P0-P5：

```text
P0：工程基线与安全边界
P1：最小问答链路与 RAG
P2：客户画像、产品和只读数据分析
P3：受控 ReAct、多 Agent 和 Mock 业务操作
P4：风控与记忆核心模块
P5：GraphRAG、增强多 Agent、NL2SQL、性能和答辩优化
```

当前方案暂不进入代码实现，等待负责人评审后冻结范围。


## 2026-07-24 向量存储决策补充：PostgreSQL + pgvector 优先

### 结论

当前项目第一版不强制使用 Milvus，优先采用：

```text
PostgreSQL + pgvector
```

Milvus 从第一版必选组件调整为后续可选扩展，不进入 P0-P4 的硬依赖。

### 为什么 PostgreSQL + pgvector 更适合当前阶段

1. 项目已经确定 PostgreSQL 为主业务数据库。
2. 文档元数据、Chunk、版本、权限、状态和向量可以在同一事务边界内管理。
3. 团队减少一个基础设施，降低 Docker、运维和联调成本。
4. 对当前文档规模和答辩型 MVP，pgvector 通常足够。
5. 现有 `C:/pg_prefect` 已经具备 PostgreSQL/pgvector 和 SQLAlchemy Async ORM 的基础。
6. RAG 检索可以使用 PostgreSQL 全文检索 + pgvector 相似度检索，保留混合检索能力。

### 第一版检索方案

```text
PostgreSQL
├── knowledge_document
├── knowledge_document_version
├── knowledge_chunk
│   ├── content
│   ├── search_vector
│   └── embedding vector(1024)
└── memory_unit
    ├── content
    ├── search_vector
    └── embedding vector(1024)
```

检索流程：

```text
用户问题
    ↓
Qwen text-embedding-v4
    ↓
PostgreSQL pgvector cosine search
    + PostgreSQL Full Text Search / trigram
    ↓
应用层 RRF 或加权重排
    ↓
Qwen 生成回答
```

### 1024 维约束

所有向量字段统一为：

```sql
vector(1024)
```

必须校验：

- 配置维度为 1024
- Embedding API 返回长度为 1024
- 写入前向量长度为 1024
- 查询向量长度为 1024

### 索引建议

PostgreSQL：

- 向量：HNSW `vector_cosine_ops`
- 关键词：GIN `tsvector`
- 可选模糊匹配：pg_trgm

示意：

```sql
CREATE INDEX ix_knowledge_chunk_embedding_hnsw
ON knowledge_chunk USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ix_knowledge_chunk_search_vector
ON knowledge_chunk USING gin (search_vector);
```

具体索引参数和数据量达到一定规模后再调优。

### 现有 RAG 项目的复用调整

`C:/pg_prefect`：

- 直接复用 PostgreSQL/pgvector 文档表、版本表、Chunk 表和异步 ORM 方案。
- 将已有 Embedding 字段维度调整为 1024。
- 复用文档 Hash、版本切换、软删除和增量导入。

`C:/simple_rag`：

- 复用 Qwen Embedding 调用和 Redis Embedding Cache。
- 复用切分、去重、TopK、查询规划和评测集。
- 不直接复用 Milvus Store 作为第一版存储。
- 将 `retrieval.py` 中的混合检索策略改写为 PostgreSQL/pgvector 查询。
- `asyncio.to_thread` 主要保留给阻塞式文件解析或其他同步 SDK，不再用于核心向量查询。

### 何时重新引入 Milvus

满足以下任一条件时再评估：

- Chunk 数量达到百万级；
- 向量检索成为 PostgreSQL 的主要性能瓶颈；
- 需要独立扩展向量检索集群；
- 需要大量并发向量查询和独立索引生命周期；
- 需要复杂的向量数据库运维能力。

引入 Milvus 时，PostgreSQL 仍保存文档版本、权限、状态和 Milvus 主键，不改变主业务事实源。

### 对 P0-P5 的影响

- P0：移除 Milvus 健康检查，增加 pgvector 扩展检查。
- P1：RAG 使用 PostgreSQL + pgvector + FTS。
- P2：无需新增向量基础设施。
- P3：Agent Tool 使用统一 KnowledgeRepository。
- P4：记忆向量存 PostgreSQL，Memory Retrieval 走 pgvector + 结构化过滤。
- P5：Milvus 作为可选扩展和性能对比项。


## 2026-07-24 风险预案补充已纳入

已将以下负责人反馈固化：

- ReAct 最大迭代、Tool 调用、Token 和耗时预算
- 关键画像和“请记住”场景的同步记忆通道
- GraphRAG 最小两步演示预案
- `text-embedding-v4` 启动 Smoke Check
- Repository / Service / Tool 三层职责约束
- M1 后立即提供演示数据一键重置脚本


## 2026-07-24 记忆任务分类已补充

已增加任务分类清单，区分同步和异步：

```text
明确偏好、明确事实、明确修改：同步
历史推断、交易行为分析、对话摘要、情绪分析、全量画像重算：异步
```

详见：

- `docs/P0-P5_IMPLEMENTATION_PLAN.md`
- `docs/ARCHITECTURE_DATABASE_DRAFT.md`


## 2026-07-24 同步记忆超时降级已纳入

同步记忆默认超时为 2.5 秒。超时后不丢失用户输入，自动降级到 `stream:wealth:memory`，本轮使用临时上下文，后台完成持久化和向量化。


## LangChain / LangGraph 当前决策

项目原先没有明确要求 LangChain 或 LangGraph；现已正式加入当前方案。

- LangChain 负责模型、Prompt、Tool、ReAct 和 Retriever 抽象。
- LangGraph 负责 Supervisor、领域 Agent、检查/反思、人工确认和多 Agent 状态编排。
- P0 只接入依赖和基础 Schema，不提前实现完整 Agent。


## 2026-07-28 Agent 框架版本修订

负责人明确使用 LangChain 和 LangGraph V1.0 版本线，之前 `>=0.3` / `>=0.2` 的依赖约束已弃用。

当前实现以 `requirements.txt` 和 `docs/DECISIONS.md` 的 V1.0 约束为准。


## 2026-07-28 文档与进度记录规则确认

负责人确认：以后每次实现完成后必须同步维护以下文档：

```text
docs/PLAN.md
```

有测试或验证时：

```text
docs/TESTS.md
```

发现问题、讨论方案、Bug 修复或延期时：

```text
docs/ISSUES.md
```

形成技术决策时：

```text
docs/DECISIONS.md
```

## P0 当前状态

- P0 工程骨架：已完成第一版
- langchain_env 依赖检查：通过
- LangChain/LangGraph V1.0 导入检查：通过
- Python 编译检查：通过
- P0 Compose 配置检查：通过
- PostgreSQL/Redis 真实联调：待执行
- Outbox Publisher：待实现
- 真实 `/health` 和任务 API 联调：待执行

## 下一步计划

下一步进入 P0 真实基础设施联调：

1. 启动 `docker-compose.p0.yml`
2. 验证 PostgreSQL + pgvector
3. 验证 Redis Streams
4. 启动 FastAPI
5. 测试 `/api/v1/health`
6. 测试任务创建和查询接口
7. 启动 Worker
8. 验证任务状态 `queued → running → success`
9. 记录测试结果和问题

## 2026-07-28 P0 真实基础设施联调完成

### 本次完成

- 启动独立 `docker-compose.p0.yml`
- PostgreSQL/pgvector 健康
- Redis 健康
- FastAPI 临时运行在 `8011`
- `/api/v1/health` 验证通过
- 创建 P0 集成测试任务
- 启动 Worker
- 验证 Redis Streams 任务状态闭环

### 当前状态

```text
P0：基础设施联调完成
下一步：补齐 Outbox Publisher、Trace ID、真实 Qwen Smoke Check（有 API Key 后）
```

## 2026-07-28 P0 可靠任务机制进展

### 已实现

- TaskService 改为 PostgreSQL 事务写入 `async_task + outbox_event`
- Redis Streams 封装增加 Pending 查询和 `XCLAIM` 恢复
- Worker 增加任务 `running/success/failed/dead` 状态处理
- 达到最大重试次数后投递 Dead Letter Stream
- 增加 Trace ID Middleware，响应返回 `X-Trace-Id`
- P0 关键模块编译检查通过

### 当前限制

- Outbox Publisher 还未独立为常驻 Worker；当前任务创建已落 Outbox，但 Publisher 需要下一步接入。
- P0 任务处理器仍是验证型 Handler，业务任务将在 P1-P4 接入。
- Dead Letter 已有投递路径，尚未进行真实失败注入集成测试。

### 下一步

1. 实现 `outbox_worker.py`，从 PostgreSQL 发布 pending Outbox 到 Redis Streams。
2. 将 TaskService 改为只写数据库，不直接写 Redis。
3. 增加失败注入、Pending 恢复和 Dead Letter 测试。
4. P0 通过后进入 P1 RAG 基线。

## 2026-07-28 Outbox 真实联调完成

### 测试结果

已验证：

```text
TaskService
  ↓ PostgreSQL 事务
async_task=queued
outbox_event=pending
  ↓ Outbox Publisher
outbox_event=published
  ↓ Redis Streams
Task Worker 消费
  ↓
async_task=success
```

测试任务：

```text
p0.outbox.integration
p0.outbox.worker
```

结果均为：

```text
attempt=1
status=success
outbox attempt=1
outbox status=published
```

### P0 当前状态

P0 可靠任务闭环已完成：

- Outbox Publisher
- PostgreSQL 事务任务创建
- Redis Streams 发布
- Consumer Group 消费
- ACK
- Pending 恢复代码
- Dead Letter 路径
- Trace ID

下一步进入 P1：PostgreSQL + pgvector RAG 基线。

## 2026-07-28 端口冲突记录

P0 服务当前仍在 8012 运行，健康检查通过。后续启动命令统一使用：

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8012 --reload
```

如果 8012 已被本项目服务占用，直接访问现有服务，不要重复启动；需要重启时先结束旧 PID。

## 2026-07-28 数据库密码配置问题已解决

FastAPI 启动失败的原因是根目录 `.env` 为空，没有读取 P0 Compose 的 `wealth/wealth_dev` 配置。已创建本地 `.env`，并验证 FastAPI 在 8013 启动成功、健康检查返回 200。

以后本地启动前不需要手动设置 `DATABASE_URL` 和 `REDIS_URL`，项目会读取根目录 `.env`。

## 2026-07-28 对 simple_rag 融合状态的检查结论

已检查 `C:/simple_rag` 当前封档版本。结论：它已经吸收了 `C:/pg_prefect` 的大量设计和部分实现，但还不能表述为“已完成 PostgreSQL + pgvector 融合”。

### 已融合/可直接复用的内容

- 文档生命周期：documents、document_versions、ingestion_runs
- 文档 Hash、版本切换、软删除、恢复
- Chunk Diff 和 unchanged/updated/added/deleted 统计
- 增量 Embedding 和向量复用策略
- 文档导入 Worker、Redis 队列、批处理、失败状态
- PostgreSQL Metadata Repository 和 `postgres_schema.sql`
- LangChain 文本切分、Qwen Embedding、Milvus Hybrid Search、BM25、RRF、去重和缓存
- 大量真实回归测试、问题记录和性能记录

### 尚未融合完成的内容

- simple_rag 默认运行 backend 仍是 SQLite
- 向量主存储仍然是 Milvus，不是 PostgreSQL + pgvector
- PostgreSQL Repository 仍处于迁移准备阶段
- simple_rag 的队列仍是旧的 Redis List/BRPOP 风格，不等同于本项目已完成的 Outbox + Redis Streams 方案
- simple_rag 入口、配置、数据库模型和本项目金融业务模型不同，不能直接覆盖当前项目 `app/`

### 当前复用策略

不复制整个 simple_rag，也不直接修改其封档代码。采用“适配层复用”：

```text
simple_rag
├── 文档生命周期和 Chunk Diff：作为 P1 ingestion 参考/迁移来源
├── Qwen Embedding 和切分：优先复用设计和测试
├── Query Planner/路由/去重：后续评估接入
├── Milvus Store：当前项目不直接采用，改写为 PgVector Repository
└── SQLite Metadata：不作为当前项目主库
```

### 对当前项目的影响

当前项目不需要重复实现已经在 simple_rag 中成熟的文档生命周期和检索策略，但必须重新适配：

```text
金融业务 PostgreSQL 主库
PostgreSQL + pgvector
Redis Streams + Outbox
LangChain/LangGraph V1.0
Supervisor + 金融领域受控 Agent
```

下一步 P1 应优先从 simple_rag 迁移/适配以下能力：

1. 文档版本和 Chunk Diff 数据契约；
2. Qwen Embedding Provider；
3. 中文切分器；
4. pgvector 混合检索 Repository；
5. 知识库导入 Worker Handler；
6. RAG 回归测试结构。


## 2026-07-29 Agent 通信与 GraphRAG 方案更新

### 当前有效结论

1. 领域 Agent 默认不直接互相通信，由 Supervisor 负责调度、传递只读结果和最终聚合。
2. Agent 之间的异步协作使用业务事件和 Redis Streams，不使用 Agent 自由对话。
3. Neo4j 与 pgvector 是互补关系，不固定一个全局检索顺序。
4. 关系问题采用“图 → 文”；文本问题采用“文”；复杂问题采用“图 + 文并行”；推荐/风控采用“业务事实/规则 → 图 → 文”。
5. P1-P4 不因 Neo4j 阻塞，P5 再实现 GraphRAG Query Router 和最小图谱。


## 2026-07-29 Agent 范式方案收敛

当前有效 Agent 范式：

```text
受控 ReAct 为主
Supervisor/QueryRouter 轻量拆分
必要时有限 Fan-out/Fan-in
确定性 Service 负责业务执行
```

暂不引入：

```text
独立 Plan-and-Execute Agent
Reflexion Agent
Self-Ask Agent
```

后续只有评测证明现有范式不足时，才在 P5 做独立实验，不改变 P1-P4 主链路。


## 2026-07-29 公共基础设施组件第一批完成

### 已实现

新增 `app/common/` 公共层：

```text
middleware/    Trace ID
logging/       结构化日志和上下文
response/      统一响应模型
exceptions/    统一异常和校验错误
pagination/    分页模型
rate_limit/    Redis 固定窗口限流
idempotency/   Redis 幂等缓存基础
security/      脱敏和审计上下文基础
dependencies/  请求依赖入口
```

已接入现有 FastAPI：

- Trace ID 响应头
- 统一错误响应
- Pydantic 参数校验响应
- 统一健康检查响应
- 任务 API 使用公共异常和响应模型

### 验收结果

```text
公共组件编译检查：通过
FastAPI 8014 启动：通过
/api/v1/health：200
X-Trace-Id：通过
非法任务请求：422 统一校验响应
```

### 下一步

1. 将 Redis 限流接入实际 API 路由。
2. 将幂等存储接入非任务类写接口。
3. 增加审计日志持久化表和 Service。
4. 进入 P1 知识库 Repository/文档生命周期适配。

## 2026-07-29 P1 知识库数据层基线启动

### 已实现

- 新增 `knowledge_base` ORM 模型
- 新增 `knowledge_document` ORM 模型
- 新增 `knowledge_document_version` ORM 模型
- 新增 `knowledge_chunk` ORM 模型
- `knowledge_chunk.embedding` 使用 `vector(1024)`
- 启动 schema 增加 pgvector HNSW 索引
- 新增 `KnowledgeRepository`
- 支持默认知识库、文档创建、文档查询、版本创建、Chunk 批量保存

### 复用边界

本轮参考并准备适配 `C:/simple_rag` 的：

- 文档版本生命周期
- Chunk Diff 数据契约
- 文档 Hash
- 增量 Embedding 思路

当前没有复制 Milvus Store、SQLite Metadata 或旧 Redis List Worker。

### 当前状态

```text
P1 数据库模型基线：完成
P1 文档导入 Worker：未开始
P1 pgvector 实际检索：未开始
P1 LangChain Retriever：未开始
```

### 下一步

1. 为知识库模型生成 Alembic 迁移。
2. 增加知识库 Schema 和文档管理 API。
3. 接入 simple_rag 的解析/切分契约。
4. 实现 Qwen Embedding Provider。
5. 实现 PostgreSQL pgvector 混合检索。

## 2026-07-29 P1 知识库 API 与迁移基线完成

### 已实现

- 新增 Alembic `0002_knowledge_base` 迁移
- 新增知识库、文档、文档版本、Chunk 表结构
- 增加 `vector(1024)` 和 HNSW 索引
- 增加知识库 Pydantic Schema
- 增加默认知识库 API
- 增加文档创建 API
- 增加基础文本搜索 API（当前为 baseline，尚未接入 Embedding）
- Repository 支持文档生命周期基础写入和文本查询

### 已验证

```text
P1 知识库代码编译：通过
FastAPI 8015 启动：通过
GET /api/v1/knowledge/default：200
POST /api/v1/knowledge/documents：201
```

### 当前限制

- 迁移文件已创建，尚未执行独立 Alembic upgrade 回归。
- 当前搜索使用 `ILIKE` baseline，不是最终 pgvector/FTS 混合检索。
- 文档创建还没有接入知识导入任务和 Outbox。
- 尚未接入 simple_rag 的解析、切分和 Qwen Embedding。

### 下一步

1. 执行 `alembic upgrade head` 并验证迁移。
2. 增加 PostgreSQL FTS `search_vector` 字段和检索。
3. 接入 Qwen Embedding Provider，写入 1024 维向量。
4. 实现 pgvector + FTS 混合检索。
5. 接入文档导入 Worker。
## 2026-07-29 P1 文档导入与 Embedding 基线进展

### 本次完成

- 新增 `app/services/knowledge_ingestion.py`：实现纯文本读取、固定窗口切块、chunk hash、版本创建、Embedding、chunk 保存和版本激活。
- 扩展 `app/infrastructure/qwen.py`：接入 DashScope OpenAI-compatible Embedding 客户端，使用 `text-embedding-v4`，校验 1024 维。
- 扩展 `KnowledgeRepository`：补充版本激活和文档失败状态更新。
- 保持失败可追踪：解析、Embedding 或入库异常会写回文档/版本错误状态。

### 当前阶段状态

```text
P0：已完成基础设施联调（Docker 受限环境下本轮未重复启动）
P1：文档模型、API、导入服务和 Qwen Embedding 基线已完成
P1：真实 Alembic/pgvector/Embedding 联调待执行
```

### 下一步计划

1. 在 Docker/数据库可用环境执行 Alembic upgrade 和 pgvector schema 验证。
2. 增加知识库导入 API/Worker，将 `KnowledgeIngestionService` 接入 Redis Streams。
3. 使用测试文本完成端到端导入，验证 `processing → active/failed` 和 chunk 检索。
4. 将文本 `ILIKE` 基线替换为 pgvector + PostgreSQL FTS 混合检索。

### 关键文件

- `app/infrastructure/qwen.py`
- `app/services/knowledge_ingestion.py`
- `app/repositories/knowledge.py`
- `docs/PLAN.md`
- `docs/TESTS.md`
- `docs/ISSUES.md`
## 2026-07-29 P0 Docker/数据库真实联调恢复

### 本次验证

- Docker Desktop Service 已恢复运行。
- `docker compose -f docker-compose.p0.yml ps` 显示 PostgreSQL 和 Redis 均为 healthy。
- PostgreSQL 连接验证通过：PostgreSQL 16.14。
- pgvector 扩展验证通过：0.8.5。
- 知识库四张表已存在：`knowledge_base`、`knowledge_document`、`knowledge_document_version`、`knowledge_chunk`。
- Alembic `upgrade head` 执行通过。
- 默认知识库创建/读取验证通过。

### 当前状态

```text
P0：Docker、PostgreSQL、pgvector、Redis 真实联调通过
P1：知识库数据库基线已验证
下一步：接入知识库 Redis 导入 Worker，并完成真实文档导入和检索验证
```
## 2026-07-29 P1 知识库 Redis 导入 Worker 接入

### 本次完成

- 文档创建事务现在同时写入 `knowledge_document` 和 `outbox_event`。
- 新增 `POST /api/v1/knowledge/documents/{document_id}/ingest`，支持手动重新排队导入。
- 新增 `workers/knowledge_worker.py`，消费 `stream:wealth:ingestion`。
- Outbox Publisher 已将 `knowledge.*` 事件路由到 ingestion Stream。
- 导入 Worker 调用 `KnowledgeIngestionService`，执行文本解析、切块、Embedding、chunk 入库和版本激活。
- 本轮已完成 Python 编译验证。

### 当前阶段状态

```text
P0：Docker、PostgreSQL、pgvector、Redis 联调通过
P1：知识库导入事件、API 和 Worker 接入完成
P1：真实 DashScope Embedding 端到端验证待执行
P1：混合检索（FTS + pgvector）待实现
```

### 下一步计划

1. 启动 FastAPI、Outbox Publisher 和 Knowledge Worker。
2. 创建测试文档，验证事件进入 ingestion Stream。
3. 验证文档状态 `processing → active` 或 `processing → failed`。
4. 有效 API Key 时执行真实 Qwen Embedding；无 Key 时记录可解释失败。
5. 实现 PostgreSQL FTS + pgvector 混合检索。
## 2026-07-29 使用仓库现有金融文档进行导入联调

### 本次验证

- 使用现有文件 `金融政策/个人投资者适当性管理指南.md`，未新建测试文档。
- 文档创建成功，状态为 `processing`。
- Outbox Publisher 成功发布 `knowledge.document_created` 事件。
- Redis ingestion Stream 成功收到消息。
- 手动执行导入服务时，Qwen Embedding 请求失败：`APIConnectionError: Connection error.`
- 文档和版本均正确回写为 `failed`，错误信息已保存，chunk 数量为 0。

### 当前状态

```text
文档发现：完成
Outbox → Redis ingestion：通过
Worker/导入服务：已执行到 Embedding 阶段
DashScope 真实 Embedding：连接失败，待排查网络/API 配置
```

### 下一步计划

1. 检查 DashScope endpoint 的网络可达性和 API Key 有效性。
2. 修复连接后重新触发该文档导入，不新建重复文档。
3. 验证 `processing → active`、chunk 入库和搜索结果。
## 2026-07-29 DashScope Embedding 配置修复与现有文档导入成功

### 根因

本次确认 API Key 可用。实际问题有两个：

1. 当前 PowerShell 设置了 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY=http://127.0.0.1:9`，导致 OpenAI 客户端默认走失效代理。
2. `text-embedding-v4` 不在当前 DashScope OpenAI-compatible `/models` 返回的可用 Embedding 模型列表中；当前可用模型为 `qwen3.7-text-embedding`。

### 修复

- Qwen 客户端使用 `httpx.AsyncClient(trust_env=False)`，不读取失效代理环境变量。
- 默认 Embedding 模型调整为 `qwen3.7-text-embedding`。
- Embedding 请求按单文本调用，导入服务按 4 个 chunk 一批调度，避免 DashScope 对多输入请求偶发断连。

### 真实导入结果

使用现有文件：

```text
金融政策/个人投资者适当性管理指南.md
```

验证结果：

```text
document_status = active
version_status = active
chunk_count = 12
embedding_dimension = 1024
```

### 下一步

1. 将现有导入 Worker 以独立进程方式运行，验证 Outbox→Redis→Worker 全链路。
2. 为搜索接口增加 pgvector 语义检索，并保留 FTS/文本检索降级。
3. 增加重复导入幂等和版本复用，避免相同内容重复生成版本。
## 2026-07-29 P1 pgvector + PostgreSQL FTS 混合检索完成

### 本次完成

- `KnowledgeRepository` 新增 `search_hybrid`。
- 语义得分使用 pgvector cosine similarity。
- 词法得分使用 PostgreSQL `to_tsvector('simple')` + `plainto_tsquery('simple')`。
- 综合得分：

```text
0.65 × semantic_score + 0.35 × lexical_score
```

- `/api/v1/knowledge/search` 已优先使用混合检索。
- Embedding 服务异常时保留 `ILIKE` 文本检索降级。
- Qwen HTTP 客户端增加显式连接/请求超时配置。

### 真实验证

使用已导入文档 `金融政策/个人投资者适当性管理指南.md`，查询：

```text
投资者适当性管理
```

返回 3 条结果，最高得分约 `0.495311`，检索命中相关章节。

### 当前状态

```text
P1：知识库导入、Embedding、pgvector + FTS 混合检索已完成第一版
下一步：启动 FastAPI 做 API 层真实搜索验证，并补充重复导入幂等
```
## 2026-07-29 FastAPI 启动命令修正

### 问题

在 `app` 子目录执行：

```powershell
uvicorn main:app --host 127.0.0.1 --port 8010 --reload
```

`main.py` 内部使用绝对导入 `from app.api import api_router`，但当前工作目录是 `app`，项目根目录不在 `sys.path`，因此报：

```text
ModuleNotFoundError: No module named 'app'
```

### 正确启动方式

必须在项目根目录执行：

```powershell
cd "C:\Users\tx360\Desktop\金融 - 副本"
uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

或在 `app` 目录执行：

```powershell
uvicorn main:app --app-dir .. --host 127.0.0.1 --port 8010 --reload
```

推荐第一种方式，和项目包结构及 Worker 启动方式一致。
## 2026-07-29 全链路日志与请求监控第一版

### 验证结论

用户提供的启动日志表明 FastAPI 已成功启动，`POST /api/v1/knowledge/search` 返回 `200 OK`，Qwen Embedding 请求最终返回 `200 OK`，因此搜索链路本次调用成功。

### 本次增强

- Trace Middleware 现在记录请求完成/失败、HTTP 方法、路径、状态码和耗时。
- 日志统一带 `trace`、`component`、`method`、`path` 字段。
- Qwen Embedding 增加开始/完成日志，记录模型、文本数量和向量维度。
- 保留现有 `X-Trace-Id` 请求头透传和响应头返回。

### 当前能力

```text
HTTP 请求 → Trace ID → API 日志 → Embedding 日志 → Repository/数据库日志（待补充）
```

### 下一步

1. 给数据库、Redis、Outbox Publisher、Knowledge Worker 增加统一结构化日志。
2. 增加请求级成功/失败指标和外部调用耗时。
3. 如需类似 simple_rag 的可视化观测，再接入 Prometheus/Grafana 或 OpenTelemetry；当前先完成统一日志链路。
## 2026-07-29 基础日志监控链路补齐

### 范围决策

本阶段先完成应用内基础日志监控，不引入 Prometheus/Grafana。Prometheus、Grafana、OpenTelemetry 等监控中心方案延期到 P5，避免提前扩大 P1-P4 的基础设施范围。

### 本次完成

- HTTP Middleware：记录请求完成/失败、状态码、耗时、方法、路径和 Trace ID。
- Qwen Embedding：记录调用开始、完成、模型、文本数量、向量维度。
- Redis Streams：记录 publish、consume、claim、ack。
- Outbox Publisher：记录每轮选取事件数量和发布数量。
- Knowledge Worker：记录文档导入开始、完成、chunk 数量和耗时。
- Database：记录连接资源释放。
- Worker 进程使用统一 `configure_logging`，不再使用独立的 `basicConfig` 格式。

### 基础日志字段

```text
trace
component
method
path
event
status
duration_ms
业务对象 ID（document_id/event_id/message_id/task_id）
```

### 当前状态

```text
基础日志监控：已完成第一版
Prometheus/Grafana/OTel：延期至 P5
下一步：启动 API、Outbox 和 Knowledge Worker，验证日志可以串起完整导入链路
```
## 2026-07-29 基础日志模块循环导入修复

### 问题

FastAPI 启动时报：

```text
ImportError: cannot import name 'get_trace_id' from partially initialized module 'app.common.middleware.trace'
```

原因是：

```text
trace.py → app.common.logging → logging.config → trace.py
```

日志配置模块反向依赖 Trace Middleware，形成循环导入。

### 修复

- `app/common/logging/config.py` 不再导入 `get_trace_id`。
- 日志 Trace ID 统一从 `ContextVar` 日志上下文读取。
- `TraceIdMiddleware` 负责写入 `trace_id`，日志配置只负责读取，不再反向依赖 Middleware。

### 验证

```text
python -m py_compile ...：通过
import app.main：通过
```

下一步：重新启动 FastAPI，验证 HTTP 日志、Embedding 日志和 Trace ID。
## 2026-07-29 基础日志真实 HTTP 验证与缺陷修复

### 发现的问题

基础日志模块修复循环导入后，独立启动 API 做真实验证时发现 `config.py` 缺少 `get_log_context` 导入，健康检查请求会因日志过滤器抛出 `NameError` 而返回 500。

### 修复

- 在 `app/common/logging/config.py` 补回 `get_log_context` 的直接导入。
- 保持日志模块不依赖 Trace Middleware，避免恢复循环导入。

### 真实验证结果

- `GET /api/v1/health`：200
- `POST /api/v1/knowledge/search`：200
- 两个请求均返回 `X-Trace-Id`。
- 日志已串起：

```text
Trace ID → HTTP → Qwen Embedding → HTTP completed
```

- 搜索请求耗时约 18178 ms；Qwen 请求发生一次 SDK retry 后返回 200。

### 当前状态

```text
基础日志监控：HTTP + Trace + Qwen 已通过真实验证
Redis/Outbox/Knowledge Worker 日志：代码已补齐，待三进程联调
Prometheus/Grafana/OTel：P5
```

### 下一步

启动 Outbox Publisher 和 Knowledge Worker，使用现有金融文档重新排队一次导入，验证 Redis、Outbox、Worker 日志串联。
## 2026-07-29 Outbox + Knowledge Worker 全链路日志联调

### 本次完成

- 使用现有文档 `金融政策/个人投资者适当性管理指南.md` 重新排队导入。
- Outbox Publisher 独立进程启动并成功发布事件到 `stream:wealth:ingestion`。
- Knowledge Worker 独立进程成功消费、执行 Embedding、保存 chunk 并 ACK。
- Qwen Embedding 过程中发生重试，但最终 12 个 chunk 全部生成 1024 维向量。
- 文档最终状态为 `active`。

### 日志链路验证

```text
Outbox publish
→ Redis consume
→ knowledge_ingestion_started
→ embedding_started/completed
→ knowledge_ingestion_completed
→ Redis ACK
```

关键结果：

```text
outbox_publish_cycle selected=1 published=1
knowledge_ingestion_completed status=active chunks=12 duration_ms=43427.16
redis_ack result=1
```

### 发现与修复

- Worker 环境下 DashScope 偶发连接失败，OpenAI SDK 自带重试不足以覆盖所有情况。
- `QwenProvider.embed` 增加每个文本最多 3 次应用层重试，并记录 `embedding_retry`。

### 当前状态

```text
基础日志监控：HTTP、Qwen、Redis、Outbox、Knowledge Worker 已完成真实链路验证
P1 知识库导入：通过
Prometheus/Grafana/OTel：延期到 P5
```

### 下一步

进入 P2：客户画像、产品目录和只读业务查询；同时保留 P1 检索链路作为客服问答基础。
## 2026-07-29 用户画像分层设计讨论结论

### 讨论结论

用户画像采用分层、可追溯设计，不把所有内容混成单一画像 JSON。整体演进顺序为：

```text
结构化数据提取
→ 主观/客观来源区分
→ 接入真实资产和业务事实
→ 保存原始行为
→ 计算行为特征
→ 生成综合画像快照
```

### 数据分类

1. 基础身份信息：客户姓名、职业、地区等相对稳定信息。
2. 用户主观信息：投资目标、可接受损失、投资期限、流动性偏好等用户声明。
3. 客观业务事实：账户资产、余额、持仓、交易、风险测评等系统或业务系统数据。
4. 行为数据：交易频率、平均持仓周期、风险产品购买比例、咨询主题等原始事件和统计特征。
5. 系统计算结果：风险暴露、主客观一致性、流动性压力、推荐置信度等分析结果。

### 来源与可追溯要求

每条画像信息需要保留：

```text
source_type
source_text
source_system
source_record_id
verified
confirmed
confidence
collected_at
snapshot_time
expires_at
```

用户自述不能直接覆盖系统事实；真实资产、持仓和交易采用带时间的事实快照，不保存为永久静态字段。

### 推荐数据模块

第一版建议包含：

```text
customer
customer_subjective_profile
customer_asset_snapshot
customer_holding
customer_transaction
customer_behavior_feature
customer_profile_snapshot
profile_extraction_record
```

### AI 使用边界

AI 负责从对话或文本中提取候选主观信息、归纳偏好、发现主客观冲突和生成解释；AI 不直接决定真实资产、账户余额、真实持仓、最终风险等级或是否允许交易。候选信息应经过用户确认或确定性规则后再进入有效画像。

### 实施顺序

1. 先实现结构化字段提取和原文保存。
2. 再分离用户声明、系统验证和系统推导。
3. 接入真实资产、持仓和交易快照。
4. 保存原始行为并计算带时间窗口的行为特征。
5. 最后生成带理由和版本的综合画像快照。
## 2026-07-29 前端技术栈切换记录

### 本次决策

前端切换为：

```text
Vue 3 + Vite + Axios
```

后续前端工作重点：

1. 整理 Vue 3 页面和组件结构。
2. 创建 Axios 实例，统一配置 API Base URL、Trace ID 和错误处理。
3. 对接 FastAPI 健康检查、知识库、文档导入和搜索接口。
4. 保持前后端通过 HTTP API 解耦。

详细技术决策见 `docs/DECISIONS.md` 的 `DECISION-20260729-002`。
## 2026-07-29 Vue 3 + Axios 前端第一版完成

### 本次完成

- 将原 Streamlit 占位页面替换为 Vue 3 + Vite + TypeScript。
- 新增 Axios HTTP 客户端，统一 API Base URL、超时、Trace ID 和错误处理。
- 新增首页状态概览，调用 `/api/v1/health`。
- 新增知识库检索页面，调用 `/api/v1/knowledge/search`。
- 新增 Vue Router 和基础响应式深色 UI。

### 当前状态

```text
前端框架：Vue 3 + Vite + TypeScript
HTTP 客户端：Axios
已对接：health、knowledge search
待对接：文档管理、导入触发、客户画像页面
```

### 下一步

1. 安装依赖并执行前端 build。
2. 启动 FastAPI 和 Vite，验证健康检查和知识库检索页面。
3. 在后端用户画像模型确定后，再实现客户画像前端页面。
## 2026-07-29 用户画像启动前的资料清单

前端第一版完成后，开始用户画像前需要确认或准备以下资料。当前没有这些资料时，先使用脱敏测试数据，不阻塞数据模型设计。

### 必需资料

1. 客户基础信息样例：客户编号、年龄、职业、地区、客户类型。
2. 用户主观信息样例：投资目标、投资期限、可接受亏损、流动性偏好、偏好产品。
3. 风险测评问卷和评分规则：题目、选项、分值、风险等级映射。
4. 产品目录样例：产品类型、风险等级、期限、最低金额、流动性、产品说明。
5. 产品适配规则：客户风险等级、投资者类型、资产门槛与产品风险的匹配规则。
6. 资产/持仓样例：账户资产、可投资资产、负债、产品持仓、市值、成本、收益。
7. 交易行为样例：交易时间、买卖方向、金额、产品、持仓周期、渠道。

### 可选资料

- 历史客服对话或问答数据；
- 用户对推荐结果的接受/拒绝反馈；
- 产品浏览、收藏、搜索和点击记录；
- 脱敏后的真实资产或账户快照；
- 需要抽取的画像字段清单及业务口径。

### 下载/提供后用途

这些资料将用于设计：

```text
结构化字段提取
主观信息表
客观资产快照
持仓与交易表
行为特征表
综合画像快照
```
## 2026-07-29 前端可用性确认与构造数据决策

### 前端状态

- `frontend` 已包含 Vue 3 + Vite + TypeScript + Axios 源码。
- `node_modules` 已存在。
- 已生成 `frontend/dist`。
- `npm run build` 已成功：Vite 8.1.5，81 modules transformed。

因此前端已经可以使用：

```powershell
cd frontend
npm run dev
```

默认访问：

```text
http://127.0.0.1:5173
```

Vite 开发服务器通过 `/api` 代理到 `http://127.0.0.1:8010`。

### 命令说明

- `npm install`：根据 `package.json` 安装前端依赖。
- `npm run dev`：启动 Vite 开发服务器，支持热更新。
- `npm run build`：构建生产静态文件到 `frontend/dist`。
- `npm run preview`：预览已经构建好的 `dist`。

### 用户数据决策

现阶段用户画像、客户资产、持仓、交易和行为数据允许使用构造/脱敏测试数据，不要求等待真实业务数据。构造数据必须标记来源，例如：

```text
source_type = synthetic
```

并与未来的 `user_declared`、`system_verified`、`observed_behavior` 数据区分。

### 下一步

1. 启动后端 `uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload`。
2. 启动前端 `npm run dev`。
3. 验证首页健康状态和知识库检索页面。
4. 使用构造数据开始用户画像第一版设计和接口实现。
## 2026-07-29 前端设计技能使用说明

本次前端实现使用了项目可用的 `frontend-design` 技能规范，按照 Vue 3 页面场景完成：

- 明确金融财富管理产品定位；
- 使用深色金融工作台视觉方向；
- 建立字体、颜色、间距、圆角和状态色等基础设计 token；
- 使用响应式 Grid/Flex 布局；
- 加入 hover、focus、loading、error 和移动端适配；
- 通过 Vue 组件和 Axios API 保持页面与后端解耦。

前端构建已通过 `npm run build`。
## 2026-07-29 多角色前台/后台与鉴权需求纳入计划

### 新增范围

系统后续不仅是知识库和内部服务，还需要：

```text
客户前台
工作人员后台
超级管理员后台
产品展示
资产/持仓查看
交易申请与 Mock 执行
RBAC 权限
Access Token + Refresh Token
审计日志
```

### 建议实施顺序

```text
P2-A：用户、角色、权限、Refresh Session、登录/刷新/注销
P2-B：客户前台和内部后台基础壳
P2-C：产品、客户、资产、持仓和订单 CRUD
P2-D：适当性校验和 Mock 交易闭环
P2-E：工作人员工作台和超级管理员管理
P2-F：审计、风控预警和数据隔离增强
```

鉴权和权限应早于交易和资产接口实现，避免先写出无权限边界的业务 API。

### 当前建议

用户画像继续使用构造数据，但构造数据必须带 `source_type=synthetic`。真实资产暂不接入外部系统，先实现账户、资产快照、持仓、订单和 Mock 交易模型。
## 2026-07-29 P2-A 鉴权基础第一版

### 本次完成

- 新增 `User`、`Role`、`Permission`、`RefreshSession` 模型。
- 新增登录、刷新、注销和当前用户接口：

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/me
```

- Access Token 使用 JWT，默认有效期 30 分钟。
- Refresh Token 使用随机不透明字符串，服务端只保存 SHA-256 哈希。
- Refresh Token 刷新时执行轮换：旧会话撤销，创建新 Refresh Session。
- 新增 `current_user` 和 `require_permission` 基础依赖。
- 前端 Axios 已加入 Access Token 注入和 401 后单次 Refresh Token 刷新重放机制。

### 当前限制

- 当前密码哈希为开发阶段 SHA-256，占位实现；正式环境必须替换为 Argon2id 或 bcrypt。
- 默认管理员和角色种子数据尚未创建。
- 尚未将客户、知识库和任务接口逐一接入权限依赖。
- Refresh Token 当前通过 JSON 请求体传递；浏览器正式方案应切换为 HttpOnly Secure Cookie。

### 下一步

1. 增加鉴权种子数据和开发管理员账号。
2. 执行登录/刷新/注销真实 API 验证。
3. 为客户、产品、文档、资产和交易接口增加 RBAC 权限。
4. 将开发密码哈希替换为 Argon2id/bcrypt。
5. 再实现客户前台和工作人员后台的登录态。
## 2026-07-29 密码加盐与开发测试账号方案

### 本次确认

- 正式密码存储必须使用带盐密码哈希；Argon2id 优先，bcrypt 兼容。
- 开发环境为每类角色提供构造测试账号。
- 前端开发模式显示测试账号和密码，方便联调。
- 测试账号展示必须受环境开关控制，生产环境关闭。
- 测试账号只访问构造数据，不接触真实资产和交易。

### 下一步

1. 增加 Argon2id/bcrypt 密码哈希依赖和迁移逻辑。
2. 增加开发角色、权限和测试账号种子。
3. 增加 `/auth/demo-accounts` 开发接口或在登录页注入演示账号配置。
4. 前端增加仅开发环境显示的测试账号面板。
5. 完成登录、刷新、注销和角色切换验证。
## 2026-07-29 测试账号凭据文件方案确认

### 本次确认

测试账号不在前端显示，改为放入本地开发凭据文件：

```text
docs/DEMO_ACCOUNTS.txt
```

前端继续使用正常登录页面，不增加演示账号专用代码。仓库提供：

```text
docs/DEMO_ACCOUNTS.example.txt
```

真实 `DEMO_ACCOUNTS.txt` 加入 `.gitignore`。
## 2026-07-29 P2-A 鉴权真实联调与启动问题修复

### 启动问题

首次启动时，认证种子给 SQLAlchemy 异步关系赋值触发 `MissingGreenlet`。原因是异步 ORM 的 `roles/permissions` 关系发生了隐式懒加载。

### 修复

- 种子逻辑改为显式刷新关系后清空/追加，避免隐式懒加载。
- 登录和刷新查询使用 `selectinload` 显式加载用户角色。
- 当前用户依赖使用 `selectinload` 预加载角色和权限。
- 默认 JWT 开发密钥长度调整为满足 HS256 推荐长度，生产环境仍必须通过环境变量配置随机密钥。

### 真实验证

```text
auth seed ok
login = bearer，Access Token 和 Refresh Token 均生成
refresh = 成功，Refresh Token 已轮换
logout = 成功，旧 Refresh Session 已撤销
```

### 下一步

1. 用 FastAPI HTTP 入口重复验证登录、刷新、注销和 `/auth/me`。
2. 为业务 API 增加 RBAC 权限依赖。
3. 完成前端登录页和 Axios 登录态管理。
## 2026-07-29 P2-A 业务 RBAC 接入与前端登录页

### 本次完成

- 任务接口接入权限：创建任务需要 `order:write`，查询任务需要 `order:read`。
- 知识库默认库和搜索需要 `product:read`。
- 文档创建和手动导入需要 `product:write`。
- 前端新增 `/login` 登录页。
- 前端 Axios 自动注入 Access Token，401 时自动轮换 Refresh Token 并重放原请求。
- Vue Router 对首页和知识库页增加登录保护。
- 前端登录成功后将 Token 保存在 sessionStorage；当前仍是开发阶段方案。

### 验证

```text
RBAC API compile：通过
npm run build：通过
Vue modules transformed：85
```

### 下一步

1. 用 FastAPI HTTP 入口验证登录、`/auth/me`、Refresh、Logout。
2. 将客户、资产、订单等 P2 接口逐一接入 RBAC。
3. 将 Refresh Token 从 JSON Body 迁移到 HttpOnly Cookie。
4. 开始客户前台和工作人员后台基础布局。
## 2026-07-29 P2-A 客户/员工注册接口

### 本次完成

新增：

```http
POST /api/v1/auth/register
```

注册请求使用：

```text
register_type = customer | employee
username
password
display_name
company_code（员工必填）
```

规则：

- 客户注册不需要公司码；
- 员工注册必须匹配后端 `EMPLOYEE_REGISTRATION_CODE`；
- 客户注册后绑定 `customer`；
- 员工注册后绑定 `employee_pending`；
- 员工暂不自动加入客服、产品、风控或运营分组；
- `employee_pending` 不拥有业务管理权限；
- 生产环境禁止启用演示账号。

### 当前状态

```text
注册枚举：完成
公司码校验：完成
客户初始角色：完成
员工待分组角色：完成
管理员分组后台：后续实现
```
## 2026-07-29 P2-A 注册与鉴权 HTTP 全链路验证完成

### 本次验证

通过临时 FastAPI `8011` 服务完成真实 HTTP 联调：

```text
客户注册：201
错误公司码员工注册：400
正确公司码员工注册：201
客户登录：200
/auth/me：200，角色为 customer
Refresh Token 轮换：200，旧 Token 与新 Token 不同
注销：200
```

日志中每个接口均有 Trace ID 和 HTTP 完成耗时。

### 修复问题

`/auth/me` 初次联调时发现 `selectinload` 使用字符串路径，SQLAlchemy 报：

```text
ArgumentError: Strings are not accepted for attribute names in loader options
```

已改为类属性路径：

```python
selectinload(User.roles).selectinload(Role.permissions)
```

### 当前状态

```text
客户注册：完成并验证
员工注册：完成并验证
公司码校验：完成并验证
登录/Me/刷新/注销：完成并验证
员工待分组：已暂定
下一步：接入前端登录页面和业务接口 RBAC 验证
```
## 2026-07-29 基础日志落盘实现

### 本次完成

- 统一日志现在同时输出到控制台和文件。
- 自动创建：

```text
logs/app.log
logs/error.log
```

- `app.log` 保存 INFO 及以上应用日志。
- `error.log` 只保存 ERROR 及以上日志。
- 使用 `TimedRotatingFileHandler` 按天轮转。
- 应用日志保留 14 天，错误日志保留 30 天。
- FastAPI、Outbox Publisher、Task Worker、Knowledge Worker 继续使用统一 `configure_logging`，因此均会落盘。
- `logs/` 已加入 `.gitignore`，运行日志不会提交到仓库。

### 当前日志链路

```text
控制台输出 + logs/app.log
错误日志   + logs/error.log
```

### 下一步

继续补充登录、注册和 RBAC 的业务审计事件；Prometheus/Grafana 仍延期到 P5。
## 2026-07-30 登录注册前端页面与登录态完成

### 本次完成

- 新增 Vue 注册页 `/register`。
- 注册类型使用客户/员工枚举切换。
- 员工注册时显示公司码字段，客户注册不显示。
- 登录页增加“去注册”入口。
- 主界面登录后显示当前用户名称和首个角色。
- 登录后不再显示登录入口，改为显示“退出登录”。
- 退出登录调用后端注销接口，清理 Access Token/Refresh Token 并返回登录页。
- 已登录用户访问 `/login` 或 `/register` 自动回到首页。
- 未登录用户访问受保护页面自动跳转登录页。
- 前端 Axios 保留 401 自动刷新和原请求重放。

### 验证

```text
npm run build：通过
Vite modules transformed：87
生产构建 dist：生成成功
```

### 当前状态

```text
登录页：完成
客户/员工注册页：完成
登录态显示：完成
退出登录：完成
受保护路由：完成
下一步：真实浏览器联调，并继续建设客户前台/工作人员后台布局
```
## 2026-07-30 注册错误码与登录态即时更新修复

### 问题与结论

员工填写错误公司码时返回 422，原因不是业务校验，而是 `RegisterRequest.company_code` 的 Pydantic `min_length=4` 在业务逻辑前拦截了过短输入。错误公司码属于业务错误，应返回 400，并由后端提示公司码无效。

### 修复

- 移除 `company_code` 的最小长度校验，只保留最大长度；
- 错误公司码进入 `AuthService.register` 后返回 400；
- 保留字段必填逻辑：员工注册前端仍要求填写公司码；
- 移除 Axios 重复响应拦截器，避免错误处理重复执行；
- 前端登录成功后通过全局登录状态事件立即加载 `/auth/me`，主界面无需手动刷新即可显示用户和角色；
- 退出登录后同步清理状态并跳转登录页。

### 当前状态

```text
错误公司码：业务错误 400
请求字段格式错误：422
登录后用户状态：即时显示
退出登录：即时清除
```
## 2026-07-30 登录后顶部状态未即时更新的修复

### 问题确认

前一版虽然保存了 Token，但 `App.vue` 只在首次挂载时调用 `loadMe`；登录页保存 Token 后直接跳转，App 没有收到状态变化通知，因此顶部用户信息可能需要手动刷新才显示。

### 修复

- 新增 `frontend/src/stores/auth.ts` 作为全局认证状态；
- 登录动作统一通过 `useAuth().login`，保存 Token 后立即调用 `/auth/me`；
- App 监听 `auth:changed` 事件，登录/刷新/退出后立即同步当前用户；
- 退出登录统一通过 `useAuth().logout` 清理 Token、用户和页面状态；
- Axios 401 刷新成功后发送 `auth:changed`；
- 前端删除重复 Axios 响应拦截器。

### 公司码错误码

员工错误公司码返回 422 的原因是 `company_code` 的 `min_length=4` 在业务校验前拦截。已移除最小长度约束，错误公司码进入业务层返回 400；真正的字段格式错误仍返回 422。

### 验证

```text
npm run build：通过
88 modules transformed
```

### 下一步

重新启动 Vite 后验证浏览器实际点击：登录后立即显示用户/角色、刷新页面保持登录、退出后立即显示登录入口。
## 2026-07-30 登录注册浏览器测试通过后的下一步

用户已确认客户登录、员工登录、注册、退出和登录态测试均通过。当前进入 P2-B：客户前台与工作人员后台基础壳。

### 本次补充

- 全局认证状态已集中到 `frontend/src/stores/auth.ts`。
- 页面启动时会先同步 `/auth/me`，避免已有 Token 时顶部状态闪烁或显示错误。
- 构建验证通过：88 modules transformed。

### 下一步范围

1. 建立客户前台布局：产品展示、产品详情、我的资产、持仓、订单、个人资料。
2. 建立工作人员后台布局：根据角色显示工作台入口。
3. 建立超级管理员后台入口占位。
4. 后端补充用户角色/权限查看接口，前端根据权限渲染菜单。
5. 再进入客户画像、资产、持仓和 Mock 交易页面。
## 2026-07-30 P2-B 客户前台与工作人员后台基础壳完成

### 本次完成

- 新增客户前台 `/customer`：产品展示、我的资产、我的订单、智能问答入口。
- 新增工作人员后台 `/staff`：根据当前角色显示对应工作台入口。
- `employee_pending` 显示“等待管理员分组”，不显示业务工作台。
- `super_admin` 显示用户管理、角色权限和系统配置入口占位。
- 新增模块占位页，为产品、资产、订单和工作人员模块预留路由。
- 顶部导航增加客户前台、工作人员后台和知识库入口。
- 页面继续使用 Vue 3、Axios、全局认证 Store 和角色信息。

### 当前状态

```text
鉴权：完成
客户前台基础壳：完成
工作人员后台基础壳：完成
员工待分组状态：完成
角色菜单：第一版完成
真实产品/资产/订单 API：待接入
```

### 下一步

1. 设计并实现用户画像第一版数据模型和构造数据。
2. 接入客户画像、资产快照、持仓和产品 CRUD。
3. 将后台菜单从角色静态映射升级为后端权限返回。
## 2026-07-30 前端空白页修复

### 问题

新增角色后台后，前端出现空白页。根因为 Vue 模板中对 `useAuth()` 返回的 ref 进行了显式 `.value` 访问；Vue 模板会自动解包 ref，导致模板表达式访问错误。

同时，未登录初始化时 `initialized` 没有及时设置为 `true`，可能导致顶部区域一直等待。

### 修复

- 将 App 模板中的 `auth.loggedIn.value`、`auth.me.value`、`auth.loading.value` 等改为模板自动解包形式；
- `loadMe()` 在无 Token 时也立即设置 `initialized=true`；
- 保留已登录用户启动时调用 `/auth/me` 的逻辑；
- 前端重新构建通过。

### 下一步

重启 Vite 并强制刷新浏览器，验证登录、客户前台、工作人员后台和登出页面。
## 2026-07-30 前端空白页二次修复

### 根因

第一次修复后仍残留两个问题：

1. `App.vue` 模板仍使用 `auth.me.value`，Vue 模板对 ref 会自动解包，导致运行时渲染错误；
2. `StaffDashboardView.vue` 同样使用 `auth.me.value`；
3. 占位路由生成函数把中文标题当成 URL path，客户前台卡片的 `/products`、`/assets`、`/orders` 没有对应路由。

### 修复

- 所有 Vue 模板改为 `auth.me`、`auth.me.roles`；
- 占位路由改为显式路径：`/products`、`/assets`、`/orders`；
- 前端构建重新通过。

### 下一步

重启 Vite 后清理浏览器缓存并验证 `/`、`/customer`、`/staff`、`/products` 等路径。
## 2026-07-30 公开前台与独立工作人员后台架构修正

### 问题确认

前一版把客户前台、工作人员后台和登录状态放在同一个顶部导航中，并把首页、知识库和客户前台设为必须登录。与业务目标不一致。

### 修正

- 公开前台：`/`、`/products`、`/knowledge` 可匿名访问；
- 登录后客户操作：`/customer`、`/assets`、`/orders` 等需要登录；
- 工作人员后台：`/staff` 单独作为后台入口，不再作为普通前台导航项；
- 顶部公开导航显示首页、产品展示、知识库；
- 登录后普通客户显示“我的服务”，工作人员显示“工作后台”；
- 未登录用户显示“登录”和“注册”；
- 已登录用户显示用户名称、角色和“退出登录”；
- 增加公开产品展示页，当前使用构造产品数据，后续替换为后端产品目录。

### 当前状态

```text
公开前台：已拆分
客户登录操作区：已拆分
工作人员后台入口：已拆分
后台独立布局：基础入口完成，完整后台壳待继续
```
## 2026-07-30 知识库页面定位修正

知识库检索页面只是当前 P1 检索链路和 Embedding 的测试页面，后续由 Agent 通过 Service/Tool 调用，不作为客户前台独立功能。

本次调整：

- `/knowledge` 改为登录保护；
- 顶部导航将其标记为“知识库测试”；
- 匿名用户不显示该入口；
- 后续 Agent 接入后再决定隐藏或删除页面。
## 2026-07-30 P2-C 客户画像/资产/持仓/产品后端第一版

### 本次完成

- 新增客户画像模型：`CustomerProfile`。
- 新增主观画像字段模型：`CustomerSubjectiveProfile`。
- 新增客观资产快照模型：`CustomerAssetSnapshot`。
- 新增产品模型：`Product`。
- 新增产品适配规则模型：`ProductSuitabilityRule`。
- 新增客户持仓模型：`CustomerHolding`。
- 启动时为 `customer_demo` 创建构造数据：
  - 客户画像；
  - 资产快照；
  - 稳健增值计划产品；
  - 一条构造持仓。
- 新增基础 API：

```http
GET /api/v1/profile/me
PUT /api/v1/profile/me
POST /api/v1/profile/me/assets
GET /api/v1/profile/products
GET /api/v1/profile/staff/customer/{user_id}
```

- 客户前台 `/customer` 已开始调用真实画像摘要 API，显示总资产、可投资资产、持仓数量、投资目标和持仓。
- 公开产品页 `/products` 已开始调用后端产品目录，不再使用页面内硬编码产品。

### 验证

```text
profile full compile ok
profile seed ok
npm run build：通过，97 modules transformed
```

### 当前限制

- 资产和持仓暂时只使用 `source_type=synthetic` 构造数据。
- 产品适配规则模型已建立，但匹配 Service 尚未实现。
- 客户画像主观字段模型已建立，尚未接入 LLM 抽取。
- Staff 页面仍是基础壳，尚未接入客户列表和画像查询。

### 下一步

1. 增加产品适配规则 CRUD 和确定性产品匹配接口。
2. 增加客户/员工权限范围校验。
3. 更新工作人员后台客户画像页面。
4. 增加构造客户、产品和持仓批量种子。
## 2026-07-30 公司信息/公司业务/金融政策/用户研判规则总结完成

### 本次完成

已读取并总结以下目录的全部现有文档：

```text
公司信息
公司业务
金融政策
用户研判规则
```

新增后续可复用总结：

```text
docs/BUSINESS_KNOWLEDGE_COMPANY.md
docs/BUSINESS_KNOWLEDGE_PRODUCTS.md
docs/BUSINESS_KNOWLEDGE_POLICIES.md
docs/BUSINESS_KNOWLEDGE_RISK.md
```

总结覆盖：

- 公司基本信息、牌照、组织和 FAQ；
- 个人理财、高净值服务、企业金融和产品目录；
- KYC、AML、适当性、销售、双录、冷静期、复评和投诉；
- 用户画像、构造客户、C1-C5/R1-R5、主客观分层、行为和可疑交易规则。

### 后续使用原则

```text
原始文档：事实和制度依据
总结文档：开发和 Agent 使用索引
结构化数据库：可执行业务事实
规则 Service：硬性校验和风险判断
LLM/Agent：提取、检索、解释，不绕过硬规则
```

涉及金额、期限、牌照、政策和保存期限时，必须回到原始文档的有效版本确认。

### 下一步

1. 按总结文档扩展 ProductSuitabilityRule 和 RiskRule。
2. 设计 CustomerProfileSnapshot 和产品推荐规则。
3. 将公司/产品知识接入 Agent Retriever，而不是直接硬编码在 Prompt。
## 2026-07-30 P2-C 产品适配规则与确定性推荐第一版

### 本次完成

- 新增 `RiskRule` 模型，保存规则编码、配置、风险级别、版本和来源文档。
- 完善 `ProductSuitabilityRule` 种子。
- 新增 `SuitabilityService`，根据客户资产和产品风险等级执行确定性匹配。
- 新增接口：

```http
GET /api/v1/profile/me/recommendations
```

- 客户前台新增产品匹配结果，区分：

```text
匹配产品
排除产品
排除原因
```

- 工作人员后台增加“风险与产品匹配”入口。
- 构造种子验证：`customer_demo` 当前得到 1 个匹配产品。

### 规则边界

Agent 后续只负责解释匹配原因；产品风险、资产门槛和适当性硬规则由后端 Service 执行。

### 当前限制

- 当前客户风险等级暂用基础 C2 占位，尚未接入风险测评结果字段。
- 规则还未覆盖投资期限、流动性、客户类型和双录/冷静期。
- 工作人员后台入口暂为页面壳，尚未接入客户列表和风控详情。

### 下一步

1. 接入风险测评模型和 C1-C5 计算。
2. 完善产品规则 CRUD 和版本。
3. 增加适配性拒绝、二次确认、双录/冷静期字段。
4. 接入工作人员客户画像查询。



## 2026-07-30 交易业务闭环启动

### 当前方向修正

当前主线调整为先完成客户前台交易、用户后台和各类员工后台管理闭环，用户画像与 Agent 暂缓。

### 本次完成

- 新增账户、订单、交易和订单状态历史模型。
- 新增交易 Service，支持创建订单、客户确认、客户取消、员工审核、拒绝和 Mock 执行。
- Mock 执行后更新账户冻结/可用余额和客户持仓。
- 新增客户交易 API、员工待审核订单 API。
- 新增客户订单页面、产品购买入口和员工订单审核页面。
- 为演示客户创建交易账户。

### 下一步

1. 验证客户创建订单、确认、员工审核、Mock 执行和持仓变化的真实 HTTP 全链路。
2. 补充订单详情、交易记录和客户账户页面。
3. 完善员工客户管理、产品管理、风控审核、运营和审计后台。
4. 修正交易权限和角色数据范围。
5. 业务闭环稳定后再进入用户画像和 Agent。


## 2026-07-30 虚拟用户交易闭环真实验证

### 本次完成

- 新增 `scripts/seed_demo_data.py`，可初始化或重置开发演示用户账户。
- 新增 `app/services/trading_seed.py`，为 `customer_demo` 创建/重置交易账户。
- 新增 `scripts/test_trading_flow.py`，执行客户下单、确认、运营审核、Mock 执行和数据核对。
- 已在本地 PostgreSQL/pgvector 上完成真实数据库验证。

### 验证结果

```text
order_status: executed
available_balance: 200000.00 -> 190000.00
holding_quantity: 100.0000 -> 10100.0000
trade: created
```

### 下一步

1. 启动 FastAPI，使用真实 Token 验证 HTTP 交易接口。
2. 验证拒绝、取消、余额不足和权限边界。
3. 将交易记录和账户页面接入客户前台。


## 2026-07-30 交易 HTTP 全链路验证完成

### 本次验证

已通过运行中的 API 完成：

```text
客户登录
→ 查询产品和账户
→ 创建订单
→ 确认订单
→ 运营查看待审核订单
→ 运营审核并 Mock 执行
→ 查询订单、账户和交易记录
```

同时验证了取消、拒绝访问、余额不足和跨用户订单访问边界。

### Seed 调整

`seed_demo_data.py` 现在会重置 `customer_demo` 的账户余额，并清理其演示订单、交易和状态历史，保证每轮 HTTP 测试从固定状态开始。

### 下一步

1. 测试员工后台页面的浏览器交互。
2. 补充订单详情、账户、持仓和交易记录页面。
3. 增加订单幂等和并发重复提交保护。
4. 继续完善员工角色后台管理。


## 2026-07-30 开发环境一键重置

新增：

```text
scripts/reset_dev_state.py
```

用于在本地开发环境清除 PostgreSQL 应用状态、Redis 缓存并重新导入演示数据。默认保留知识库；增加 `--include-knowledge` 时同时清理知识库文档和向量。脚本执行后已重新通过交易闭环测试。


## 2026-07-30 客户资产与订单详情第二步

### 本次完成

- 客户首页接入真实交易账户可用余额。
- 我的订单页接入交易记录和订单状态历史。
- 新增客户订单详情 API：`GET /api/v1/trading/orders/{order_id}`。
- 新增客户订单幂等键字段和重复请求复用逻辑。
- 演示 seed 会自动补齐订单幂等字段的数据库结构。

### 验证

- 开发环境重置和重新 seed：PASS。
- 交易闭环脚本：PASS。
- 账户余额：`200000.00 → 190000.00`。
- 订单状态：`executed`。

### 下一步

1. 重新启动 API 使订单详情和幂等字段代码生效。
2. 做真实 HTTP 的重复幂等请求验证。
3. 接入工作人员客户列表、订单详情和客户查询。
4. 再补账户/持仓独立页面和员工角色后台。


## 2026-07-30 权限 Seed 重复键修复

服务热重载期间发现权限初始化重复唯一键问题。已修复 `ensure_auth_seed` 的 Session flush 顺序，使权限 seed 可重复执行。当前 API 进程因启动失败已退出，需要重新启动后再继续验证订单详情和幂等接口。


## 2026-07-30 重启后订单详情与幂等验证完成

### 本次验证

- API 重启后健康检查通过。
- 权限 seed 重复键问题不再出现。
- 订单详情返回状态历史。
- 同一幂等键重复提交只创建一个订单。
- 订单确认、运营审核、Mock 执行和余额扣减通过。

### 修复

订单历史 ORM 对象未转换造成的 HTTP 500 已修复。

### 下一步

进入工作人员后台业务：先实现客户列表/客户详情和订单详情联动，再补产品管理、风控审核、运营处理和审计记录。


## 2026-07-30 工作人员客户管理第一版

### 本次完成

- 新增工作人员客户列表接口：`GET /api/v1/profile/staff/customers`。
- 支持用户名/姓名搜索和分页参数。
- 新增工作人员客户订单接口：`GET /api/v1/trading/orders/customer/{user_id}`。
- 新增客户管理前端页面：`/staff/customers`。
- 客户详情展示画像摘要、资产、持仓和订单。
- 增加 `require_staff_permission`，禁止客户角色调用工作人员客户管理接口。

### 当前验证

- 客服角色查询客户列表：PASS。
- 客户角色在旧运行进程中仍可查询客户列表，说明需要重启 API 载入新的 staff-only 依赖；源码编译已通过。

### 下一步

1. 重启 API 后重新验证客户角色 HTTP 403。
2. 验证工作人员客户详情和订单联动。
3. 接入客户管理页面浏览器操作。
4. 继续实现产品管理和风控/运营后台。


## 2026-07-30 客户管理依赖注入问题修复

真实 HTTP 验证发现工作人员客户接口因 `_user` 参数命名导致 FastAPI 将依赖误判为必填 query 参数，返回 422。已改为显式类型注解和 Depends 写法。需要重启 API 后继续验证客户列表、详情和订单接口。


## 2026-07-30 账户角色分层与顶部个人中心修正

### 问题确认

之前所有登录用户都可以通过通用交易 API 获取账户、创建订单和查看订单；员工后台入口也与客户前台入口混在顶部导航中，管理员/员工的入口层级不清晰。

### 本次修正

- 交易账户和客户订单 API 增加 customer 角色限制。
- 员工访问客户交易接口时返回 403。
- 顶部导航改为客户前台和员工后台分层。
- 右上角用户区域改为下拉个人中心菜单。
- 客户下拉菜单包含个人中心和我的订单。
- 员工下拉菜单包含工作后台、按权限显示订单审核和客户管理。
- 退出登录统一收纳到右上角下拉菜单。
- 管理员按员工工作台处理，不再被视为 customer。

### 当前限制

员工账号仍可能存在历史账户/订单数据，需要通过 reset seed 或数据迁移清理；本轮先从 API 权限和前端入口阻断继续产生员工交易数据。

### 下一步

1. 重启 API 验证所有员工交易接口均返回 403。
2. 调整 seed，确保仅 customer 创建交易账户。
3. 验证顶部下拉菜单的客户/员工/管理员入口。
4. 继续完善员工后台客户、产品、风控和运营模块。


## 2026-07-30 客户/员工数据 seed 分层修正

- `profile_seed` 仅为拥有 `customer` 角色的用户创建画像、资产、产品构造持仓。
- `trading_seed` 仅为拥有 `customer` 角色的用户创建或重置交易账户。
- 员工和管理员不会再由 seed 自动生成客户资产、持仓或交易账户。
- 已在开发环境清理后补充该保护，下一步进行真实 HTTP 角色边界验证。


## 2026-07-30 修复异步 Seed MissingGreenlet

API 启动失败原因是 seed 过程懒加载 `User.roles`。已在 profile/trading seed 中预加载角色关系，seed 命令验证通过。现在可以重新启动 API。


## 2026-07-30 客户前台与员工后台完全解耦修复

本轮将问题提升为 P0 架构修正：客户前台只提供公司/业务/产品介绍和客户购买、订单等客户能力；员工进入独立工作后台，不拥有客户账户、资产和交易能力。右上角用户菜单承载客户个人中心、我的订单、员工工作后台和退出登录。

同时修复 seed/Async ORM 的角色判断：避免访问懒加载 `User.roles`，并确保管理员 seed 显式写回 `is_super_admin`。待 API 重启后进行最终角色矩阵验证。


## 2026-07-30 角色矩阵验证与客户前台/员工后台解耦

角色数据已正确：customer、各员工角色和 super_admin 均能正确返回；员工交易账户接口已返回 403。工作人员客户列表仍因 FastAPI 依赖参数签名返回 422，已进一步调整为依赖参数置于 query 参数之前，待 reload 后完成复验。


## 2026-07-30 客户/员工独立后台入口第一版

### 本次完成

- 公开前台顶部只保留首页和产品展示。
- 客户后台统一入口：`/customer-center`，订单：`/customer-center/orders`。
- 员工后台统一入口：`/employee-workspace`，订单审核和客户管理均在该前缀下。
- 新增客户后台概览页和员工后台概览页。
- 右上角下拉菜单负责进入客户后台/员工后台和退出登录。
- 修正工作人员客户列表接口依赖签名，避免把认证依赖解析成 query 参数。

### 当前状态

前后端代码编译通过；需要 API reload 后验证 staff customer 接口不再返回 422，以及浏览器路由和下拉菜单。


## 2026-07-30 参考 C:\jiaoyu\backend 重构后台管理方向

### 方向确认

参考项目的后台采用独立管理后台、用户管理、角色/员工管理和按角色进入不同业务页面的模式，而不是把后台模块作为公开前台导航的一部分。

### 本次实现

- 新增管理员 API：用户搜索、角色列表、权限列表、用户角色修改。
- 新增管理员独立页面：`/admin-workspace`。
- 超级管理员右上角下拉增加“系统管理”。
- 修正员工客户列表接口依赖签名。
- 保留客户后台、员工后台、管理员后台三套独立入口。

### 下一步

1. API reload 后验证 staff customer 接口不再 422。
2. 验证超级管理员搜索用户、查看角色权限并修改角色。
3. 将管理员用户管理扩展为真实独立后台布局。
4. 继续建设客户、员工、产品、风控和审计后台模块。


## 2026-07-30 管理后台与 staff 依赖修复第二轮

- 新增独立 `staff_customer_user` 依赖，避免 FastAPI 将认证用户识别为 query 参数。
- 工作人员客户列表、客户详情、客户订单统一使用该依赖。
- 新增超级管理员用户搜索、角色列表、权限列表和角色修改 API/页面。
- 当前 API 因 reload/进程已停止，需要重新启动后验证。


## 2026-07-30 管理后台统一侧边栏壳与管理员 ORM 修复

### 本次完成

- 管理员用户列表查询增加 `selectinload(User.roles)`，角色列表增加 `selectinload(Role.permissions)`，角色修改查询预加载角色，避免 Async ORM `MissingGreenlet`。
- 新增统一 `/backoffice` 管理后台壳。
- 后台采用左侧侧边栏、右侧内容区结构。
- 根据当前用户权限动态显示客户管理、订单审核、产品管理、风险管理、审计查询和用户权限模块。
- `/admin-workspace` 和 `/employee-workspace` 兼容重定向到统一后台。
- 公开前台不再作为后台内容渲染，后台内部只显示工作模块。

### 下一步

1. 重启前端/API，验证管理员用户搜索和角色修改。
2. 验证统一侧边栏按不同权限显示模块。
3. 将产品管理、风险管理、审计查询逐一接入右侧内容区。


## 2026-07-30 后台统一工作台体验修正

- 统一工作后台内的客户管理、订单审核、产品、风控、审计和系统管理模块，不再在右上角下拉菜单重复展示子模块。
- 右上角下拉菜单只保留进入统一后台、客户进入客户后台和退出登录。
- 后台顶部显示当前登录用户、角色和审计提示。
- 后台页面增加明确的退出登录按钮。
- 后台侧边栏继续根据权限动态展示模块。


## 2026-07-30 客户订单查询修复与前端视觉重构

- 保留并确认 `GET /api/v1/trading/orders/me` 客户订单接口；当前 404 需要检查运行 API 是否加载了最新 trading router。
- 客户后台继续使用独立 `/customer-center` 页面，不复用管理后台。
- 公开前台和客户页面从压抑的深色风格改为明亮、轻盈、蓝紫渐变和卡片式视觉。
- 管理后台保留稳重浅色工作台风格，独立于公开前台。


## 2026-07-30 客户后台侧边栏化与下拉菜单稳定性修正

- 新增独立客户后台视图 `CustomerBackofficeView`。
- 客户后台采用左侧菜单、右侧内容区，不显示首页、产品展示等公开前台导航。
- 客户后台模块：账户概览、我的订单、资产与持仓、个人资料。
- 右上角账号下拉菜单增加明确的层级和显示状态，避免只在 hover 时出现。
- 公开前台与客户后台使用不同的视觉容器。


## 2026-07-30 客户后台对齐员工后台与下拉激活态修复

- 客户后台路由统一隐藏公开前台顶部栏，包括 `/customer-center` 及其子路由。
- 客户后台增加与员工后台一致的侧边栏品牌区、菜单区、右侧内容区和退出登录。
- 客户右上角下拉菜单为个人中心/我的订单增加独立激活态样式，避免当前路由链接文字与背景同色而看不见。
- 后台路由判断改为前缀匹配，覆盖所有客户和员工后台子页面。


## 2026-07-30 客户后台直接复用员工后台壳修正

- 客户后台改为直接复用员工后台的 `backoffice-root/backoffice-topbar/backoffice-layout/backoffice-sidebar/backoffice-content` 结构和样式。
- 客户后台固定为 250px 左侧栏和右侧内容区，不再维护另一套易冲突布局。
- 公开顶部栏恢复显示，因此客户在个人中心仍可使用右上角账号下拉菜单。
- `/customer-center/orders` 重定向回统一客户后台，订单通过左侧“我的订单”在右侧区域显示。
- 下拉菜单补充 RouterLink 默认激活类样式，当前项保持可见。


## 2026-07-31 按参考项目重构客户端与管理端后台 UI

### 参考模式

参考 `C:\jiaoyu\backend\templates\base.html` 和 `static/css/style.css`：

- 整页 `flex` 后台容器；
- 240px 固定侧边栏；
- 侧边栏展示平台、当前用户和角色；
- 返回前台和退出登录均放在侧边栏；
- 菜单激活项使用高对比主色背景；
- 右侧为独立浅色内容区域。

### 本次完成

- 员工/管理员后台重写为统一 `portal-shell`。
- 客户后台使用同一套结构，仅菜单和业务内容不同。
- 后台路由隐藏公开前台顶部栏。
- 员工后台菜单按权限动态显示。
- 客户后台菜单固定展示看板、个人信息、订单、资产持仓。
- 前端生产构建通过，104 modules transformed。


## 2026-07-31 前台导航与后台间距微调

- 参考后台模板的侧边栏增加内边距和用户区留白，避免内容贴边。
- 后台主内容区和顶部标题增加顶部空间。
- 公开前台产品按钮增加间距。
- 移除公开首页“查看知识库”按钮；知识库仅保留在员工工作后台使用。
- 首页文案调整为公开公司/业务/产品浏览，客户操作进入客户后台。


## 2026-07-31 前台信息架构与后台空状态清理

- 公开首页改为公司品牌与业务定位文案，不再解释登录/后台操作流程。
- 产品页改为产品中心文案，不再使用“公开浏览”“登录后操作”等测试性说明。
- 管理后台数据看板移除“请从左侧选择模块”等说明性欢迎卡，仅保留真实统计信息区域。
- 客户后台同样移除欢迎说明卡，侧边栏和具体业务模块承担导航职责。
- `C:\jiaoyu\backend` 参考后台继续使用侧边栏、当前用户、角色、返回前台和退出登录结构。


## 2026-07-31 管理员用户管理第一阶段

### 本次完成

- 验证超级管理员可以查询用户列表。
- 管理员用户列表角色关系使用 `selectinload`，避免异步懒加载错误。
- 增加用户状态启用/禁用 API。
- 禁止管理员禁用自己的账号。
- 管理员后台用户列表增加账号状态操作。

### 下一步

1. 重启 API 后验证用户状态修改。
2. 验证修改员工角色后权限生效。
3. 增加角色权限矩阵编辑。
4. 增加操作审计记录。
5. 进入员工订单审核后台。


## 2026-07-31 管理员权限矩阵管理

### 本次完成

- 新增角色权限更新接口：`PUT /api/v1/admin/roles/{role_id}/permissions`。
- 管理员页面增加角色权限列表和编辑权限复选框。
- 超级管理员角色权限固定，不允许被普通权限矩阵覆盖。
- 管理员用户管理继续支持用户搜索、角色修改、账号启用/禁用。
- 前端构建通过。

### 下一步

1. API reload 后验证角色权限修改。
2. 重新登录被修改角色账号，验证后台菜单即时变化。
3. 增加权限变更审计记录。
4. 进入员工订单审核后台的筛选、详情和审核意见功能。


## 2026-07-31 管理员用户/角色表格化与分页

### 本次完成

- 管理员用户管理由卡片列表改为表格列表，参考 `C:\jiaoyu\backend\templates\users.html`。
- 修改角色改为居中弹窗，不再在页面底部展开。
- 编辑角色权限改为居中弹窗，不再为每条角色单独展开权限区域。
- 用户列表支持关键词搜索。
- 用户列表支持每页 10/20/50/100 条切换。
- 支持输入页码跳转、上一页、下一页。
- 后端用户接口支持 `limit` 和 `offset` 分页参数。

### 下一步

1. API reload 后验证用户分页和角色权限保存。
2. 验证权限变化后员工重新登录的侧边栏联动。
3. 增加管理员操作审计。
4. 进入员工订单审核表格和筛选。


## 2026-07-31 管理员用户与权限页面收敛

- 按需求移除管理员页面底部独立“角色权限”区域。
- 用户与权限页面现在只保留用户表格、分页、状态操作和修改角色弹窗。
- 角色权限编辑 API 保留在后端，后续如需要应从统一角色管理入口进入，不在用户列表下方重复展示。
- 前端构建通过。


## 2026-07-31 后台模块表格化与通用分页

### 本次完成

- 新增通用 `PaginationBar` 组件：页码、上一页、下一页、输入页码跳转、每页条数。
- 新增通用 `AdminTableShell` 组件基础壳。
- 客户管理改为表格展示，支持搜索、分页、详情弹窗和订单表格。
- 订单审核改为表格展示，支持分页和审核操作。
- 管理员用户/权限页面继续使用表格、弹窗和分页。
- 后端客户列表和待审核订单接口接入 `limit/offset`。
- 客户个人空间保留同样的表格化订单区域。

### 下一步

1. 修正待审核订单接口返回 total，保证后端分页准确。
2. 将客户订单、交易记录和资产持仓全部统一为表格。
3. 增加通用筛选器和日期范围筛选。
4. 进入产品管理后台表格化实现。


## 2026-07-31 订单与客户模块统一表格化第二阶段

- 待审核订单接口返回 `{items,total}`，支持准确分页。
- 客户管理继续使用统一表格、搜索和分页组件。
- 订单审核使用统一表格、分页和操作按钮。
- 客户订单和交易记录改为表格展示。
- 客户详情弹窗内订单使用表格展示。
- `PaginationBar` 作为后台模块通用分页组件。

### 下一步

1. API reload 后验证订单分页返回 total。
2. 增加订单状态、日期和产品筛选。
3. 统一资产与持仓表格。
4. 进入产品管理后台。

## 2026-07-31 重启后真实 HTTP 验收与测试策略补充

### 本次完成

- 完成健康检查、认证、角色权限、管理员分页、客户交易和订单审核的真实 HTTP 验收。
- 验证客户账号禁用后无法登录，恢复后可用。
- 验证订单幂等：同一 `idempotency_key` 重复创建返回同一订单。
- 修复待审核订单分页参数缺少边界约束导致的异常风险。
- Python 编译通过。

### 测试策略结论

压力、并发和异常注入不需要每次功能测试都启动；应分为三层：

1. 每次改动后运行快速 Smoke/回归测试：健康、登录、权限、分页、交易主链路。
2. 每日或合并前运行并发测试：认证、只读列表、幂等下单，控制规模，不对生产或共享环境运行。
3. 发布前运行异常注入和稳定性测试：数据库/Redis不可用、超时、重复消息、进程重启、长时间 soak。

建议新增独立脚本：`tests/test_http_smoke.py`、`tests/test_http_concurrency.py`、`tests/test_http_faults.py`，并通过参数控制测试强度。

### 下一步

1. 在本地补齐可重复执行的 HTTP Smoke 脚本。
2. 增加受控并发测试，优先验证登录、列表查询和幂等下单。
3. 增加异常注入测试，但必须使用开发环境和可恢复的 Docker 服务。
4. 浏览器逐项验收完成后，再进入产品管理后台。

## 2026-07-31 HTTP Smoke 回归脚本完成

### 本次完成

- 新增 `tests/test_http_smoke.py`，将本轮真实 HTTP 验收固化为可重复执行的快速回归测试。
- 覆盖健康、认证、权限、分页、订单幂等和审核主流程。
- 默认使用演示账号，支持通过 `--accounts` 指定本地凭据文件。
- 当前重启后的 API 执行结果：PASS。

### 执行命令

```powershell
C:\Users\tx360\.conda\envs\langchain_env\python.exe tests/test_http_smoke.py
```

### 下一步

1. 每次 API 重启或业务代码修改后先运行 Smoke 测试。
2. 增加受控 HTTP 并发测试，重点验证只读接口和幂等下单。
3. 增加开发环境故障注入测试。
4. Smoke/并发/异常测试稳定后进入产品管理后台。

## 2026-07-31 HTTP 并发测试与幂等竞态修复

### 本次完成

- 新增 `tests/test_http_concurrency.py`。
- 覆盖只读接口、管理员分页和同一幂等键并发下单。
- 发现并修复并发幂等下单竞态，避免重复订单和 500。
- 增加唯一部分索引和历史重复数据清理。
- 并发测试通过：5 并发、每场景 10 请求、失败 0。

### 下一步

1. 增加开发环境异常注入测试。
2. 复跑前端构建和浏览器验收。
3. 完成并发 10/25 档位测试后，进入产品管理后台。

## 2026-07-31 并发脚本命令与语法修复

- 修复 `tests/test_http_concurrency.py` 中残留字面量换行导致的 SyntaxError。
- 确认 PowerShell 正确执行方式必须写完整脚本路径，不能执行 `python tests/`。
- 当前环境复测通过。

## 2026-07-31 开发环境异常注入与恢复测试

### 本次完成

- 新增 `tests/test_http_faults.py`，覆盖非破坏性异常输入、鉴权边界和恢复检查。
- 发现并修复非法金额校验错误导致的 500。
- 统一异常响应使用 `jsonable_encoder`，非法参数返回 422。
- 异常测试 12 项全部通过。

### 测试边界

当前脚本不自动停止 PostgreSQL/Redis，避免误伤开发环境数据。数据库/Redis 进程级故障注入需要单独确认后执行，并且必须在 Docker 开发环境进行。

### 下一步

1. 重启 API 使最新异常处理和交易代码生效。
2. 重启后运行 Smoke、并发、异常三套测试。
3. 确认三套测试均通过后，进入产品管理后台。

## 2026-07-31 三套稳定性测试通过并进入产品管理后台

### 验收结论

用户在重启后的 `langchain_env` 中完成以下三套测试：

- `python tests/test_http_smoke.py`：PASS。
- `python tests/test_http_concurrency.py --concurrency 5 --requests 10`：PASS，30 请求失败 0，median 26.49ms，P95 106.48ms，最大 110.78ms。
- `python tests/test_http_faults.py`：PASS，12 项失败 0。

核心 API、权限、交易幂等、并发、异常输入和恢复检查均无阻塞问题，可以进入下一阶段。

### 产品管理后台第一阶段

- 新增产品管理查询接口：`GET /api/v1/profile/staff/products`，支持搜索、状态筛选、分页。
- 新增产品创建接口：`POST /api/v1/profile/staff/products`。
- 新增产品编辑接口：`PUT /api/v1/profile/staff/products/{product_id}`。
- 接口统一要求 `product:write` 权限。
- 新增前端 `StaffProductsView.vue`，接入统一后台侧边栏，支持产品表格、搜索、状态筛选、分页、新增和编辑弹窗。
- 已验证产品经理账号可以访问产品管理接口。
- 后端 Python 编译通过。
- 前端构建在当前沙箱再次受到 Vite `spawn EPERM` 限制，需用户本机终端复跑。

### 下一步

1. 用户重启 API 后运行产品管理接口 Smoke 验证。
2. 本机终端运行 `npm run build`，确认前端编译。
3. 浏览器使用产品经理账号验证产品管理表格、新增、编辑和筛选。
4. 再进入产品状态审计、适当性规则和风险管理后台。

## 2026-07-31 用户角色筛选与产品编辑 UI 修正

### 本次完成

- 管理员用户接口增加 `role` 查询参数，支持按角色筛选用户。
- 用户与权限前端增加角色下拉筛选，选择角色后自动刷新列表。
- 产品管理新增/编辑弹窗增加显式 `showForm` 状态，修复“新增产品”点击后表单不显示的问题。
- 产品编辑弹窗改为清晰的双列表单布局，长描述单独占整行，增加关闭按钮和统一编辑器样式。
- 产品管理 API 和用户角色筛选接口验证返回 200。
- Smoke 测试通过。

### 待用户验证

1. 重启 API 后进入用户与权限，切换角色筛选。
2. 进入产品管理，点击新增产品，填写并保存。
3. 编辑已有产品，确认弹窗布局和保存结果。
4. 在本机终端运行前端 `npm run build`，并进行浏览器点击验证。

## 2026-07-31 用户角色筛选根因修复

- 定位到后端 `list_users` 虽接收 `role` 参数，但未将其加入 SQL 查询。
- 已增加角色关系过滤条件。
- 验证客户和产品经理角色筛选均返回正确结果。
- Smoke 和 Python 编译通过。

### 用户操作

API reload 后刷新浏览器；如果前端仍显示旧列表，执行一次 Ctrl+F5。角色下拉选择后会自动刷新，也可以再点击搜索。

## 2026-07-31 软删除与数据恢复第一阶段

### 本次完成

- 用户模型增加 `deleted_at`，产品模型增加 `deleted_at`。
- 数据库启动兼容增加 `users.deleted_at` 和 `product.deleted_at`。
- 用户列表增加 `status=active|deleted|all` 筛选，默认只看正常用户。
- 用户增加软删除和恢复接口：
  - `DELETE /api/v1/admin/users/{user_id}`
  - `PUT /api/v1/admin/users/{user_id}/restore`
- 产品增加软删除和恢复接口：
  - `DELETE /api/v1/profile/staff/products/{product_id}`
  - `PUT /api/v1/profile/staff/products/{product_id}/restore`
- 正常产品查询默认排除已删除产品。
- 管理员前端增加正常/已删除/全部筛选以及删除/恢复 API 客户端封装。

### 验证

- 用户删除接口：PASS。
- 用户恢复接口：PASS。
- Smoke：PASS。
- Python 编译：PASS。

### 待完成

- 产品管理页面增加删除/恢复按钮及回收站筛选。
- 管理员页面删除操作增加确认弹窗，避免误操作。
- 软删除审计记录和永久删除策略需要后续补充。

## 2026-07-31 软删除回收站前端与恢复验证完成

### 本次完成

- 用户权限页面支持正常/已删除/全部用户筛选。
- 用户删除和恢复增加确认/操作流程。
- 产品管理页面支持正常产品/回收站/全部产品筛选。
- 产品支持软删除、恢复，回收站中提供恢复按钮。
- 产品删除后正常列表隐藏，回收站列表可见，恢复后重新回到正常列表。
- Smoke 测试和 Python 编译通过。

### 验证结果

- 产品软删除接口：200。
- 删除后正常列表不包含产品：PASS。
- 回收站列表包含产品：PASS。
- 产品恢复接口：200。
- 用户软删除/恢复接口此前已验证通过。

### 后续

- 增加操作审计记录。
- 评估永久删除策略和保留期限。
- 浏览器逐项验证回收站筛选、确认弹窗和恢复按钮。

## 2026-07-31 回收站入口与审计查询第一阶段

### 本次完成

- 后台侧边栏已接入审计查询视图 `AuditLogsView`。
- 新增 `audit_logs` 模型，记录软删除和恢复操作。
- 用户删除/恢复写入审计记录。
- 产品删除/恢复写入审计记录。
- 新增 `GET /api/v1/admin/audit-logs`，支持按操作和对象类型筛选。
- 用户权限页面保留正常/已删除/全部筛选，产品管理页面保留正常/回收站/全部筛选。

### 注意

新增模型和路由需要 API 完整重启后才会加载；当前运行中的旧 Uvicorn 进程未能稳定响应，重启后再验证审计接口。

### 下一步

1. 重启 API。
2. 进入统一后台侧边栏的“审计查询”。
3. 在产品管理选择“回收站”，在用户权限选择“已删除”。
4. 删除/恢复一次后刷新审计查询，确认出现 `soft_delete`/`restore` 记录。

## 2026-07-31 API 启动重复持仓修复与审计接口验证

- 修复 `profile_seed` 因历史重复 `CustomerHolding` 导致的 `MultipleResultsFound`。
- 开发环境重置成功。
- Smoke 测试通过。
- API 重启后审计查询接口返回正常：`items=[]`、`total=0`。
- 后续删除/恢复用户或产品后，审计查询会显示对应 `soft_delete` 和 `restore` 记录。

## 2026-07-31 独立超级管理员数据回收站

### 本次完成

- 新增独立回收站接口：`GET /api/v1/admin/recycle-bin?module=user|product`。
- 回收站接口使用 `admin:write` 权限，并通过超级管理员后台菜单展示；普通工作人员不会看到该入口。
- 新增前端 `RecycleBinView.vue`，统一展示用户/产品已删除数据。
- 回收站通过下拉列表切换模块：用户数据、产品数据。
- 用户和产品均提供“恢复数据”操作。
- 审计查询入口继续保留在统一后台，可查看删除/恢复记录。
- 回收站接口返回 200，Smoke 和 Python 编译通过。

### 下一步

1. API reload 后用超级管理员登录，确认侧边栏出现“数据回收站”。
2. 删除一个用户或产品后，在回收站下拉切换对应模块并恢复。
3. 普通工作人员登录确认看不到回收站入口且接口返回 403。
4. 审计查询确认出现对应 `soft_delete`/`restore` 记录。
