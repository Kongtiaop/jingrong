# 「分层记忆 + 异步 Outbox/Redis Streams/Worker」实现细节简报（面试答辩版）

> 证据均来自仓库源码：`app/`、`workers/`、`docs/`，引用格式为「文件:行号」。

---

## 1. 记忆实现现状

### 1.1 表结构

**`CustomerMemory`（`customer_memory` 表）— `app/models/memory.py:7-23`**

| 字段 | 说明 |
|---|---|
| `id` | String(36) 主键 |
| `customer_id` | FK → `users.id`，CASCADE，索引 |
| `memory_type` | String(32)，默认 `conversation_fact`，索引 |
| `key` | String(128)，索引 |
| `value` | Text |
| `source` | String(32)，默认 `conversation` |
| `confidence` | float，默认 1.0 |
| `status` | String(24)，默认 `active`，索引（支持软失效） |
| `last_confirmed_at` | 时区时间 |
| `expires_at` | 可空——预留过期能力 |
| `created_at` / `updated_at` | 审计 |

关键点：**部分唯一索引 `uq_customer_memory_active_key(customer_id, key) WHERE status='active'`**（memory.py:23）——同一客户同一 key 只允许一条 active 记忆，这是"记忆卡片"语义的数据库层保障。

**`CustomerMemoryEvent`（`customer_memory_event` 表）— `app/models/memory.py:26-35`**：`id`、`customer_id`、`conversation_id`(可空)、`memory_id`(FK，SET NULL)、`event_type`(如 `upsert`)、`payload`(JSON)、`created_at`。作用：**记忆变更的审计/事件留档**，与业务流水解耦，可追溯每条记忆的来源事件。

### 1.2 `CustomerMemoryService` 提供的能力（`app/services/memory_service.py`）

短期记忆（Redis 层）：
- `load_short_term`（:21）— 读 JSON 状态，无则返回 `{}`
- `save_short_term`（:29）— 写 JSON，TTL 默认 1800 秒
- `clear_short_term`（:34）— 会话结束/转人工清理
- `update_short_term`（:58）— 读-改-写合并

长期记忆（PostgreSQL 层）：
- `remember`（:38）— **upsert 语义**：`active` key 已存在则更新 `value/source/confidence/last_confirmed_at`，不存在则插入；**同一事务内**追加一条 `CustomerMemoryEvent(event_type="upsert")`，然后 commit（:39-48）。
- `load_long_term`（:51）— 按 `customer_id` + 可选 `keys` 过滤 `status='active'`，按 `last_confirmed_at` 倒序。
- `remember_confirmed_parameter`（:64）— 便捷方法：`memory_type="confirmed_parameter"`、`source="customer_confirmed"`、`confidence=1.0`（对应"客户明确确认的参数"写入边界）。

### 1.3 「写入/检索/Agent 接入」实现状态

- **写入（长期）**：`remember`/`remember_confirmed_parameter` 已实现 ✅（但无调用方，见下）。
- **检索（长期）**：`load_long_term` 已实现 ✅（同样无调用方）。
- **Agent 接入**：**短期记忆已真实接入两条链路，长期记忆零接入**：
  - `app/api/customer_service.py:164-176`（`/intent-plan` 接口）：`load_short_term` → 用 `original_query`/`clarification`/`missing_parameter` 组合补充后的查询 → `save_short_term` 保存 `intent_plan`/`clarification` 状态。
  - `app/services/customer_agent_execution.py:127-188`（统一 Agent `execute()`）：读短期记忆里的 `handoff` 状态做转人工门控（:129-137）、`clarification_count` 澄清次数、`pending_intent_plan` 复用原问题（:139-142）；澄清阶段写回 `pending_intent_plan`/`clarification_count`（:186-188）；LLM 意图影子模式还读取短期记忆里的 `conversation_summary`（:154）。
  - **证据**：全仓库 grep `remember(|load_long_term|remember_confirmed_parameter` 只有 `memory_service.py` 内部定义，**没有任何业务调用点**。→ 长期记忆是"服务层就绪、链路未接"。

### 1.4 L0-L3 分层：已实现 vs 仅规划

来源：`docs/ARCHITECTURE_OVERVIEW.md:477-489`（2026-08-11「TencentDB Agent Memory 方法论采纳边界」），决策记录在 `docs/DECISIONS.md:2162`（DECISION-20260811-030）。

| 层级 | 对应物 | 状态 | 证据 |
|---|---|---|---|
| **L0 原始流水**（会话、消息、审计记录） | 现有业务表（customer_service_session/message、AuditLog 等） | ✅ **已实现**，作为记忆的可追溯证据，不由模型改写 | ARCHITECTURE_OVERVIEW.md:483 |
| **L1 记忆卡片** | `CustomerMemory` | ✅ **表+服务已实现**；但"P2 收尾升级为版本化卡片"是**规划**——当前表没有 `version`/`valid_from`/`valid_to` 字段，只有 `expires_at`，是"覆盖更新"而非"旧失效+新增"的版本化写入 | memory.py:7-23；DECISIONS.md:2189 |
| **L2 场景档案** | — | ❌ **暂无**（纯规划，"P4 后按真实业务场景评估"） | ARCHITECTURE_OVERVIEW.md:485 |
| **L3 稳定摘要** | — | ❌ **暂无**长期版（纯规划）；仅短期记忆里有 `conversation_summary` 字段供 LLM 影子模式使用 | :486；customer_agent_execution.py:154 |
| Gen3 记忆图谱 | — | ❌ 暂无（P5 可选项，需跨实体追溯需求证明价值） | :487 |

补充规划文档：`docs/AGENT_STRATEGY_EVALUATION_AND_IMPLEMENTATION_ROADMAP_2026-08-14.md` 与 `docs/AGENT_NEXT_STEPS_AND_PROGRESS_2026-08-14.md` 明确 **P3「最小共享工作记忆 MVP」设计已完成、实施未开始**（如 `memory_type/scope/source_agent/permission_context_fingerprint/subject_scope_version/ttl/audit_ref` 等 schema 项均未建表）；决策是"先冻结 P2 无记忆评估基线，再上记忆"。

> 答辩要点：**记忆三件套（短期读写、长期 upsert、事件审计）已实现；Agent 只接了短期；长期记忆与 L2/L3/共享记忆是明确的规划项，且有文档化的分阶段决策**——不要混淆。

---

## 2. Redis 短期记忆 vs PostgreSQL 长期记忆

| 维度 | Redis 短期 | PostgreSQL 长期 |
|---|---|---|
| 存储 | JSON 字符串，key=`customer:memory:short:{customer_id}:{conversation_id}`（memory_service.py:18-19） | `CustomerMemory` + `CustomerMemoryEvent` 两张表 |
| TTL | 1800 秒（30 分钟），`ex=` 设置 | `expires_at` 字段可空，暂无清理逻辑 |
| 内容 | 澄清上下文：`original_query`、`clarification`、`missing_parameter`、`clarification_count`、`pending_intent_plan`、`handoff`、`conversation_summary` | 只允许三类：`confirmed_parameter`（客户确认参数）、`service_preference`（服务偏好）、`conversation_fact`（非敏感会话事实）——见 DECISIONS.md:2189 |
| 用途 | 快速恢复对话上下文、澄清组合、转人工门控（customer_agent_execution.py:128-188） | 跨会话长期事实留档 + 变更审计 |
| 一致性 | 会话状态缓存，允许丢失 | 业务事实库；**业务事实表永远高于记忆**（风险/资产/持仓等不得以记忆为最终依据，ARCHITECTURE_OVERVIEW.md:489） |
| 降级 | `redis=None` 时读写为空操作（graceful）；chaos 测试验证故障**不静默吞异常**（`tests/chaos/test_memory_faults.py`、`test_recovery_contract.py`） | 依赖 PG 事务 |

设计语义（DECISIONS.md:2054 DECISION-20260807-05）：**Redis 是快速恢复上下文的缓存，不是长期事实库**；客服转人工/会话结束/超时会清理短期记忆，但 PG 留档不丢。

---

## 3. Outbox 模式

### 3.1 表结构（`app/models/outbox.py:8-21`）

`outbox_event`：`id`(UUID PK)、`event_type`(索引)、`aggregate_type`、`aggregate_id`(UUID 可空)、`payload_json`(JSONB)、`status`(索引，`pending/published/dead`)、`attempt`(默认0)、`error_message`(Text)、`published_at`、`created_at`/`updated_at`。

### 3.2 事务内写业务 + outbox（已实现，5 个产出方）

所有写入都在**同一个数据库事务**里 commit，保证"业务成功 ⇒ 事件必落库"：
- `app/services/task_service.py:29-52` — `AsyncTask` + `OutboxEvent("task.created")` 一起 `session.add_all` 后 commit。
- `app/services/notification_service.py:49-64` — `enqueue_customer_service_notification` 在客服消息事务内写 `OutboxEvent("notification.customer_service")`，并带 `idempotency_key=event_key`。
- `app/services/advisor_authorization_service.py:88` — `authorization.subject_scope_invalidate`。
- `app/repositories/knowledge.py:42,50` — `knowledge.document_created`。
- `app/repositories/data_catalog.py:78` — `data_catalog.embedding_requested`。

### 3.3 Worker 如何消费（`workers/outbox_worker.py`）

`OutboxPublisher.publish_once`（:31-65）：
1. `SELECT ... WHERE status='pending' ORDER BY created_at LIMIT 20 FOR UPDATE SKIP_LOCKED`（:33-39）→ **行锁+跳过已锁行，多实例并发安全**；
2. 先 `attempt += 1` 并 commit（:43-45）——把"认领"落库，崩溃后不重复计数错乱；
3. 逐个 `queue.publish(stream, payload)`（:58），流按 `event_type` 前缀路由（`_stream_for` :94-109：`risk.`→risk、`memory.`→memory、`knowledge.`→ingestion、`notification.`→notifications、`data_catalog.`→catalog、`authorization.`→authorization、其他→tasks）；
4. 成功 `_mark_published`（:67-78）：`status=published`、`published_at`、把 Redis `message_id` 写回 payload_json；
5. 失败 `_mark_failed`（:80-92）：`attempt >= task_max_attempts(3)` → `status=dead`，否则回 `pending` 等待下轮重试；`error_message` 截断 4000 字符。
6. 主循环（:120-124）：无 pending 时 sleep 1s 再轮询——**outbox_worker 是唯一的 DB 轮询型 worker**。

### 3.4 至少一次投递

- **不丢**：业务与 outbox 同事务（3.2），无分布式事务、无 2PC。
- **至少一次**：`publish` 与 `mark_published` 是**两个独立事务**——若 publish 成功后进程崩溃/`_mark_published` 失败，事件仍为 `pending`，会被重发到 Stream（重复投递）；下游靠幂等消费去重（见 3.5）。

### 3.5 幂等消费（已实现，双层兜底）

`NotificationDeliveryService.deliver_customer_service`（`app/services/notification_delivery.py:16-41`）：
- **先查后插**：`SELECT Notification.id WHERE user_id=? AND idempotency_key=?`，已存在则 `return False`（:23-25）；
- **唯一索引兜底**：`Notification` 表部分唯一索引 `ux_notification_user_idempotency(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL`（notification.py:25）——并发重复插入触发 `IntegrityError` → rollback → `return False`（:37-41）。
- 任务创建侧：`TaskService.create_task` 按 `idempotency_key` 查重直接返回已有任务（task_service.py:24-28）。

### 3.6 重试 / 死信（已实现）

- **Outbox 侧**：attempt/max_attempts 状态机 → `dead` 终态；`/admin/outbox-events?status=dead` 可查询、人工 `retry`（`app/api/admin.py:96,102`，并写审计 `outbox_event_retry`）。`scripts/test_outbox_dead_letter_http.py` 验证了 `forced_outbox_publish_failure → event_becomes_dead`。
- **任务侧**：`runner.mark_failure` 终态时把 `{task_id, task_type, error}` 发布到 `stream:wealth:dead-letter` 流（runner.py:65-69）。
- **流侧**：消费异常不 ack → 消息留在 pending，由 `claim_pending` 恢复（见第 4 节）。

---

## 4. Redis Streams：真实消费组，非轮询

**是，真用 Streams + Consumer Group**（`app/tasks/streams.py`，`RedisStreams` 类）：
- `ensure_group`（:30-35）：`XGROUP CREATE stream group 0 MKSTREAM`，`BUSYGROUP` 幂等。
- `publish`（:37-44）：`XADD ... MAXLEN ~10000`（近似裁剪，控制内存）。
- `consume_once`（:46-62）：`XREADGROUP GROUP g CONSUMER c STREAMS s ">" COUNT 10 BLOCK 5000`——**阻塞式消费**（`task_block_ms=5000`，settings.py:50）。
- `claim_pending`（:64-77）：`XPENDING_RANGE` + 对 `time_since_delivered >= 30000ms` 的消息 `XCLAIM`——**崩溃消费者恢复机制**：worker 挂掉后其未 ack 消息 30 秒后由同组其他消费者接管。
- `ack`（:79-82）：`XACK`。

**消费逻辑位置**：每个流 worker 的 `main()` 循环里统一模式 `claim_pending() + consume_once() → 逐条处理 → ack`（runner.py:99-108、notification_worker.py:28-41、knowledge_worker.py:32-45、authorization_worker.py:48-57）。异常时不 ack，消息留在 PEL（pending entries list）等待 claim 重试。

**分工结论**：`outbox_worker` 轮询 PG outbox 表（关系型可靠投递 + 行锁认领），其余 worker 全部走 Redis Streams 消费组（高性能队列 + 崩溃恢复）。流命名 `stream:wealth:{tasks|notifications|ingestion|authorization|risk|memory|catalog|dead-letter}`，消费组 `wealth-workers[-notifications|-knowledge|-authorization]`（settings.py:16-18）。

---

## 5. Worker 体系

### 5.1 各 worker 职责一览

| Worker | 模式 | 干什么 | 关键依赖 |
|---|---|---|---|
| `runner.py`（tasks worker） | 流消费者 `:tasks` | 通用任务执行器：`mark_started`(queued→running, attempt+1, FOR UPDATE 防并发) → 执行 → `mark_success` / `mark_failure`(重试或 dead，终态发 dead-letter) | `AsyncTask` 表 + RedisStreams |
| `notification_worker.py` | 流消费者 `:notifications` | 只处理 `notification.customer_service`，调 `NotificationDeliveryService` 幂等落库；忽略其他 event_type | notification_delivery.py |
| `knowledge_worker.py` | 流消费者 `:ingestion` | `KnowledgeIngestionService.ingest_document(document_id)`：读文件(plain/docx) → sha256 分块(1000/120) → **按内容哈希复用已有 embedding** → Qwen 批量 embed(4) → 存 chunk → activate_version；失败 `mark_document_failed` | knowledge_ingestion.py:34-99 |
| `authorization_worker.py` | 流消费者 `:authorization` | 处理 `authorization.subject_scope_invalidate` → `bump_subject_scope_version(redis, advisor_id)` 权限缓存版本+1 使 subject 缓存失效 | permission_scoped_cache.py:114 |
| `aml_scheduler.py` | 定时轮询（非流） | 每日 AML 扫描：UTC 时/分匹配配置才执行 `DailyAmlScanService.run`；`AmlSchedulerState` 心跳/状态留档；slot 去重保证一个 UTC 分钟最多跑一次；`AML_SCHEDULER_ENABLED` 开关 | aml_scheduler.py:17-63 |
| `customer_service_timeout_worker.py` | 定时轮询（非流） | `CustomerServiceTimeoutService.sweep`：心跳超时(human_active→paused)、等待超时(waiting_human→expired)；开关+轮询间隔配置 | customer_service_timeout_worker.py:15-30 |

### 5.2 任务类型（task_type）与状态机

- `AsyncTask`（`app/models/task.py:8-26`）：`task_type`、`aggregate_type/id`、`idempotency_key`(unique)、`status`、`attempt`、`max_attempts`(默认3)、`payload_json`/`result_json`/`error_message`、`available_at`(延迟任务预留)、`started_at`/`finished_at`。
- **状态机**：`queued → running → success`；`running → failed`（attempt < max_attempts，可再次入队重试）→ `running → dead`（attempt ≥ max_attempts，终态）；`cancelled` 终态（runner.py:33,79 跳过终态）。
- **注意（答辩加分点）**：`TaskExecutor.execute`（runner.py:72-85）目前是**占位/骨架实现**——不按 `task_type` 分发到具体业务 handler，而是直接 `mark_success` 返回 `{"worker":"p0", task_type, message_id, payload}`。也就是说**通用任务管道（表/状态机/重试/死信）已实现，但还没有真实业务任务注册进这个管道**；真实业务异步化走的是 outbox 事件 + 各专用流 worker。

### 5.3 runner.py 如何启动 worker

没有统一进程管理器（无 supervisord/docker-compose 证据）；每个 worker 都是**独立进程**：`if __name__ == "__main__": asyncio.run(main())`。`main()` 统一模板：`get_settings()` → `configure_logging()` → `Database(settings)` + `RedisStreams(settings)` → `ensure_group` → `while True: claim_pending + consume_once + 处理 + ack`，`finally: queue.close() + database.dispose()`。

---

## 6. 设计取舍：为什么 outbox+worker 而不是同步写

1. **主链路延迟**：同步调用会拖慢 HTTP 响应——Qwen embedding（knowledge）、跨组件通知、权限缓存失效都是慢/有外部依赖的副作用；异步化让"发客服消息/提交任务"等请求快速返回（ARCHITECTURE_DATABASE_DRAFT.md:1148「对话主链路优先返回，记忆提取通过 Redis Streams 异步处理」）。
2. **可靠投递、不要分布式事务**：业务行与 outbox 行同本地事务原子提交 ⇒ 业务成功事件必达；避免跨 DB/跨 Redis 的 2PC。Redis Pub/Sub 会丢消息（无持久化），Streams + 消费组才有持久化+重放+崩溃恢复。
3. **至少一次 + 幂等消费兜底**：重复是不可避免的（publish 与 mark 分事务），用业务幂等键 + 唯一索引把"恰好一次"降级为"至少一次 + 幂等"，这是分布式系统的标准取舍。
4. **解耦与独立伸缩**：每个 worker 独立进程、独立消费组、独立扩缩容；`event_type` 前缀路由决定走哪个流，新增消费者不改生产者。
5. **可审计、可重放、可人工干预**：outbox 表本身就是投递审计（status/attempt/error/published_at），admin 可查 dead 并人工重试；dead-letter 流给运维兜底。
6. **崩溃恢复**：DB 侧 `FOR UPDATE SKIP_LOCKED` 认领，Redis 侧 `XPENDING/XCLAIM` 30s 超时接管，两侧都有明确的恢复语义。

---

## 7. 面试高频问答（5-6 个，基于代码、区分已实现/规划）

**Q1：你的记忆体系分了几层？哪些真正落地了？**
答：物理上是两层——Redis 短期会话记忆（key=`customer:memory:short:{customer_id}:{conversation_id}`，TTL 30 分钟，存澄清上下文/待执行计划/转人工状态）和 PostgreSQL 长期记忆（`CustomerMemory` 记忆卡片 + `CustomerMemoryEvent` 事件审计，单事务 upsert，部分唯一索引保证同一客户同一 key 只有一条 active 记忆）。方法论上参照 TencentDB Agent Memory 的 L0-L3：L0 原始流水（会话/消息/审计，已有业务表）和 L1 记忆卡片（CustomerMemory 表+服务）已实现；L2 场景档案、L3 稳定摘要、Gen3 记忆图谱都明确是规划项（见 ARCHITECTURE_OVERVIEW.md 的采纳边界表），并且有文档化的分阶段决策：P3 最小共享工作记忆 MVP 设计完成、实施未开始。要特别说明：长期记忆的写入/检索服务已实现，但**当前 Agent 链路只接入了短期记忆**——长期记忆是"服务就绪、链路未接"。

**Q2：短期记忆和长期记忆各自解决什么问题？为什么不能只用一个？**
答：短期记忆解决**同一会话内的上下文连续性**——澄清补参（original_query + clarification + missing_parameter 组合查询）、澄清次数控制（超过 2 次转人工）、pending_intent_plan 复用、转人工 handoff 状态门控；要快、要随会话生命周期清理，所以用 Redis + TTL。长期记忆解决**跨会话的客户事实留档**——只写客户明确确认的参数、服务偏好和非敏感会话事实，且带事件审计可追溯；因为金融场景里业务事实表永远高于记忆（风险/资产/持仓不能以记忆为最终依据），所以长期记忆是受控的、带确认边界的 PG 表。一个管"这一轮聊到哪"，一个管"这个客户已知什么"，职责不同、生命周期不同。

**Q3：Outbox 怎么保证"业务成功事件必达、且不丢"？**
答：三个机制。第一，**同事务写入**：业务行和 `OutboxEvent` 在同一个 SQLAlchemy session 里 `session.add_all` 后一次 commit（task_service.py:51、notification_service.py:49-64），业务成功事件必落库，不需要分布式事务。第二，**可靠的发布者**：`outbox_worker` 用 `SELECT ... FOR UPDATE SKIP_LOCKED` 认领 pending 事件（多实例并发安全），先 attempt+1 落库再逐个发布到 Redis Stream，成功才标记 published 并回写 redis_message_id。第三，**至少一次 + 幂等兜底**：publish 和标记 published 是两个事务，进程崩溃可能重复投递，但下游消费者用幂等键先查后插 + 数据库唯一索引兜底（notification_delivery.py:23-41），重复投递不会产生重复业务数据。

**Q4：失败重试和死信是怎么设计的？**
答：两级。**Outbox 层**：事件失败时 `_mark_failed`，attempt 达到 `task_max_attempts`(默认3) 置 `dead`，否则回 pending 下轮重试；dead 事件可在 `/admin/outbox-events` 查询并人工 retry（写审计）。**任务/流层**：AsyncTask 状态机 queued→running→success/failed/dead，failed 可再次入队，终态时把 `{task_id, task_type, error}` 发布到 `stream:wealth:dead-letter` 死信流；Redis Stream 消费失败不 ack，消息留在 pending，30 秒后由 `XCLAIM` 被同组其他消费者接管——这是流侧的"崩溃恢复"而非普通重试，配合业务幂等保证安全。

**Q5：Redis Streams 消费是怎么做的？和简单轮询有什么区别？**
答：用的是标准消费组模式（app/tasks/streams.py）：`XGROUP CREATE ... MKSTREAM` 建组，`XREADGROUP ... COUNT 10 BLOCK 5000` 阻塞式拉取（不是空转轮询），处理完 `XACK`。区别在于：消费组能把消息按组负载均衡给多个消费者；未 ack 的消息留在 Pending Entries List，消费者崩溃后 `XPENDING_RANGE + XCLAIM(min_idle=30000)` 由其他消费者接管，配合 `MAXLEN ~10000` 限制流长度。全系统只有 `outbox_worker` 是 DB 轮询——那是因为 outbox 表本身就是关系型持久化队列，需要行锁认领语义，它把事件搬运到 Redis Stream 后就由各领域 worker 阻塞消费。

**Q6：为什么引入这一整套异步机制，而不直接在请求里同步调 embedding/通知？**
答：三点。一是**延迟**：Qwen embedding 等调用很慢，同步会拖垮客服发消息等主链路接口，主链路应快速返回。二是**可靠性**：同步调用失败会污染业务事务（要么重试污染、要么丢事件），Redis Pub/Sub 又不持久化会丢消息；outbox 同事务保证不丢，Streams 持久化+重放保证可达，幂等键保证不乱。三是**解耦与可运维**：每个 worker 独立进程独立伸缩，事件路由按前缀分流，outbox 表自带投递审计，dead 可人工重试——这是财富管理这种对审计、可追溯要求高的场景的正确取舍。

**Q7（备选）：任务状态机里为什么 running 之前要 FOR UPDATE？available_at 是干什么的？**
答：`mark_started` 用 `SELECT ... FOR UPDATE` 锁行后再改状态，防止两个消费者同时执行同一任务（消息重复投递时兜底）；终态（success/dead/cancelled）直接跳过，保证状态机单向推进。`available_at` 是延迟任务的预留字段（到点才可执行），当前 runner 由流消息驱动、未显式使用它，属于"表结构已支持、调度语义待接入"。

---

## 附：最容易踩的"已实现 vs 规划"对照表（答辩前自检）

| 能力 | 状态 | 一句话证据 |
|---|---|---|
| 短期记忆读写 | ✅ 已实现+已接入 | customer_service.py:164-176；customer_agent_execution.py:127-188 |
| 长期记忆表+服务 | ✅ 已实现 | memory.py；memory_service.py:38-65 |
| 长期记忆接入 Agent | ❌ 未接入 | grep 无调用方 |
| L0/L1 | ✅ 落地 | ARCHITECTURE_OVERVIEW.md:483-484 |
| L2/L3/Gen3 | ❌ 纯规划 | ARCHITECTURE_OVERVIEW.md:485-487 |
| 版本化记忆卡片（valid_from/valid_to） | ❌ 规划 | DECISIONS.md:2189 |
| Outbox 事务写入 | ✅ 5 个产出方 | task/notification/authorization/knowledge/data_catalog |
| Outbox 至少一次+幂等+死信 | ✅ | outbox_worker.py；notification_delivery.py；admin.py |
| Redis Streams 消费组+claim | ✅ | streams.py:46-77 |
| 通用任务管道（AsyncTask 状态机/重试/死信） | ✅ 管道就绪，业务分发为占位 | runner.py:72-85（不按 task_type 分发） |
| 记忆/风险流（stream:wealth:memory、:risk） | ⚠️ 路由已写、暂无生产者 | outbox_worker.py:95-97 |
