# 文档目录说明

> 当前有效方案以 `docs/P0-P5_IMPLEMENTATION_PLAN.md v2.0` 和 `docs/DECISIONS.md` 为准。

## 当前文档

```text
PLAN.md
    项目总体计划、阶段进度和决策变更记录

P0-P5_IMPLEMENTATION_PLAN.md
    当前有效的 P0-P5 分级实施方案

ARCHITECTURE_DATABASE_DRAFT.md
    当前架构、数据库和边界设计草案；冲突时以 P0-P5 v2 为准

DECISIONS.md
    当前技术决策、弃用方案和文档维护规则

TESTS.md
    测试策略、测试命令、测试结果和未覆盖范围

ISSUES.md
    问题、Bug、讨论过程、解决方案、验证结果和延期事项

P0_IMPLEMENTATION.md
    P0 实施说明

P0_INFRASTRUCTURE.md
    P0 Docker/基础设施说明
```

## 文档优先级

```text
P0-P5_IMPLEMENTATION_PLAN.md
    ↓
DECISIONS.md
    ↓
ARCHITECTURE_DATABASE_DRAFT.md
    ↓
PLAN.md 的历史追加记录
```

## 每次实现完成后的记录规则

### 必须更新

```text
docs/PLAN.md
```

至少记录：

- 本次完成内容
- 当前阶段状态
- 下一步计划
- 新增或修改的关键文件

### 有测试或验证时必须更新

```text
docs/TESTS.md
```

记录：

- 测试编号
- 测试环境
- 命令或步骤
- 预期结果
- 实际结果
- PASS/FAIL
- 未覆盖范围

### 发现问题、讨论方案或修复 Bug 时必须更新

```text
docs/ISSUES.md
```

记录：

- 问题描述
- 讨论过程
- 最终结论
- 处理方式
- 验证结果
- 当前状态

如果问题形成新的架构决策，还要同步更新：

```text
docs/DECISIONS.md
```
## 跨窗口上下文维护规则

除非用户明确说明“本次讨论不录入”，否则每次项目讨论都必须同步写入文档，以便切换新窗口后恢复上下文。

新窗口开始时优先阅读：

```text
docs/README.md
docs/DECISIONS.md
docs/PLAN.md
docs/ISSUES.md
docs/TESTS.md
```

具体记录规则和文档分工见 `docs/DECISIONS.md` 中的 `DECISION-20260729-001`。
## 业务知识总结文档

以下文档是对项目根目录业务资料的结构化总结，供后续模型、规则、API 和 Agent 使用：

```text
BUSINESS_KNOWLEDGE_COMPANY.md
    公司基本信息、牌照资质、组织、个人/企业服务、FAQ 字段

BUSINESS_KNOWLEDGE_PRODUCTS.md
    个人理财、高净值服务、企业金融、产品字段和产品匹配原则

BUSINESS_KNOWLEDGE_POLICIES.md
    KYC、AML、适当性、销售、双录、冷静期、复评、投诉和档案

BUSINESS_KNOWLEDGE_RISK.md
    用户画像维度、主客观分层、C1-C5/R1-R5、AML 规则和风险预警流程
```

总结文档是实现参考，不替代原始制度文件。涉及政策、牌照、金额、期限和保存要求时，必须回到当前有效原文和版本。



## 开发环境重置与演示数据

推荐使用以下命令恢复干净的应用状态并重新导入虚拟用户数据：

```powershell
C:\Users\tx360\.conda\envs\langchain_env\python.exe -m scripts.reset_dev_state
```

该命令会：

- 清空应用业务表和认证表；
- 清空 Redis 当前数据库；
- 保留知识库文档和向量；
- 重新导入演示账号、产品、客户画像、资产、持仓和交易账户。

如果连知识库文档和向量也要清除：

```powershell
C:\Users\tx360\.conda\envs\langchain_env\python.exe -m scripts.reset_dev_state --include-knowledge
```

如果只清 PostgreSQL，不清 Redis：

```powershell
C:\Users\tx360\.conda\envs\langchain_env\python.exe -m scripts.reset_dev_state --no-redis
```

该脚本仅允许在 `development`、`dev` 或 `local` 环境执行。
