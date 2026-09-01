# 多 Agent 编排 + 安全边界 模块实现细节简报

> 说明：本简报基于对 14 个指定文件的逐行精读，并交叉核对了 `app/schemas/intent.py`、`app/schemas/agents.py`、`app/core/settings.py`、`app/services/nl2sql_policy.py`、`app/services/customer_rag_handoff.py`、`app/api/customer_service.py` 中与本模块相关的调用点。文件路径统一简写为相对 `app/` 的形式；代码证据均标注 `文件:行号`。

---

## 1. 【真实实现流程】Agent 请求从进入到返回的完整链路

### 1.1 入口（真实、已接线）

API 层 `app/api/customer_service.py` 有三个调用点，全部汇聚到同一个服务：

- `POST /agent/execute`：`execute_unified_agent`（约 `app/api/customer_service.py:188`）——**这是唯一带能力预检的入口**：先 `AgentCapabilityRegistry().available_for(user)`（`app/api/customer_service.py:191-193`），若用户可用 `DataAnalysisAgent` 则先做 `analyzer.evaluate_query_safety(payload.content)` 输入安全评估。
- 普通 chat 接口（`app/api/customer_service.py:336`）、备注接口（`:360`）、WebSocket（`:404`）：直接调用 `CustomerAgentExecutionService(database, redis, settings=...).execute(customer_id=user.id, conversation_id=..., query=...)`。

### 1.2 核心链路（真实实现）

`CustomerAgentExecutionService.execute`（`app/services/customer_agent_execution.py:125`）是真正的主编排器，步骤如下：

1. **会话状态加载**：`CustomerMemoryService(self.database, self.redis_client).load_short_term(...)` 从 Redis 短期记忆取 `handoff`、`clarification_count`、`pending_intent_plan`、`original_query`（`customer_agent_execution.py:127-128`）。
2. **人工接管门**（`customer_agent_execution.py:129-137`）：若短期记忆里有 `customer_service_session_id` 且对应 `CustomerServiceSession.status in {waiting_human, human_active, paused}`，仅注入 `active_handoff` 状态标记；**只读 Agent 请求仍可继续走 Supervisor 入口**（注释明确：UI 只把普通聊天输入路由到人工通道，独立只读请求不被禁用）。
3. **追问上下文合并**（`customer_agent_execution.py:139-143`）：若有挂起的 `pending_intent_plan`，用 `combined_query = original_query + 本次query` 重新规划，实现"上一轮追问 + 本轮回答"的上下文延续。
4. **意图识别（确定性规划）**：`CustomerIntentPlanner().plan(combined_query)`（`app/services/customer_intent.py:79`）——关键词正则命中表 `_patterns`（`customer_intent.py:65-77`，17 组 intent→marker→route→confidence），产出 `IntentPlan{normalized_query, tasks: list[IntentTask], clarification, requires_handoff}`（`customer_intent.py:27-32`）；实体抽取用 `extract_entities`（`customer_intent.py:41-61`，只抽显式标识符 `order_no/ticket_no/product_name`，"不猜测业务含义"）。
5. **LLM 影子/回退层**（默认关闭，见 3.3）：`QwenProvider` + `LLMIntentFallbackService.classify` → `IntentPolicyResolver().resolve(rule_intent, llm_intent)`（`customer_agent_execution.py:157`）→ `_apply_structured_intent`（allowlist 白名单，`customer_agent_execution.py:85-107`）→ 全程 `_audit_intent_shadow` 写 `AuditLog`（action=`customer_agent.intent_shadow_evaluated`，`customer_agent_execution.py:109-123`，记录 rule/llm/resolved/decision/error）。
6. **路由决策**：`route_for_plan(plan)`（`customer_agent_execution.py:50-53`）——每个 task 经 `AGENT_ROUTE_BY_INTENT`（`customer_agent_execution.py:20-36`）映射到路由，取 `ROUTE_PRIORITY`（`customer_agent_execution.py:47`）最高的一个，再映射到 `AGENTS_BY_ROUTE`（`customer_agent_execution.py:37-46`）里的 Agent 名单。
7. **澄清状态机**（`customer_agent_execution.py:182-189`）：若 `plan.clarification` 非空 → `clarification_count+1`；**达到 2 次**→ 直接 `CustomerRagHandoffService.handoff` 转人工并返回 `overall_status="handoff"`；否则把 `pending_intent_plan`（含 tasks 与 execution_modes）存回短期记忆，返回 `needs_clarification`。
8. **计划执行**：`CustomerTaskExecutor(self.database).execute(customer_id=customer_id, plan=plan)`（`customer_agent_execution.py:190`，实现在 `app/services/customer_task_executor.py:113-124`）——见 1.3。
9. **结果汇总**：`CustomerTaskExecutor.summarize(results, requires_handoff=plan.requires_handoff)`（`customer_agent_execution.py:191`，实现在 `customer_task_executor.py:186-210`）产出 `(overall_status, clarification, aggregate_answer)`。
10. **二次转人工判定**（`customer_agent_execution.py:193-197`）：`needs_handoff = plan.requires_handoff or 任一结果 error=="low_confidence"` → `CustomerRagHandoffService.handoff` 生成工单，回答追加"已为您转接人工客服，工单号：…"。
11. **bounded_react 审计**（`customer_agent_execution.py:198-228`）：凡结果 `data.execution_mode=="bounded_react"` 的任务，写 `AuditLog`（action=`customer_service.bounded_react_executed`），记录 `tool_call_count`、`tools` 列表，并显式标记 `private_reasoning_logged: False`、`write_tools_used: False`。
12. **状态持久化 + 返回**（`customer_agent_execution.py:229-231`）：`memory.save_short_term(...)` 存 task_results/execution_modes/clarification/handoff 等，返回 `CustomerTaskExecutionResponse`（含 `route`、`agents`、每个任务的 `TaskExecutionResultResponse`、`work_order_id/ticket_no/customer_service_session_id`）。

### 1.3 任务执行器（真实实现）

`CustomerTaskExecutor`（`app/services/customer_task_executor.py:37`）：

- **DAG 调度**（`customer_task_executor.py:113-124`）：`pending = list(plan.tasks)`；每轮 `ready = 依赖全部完成的项`，按 `max_parallel_tasks=4` 分批 `asyncio.gather` 并行；若某轮无 ready 项（依赖死锁）→ 剩余任务标 `blocked`（error=`dependency_not_completed`）。
- **`_execute_one` 分派**（`customer_task_executor.py:126-184`）：
  - `greeting` → 固定欢迎语，`succeeded`（`:127-128`）。
  - `human_handoff` → `needs_clarification`（"是否现在转接人工客服？"，`:129-130`）。
  - `REACT_INTENTS`（company_info/company_business/faq/workflow/policy/risk_public，`:42`）→ `_bounded_react_knowledge`（`:49-111`，见下）。
  - `business_operation` → **绝不执行交易**（`:133-153`）：若前面已有 `product_recommendation` 结果 → `needs_clarification`（data 带 `recommended_purchase=True, confirmation_required=True, executed=False`，`:136-151`），要求客户从推荐产品中明确选择；否则 `needs_confirmation`，data 带 `OPERATION_ACTION_TARGETS` 深链（`:35`：`赎回→/customer-center?tab=operations&action=redeem` 等）+ `prefill_hint`，并附 `clarification` 要求补充对象/金额（`:152-153`）。
  - `product_recommendation` → 组装 `SqlAlchemyTradingUnitOfWork` + `SuitabilityService.recommend(session, customer_id)`（内部用 `AssetSummaryService`），返回 `execution_mode="plan_rules"` 的推荐结果（`:154-168`）。
  - `unknown` → `needs_clarification`（`:169-170`）。
  - 带 `depends_on` 的 workflow/faq/policy → 按已完成的依赖选择 `explain_order_status` 或 `explain_workorder_status` 工具（`:171-176`）。
  - 其余业务事实意图 → `TOOL_BY_INTENT` 映射（`:17-23`）到 5 个工具（workorder_status→get_my_workorder_status 等），走 `CustomerReadTools.execute`（`:177-184`）；错误映射：`fact_not_found/ambiguous_product → needs_clarification`，其余 → `failed`；无映射 → `failed(error="no_tool_mapping")`（`:179`）。
- **bounded ReAct**（`customer_task_executor.py:49-111`）：`MAX_REACT_ITERATIONS=3`、`MAX_REACT_TOOL_CALLS=3`（`:40-41`）；循环内只调一个工具 `search_customer_knowledge`；**首轮 low_confidence 只允许一次查询改写**（追加"请提供官方流程、适用条件和注意事项"，`:97-101`），第二次失败直接 `needs_clarification`（error 保留）；成功且有 citations → 用 `CustomerRagAnswerService.generate(query, rag_result)`（QwenProvider）做答案生成（`:74-87`）；每次尝试记录 `{tool, iteration, success, error, evidence_status}` 进 `tool_calls`（`:51-63`，不含私有推理）。
- **汇总**（`customer_task_executor.py:186-210`）：status 判定顺序 handoff > 空结果 > 全成功 completed > 部分成功 partially_completed > needs_clarification > failed > blocked_by_human（`:189-206`）；多任务答案按行拼接，最后附"下一步：{clarification}"（`:207-210`）。

### 1.4 工具门面与事实路由（真实实现）

- `CustomerReadTools.execute`（`app/services/customer_tools.py:29`）：**固定 8 个名字的白名单**（`:30-33`，5 个 get_my_* + get_product_status + search_customer_knowledge + explain_order_status/explain_workorder_status）；未命中 → `tool_not_allowed`（`:34-35`）。知识类工具 → `WealthKnowledgeRetriever(KnowledgeRepository)` + `CustomerRagService.answer(query, intent)`（embedding 用 QwenProvider），`rag.handoff_required → error="low_confidence"`（`:36-40`）。业务事实工具 → `CustomerQueryRouter().route(session, customer_id, query, entities)`（`:41-42`），并做 **`route_mismatch` 校验**（工具名预期的路由 ≠ 实际路由 → 失败，`:45-51`）。
- `CustomerQueryRouter`（`app/services/customer_query_router.py:27`）：`detect_route` 按 5 组关键词（工单/订单/风险/资产/产品，`:35-58`）确定路由；每个处理器**全部带 customer_id 作用域过滤**（见 3.2），返回 `CustomerQueryRouteResult{source_type, route, answer, data, evidence_status, next_action}`（`:17-24`），evidence_status ∈ {sufficient, partial, not_found, needs_clarification}。

### 1.5 占位 / 规划性质的代码

| 文件 | 状态 | 证据 |
|---|---|---|
| `app/agents/graph.py` | **纯占位** | 类 docstring 原文（`graph.py:12-17`）："P0 LangGraph wiring placeholder. P0 only validates that the state graph can be constructed. Domain Agent nodes and tools are added from P1/P2/P3." `supervisor_node` 只做 `iteration_count+1`（`graph.py:23-24`）；`build()` 只有 START→supervisor→END 两个节点（`graph.py:26-30`）；`max_iterations/max_tool_calls` 参数（`graph.py:19`）在节点内从未使用。除 `app/agents/__init__.py` 导出外无任何调用点。 |
| `app/agents/orchestrator.py` | **基础设施/规划** | `AgentOrchestrator` 只是 name→Agent 的字典注册表（`orchestrator.py:6-13`），run 对未注册 Agent 返回 `AgentResult(status="error", summary="agent not registered")`（`orchestrator.py:15-18`）。未接入客服运行时。 |
| `app/ports/agent.py` | **真实接口契约** | `Agent` Protocol（`name` + `async run(message, context) -> AgentResult`，`agent.py:13-15`）、`AgentContext` dataclass（`agent.py:7-11`）——为未来 P1/P2/P3 的 Supervisor 多 Agent 预留的端口；P0 的客服路径并不通过它。 |
| `app/services/intent_templates.py` | **真实注册表** | 11 个模板的 `IntentTemplate` 字典（`intent_templates.py:6-18`），定义 execution_mode/required_fields/prohibited_actions，供策略与后续执行参考；`get_template` 兜底 T0_UNKNOWN（`:21-22`）。 |

**结论**：P0 的真实链路完全不走 LangGraph / AgentOrchestrator，是一条**确定性管线**：`api/customer_service.py → CustomerAgentExecutionService.execute → CustomerIntentPlanner.plan → CustomerTaskExecutor.execute → CustomerReadTools.execute → CustomerQueryRouter.route`。graph.py 只是"图可构建"的自检占位。

---

## 2. 【能力注册表 AgentCapabilityRegistry】

文件：`app/services/agent_capabilities.py`

### 2.1 真实数据结构

- `AgentCapability`（`agent_capabilities.py:8-13`）：`@dataclass(frozen=True)`，四个字段——`code: str`、`required_roles: frozenset[str]`、`required_permissions_any: frozenset[str]`、`internal_only: bool`。frozen 保证注册表不可变。
- 注册表本体：类属性 **`_capabilities: tuple`（不可变元组，非 dict）**（`agent_capabilities.py:19-29`），共 9 项：
  - 客户可见 4 项：`CustomerServiceAgent`（role=customer，`:20`）、`CustomerRecommendationCapability`（customer，`:21`）、`CustomerTradeDraftCapability`（customer，`:22`）、`CustomerRiskExplanationCapability`（customer，`:23`）；
  - 内部 5 项（`internal_only=True`）：`DataAnalysisAgent`（permissions_any={asset:read, customer:read_all, customer:read_assigned, risk:alert_read, order:review, operation:read_staff}，`:24`）、`AdvisorAgent`（role=advisor，`:25`）、`RiskAgent`（permissions_any={risk:alert_read, risk:alert_handle, risk:alert_action, risk:scan_execute}，`:26`）、`BusinessAgent`（permissions_any={operation:read_staff, order:review, transfer:submit_staff}，`:27`）、`AdminAgent`（permissions_any={audit:read, admin:write}，`:28`）。

### 2.2 白名单/过滤的实现方式

- `role_codes(user)`（`agent_capabilities.py:31-33`）：`{role.code for role in user.roles}`；`permission_codes(user)`（`:35-37`）：对每个 role 的 permissions 展平去重。
- `available_for(user)`（`agent_capabilities.py:39-54`）三连闸：
  1. `user.is_super_admin` → 直接放行全部 9 个能力（`:40-41`）；
  2. `internal_only and is_customer`（角色含 customer）→ 跳过内部 Agent（`:47-48`）；
  3. `required_roles` 非空时要求 `roles & required_roles` 非空（**交集**，`:49-50`）；`required_permissions_any` 非空时要求 `permissions & required_permissions_any` 非空（**任一命中即可**，`:51-52`）。
- `denied_for(user)`（`:56-58`）：全体能力 − available，取补集，便于 API 层同时返回"可用/被拒"两份清单。

### 2.3 在哪一步做权限前置过滤（真实位置）

`app/api/customer_service.py:191-193` 的 `execute_unified_agent`：进入任何 Agent 执行前先 `registry.available_for(user)` / `registry.denied_for(user)`，随后 `if "DataAnalysisAgent" in available_agents:`（`:198`）才允许走数据分析分支（否则 `template_attempt_reason="agent_unavailable"`，`:196`）。

需要诚实说明的边界：**能力注册表是 API 层的"路由前门"（pre-routing gate）**，决定"这个用户能触发哪些 Supervisor 可见能力"；而客服运行时内部 `route_for_plan` 是按意图→优先级确定路由的，**不再查注册表**。也就是说注册表管"入口能否进入"，意图规划管"进入后走哪条路"——两层职责分离。另外注意一个实现细节：客户角色天然只暴露 4 个客户能力，与 `AGENTS_BY_ROUTE` 中 `customer_*` 路由的 Agent 名单（CustomerServiceAgent / CustomerRecommendationCapability / CustomerTradeDraftCapability / CustomerRiskExplanationCapability，`customer_agent_execution.py:38-41`）一一对应。

---

## 3. 【安全边界】真实实现

### 3.1 工具预算（tool budget）

- **ReAct 知识任务**（`customer_task_executor.py:40-41`）：`MAX_REACT_ITERATIONS = 3`、`MAX_REACT_TOOL_CALLS = 3`。循环条件 `for iteration in range(1, MAX_REACT_ITERATIONS+1)`（`:53`），且 `if len(attempts) >= MAX_REACT_TOOL_CALLS: break`（`:54-55`）——双重上限，任一先到即停。且**首轮 low_confidence 只允许一次查询改写**（`:97-101`），其余失败路径直接 clarify/handoff，"不让 agent 持续试探"。
- **工具名预算**：`CustomerReadTools.allowed` 固定 8 个名字（`customer_tools.py:30-33`），白名单之外直接 `tool_not_allowed`（`:34-35`），不存在动态注册工具。
- **SQL 预算**（`DataAnalysisAgent` + `nl2sql_policy.py`）：`MAX_SQL_LENGTH=20000`、`MAX_RESULT_ROWS=100`、`MAX_PARAMETER_COUNT=12`、`MAX_STRING_PARAMETER_LENGTH=256`、`MAX_DATE_RANGE_DAYS=366`（`nl2sql_policy.py:5-9`）；`limit` 参数强制 `1 ≤ limit ≤ 100`（`data_analysis_agent.py:72-73`），日期范围超上限拒绝（`:74-76`）。
- 对照说明：`graph.py` 里也有 `max_iterations=4 / max_tool_calls=8` 构造参数（`graph.py:19`），但**从未在节点中使用**——那是占位骨架的参数，真实预算在 `CustomerTaskExecutor` 常量里。

### 3.2 Subject Scope / customer_id 绑定

- **客服事实路由**（`customer_query_router.py`）：每个查询都硬编码当前 `customer_id` 作用域，全部在 SQLAlchemy where 层：
  - 工单：`WorkOrder.customer_id == customer_id`（`:77`）；
  - 订单：`Order.user_id == customer_id`（`:105`，注意订单表字段叫 user_id）；
  - 风险：`Account.user_id == customer_id`（`:131`）+ `RiskAlert.customer_id == customer_id`（`:134`）；
  - 资产：`CustomerAssetSnapshot.user_id == customer_id`（`:166`）、`CustomerHolding.user_id == customer_id`（`:172`）；
  - 产品状态是**公开目录**（Product 无客户过滤，`deleted_at is null and status in(...)`，`:205-206`），不属个人数据。
- **数据分析**（`data_analysis_agent.py`）：参数绑定阶段对 `authorized_scope_only` 来源参数做主题绑定（`:150-161`）：超管且 `authorized_subject_ids == set()` → 不限范围；否则必须来自 `authorized_subject_ids` 或当前 `user.id`；查询里出现作用域外的客户样式 ID → 显式拒绝 `unauthorized_subject:{name}`（`:153-154`）；多个已授权主体且未明确 → `missing_authorized_subject:{name}`（`:161`，并给出可操作的澄清文案，`:111-113`）。**客户角色（`_is_customer`，`:178-179`）在 `analyze` 入口直接 `return None`**（`:319-320`），客户永远进不了数据分析。
- **权限前置**：指标模式每个 metric 有独立权限交集（如 open_risk_alert_count 需 {risk:alert_read, risk:alert_handle, risk:alert_action, audit:read} 之一，`:326-327`），非超管无权限 → `return None`（`:327`、`:330`、`:333`、`:336`、`:339`）。

### 3.3 错误兜底（error fallback）全链条

| 层 | 兜底行为 | 代码位置 |
|---|---|---|
| 意图规划 | 空输入 → clarification"请描述您需要查询或办理的事项"（`customer_intent.py:81-82`）；未命中任何关键词 → `unknown` 任务 + clarification（`:96-97`） | `customer_intent.py` |
| 澄清计数 | `clarification_count >= 2` → 强制 `CustomerRagHandoffService.handoff` 转人工（`overall_status="handoff"`，`customer_agent_execution.py:184-187`） | `customer_agent_execution.py` |
| LLM 回退 | 超时（8s）或异常 → 只写 shadow 审计，**确定性规划器保持权威**（"Model fallback is advisory only; deterministic planner remains authoritative on failure"，`:174-178`）；allowlist 外的模板 / requires_clarification / 无安全映射 → 不应用（`:160-171`） | `customer_agent_execution.py` |
| ReAct 知识 | low_confidence 首轮改写一次（`:99-101`）→ 仍失败 → `needs_clarification`（error=low_confidence，`:103-111`）→ 上层转人工（`customer_agent_execution.py:193`） | `customer_task_executor.py` |
| 工具层 | 名称越权 → tool_not_allowed（`customer_tools.py:34-35`）；路由不符 → route_mismatch（`:50-51`）；事实缺失 → fact_not_found（`:52-53`）；产品歧义 → ambiguous_product（→ needs_clarification） | `customer_tools.py` |
| 执行器 | 依赖死锁 → blocked（`customer_task_executor.py:118-120`）；无工具映射 → failed(no_tool_mapping)（`:179`）；SQL 执行失败 → 先 `session.rollback()` 再返回 `execution_failed`（`data_analysis_agent.py:288-295`，注释说明：避免 PostgreSQL 事务中毒掩盖拒绝审计、污染整个 Agent 请求） | `customer_task_executor.py` / `data_analysis_agent.py` |
| 汇总/回答 | 无回答时兜底文案"当前没有可返回的结果，建议转人工客服。"（`customer_agent_execution.py:231`）；handoff 成功则追加工单号（`:197`） | `customer_agent_execution.py` |
| 转人工去重 | `CustomerRagHandoffService.handoff` 用 `sha256(f"{intent}:{normalized}")` 做 query_key（`customer_rag_handoff.py:21-22`），20 条内已有 open 工单则复用并追加 `WorkOrderUpdate`（`:25-30`），避免重复建单 | `customer_rag_handoff.py` |

### 3.4 多意图处理（真实实现）

- 规划：`CustomerIntentPlanner.plan` 一次可产出多个 `IntentTask`（去重 `seen`，`customer_intent.py:87-95`），并建立**任务依赖**：`workflow/policy/faq` 若与 `order_status/workorder_status` 同现，则 depends_on 指向状态查询任务（`customer_intent.py:91-92`）；`business_operation` 依赖 `product_recommendation`（`:93-94`）。
- 执行：`CustomerTaskExecutor.execute` 按拓扑就绪分批并行（依赖完成才进 ready 批，`customer_task_executor.py:116-123`），`max_parallel_tasks=4`，`asyncio.gather`。
- 模板：多意图（且含 order_status）→ `T8_MULTI_INTENT`（`intent_template_resolver.py:56-57`），execution_mode=`plan_rules`（`intent_templates.py:14`）。
- 路由：`route_for_plan` 对多任务取 `ROUTE_PRIORITY` 最高路由（`customer_agent_execution.py:47`：operation_risk=8 > customer_trade_draft=7 > advisor_risk=6 > customer_recommendation=5 > risk/customer_risk=4 > analysis=3 > customer_service=1）。
- 结果：`summarize` 聚合多任务状态（completed / partially_completed / needs_clarification / failed / blocked_by_human，`customer_task_executor.py:189-206`），回答按任务逐行拼接（`:207-210`），响应携带 per-task 结果数组（`customer_agent_execution.py:231`）。
- 边界：依赖任务的失败会**阻断下游**（business_operation 只有在拿到 product_recommendation 结果后才进入"请选择产品"分支，`customer_task_executor.py:135-151`，否则走普通确认分支）。

### 3.5 其他关键安全设计

- **LLM 防御性加固**（`llm_intent_fallback.py:58-61`）：模型只要产出 T3/T4，强制 `explicit_confirmation=False` 并注入 `must_not_submit_without_verified_customer_confirmation`——"模型不能声称客户已确认"。
- **策略合并防篡改**（`intent_policy_resolver.py:18-43`）：LLM 永远不能删除规则发现的 `missing_required_fields`（`:36`）；规则的 `explicit` 实体覆盖模型实体（`:37-39`）；规则置信度 ≥0.9 且非 T0 时以规则为骨架（`:22-23`）；**规则 T0 时禁止模型把模糊查询升级为交易/草稿模板**（`:26-31`）；T3/T4 额外强制无确认（`:40-42`）。
- **Fallback 只读白名单**（`customer_agent_execution.py:82-107` + `settings.py:37`）：默认 allowlist=`T1_INFORMATION_QUERY,T2_PRODUCT_RECOMMENDATION,T7_HUMAN_HANDOFF`；交易/草稿模板永远不能来自模型；`T1_INFORMATION_QUERY` 只映射到公开 RAG 意图（company_info/company_business/faq/workflow/policy/risk_public，`:102`），未知标签兜底为 `faq`（`:104`）——"不从模型标签推断账户/订单/风险事实"（`:100-106`）。
- **审计**：意图影子评估（`customer_agent.intent_shadow_evaluated`，`customer_agent_execution.py:122`）与 bounded_react 执行（`customer_service.bounded_react_executed`，`customer_agent_execution.py:221`）两条审计链路均记录到 `AuditLog`，detail 里显式标注 `verified_customer_confirmation_required: True`（`:119`）。
- **相关配置项**（`app/core/settings.py:36-40`）：`llm_intent_fallback_enabled=False`、`llm_intent_shadow_enabled=False`（默认关闭）、`llm_intent_fallback_allowlist="T1_INFORMATION_QUERY,T2_PRODUCT_RECOMMENDATION,T7_HUMAN_HANDOFF"`、`llm_intent_fallback_timeout_seconds=8.0`、`llm_intent_fallback_max_concurrency=4`。

---

## 4. 【设计取舍】从代码看出的关键决策

1. **确定性优先、LLM 仅咨询（Deterministic-first, LLM-advisory）**：意图识别、模板解析、路由、工具选择全部规则化；LLM 回退默认关闭（`settings.py:36-38` 两个开关默认 False），开启也只是 shadow + allowlist 只读模板。理由：可审计、可测试、延迟可控；金融场景下"安全解释"优先于"聪明猜测"。
2. **双路由分层**：粗粒度关键词意图规划（`customer_intent.py`）→ 模板解析（`intent_template_resolver.py`）→ `route_for_plan` 优先级路由（`customer_agent_execution.py:50-53`）；而 `CustomerQueryRouter` 是"业务事实第一道确定性路由"，非个人事实一律落回 RAG（`customer_query_router.py:27-33` docstring）。理由：把"能读库的事实"与"需要检索解释的知识"分开，缩小数据库暴露面。
3. **有界 ReAct 而非自由 ReAct**：只用 1 个固定工具、3 轮 3 次上限（`customer_task_executor.py:40-41`）、只允许 1 次查询改写（`:97-101`）、不记录私有推理（`customer_agent_execution.py:216`：`private_reasoning_logged: False`）。理由：知识问答需要一点"检索-修正"弹性，但绝不引入自由工具调用和自由 SQL。
4. **对话中永不执行交易**：`business_operation` 恒返回 `needs_confirmation/needs_clarification` + `executed=False`（`customer_task_executor.py:147,157`）+ 页面深链（`:35`：`/customer-center?tab=operations&action=...`）。理由：把状态变更执行权收回到受控业务页面，客服助手只做"引导 + 预填提示"，把确认责任完整交给客户与业务系统。
5. **LLM 不可信原则显式编码**：schema 注释"StructuredIntent … is never an execution command"（`app/schemas/intent.py:29`）；模型输出强制 source=llm_fallback（`llm_intent_fallback.py:56-57`）；T3/T4 强制无确认（`:58-61`）；策略合并单向（规则→模型）叠加约束（`intent_policy_resolver.py:32-43`）。理由：纵深防御，模型幻觉不能转化为授权。
6. **模板+目录双重白名单的 SQL 安全**（`data_analysis_agent.py:226-251`）：不允许自由 SQL；模板必须 approved+active+角色匹配（`:263-268`）；SQL 静态解析出表/列后做"⊆ 模板声明 ⊆ 活跃可查询目录（且非敏感列）"双重子集校验（`:232-250`）；参数按 schema 绑定（`:116-175`）；结果双重 LIMIT 兜底（`:285-288`）；失败先回滚再审计（`:288-295`）。理由：面向运营/风控角色的分析需求用"批准模板"满足，同时把任意 SQL 注入面归零。
7. **记忆驱动的澄清状态机**：`clarification_count`、`pending_intent_plan`、`original_query` 持久化到 Redis 短期记忆（`customer_agent_execution.py:127-143`、`:182-189`），追问后合并查询重新规划，澄清 2 次即转人工。理由：避免死循环追问，保证用户体验与工单兜底。
8. **LangGraph 占位、确定性管线先交付（P0 务实主义）**：端口（Agent Protocol / AgentOrchestrator / SupervisorGraph）先行定义并保留，`graph.py` 明确标注 P0 placeholder（`graph.py:12-17`），真实执行走确定性管线。理由：P0 用可验证代码交付价值，同时为 P1/P2/P3 的真实多 Agent 图预留接缝，不阻塞演进。
9. **转人工去重**：sha256 query_key 复用 open 工单 + WorkOrderUpdate 追加（`customer_rag_handoff.py:21-30`），避免同问题重复建单、重复通知。

---

## 5. 【面试高频问答】6 题，答案基于真实代码

### Q1：你们的"多 Agent 编排"到底在哪？graph.py 是不是没实现？
**答**：P0 的真实编排不依赖 LangGraph。`app/agents/graph.py` 的类 docstring 自己写明是 "P0 LangGraph wiring placeholder"（`graph.py:12-17`），`supervisor_node` 只做 `iteration_count+1`（`graph.py:23-24`），`build()` 只有 START→supervisor→END（`graph.py:26-30`），`max_iterations/max_tool_calls` 参数在节点里根本没用到（`graph.py:19`），全项目除了 `app/agents/__init__.py` 导出没有任何调用点。真实链路是确定性管线：`CustomerAgentExecutionService.execute`（`customer_agent_execution.py:125`）做意图规划→LLM 影子/回退→优先级路由→澄清状态机→转人工判定；`CustomerTaskExecutor` 做多任务 DAG 并行调度（`customer_task_executor.py:113-124`）；`CustomerReadTools` 做工具白名单门面（`customer_tools.py:29-35`）；`CustomerQueryRouter` 做带客户作用域的确定性事实路由（`customer_query_router.py:60-72`）。这是刻意的分层：P0 用可审计的确定性代码交付，`app/ports/agent.py` 的 Agent Protocol（`agent.py:13-15`）和 `AgentOrchestrator`（`orchestrator.py:5-18`）是为 P1/P2/P3 上真实 Supervisor 图预留的端口。

### Q2：多意图请求怎么拆？任务之间有依赖怎么办？会不会有并行问题？
**答**：`CustomerIntentPlanner.plan` 按 `_patterns` 关键词命中一次性生成多个 `IntentTask`（`customer_intent.py:87-95`），每个带 task_id/priority/confidence/depends_on。依赖是显式的：例如"查订单+查流程"时 workflow 任务的 `depends_on` 指向 order_status 任务（`customer_intent.py:91-92`），"推荐+申购"时 business_operation 依赖 product_recommendation（`customer_intent.py:93-94`）。`CustomerTaskExecutor.execute`（`customer_task_executor.py:113-124`）是拓扑调度：每轮取"依赖全部完成的 ready 任务"，按 `max_parallel_tasks=4` 用 `asyncio.gather` 并行执行；若出现依赖死锁（没有 ready 项）就把剩余任务标 `blocked(error=dependency_not_completed)`（`:118-120`）防止挂死。模板层归为 `T8_MULTI_INTENT`（`intent_template_resolver.py:56-57`），路由取 `ROUTE_PRIORITY` 最高者（`customer_agent_execution.py:47`），`summarize` 输出 `completed / partially_completed / needs_clarification / failed / blocked_by_human` 聚合状态（`customer_task_executor.py:189-206`）并把每个任务答案逐行拼接（`:207-210`）。

### Q3：LLM 在意图识别里起什么作用？如何防止模型越权、幻觉变执行？
**答**：默认是纯规则，LLM 回退默认关闭（`settings.py:36-38`：`llm_intent_fallback_enabled/shadow_enabled` 默认 False）。开启后是"影子/回退"双模式：`LLMIntentFallbackService.classify` 先做 pydantic 校验并把 source 强制为 llm_fallback（`llm_intent_fallback.py:35-57`），**只要模型产出 T3/T4 交易/草稿模板就强制 `explicit_confirmation=False` 并注入 `must_not_submit_without_verified_customer_confirmation`**（`llm_intent_fallback.py:58-61`）；然后 `IntentPolicyResolver.resolve` 做单向合并（`intent_policy_resolver.py:18-43`）：规则置信度≥0.9 时以规则为骨架（`:22-23`），规则 T0 时禁止模型把模糊查询升级为交易模板（`:26-31`），模型永远删不掉规则的缺失字段（`:36`），规则的 explicit 实体覆盖模型实体（`:37-39`）；最后 `_apply_structured_intent` 只接受 allowlist（默认 T1/T2/T7，`customer_agent_execution.py:82-107` + `settings.py:37`），交易和草稿模板**永远进不了 plan**（`customer_agent_execution.py:91` 注释："Transaction and draft templates never come from the model"），requires_clarification 也直接不应用（`:91`）。还有超时 8s（`settings.py:39`）、并发信号量 4（`customer_agent_execution.py:65-71`）、异常时规则规划器保持权威（`:174-178`），全程写 `customer_agent.intent_shadow_evaluated` 审计（`:109-123`）。一句话总结：模型输出只是"建议解释"，不是执行授权。

### Q4：工具调用的安全边界具体怎么实现的？尤其是防止越权读别人数据？
**答**：三层。第一层名称白名单：`CustomerReadTools.allowed` 固定 8 个工具名（`customer_tools.py:30-33`），白名单外直接 `tool_not_allowed`（`:34-35`），工具门面本身不碰任何 repository（docstring 明示 "Tools never access repositories directly"，`customer_tools.py:24`）。第二层数据作用域：所有业务事实查询在 `CustomerQueryRouter` 里硬编码 customer_id 条件——`WorkOrder.customer_id==`（`customer_query_router.py:77`）、`Order.user_id==`（`:105`）、`Account.user_id==`（`:131`）、`RiskAlert.customer_id==`（`:134`）、`CustomerAssetSnapshot.user_id==`（`:166`）、`CustomerHolding.user_id==`（`:172`），并且 `CustomerReadTools` 做 `route_mismatch` 校验（`customer_tools.py:45-51`），防止一个工具拿到另一个路由的结果；产品状态是公开目录所以不过滤（`customer_query_router.py:205-206`）。第三层预算与只读：ReAct 知识任务 `MAX_REACT_ITERATIONS=3`、`MAX_REACT_TOOL_CALLS=3`（`customer_task_executor.py:40-41`），首轮 low_confidence 只允许一次改写（`:97-101`）；交易类意图根本不调工具，只返回 `needs_confirmation` + 页面深链（`customer_task_executor.py:152-153`），`executed` 永远是 False（`:147,157`）。数据分析另有全套：客户角色入口直接 return None（`data_analysis_agent.py:319-320`）+ 模板/目录双重白名单（`:226-251`）+ authorized_subject_ids 绑定（`:150-161`）+ 双重 LIMIT（`:285-288`）。

### Q5：DataAnalysisAgent 的 SQL 安全怎么做的？自由 SQL 支持吗？
**答**：明确不支持自由 SQL。两条路径：指标模式（`analyze`，`data_analysis_agent.py:318-342`）——5 个批准指标（open_risk_alert_count 等，`:306-312`）由 `nl2sql_policy.metric_code_for_query` 关键词匹配（`nl2sql_policy.py:47-52`），每个指标有独立权限检查（`data_analysis_agent.py:326-341`），执行的 SQL 是代码里写死的 `func.count()/func.sum()` 聚合，永远不拼用户输入；模板模式（`analyze_template_attempt`，`data_analysis_agent.py:259-301`）——模板必须 `is_active + is_approved + 角色匹配`（`:263-268`），`validate_readonly_template` 要求以 SELECT/WITH 开头、禁止 `; -- /* */ insert/update/delete/drop/alter/truncate/create/grant/revoke/copy/execute/call/pg_sleep/into`、长度≤20000（`nl2sql_policy.py:34-44`）；参数按模板 `metadata_json.parameters` schema 绑定（`bind_template_parameters`，`data_analysis_agent.py:116-175`），`authorized_scope_only` 源绑定 authorized_subject_ids（`:150-161`），越界客户 ID 显式 `unauthorized_subject` 拒绝（`:153-154`）；动态目录校验（`validate_catalog_allowlist`，`:226-251`）把 SQL 静态解析出的表/列（`_extract_references`，`:192-223`）与"模板声明 + 活跃可查询目录 + 非敏感列"做双重子集校验；最后 `SELECT * FROM (模板) AS approved_template LIMIT :__result_limit` 外层兜底（`:285`）+ `fetchmany(100)`（`:288`）双重限制。执行失败先 `session.rollback()` 再返回 execution_failed（`:288-295`），避免 PostgreSQL 事务中毒污染审计。它和客服 ReAct 是两套东西：ReAct 只调 `search_customer_knowledge` 检索知识（`customer_task_executor.py:56-62`），DataAnalysisAgent 只跑批准模板/指标，二者共同点是"有界执行"。

### Q6：如果规则意图和模型意图冲突，谁说了算？会不会出现"工具要的数据和意图对不上"？
**答**：规则说了算，且冲突被显式消化。`IntentPolicyResolver.resolve`（`intent_policy_resolver.py:18-43`）四类情况：①规则置信度≥0.9 且非 T0 → 以规则为骨架合并模型约束（`:22-23`，`_merge_constraints` `:45-49`）；②规则是 T0 而模型给了交易/草稿模板 → 禁止升级，返回带强制约束的规则解释（`:26-31`）；③否则以模型为骨架但**单向叠加**规则约束（missing_required_fields 取并集 `:36`、explicit 实体覆盖 `:37-39`、T3/T4 强制无确认 `:40-42`）；④模型永远不能声称客户已确认（`llm_intent_fallback.py:58-61`）。至于意图-工具错位，有两道防线：`CustomerReadTools` 在拿到 `CustomerQueryRouter.route` 结果后校验"工具名预期路由 == 实际路由"，不符返回 `route_mismatch`（`customer_tools.py:45-51`）；`_apply_structured_intent` 对模型的 T1_INFORMATION_QUERY 只映射公开 RAG 意图（company_info/company_business/faq/workflow/policy/risk_public，`customer_agent_execution.py:102`），未知标签兜底为 faq（`:104`），注释明确"不从模型标签推断账户/订单/风险事实"（`:100-106`）。所以"模型说这是订单查询"永远不会变成读订单库的动作。

---

### 附：本简报覆盖的关键文件清单（相对 `app/`）
- `services/customer_agent_execution.py`（主编排）、`services/customer_intent.py`（意图规划）、`services/customer_task_executor.py`（任务调度/ReAct）、`services/customer_tools.py`（工具门面）、`services/customer_query_router.py`（事实路由）
- `services/intent_policy_resolver.py`、`services/llm_intent_fallback.py`、`services/intent_template_resolver.py`、`services/intent_templates.py`（策略层）
- `services/agent_capabilities.py`（能力注册表）、`services/data_analysis_agent.py`（SQL 安全分析）
- `agents/graph.py`（占位）、`agents/orchestrator.py`（端口基础设施）、`ports/agent.py`（Agent 协议）
- 交叉核对文件：`schemas/intent.py`、`schemas/agents.py`、`core/settings.py`、`services/nl2sql_policy.py`、`services/customer_rag_handoff.py`、`api/customer_service.py`
