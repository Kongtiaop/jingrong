# ISSUE 记录

> 文档状态：当前问题、讨论和解决记录  
> 更新日期：2026-07-28

## 1. 使用规则

每次发现问题、讨论技术方案、修复 Bug 或确认风险，都记录在本文件。

每条记录包含：

- 问题编号
- 发现日期
- 分类
- 严重程度
- 问题描述
- 讨论过程
- 最终结论
- 处理方式
- 验证结果
- 关联文件
- 当前状态

状态统一使用：

```text
OPEN
IN_PROGRESS
RESOLVED
WONT_FIX
DEFERRED
```

## 2. 当前记录

### ISSUE-0001：P0 文档与测试记录机制需要统一

- 发现日期：2026-07-28
- 分类：流程 / 文档
- 严重程度：P0
- 状态：RESOLVED

#### 问题描述

项目每次实现后需要同步记录计划、测试、问题讨论和解决结果，否则后续成员容易不知道当前方案、测试范围和已知限制。

#### 讨论过程

负责人提出：

1. 每次做完之后更新 `PLAN.md`。
2. 有测试时，写入独立的测试 Markdown 文档。
3. 发现的问题、讨论过程、结果和 Bug 修复记录写入 `ISSUES.md`。

#### 最终结论

采用以下文档规则：

```text
docs/PLAN.md       # 总计划和每次阶段进度
docs/TESTS.md      # 测试策略、测试记录和结果
docs/ISSUES.md     # 问题、讨论、Bug、解决和延期事项
docs/P0-*.md       # 阶段实施说明
```

#### 处理方式

已创建本文件，并创建 `docs/TESTS.md`。从本次 P0 之后，每次实现结束必须更新：

1. `docs/PLAN.md`
2. `docs/TESTS.md`（如果有测试或验证）
3. `docs/ISSUES.md`（如果发现问题或产生技术决策）
4. 阶段专项文档（必要时）

#### 验证结果

文档规则已写入 `docs/README.md`，后续以该规则执行。

### ISSUE-0002：P0 真实联调中的配置文件与端口隔离

- 发现日期：2026-07-28
- 分类：环境 / 配置
- 严重程度：P1
- 状态：RESOLVED

#### 问题描述

当前 `simple_rag` 已占用 Redis 6379，不能让本项目 P0 复用该端口，否则两套项目会发生数据和 Stream 名称混用。

#### 讨论过程

确认采用独立 Compose：

```text
PostgreSQL：5433
Redis：6380
```

FastAPI 本地运行时连接宿主机端口；未来容器化时改为 Compose 服务名。

#### 处理方式

使用：

```text
docker-compose.p0.yml
.env.p0.example
```

并为本项目使用独立容器、Volume 和网络：

```text
wealth-postgres
wealth-redis
wealth_pgdata
wealth_redisdata
wealth-p0
```

#### 验证结果

P0 Compose 启动成功，PostgreSQL 和 Redis 均为 healthy，健康检查与任务闭环通过。

### ISSUE-0003：任务直接写 Redis 会导致数据库与队列不一致

- 发现日期：2026-07-28
- 分类：架构 / 可靠性
- 严重程度：P0
- 状态：IN_PROGRESS

#### 问题描述

早期 `TaskService` 在提交 PostgreSQL 任务后直接执行 Redis `XADD`。如果 PostgreSQL 提交成功但 Redis 发布失败，会出现任务表存在但队列没有消息的问题。

#### 讨论过程

确认使用 Outbox 模式：

```text
TaskService
  └── PostgreSQL 事务：async_task + outbox_event

Outbox Worker
  └── 查询 pending outbox_event
  └── 发布 Redis Streams
  └── 更新 outbox_event 状态
```

#### 当前处理

已将 TaskService 改为同时写入：

```text
async_task
outbox_event
```

已增加 Worker 侧的失败状态、Pending 恢复和 Dead Letter 路径。

#### 尚未完成

独立 `outbox_worker.py` 尚未实现，因此 ISSUE 当前仍为 `IN_PROGRESS`。

#### 解决标准

- TaskService 不再直接依赖 Redis 发布成功。
- PostgreSQL 事务成功后，Outbox Worker 最终一定可以补发。
- 发布成功后 Outbox 状态为 `published`。
- 重复发布不造成业务重复执行。

### ISSUE-0003：任务直接写 Redis 会导致数据库与队列不一致

- 当前状态：RESOLVED

#### 解决结果

已完成：

- TaskService 只在 PostgreSQL 事务中写入 `async_task + outbox_event`
- `workers/outbox_worker.py` 发布 pending Outbox
- 发布成功更新 `outbox_event=published`
- Task Worker 消费 Redis Streams 并 ACK
- 真实测试验证 `queued → published → success`

#### 未覆盖

以下异常注入测试仍待后续完善，但不阻塞 P0 闭环：

- Redis 发布失败重试
- Pending 消息超时 XCLAIM
- Dead Letter 真实注入

## 2026-07-28 ISSUE-0004：8012 端口已被 P0 服务占用

- 分类：环境 / 启动
- 严重程度：P1
- 状态：RESOLVED

### 问题描述

用户执行：

```powershell
uvicorn main:app --host 127.0.0.1 --port 8012 --reload
```

出现：

```text
[WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试
```

同时发现：

```text
127.0.0.1:8012 LISTENING PID 40352
```

### 原因

8012 已经被之前启动的本项目 P0 FastAPI 进程占用，并不是 PostgreSQL、Redis 或 simple_rag 冲突。

当前 8012 服务健康检查正常：

```text
GET http://127.0.0.1:8012/api/v1/health
status=ok
```

### 处理方式

当前服务可以直接访问：

```text
http://127.0.0.1:8012/docs
http://127.0.0.1:8012/api/v1/health
```

如果需要重启，先查 PID，再结束对应进程，然后重新启动：

```powershell
netstat -ano | Select-String ':8012'
Stop-Process -Id 40352
uvicorn app.main:app --host 127.0.0.1 --port 8012 --reload
```

如果不想停止旧服务，改用其他端口：

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8013 --reload
```

### 结论

端口问题已定位。8012 当前服务可用，不需要重复启动同一端口的服务。

### ISSUE-0005：应用未自动读取 P0 数据库连接配置

- 发现日期：2026-07-28
- 分类：环境 / 配置
- 严重程度：P1
- 状态：RESOLVED

#### 问题描述

FastAPI 启动时出现：

```text
asyncpg.exceptions.InvalidPasswordError: 用户 "wealth" Password 认证失败
```

#### 原因

PostgreSQL 容器实际账号密码为：

```text
wealth / wealth_dev
```

但项目根目录 `.env` 原本为空，应用没有自动读取 `.env.p0.example`。因此如果用户没有在当前 PowerShell 会话手动设置 `DATABASE_URL`，应用使用了默认连接配置，导致密码不匹配。

#### 讨论与结论

当前 P0 Compose 与本地应用是两套运行边界：

```text
Compose：.env.p0.example
本地 FastAPI：.env
```

为了减少启动步骤，本地开发使用根目录 `.env`；`.env` 不提交真实密钥。

#### 处理方式

已创建并写入本地 `.env`：

```env
DATABASE_URL=postgresql+asyncpg://wealth:wealth_dev@127.0.0.1:5433/wealth_manager
REDIS_URL=redis://127.0.0.1:6380/0
```

并保留 `.env` 在 `.gitignore` 中。

#### 验证结果

使用修正后的配置启动 FastAPI 到 8013，应用启动成功并通过：

```text
GET /api/v1/health → 200 OK
PostgreSQL: ok
pgvector: ok
Redis: ok
```


### ISSUE-0006：simple_rag 已融合 pg_prefect 的边界判断

- 发现日期：2026-07-28
- 分类：架构 / 复用边界
- 严重程度：P1
- 状态：RESOLVED

#### 问题描述

需要确认 `C:/simple_rag` 是否已经完成 `C:/pg_prefect` 的融合，以决定当前项目是直接复用、复制还是重新实现 P1 RAG。

#### 检查结果

`simple_rag` 已经具备：

- PostgreSQL Metadata Repository 和 schema；
- 文档版本、Chunk Diff、增量 Embedding、软删除和恢复；
- Redis Worker、Qwen Embedding、Milvus Hybrid Search；
- 完整的 PLAN、ISSUES、TEST_RESULTS 和真实回归记录。

但其默认运行态仍是：

```text
SQLite Metadata + Milvus Vector Store + Redis List Worker
```

因此不能直接等同于当前项目目标：

```text
PostgreSQL 主库 + pgvector + Redis Streams + Outbox
```

#### 最终结论

采用适配层复用，不复制整个项目，也不把 simple_rag 的旧运行时直接搬入金融系统。

当前项目后续复用其成熟的：

```text
文档生命周期
Chunk Diff
增量 Embedding 思路
Qwen/切分/检索策略
测试结构
```

并重新实现：

```text
金融业务模型
PostgreSQL + pgvector Repository
Outbox + Redis Streams Worker
LangGraph Agent 编排
```

### ISSUE-0007：Agent 是否应直接互相通信、GraphRAG 检索顺序如何确定

- 发现日期：2026-07-29
- 分类：架构决策
- 严重程度：P1
- 状态：RESOLVED

#### 讨论问题

需要决定：

1. 领域 Agent 是互相直接沟通，还是各自完成任务后返回 Supervisor？
2. Neo4j GraphRAG 是先查图谱再查向量，还是先查向量再查图谱？

#### 讨论结果

采用：

```text
Supervisor 中心编排
领域 Agent 默认只做领域任务并返回 AgentResult
业务事件用于异步协作
Query Router 根据问题类型选择图/文顺序
```

#### 最终策略

```text
关系问题：图 → 文
文本/政策问题：文
复杂问题：图 + 文并行
推荐/风控：业务事实/规则 → 图 → 文
```

Agent 不允许直接调用其他 Agent 内部方法、共享未授权上下文或绕过确定性 Service。

### ISSUE-0008：Agent 范式过多导致架构复杂度膨胀

- 发现日期：2026-07-29
- 分类：架构 / 复杂度控制
- 严重程度：P1
- 状态：RESOLVED

#### 讨论问题

是否需要同时实现：

```text
ReAct
Plan-and-Execute
Reflexion
Self-Ask
```

#### 讨论结果

当前系统已经包含：

- Supervisor
- 五类领域 Agent
- LangChain Tool Calling
- LangGraph 状态图
- Check/Reflect 节点
- Redis Streams
- Outbox
- 记忆、风控和 GraphRAG 规划

如果再默认实现四套 Agent 范式，调试、Token、延迟和测试成本会明显增加。

#### 最终结论

采用最小充分集合：

```text
受控 ReAct + Supervisor 轻量拆分 + 有限并行 + 确定性 Service
```

Reflexion 和 Self-Ask 暂不进入主链路，Plan-and-Execute 不作为独立 Agent，只保留 Supervisor 的一次性轻量拆分能力。

### ISSUE-0009：公共组件接入边界

- 发现日期：2026-07-29
- 分类：架构 / 基础设施
- 严重程度：P1
- 状态：IN_PROGRESS

#### 问题描述

公共组件如果只创建不接入业务，就容易形成重复实现；但如果一次性接入所有业务，又会扩大 P0 范围。

#### 讨论结果

采用分批接入：

```text
第一批：响应、异常、Trace、日志上下文、分页模型、限流/幂等基础
第二批：实际 API 限流、写接口幂等、审计落库
第三批：P1 知识库和 RAG 业务接入
```

#### 当前处理

第一批公共组件已创建并接入 health/tasks API。限流、幂等和审计暂保留基础实现，待对应写接口出现后接入。

### ISSUE-0010：simple_rag 文档模型与当前金融项目模型的适配

- 发现日期：2026-07-29
- 分类：P1 / 数据模型
- 严重程度：P1
- 状态：IN_PROGRESS

#### 问题描述

`simple_rag` 已有成熟的文档生命周期，但其表名、字段和默认运行时面向通用 RAG，不能直接覆盖当前金融项目模型。

#### 当前结论

采用当前项目自己的 ORM 模型：

```text
knowledge_base
knowledge_document
knowledge_document_version
knowledge_chunk
```

适配复用：

```text
文档 Hash
版本状态
Chunk Diff
增量 Embedding
软删除/恢复
```

不直接复用：

```text
SQLite Metadata
Milvus 主存储
Redis List Worker
```

#### 当前处理

已完成模型和 Repository 基线；导入 Worker、迁移和检索仍未完成。

### ISSUE-0010：simple_rag 文档模型与当前金融项目模型的适配

- 当前状态：IN_PROGRESS
- 本次进展：
  - 已完成当前项目 KnowledgeBase/Document/Version/Chunk ORM
  - 已完成 Alembic 迁移文件
  - 已完成默认知识库和文档创建 API
  - 已完成基础文本搜索 API
- 尚未完成：pgvector/FTS 最终检索、导入 Worker、Qwen Embedding 和 simple_rag 生命周期逻辑适配
### ISSUE-0011：P1 文档导入链路尚未接入 Worker

- 发现日期：2026-07-29
- 分类：P1 / 文档导入
- 严重程度：P1
- 状态：IN_PROGRESS

#### 问题描述

文档 ORM、创建 API 和导入服务基线已经存在，但当前还没有把导入服务接入 Redis Streams；创建文档后不会自动执行解析、切块和 Embedding。

#### 当前处理

已完成 `KnowledgeIngestionService` 和 Qwen Embedding 客户端基线。下一步增加知识库专用事件生产和 Worker 消费，并补充真实 pgvector/Embedding 端到端测试。
### ISSUE-0012：Docker Engine named pipe 权限异常

- 发现日期：2026-07-29
- 分类：开发环境 / 基础设施
- 严重程度：P1
- 状态：RESOLVED

#### 问题描述

Docker Desktop 图形界面和进程已运行，但 `docker` CLI 无法访问 `dockerDesktopLinuxEngine` named pipe。

#### 原因与处理

`com.docker.service` 当时处于 `Stopped` 状态。启动 Docker Desktop Service 后，Docker Engine 访问恢复。

#### 验证结果

- `docker version` 能看到 Server/Engine 信息
- PostgreSQL 和 Redis 容器均 healthy
- PostgreSQL、pgvector、Alembic 和知识库表检查通过
### ISSUE-0013：知识库导入事件需要避免重复处理

- 发现日期：2026-07-29
- 分类：P1 / 异步一致性
- 严重程度：P1
- 状态：IN_PROGRESS

#### 问题描述

当前知识库导入事件已进入 Outbox 和 Redis Streams，但 Worker 的 ACK 语义仍是“处理异常后也 ACK”，重复投递和失败重试策略还需要进一步收敛。

#### 当前处理

本轮先完成主链路接入，确保异常会回写文档和版本的 `failed` 状态。下一轮补充导入幂等键、失败重试/Dead Letter 规则和端到端验证。
### ISSUE-0014：DashScope Embedding 连接失败

- 发现日期：2026-07-29
- 分类：P1 / 外部模型服务
- 严重程度：P1
- 状态：IN_PROGRESS

#### 问题描述

使用现有文件 `金融政策/个人投资者适当性管理指南.md` 进行真实导入时，文本读取、Outbox 发布和 Redis Stream 消费均正常，但调用 Qwen `text-embedding-v4` 时出现：

```text
APIConnectionError: Connection error.
```

#### 影响

文档被正确标记为 `failed`，尚未产生 chunk 和 active 版本。

#### 下一步

检查当前环境到 `dashscope.aliyuncs.com` 的网络、代理、证书和 API Key 配置；恢复后通过已有文档的重新导入接口重试。
### ISSUE-0014：DashScope Embedding 连接失败（已解决）

- 当前状态：RESOLVED
- 解决结果：确认 API Key 有效；根因为失效代理环境变量和旧 Embedding 模型配置。
- 处理方式：Qwen 客户端禁用环境代理，模型切换为 `qwen3.7-text-embedding`，并调整请求批量策略。
- 验证：现有金融政策文档成功导入，生成 12 个 1024 维 chunk，文档和版本均为 `active`。
### ISSUE-0015：监控中心方案范围控制

- 发现日期：2026-07-29
- 分类：架构 / 可观测性
- 严重程度：P2
- 状态：DEFERRED

#### 结论

当前先使用统一应用日志、Trace ID、耗时和组件事件字段完成基础监控。Prometheus、Grafana、OpenTelemetry 和集中式日志中心不进入当前 P1 主链路，统一延期到 P5。

#### 当前处理

已补齐 HTTP、Qwen、Redis、Outbox、Knowledge Worker 的基础日志。
### ISSUE-0016：基础日志模块与 Trace Middleware 循环导入

- 发现日期：2026-07-29
- 分类：Bug / 可观测性
- 严重程度：P1
- 状态：RESOLVED

#### 原因

`trace.py` 导入 `app.common.logging`，而 `logging/config.py` 又导入 `trace.get_trace_id`，导致 FastAPI 启动时模块处于 partially initialized 状态。

#### 处理

日志配置模块改为只读取日志上下文，不再依赖 Trace Middleware；Trace Middleware 负责写入 Trace ID。

#### 验证

`import app.main` 通过，相关模块编译通过。
### ISSUE-0017：日志过滤器缺少日志上下文导入

- 发现日期：2026-07-29
- 分类：Bug / 可观测性
- 严重程度：P1
- 状态：RESOLVED

#### 现象

独立启动 API 后，`GET /api/v1/health` 返回 500，日志过滤器报 `NameError: get_log_context is not defined`。

#### 修复与验证

在 `app/common/logging/config.py` 补充 `get_log_context` 导入；随后健康检查和知识库搜索 HTTP 验证均返回 200，Trace ID 正常返回。
### ISSUE-0018：Worker Embedding 偶发连接失败与重试策略

- 发现日期：2026-07-29
- 分类：P1 / 外部服务可靠性
- 严重程度：P1
- 状态：MITIGATED

#### 现象

Knowledge Worker 执行 Embedding 时偶发连接失败；本次最终通过 SDK 重试成功。

#### 处理

`QwenProvider.embed` 增加每个文本最多 3 次应用层重试，并记录 `embedding_retry` 日志。

#### 验证

现有金融文档成功导入 12 个 chunk，文档状态为 `active`，Redis 消息 ACK 成功。
### ISSUE-0019：前端依赖安装受 npm 离线缓存限制

- 发现日期：2026-07-29
- 分类：开发环境 / 前端
- 严重程度：P1
- 状态：IN_PROGRESS

#### 现象

在 `frontend` 执行 `npm install` 时，npm 使用 `only-if-cached` 模式，缺少 `@vitejs/plugin-vue` 缓存，报：

```text
ENOTCACHED
```

#### 影响

前端源代码和配置已经完成，但当前环境未能完成依赖安装和 `npm run build`。

#### 处理

需要在可访问 npm registry 的环境执行依赖安装和构建验证。
### ISSUE-0020：P2 鉴权第一版仍存在开发阶段安全占位

- 发现日期：2026-07-29
- 分类：P2 / 安全
- 严重程度：P1
- 状态：IN_PROGRESS

#### 当前占位

- 密码哈希暂用 SHA-256，未达到生产安全要求；
- Refresh Token 暂通过 JSON Body 传递，尚未使用 HttpOnly Cookie；
- 尚未创建默认角色/权限种子；
- 业务接口尚未全面接入 RBAC。

#### 处理计划

在完成基础登录链路验证后，替换为 Argon2id 或 bcrypt，增加刷新会话 Cookie 策略、种子数据和逐接口权限检查。
### ISSUE-0021：认证种子和登录关系懒加载触发 MissingGreenlet

- 发现日期：2026-07-29
- 分类：Bug / 异步 ORM
- 严重程度：P1
- 状态：RESOLVED

#### 原因

异步 SQLAlchemy 中访问未预加载的 `roles/permissions` 关系触发隐式 IO，导致 `MissingGreenlet`。

#### 修复

种子逻辑显式加载关系，认证查询使用 `selectinload`，避免异步上下文中的隐式懒加载。

#### 验证

认证种子、登录、Refresh Token 轮换和注销服务层联调全部通过。
### ISSUE-0022：前端登录态当前使用 sessionStorage

- 发现日期：2026-07-29
- 分类：P2 / 安全 / 前端
- 严重程度：P1
- 状态：IN_PROGRESS

#### 当前实现

Access Token 和 Refresh Token 暂存在 `sessionStorage`，用于开发阶段登录联调。

#### 后续处理

正式浏览器方案需要将 Refresh Token 改为 HttpOnly、Secure、SameSite Cookie，Access Token 也需要结合 XSS 防护和部署环境重新评估。
### ISSUE-0023：员工注册后的分组和审批暂未实现

- 发现日期：2026-07-29
- 分类：P2 / 权限
- 严重程度：P1
- 状态：DEFERRED

员工注册后暂绑定 `employee_pending`，不授予业务管理权限。后续由超级管理员后台完成员工审批、分组和角色绑定。
### ISSUE-0024：`/auth/me` selectinload 字符串路径错误

- 发现日期：2026-07-29
- 分类：Bug / 异步 ORM / 鉴权
- 严重程度：P1
- 状态：RESOLVED

#### 现象

真实 HTTP 调用 `/auth/me` 返回 500，SQLAlchemy 报 `ArgumentError`，因为 `selectinload` 使用了字符串属性名。

#### 修复

改为使用模型类属性：`selectinload(User.roles).selectinload(Role.permissions)`。

#### 验证

重新执行客户注册、员工注册、登录、`/auth/me`、Refresh Token 轮换和 Logout，全部通过。
### ISSUE-0025：日志此前仅输出控制台

- 发现日期：2026-07-29
- 分类：可观测性
- 严重程度：P1
- 状态：RESOLVED

#### 处理

统一日志配置新增控制台、`logs/app.log` 和 `logs/error.log` 三个 Handler，并按天轮转。日志目录加入 `.gitignore`。

#### 验证

日志目录和两个日志文件自动生成，日志内容成功写入。
### ISSUE-0026：登录态前端浏览器联调尚未完成

- 发现日期：2026-07-30
- 分类：P2 / 前端鉴权
- 严重程度：P1
- 状态：IN_PROGRESS

前端登录、注册、用户状态显示和退出逻辑已完成构建验证，但尚未通过真实浏览器逐项点击验证。下一步验证客户注册、员工公司码注册、登录、刷新页面、退出登录和受保护路由跳转。
### ISSUE-0027：错误公司码被字段校验返回 422

- 发现日期：2026-07-30
- 分类：Bug / 注册
- 严重程度：P1
- 状态：RESOLVED

错误公司码属于业务规则错误，不应因为输入长度不足而在 Pydantic 层返回 422。已移除公司码最小长度约束，交由业务层统一返回 400。

### ISSUE-0028：登录后顶部用户状态需要手动刷新

- 发现日期：2026-07-30
- 分类：Bug / 前端鉴权
- 严重程度：P1
- 状态：RESOLVED

登录成功后增加全局登录状态事件，App 立即加载 `/auth/me`，不再依赖手动刷新页面。
### ISSUE-0028：登录后顶部用户状态需要手动刷新（最终修复）

- 发现日期：2026-07-30
- 当前状态：RESOLVED
- 根因：登录页直接写入 sessionStorage，App 没有响应式认证状态。
- 修复：新增 `frontend/src/stores/auth.ts`，登录后立即请求 `/auth/me`，并通过 `auth:changed` 事件同步 App。
- 验证：前端构建通过。
### ISSUE-0029：已登录页面初始化期间需要等待用户状态

- 发现日期：2026-07-30
- 分类：前端 / 鉴权体验
- 严重程度：P2
- 状态：RESOLVED

全局认证 Store 增加 `initialized` 状态，页面启动时先完成 `/auth/me` 同步，避免刷新页面时短暂出现错误登录态或用户名称。
### ISSUE-0030：前台和后台当前使用角色静态菜单映射

- 发现日期：2026-07-30
- 分类：P2 / 权限 / 前端
- 严重程度：P2
- 状态：IN_PROGRESS

当前工作人员后台根据 `/auth/me` 返回的角色在前端映射菜单，后续需要改为基于后端返回的 permissions 动态生成菜单，并对每个 API 做后端权限校验。
### ISSUE-0031：角色后台改造后前端空白页

- 发现日期：2026-07-30
- 分类：Bug / 前端 Vue
- 严重程度：P1
- 状态：RESOLVED

Vue 模板错误使用组合式 API 返回 ref 的 `.value`，导致运行时页面渲染失败。已改为模板自动解包，并修复认证初始化状态。构建验证通过。
### ISSUE-0031：角色后台改造后前端空白页（二次根因）

- 当前状态：RESOLVED
- 补充原因：App 和 Staff 模板仍残留 `auth.me.value`；占位路由路径生成错误。
- 修复：改用 Vue 模板 ref 自动解包，并显式配置占位路由路径。
- 验证：前端构建通过。
### ISSUE-0032：前台、客户操作区和工作人员后台边界混合

- 发现日期：2026-07-30
- 分类：架构 / 前端路由
- 严重程度：P1
- 状态：RESOLVED

前一版将前台和后台放在同一导航，并强制首页登录。已调整为公开前台、登录后客户操作区和独立工作人员后台入口。

### ISSUE-0033：知识库匿名访问与后端权限策略需要统一

- 发现日期：2026-07-30
- 分类：架构 / 权限
- 严重程度：P1
- 状态：IN_PROGRESS

前端现在将知识库作为公开前台入口，但当前后端 `/knowledge/search` 仍要求 `product:read`。后续需要明确知识库哪些内容公开，哪些内容必须登录，并将后端权限拆分为公开检索和受保护检索。
### ISSUE-0034：P2 客户画像数据目前为构造数据

- 发现日期：2026-07-30
- 分类：P2 / 用户画像
- 严重程度：P2
- 状态：IN_PROGRESS

当前画像、资产快照、产品和持仓使用 `source_type=synthetic`，用于先完成业务模型和页面联调。后续需要补充主观信息确认、行为特征和真实资产接入边界。

### ISSUE-0035：产品适配规则和匹配 Service 尚未实现

- 发现日期：2026-07-30
- 分类：P2 / 产品匹配
- 严重程度：P1
- 状态：IN_PROGRESS

ProductSuitabilityRule 模型已建立，但还没有完成规则 CRUD、确定性匹配、排除原因和客户产品推荐接口。
### ISSUE-0036：业务知识需要原文版本和结构化规则双重维护

- 发现日期：2026-07-30
- 分类：知识库 / 业务规则
- 严重程度：P1
- 状态：IN_PROGRESS

已生成公司、产品、政策和风险四类总结文档，但总结不能替代原始制度。涉及金额、期限、牌照、合规要求和保存期限时，需要保留原文来源、版本和有效期，并最终落入可执行的 ProductSuitabilityRule/RiskRule。
### ISSUE-0037：产品推荐当前使用基础 C2 风险占位

- 发现日期：2026-07-30
- 分类：P2 / 适当性
- 严重程度：P1
- 状态：IN_PROGRESS

第一版 `SuitabilityService` 已完成基本风险等级和资产门槛判断，但客户风险等级暂时使用 C2 占位。下一步接入风险测评结果和综合画像快照，禁止在没有有效风险结果时给出强推荐。



### ISSUE-0038：当前开发主线应优先完成交易和后台业务闭环

- 发现日期：2026-07-30
- 分类：范围/计划
- 严重程度：P1
- 状态：IN_PROGRESS

当前阶段暂缓用户画像、复杂产品推荐和 Agent，优先完成客户前台交易、用户后台、员工订单处理、客户管理、产品管理、风控、运营和审计闭环。


### ISSUE-0039：开发热重载期间权限 seed 触发重复唯一键

- 发现日期：2026-07-30
- 分类：启动/并发初始化
- 严重程度：P1
- 状态：RESOLVED

开发服务重载时，`ensure_auth_seed` 在同一 Session 中先添加权限对象、随后查询下一个权限，SQLAlchemy autoflush 与重复启动竞争可能导致 `permissions.code` 唯一键冲突。已改为每次新建权限后立即 flush，并同步更新已有权限名称，保证重复 seed 可重入。

- 验证：`python -m scripts.seed_demo_data` PASS。


### ISSUE-0040：订单详情返回 ORM 历史对象导致 HTTP 500

- 发现日期：2026-07-30
- 分类：Bug / API 响应
- 严重程度：P1
- 状态：RESOLVED

订单详情响应将 SQLAlchemy `OrderStatusHistory` 对象直接传给 Pydantic，导致订单创建和订单详情接口返回 500。已改为使用 `OrderStatusHistoryResponse.model_validate(..., from_attributes=True)` 转换后再返回。

- 验证：重启后创建订单、订单详情、幂等重复提交和最终执行全部 PASS。


### ISSUE-0041：customer:read 权限不能单独代表工作人员身份

- 发现日期：2026-07-30
- 分类：权限/数据边界
- 严重程度：P1
- 状态：IN_PROGRESS

客户角色也拥有 `customer:read`，但该权限原本直接用于工作人员客户列表和客户详情接口，造成客户可以访问工作人员范围的客户查询 API。已新增 `require_staff_permission`，要求具备权限且角色不是 `customer`；需要 API 重启后完成真实 HTTP 复验。


### ISSUE-0042：工作人员客户列表依赖参数名 `_user` 导致 FastAPI 误认为查询参数

- 发现日期：2026-07-30
- 分类：Bug / API 依赖注入
- 严重程度：P1
- 状态：RESOLVED

工作人员客户列表、客户详情和客户订单接口使用 `_user` 参数但未使用 `Annotated`/明确 Depends 类型，FastAPI 将其识别为必填 query 参数 `user`，返回 422。已改为显式 `user: User = Depends(require_staff_permission(...))`。


### ISSUE-0043：客户与员工账户能力混用

- 发现日期：2026-07-30
- 分类：架构/权限/业务边界
- 严重程度：P0
- 状态：IN_PROGRESS

员工和管理员通过通用交易接口拥有账户、资产和订单能力，顶部导航也将客户服务与员工后台混在一起。已增加 customer 角色限制和统一右上角下拉入口；待重启后验证，并清理历史错误 seed 数据。


### ISSUE-0044：seed 为员工创建客户资产和交易账户

- 发现日期：2026-07-30
- 分类：数据初始化/业务边界
- 严重程度：P0
- 状态：RESOLVED

已为 `profile_seed` 和 `trading_seed` 增加 customer 角色判断。开发环境 reset 后，只有客户账号会获得构造画像、资产、持仓和交易账户。


### ISSUE-0045：异步 seed 访问 User.roles 触发 MissingGreenlet

- 发现日期：2026-07-30
- 分类：Bug / SQLAlchemy Async ORM
- 严重程度：P1
- 状态：RESOLVED

`profile_seed` 和 `trading_seed` 查询用户后直接访问懒加载的 `user.roles`，在异步启动上下文中触发 `sqlalchemy.exc.MissingGreenlet`，导致 API 无法启动。已使用 `selectinload(User.roles)` 预加载角色关系。

- 验证：`python -m scripts.seed_demo_data` PASS。


### ISSUE-0046：员工前台/后台耦合与 seed/权限初始化异步问题

- 发现日期：2026-07-30
- 分类：P0 / 架构 / 权限 / 启动
- 状态：IN_PROGRESS

已确认三类问题：

1. 员工和管理员曾被允许访问客户交易能力；
2. 顶部导航把客户前台和工作后台混在一起，个人中心入口不符合要求；
3. 员工客户列表依赖参数仍被 FastAPI 识别为 query 参数，且交易 Service 访问懒加载 roles 触发 MissingGreenlet。

已完成代码修复：客户交易角色限制、客户/员工下拉入口、seed 角色预加载、交易 Service 使用 SQL 查询判断 customer 角色、工作人员依赖使用 Annotated。待 API 重启后完成最终验证。


### ISSUE-0047：staff customer endpoint 仍因默认值被解析为 query 参数

- 发现日期：2026-07-30
- 分类：Bug / FastAPI 依赖注入
- 严重程度：P1
- 状态：RESOLVED

验证发现工作人员客户接口仍返回缺少 query 参数 `user` 的 422。原因是 `Annotated[User, Depends(...)]` 依赖放在带默认值的参数后并设置了默认值，FastAPI 仍产生错误参数签名。已将依赖参数放到普通 query 参数之前并移除默认值。


### ISSUE-0048：管理员无法搜索用户和修改角色权限

- 发现日期：2026-07-30
- 分类：P0 / 管理后台
- 状态：IN_PROGRESS

当前项目缺少独立管理员用户管理 API 和页面，导致超级管理员无法查找用户、查看角色/权限或修改角色。已新增管理员用户搜索、角色列表、权限列表和用户角色修改能力，待 API reload 后验证。


### ISSUE-0049：管理员用户列表访问角色关系触发 MissingGreenlet

- 发现日期：2026-07-30
- 分类：Bug / Async ORM / 管理后台
- 严重程度：P1
- 状态：RESOLVED

管理员用户列表在序列化 `u.roles` 时触发异步懒加载，返回 500。已在查询中使用 `selectinload(User.roles)`；角色列表同步预加载 `Role.permissions`。

### ISSUE-0050：后台需要统一侧边栏而不是多个跳转页面

- 发现日期：2026-07-30
- 分类：架构 / 前端
- 严重程度：P0
- 状态：IN_PROGRESS

后台改为统一 `/backoffice` 页面：左侧根据权限显示模块，右侧显示选中模块内容；公开前台只保留公司、业务、产品和购买相关内容。


### ISSUE-0051：前端直连 API 触发 CORS preflight 405

- 发现日期：2026-07-30
- 分类：Bug / 前后端联调
- 严重程度：P1
- 状态：RESOLVED

前端 Axios 被改为直连 `http://127.0.0.1:8010`，浏览器对带 Authorization/X-Trace-Id 的请求发送 OPTIONS 预检，而后端未配置 CORS，导致 `/api/v1/health` 等接口返回 405。已同时配置 FastAPI CORSMiddleware，并恢复前端优先使用 Vite `/api` 代理。


### ISSUE-0052：`/orders/me` 被动态订单详情路由抢先匹配

- 发现日期：2026-07-30
- 分类：Bug / FastAPI 路由顺序
- 严重程度：P1
- 状态：RESOLVED

`GET /trading/orders/{order_id}` 注册在 `GET /trading/orders/me` 之前，Starlette 按路由注册顺序匹配时将 `me` 当成 `order_id`，随后订单归属查询返回 `order not found`（404）。已将静态路由 `/orders/me` 移到动态路由 `/orders/{order_id}` 之前。


### ISSUE-0053：超级管理员订单审核接口误返回 403

- 发现日期：2026-07-30
- 分类：Bug / 权限 / FastAPI 依赖注入
- 严重程度：P1
- 状态：RESOLVED

`pending_orders` 使用 `_user` 作为依赖参数名，当前 FastAPI 版本可能将其解析为普通 query 参数或导致权限依赖行为不符合预期。已改为明确的 `user: Annotated[User, Depends(require_permission("order:write"))]`。超级管理员因 `require_permission` 的 `is_super_admin` 快速通道应可访问。


### ISSUE-0054：动态订单详情路由同时抢占 `/orders/pending`

- 发现日期：2026-07-30
- 分类：Bug / 路由顺序 / 权限假象
- 严重程度：P1
- 状态：RESOLVED

`/orders/{order_id}` 虽已移到 `/orders/me` 后，但仍位于 `/orders/pending` 和 `/orders/customer/{user_id}` 之前。访问 `/orders/pending` 时，`pending` 被当成客户订单 ID，随后客户角色检查返回 `customer account required`，表现为超级管理员 403。已将动态详情路由移动到所有静态 `/orders/*` 路由之后。
