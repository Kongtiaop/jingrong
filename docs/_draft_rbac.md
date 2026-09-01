# 认证授权 RBAC + 权限版本化缓存 + Subject Scope 实现细节简报

> 用途：面试答辩用实现细节简报。依据代码逐一核对，所有证据均标注 `文件:行号`。

## 1. 认证流程（注册 / 登录 / JWT / Argon2 / 刷新轮换）

**接线位置**：`AuthService` 在 `app/main.py:47` 挂到 `app.state.auth_service`；HTTP 端点集中在 `app/api/auth.py`。

### 1.1 注册
- 端点：`POST /auth/register`（`app/api/auth.py:28-35`），入口校验在 `app/schemas/auth.py:27-32`（username 正则 `^[A-Za-z0-9_.-]+$`，密码 ≥8 位，员工注册可带 company_code）。
- 核心实现：`AuthService.register`（`app/services/auth_service.py:35-52`）：
  1. **员工注册码校验**：`register_type == employee` 时必须等于 `settings.employee_registration_code`，否则 `ValueError`（36-37 行）。
  2. 用户名唯一性检查（39-41 行）。
  3. 按类型分配角色：customer → `"customer"`，employee → `"employee_pending"`（待分组员工，42 行）；角色不存在则自动建角色（43-47 行）。
  4. 用 `hash_password(password)`（Argon2）生成哈希并创建 `User`（48-49 行），默认 `status="active"`、`is_super_admin=False`。
  5. 返回 `RegisterResponse`（含 account_type 与 role）。

### 1.2 Argon2 密码哈希
- `app/services/auth_service.py:17-24`：模块级单例 `password_hasher = PasswordHasher()`。
  - `hash_password` → `password_hasher.hash(password)`。
  - `verify_password` → `password_hasher.verify(...)`，捕获 `InvalidHashError` 和 `VerificationError` 统一返回 `False`（不区分"用户不存在/密码错"，防枚举）。

### 1.3 登录
- 端点：`POST /auth/login`（`app/api/auth.py:9-13`），`ValueError` → 401。
- 核心实现：`AuthService.login`（`auth_service.py:54-61`）：
  1. 按 username 加载用户并 `selectinload(User.roles)` 预取角色（56 行）。
  2. 三重校验：用户存在、`status == "active"`、Argon2 密码正确（57 行）。
  3. 生成刷新令牌 `secrets.token_urlsafe(64)`，**只存 SHA-256 哈希** `token_hash()`（27 行）到 `RefreshSession`，`expires_at = now + refresh_token_expire_days`（默认 30 天，58-60 行）。
  4. 返回 `{access_token, refresh_token, token_type: "bearer", expires_in: jwt_expire_minutes*60}`。

### 1.4 JWT 生成与校验
- **生成**：`AuthService.create_access_token`（`auth_service.py:31-34`）。Payload：`sub=user.id`、`username`、`roles=[r.code ...]`、`is_super_admin`、`type="access"`、`exp=now+timedelta(minutes=jwt_expire_minutes)`（默认 1440 分钟=24h）；算法 `HS256`（`settings.jwt_algorithm`），密钥 `settings.jwt_secret`。
- **校验**：`current_user`（`app/common/security/auth.py:12-21`）：
  1. `HTTPBearer(auto_error=False)` 取凭据，缺失 → 401。
  2. `jwt.decode(..., algorithms=[settings.jwt_algorithm])`，任何 `PyJWTError` → 401；校验 `type == "access"`（17 行）。
  3. **每次都从数据库重新加载用户**（`selectinload(User.roles).selectinload(Role.permissions)`，且 `status=="active"`）——JWT 只是"身份 + 过期"的无状态凭证，**权限永远以数据库当前值为准**（这是角色变更即时生效的关键，见第 6 节 Q1）。
- WebSocket 版本：`websocket_current_user`（`auth.py:68-80`）从 `query_params["access_token"]` 取 token，同样的 decode + 重载用户流程。
- `/auth/me`（`app/api/auth.py:23-26`）：返回当前用户 id、roles、**权限并集**、is_super_admin。

### 1.5 Token 刷新 / 轮换 / 撤销
- **刷新**：`POST /auth/refresh`（`api/auth.py:14-18`）→ `AuthService.refresh`（`auth_service.py:62-72`）：
  1. 按 `token_hash` 查 `RefreshSession`，要求 `revoked_at IS NULL` 且 `expires_at > now`（64 行），查不到 → 401。
  2. 校验用户仍 `active`（66-67 行）。
  3. **轮换**：把旧行 `revoked_at=now`（68 行），签发新 refresh token 并入库（69-71 行）。旧 token 一旦被用过即作废——重放被轮换过的旧 token 会查不到有效行而 401，天然具备**重用检测/轮换安全**语义。
- **撤销/登出**：`POST /auth/logout`（`api/auth.py:19-22`）→ `AuthService.revoke`（`auth_service.py:73-76`），按哈希把有效行置 `revoked_at`。
- 数据表：`RefreshSession`（`app/models/auth.py:43-50`）：`user_id`（FK CASCADE）、`token_hash`（SHA-256、unique、index）、`expires_at`、`revoked_at`、`created_at`。

---

## 2. RBAC 数据模型与权限矩阵

### 2.1 表结构（`app/models/auth.py`）
| 表 | 说明 |
|---|---|
| `users`（15-26 行） | `id`(UUID str36, PK)、`username`(unique, index)、`password_hash`、`display_name`、`status`(active/deleted/disabled, index)、`deleted_at`、`is_super_admin`(bool)、`created_at/updated_at`；`roles` 多对多 |
| `roles`（28-34 行） | `id`、`code`(unique, index)、`name`；`users` 与 `permissions` 双向关系 |
| `permissions`（36-41 行） | `id`、`code`(unique, index)、`name` |
| `user_roles`（6-9 行） | 关联表，`(user_id, role_id)` 复合主键，FK 均 `ondelete="CASCADE"` |
| `role_permissions`（10-13 行） | 关联表，`(role_id, permission_id)` 复合主键，FK CASCADE |
| `refresh_sessions`（43-50 行） | 见 1.5 |
| `customer_advisor_authorization`（57-72 行） | Subject Scope 授权记录，见第 3 节 |

### 2.2 权限矩阵怎么定义
- **权威文档**：`docs/RBAC_PERMISSION_MATRIX.md`——六大设计原则（角色≠权限、**后端为最终边界**、本人/已授权/全量三分、最小权限与职责分离、高风险可审计、超级管理员受控）、角色目录（§2）、权限码目录（§3，`customer:* / product:* / risk:* / asset:* / order:* / transfer:* / operation:* / task:* / workorder:* / customer_service:* / audit:* / admin:*` 共 12 个权限域）、角色×能力矩阵（§4）。
- **可执行种子**：`app/services/auth_seed.py`：
  - `PERMISSIONS`（22-37 行）：25 个权限码（code, name）列表，如 `customer:write`、`customer:read_all`、`customer:read_assigned`、`risk:alert_read/handle/action`、`risk:scan_execute`、`order:submit_staff`、`transfer:submit_staff`、`order:review`、`operation:read_staff/audit_read`、`task:create/read`、`workorder:create/read/handle/assign`、`customer_service:accept/assign`、`audit:read`、`admin:write` 等。
  - `ROLE_PERMISSIONS`（38-49 行）：**角色→权限码映射表**（即矩阵的实现），`super_admin` = 全部权限，`employee_pending` = 空。
  - `DEMO_ACCOUNTS`（10-21 行）：10 个演示账号，密码确定性生成 `Demo@2026{角色}`。

### 2.3 角色目录（10 个）
`customer`（普通客户）、`customer_service`（客服）、`customer_manager`（客户经理）、`advisor`（理财顾问）、`product_manager`（产品）、`risk_manager`（风控）、`operations`（运营）、`auditor`（审计）、`super_admin`（超级管理员）、`employee_pending`（待分组员工）。

### 2.4 种子装载 `ensure_auth_seed`（`auth_seed.py:51-91`）
- 仅当 `demo_accounts_enabled` 且 `app_env ∈ {development, dev, local}` 时执行（52 行）；`Settings.validate_p0`（`settings.py:81-82`）强制生产环境关闭演示账号。
- **幂等且并发安全**：逐条 SELECT 后补 INSERT，并在下次 SELECT 前 `flush()`，避免并发启动 worker 重复插入（56-64 行注释明确说明）；角色/用户存在则 `refresh` 后 `clear()` 再重灌（76-78、86-89 行），每次启动把矩阵收敛到种子定义的状态。

### 2.5 管理端 RBAC 接口（`app/api/admin.py`）
- `GET /admin/roles`（200-206 行）、`GET /admin/permissions`（390-396 行）。
- `PUT /admin/roles/{role_id}/permissions`（473-493 行）：校验权限码全部存在（482-483）、**禁止修改 super_admin 角色权限**（484-485）、写审计日志、**查询该角色全部受影响用户并逐个 `bump_permission_context_version`**（488-491 行，触发缓存失效）。
- `PUT /admin/users/{user_id}/roles`（399-417 行）：**禁止给自己移除 super_admin**（402-403）、校验角色存在（410-411）、写审计、`bump_permission_context_version`（415 行）。
- `PUT /admin/users/{user_id}/status`、软删除/恢复、回收站（420-470 行）：状态机 + 审计。
- 双通道管理员判定 `ensure_admin`（32-35 行）：`is_super_admin` **或** 拥有 `super_admin` 角色。
- 通用数据层：`resolve_admin_uow`（23-28 行）→ `app/repositories/unit_of_work.py` 的 `create_configured_unit_of_work`（49-55 行，按 `database_backend` 选择 PostgreSQL 的 `SqlAlchemyTradingUnitOfWork` 或 MySQL 校验版 UoW）；角色/权限/用户变更与审计日志在同一 session 内提交，保证事务一致性。

### 2.6 鉴权依赖注入（`app/common/security/auth.py`）
- `require_permission(code)`（23-29 行）：`is_super_admin` 直接放行；否则取 `{p.code for role in user.roles for p in role.permissions}`，缺权限 → 403。
- `require_staff_permission(code)`（31-39 行）：在权限校验之外**额外拒绝持有 `customer` 角色的用户**（36 行），防止客户调工作人员接口。
- `require_advisor_permission(code)`（47-56 行）：必须同时具备 `advisor` 角色 + 该权限码。
- `advisor_user`（59-65 行）、`staff_customer_user`（42-43 行，即 `require_staff_permission("customer:read_all")`）。

---

## 3. Subject Scope："客户经理/投顾只能访问被授权客户"

### 3.1 授权记录的数据结构
`CustomerAdvisorAuthorization`（`app/models/auth.py:57-72`）：
- `customer_id`、`advisor_id`（均 FK `users.id`，CASCADE，index）——**被授权客户 = advisor 对 customer 的一行记录**。
- 业务约束字段：`allowed_actions`（默认 "buy"）、`product_scope`（默认 "*"）、`max_single_amount`、`max_daily_amount`、`risk_level_ceiling`（默认 "C2"）——scope 不只是"客户 id 集合"，还携带**金额/产品/风险等级上限**。
- 有效期：`effective_from`（默认 now）、`effective_to`（可空=长期）、`status`（active）、`revoked_at`。

### 3.2 计算入口
`SubjectScopeService.authorized_subject_ids(session, user)`（`app/services/subject_scope.py:13-31`）：
1. **超级管理员**：返回 `set()`，注释明确"空集合仅对 super_admin 表示无限制"（15-16 行）。
2. **advisor / customer_manager**：查询授权表，条件：`advisor_id == user.id`、`status == "active"`、`effective_from <= now`、`(effective_to IS NULL OR effective_to >= now)`、`revoked_at IS NULL`，返回 `customer_id` 集合（18-29 行）。
3. **其他员工**：只能访问自己上下文 `{user.id}`（31 行）。
- 注意：`customer_manager` 拿到的也是授权集合（`customer:read_all` 权限 + 授权范围双重限定），与文档"已授权/已分配范围"一致。

### 3.3 绑定环节与防越权（关键链路）
- **绑定点**：`app/api/customer_service.py:255`——数据分析执行前 `authorized_subject_ids = await SubjectScopeService.authorized_subject_ids(analysis_session, user)`，传入 `DataAnalysisAgent.analyze_template_attempt`（256-264 行）。
- **防越权核心**：`DataAnalysisAgent.bind_template_parameters`（`app/services/data_analysis_agent.py:116-151`）：
  - 从用户问题里提取的 subject 引用与授权集合求交集（136-138 行），**凡是请求里出现不在授权集合内的客户标识 → 直接拒绝**（`invalid_parameters` 理由，走澄清/拒绝路径）。
  - SQL 模板的 subject 参数**由服务端替换**为 `subject_scope`：super_admin 且授权集为空 → `None`（不限制）；否则 = 授权 id 集合；普通用户 = 本人 id（151 行）。**客户提供的任意 customer_id 都无法进入最终 SQL**，这是防止客户间越权的根本机制——不是靠"前端传参后校验"，而是**参数在服务端被改写为授权闭包**。
- 路由层：投顾相关接口用 `advisor_user` / `require_advisor_permission`（角色+权限门），范围层用 SubjectScopeService，**两层独立可组合**。
- 目录检索注释（`customer_service.py:239-240`）明确说明：检索用模板语义问题而非原始 subject id，**subject 授权始终由 SubjectScopeService 兜底**。

### 3.4 授权变更的单一写入口与缓存失效
- `AdvisorAuthorizationService`（`app/services/advisor_authorization_service.py`）：`grant_or_update`（25-44 行，`with_for_update` 行锁防并发重放）、`revoke_ids`（47-63 行）、`revoke_for_customer`（66-78 行）——所有变更走这一处，保证"单一写边界"。
- 管理端批量接口 `PUT /admin/advisor-authorizations/batch`（`admin.py:345-387`）：批量 grant/revoke + 审计 + **事务内写入 Outbox 失效事件**（`enqueue_scope_invalidation_events`，`advisor_authorization_service.py:80-95`，事件类型 `authorization.subject_scope_invalidate`）——**事件与数据变更同事务提交，进程崩溃可重试**（`admin.py:94` 允许重放 dead 的该类事件）。
- 订阅方执行 `bump_subject_scope_version`（INCR `wm:subject-scope-version:{user_id}`，见第 4 节）使受影响的投顾/客户经理缓存整体失效。

---

## 4. 权限版本化缓存 permission_scoped_cache（`app/services/permission_scoped_cache.py`）

### 4.1 Cache Key 组成
- `PermissionContext`（9-59 行，frozen dataclass）建模"**能影响一个缓存读结果的全部授权维度**"：`user_id`、`role_codes`（排序元组）、`permission_codes`（所有角色权限的排序并集）、`tenant_id`、`data_scope_version`、`catalog_policy_version`。
- `fingerprint()`（49-59 行）：将 context 序列化为**规范化 JSON**（`sort_keys=True` + 紧凑分隔符 + ensure_ascii）→ **SHA-256 → 取前 24 位 hex**。规范化序列化保证相同语义产生相同指纹。
- `PermissionScopedCache.build_key`（74-77 行）：`{key_prefix}:{namespace}:pc-{context.fingerprint()}:sc-{scope_fingerprint}`。其中：
  - `namespace` 是逻辑名（如 `data_catalog_search`），**禁止含 ":"**，防止调用方构造注入 key（75-76 行）。
  - `scope_fingerprint`（69-72 行）对查询级参数（如 `{"query","top_k","domain_code"}`，见 `data_catalog_service.py:31`）同样做规范化 JSON + SHA-256。
- 效果：**"谁的（指纹）+ 查什么（scope 指纹）"共同决定缓存条目**，不同用户/权限/范围天然物理隔离。

### 4.2 角色/权限变更如何使旧缓存失效
- 版本键：`wm:permission-context-version:{user_id}` 与 `wm:subject-scope-version:{user_id}`。
- **读侧**：`PermissionContext.from_user_cached`（21-41 行）每次构造 context 时从 Redis 读两个版本号，`data_scope_version = "{permission版本}"` 或 `"{permission版本}:subject-{scope版本}"`（31-37 行）——版本号进入指纹。
- **写侧**：
  - `bump_permission_context_version`（103-111 行）：`INCR wm:permission-context-version:{user_id}`。触发点：管理员改用户角色（`admin.py:415`）、改角色权限（`admin.py:490-491`，遍历受影响用户）。
  - `bump_subject_scope_version`（114-122 行）：`INCR wm:subject-scope-version:{user_id}`。触发点：客户-投顾授权变更（经 Outbox 事件驱动，见 3.4）。
- **失效原理**：版本号变了 → 指纹变了 → `build_key` 生成的新 key 与旧 key 不同 → 旧条目"逻辑上不可达"，靠 TTL（默认 60 秒，`set_json` 92-100 行）自然回收。**不需要扫描/删除任何 key**。

### 4.3 为什么这样做（设计意图）
1. **缓存永远不改变授权结果**：`get_json` 任何异常都返回 `None`（79-90 行，走实时查询）、`set_json` 失败返回 `False`；注释明确"Cache is an optimization only. A backend failure must never change authorization behavior"（89-90 行）。
2. **双重保险**：指纹里本来就含实时角色/权限码——即使版本号 INCR 失败，角色/权限本身变了也会改变指纹（版本号是"主动/急切失效"，指纹是"安全底线"，见 110、121-122 行注释）。**安全性从不依赖缓存**。
3. **原子且无竞态**：`INCR` 原子递增，并发多次变更不会丢版本；相比"枚举删除某用户的所有 key"，O(1) 且不依赖 Redis SCAN。
4. **失效粒度精确**：按 `user_id` 精确失效（角色变更只影响该用户；角色权限变更影响该角色的所有用户，逐个 bump），不误伤其他用户缓存。
5. **与 TTL 协同**：版本化 + 短 TTL 双保险，即使漏 bump 也最多延迟一个 TTL。
6. **数据范围维度独立**：subject scope 版本号独立于权限版本号，授权变更（更频繁）与角色变更互不串扰。

### 4.4 实际应用场景
`DataCatalogService.search`（`app/services/data_catalog_service.py:30-193`）：先按 `PermissionContext`+查询 scope 查缓存（32-35 行），命中则直接返回并标注 `+permission-cache`；未命中走真实检索后 `set_json(..., ttl_seconds=60)`（191-192 行）。`CatalogSearchScope.from_user`（15-21 行）在 super_admin 时把 `super_admin` 角色并入 role_codes 参与检索，与授权语义一致。

---

## 5. 设计取舍：为什么权限/数据范围放在服务端，而非前端/模型层

1. **前端只做体验，不做法定依据**：`docs/RBAC_PERMISSION_MATRIX.md` 原则 2 明确"后端为最终边界；前端菜单/按钮隐藏只改善体验，不能作为授权依据；每个受保护 API 必须执行服务端鉴权"。前端可被手工请求、调试工具、爬虫绕过。
2. **JWT claims 不可信为授权源**：token 里的 roles 只是便捷快照；`current_user` 每次请求**从数据库重载用户 + 角色 + 权限**（`auth.py:18-20`），使角色/权限/状态变更（含禁用、删权）**即时生效，无需等 token 过期**——这是"服务端为唯一事实源"的直接体现。
3. **权限与数据范围分层，而非模型层全局过滤**：
   - 操作级（能不能做）→ FastAPI 依赖注入 `require_permission / require_staff_permission / require_advisor_permission`，声明式、可组合、可单测。
   - 数据级（能做哪些客户）→ `SubjectScopeService.authorized_subject_ids` + 模板参数绑定（`data_analysis_agent.py`），**把 SQL 的 subject 参数服务端改写为授权闭包**，客户端无法注入任意 customer_id。
   - 不把过滤做在 ORM 模型层（如全局 query filter）的原因：模型层过滤隐式、难以审计、多角色并集/交集语义混乱、join 复杂、且无法覆盖"模板 SQL 参数"这种动态路径；显式传参 + 显式校验可测试、可读、可审计。
4. **缓存不承担任何授权职责**：缓存 key 绑定授权指纹、失败降级实时计算、版本号只做急切失效——授权结果 100% 来自数据库实时计算。
5. **变更即审计**：角色/权限/状态/授权变更全部写 `AuditLog` 且与业务变更同事务提交，满足"高风险操作可审计"原则。
6. **超级管理员受控而非默认全放行**：`is_super_admin` 标志 + `super_admin` 角色双通道，但文档要求超级管理员也按明确契约测试（原则 6）；数据范围内 super_admin 返回空集 = "无限制"是显式约定（`subject_scope.py:15-16`）。

---

## 6. 面试高频问答（基于本代码的应答稿）

**Q1：JWT 里已经放了 roles，为什么每个请求还要查一次数据库？**
答：JWT 在我们系统里只承担"身份 + 过期时间"的无状态凭证，授权信息永远以数据库为唯一事实源。`current_user`（`app/common/security/auth.py:12-21`）每次请求都 `selectinload` 重载用户、角色、权限并校验 `status == "active"`。这样做有三个收益：一是角色/权限/禁用状态**变更即时生效**，管理员改完权限下一个请求就生效，不需要等 token 过期或强制重新登录；二是撤销授权立即生效；三是 token 被篡改/过期/吊销（refresh 轮换）都能被检测。代价是多一次查询，但权限表很小且有索引，收益远大于成本。

**Q2：权限改了，权限缓存怎么失效？为什么用"版本号"而不是直接删 key？**
答：缓存 key = `wm:{namespace}:pc-{指纹}:sc-{范围指纹}`，指纹由角色码、权限码、data_scope_version、catalog_policy_version 等维度做规范化 JSON + SHA-256 生成（`permission_scoped_cache.py:49-59, 74-77`）。角色/权限变更时，管理员接口会找到**所有受影响用户**，对每人 `INCR wm:permission-context-version:{user_id}`（`admin.py:415, 490-491`；`bump_permission_context_version` 103-111 行）。版本号一变化，下次请求算出的指纹就不同，新 key 与旧 key 不同，旧条目**逻辑上不可达**，靠 TTL 自然回收。选版本号而非删 key 的原因：不需要枚举/扫描 Redis 里的 key（O(1) 失效）；INCR 原子无并发竞态；失效粒度精确到用户；并且即使 INCR 失败，指纹里本来就有实时角色/权限码，安全性不依赖缓存——版本号是"急切失效"，指纹是"安全底线"。

**Q3：怎么保证投顾/客户经理只能看到被授权客户、无法越权？**
答：三层防线。第一层，路由依赖注入：投顾接口用 `require_advisor_permission` / `advisor_user`，客户经理用 `require_staff_permission("customer:read_all")`，角色+权限不合格直接 403（`app/common/security/auth.py`）。第二层，数据范围：`SubjectScopeService.authorized_subject_ids`（`app/services/subject_scope.py:13-31`）从 `customer_advisor_authorization` 表按 `advisor_id + status=active + 有效期 + revoked_at IS NULL` 算出授权客户 id 集合。第三层，也是最关键的：数据分析时把 SQL 模板的 subject 参数**在服务端改写为授权集合**（`data_analysis_agent.py:136-151`），请求里出现的任何不在授权集合内的客户标识都会被拒绝，客户传什么 id 都绕不过——因为最终执行的查询参数是服务端计算的闭包。此外授权变更会通过 Outbox 事件 bump subject scope 版本号，把可能缓存了旧授权结果的条目全部失效（`advisor_authorization_service.py:80-95`）。

**Q4：如果 Redis 挂了，系统还能保证安全吗？**
答：能，而且这正是设计核心。缓存是纯优化：`get_json` 读缓存任何异常都返回 `None` 然后走实时查询（`permission_scoped_cache.py:79-90`），`set_json` 失败只返回 `False` 不影响业务；授权检查（权限、数据范围）全部来自数据库实时计算，从不依赖缓存。版本号 INCR 失败也只是丢失"急切失效"，指纹仍含实时角色/权限码，安全不变（注释 110、121-122 行）。所以 Redis 故障的代价只是性能下降，不改变任何授权结果。

**Q5：Refresh Token 怎么防重用/防泄露？**
答：三点。一是**只存哈希**：数据库 `refresh_sessions` 只存 `token_hash = sha256(token)`，明文 token 只出现在响应里（`auth_service.py:27, 58-59`）。二是**每次刷新必轮换**：`AuthService.refresh` 把旧行 `revoked_at=now` 再签发新 token（`auth_service.py:68-71`），旧 token 一旦被使用即失效，重放已轮换的旧 token 查不到有效行直接 401——这天然支持**重用检测**（同一 token 出现第二次即可判定异常）。三是**可撤销**：登出/管理操作调用 `revoke` 按哈希置 `revoked_at`（73-76 行）。加上 30 天过期（`settings.refresh_token_expire_days`）缩小暴露窗口。

**Q6：注册时员工和客户为什么角色不同？员工能随便注册成高权限角色吗？**
答：注册端点只分配两种角色：`customer` 或 `employee_pending`（待分组员工），`AuthService.register`（`auth_service.py:42`）。员工注册必须提供 `company_code` 且等于 `settings.employee_registration_code`（36-37 行），否则 400；员工默认落到**零权限**的 `employee_pending`，正式岗位角色只能由管理员通过 `PUT /admin/users/{user_id}/roles` 分配（该接口要求 `admin:write` 权限 + `ensure_admin` 双通道校验，且禁止给自己移除 super_admin，`admin.py:399-417`）。同时 `super_admin` 角色的权限是固定的、不可通过 API 修改（`admin.py:484-485`）。种子数据（演示账号）只在 development/local 且 `demo_accounts_enabled` 时装载，生产环境 `validate_p0` 强制关闭（`settings.py:81-82`）。

**补充问答（加分项）Q7：is_super_admin 和 super_admin 角色有什么区别？**
答：两者都提供管理员能力但机制不同：`is_super_admin` 是 `users` 表上的布尔标志，在依赖注入层直接放行权限校验（`auth.py:25`）；`super_admin` 是拥有全部权限码的角色。`ensure_admin`（`admin.py:32-35`）双通道认可。数据范围内 super_admin 返回空集合表示"无限制"（`subject_scope.py:15-16`），是显式约定而非隐式全放行。系统做了自我保护：不能通过 API 删除自己的 super_admin（`admin.py:402-403`）、不能改 super_admin 角色的权限集（484-485 行）、不能禁用/删除自己（423-424、440-441 行）。

---

## 关键文件索引（答辩时定位用）

| 主题 | 位置 |
|---|---|
| Argon2 哈希 | `app/services/auth_service.py:17-24` |
| 注册/登录/刷新/撤销 | `auth_service.py:35-76`；端点 `app/api/auth.py` |
| JWT 生成 | `auth_service.py:31-34` |
| JWT 校验/权限依赖 | `app/common/security/auth.py:12-66` |
| 表模型 | `app/models/auth.py:6-72` |
| 权限矩阵种子 | `app/services/auth_seed.py:10-91` |
| 矩阵规范 | `docs/RBAC_PERMISSION_MATRIX.md` |
| 管理端 RBAC/用户 | `app/api/admin.py:32-35, 399-493` |
| UoW/仓储 | `app/repositories/unit_of_work.py` |
| Subject Scope | `app/services/subject_scope.py`；授权服务 `app/services/advisor_authorization_service.py` |
| 范围绑定（防越权） | `app/services/data_analysis_agent.py:116-151`；`app/api/customer_service.py:255-264` |
| 权限版本化缓存 | `app/services/permission_scoped_cache.py`；调用方 `app/services/data_catalog_service.py:30-35, 191-192` |
| 配置项 | `app/core/settings.py:42-47`（JWT/refresh/注册码/演示开关） |
