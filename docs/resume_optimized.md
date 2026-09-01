# 叶绍康 
 
## 个人技能 
 
- AI Agent 与多智能体系统：熟悉 ReAct、Plan-and-Execute、CoT、Self-Ask 等推理范式；基于 LangGraph 构建 Supervisor 与专业 Agent 协作系统，掌握状态机、条件分支、循环校验、异常兜底、MCP、Function Calling 和 Tool 标准化。 
- RAG 与知识库：具备 Agentic RAG 实战经验，覆盖 MinerU 文档解析、父子块切分、Dense + BM25 + RRF 混合检索、Reranker、引用溯源；熟悉 Milvus 与 PostgreSQL/pgvector。 
- 大模型推理与优化：了解 LoRA/QLoRA、4bit 量化、多 LoRA/TIES-Merging、vLLM、SGLang、GPTQ、AWQ 等微调、量化和推理部署技术。 
- 模型原理：熟悉机器学习、深度学习和 Transformer 基础，了解 GQA、MLA、MoE、MoA 等架构及其在模型容量与推理效率之间的权衡。 
- 后端工程化：熟练使用 FastAPI、SQLAlchemy Async、PostgreSQL、MySQL、Redis Streams、Outbox、Repository/Unit of Work、幂等设计和审计日志。 
 
## 项目经历 
 
### 金源智枢 · 多 Agent 协同财富管理系统 
项目时间：2025.08 – 2026.08 
项目背景：面向投顾、客服、风控、运营、客户经理等角色，建设覆盖投前推荐、投中诊断、投后分析和业务办理的 AI 协作平台，解决人工成本高、知识难沉淀、数据分析链路长和 AI 操作不可控等问题。 
技术栈：LangChain、LangGraph、FastAPI、PostgreSQL/pgvector、MinIO、Redis、React、Playwright、Docker、CI/CD、Qwen（1.5B/7B/32B）、RBAC、Outbox 
 
核心职责与成果： 
- 设计 Supervisor + 6 类专业 Agent（客服、投顾、风控、数据分析、业务操作、监督）协作体系，引入 AgentCapabilityRegistry 进行权限前置过滤，限制 Agent 的数据和工具访问范围，降低 AI 越权和错误执行风险。 
- 建设客服、投顾、风控和数据分析 Agent：客服重复咨询处理能力由日均约 40 件提升至 120 件；投顾报告撰写由 20–30 分钟压缩至 2–3 分钟，日均服务客户数由约 20 人提升至 60+；风控支持 AML 实时扫描和事中拦截；数据分析支持自然语言取数和图表生成，将部分取数链路由约 2 天压缩至 30 秒。 
- 设计“自然语言请求 → 申购/赎回/转账草稿 → 风险与参数校验 → 用户确认 → 审核流程”，Agent 只生成草稿，不直接执行资金操作；结合幂等、状态机、确认机制和审计，解决 AI 代客操作的合规与重复提交问题。 
- 设计监督 Agent 和三层执行护栏，通过硬预算、状态收敛检测和信息增益判定限制 Agent 无限循环；规划基于任务快照的断点恢复，并在恢复前重新校验权限和客户范围。 
- 规划四层共享记忆架构：L0 原始会话流水、L1 结构化事实卡片、L2 场景档案、L3 长期用户画像，并结合三类 Scope 控制跨 Agent 共享边界；坚持业务表优先，记忆只用于上下文补充，解决客服、投顾和风控之间的记忆孤岛问题。 
- 设计受控自然语言数据分析 / NL2SQL 方案，采用数据目录动态白名单、批准模板只读执行和参数契约三重约束；当前以模板化查询为基础，后续演进为 SQL IR，兼顾非技术人员取数效率与敏感数据、越权查询和 Prompt Injection 防护。 
- 设计职责分离的 RAG 架构：知识库负责公司制度、业务流程和 FAQ 等解释性内容，订单、余额、持仓等权威事实始终从 PostgreSQL 读取；规划结合混合检索、引用门禁、低置信度拒答和人工转接降低幻觉及合规风险。 
- 通过 Redis Streams、Outbox、异步 Worker、重试/死信和 Playwright E2E 测试完善任务可靠性与回归验证；当前记录的系统指标包括平均响应约 1.2 秒、P95 约 1.8 秒、P99 约 3.5 秒、多意图任务完成率约 87%、任务幂等成功率 100%。 
 
### 企业内部知识库 RAG 
项目时间：2025.08 – 2026.01 
项目背景：面向投顾、客服和风控人员建设企业内部知识检索平台，覆盖公司制度、金融政策、产品说明书、业务流程和 FAQ 等 2000+ 份文档，解决关键词搜索搜不全、搜不准和版本污染问题。 
技术栈：Python、FastAPI、Qwen、PostgreSQL/pgvector、Redis、Docker Compose、BGE-Reranker、BGE-M3 
核心职责与成果： 
- 针对客服快速查条款和投顾精读完整章节的差异，设计双轨检索架构：客服采用 Dense + BM25 + RRF 混合 Chunk RAG，投顾采用“搜索 → 大纲 → 精读”渐进式检索，兼顾响应速度和长文档上下文完整性。 
- 通过 file_hash + chunk_hash 两级哈希和多级匹配实现增量更新与版本控制，文档局部修改时仅重新向量化变更片段，将一次更新由约 30 分钟压缩至 2 分钟，并保留历史快照及当前生效版本。 
- 建立 Bad Case 回收、错误分类、回归测试集和证据状态门禁；检索不足时拒答并转人工，避免无依据生成，文档检索 Top-5 命中率约 93%，平均检索耗时约 380ms。 
 
## 技术关键词 
 
Agent：LangGraph、LangChain、Supervisor、ReAct、Plan-and-Execute、MCP、Function Calling、Tool Calling 
RAG：Agentic RAG、Dense Retrieval、BM25、RRF、Reranker、Milvus、pgvector、引用溯源、增量索引 
后端：Python、FastAPI、SQLAlchemy Async、PostgreSQL、MySQL、Redis Streams、Outbox、Docker 
工程与安全：RBAC、权限范围控制、幂等、审计、状态机、异步 Worker、Playwright、CI/CD
