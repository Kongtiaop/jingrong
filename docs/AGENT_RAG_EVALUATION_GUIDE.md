# Agent 与 RAG 效果评测指南 
 
更新时间：2026-08-26 
 
## 1. 当前结论 
当前项目已经具备安全回归和规则契约测试基础，但尚未形成完整的 Agent 与 RAG 效果评测体系。 
已有：意图规则基线、StructuredIntent 与 Shadow Mode 契约、受控数据分析安全评测、RAG 集成测试、HTTP 和 E2E 回归。 
缺少：统一 Agent Golden Dataset、RAG Golden Dataset、任务完成度、工具调用质量、RAG 忠实度、引用质量、成本和延迟综合报告。
 
## 2. 我们前面讨论后的当前能力盘点 
 
### 2.1 已经具备的评测基础 
- 复杂中文意图规则基线：tests/fixtures/complex_customer_intent_cases.json 和 tests/test_complex_customer_intent_baseline.py。 
- StructuredIntent 安全契约：测试 Schema、非法模板、交易确认约束、未知上下文交易阻断和 fallback allowlist。 
- Shadow Mode：模型候选可以被记录和比较，但默认不改变真实执行计划。 
- 受控数据分析评测：scripts/run_nl2sql_eval.py 和 scripts/run_nl2sql_http_eval.py 已统计语义准确率、拒答率、澄清率、P95 延迟和安全事故数。 
- RAG 集成测试：tests/test_customer_rag_retrieval.py 已覆盖 embedding 传递、Citation 和 evidence 状态。 
- 系统回归：已有真实 HTTP、Redis、Worker、权限、交易草稿和 Playwright E2E 测试。 
 
### 2.2 当前最缺的能力 
- 缺统一 Agent Golden Dataset，当前主要覆盖意图模板，不足以评估完整任务是否完成。 
- 缺统一 Agent Eval Runner，无法一次汇总路由、拆解、工具、约束、澄清、转人工、延迟和成本。 
- 缺 RAG Golden Dataset，当前没有系统化标注问题对应的文档、Chunk、版本、Scope 和必需事实。 
- 缺 RAG 检索排序指标，例如 Recall at K、MRR、NDCG、当前版本命中率和 Scope 泄露率。 
- 缺 RAG 生成质量指标，例如忠实度、完整性、相关性、引用准确率、引用覆盖率和正确拒答率。 
- 缺统一成本观测，包括 Token、模型调用、工具调用、Embedding、数据库访问、缓存命中和每请求成本。 
- 缺共享记忆的有/无记忆对照评测；当前共享记忆仅设计完成，未形成可验证收益。 
- 缺断点续跑的故障注入评测；当前是设计完成，尚未验证恢复成功率和重复执行风险。
 
## 3. 评测分层 
1. 硬安全门禁：零越权、零未经确认交易、零自由 SQL、零知识 Scope 泄露。 
2. Agent 行为评测：路由、任务拆解、工具、参数、约束、澄清、人工转接和任务完成。 
3. RAG 检索与生成评测：先测是否找对证据，再测答案是否忠实和完整。 
4. 综合系统评测：真实 HTTP、数据库、Redis、Worker、前端 E2E、并发、延迟和成本。 
 
## 4. 硬安全发布门槛 
以下指标不应用平均值掩盖，应作为发布门槛： 
- security_incident_count 为零。 
- forbidden_tool_call_rate 为零。 
- scope_leak_rate 为零。 
- unverified_trade_submission_count 为零。 
- free_sql_execution_count 为零。 
- unsupported_high_risk_answer_rate 为零。 
任一门槛不满足时，不应扩大 fallback、共享记忆或自动化业务能力。
 
## 5. Agent 应该如何评测 
 
### 5.1 Agent Golden Dataset 
建议新增 tests/fixtures/agent_eval_cases.json 和 scripts/run_agent_eval.py。 
每条样本除 query 外还应标注用户角色、授权客户范围、期望路由、期望任务、禁止 Agent、禁止工具、期望状态、强制约束和禁止副作用。 
交易样本应检查是否进入 needs_confirmation，是否携带必须经过验证确认的约束，以及是否没有 create_order、freeze_funds、submit_trade 等禁止副作用。 
多意图样本应标注所有子任务、依赖关系和最终汇总状态，不能只因完成一个子任务就算成功。 
 
### 5.2 Agent 指标 
- route_accuracy：正确路由请求数除以总请求数。 
- full_task_success_rate：完整完成任务数除以总任务数。 
- partial_success_rate：部分完成任务数除以总任务数。 
- task_decomposition_precision 和 task_decomposition_recall：任务拆解的准确率和召回率。 
- forbidden_tool_call_rate：禁止工具调用数除以总请求数。 
- invalid_argument_rate：工具参数错误率。 
- constraint_preservation_rate：所有强制约束均被保留的样本数除以需要约束的样本数。 
- clarification_precision、clarification_recall、clarification_actionability：澄清是否该触发、是否不漏触发、是否可执行。 
- handoff_precision、handoff_context_completeness：是否该转人工、交接上下文是否完整。 
- P50、P95、P99 延迟、模型调用次数、工具调用次数、Token、Embedding 次数、缓存命中和每请求成本。
 
## 6. RAG 应该如何评测 
 
### 6.1 RAG Golden Dataset 
建议新增 tests/fixtures/rag_eval_cases.json 和 scripts/run_rag_eval.py。 
每条样本应标注 query、intent、允许 Scope、期望文档、期望 Chunk、必需事实、是否可回答、是否应转人工、当前生效版本和禁止知识域。 
数据集必须覆盖正向问题、无答案问题、旧版本问题、跨 Scope 问题、问候问题、Embedding 降级问题和文档更新问题。 
 
### 6.2 RAG 检索层指标 
- Recall at K：Top K 是否包含正确证据，客服场景重点看 Recall at 5。 
- Precision at K：Top K 中相关 Chunk 的比例。 
- MRR：第一个正确证据排名倒数的平均值。 
- NDCG at K：多证据和分级相关性排序质量。 
- active_version_hit_rate：当前生效版本 Chunk 占返回结果的比例。 
- scope_leak_rate：返回未授权知识的请求数除以总请求数，目标为零。 
 
### 6.3 RAG 生成层指标 
- faithfulness：答案中被证据支持的事实数除以答案事实总数。 
- answer_relevance：是否直接回答用户问题且没有无关扩展。 
- completeness：覆盖必需事实数除以必需事实总数。 
- citation_precision：有效引用数除以总引用数。 
- citation_recall：带有效引用的关键事实数除以应引用关键事实数。 
- correct_abstention_rate：无答案问题上正确拒答或转人工的比例。
 
## 7. 当前代码已经体现的 RAG 评测点 
- 问候分流：CustomerRagService.greeting_message 应避免对你好、你是谁、谢谢、再见等问题触发检索或工单。 
- 查询归一化：normalize_customer_query 将客户规模、客服电话等表达扩展为同义检索词，应通过 A/B 比较扩展前后的 Recall at 5、MRR 和额外延迟。 
- Embedding 降级：有向量时走 hybrid search，无向量时走 text search；需要比较正常路径和降级路径的成功率、延迟、答案质量和转人工率。
 
## 8. Shadow Mode、共享记忆和断点续跑评测 
Shadow Mode 应比较规则 Planner、LLM StructuredIntent 和 Policy Resolver，观察模板差异、实体差异、约束保留、交易误升级、澄清变化、延迟、成本和 fallback 错误。 
共享记忆当前设计完成但未完整实现，落地前应比较有记忆和无记忆两组，观察重复询问、转人工摘要完整度、投顾准备时间、风险上下文遗漏、任务完成率、错误记忆传播、越权读取、用户纠正、延迟和成本。 
断点续跑当前设计完成但统一执行层尚未实现，应注入 Worker 崩溃、超时、外部等待、重复回调、权限变化和已完成步骤重放。
