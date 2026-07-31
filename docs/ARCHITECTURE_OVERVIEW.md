# 智能财富管家整体架构说明

> 文档版本：v1.0
> 更新日期：2026-07-31
> 系统定位：可运行、可演示、可扩展的财富管理 Mock/MVP 系统

## 1. 架构目标

系统采用模块化单体架构，优先保证：

1. 业务规则可解释、可审计。
2. 客户、产品、风险、订单和资产数据保持一致。
3. Agent 可以替换和扩展，不直接掌控资金、权限和风险决策。
4. 数据库和向量数据库通过 Port/Adapter 抽象，支持后续迁移。
5. 当前可以本地演示，未来可以逐步拆分服务。

## 2. 总体架构

```text
┌──────────────────────────────────────────────────────────────────┐
│                         Vue 3 + Vite 前端                        │
│  客户中心｜产品展示｜风险测评｜企业认证｜订单｜员工后台｜审计       │
└──────────────────────────────┬───────────────────────────────────┘
                               │ HTTP/JSON + JWT
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                         FastAPI API 层                            │
│ /auth  /profile  /trading  /admin  /knowledge  /tasks  /health    │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                         Service 业务层                            │
│ 客户画像｜企业认证｜客户分层｜风险测评｜适当性｜交易｜资产汇总      │
│ 审计｜任务｜知识库｜Agent 编排                                    │
└──────────────┬───────────────────────┬───────────────────────────┘
               │                       │
               ▼                       ▼
┌──────────────────────────┐  ┌────────────────────────────────────┐
│       Port / 接口层       │  │          基础设施 Adapter 层         │
│ Repository Port           │  │ SQLAlchemy/PostgreSQL                │
│ VectorStore Port          │  │ MySQL Adapter（迁移中）              │
│ Agent Contract            │  │ pgvector Adapter                     │
└──────────────┬───────────┘  │ Milvus Adapter                       │
               │              │ Redis / Qwen                           │
               ▼              └────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│                         数据与外部系统                            │
│ PostgreSQL + pgvector｜MySQL 迁移库｜Milvus｜Redis｜DashScope/Qwen │
└──────────────────────────────────────────────────────────────────┘
```

## 3. 分层说明

### 3.1 前端层

目录：

```text
frontend/src/
```

主要页面：

- 客户中心：账户、个人画像、风险测评、企业认证、订单。
- 产品中心：产品展示、风险等级、客户类型和购买资格提示。
- 员工后台：客户管理、订单审核、产品管理、企业认证审核、审计和回收站。

前端职责：

- 展示业务状态和规则原因。
- 提交用户操作。
- 对明显不适配的产品提前置灰。
- 不承担最终权限、资产、风险和交易判断。

### 3.2 API 层

目录：

```text
app/api/
```

API 只负责：

- 身份认证和权限依赖。
- 参数校验。
- 调用 Service。
- 统一响应和错误转换。

不建议在 API 路由中直接编写复杂交易、风控和资产逻辑。

### 3.3 Service 业务层

目录：

```text
app/services/
```

核心服务：

| 服务 | 责任 |
|---|---|
| `AssetSummaryService` | 账户现金、持仓市值和资产快照汇总 |
| `RiskAssessmentService` | C1-C5 风险测评和 provisional 默认等级 |
| `SuitabilityService` | 风险等级、客户类型、资产门槛适配 |
| `TradingService` | 订单、确认、冻结、审核、执行和持仓 |
| `CustomerTierService` | 普通、黄金、白金、钻石、私人银行分层 |
| `AuthService` | 注册、登录、令牌和密码 |
| `KnowledgeRepository` | 知识库文档、分块和检索 |
| `AgentOrchestrator` | Agent 注册、选择和调用 |

业务决策原则：

```text
确定性后端规则决定结果
Agent 负责理解、解释和建议
```

### 3.4 Port 层

目录：

```text
app/ports/
```

当前接口：

```text
app/ports/agent.py
app/ports/vector_store.py
app/ports/repositories.py
app/ports/trading.py
```

Port 层只描述能力，不绑定具体厂商。

## 4. Agent 架构

### 4.1 接入契约

实验 Agent 应实现：

```python
async def run(
    message: str,
    context: AgentContext,
) -> AgentResult:
    ...
```

统一返回：

```text
AgentResult
├── agent_name
├── status
├── summary
├── data
├── evidence
├── tool_calls
├── confidence
├── requires_confirmation
└── errors
```

### 4.2 Agent 边界

Agent 可以：

- 理解用户意图。
- 查询知识库。
- 生成产品或风险解释。
- 给出分析建议。
- 调用只读分析工具。

Agent 不可以直接决定：

- 账户余额。
- 订单状态。
- 客户类型。
- 客户层级。
- 正式风险等级。
- 是否绕过适当性检查。
- 是否执行真实资金交易。

## 5. 数据架构

### 5.1 PostgreSQL 主库

当前主数据库：

```text
PostgreSQL + pgvector
```

主要业务表：

```text
users
roles / permissions / user_roles
customer_profile
customer_enterprise_verification
customer_risk_assessment
customer_asset_snapshot
customer_holding
product
product_suitability_rule
account
orders
trades
order_status_history
audit_logs
knowledge_base
knowledge_document
knowledge_document_version
knowledge_chunk
```

### 5.2 资产与交易关系

```text
User
 ├── Account
 ├── CustomerProfile
 ├── CustomerRiskAssessment
 ├── CustomerAssetSnapshot
 ├── CustomerHolding
 └── Order ─── Trade
```

实时派生资产口径：

```text
现金资产 = available_balance + frozen_balance
持仓资产 = 有效持仓 market_value 总和
总资产 = 现金资产 + 持仓资产
净资产 = 总资产 - 负债
```

### 5.3 风险测评关系

```text
注册/默认画像
      ▼
C1 provisional
      ▼
正式风险测评
      ▼
C1-C5 active
      ▼
产品适配 / 交易确认
```

适配顺序：

```text
客户类型
  → 风险等级
  → 资产/金额门槛
  → 订单状态
```

## 6. 交易流程

```text
客户浏览产品
      ▼
前端展示适配状态
      ▼
创建订单 pending_confirmation
      ▼
客户确认订单
      ├── 账户余额检查
      ├── 客户类型检查
      ├── 风险等级检查
      └── 产品金额检查
      ▼
账户冻结金额增加
      ▼
pending_review
      ▼
工作人员审核
      ├── rejected
      └── executing
              ▼
          持仓更新
              ▼
          Trade 记录
              ▼
          executed
              ▼
          派生资产快照
```

所有关键状态转换写入：

```text
order_status_history
audit_logs
```

## 7. 企业客户流程

```text
普通客户
   ▼
提交企业资料
   ▼
pending
   ▼
超级管理员审核
   ├── rejected
   └── approved
           ▼
customer_type = enterprise
customer_tier = enterprise_standard
source_type = enterprise_verified
```

客户不能直接修改企业类型。

## 8. Repository 与数据库迁移

### 8.1 当前结构

```text
Service
  ↓
Repository Port
  ↓
SQLAlchemy PostgreSQL Adapter
```

已建立的 Port：

```text
CustomerRepository
ProductRepository
RiskAssessmentRepository
AccountRepository
OrderRepository
HoldingRepository
AssetRepository
```

### 8.2 MySQL 状态

已完成：

- 隔离 MySQL 8 容器。
- 连接、建表和核心类型探针。
- users、customer_profile、product、customer_risk_assessment、account 最小 DDL。
- aiomysql 驱动接入。

尚未完成：

- 全量业务表迁移。
- MySQL Repository 完整实现。
- PostgreSQL/MySQL 双读对比。
- 回滚和生产切换方案。

### 8.3 Milvus 状态

已完成：

- Milvus 连接和集合创建。
- `wealth_documents` 正式集合。
- 12 条 PostgreSQL active 知识块迁移。
- 向量写入、加载和查询。

当前主检索仍为：

```text
PostgreSQL + pgvector
```

## 9. 向量与知识库流程

```text
原始文档
   ▼
文档解析与分块
   ▼
Embedding Provider
   ▼
PostgreSQL knowledge_chunk + pgvector
   ├── 主检索
   └── Milvus wealth_documents（迁移/验证）
```

未来切换由以下接口控制：

```text
VectorStore
├── PgVectorStore
└── MilvusVectorStore
```

## 10. 外部基础设施

| 组件 | 当前用途 | 当前状态 |
|---|---|---|
| PostgreSQL | 主业务库、知识库、pgvector | 主链路 |
| MySQL 8 | 迁移验证 | 隔离容器 |
| Redis | 会话、缓存、Streams、Outbox | 使用中 |
| Milvus | 向量迁移验证 | 已连接 |
| Qwen/DashScope | Embedding 和模型入口 | 配置中 |
| Docker | 基础设施隔离 | 使用中 |

## 11. 配置抽象

```env
DATABASE_BACKEND=postgresql
VECTOR_STORE_BACKEND=pgvector
AGENT_BACKEND=local
MILVUS_URI=http://127.0.0.1:19530
MILVUS_COLLECTION=wealth_documents
```

配置开关只是选择 Adapter 的入口。切换到 MySQL/Milvus 前必须完成迁移、契约测试和回归验证。

## 12. 安全与业务边界

- 本系统交易为 Mock。
- 所有金额和权限由后端确定。
- 风险适配由确定性规则决定。
- 企业客户必须经过审核。
- Agent 不得绕过权限、风控和交易确认。
- 生产环境不得保存明文演示密码。

## 13. 测试与验收

主要测试：

```powershell
conda run -n langchain_env python tests/test_http_smoke.py --base-url http://127.0.0.1:8010/api/v1
conda run -n langchain_env python tests/test_http_faults.py --base-url http://127.0.0.1:8010/api/v1
conda run -n langchain_env python tests/test_http_concurrency.py --base-url http://127.0.0.1:8010/api/v1
```

迁移探针：

```powershell
conda run -n langchain_env python scripts/mysql_core_probe.py
conda run -n langchain_env python scripts/migrate_knowledge_to_milvus.py
conda run -n langchain_env python scripts/query_milvus_knowledge.py
```

## 14. 后续路线

1. 完成 Agent 实验品接入和工具权限边界。
2. 完成 MySQL Repository 与全量迁移。
3. 完成 pgvector/Milvus Top-K 对比。
4. 完成 VectorStore 后端配置切换。
5. 增加风险预警状态机。
6. 再实现长期行为画像和动态风险建议。
