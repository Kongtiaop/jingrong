# Bug 修复记录

> 由 `docs/ISSUES.md` 整理抽取的**真 bug 修复记录**（含发现时间与解决方案）。未收录纯进度/决策/设计讨论条目。

- 共 179 条真 bug 修复记录。

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
**解决**：定位手段为对照前执行 `redis-cli FLUSHDB` 清空短时记忆。根治方向（未做）：对照脚本应在每条 query 前重置该会话短时记忆，使两条路径处于同一初始状态。
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
**解决**：清掉残留容器后统一用 `docker compose -f docker-compose.p0.yml up -d`（数据在固定卷 `wealth_pgdata`，`docker inspect wealth-postgres` 确认挂载即此卷；删容器不丢数据）。根治方向（未做）：去掉 `docker-compose.p0.yml` 的 `container_name`，改用自动命名（`wealth-p0-postgres-1`，与 CI 已有的 `wealth-p0-api-1` 风格一致），使两套 compose 可并存互不干扰。
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
**解决**：**未做（切流前置项）**。需在图的 `intent_plan_node`（或新增 `preference_node`）补齐 `extract_preference_statement` + `remember` + `pure_preference_statement` 短路，补齐后方可与管线谈等价替换。
**验证**：`grep -c "pure_preference_statement\|extract_preference_statement" app/agents/graph.py` → 0；管线对应命中见上述行号。

