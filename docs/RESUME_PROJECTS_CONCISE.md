# resume
# 简历项目描述（精简版） 
 
## 多 Agent 智能财富管理平台 
 
项目时间：2026.07 - 至今 
项目角色：AI 应用开发负责人 / 后端核心开发 
技术栈：Python、FastAPI、LangGraph、LangChain、Qwen/DashScope、PostgreSQL、pgvector、Redis Streams、Vue 3、TypeScript、Docker Compose、Pytest、Playwright 
 
面向财富管理场景设计可控的多 Agent 平台，覆盖客户服务、客户画像、产品推荐、风险评估、经营数据分析、订单查询和自然语言申购辅助等业务。系统采用“模型负责理解和解释，规则与服务负责权限、风控和执行”的设计，避免 AI 越权访问或直接执行高风险操作。
 
### 项目亮点 
 
- 多 Agent 编排：基于 Supervisor、LangGraph 和 Agent Capability Registry 拆分客服、投顾、风控、数据分析及业务 Agent；根据用户意图、角色权限、客户授权范围和业务状态动态限制 Agent 与工具调用，解决单一 Chatbot 职责混乱和模型越权问题。 
- 自然语言交易辅助：设计“自然语言申购 → 订单草稿 → 风险/适当性预检 → 用户确认 → 幂等建单”流程，结合确认令牌、状态机、审计和 Outbox，确保 Agent 只生成业务草稿，不直接执行下单、扣款或资金变更。 
- 实时产品推荐与风险解释：将产品推荐与静态 RAG 问答分离，结合实时产品、客户画像、持仓和适当性规则生成推荐结果，由服务端完成风险等级、投资期限、流动性和购买资格校验，模型主要负责解释和交互引导。 
- 受控数据分析 / NL2SQL：针对金融数据的权限和敏感字段问题，规划并实现基于数据目录、审核模板、表字段白名单、参数校验和 Subject Scope 的受控查询链路，逐步从模板执行演进到 SQL IR，避免自由 SQL 生成带来的 Prompt Injection、越权查询和口径不一致风险。 
- RAG 知识底座：基于 PostgreSQL/pgvector、Embedding 和混合检索建设知识服务，支持文档版本、Chunk Hash 复用、知识 Scope 隔离、引用式生成和低置信度转人工；后续规划结构化切块、Query Rewrite、Rerank 和知识有效期治理，提升制度与产品文档问答的准确性和可维护性。 
- 分层记忆与长期协作：规划基于 Redis 短期会话、PostgreSQL 长期事实和异步 Worker 的分层记忆体系，进一步演进为 L0–L3 多层记忆，支持客户事实抽取、场景摘要、记忆冲突处理、时间衰减和按权限检索，解决多 Agent 重复沟通和上下文持续性问题。 
- 异步可靠性与人机协同：使用 Redis Streams、Outbox、Worker、重试/死信和状态机处理通知、超时、授权校验等任务；规划在大额交易、风险变化、敏感数据分析和 AML 场景引入人工审批、二次确认和长时任务恢复机制。 
- 安全与测试：建立 RBAC、Subject Scope、权限版本化缓存和统一执行上下文，服务端绑定 customer_id 和授权客户范围；使用 Pytest、HTTP 测试和 Playwright E2E 覆盖权限隔离、危险查询、订单幂等、Agent 路由和实时会话等场景。
 
### 技术关键词 
 
- AI / Agent：Supervisor、Multi-Agent、LangGraph、LangChain、RAG、混合检索、受控 NL2SQL、SQL IR、分层记忆、Human-in-the-loop 
- 后端与基础设施：Python、FastAPI、PostgreSQL、pgvector、Redis Streams、Outbox、异步 Worker、Docker Compose 
- 安全与工程：RBAC、Subject Scope、权限感知缓存、幂等、审计、状态机、Pytest、Playwright E2E 
 
注：其中“规划”“逐步演进”“后续规划”用于标识尚在设计或迭代中的能力；正式投递时可根据实际完成情况改为“实现”或删除。 
