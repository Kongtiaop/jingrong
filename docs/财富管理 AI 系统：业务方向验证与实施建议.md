# 财富管理 AI 系统：业务方向验证与实施建议

> **文档用途**：业务方向确认、优先级决策、实施规划
> **适用项目**：智能财富管理平台（Wealth Manager）
> **文档日期**：2026-08-14
> **状态**：分析完成，待排期实施

## 目录

1. 核心结论
2. 五个业务方向的行业证据
3. 深层洞察：行业真正在争什么
4. 实施优先级建议
5. 各方向详细实施方案
6. 总结

## 1. 核心结论

### 1.1 直球回答

**业务方向是对的，而且和行业正在做的事高度吻合。**

上一轮提到的五个方向，每一个都能从公开资料里找到对应的行业实践。但你的优势在于——**你已经有“执行”的工程根基，竞品还在“演示”阶段。**

### 1.2 行业证据总览





| 方向                  | 行业实践                | 行业玩家             |
| --------------------- | ----------------------- | -------------------- |
| 推荐 → 可执行策略     | 自然语言下单、任务交办  | 华泰、国泰海通       |
| 投后诊断              | 持仓诊断、账户诊断      | 国信、国泰海通       |
| 策略模板 + 场景化投资 | 投资场景、技能广场      | 华泰、国金、中信建投 |
| 人机协同              | 机器增强人、持牌复核    | 国联民生、行业共识   |
| 市场分析 + 投研       | 投研智能体、AI 原生研报 | 国泰海通、招商证券   |

## 2. 五个业务方向的行业证据

### 2.1 方向 1：推荐 → 可执行策略

#### 行业证据

华泰证券“AI涨乐”的核心定位是 **“真智能、会交易”** 。2026 年 1 月焕新发布会上，AI涨乐负责人明确说：**“让普通散户也能享受到专业的机构金融服务”**——这不是停留在建议层面，而是**客户确认策略后通过自然语言指令完成下单操作**。

华泰把“任务交办”作为三大核心工作区之一，用户可交办 AI 完成打板、清仓等复杂交易操作。背后是**多专家 Agent 协作架构**——主 Agent 调动多个专家 Agent，分别负责图表分析、财报查询、技术面判断。

国泰海通“灵犀 APP 2.0”同样支持 **“言出即达的交易转账功能”** ，覆盖“投前、投中、投后”全流程。

#### 你与竞品的差距

竞品已经把 AI 和交易执行深度绑定了，你的 AdvisorAgent 还停在“推荐 → 展示”阶段，AI 和交易之间有一个“人工确认 + 手动操作”的断点。

#### 你的优势

华泰 AI涨乐上线百天注册用户 240 万，但资金一致性基础设施（事务、幂等、流水）做得怎么样，公开资料看不出来。你在这方面的工程基础比竞品更扎实。

#### 实施建议

text

```
优先级：P0（立即做）
工期：1 周
关键动作：
  1. 打通 AdvisorAgent → BusinessAgent 执行链路
  2. 复用 needs_confirmation 做确认流程
  3. 策略执行后自动跟踪，持仓展示“策略来源：AI 推荐”
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

------

### 2.2 方向 2：投后诊断

#### 行业证据

国信证券“鑫投顾 AI 助手”覆盖**个股诊断、基金诊断和账户诊断**。AI 投顾的产品形态已从“聊天机器人式问答”升级为具备**持仓诊断、策略生成、动态调仓提醒、风险预警的全链路决策辅助**。

国泰海通“灵犀 APP 2.0”提供**贯穿“投前、投中、投后”的全流程智慧陪伴与策略跟踪**，其数字人能“完成个股专业诊断、解读行业产业动态、结合市场实时数据提供个性化交易参考建议”。

#### 你与竞品的差距

你有持仓查询和风险预警（RiskAgent），但没有“诊断报告”——把数据变成可读的分析和建议。

#### 实施建议

text

```
优先级：P1（下个 Sprint）
工期：1-2 周
关键动作：
  1. 扩展 DataAnalysisAgent 做持仓分析
  2. 结合 RiskAgent 做风险诊断
  3. 输出结构化诊断报告（复用审计报告样式）
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

------

### 2.3 方向 3：策略模板 + 场景化投资

#### 行业证据

华泰 AI涨乐明确回答“以后扩展不堆功能，而是按‘风险管理’‘提资金效率’这些**用户场景**来”。产品负责人说：“未来的能力扩展将坚持‘交易场景’的原则”。

国金证券的 AI 投顾围绕“买什么、什么时候买、什么时候卖、已持有的标的怎么办”等**投资痛点**构建服务体系。

中信建投“蜻蜓 Skill 广场”以 **“一句话调技能”** 为核心理念，用户用日常语言描述需求即可获取结构化分析结果。

#### 你与竞品的差距

客户提问 → Agent 回答，被动响应。竞品在做“任务找人”（AI 主动提供场景化入口），你在做“人找功能”。

#### 实施建议

text

```
优先级：P2（后续）
工期：1-2 周
关键动作：
  1. 定义 5-10 个投资场景
  2. 每个场景对应一个策略模板（资产配置 + 产品选择）
  3. 客户选择场景 → 自动生成配置方案 → 确认后执行
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

------

### 2.4 方向 4：人机协同

#### 行业证据

行业共识是 **“机器增强人”** 而不是“机器替代人”。国联民生证券明确说，AI 投顾 2.0 的根本变化是 **“从‘机器替代人’转向‘机器增强人’”** 。

高净值客户仍然希望接受客户经理的服务， **“信任不仅现在没有办法被替代，未来也很难被替代”** 。

AI 投顾要跨越“能用”到“敢用”的鸿沟，核心是**责任链条被制度性地明确**。涉及重大调仓决策时，最终仍需依赖**持牌投顾的专业判断**。

#### 你与竞品的差距

你的“低置信度转人工”是转客服工单，不是转投顾复核。竞品在做人机协同的深度嵌入，你只有“AI 不行就转人”的简单兜底。

#### 实施建议

text

```
优先级：P2（后续）
工期：1-2 周
关键动作：
  1. 低置信度推荐进入投顾复核队列（而非客服工单）
  2. 投顾可在 AI 方案上修改并保存修改记录
  3. 人工修改数据回流训练
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

------

### 2.5 方向 5：市场分析 + 投研

#### 行业证据

国泰海通“Nova 投研 AI 全域产品生态”已上线**11 个专项智能体和 30 余个投研专用 Skills**。

招商证券国际的“AI 原生投研 2.0 执行系统”将主动选股改造为**可审计、可复核的自动化生产线**——AI Agent 在工程约束下完成整个研究生产过程，最终结论由持牌分析师签署负责。

AI 投研正从“概念验证”迈入“规模化落地”阶段。已有**32 家券商完成算法与模型备案，备案项目总数超过 50 个**。

#### 你与竞品的差距

基本没有。你的 DataAnalysisAgent 目前只做内部固定指标查询，没有外部市场数据接入，也没有投研分析能力。

#### 实施建议

text

```
优先级：P3（视条件）
工期：2-3 周
关键动作：
  1. 接入行情数据（模拟或真实）
  2. 扩展 DataAnalysisAgent 做市场分析
  3. 输出标准化市场卡片
  注意：数据源是最大门槛，可先做“演示级”版本
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 3. 深层洞察：行业真正在争什么

### 3.1 争的是“交易执行”，不是“建议”

华泰 AI涨乐定位非常清晰： **“以交易为核心的 AI 原生投资工具”** ，不追求功能最全，而是 **“在关键交易决策场景中有用、好用、敢用”** 。

行业已经从“拿算法讲资本故事”进入“用 AI 实现规模化交易服务”的阶段。 **“真智能 会交易”** 是华泰发布会标题里的原话。

**对项目的启示**：你的工程根基（事务、幂等、流水、Outbox）正好是“会交易”的护城河。竞品在做“AI 投顾”的概念，你可以做“AI 执行”的实践。

### 3.2 争的是“千人千面”，不是“合规门槛”

行业共识：财富管理正从“卖方销售”转向“买方投顾”，核心是 **“千人千面”的个性化服务**。

华泰的实践：AI涨乐接入微信后，核心差异化是**基于账户授权后的个性化服务能力**——绑定账户后，AI 可调取用户自选股、持仓成本、历史盈亏等数据，生成与用户资产状况相关度更高的策略参考。

国泰海通灵犀 2.0 也强调 **“依托记忆智能体精准捕捉客户的风险偏好与交易习惯”** 。

**对项目的启示**：你的“适当性匹配”（风险等级 + 产品风险）是合规门槛，不是个性化。真正的个性化需要**结合客户的历史行为和偏好**——这正是你做完共享记忆后能做的事。

### 3.3 争的是“全生命周期”，不是“单点工具”

竞品正在从“单点应用”走向“全生命周期深度协同”。AI 投顾被定义为覆盖 **“投前、投中、投后”** 的全流程服务。

**对项目的启示**：你的“投中”（订单、赎回、转账）很强，但“投前”（策略生成、市场分析）和“投后”（持仓诊断、收益分析）基本空白。这是业务深度上最大的差距。

## 4. 实施优先级建议

### 4.1 优先级总览





| 优先级 | 方向                  | 工期   | 业务价值 | 技术依赖                      | 决策依据             |
| ------ | --------------------- | ------ | -------- | ----------------------------- | -------------------- |
| **P0** | 推荐 → 可执行策略     | 1 周   | 🔴 最高   | 现有 Agent + 交易闭环         | 行业已在做，你最擅长 |
| **P1** | 投后诊断              | 1-2 周 | 🔴 高     | DataAnalysisAgent + RiskAgent | 行业已在做，你有基础 |
| **P2** | 策略模板 + 场景化投资 | 1-2 周 | 🟡 中     | P0 成果 + 产品定义            | 方向对，需产品协同   |
| **P2** | 人机协同              | 1-2 周 | 🟡 中     | 现有工单系统                  | 方向对，需组织协同   |
| **P3** | 市场分析 + 投研       | 2-3 周 | 🟢 低     | 数据源 + DataAnalysisAgent    | 先做演示版，再升级   |

### 4.2 建议实施顺序

text

```
Week 1（立即开始）:
  └── P0：推荐 → 可执行策略
      ├── Day 1-2：打通 AdvisorAgent → BusinessAgent
      ├── Day 3-4：确认流程 + 执行跟踪
      └── Day 5：测试 + 验收

Week 2-3（下个 Sprint）:
  └── P1：投后诊断
      ├── Week 2：持仓分析 + 风险诊断
      └── Week 3：诊断报告输出 + 前端展示

Week 4+:
  ├── P2：策略模板 + 场景化投资（需产品团队协作）
  ├── P2：人机协同（需组织流程支持）
  └── P3：市场分析（视数据源条件，先做演示版）
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 5. 各方向详细实施方案

### 5.1 P0：推荐 → 可执行策略

#### 目标

客户在 AdvisorAgent 推荐结果上点击“执行”，系统自动创建订单、走完交易流程，并在持仓中标记策略来源。

#### 当前能力

- ✅ AdvisorAgent 已能生成推荐
- ✅ BusinessAgent 已能创建订单（申购）
- ✅ 交易闭环已完成（事务、幂等、流水）
- ✅ 确认机制已存在（`needs_confirmation`）

#### 需要补的





| 缺口         | 说明                            |
| ------------ | ------------------------------- |
| 策略 ID 传递 | 推荐结果需要可追溯的 ID         |
| 执行入口     | 前端“一键执行”按钮              |
| 来源标记     | 订单增加 `strategy_source` 字段 |
| 执行跟踪     | 执行后状态反馈给 AdvisorAgent   |

#### 代码改动

text

```
新增文件：
  app/api/advisor/execute.py           # 一键执行接口
  app/services/strategy_executor.py    # 策略执行服务

修改文件：
  app/services/advisor_agent.py        # 返回 recommendation_id
  app/services/business_agent.py       # 接收策略执行请求
  app/models/order.py                  # 增加 strategy_source 字段
  frontend/views/AdvisorWorkspaceView.vue  # 增加“一键执行”按钮

测试文件：
  scripts/test_advisor_execution_http.py
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 验收标准

text

```
✅ AdvisorAgent 推荐结果可一键转订单
✅ 确认流程复用现有 needs_confirmation
✅ 订单来源标记为 "advisor_recommendation"
✅ 持仓页面展示策略来源
✅ 资金一致性（事务、幂等、流水）保持
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

------

### 5.2 P1：投后诊断

#### 目标

客户可一键生成持仓诊断报告，包含持仓概况、风险分析、收益归因和建议。

#### 当前能力

- ✅ DataAnalysisAgent 已有固定指标查询能力
- ✅ RiskAgent 已有风险预警能力
- ✅ 已有结构化报告样式（审计报告可复用）

#### 需要补的





| 缺口     | 说明                          |
| -------- | ----------------------------- |
| 持仓分析 | 持仓结构、集中度、行业分布    |
| 风险诊断 | 结合 RiskAgent 做风险暴露分析 |
| 收益归因 | 各持仓的收益贡献              |
| 诊断报告 | 结构化输出，客户可读          |

#### 诊断报告模板

text

```
📊 持仓诊断报告（2026-08-14）

一、持仓概况
- 总资产：¥1,234,567
- 持仓数量：5 只
- 持仓集中度：前 3 只占比 78%

二、风险分析
- 风险等级：C3（与您的风险承受能力匹配）
- 最大回撤：-8.2%（近 30 天）
- 行业集中度：科技 45%，消费 30%，金融 25%

三、收益归因
- 近 30 天收益：+3.2%
- 主要贡献：科技板块（+2.1%）
- 最大拖累：消费板块（-0.8%）

四、建议
- 建议降低科技板块持仓至 35% 以下
- 建议增加债券配置以平衡风险
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 代码改动

text

```
新增文件：
  app/services/portfolio_diagnosis.py  # 持仓诊断服务
  app/schemas/diagnosis.py             # 诊断报告 Schema
  app/api/diagnosis.py                 # 诊断 API
  frontend/views/DiagnosisView.vue     # 诊断报告页面

修改文件：
  app/services/data_analysis_agent.py  # 增加持仓分析能力
  app/services/risk_agent.py           # 增加风险诊断能力
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 验收标准

text

```
✅ 客户可一键生成持仓诊断报告
✅ 报告包含持仓概况、风险分析、收益归因、建议
✅ 诊断结果可分享给投顾复核
✅ 诊断记录可追溯（存入 CustomerMemory）
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

------

### 5.3 P2：策略模板 + 场景化投资

#### 目标

客户选择投资场景 → 自动生成配置方案 → 确认后执行。

#### 场景定义

text

```
场景 1：“稳健增值”
- 目标：年化 4-6%，最大回撤 < 5%
- 配置：债券 60% + 混合基金 30% + 现金 10%

场景 2：“教育金储备”
- 目标：年化 6-8%，期限 10-15 年
- 配置：权益 50% + 债券 40% + 另类 10%

场景 3：“养老规划”
- 目标：年化 5-7%，期限 20-30 年
- 配置：权益 40% + 债券 50% + 现金 10%

场景 4：“财富增值”
- 目标：年化 8-12%，最大回撤 < 20%
- 配置：权益 70% + 另类 20% + 现金 10%

场景 5：“保守理财”
- 目标：年化 3-5%，最大回撤 < 3%
- 配置：债券 80% + 现金 20%
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 代码改动

text

```
新增文件：
  app/models/strategy_template.py      # 策略模板模型
  app/services/strategy_template.py    # 模板服务
  app/api/strategies.py                # 策略 API
  frontend/views/StrategyHubView.vue   # 策略中心页面

种子数据：
  scripts/seed_strategy_templates.py   # 5-10 个场景模板
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 验收标准

text

```
✅ 客户进入投顾页面可看到 5+ 个投资场景
✅ 选择场景后自动生成配置方案
✅ 方案包含产品推荐和配置比例
✅ 确认后可一键执行
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

------

### 5.4 P2：人机协同

#### 目标

低置信度推荐 → 投顾复核 → 客户确认 → 执行，形成“AI 生成 + 人工复核”的闭环。

#### 流程

text

```
客户请求（高净值/复杂需求）
    ↓
AI 生成方案初稿
    ↓
置信度判断
    ├─ 高置信度 → 直接展示给客户
    └─ 低置信度 → 投顾复核工作台
                      ↓
                  投顾修改/确认
                      ↓
                  展示给客户
                      ↓
                  客户确认 → 执行
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 代码改动

text

```
修改文件：
  app/services/advisor_agent.py        # 增加置信度判断
  app/api/workorders.py                # 增加投顾复核类型
  app/services/human_review.py         # 复核服务
  frontend/views/AdvisorReviewView.vue # 投顾复核工作台

新增文件：
  app/models/human_review.py           # 复核记录模型
  workers/review_feedback_worker.py    # 反馈数据回流
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 验收标准

text

```
✅ 低置信度推荐自动进入投顾复核队列
✅ 投顾可在 AI 方案上修改并保存修改记录
✅ 客户看到“由 AI 生成，经 XXX 投顾复核”的标签
✅ 人工修改数据回流训练
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

------

### 5.5 P3：市场分析 + 投研

#### 目标

客户可查看今日市场概览，包含指数、热点板块、资金流向和 AI 市场观点。

#### 市场分析卡片

text

```
📈 今日市场（2026-08-14）

上证指数：3,245.67  ▲ +1.23%
沪深 300：4,156.78  ▲ +1.45%
创业板指：2,134.56  ▲ +2.01%

🔥 热点板块
1. 半导体  +3.2%
2. 新能源  +2.8%
3. 医药    +1.9%

💰 资金流向
北向资金净流入：+45.6 亿
主力资金净流入：+123.4 亿

📊 观点
短期：震荡上行，关注科技板块
中期：结构性行情，均衡配置
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 代码改动

text

```
新增文件：
  app/services/market_data.py          # 行情数据服务
  app/services/market_analysis.py      # 市场分析服务
  app/api/market.py                    # 市场分析 API
  frontend/views/MarketView.vue        # 市场页面

修改文件：
  app/services/data_analysis_agent.py  # 增加市场分析能力
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 验收标准

text

```
✅ 客户可查看今日市场概览
✅ 包含指数、热点板块、资金流向
✅ 可生成 AI 市场观点
✅ 观点可分享/保存
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 注意

数据源是最大的门槛（行情数据、研报数据、实时资讯），获取成本高、合规要求严。建议：

1. **先做“演示级”**：基于公开数据（如新浪财经 API、Yahoo Finance），验证交互形态
2. **再升级“生产级”**：获取合规数据源后，替换底层数据接口
3. **数据接口抽象**：`MarketDataProvider` 接口隔离，便于切换数据源

## 6. 总结

### 6.1 核心结论





| 维度            | 竞品 | 你的项目 | 结论       |
| --------------- | ---- | -------- | ---------- |
| **AI 投顾概念** | 强   | 中       | 竞品领先   |
| **AI 执行能力** | 弱   | 强       | **你领先** |
| **资金一致性**  | 弱   | 强       | **你领先** |
| **Agent 编排**  | 弱   | 强       | **你领先** |
| **全生命周期**  | 中   | 弱       | 竞品领先   |
| **C 端入口**    | 强   | 无       | 竞品领先   |

### 6.2 一句话概括

> **方向都对，竞品都在做。你的优势不是“比他更早想出来”，而是“想到之后能比他更稳地做出来”——因为你的工程根基（事务、幂等、流水、Outbox）是竞品公开资料里看不出来的硬实力。**

### 6.3 立即行动清单





| 序号 | 行动                                       | 负责人      | 工期   |
| ---- | ------------------------------------------ | ----------- | ------ |
| 1    | 打通 AdvisorAgent → BusinessAgent 执行链路 | 后端        | 2 天   |
| 2    | 增加“一键执行”前端按钮                     | 前端        | 1 天   |
| 3    | 增加 `strategy_source` 订单字段            | 后端        | 0.5 天 |
| 4    | P0 验收测试                                | 测试        | 1 天   |
| 5    | 启动 P1 投后诊断设计                       | 产品 + 后端 | 1 周   |