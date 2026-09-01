# 多 Agent 范式与受限 ReAct 演进设计

- 写入时间：2026-08-12（Asia/Shanghai）
- 状态：当前架构说明与后续演进基线

## 1. 当前系统范式

当前系统不是自由自治的 ReAct 架构，而是**权限感知的轻量规划多 Agent 编排架构**：

```text
用户请求
→ 权限与能力筛选（AgentCapabilityRegistry）
→ 轻量意图规划（CustomerIntentPlanner）
→ 受控任务路由与执行
→ 统一结果、审计、事件与人工接管
```

主 Agent 的角色是 **Supervisor / Router + Lightweight Planner**，负责能力过滤、任务拆分、风险分级、分派、澄清和结果汇总；不应直接持有高风险写操作能力。

## 2. 当前轻量 Plan

`CustomerIntentPlanner` 已输出规范化请求、任务列表、优先级、依赖、参数、澄清和人工接管语义。因此主 Agent 已有轻量 Plan。

高确定性场景允许走受控快速通道。例如 `DataAnalysisAgent` 可按批准模板或固定指标执行，但必须继续受到角色、目录、参数、结果数量与审计约束。

## 3. ReAct 的适用边界

ReAct 仅用于低风险、认知型、多跳检索或解释任务，且必须是**受限 ReAct（Bounded / Guarded ReAct）**：

- 最大 3～5 轮；
- 最大 5～8 次工具调用；
- 工具白名单；
- 默认只读；
- 每轮有超时与 token 预算；
- 必须产出 evidence；
- 失败时澄清或人工接管；
- 不记录模型私有推理文本，只记录受控工具调用与结果摘要。

推荐先用于：

```text
CustomerServiceAgent：RAG、多源信息查询、客户解释、人工接管判断
AdvisorAgent：建议前的信息收集、证据汇总、建议草案
RiskAgent：风险原因解释、只读证据汇总
```

## 4. 不适用自由 ReAct 的场景

以下必须保持受控工作流 / 状态机 / Command Handler：

```text
DataAnalysisAgent 的 SQL 模板执行
转账、赎回、申购、订单审核
风险预警处置与 AML 流程
管理员角色、权限和配置写操作
通知投递与外部系统写调用
```

特别是 DataAnalysisAgent：可以将“模板选择和参数澄清”作为极小范围受限推理，但 SQL 执行始终只能调用批准模板，禁止自由生成或执行 SQL。

## 5. 子 Agent 分层

| 类别 | 范式 | 典型 Agent | 约束 |
|---|---|---|---|
| Reasoning Agent | 受限 ReAct | CustomerService、Advisor（解释） | 只读、工具白名单、轮数限制、evidence |
| Decision Agent | Plan + Rules | suitability、风险分级 | 模型用于解释，最终决定由规则/评分服务输出 |
| Execution Agent | 受控工作流 | DataAnalysis、Business、Admin 写操作 | 确认、审批、状态机、幂等、审计、Outbox |

## 6. 推荐演进顺序

1. 先在 `CustomerServiceAgent` 落地只读、受限 ReAct；
2. 再扩展 `AdvisorAgent` 的资料收集与建议草案；
3. 维持 `DataAnalysisAgent`、`BusinessAgent` 和高风险 `RiskAgent` 的工作流约束；
4. 最终统一为主 Agent 输出轻量 Plan，任务显式标记 `bounded_react` 或 `controlled_workflow` 执行模式。

## 7. 首个受限 ReAct 试点实现

已在 `CustomerTaskExecutor` 的知识类意图中落地首个受限 ReAct 风格循环。范围仅限：`company_info`、`company_business`、`faq`、`workflow`、`policy`、`risk_public`。

执行语义：

```text
retrieve（只读知识检索）
→ observe（仅保留工具结果状态与 evidence 摘要）
→ 对 low_confidence 最多精炼一次查询
→ retrieve
→ answer / clarification / handoff
```

护栏：最大 3 轮、最大 3 次工具调用、仅允许 `search_customer_knowledge`、非低置信失败不重试、不保存模型私有推理文本。

新增回归测试：`tests/test_customer_task_executor_react.py`。

## 8. 主 Agent Plan 显式执行模式

`IntentTask` 新增 `execution_mode` 字段，主 Agent 的轻量 Plan 现在会明确标记任务执行范式：

```text
知识问答 / 政策 / 流程 / FAQ / 公司信息 / 公共风险说明 → bounded_react
资产、订单、工单、风控限制、数据分析、业务操作、人工接管 → controlled_workflow
```

该字段已透出到意图规划 API 响应，并被保存到短期记忆的 pending plan 和执行状态中。这样前端、审计和后续 Supervisor 都可据此区分受限推理任务与受控流程任务。

新增回归测试：`tests/test_customer_intent_execution_modes.py`。

## 9. 受限 ReAct 审计与可观测性

`CustomerAgentExecutionService` 会在本次执行包含 `bounded_react` 任务时写入一条 `audit_logs` 记录：

```text
action: customer_service.bounded_react_executed
resource_type: customer_service_conversation
resource_id: conversation_id
```

审计中仅记录任务 ID、意图、结果状态、受控错误码、工具名、工具调用次数、查询长度、只读标志和“不记录私有推理”的标志；不记录用户原始问题、检索内容、模型思维链、完整工具参数或客户业务数据。

这样可以统计 bounded ReAct 的调用次数、失败率、工具预算使用情况和转人工趋势，同时保留金融场景要求的最小化审计原则。

## 10. 目录驱动的安全 NL2SQL / 模板执行链路

系统已存在数据目录模型：

```text
DataCatalogDomain
DataCatalogTable：物理表名、展示名、描述、业务用途、粒度、使用/禁用说明、角色、是否可查询、别名、向量
DataCatalogColumn：字段名、展示名、描述、业务含义、语义类型、单位、敏感性、是否可查询、聚合能力、别名、样例值、向量
DataCatalogRelation：表关系、字段关系、基数、连接说明
DataCatalogExampleQuery：批准问题、意图、目标表/字段、SQL 模板、角色、批准状态、向量
```

`DataCatalogService.search` 已采用目录文本召回，并在可用时使用 pgvector 混合召回：

```text
用户问题
→ 按角色过滤目录
→ 文本召回候选表 / 批准示例
→ 可用时向量召回候选表 / 字段 / 示例
→ 对命中表做字段召回
→ 返回表用途、粒度、字段含义、关系和批准示例
```

本次将该目录检索接入 `DataAnalysisAgent` 的真实 API 执行链路：

```text
用户问题
→ DataCatalogService.search（角色过滤、文本/向量混合召回）
→ 获取候选表和其可查询、非敏感字段
→ 选择批准模板
→ SQL 实际引用表/字段必须同时通过：模板声明白名单 + 目录召回候选白名单
→ 参数边界、LIMIT、审计
```

因此当前不是自由生成 SQL 的 NL2SQL；准确说是**目录检索增强的批准模板 SQL 执行**。目录已经存储表名/作用和字段名/作用，并在执行前作为动态候选白名单参与校验。

后续若扩展到受控 NL2SQL 生成，必须继续保持：仅从目录候选表和字段构造结构化查询计划、人工或策略批准后编译 SQL，禁止模型直接产出可执行 SQL。

## 11. P2 评估共享策略与 NL2SQL 安全边界

新增 `app/services/nl2sql_policy.py` 作为不依赖 ORM、数据库、缓存、模型或 FastAPI 启动过程的纯策略模块。它被 DataAnalysisAgent 与离线评估共同使用，涵盖：SQL-shaped 输入拒绝、批准模板只读校验、固定指标关键词路由、角色允许判断和目录候选范围判断。

这样 P2 离线评估不再复制服务规则；后续 SQL IR、权限感知缓存和 HTTP 评估应继续复用该策略层，而不是在脚本中手写镜像逻辑。

## 12. 权限变更后的缓存失效

权限敏感缓存不仅依赖当前角色/权限指纹，还依赖 Redis 中按用户维护的 `permission-context-version`。管理员更新用户角色后递增该版本；下一次目录检索和 DataAnalysisAgent 请求会产生新 key，旧缓存保留至 TTL 到期但不会被新上下文命中。

该机制避免在 RBAC 更新后同步删除多个命名空间的复杂扫描，同时保持“缓存命中授权结果与无缓存授权结果一致”的原则。

## 13. 角色权限变更的多用户缓存失效验证

角色权限变更会影响拥有该角色的全部用户，而不仅是发起请求的用户。管理员更新角色权限后，系统查询该角色关联用户并逐个递增 Redis permission-context version。真实回归已使用两个不同 `risk_manager` 用户验证两个版本都变化；临时测试用户通过数据库 session 创建并在 finally 清理，不为测试增加生产管理 API。

## 14. P2 真实 HTTP 安全评估

`run_nl2sql_http_eval.py` 将 DataAnalysisAgent 的真实 HTTP 请求、结果分类、审计记录和延迟汇总为六维报告。拒绝请求不会被要求返回 SQL query plan；只有实际分析执行才检查 `free_sql=false`。当前安全事故数由“预期结果不匹配、执行分析的 free_sql 违规、审计异常”组成。

## 15. 2026-08-13：批准模板状态的真实 HTTP 门禁

P2 HTTP 评估现在会临时禁用一条已批准模板并调用同题请求，确认统一 Agent 入口不会执行 inactive 模板；测试结束恢复原状态。该验证补足了“模板状态校验不只是服务内部 if，而是在真实路由、目录与执行链路中生效”的证据。

## 16. 2026-08-13：四类目录/模板执行门禁的真实证据

HTTP 评估已用被临时修改模板 ID 验证：inactive、unapproved、non-queryable table 与 sensitive column 都不能执行目标 approved template。模板拒绝后的固定指标兜底或客服回退不被误认作模板执行；只有 `query_plan.mode=approved_template` 且 `template_id` 匹配时才算执行。

## 17. 2026-08-13：声明式参数 schema 与授权 subject 绑定

批准模板的 `metadata_json.parameters` 是参数安全契约：只有声明为 `natural_language` 的参数会从问题文本解析；integer 和 date_range 必须满足模板边界；`authorized_scope_only` 不信任用户文本中的任意 subject ID，而是由后端授权上下文绑定。

当前基础实现将 subject 绑定到当前 user.id；后续应由客户经理分配范围等确定性授权服务提供可查询 subject 集合。
