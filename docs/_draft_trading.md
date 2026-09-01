# 自然语言下单闭环 + 交易状态机 + 幂等 —— 实现细节简报

以下结论全部来自实际代码，标注 `文件:行号` 可对照源码。

## 1. 【自然语言下单完整链路】

### 1.1 总览：真正的闭环是"四步"而非"两步"

```
① NL 草稿创建(不建单/不冻结) → ② 草稿确认(token, 幂等建单, 仍不冻结) → ③ 订单客户确认(冻结资金) → ④ 员工复核(执行/拒绝)
```

关键事实（容易被面试官追问）：**草稿确认 ≠ 资金冻结**。`draft.confirm()` 只是用 `create_order()` 生成一条 `status=pending_confirmation` 的订单（`confirmation_mode="nl_draft"`），真正扣可用余额/加冻结额发生在标准的 `POST /trading/orders/{order_id}/confirm`（`confirm_order()`，`trading_service.py:85-105`）。NL 链路在资金动作上完全复用普通订单流程。

### 1.2 各环节实现位置

**① 草稿创建** — `POST /trading/order-drafts/natural-language`（`app/api/trading.py:51-63`）

- 入口校验：当前用户必须带 `customer` 角色（`:54-55`）。
- 调用 `NaturalLanguageOrderDraftService.draft()`（`natural_language_order_draft.py:121-220`），内部顺序：
  1. 归一化空白；命中 `UNSUPPORTED_MARKERS`（赎回/卖出/转换/转出）→ `status="needs_clarification"`，明确提示 MVP 仅支持申购（`:123-128`）。
  2. 无 `BUY_MARKERS`（申购/购买/买入/下单）→ 同样 `needs_clarification`（`:129-134`）。
  3. `parse_purchase_intent()`（`:51-82`）：优先取引号内产品名（支持 `“ ”` 和 `" "`）；否则从动作词后的文本中截取产品名并剔除金额 token，或取动作词前的文本；金额用正则 `([0-9]+(?:\.[0-9]+)?)(万|元)`，`万` 乘以 10000，用 `Decimal` 保证精度。**系统从不猜测缺失参数**：缺产品/金额时返回 `needs_clarification + missing_fields`，提示语为"关键交易参数必须由客户明确提供，系统不会猜测"（`:137-154`）。
  4. 查在售产品（`deleted_at IS NULL AND status='active'`），按去空格名称精确匹配；无匹配/多匹配 → `needs_clarification + product_options`（最多 5 个候选，`:156-177`）。
  5. 金额低于 `product.minimum_amount` → `precheck_failed`（`:181-187`）。
  6. `get_or_create_account()`（`trading_service.py:29-37`）：无账户自动创建默认余额 200000.00 的账户（`:36`）；账户非 active 或 `risk_restriction_status=="restricted"` → `precheck_failed`（`draft.py:189-195`）；`available_balance < amount` → `precheck_failed`（`:196-201`）。
  7. 适当性预检：`suitability_service.recommend()`，按 `product_id` 找到该产品的 `SuitabilityResult`，`suitable = item.matched`，收集 reasons；不匹配 → `precheck_failed`（`:203-213`）。
  8. 全部通过 → `_persist_confirmable_draft()`（`:93-119`）。

**草稿如何持久化**（`_persist_confirmable_draft`，`natural_language_order_draft.py:93-119`）：

- 生成 `confirmation_token = secrets.token_urlsafe(32)`，`expires_at = now + 15 分钟`，`version = 1`。
- 计算 `input_fingerprint = sha256(f"{customer_id}|{product_id}|buy|{amount:.2f}|{version}")`（`:89-91`）。
- 把预检结果整体快照成 JSON 存入 `precheck_snapshot_json`（含 product_id/name、amount、side、suitable、suitability_reasons、warnings、precheck_passed）——用于审计与确认页展示的留痕。
- 写入 `NaturalLanguageOrderDraft` 表：`status="awaiting_user_confirmation"`、`confirmation_token_hash=sha256(token)`（**DB 只存 hash，明文 token 仅此一次随响应返回**）、`expires_at`、`input_fingerprint`（模型见 `models/trading.py:41-57`）。
- 写 `AuditLog(action="create_draft")` 后 `flush`（`:115-116`），由 API 层统一 `commit`（`api/trading.py:60`）。
- 响应模型 `NaturalLanguageOrderDraftPersistedResponse` 额外携带 `draft_id/version/confirmation_token/expires_at`（`schemas/trading.py:91-95`）。

**② 草稿确认（幂等建单）** — `POST /trading/order-drafts/{draft_id}/confirm`（`api/trading.py:66-85`）→ `draft.confirm()`（`natural_language_order_draft.py:222-273`）：

1. `SELECT ... FOR UPDATE` 按 `draft_id + customer_id` 行级锁（`:225-230`）——防并发双击。
2. **幂等短路**：`draft.order_id` 已存在 → 直接返回已建订单（`:233-236`）。
3. 状态守卫：`status != "awaiting_user_confirmation"` → `ValueError("draft cannot be confirmed")`（`:237-238`）。
4. 过期校验：`expires_at < now` → 置 `status="expired"` 并抛错（`:239-245`）。
5. **令牌校验**：`secrets.compare_digest(sha256(payload.confirmation_token), draft.confirmation_token_hash)`（`:246-247`）——常量时间比较，防时序攻击。
6. **确认时重跑全部确定性控制（TOCTOU 防护）**：产品仍 active、账户状态/风控限制、可用余额、`suitability_service.recommend()` 该产品仍 matched（`:249-261`）。
7. 组装幂等键：`idempotency_key = payload.idempotency_key or f"nl-draft:{draft.id}:v{draft.version}"`（`:263`）——**默认键由草稿 id + version 确定性生成**，同一草稿永远映射同一键。
8. `trading_service.create_order(..., order_source="customer_direct", confirmation_mode="nl_draft")`（`:264-267`）。
9. 回写 `draft.order_id`、`status="confirmed_order_created"`、`confirmed_at`，写 `AuditLog(action="confirm_draft")`（`:268-272`）。

错误映射（`api/trading.py:78-83`）：`draft not found`→404 `DRAFT_NOT_FOUND`；`invalid draft confirmation token`→403 `DRAFT_CONFIRMATION_INVALID`；其余 `ValueError`→409 `DRAFT_CONFIRMATION_CONFLICT`。

**③ 订单客户确认（资金冻结）** — `POST /trading/orders/{order_id}/confirm`（`api/trading.py:109-120`）→ `confirm_order()`（`trading_service.py:85-105`）：

- 订单行 `FOR UPDATE`（`:86`）；状态必须 `pending_confirmation`（`:88`）。
- 余额检查（`:90`）；无风险测评则 `ensure_default`（`:92-94`）。
- 客户类型门槛（`:97-99`）与风险等级硬校验（`:100-102`）：`C1..C5=1..5`，`actual < required` 直接拒绝。
- **冻结**：`account.available_balance -= amount; account.frozen_balance += amount`（`:103`），迁移到 `pending_review`。

**④ 员工复核（执行/拒绝）** — `POST /trading/orders/{order_id}/review`（approve，`api/trading.py:140-154`）与 `/reject`（`api/trading.py:156-163`）→ `review_order()`（`trading_service.py:137-189`），详见第 2 节。

**失败/重复提交处理**：过期→`expired` 状态并 409；令牌错→403；重复 confirm→FOR UPDATE + `order_id` 短路 + 确定性幂等键三者共同保证"同一草稿只建一单、返回同一订单"；重复 `POST /orders` 同 key→命中 `get_by_idempotency` 返回旧订单，并发竞态由 DB 唯一索引兜底（见第 4 节）。

## 2. 【交易状态机】

### 2.1 状态全集与迁移约束

`Order.status` 默认 `"pending_confirmation"`（`models/trading.py:30`）。唯一权威迁移表在 `transition()`（`trading_service.py:67-83`）：

```
pending_confirmation → {pending_review, cancelled}
pending_review       → {executing, rejected, cancelled}
executing            → {executed}
executed / rejected / cancelled → ∅（终态，不可再迁移）
```

- 同状态迁移、未列出的迁移一律抛 `TradingError`：同状态报 `"order state transition is not allowed"`，非法迁移报 `"invalid order state transition: {old} -> {to}"`（`:77-80`）。
- 观察点：`rejected` 只能从 `pending_review` 来（客户确认后由员工拒绝），客户自己在 `pending_confirmation` 只能 `cancelled`；`pending_confirmation` 不能直达 `executing`——强制经过人工复核。
- API 层把状态冲突统一映射为 409 `ORDER_STATE_CONFLICT`（`api/trading.py:26-27`）。

### 2.2 order_status_history 如何记录

- 建单即写首条历史：`OrderStatusHistory(from_status=None, to_status="pending_confirmation", operator_user_id=user.id, note="customer created order")`（`trading_service.py:64`）。
- 每次 `transition()` 都追加一条 `(from_status, to_status, operator_user_id, note)`（`:83`），模型 `models/trading.py:71-79`，仓库 `SqlAlchemyStatusHistoryRepository.save/list_for_order`（`repositories/trading.py:168-170`）。
- API 响应 `OrderResponse.history` 按 `created_at` 升序返回完整迁移链（`api/trading.py:40`），可审计"谁在何时把订单从哪移到哪、备注是什么"。

### 2.3 资金冻结（frozen_balance）与持仓更新

- **冻结（客户确认）**：`confirm_order()` 用账户行锁后 `available -= amount; frozen += amount`（`trading_service.py:89,103`），迁 `pending_review`。
- **解冻退款（取消）**：`cancel_order()` 仅允许 `pending_confirmation/pending_review`；`pending_review + customer_direct` → `available += amount; frozen -= amount`（`:114-116`）；`advisor_assisted` 单在客户确认阶段未冻结，故取消不退（注释 `:118-119`）。余额不一致抛 `"order frozen balance is inconsistent"`。
- **拒绝（员工 reject）**：`customer_direct` 解冻退款并迁 `rejected`、写 `failure_reason`（`:148-160`）；advisor 单因未冻结直接拒绝。
- **执行（员工 approve）**，`review_order()`（`:161-188`）按序：
  1. `frozen_balance >= amount` → 扣 `frozen`（正式扣款）；否则视为 advisor 未冻结路径，改为扣 `available`（`:161-169`）。
  2. `update_balances` 落库 → 迁 `executing`（`:170`）。
  3. **持仓 upsert**：无活跃持仓则新建 `CustomerHolding(quantity=order.quantity, cost_amount=order.amount, market_value=order.amount, profit_loss=0, holding_days=0)`；已有则 `quantity/cost_amount/market_value` 累加（`:172-178`）。
  4. **幂等建 Trade**：`existing_trade is None` 才创建（`:179-181`）→ 迁 `executed`（`:182`）。
  5. 资产快照 `asset_summary_service.snapshot(source_type="trade_derived")`（`:183`）。
  6. **幂等建 MoneyFlow**（cash/out，`:184-187`）→ `RiskAlertService.evaluate_order` 做交易后风控（`:188`）。
  - Trade 与 MoneyFlow 的"先查后插"是执行阶段防重入的第二道保险。
- **风控闸门**：`_blocking_risk_alert()`（`:123-134`）——存在 `open/acknowledged/investigating/pending_customer/report_pending` 状态、且关联该订单（或未关联订单但属于该客户）的预警时，员工审核直接被拦（`:141-143`），映射 409 `RISK_CONTROL_BLOCKED`（`api/trading.py:28-29`）；审核端点还会先 `evaluate_pending_order` 补扫（`api/trading.py:146-151`）。

## 3. 【适当性校验 SuitabilityService】

### 3.1 输入装配（`recommend()`，`suitability_service.py:36-55`）

1. `profile_repository.get`（客户资料，可空）；`asset_service.latest_or_derived`（资产快照）；`product_repository.list_active`；`risk_repository.get_active`（无则 `RiskAssessmentService.ensure_default`，`:40-42`）。
2. 加载 `ProductSuitabilityRule` 并按 product_id 分组（`:44-53`）。
3. 风控状态 → 警告码：`RISK_RESTRICTION_ACTIVE` / `RISK_ALERT_MONITORING`；`risk.status != "active"` → `RISK_ASSESSMENT_NOT_ACTIVE`；无资产快照 → `ASSET_SNAPSHOT_MISSING`（资产门槛按 0 算）（`:59-66`）。

### 3.2 逐产品判定（`:68-117`）

- 等级数值化：`LEVEL = {C1:1 ... C5:5}`、`RISK = {R1:1 ... R5:5}`，`_level()` 兜底默认 1（`:20-26`）。
- 每个产品计算三个门槛：
  - `required_level = max(product.risk_level, 各 rule.minimum_risk_level...)`
  - `required_investable_asset = max(product.minimum_amount, 各 rule.minimum_investable_asset...)`
  - `required_investor_types = {rule.investor_type 且 != "all"}`；`target_customer_type = product.target_customer_type or "individual"`
- **否决逻辑（任一不满足 matched=False 并记录中文 reasons）**：
  a. `required_level > customer_level` → "产品要求风险等级不高于 C{required_level}，高于当前客户风险等级"（`:79-81`）；
  b. 客户类型不满足 `target_customer_type` 或不在 `required_investor_types` 内（`:85-90`）；
  c. `investable_asset < required_investable_asset`（可投资资产门槛，非总资产）（`:94-96`）。
- 细节：`restricted` 只追加 reason 与 warning，**不直接置 matched=False**；提交拦截发生在草稿/确认的账户级检查（`draft.py:190`、`confirm()` `:254`），即"查看可用、下单不可用"。
- 输出 `RecommendationResponse(matches / excluded / context / warnings)`，`context` 含 risk_level、risk_assessment_status、total/investable asset、risk_operation（`:119-131`）。

### 3.3 与下单的两处衔接

- 草稿预检：`draft()` 用 `recommend()` 结果判定 `suitable`（`natural_language_order_draft.py:203-213`）。
- 订单确认终检：`confirm_order()` 再独立做一次等级数值比较 `actual < required` 拒绝（`trading_service.py:100-102`）——**与 SuitabilityService 是两套实现，互为兜底**；草稿确认时也会重跑 `recommend()`（`natural_language_order_draft.py:258-261`）。

## 4. 【幂等】

### 4.1 结论：订单幂等用**数据库**，Redis 组件存在但未接线

- `app/common/idempotency/store.py` 的 `IdempotencyStore`（Redis）：`key = sha256(idempotency_key)` → `"idempotency:{scope}:{digest}"`，TTL 86400s，`get/set` 存 JSON（`store.py:15-25`）。全仓 grep 仅见定义与导出（`common/idempotency/__init__.py`），**交易链路没有调用它**——答辩时如实说明：它是通用幂等组件，订单模块选用了更强的 DB 方案。
- **DB 方案**：
  1. `Order.idempotency_key VARCHAR(128)` 可空、索引（`models/trading.py:34`）。
  2. 命中查询：`get_by_idempotency(user_id, key)` 取最新一条（`repositories/trading.py:131`）。
  3. 业务短路：`create_order()` 若 key 存在且命中 → **直接返回该订单**（`trading_service.py:48-52`），不重复建单、不重复校验后续分支。
  4. **并发竞态兜底**：唯一部分索引 `ux_orders_user_idempotency_key ON orders(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL`（`db/schema.py:95`，建索引前先清理历史重复行保留最小 id，`:87-94`）。两个并发请求都 miss 预查时，后插入者撞唯一索引抛 `IntegrityError` → `map_database_error()` 映射为 `DatabaseConflictError("idempotency key already used", code="IDEMPOTENCY_CONFLICT")`（`common/exceptions/base.py:37-38`）→ `api/trading.py:96-98` 转 409。
- **重复请求返回同一结果**：命中 key 返回既有订单（状态可能仍是 `pending_confirmation` 或已推进），不产生第二次资金动作。
- **草稿确认的幂等键**：`payload.idempotency_key or f"nl-draft:{draft.id}:v{draft.version}"`（`natural_language_order_draft.py:263`）——确定性生成；再叠加三道防线：`draft.order_id` 短路（`:233-236`）、草稿行 `FOR UPDATE`（`:229`）、状态守卫（`:237-238`）。
- 执行阶段防重入：Trade 与 MoneyFlow 均"先查后插"（`trading_service.py:179-187`）。

## 5. 【设计取舍：AI 为什么不直接下单】

代码里的直接证据：

1. **服务文档字符串自证**：`NaturalLanguageOrderDraftService` 注释 "Create a non-executable purchase draft... It never creates an Order, freezes funds, or performs a state transition."（`natural_language_order_draft.py:34-37`）——刻意设计为"非可执行草稿"。
2. **响应模型固化语义**：`confirmation_required: bool = True`、`executed: bool = False`（`schemas/trading.py:87-88`），协议层就保证草稿永不执行。
3. **每个分支都在喊话**："本次不会创建订单或执行交易"（`draft.py:128/133/154/168/176/180`）。
4. **不猜测原则**：缺产品/金额只回 `needs_clarification`，"关键交易参数必须由客户明确提供，系统不会猜测"（`:153`）——AI 解析不确定性被显式隔离在"提案"层，不给资金动作授权。
5. **确认令牌 = 客户签名的证据**：15 分钟有效、一次性、DB 只存 SHA-256、常量时间比较；配合 `precheck_snapshot_json` 快照，客户确认的就是"当时看到的那份产品+金额+适当性结论"。
6. **确认时重跑确定性控制**（`:249-261`）：草稿与确认之间存在时间差，防止 TOCTOU（产品下架/余额变化/适当性变化）。
7. **人审二次闸门**：即使客户确认，订单仍停在 `pending_review` 等员工复核，复核前还有 `_blocking_risk_alert` 风控闸门（`trading_service.py:141-143`）——AI 到执行之间隔着"客户显式确认 + 员工复核"两道人工。
8. **可审计**：`AuditLog(create_draft/confirm_draft)`、`input_fingerprint`、`OrderStatusHistory` 全链路留痕。
9. **工程收益**：草稿层可做多轮澄清（候选产品列表）、可过期、可幂等确认；把"理解用户意图"与"执行资金动作"解耦，各自独立演进。

## 6. 【面试高频问答（含代码级答案）】

**Q1：AI 为什么不能直接下单？**
答：代码层面 `NaturalLanguageOrderDraftService` 的类注释明确写 "never creates an Order, freezes funds, or performs a state transition"（`natural_language_order_draft.py:34-37`），响应模型固定 `confirmation_required=True, executed=False`（`schemas/trading.py:87-88`）。设计上：①AI 解析有不确定性，代码用"缺字段只澄清、绝不猜测"（`:153`）把不确定性挡在提案层；②草稿与确认之间隔 15 分钟一次性确认令牌，客户确认的是 `precheck_snapshot_json` 里的快照内容；③确认时重跑产品/余额/适当性校验防 TOCTOU（`:249-261`）；④即使客户确认，订单仍停在 `pending_review` 等员工复核，且复核有风控预警闸门 `_blocking_risk_alert`（`trading_service.py:141-143`）。所以 AI 只负责"生成提案"，资金动作永远由显式确认+人工复核触发。

**Q2：确认令牌怎么生成、怎么校验、为什么不存明文？**
答：生成用 `secrets.token_urlsafe(32)`，有效期 15 分钟，草稿行写入 `confirmation_token_hash = sha256(token)`（`natural_language_order_draft.py:94-95,111`），明文 token 只在创建响应里返回一次（`_persist_confirmable_draft` 返回 `NaturalLanguageOrderDraftPersistedResponse.confirmation_token`）。校验时把用户提交的 token 再做一次 SHA-256，用 `secrets.compare_digest` 与库里 hash 常量时间比较（`:246-247`），防时序攻击；DB 泄库也拿不到可用令牌。同时草稿按 `customer_id` 归属校验（`:225-229`），令牌无法跨用户使用。

**Q3：用户连点两次"确认"，会不会建两个订单？**
答：不会，四层防护。①草稿行 `SELECT ... FOR UPDATE` 行锁，第二个并发请求会等第一个提交，然后看到状态已不是 `awaiting_user_confirmation` 而报 `draft cannot be confirmed`（`:225-238`）；②`draft.order_id` 已存在则直接返回既有订单，这是幂等短路（`:233-236`）；③默认幂等键 `f"nl-draft:{draft.id}:v{draft.version}"` 确定性生成（`:263`），`create_order` 内部 `get_by_idempotency` 命中即返回旧单（`trading_service.py:48-52`）；④最坏并发下唯一部分索引 `ux_orders_user_idempotency_key(user_id, idempotency_key)`（`db/schema.py:95`）兜底，后插入者抛 IntegrityError 映射成 409 `IDEMPOTENCY_CONFLICT`（`api/trading.py:96-98`）。执行阶段 Trade/MoneyFlow 也有先查后插（`trading_service.py:179-187`）。

**Q4：幂等用 Redis 还是数据库？并发怎么防重？**
答：订单模块用的是**数据库幂等**。`app/common/idempotency/store.py` 里确实有 Redis 版 `IdempotencyStore`（key 为 `idempotency:{scope}:{sha256(key)}`，TTL 86400s），但它只是通用组件，交易接口并未接线；订单用 `Order.idempotency_key` 列 + `get_by_idempotency(user_id, key)` 预查 + 唯一部分索引兜底。防竞态的要点：预查只是优化路径，真正保证"只成功一次"的是唯一索引；并发双写时后者抛 IntegrityError，被 `map_database_error` 统一映射为 `IDEMPOTENCY_CONFLICT` 409（`common/exceptions/base.py:37-38`），客户端重试同一 key 会命中既有订单拿到相同结果。

**Q5：状态机有哪些状态？为什么 pending_confirmation 不能直接到 executing？**
答：状态为 `pending_confirmation → pending_review → executing → executed`，另有 `cancelled`、`rejected` 两个终态分支（`trading_service.py:67-75`）：`pending_confirmation` 可去 `pending_review` 或 `cancelled`；`pending_review` 可去 `executing/rejected/cancelled`；`executed/rejected/cancelled` 是终态；同状态或非法迁移都抛 `TradingError`。不能直达 executing 的原因：资金冻结发生在 `confirm_order`（`available -= amount; frozen += amount`，`:103`），而执行前的放行权在员工——`review_order` 先过风控预警闸门 `_blocking_risk_alert`（`:141-143`，open/acknowledged/investigating/pending_customer/report_pending 任一未结预警即拦截，返回 409 `RISK_CONTROL_BLOCKED`），再执行解冻→`executing`→持仓/成交/资金流水→`executed`。也就是说"客户确认+员工复核"是硬性两步，任何一步缺了都到不了终态。

**Q6：冻结/解冻和持仓更新的时序如何保证一致？**
答：全程在同一个 `AsyncSession` 事务里，API 层统一 `commit`（`api/trading.py:152`），任何一步抛错整单回滚。冻结和解冻都先对账户行 `FOR UPDATE`（`trading_service.py:89,145,169`）再改 `available_balance/frozen_balance`；执行路径顺序为：扣冻结（或扣 available）→ 迁移 `executing` → 持仓 upsert（无则新建、有则 quantity/cost/market_value 累加，`:172-178`）→ 幂等建 Trade → 迁移 `executed` → 资产快照 `trade_derived` → 幂等建 MoneyFlow（cash/out）→ 交易后风控评估。解冻方向（cancel/reject）只回补余额、不动持仓，并校验 `frozen_balance >= amount` 防止负冻结（`:115,150`）。余额字段用 `Numeric(18,2)`，金额用 `Decimal`，杜绝浮点误差。

**Q7（加分项）：草稿确认和订单确认有什么区别？**
答：草稿确认（`POST /order-drafts/{id}/confirm`）只做"令牌校验 + 重跑预检 + 幂等建单"，产出 `pending_confirmation` 订单，**不冻结资金**；订单确认（`POST /orders/{id}/confirm`，`confirm_order`）才做资金冻结并把订单推入 `pending_review` 等员工复核。两段确认之间允许客户反悔（草稿过期或直接取消订单）。这是"提案-确认-执行"三段式里确认与执行分离的体现。

---

### 附：本次核实的补充文件（超出任务清单但用于佐证）
- `app/schemas/trading.py`（确认请求/响应模型、`confirmation_required=True`、`executed=False`）
- `app/db/schema.py:87-96`（重复数据清理 + 唯一部分索引）
- `app/common/exceptions/base.py:33-41`（`IntegrityError → IDEMPOTENCY_CONFLICT` 映射）
- `app/common/dependencies/request.py`（Redis/DB 依赖注入方式）
- 全仓 grep 确认 `IdempotencyStore` 仅在 `common/idempotency/` 定义与导出，交易接口未接线

一点诚实提示：若面试官问"Redis 幂等在哪用"，正确答法是"`app/common/idempotency/store.py` 提供了 Redis 幂等存储组件，但当前订单链路实际用的是 PostgreSQL 唯一索引方案，Redis 组件供其他需要快速响应缓存的场景复用"——不要声称交易接口用了 Redis。
