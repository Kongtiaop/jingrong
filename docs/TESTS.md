# 测试记录

> 文档状态：当前测试策略与执行记录  
> 更新日期：2026-07-28

## 1. 使用规则

每次执行测试、编译检查、依赖检查、接口验证或基础设施联调，都在本文件追加记录。

每条记录包含：

- 测试编号
- 日期
- 目标
- 环境
- 命令/步骤
- 预期结果
- 实际结果
- 状态
- 未覆盖内容

## 2. 测试环境

当前开发环境：

```text
Conda 环境：langchain_env
Python：3.12.13
```

核心版本：

```text
FastAPI 0.140.0
SQLAlchemy 2.0.51
asyncpg 0.31.0
Redis 7.4.1
pgvector 0.5.0
PyJWT 2.13.0
LangChain 1.3.14
LangGraph 1.2.9
```

## 3. 测试记录

### TEST-0001：P0 依赖检查

- 日期：2026-07-28
- 目标：验证 P0 所需 Python 包和 LangChain/LangGraph V1.0 API 可导入
- 环境：`langchain_env`
- 命令：

```powershell
python scripts/check_dependencies.py
```

- 预期结果：所有必需包已安装，LangGraph 和 LangChain Tool API 可导入
- 实际结果：

```text
P0 dependency check passed
langchain/langgraph imports: ok
```

- 状态：PASS
- 未覆盖：未验证外部 PostgreSQL、Redis 服务连接

### TEST-0002：P0 Python 编译检查

- 日期：2026-07-28
- 目标：验证 P0 关键模块无语法错误
- 环境：`langchain_env`
- 命令：

```powershell
python -m py_compile app\main.py app\services\task_service.py app\tasks\streams.py workers\runner.py app\agents\graph.py app\schemas\agents.py scripts\check_dependencies.py
```

- 预期结果：命令返回 0
- 实际结果：

```text
P0 py_compile ok
```

- 状态：PASS
- 未覆盖：未运行接口测试和真实数据库集成测试

### TEST-0003：P0 Compose 配置检查

- 日期：2026-07-25
- 目标：验证独立 PostgreSQL/Redis Compose 不与 simple_rag 端口冲突
- 命令：

```powershell
docker compose --env-file .env.p0.example -f docker-compose.p0.yml config
```

- 预期结果：Compose 配置可以解析
- 实际结果：配置解析成功；本项目使用 PostgreSQL 5433、Redis 6380
- 状态：PASS
- 未覆盖：未验证容器启动后的应用连接

### TEST-0004：P0 PostgreSQL/Redis 实际健康检查

- 日期：2026-07-28
- 环境：`langchain_env`、独立 P0 Compose
- PostgreSQL：`127.0.0.1:5433`
- Redis：`127.0.0.1:6380`
- FastAPI：`127.0.0.1:8011`
- 步骤：

```powershell
docker compose --env-file .env.p0.example -f docker-compose.p0.yml up -d
GET http://127.0.0.1:8011/api/v1/health
```

- 预期结果：PostgreSQL、pgvector、Redis 检查通过
- 实际结果：

```text
PostgreSQL: ok
pgvector: ok
Redis: ok
Embedding dimension: 1024
Qwen: skipped（未配置 DASHSCOPE_API_KEY）
```

- 状态：PASS
- 未覆盖：真实 Qwen API 调用

### TEST-0005：P0 任务 API 与 Redis Streams 闭环

- 日期：2026-07-28
- 测试任务：`p0.integration_ping`
- 幂等键：`p0-integration-20260728-001`
- 步骤：

```text
POST /api/v1/tasks
    ↓
任务状态 queued
    ↓
启动 workers.runner
    ↓
Redis Streams Consumer Group 消费
    ↓
任务状态 success
```

- 实际结果：

```text
queued → success
attempt = 1
max_attempts = 3
```

- 状态：PASS
- 未覆盖：失败重试、Pending 恢复、Dead Letter、Outbox Publisher

### TEST-0006：P0 可靠任务机制静态验证

- 日期：2026-07-28
- 目标：验证 Outbox、Pending 恢复、Dead Letter、Trace ID 代码基线
- 环境：`langchain_env`
- 命令：

```powershell
python -m py_compile app\main.py app\core\trace.py app\tasks\streams.py app\services\task_service.py workers\runner.py
```

- 实际结果：

```text
compile ok
```

- 状态：PASS
- 未覆盖：真实 Outbox Publisher、失败注入和 Pending 恢复集成测试

### TEST-0008：Outbox Publisher 真实联调

- 日期：2026-07-28
- 环境：`langchain_env`、P0 PostgreSQL/Redis Compose
- FastAPI：`127.0.0.1:8012`
- 测试任务：`p0.outbox.integration`、`p0.outbox.worker`
- 实际结果：

```text
async_task: queued → success
outbox_event: pending → published
Redis Streams: published and consumed
attempt: 1
```

- 状态：PASS
- 未覆盖：人为 Redis 发布失败、Pending 超时恢复、Dead Letter 真实注入

### TEST-0009：Trace ID 响应头

- 日期：2026-07-28
- 请求头：`X-Trace-Id: trace-p0-outbox-001`
- 实际响应头：

```text
X-Trace-Id: trace-p0-outbox-001
```

- 状态：PASS

### TEST-0010：8012 端口占用与服务可用性

- 日期：2026-07-28
- 目标：判断 WinError 10013 的实际原因
- 现象：8012 被 PID 40352 监听
- 检查：

```powershell
netstat -ano | Select-String ':8012'
GET http://127.0.0.1:8012/api/v1/health
```

- 实际结果：

```text
PID 40352 为 langchain_env Python 进程
健康检查 status=ok
```

- 状态：PASS
- 结论：不是服务故障，是重复绑定已占用端口

### TEST-0011：修正数据库配置后 FastAPI 启动

- 日期：2026-07-28
- 问题：`InvalidPasswordError: 用户 wealth Password 认证失败`
- 修复：创建根目录 `.env`，配置 P0 PostgreSQL 5433 和 Redis 6380
- 启动端口：8013
- 实际结果：

```text
Application startup complete
GET /api/v1/health → 200 OK
PostgreSQL: ok
pgvector: ok
Redis: ok
```

- 状态：PASS

### TEST-0012：simple_rag 与 pg_prefect 融合状态检查

- 日期：2026-07-28
- 目标：确认 simple_rag 是否已完成 pg_prefect 能力融合，并确定当前项目复用边界
- 检查目录：

```text
C:/simple_rag
C:/pg_prefect
```

- 检查内容：README、架构说明、PLAN、ISSUES、TEST_RESULTS、Metadata Repository、PostgreSQL schema、Worker、检索和配置
- 实际结论：

```text
文档生命周期/Chunk Diff/增量 Embedding：已融合或具备成熟实现
PostgreSQL Metadata Repository：已准备，但默认仍 SQLite
向量主存储：仍为 Milvus
队列：仍是 Redis List/BRPOP 风格
```

- 状态：PASS
- 结论：可以高价值适配复用，但不能整项目直接替换当前 P0/P1 架构

### TEST-0013：Agent 与 GraphRAG 方案评审记录

- 日期：2026-07-29
- 目标：确认 Agent 通信边界与 Neo4j/pgvector 检索编排方式
- 评审依据：需求文档、功能设计文档、P0-P5 方案、simple_rag RAG 路由/Planner 设计
- 结果：

```text
Agent 默认不直接通信：通过 Supervisor 传递结构化 AgentResult
异步协作：Redis Streams + 业务事件
Neo4j：关系查询和多跳路径
pgvector：文本和语义证据
检索顺序：由 Query Router 决定，不固定
```

- 状态：PASS（架构评审）
- 未覆盖：尚未实现 Neo4j 或 GraphRAG 运行时代码

### TEST-0014：Agent 范式复杂度评审

- 日期：2026-07-29
- 目标：评估 ReAct、Plan-and-Execute、Reflexion、Self-Ask 是否全部进入当前项目
- 评审结果：

```text
主范式：受控 ReAct
编排：Supervisor/QueryRouter 轻量拆分
并行：有限 Fan-out/Fan-in
业务执行：确定性 Service
Reflexion：暂不实现
Self-Ask：暂不实现
独立 Plan-and-Execute：暂不实现
```

- 状态：PASS
- 结论：控制架构复杂度，不影响 P1-P4 主链路

### TEST-0015：公共基础设施组件编译检查

- 日期：2026-07-29
- 环境：`langchain_env`
- 命令：

```powershell
python -m py_compile app\main.py app\api\health.py app\api\tasks.py app\common\response\models.py app\common\exceptions\base.py app\common\exceptions\handlers.py app\common\pagination\models.py app\common\rate_limit\redis_limiter.py app\common\idempotency\store.py app\common\security\redaction.py app\common\middleware\trace.py
```

- 实际结果：`common infrastructure compile ok`
- 状态：PASS

### TEST-0016：公共响应和 Trace ID 运行验证

- 日期：2026-07-29
- 服务：`http://127.0.0.1:8014`
- 验证：

```text
GET /api/v1/health → 200
X-Trace-Id 请求值可在响应头返回
POST /api/v1/tasks 非法 Body → 422
```

- 状态：PASS
- 未覆盖：限流实际注入、幂等 Redis 运行测试、审计落库

### TEST-0017：P1 知识库模型和 pgvector 基线编译

- 日期：2026-07-29
- 环境：`langchain_env`
- 命令：

```powershell
python -m py_compile app\models\knowledge.py app\repositories\knowledge.py app\db\schema.py app\models\__init__.py
```

- 实际结果：`knowledge baseline compile ok`
- 状态：PASS
- 未覆盖：Alembic 迁移执行、数据库实际建表、pgvector 查询

### TEST-0018：P1 知识库模型和迁移编译

- 日期：2026-07-29
- 环境：`langchain_env`
- 命令：

```powershell
python -m py_compile app\models\knowledge.py app\repositories\knowledge.py app\schemas\knowledge.py app\api\knowledge.py app\api\__init__.py migrations\versions\0002_knowledge_base.py
```

- 实际结果：`P1 knowledge API compile ok`
- 状态：PASS

### TEST-0019：P1 知识库 API 运行验证

- 日期：2026-07-29
- 服务：`http://127.0.0.1:8015`
- 实际结果：

```text
GET /api/v1/knowledge/default → 200
POST /api/v1/knowledge/documents → 201
FastAPI application startup complete
```

- 状态：PASS
- 未覆盖：Alembic upgrade、Embedding、pgvector 检索和导入 Worker
### TEST-0020：P1 文档导入与 Qwen Embedding 基线编译

- 日期：2026-07-29
- 环境：`C:\Users\tx360\.conda\envs\langchain_env\python.exe`
- 命令：

```powershell
python -m py_compile app\infrastructure\qwen.py app\repositories\knowledge.py app\services\knowledge_ingestion.py app\services\__init__.py
```

- 预期结果：相关模块编译通过。
- 实际结果：`P1 ingestion compile ok`
- 状态：PASS
- 未覆盖：真实 DashScope 调用、Alembic 执行、PostgreSQL pgvector 入库和 Redis Worker 联调。
### TEST-0021：P0/P1 Docker 与 PostgreSQL/pgvector 真实联调

- 日期：2026-07-29
- 环境：Docker Desktop 4.82.0；`langchain_env`
- 步骤：

```powershell
docker compose -f docker-compose.p0.yml ps
python -m alembic upgrade head
```

并使用 asyncpg 检查 PostgreSQL、vector 扩展和知识库表。

- 实际结果：
  - PostgreSQL：healthy，版本 16.14
  - Redis：healthy
  - pgvector：0.8.5
  - 知识库表：4 张已存在
  - Alembic：执行通过
  - 默认知识库：创建/读取通过
- 状态：PASS
- 未覆盖：FastAPI/Worker 完整端到端启动、DashScope 真实 Embedding 调用。
### TEST-0022：P1 知识库导入事件与 Worker 编译验证

- 日期：2026-07-29
- 环境：`langchain_env`
- 命令：

```powershell
python -m py_compile app\api\knowledge.py app\repositories\knowledge.py app\schemas\knowledge.py app\infrastructure\qwen.py app\services\knowledge_ingestion.py workers\knowledge_worker.py workers\outbox_worker.py
```

- 实际结果：`all p1 worker compile ok`
- 状态：PASS
- 未覆盖：FastAPI、Outbox Publisher、Knowledge Worker 三进程真实联调；真实 DashScope Embedding；混合检索。
### TEST-0023：使用现有金融文档验证 Outbox/Redis/Embedding 导入链路

- 日期：2026-07-29
- 输入文件：`金融政策/个人投资者适当性管理指南.md`
- 实际结果：
  - 文档创建：PASS
  - Outbox 发布：PASS，`published=1`
  - ingestion Stream：PASS，长度为 1
  - 导入服务读取文件：PASS
  - Qwen Embedding：FAIL，`APIConnectionError: Connection error.`
  - 失败状态回写：PASS，文档/版本为 `failed`，错误信息已持久化
- 状态：PARTIAL
- 未覆盖：Embedding 成功后的 chunk 入库、版本激活和检索。
### TEST-0024：DashScope Embedding 修复后现有文档真实导入

- 日期：2026-07-29
- 输入文件：`金融政策/个人投资者适当性管理指南.md`
- 修复项：
  - 禁用错误的 `127.0.0.1:9` 代理继承
  - 使用当前可用模型 `qwen3.7-text-embedding`
  - 单文本请求，导入层按 4 个 chunk 分批
- 实际结果：
  - Embedding 请求：PASS
  - 维度：1024
  - 文档状态：`active`
  - 版本状态：`active`
  - chunk 数量：12
- 状态：PASS
- 未覆盖：独立 Worker 进程运行、pgvector 语义搜索 API。
### TEST-0025：P1 pgvector + PostgreSQL FTS 混合检索真实验证

- 日期：2026-07-29
- 环境：Docker PostgreSQL 16.14 + pgvector 0.8.5；`langchain_env`
- 查询：`投资者适当性管理`
- 实际结果：
  - Qwen Embedding：PASS，1024 维
  - pgvector cosine similarity：PASS
  - PostgreSQL FTS：PASS
  - 混合排序：PASS
  - 返回结果：3 条
  - 最高得分：约 `0.495311`
- 状态：PASS
- 未覆盖：通过 FastAPI HTTP 入口验证、重复导入幂等和大规模性能。
### TEST-0026：FastAPI 包路径启动命令诊断

- 日期：2026-07-29
- 现象：在 `app` 目录执行 `uvicorn main:app ...`。
- 实际结果：失败，`ModuleNotFoundError: No module named 'app'`。
- 原因：项目代码使用 `app.*` 绝对包导入，`app` 目录作为当前目录时，父目录未加入模块搜索路径。
- 正确命令：

```powershell
cd "C:\Users\tx360\Desktop\金融 - 副本"
uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

- 状态：DIAGNOSED
### TEST-0027：FastAPI 搜索接口与 Trace/Embedding 日志验证

- 日期：2026-07-29
- 启动命令：`uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload`
- 实际结果：
  - FastAPI startup：PASS
  - `POST /api/v1/knowledge/search`：HTTP 200
  - Qwen `/embeddings`：HTTP 200
  - Trace ID：请求日志和响应链路可见
- 状态：PASS
- 当前日志覆盖：HTTP Middleware、Qwen Embedding；数据库、Redis Worker、Outbox 仍需补齐。
### TEST-0028：基础日志监控模块编译验证

- 日期：2026-07-29
- 命令：

```powershell
python -m py_compile app\db\session.py app\tasks\streams.py workers\outbox_worker.py workers\knowledge_worker.py
```

- 实际结果：`observability compile ok`
- 状态：PASS
- 未覆盖：三个进程同时运行时的完整日志串联；Prometheus/Grafana 指标。
### TEST-0029：基础日志循环导入修复验证

- 日期：2026-07-29
- 现象：FastAPI 启动时 `trace.py` 与 `logging/config.py` 循环导入。
- 修复：移除日志配置模块对 `TraceIdMiddleware.get_trace_id` 的反向依赖。
- 验证命令：

```powershell
python -m py_compile app\common\logging\config.py app\common\middleware\trace.py app\main.py
python -c "import app.main; print('import ok')"
```

- 实际结果：编译通过，`import app.main` 通过。
- 状态：PASS
### TEST-0030：基础日志真实 HTTP 验证

- 日期：2026-07-29
- 服务：临时 `127.0.0.1:8011`
- 实际结果：
  - `/api/v1/health`：200
  - `/api/v1/knowledge/search`：200
  - `X-Trace-Id`：两个接口均返回
  - Qwen Embedding：200，1024 维
  - 日志字段：trace/component/method/path/duration_ms 均出现
- 备注：搜索请求约 18 秒，期间发生一次 SDK retry 后成功。
- 状态：PASS
### TEST-0031：Outbox + Knowledge Worker 全链路日志联调

- 日期：2026-07-29
- 输入文件：`金融政策/个人投资者适当性管理指南.md`
- 进程：Outbox Publisher、Knowledge Worker
- 实际结果：
  - Outbox 发布：PASS，`selected=1 published=1`
  - Redis 消费：PASS
  - Embedding：PASS，12 个 1024 维向量
  - 文档导入：PASS，`status=active`
  - chunk 数量：12
  - Redis ACK：PASS，`result=1`
  - 导入耗时：约 43427 ms
- 状态：PASS
- 备注：Embedding 期间出现一次 SDK retry，但应用链路最终成功。
### TEST-0032：Vue 3 + Axios 前端骨架验证

- 日期：2026-07-29
- 实际结果：
  - Vue 3/Vite/Axios/TypeScript 源文件已生成：PASS
  - Axios API 模块、Vue Router、首页和知识库页面已生成：PASS
  - `npm install`：未完成，当前环境 npm 使用离线缓存且缺少 `@vitejs/plugin-vue` 缓存包，报 `ENOTCACHED`。
- 状态：PARTIAL
- 下一步：在可访问 npm registry 的环境执行 `npm install` 和 `npm run build`。
### TEST-0033：Vue 3 + Axios 前端构建验证

- 日期：2026-07-29
- 命令：

```powershell
cd frontend
npm run build
```

- 实际结果：
  - Vite：8.1.5
  - modules transformed：81
  - `frontend/dist`：生成成功
- 状态：PASS
- 说明：前端可以使用 `npm run dev` 启动开发服务器。
### TEST-0034：P2-A 鉴权模块编译和导入验证

- 日期：2026-07-29
- 验证：

```powershell
python -m py_compile app\models\auth.py app\services\auth_service.py app\common\security\auth.py app\api\auth.py app\main.py
python -c "import app.main; print('auth import ok')"
```

- 实际结果：编译通过，`auth import ok`。
- 状态：PASS
- 未覆盖：数据库种子、真实登录/刷新/注销 HTTP 联调、Argon2/bcrypt、所有业务接口权限接入。
### TEST-0036：P2-A 鉴权真实服务层联调

- 日期：2026-07-29
- 实际结果：
  - 认证种子：PASS
  - `super_admin_demo` 登录：PASS
  - Access Token：生成成功
  - Refresh Token：生成成功
  - Refresh Token 轮换：PASS
  - 注销/撤销：PASS
- 启动修复：关系预加载后不再触发 `MissingGreenlet`。
- 状态：PASS
- 未覆盖：FastAPI HTTP 入口、前端登录页、业务接口 RBAC。
### TEST-0037：P2-A RBAC 与前端登录构建验证

- 日期：2026-07-29
- 实际结果：
  - 任务 API 权限依赖编译：PASS
  - 知识库 API 权限依赖编译：PASS
  - Vue 登录页：PASS
  - Axios Token 注入与 401 刷新逻辑：PASS（构建级验证）
  - `npm run build`：PASS，85 modules transformed
- 状态：PASS
- 未覆盖：真实 HTTP 登录和受保护接口 401/403 联调、Cookie Refresh、安全审查。
### TEST-0038：客户/员工注册接口编译验证

- 日期：2026-07-29
- 实际结果：
  - `RegisterType` 枚举：PASS
  - 客户注册不需公司码：代码路径已实现
  - 员工注册公司码校验：代码路径已实现
  - 员工初始 `employee_pending` 角色：代码路径已实现
  - 模块编译：`registration compile ok`
- 状态：PASS
- 未覆盖：真实 HTTP 注册、数据库重复用户名、员工后续管理员分组。
### TEST-0039：客户/员工注册与鉴权 HTTP 全链路验证

- 日期：2026-07-29
- 服务：临时 `127.0.0.1:8011`
- 实际结果：
  - 客户注册：201 PASS
  - 错误公司码员工注册：400 PASS
  - 正确公司码员工注册：201 PASS
  - 登录：200 PASS
  - `/auth/me`：200 PASS，角色 `customer`
  - Refresh Token 轮换：200 PASS
  - Logout：200 PASS
  - Trace/HTTP 日志：PASS
- 状态：PASS
### TEST-0040：基础日志文件落盘和轮转配置验证

- 日期：2026-07-29
- 实际验证：
  - 日志目录自动创建：PASS
  - `logs/app.log` 生成：PASS
  - `logs/error.log` 生成：PASS
  - 控制台输出：PASS
  - 应用日志落盘：PASS
  - `TimedRotatingFileHandler`：配置通过
- 状态：PASS
- 说明：临时 `logs_test` 测试目录已清理；正式运行时目录为项目根目录下的 `logs/`。
### TEST-0041：Vue 登录/注册/退出登录前端构建验证

- 日期：2026-07-30
- 实际结果：
  - `/login` 页面：代码完成
  - `/register` 页面：代码完成
  - 客户/员工注册切换：代码完成
  - 员工公司码字段条件显示：代码完成
  - 登录状态和角色显示：代码完成
  - 退出登录和 Token 清理：代码完成
  - 受保护路由：代码完成
  - `npm run build`：PASS，87 modules transformed
- 状态：PASS
- 未覆盖：真实浏览器点击联调、跨页面刷新后的登录态体验、Refresh Token 过期场景。
### TEST-0042：员工公司码错误码和登录态即时更新

- 日期：2026-07-30
- 结论：
  - 过短错误公司码不再被 Pydantic 误判为 422；
  - 错误公司码由业务层返回 400；
  - 合法字段格式错误仍返回 422；
  - 登录成功后前端通过状态事件立即请求 `/auth/me` 并显示用户/角色；
  - 退出登录后前端立即清理用户状态。
- 前端构建：PASS，87 modules transformed。
- 状态：PASS（构建和代码路径验证）。
- 未覆盖：浏览器实际点击操作和错误公司码 400 的重新 HTTP 联调。
### TEST-0043：全局认证状态即时更新修复

- 日期：2026-07-30
- 实际结果：
  - 全局 `useAuth` 状态：已实现
  - 登录后立即调用 `/auth/me`：已实现
  - `auth:changed` 状态事件：已实现
  - 退出后即时清理状态：已实现
  - Axios Refresh 后同步状态：已实现
  - 员工错误公司码业务层返回 400：代码已修复
  - `npm run build`：PASS，88 modules transformed
- 状态：PASS（构建与代码路径验证）
- 未覆盖：真实浏览器点击和缓存旧前端资源后的强制刷新。
### TEST-0044：登录态初始化和下一阶段入口验证

- 日期：2026-07-30
- 用户验证：客户/员工登录注册和退出功能已确认通过。
- 本次补充：
  - 全局认证 Store 初始化状态：完成
  - 页面加载时调用 `/auth/me`：完成
  - 登录态初始化期间避免错误显示：完成
  - `npm run build`：PASS，88 modules transformed
- 状态：PASS
### TEST-0045：客户前台与工作人员后台基础壳构建验证

- 日期：2026-07-30
- 实际结果：
  - 客户前台 `/customer`：代码完成
  - 工作人员后台 `/staff`：代码完成
  - `employee_pending` 待分组提示：代码完成
  - 角色工作台菜单：代码完成
  - 模块占位路由：代码完成
  - `npm run build`：PASS，94 modules transformed
- 状态：PASS
- 未覆盖：真实后端产品/资产/订单 API、后端权限动态菜单、浏览器点击验证。
### TEST-0046：前端空白页修复构建验证

- 日期：2026-07-30
- 根因：Vue 模板对 ref 使用 `.value`，破坏模板自动解包。
- 修复：改为模板自动解包，并完善认证初始化状态。
- 实际结果：`npm run build` 通过，94 modules transformed。
- 状态：PASS
- 未覆盖：浏览器强制刷新后的实际页面点击验证。
### TEST-0047：前端空白页二次修复构建验证

- 日期：2026-07-30
- 修复：清理 App/Staff 模板残留 `.value`，修正客户占位页面路由。
- 实际结果：`npm run build` PASS，94 modules transformed。
- 状态：PASS
- 未覆盖：浏览器运行时页面点击和控制台错误检查。
### TEST-0048：公开前台与独立后台前端构建验证

- 日期：2026-07-30
- 实际结果：
  - 首页匿名访问路由：代码完成
  - 产品公开展示 `/products`：代码完成
  - 客户操作 `/customer`、资产、订单：登录保护
  - 工作人员后台 `/staff`：独立入口
  - 登录/注册按钮：匿名状态显示
  - 登录后用户/角色/退出：登录状态显示
  - `npm run build`：PASS，96 modules transformed
- 状态：PASS
- 未覆盖：浏览器实际匿名访问、客户登录后操作、工作人员角色后台点击联调。
### TEST-0049：知识库测试页面访问边界调整

- 日期：2026-07-30
- 实际结果：
  - `/knowledge` 路由增加登录保护：PASS
  - 匿名导航隐藏知识库测试入口：PASS
  - `npm run build`：PASS，96 modules transformed
- 状态：PASS
### TEST-0050：P2-C 客户画像/资产/持仓/产品模型和种子验证

- 日期：2026-07-30
- 实际结果：
  - P2 模型编译：PASS
  - Schema/ API 编译：PASS
  - `profile seed ok`：PASS
  - customer_demo 构造画像、资产、产品和持仓种子：PASS
  - 前端 `npm run build`：PASS，97 modules transformed
- 状态：PASS
- 未覆盖：HTTP API 端到端、产品匹配、员工后台真实客户查询。
### TEST-0051：业务资料分类总结文档完整性检查

- 日期：2026-07-30
- 输入目录：公司信息、公司业务、金融政策、用户研判规则
- 输出文档：

```text
docs/BUSINESS_KNOWLEDGE_COMPANY.md
docs/BUSINESS_KNOWLEDGE_PRODUCTS.md
docs/BUSINESS_KNOWLEDGE_POLICIES.md
docs/BUSINESS_KNOWLEDGE_RISK.md
```

- 实际结果：四类资料均已读取并形成分类总结。
- 状态：PASS
- 说明：总结文档用于开发索引，不替代原始政策和业务文档。
### TEST-0052：产品适配规则与确定性推荐验证

- 日期：2026-07-30
- 实际结果：
  - RiskRule/ProductSuitabilityRule 模型编译：PASS
  - 构造种子：PASS
  - `customer_demo` 推荐：PASS，`matches=1`，`excluded=0`
  - 前端产品匹配展示构建：PASS，97 modules transformed
- 状态：PASS
- 未覆盖：多风险等级客户、拒绝原因、二次确认、双录和冷静期。



### TEST-0053：交易闭环第一版代码验证

- 日期：2026-07-30
- 实际结果：
  - 账户、订单、交易、订单状态历史模型已新增；
  - 客户创建/确认/取消订单 API 已新增；
  - 员工审核、拒绝和 Mock 执行 API 已新增；
  - Mock 执行会更新账户余额、冻结金额和客户持仓；
  - 后端交易模块 `py_compile`：PASS；
  - 前端交易模块和订单页面已新增，待 npm 构建和真实浏览器联调。
- 状态：PARTIAL
- 未覆盖：数据库真实迁移/启动、HTTP 全链路、浏览器操作、并发幂等和权限数据范围。


### TEST-0054：虚拟用户交易闭环真实数据库验证

- 日期：2026-07-30
- 环境：本地 PostgreSQL/pgvector Docker，`langchain_env` Python 环境。
- 步骤：
  1. 执行 `python -m scripts.seed_demo_data`；
  2. 使用 `customer_demo` 创建 10000 元订单；
  3. 客户确认订单；
  4. 使用 `operations_demo` 审核并 Mock 执行；
  5. 查询订单、交易、账户和持仓。
- 实际结果：
  - seed：PASS；
  - 订单状态：`executed`；
  - 交易记录：成功生成；
  - 可用余额：`200000.00 → 190000.00`；
  - 持仓数量：`100.0000 → 10100.0000`；
  - 余额和订单金额核对：PASS。
- 状态：PASS
- 未覆盖：HTTP/FastAPI 入口、浏览器点击、并发幂等和拒绝订单路径。


### TEST-0055：交易 HTTP 全链路及异常路径验证

- 日期：2026-07-30
- 环境：已启动 API `http://127.0.0.1:8010`，本地 PostgreSQL/pgvector/Redis。
- 结果：
  - `/api/v1/health`：PASS，PostgreSQL、pgvector、Redis、Embedding 均正常；
  - 客户登录和 `/auth/me`：PASS；
  - 产品查询和账户查询：PASS；
  - 客户创建订单：PASS；
  - 客户确认订单：PASS；
  - 运营查看待审核订单：PASS；
  - 运营审核并 Mock 执行：PASS；
  - 订单状态变为 `executed`：PASS；
  - 账户余额 `200000.00 → 190000.00`：PASS；
  - 交易记录查询：PASS；
  - 客户创建后直接取消：PASS，状态为 `cancelled`；
  - 余额不足：PASS，HTTP 400，`insufficient available balance`；
  - 客户访问待审核订单接口：PASS 被拒绝，HTTP 403；
  - 其他用户确认订单：PASS 被拒绝，HTTP 400，`order not found`。
- 状态：PASS
- 备注：由于之前数据库中已有成功交易，账户余额测试前通过 seed 重置为 200000 元；seed 同时清理 customer_demo 的演示订单、交易和状态历史。
- 未覆盖：浏览器实际点击、员工后台页面视觉验证、并发重复提交、订单幂等键。


### TEST-0056：开发环境一键清理和重新 seed 验证

- 日期：2026-07-30
- 命令：`python -m scripts.reset_dev_state`
- 实际结果：
  - PostgreSQL 应用/认证业务表清理：PASS；
  - Redis 清理调用：PASS；
  - 演示账号、产品、画像、资产、持仓和交易账户重新 seed：PASS；
  - `customer_demo` 账户恢复到 `200000.00` 可用余额：PASS；
  - 交易闭环测试再次通过：PASS。
- 可选参数：`--include-knowledge` 清理知识库，`--no-redis` 跳过 Redis。
- 状态：PASS
- 备注：当前终端对 Docker named pipe 查询权限不足，因此未额外执行 Docker CLI 的 Redis/PG 计数检查；脚本自身通过应用数据库连接完成验证。


### TEST-0057：客户资产、订单详情和交易记录第二步验证

- 日期：2026-07-30
- 实际结果：
  - 客户首页读取真实交易账户余额：代码完成；
  - 我的订单页面读取订单状态历史：代码完成；
  - 交易记录展示：代码完成；
  - 订单详情 API：代码完成；
  - 订单幂等字段数据库补齐：PASS；
  - reset seed 后交易闭环：PASS。
- 状态：PARTIAL
- 未覆盖：运行中的 API 尚未重启加载本轮新增路由/幂等代码，HTTP 幂等重复请求待重启后验证；浏览器点击待验证。


### TEST-0058：权限 Seed 重复初始化修复验证

- 日期：2026-07-30
- 问题：服务热重载期间出现 `duplicate key value violates unique constraint ix_permissions_code`。
- 修复：权限 seed 新建记录后立即 flush，避免同一 Session 内重复待插入对象。
- 实际结果：重复执行 `python -m scripts.seed_demo_data`：PASS。
- 状态：PASS
- 未覆盖：多个独立 API worker 同时首次启动时的跨进程竞争；如需完全覆盖，应进一步增加数据库原生 UPSERT/分布式启动锁。


### TEST-0059：重启后订单详情与幂等 HTTP 验证

- 日期：2026-07-30
- API 健康检查：PASS。
- 权限 seed：服务成功启动，未再出现 `ix_permissions_code` 重复键错误。
- 订单详情：`GET /api/v1/trading/orders/{order_id}` 返回订单状态历史，PASS。
- 幂等重复提交：同一客户使用相同 `idempotency_key` 连续创建两次，返回相同订单 ID，PASS。
- 后续确认和运营审核执行：PASS。
- 最终订单状态：`executed`。
- 账户余额：`200000.00 → 190000.00`。
- 状态：PASS
- 问题修复：订单历史 ORM 对象已转换为 Pydantic 响应模型，避免 HTTP 500。


### TEST-0060：工作人员客户管理第一版验证

- 日期：2026-07-30
- 客服角色调用客户列表：PASS，返回客户、画像摘要、资产摘要和持仓数量。
- 客户角色调用客户列表：旧 API 进程返回 200；已新增 staff-only 依赖，需重启 API 后复验 HTTP 403。
- 客户订单查询接口和前端客户管理页面：代码完成。
- 后端 Python 编译：PASS。
- 状态：PARTIAL


### TEST-0061：工作人员客户管理依赖注入修复验证

- 日期：2026-07-30
- 发现：重启后客户列表接口返回 422，缺失 query 参数 `user`。
- 根因：依赖参数名 `_user` 被 FastAPI 当作普通查询参数。
- 修复：改为显式 `user: User = Depends(require_staff_permission(...))`。
- 后端编译：PASS。
- 待验证：重启 API 后重新执行工作人员/客户角色 HTTP 权限测试。
- 状态：PARTIAL
