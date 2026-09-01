# 智能财富管理平台：功能、架构、设计取舍与可追溯清单 
 
更新时间：2026-08-21 | 文档性质：功能与架构全景盘点 | 口径：以代码、测试、设计/决策文档为准 
 
## 1. 阅读说明与状态定义 
 
本文将系统能力分为：已实现并有代码证据、部分实现/仍在收尾、设计完成待实施、业务方向验证与后续候选四类。任何“规划”能力均不应表述为已经上线。 
 
追溯格式：每个模块列出设计讨论、核心代码、测试/验收和后续计划。文档冲突时以 docs/P0-P5_IMPLEMENTATION_PLAN.md、docs/DECISIONS.md、架构文档优先。 
 
## 2. 总体架构与总设计原则 
 
### 2.1 架构形态：模块化单体 + 可拆分基础设施 
- 前端采用 Vue 3/Vite，后端采用 FastAPI；业务规则集中于 Service 层，API 层仅做鉴权、参数校验和响应组装。 
- 数据层采用 PostgreSQL 为主业务库，pgvector 承担向量检索；Redis/Redis Streams 承担缓存和异步消息；Worker 承担 Outbox、知识导入、通知、授权失效等后台任务。 
- 使用 Repository/Port/Adapter 边界隔离数据库、向量库和模型提供方，避免业务代码直接绑定外部设施。 
- 为什么这样设计：在 MVP 阶段先避免过早微服务化带来的部署、事务和调试成本，同时保留后续拆分空间。 
- 主要兜底：前端不做最终权限、适当性、风控和交易判断；后端确定性服务为最终裁决。 
- 追溯：docs/ARCHITECTURE_OVERVIEW.md；docs/TEAM_ONBOARDING.md；app/api/；app/services/；app/repositories/；app/infrastructure/；workers/。 
 
### 2.2 总原则：模型做理解/解释/草稿，确定性服务做事实/权限/执行 
- 解决的问题：模型输出不稳定、可被提示注入、不能直接承担金融资金和权限决策。 
- 约束：客户、金额、适当性、风险、订单状态、权限和数据范围都由后端服务二次或最终校验。 
- 追溯：docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md；docs/DECISIONS.md；docs/DEFENSE_SYSTEM_DESIGN_AND_EVOLUTION.md。
 
## 3. 已实现或已有代码证据的核心能力 
 
### 3.1 身份认证、RBAC 与客户数据范围 
- 功能：基于用户角色和权限码控制 API、后台入口和 Agent 可见能力；投顾/客户经理按有效授权关系访问客户主体。 
- 为什么：财富管理数据属于强敏感信息，只做“登录鉴权”无法阻止员工跨客户查看资产、订单或风险信息。 
- 优化与兜底：采用 RBAC + Subject Scope 双层控制；SubjectScopeService 按授权状态、生效期、失效期与撤销时间确定可访问客户集合；普通工作人员默认只允许自身主体，上层文本参数不能扩大数据范围。 
- 缓存兜底：角色、权限或授权客户范围变更后递增 permission-context-version / subject-scope-version，使旧缓存自然失效；Redis 故障时回退实时授权校验，不因缓存故障放宽权限。 
- 追溯：docs/RBAC_PERMISSION_MATRIX.md；docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md；app/services/subject_scope.py；app/services/advisor_authorization_service.py；app/services/permission_scoped_cache.py；workers/authorization_worker.py；tests/test_subject_scope.py；tests/test_permission_scoped_cache.py。 
 
### 3.2 Agent 能力注册与 Supervisor 路由 
- 功能：以 Supervisor/统一入口理解请求，再按角色、权限和业务意图路由客服、推荐、风险解释、数据分析、业务操作、投顾、风控与后台能力。 
- 为什么：将所有业务都放进一个 Chatbot 会造成职责混乱、内部 Agent 暴露、高风险工具被错误调用和审计困难。 
- 优化与兜底：先执行 AgentCapabilityRegistry 白名单过滤，再进行语义路由；客户角色不能看到 internal_only Agent；主 Agent 不直接持有资金写操作能力。 
- 追溯：docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md；docs/MULTI_AGENT_PARADIGM_AND_BOUNDED_REACT_2026-08-12.md；app/services/agent_capabilities.py；app/services/customer_agent_execution.py；app/services/customer_intent.py；tests/test_customer_agent_intent_shadow.py。 
 
### 3.3 低风险客服：bounded ReAct + 证据式 RAG + 转人工 
- 功能：客服处理公司信息、流程、政策、公开风险说明、FAQ、订单/工单状态等低风险问题；知识类问题可执行有限次检索和一次查询精炼。 
- 为什么：完全固定流程不够灵活，自由 ReAct 又可能无限循环、无依据回答或越界调用工具。 
- 优化：最多 3 轮、最多 3 次工具调用、工具白名单、默认只读、仅低置信时允许一次精炼；记录工具调用摘要和 evidence_status，但不记录模型私有思维链。 
- 兜底：证据不足则澄清或转人工，不把低置信检索结果直接交给模型编造；Embedding/检索异常可降级为文本检索或人工处理。 
- 追溯：docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md；docs/KNOWLEDGE_AND_DATA_CATALOG_ARCHITECTURE.md；app/services/customer_task_executor.py；app/services/customer_rag_service.py；app/services/customer_rag_answer.py；app/services/customer_rag_handoff.py；tests/test_customer_rag_retrieval.py。 
 
### 3.4 结构化意图、Shadow Mode 与受控 LLM fallback 
- 功能：规则 planner 负责当前真实执行路径；Qwen StructuredIntent 可在 Shadow Mode 生成候选意图并审计规则/模型差异。 
- 为什么：模型直接改写交易或草稿计划会带来未知上下文交易推断、确认绕过和错误路由风险。 
- 优化与兜底：默认 shadow_only；即使开启 fallback，也仅允许公开信息、产品推荐、人工交接等低风险模板影响计划；交易、草稿、取消等模板始终不能被 fallback 改写。 
- 运行时保护：并发信号量限制结构化模型调用；超时、网络错误、空响应、非法 JSON、非法模板均回退规则 planner；聚合审计不保存原始客户 query。 
- 追溯：docs/DECISIONS.md（2026-08-15 相关决策）；app/services/llm_intent_fallback.py；app/services/intent_policy_resolver.py；app/services/intent_template_resolver.py；tests/test_llm_intent_fallback_contract.py；tests/test_intent_shadow_metrics_contract.py；scripts/evaluate_intent_fallback.py。
 
### 3.5 自然语言交易：草稿、预检、确认令牌与幂等建单 
- 功能：客户可自然语言表达申购、赎回、转账等意图；系统生成可持久化草稿和下一步指引，完成后端验证的用户确认后才进入既有订单流程。 
- 为什么：金融交易不能让模型文本直接成为授权，也不能因前端重试或重复点击创建多笔订单。 
- 优化：将意图理解、草稿生成、产品状态、金额规则、风险/适当性预检、确认、订单状态机拆分；产品推荐使用实时 Product、画像与持仓，而非依赖可能过期的文档 RAG。 
- 兜底：模型不生成或消费确认令牌；令牌绑定客户身份、草稿版本和有效期；错误或过期令牌拒绝，重复确认返回同一订单；最终提交仍复检并写审计/Outbox。 
- 追溯：docs/DECISIONS.md（2026-08-15 客户确认后下单）；docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md；app/services/natural_language_order_draft.py；app/services/trading_service.py；app/api/trading.py；tests/test_natural_language_order_draft.py；scripts/test_customer_controlled_operation_http.py。 
 
### 3.6 产品推荐、适当性与风险解释 
- 功能：将“产品介绍/条款解释”与“能否推荐/能否购买”拆开；模型负责解释，服务端适当性和风险规则负责最终匹配与限制。 
- 为什么：实时产品状态、风险等级、最低金额、流动性、客户风险等级和账户限制不能由静态文档或模型推断。 
- 优化与兜底：前端可做提示或置灰，最终判断必须由服务端；推荐结果提供匹配/排除原因；模型不能直接下单或改变风险事实。 
- 追溯：docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md；docs/KNOWLEDGE_AND_DATA_CATALOG_ARCHITECTURE.md；app/services/suitability_service.py；app/services/customer_tools.py；app/api/profile.py；tests/test_suitability_p2_http.py。 
 
### 3.7 受控数据分析：数据目录 + 批准模板 + 策略层 
- 功能：运营、风控、投顾等内部角色可通过自然语言发起经营数据分析；系统返回结构化结果、查询计划和图表规格，而不是执行模型自由生成的 SQL。 
- 为什么：自由 NL2SQL 难以稳定校验，可能产生写操作、敏感字段访问、跨客户查询、错误口径和 Prompt Injection 绕过。 
- 已做优化：DataCatalog 先按角色检索表、字段、关系与批准示例；SQL 必须同时通过模板声明白名单和目录动态候选白名单；参数 schema 限制整数、日期范围和自然语言参数；固定指标可走确定性兜底。 
- 兜底：拒绝 SQL-shaped 输入；只读模板门禁；非批准/非 active 模板、不可查询表、敏感字段和超界参数拒绝；未指定多个授权客户主体时要求澄清，不猜测 subject；执行审计记录模板和拒绝原因。 
- 缓存优化：目录检索使用 PermissionScopedCache，角色/权限或授权范围变更后版本化失效；缓存不可用时不改变授权判断。 
- 追溯：docs/DATA_ANALYSIS_AGENT_SECURITY_2026-08-12.md；docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md；app/services/data_analysis_agent.py；app/services/data_catalog_service.py；app/services/nl2sql_policy.py；app/services/subject_scope.py；tests/test_data_analysis_agent_security.py；tests/test_nl2sql_policy.py；scripts/run_nl2sql_eval.py；scripts/run_nl2sql_http_eval.py。 
 
### 3.8 知识库、RAG、文档版本和检索治理 
- 功能：管理员上传 TXT/MD/DOCX 后，经 Outbox、Redis ingestion Stream、Worker 解析/切块/Embedding 写入 pgvector；按知识 Scope、分类、版本和可见性检索。 
- 为什么：金融制度、产品说明和流程文档更新频繁；纯关键词搜索语义弱，全量重嵌入成本高，旧版本还会污染答案。 
- 优化：版本化文档与 Chunk Hash 复用，局部变更只处理变化片段；支持文本/向量混合召回、引用式回答、Scope 隔离。 
- 兜底：低置信度不生成无依据答案而是转人工；产品推荐/订单/余额/持仓等权威事实不以 RAG 为准；Embedding 异常可降级文本检索。 
- 追溯：docs/KNOWLEDGE_AND_DATA_CATALOG_ARCHITECTURE.md；docs/ARCHITECTURE_OVERVIEW.md；app/api/knowledge.py；app/services/knowledge_ingestion_service.py；app/services/knowledge_retrieval.py；workers/knowledge_worker.py；tests/test_knowledge_ingestion_status.py；tests/test_customer_rag_retrieval.py。
 
### 3.9 异步可靠性：AsyncTask、Outbox、Redis Streams、Worker、通知 
- 功能：将知识导入、通知、授权范围失效等非同步任务通过事务内 Outbox 可靠投递到 Redis Streams，由专用 Worker 消费。 
- 为什么：直接在业务事务中同步调用外部服务会造成请求阻塞、事务与消息不一致、服务故障后任务丢失。 
- 优化与兜底：Outbox 保证数据库事务提交后可继续发布；Worker 处理 pending/XCLAIM、重试和死信；按事件类型路由到 knowledge、notification、authorization、catalog、risk 等 Stream。 
- 追溯：docs/ARCHITECTURE_OVERVIEW.md；docs/TEAM_ONBOARDING.md；app/models/async_task.py；app/models/outbox.py；workers/outbox_worker.py；workers/notification_worker.py；workers/authorization_worker.py；scripts/test_authorization_worker_retry.py；scripts/test_outbox_dead_letter_http.py。 
 
### 3.10 审计、可观测性、测试与演进记录 
- 功能：关键行为记录审计，包括 Agent/模板执行或拒绝、执行模式、权限相关事件、订单确认、恢复/重放等；不保存模型私有思维链、完整敏感 query、客户资产正文或完整 SQL 参数。 
- 为什么：金融场景需要可回放、可解释和可追责，同时审计本身也不能成为敏感数据泄漏源。 
- 优化：策略层与离线/HTTP 评测复用，避免测试脚本复制生产安全规则；使用 Pytest、真实 HTTP、Redis/Worker 回归和 Playwright E2E 共同验证。 
- 追溯：docs/TESTS.md；docs/CODE_CHANGELOG.md；docs/ISSUES.md；app/models/audit.py；scripts/run_nl2sql_http_eval.py；scripts/test_customer_agent_websocket.py；frontend/e2e/agent-bubble-human-session.spec.ts；frontend/e2e/agent-bubble-trading.spec.ts。 
 
## 4. 部分实现、正在收尾或明确待补齐的能力 
 
### 4.1 P2 受控数据分析产品化 
- 已有基础：目录检索、批准模板、白名单、参数契约、权限/subject 范围、HTTP 安全评测已具备。 
- 待补齐：多授权主体未指定 subject 时的真实 HTTP 澄清闭环；前端 needs_clarification 展示；表格、柱状图、折线图、饼图和 query_plan 的稳定前端闭环；Schema/Few-shot/枚举缓存统一迁移；六维评测报告接入 CI 与发布门槛。 
- 为什么尚未一次完成：P2 先冻结安全无记忆基线，避免共享记忆、自由 SQL 或复杂图表同时叠加导致问题不可归因。 
- 追溯：docs/AGENT_NEXT_STEPS_AND_PROGRESS_2026-08-14.md；docs/AGENT_STRATEGY_EVALUATION_AND_IMPLEMENTATION_ROADMAP_2026-08-14.md；docs/DATA_ANALYSIS_AGENT_SECURITY_2026-08-12.md。 
 
### 4.2 权限矩阵与工单域 
- 已有基础：客户、投顾、客户经理、风险、后台等权限码和部分迁移已完成。 
- 待补齐：前端菜单/路由守卫全面切换新矩阵；统一“角色 × API × 预期状态码”自动化矩阵；工单实体、路由、前端工作台以及客户服务、风险调查、业务异常三类 Scope 的完整建模。 
- 设计取舍：先登记工单权限而不分配给业务角色，避免把“权限码存在”伪装为“工单功能已完成”。 
- 追溯：docs/RBAC_PERMISSION_MATRIX.md；docs/WORKORDER_DESIGN.md；docs/ISSUES.md。 
 
### 4.3 知识治理和检索增强 
- 已有基础：上传、版本、切块、向量化、Scope、低置信度转人工和基础混合检索路径。 
- 待补齐：闲聊/问候分流；结构感知切块；Query Rewrite；Cross-Encoder Rerank；多路召回融合参数化；资料有效期、审批、失效提醒、差异比对和更细粒度知识权限。 
- 设计取舍：先共用 PostgreSQL/pgvector 与逻辑 Scope 隔离，只有强合规物理隔离、量级或模型差异出现时再拆库。 
- 追溯：docs/KNOWLEDGE_AND_DATA_CATALOG_ARCHITECTURE.md；docs/RESUME_PROJECTS.md。
 
## 5. 设计完成、尚未实施的重点能力 
 
### 5.1 P3：多 Agent 最小共享工作记忆 
- 当前状态：设计完成，尚未开始完整实施；当前存在 Redis 会话状态、CustomerMemory/CustomerMemoryEvent 等基础，但 Agent 间没有统一的共享记忆语义层。 
- 要解决的问题：客服掌握的偏好、投诉/转人工上下文，风险发现的预警摘要，投顾需要的客户偏好和历史交互无法安全复用，导致重复询问和信息孤岛。 
- 方案：从最小共享工作记忆开始，而不是直接上全量长期记忆；优先客服转人工摘要、投顾准备摘要等明确场景。L0 保存原始会话/任务流水，L1 异步抽取原子事实卡片，L2 按投顾/风险/投诉/交易场景聚合，L3 在定时或关键事件触发后形成稳定画像。 
- 安全与治理：读取和写入都经 AgentCapabilityRegistry、PermissionContext、SubjectScope、Scope 标签和 TTL 控制；记忆保留来源、置信度、双时态版本、消费记录、冲突边/替代边；业务表永远高于记忆，不允许记忆覆盖权威事实。 
- 为什么暂缓：没有冻结无记忆基线前无法证明共享记忆带来收益；过早共享可能复制敏感数据、传播错误判断或绕过权限。 
- 验收方向：有/无记忆对照评估；越权读取拒绝；撤销/删除/过期可验证；客服、投顾、风险等跨 Agent 场景可检索相关摘要且写审计。 
- 追溯：docs/多 Agent 共享记忆设计方案.md；docs/AGENT_NEXT_STEPS_AND_PROGRESS_2026-08-14.md；docs/AGENT_STRATEGY_EVALUATION_AND_IMPLEMENTATION_ROADMAP_2026-08-14.md；app/models/customer_memory.py；app/services/memory_service.py；app/services/permission_scoped_cache.py；app/services/subject_scope.py。 
 
### 5.2 P4：长时运行 Agent 的断点续跑 
- 当前状态：设计完成，尚未实施为统一 Agent 执行层能力。AML 批次补跑、AsyncTask 重试、WorkOrder 暂停/恢复、客服会话状态和 LangGraph Checkpointer 依赖是可复用基础，但尚未统一。 
- 要解决的问题：多意图 Agent、文档导入、AML 扫描、外部 API 等待等场景中，Worker 崩溃、网络波动或人工介入后不应从头执行，更不能重复外部写操作。 
- 方案：先做最小恢复快照，保存 workflow_id、类型、步骤、状态、输入/权限指纹、subject scope 版本、已完成输出引用、幂等键、恢复令牌引用和失效时间；不保存完整推理链、原始敏感请求体或可绕权结果副本。 
- 设计取舍：优先复用 AsyncTask + Outbox + Redis Streams + 状态机；先选择 AML、知识导入、人工审批等待等真实长时流程试点，不直接全局引入 LangGraph Checkpointer。 
- 恢复兜底：恢复前重新校验权限和 Subject Scope；外部写操作必须幂等；超过恢复上限转人工；外部等待使用回调 token，不占用 Worker。 
- 验收方向：已完成步骤不重跑、Worker 崩溃可恢复、权限变化不能恢复旧权限结果、重复回调不产生重复写入、失败/恢复/重放均留审计。 
- 追溯：docs/长时运行 Agent 断点续跑设计方案.md；docs/AGENT_NEXT_STEPS_AND_PROGRESS_2026-08-14.md；app/models/async_task.py；app/models/outbox.py；app/models/aml.py；app/models/customer_service.py；workers/outbox_worker.py。 
 
### 5.3 受控 NL2SQL 的下一阶段：SQL IR 与指标层 
- 当前状态：已具备目录检索增强的批准模板执行，不是自由 NL2SQL。 
- 后续方案：模型只输出结构化查询意图/IR（指标、维度、筛选、时间范围），服务端从目录候选中编译参数化 SQL，再经策略/人工审批执行。 
- 目标：统一客户数、AUM、订单量、转化率等指标口径，补充成本预估、行数限制、statement_timeout、脱敏列投影、异常访问检测与审计回放。 
- 为什么不直接上：自由 SQL 的可验证性和权限边界不足；SQL IR 必须复用当前策略层与评测数据集，避免“新能力绕过旧护栏”。 
- 追溯：docs/RESUME_PROJECTS.md；docs/DATA_ANALYSIS_AGENT_SECURITY_2026-08-12.md；docs/AGENT_STRATEGY_EVALUATION_AND_IMPLEMENTATION_ROADMAP_2026-08-14.md；app/services/nl2sql_policy.py。 
 
## 6. 业务方向验证与候选路线 
 
### 6.1 已完成的业务方向验证 
- 对推荐到可执行策略、投后诊断、策略模板/场景化投资、人机协同、市场分析/投研五个方向进行了业务价值与行业实践对照分析。 
- 结论：项目的差异化不在“让模型自由交易”，而在已有的权限、适当性、确认、幂等、审计和异步可靠性基础上，将建议安全地推进到可控办理。 
- 追溯：docs/财富管理 AI 系统：业务方向验证与实施建议.md。 
 
### 6.2 候选方向及进入条件 
- 推荐到可执行策略：打通 AdvisorAgent 到 BusinessAgent，但仍需要显式客户确认、适当性/风控复检、幂等和审计。 
- 投后诊断：复用持仓、风险和数据分析能力生成结构化诊断报告，不能把模型摘要当成投资结论。 
- 策略模板与场景化投资：先沉淀受控模板和适用条件，再允许用户选择或编辑，避免模型自由构造策略。 
- 人机协同：对大额申购、风险变化、异常 AML、敏感数据分析设置二次认证、人工审批和升级通道。 
- 市场分析与投研：先通过 Provider 抽象接入演示级公开数据，生产化前必须解决合规数据源、时效性、版权与免责声明。
 
## 7. 全局优化、兜底和否决条件汇总 
 
1. 权限先于路由：先过滤可用 Agent，再处理语义意图。 
2. 风险分层：低风险解释可 bounded ReAct，高风险交易/权限/分析必须受控工作流。 
3. 不信任模型文本：customer_id、subject、确认状态、权限、金额和适当性由服务端绑定或复检。 
4. 不信任缓存：缓存 key 纳入权限与 Scope 指纹，版本化失效；缓存故障不改变授权结果。 
5. 不信任自由 SQL：目录、模板、字段、参数、角色、主体范围均需共同通过。 
6. 不信任检索结果：RAG 必须有证据、置信度和引用；不足时拒答/澄清/转人工。 
7. 不信任一次投递：Outbox、Streams、重试、pending 认领和死信处理避免异步事件静默丢失。 
8. 不信任恢复：恢复必须带幂等键、权限重校验、Scope 版本和审计，不能复用过期授权。 
9. 不信任单次模型评测：Shadow Mode、离线基线、真实 HTTP、Worker 与 E2E 回归共同验证。 
 
阶段否决条件：P2 若出现自由 SQL、越权目录/字段、跨用户缓存命中或安全事故大于 0 则暂停；P3 若记忆绕过权限/无法撤销或对照评估无收益则暂停；P4 若恢复会重复写入、权限变化后仍可恢复旧结果或 Worker 被长期阻塞则暂停。 
 
## 8. 建议的阅读与面试讲解顺序 
 
1. 先讲总体原则：模型理解，规则执行。 
2. 再讲最具业务闭环的自然语言交易：草稿、预检、确认、幂等、状态机。 
3. 再讲最具安全差异化的受控数据分析：目录、模板、白名单、Subject Scope、权限缓存。 
4. 再讲 RAG 和多 Agent：为什么 bounded ReAct，为什么低置信度转人工，为什么实时推荐不能依赖 RAG。 
5. 最后讲路线图：P2 先收尾，P3 最小共享工作记忆，P4 长时任务恢复，再扩展投后诊断和市场分析。 
 
## 9. 主要来源索引 
 
- 当前功能与业务边界：docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md 
- 总体架构与工程边界：docs/ARCHITECTURE_OVERVIEW.md；docs/TEAM_ONBOARDING.md 
- 决策与灰度边界：docs/DECISIONS.md 
- 数据分析安全：docs/DATA_ANALYSIS_AGENT_SECURITY_2026-08-12.md 
- RAG/知识治理：docs/KNOWLEDGE_AND_DATA_CATALOG_ARCHITECTURE.md 
- 多 Agent 共享记忆：docs/多 Agent 共享记忆设计方案.md 
- 长时任务恢复：docs/长时运行 Agent 断点续跑设计方案.md 
- 业务方向验证：docs/财富管理 AI 系统：业务方向验证与实施建议.md 
- 阶段进度与验收：docs/AGENT_NEXT_STEPS_AND_PROGRESS_2026-08-14.md；docs/AGENT_STRATEGY_EVALUATION_AND_IMPLEMENTATION_ROADMAP_2026-08-14.md；docs/TESTS.md；docs/CODE_CHANGELOG.md。
 
--- 技术实现详解与踩坑 ---  
## 20. 技术实现详解  
### 20.1 FastAPI + SQLAlchemy Async  
- 业务问题：同时支持 REST、WebSocket、异步数据库、Redis、模型调用和 Worker。  
- 实现：app/api 按业务域拆路由；Pydantic Schema 约束输入输出；Service 承载状态机、风控、适当性和 Agent 编排；Repository/Unit of Work 隔离数据库。  
- 优化：复杂规则从 API 下沉到 Service 和纯策略模块，使 HTTP、单元测试和离线评测复用同一规则；异步任务与主请求解耦。  
- 踩坑：入口鉴权不等于客户数据范围授权；API 进程未加载最新代码会出现代码有字段但 HTTP 没字段，需要容器重建和真实 HTTP 验收。  
### 20.2 PostgreSQL + SQLAlchemy Async  
- 实现：PostgreSQL 保存业务事实、订单草稿、知识元数据、审计和任务；交易确认使用事务、行锁和状态校验；草稿保存版本、预检快照、令牌哈希、过期时间和输入指纹。  
- 优化：业务事实和知识解释分离；确认前重新读取产品、账户、余额和适当性；幂等键和状态机避免重复建单。  
- 踩坑：前端按钮不能防止网络重试；随机订单 ID 不能解决重复提交；预检结果不能代替提交时复检。  
### 20.3 Redis + PermissionScopedCache  
- 实现：Redis 存短期会话和任务状态；缓存 Key 纳入用户、角色、权限、数据范围版本、目录策略版本和业务 Scope；Redis Streams 使用 Consumer Group、pending 和 claim。  
- 优化：权限或授权变化递增版本产生新 Key，避免全库扫描；缓存异常只造成 cache miss，实时权限仍是权威。  
- 踩坑：只用 user_id 会产生权限串扰；角色权限变化影响所有关联用户；Streams 至少一次投递要求消费者幂等。  
### 20.4 LangGraph + LangChain  
- 实现：Supervisor 生成轻量 Plan；任务携带 execution_mode；低风险知识进入 bounded ReAct；交易、风控、数据分析和权限进入 controlled workflow；统一上下文携带身份、权限、客户主体和 Scope。  
- 优化：不让所有 Agent 自由 ReAct；主 Agent 不持有资金写能力；模型返回的 Agent、工具和 customer_id 必须与注册表和后端上下文匹配。  
- 踩坑：全自由 ReAct 会造成循环、Token 浪费、工具越权和不可审计；全局 Checkpointer 可能持久化敏感上下文，因此当前仅保留选择性接入方案。  
### 20.5 RAG  
- 实现：文档上传后写入版本和 Outbox；Worker 解析、切块、Embedding，写入 pgvector；检索结合文本/向量候选、Scope 和引用。  
- 优化：file_hash 识别文档、chunk_hash 识别局部变更，只重新向量化变化片段；Bad Case 进入回归集；低置信度拒答或转人工；业务数据库事实优先。  
- 踩坑：文件重命名会导致重复 Embedding；固定长度切块容易截断制度上下文；向量命中不等于证据足够；产品文档不能决定实时可售和适当性。  
### 20.6 Qwen/DashScope + StructuredIntent  
- 实现：统一 Provider 调用 Qwen；StructuredIntent 用 Schema 约束；Shadow Mode 只记录候选；fallback 用 allowlist 仅允许低风险模板；并发信号量限制调用。  
- 兜底：超时、网络错误、空响应、非法 JSON 和非法模板回退规则 Planner；交易模板不能被 fallback 改写。  
- 踩坑：模型可能将未知上下文误判为交易；返回 JSON 不等于安全；Shadow 指标不能存原始客户 query。  
### 20.7 自然语言交易草稿  
- 实现：NaturalLanguageOrderDraftService 解析产品名和金额，检查产品、账户、余额、风险限制和适当性；预检通过后只保存草稿。确认时使用客户 ID、行锁、状态/过期/令牌校验、实时复检、幂等键和 TradingService 建单。  
- 优化：令牌只保存哈希；与客户、草稿版本和有效期绑定；重复确认返回已有订单；审计记录确认和订单关联；不支持的赎回/转账转白名单业务入口。  
### 20.11 多 Agent 共享记忆与断点续跑  
- 多 Agent 共享记忆：设计完成、尚未完整实施；L0 原始流水、L1 事实卡片、L2 场景档案、L3 长期画像；Scope、TTL、来源、置信度、双时态、冲突和消费记录用于治理。  
- 暂缓原因：需要先冻结无记忆 baseline，避免复制敏感原文、传播未确认判断或绕过权限。优先从客服转人工摘要和投顾准备摘要开始。  
- 断点续跑：设计完成、统一 Agent 执行层尚未实施；复用 AsyncTask、Outbox、AmlBatchRun、WorkOrder 状态机和 LangGraph Checkpointer 依赖。  
- 快照保存 workflow、step、状态、输入/权限/Scope 指纹、已完成输出引用、幂等键、resume token 和过期时间，不保存完整推理链和原始敏感请求体。  
- 兜底：恢复前重新校验权限；外部写操作必须幂等；外部等待不占用 Worker；超过恢复上限转人工。  
### 20.12 业务方向与竞品对照  
- 推荐到可执行策略：已有草稿、确认、适当性、风控、幂等和状态机基础，下一步打通 AdvisorAgent 到 BusinessAgent 并跟踪策略来源。  
- 投后诊断：已有持仓、风险和分析基础，规划结构化诊断报告，不能用模型摘要替代规则结论。  
- 策略模板：规划现金管理、定投、养老、教育金等版本化模板，包含适用条件、风险边界和参数。  
- 人机协同：规划大额申购、风险变化、AML 异常、敏感查询和投诉的二次认证、人工审批、SLA 和升级机制。  
- 市场分析/投研：规划 MarketDataProvider 抽象，先演示级数据，再接入合规数据源，重点解决时效、版权、来源和免责声明。  
- 竞品对照：行业产品在 C 端入口、用户规模和全生命周期成熟度方面更强；本项目工程差异化在 Agent 权限边界、交易安全、幂等、审计、受控数据分析、Outbox 和可恢复性基础。 
