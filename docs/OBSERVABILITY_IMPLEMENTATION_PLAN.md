# 可观测性落地实施计划

> 目标分两层：
>
> 1. **对话内可见**——在前端 Agent 界面直接看到一次问答的思维链、工具链、调用成败、token 与延迟；
> 2. **系统级可观测**——指标落库、管理端聚合视图、Prometheus 告警，使"生产级"说法站得住。
>
> 适用对象：本项目开发者。假设读者不熟悉可观测性体系，本文给出可直接照抄的代码与命令。
>
> **重要前提**：经代码勘查，第 1 层所需的**思维链与工具链数据后端已全部产生并返回前端**，
> 缺的只是三个字段和前端渲染。详见[一、现状盘点](#一现状盘点)。

---

## 目录

- [一、现状盘点](#一现状盘点)
- [二、认知前提：可观测性不是"上 Prometheus"](#二认知前提可观测性不是上-prometheus)
- [三、阶段一：对话内链路追踪面板（约 1-1.5 天）](#三阶段一对话内链路追踪面板约-1-15-天)
- [四、阶段二：LLM 指标落库与管理端展示（约 1 天）](#四阶段二llm-指标落库与管理端展示约-1-天)
- [五、阶段三：Prometheus + Grafana（约 2-3 天）](#五阶段三prometheus--grafana约-2-3-天)
- [六、阶段四：分布式追踪（可选）](#六阶段四分布式追踪可选)
- [七、管理端应展示的四张图](#七管理端应展示的四张图)
- [八、时间预算与优先级](#八时间预算与优先级)
- [九、答辩与面试话术升级](#九答辩与面试话术升级)
- [十、风险与注意事项](#十风险与注意事项)

---

## 一、现状盘点

**结论：原料已齐八成，缺的是接线和渲染，不是从零建设。**

### 1.1 对话级链路（思维链 / 工具链）——后端已全部产生

最关键的发现：**你要的思维链和工具链，后端已经算好并且随响应返回了，只是前端没渲染。**

`app/services/customer_model_tools.py:221` 定义的 `ToolRound`：

```python
@dataclass
class ToolRound:
    """一轮工具调用的记账（用于审计与观测）。"""
    round_index: int
    tool: str
    arguments: dict
    success: bool
    error: str | None = None
    evidence_status: str | None = None
    citations: int = 0
    reasoning: str = ""          # 模型自述的调用理由（审计/排查用，不参与控制流）
```

逐项对照需求：

| 需求 | 字段 | 是否已产生 |
|---|---|---|
| 调用了哪些工具 | `tool` | ✅ |
| 成功了没 | `success` / `error` | ✅ |
| 做了什么操作 | `arguments` | ✅ |
| **思维链** | `reasoning`（模型自述调用理由） | ✅ |
| 输出什么内容 | `citations` / `evidence_status` | ⚠️ 只有引用数，无输出内容 |
| 花费 token | — | ❌ 无 |
| 延迟 | — | ❌ 无 |

此外 `ModelToolOutcome.decisions`（`:245`）还记录了**被拦下**的调用（越界 / 重复），
即工具链里被安全护栏拦截的部分——这是答辩时很能说明问题的一块。

### 1.2 链路数据已随响应返回前端

`app/services/graph_agent_runner.py:52-70` 把轨迹按层注入 `task_results[].data.model_tool_trace`：

```python
data["model_tool_trace"] = {
    "rounds": layer_trace.get("rounds") or [],
    "decisions": layer_trace.get("decisions") or [],
    "stopped_reason": layer_trace.get("stopped_reason") or "",
    "layer": layer_trace.get("layer"),
    "layer_intents": layer_trace.get("layer_intents") or [],
    "all_stopped_reasons": stopped_reason,
}
```

而 `CustomerTaskExecutionResponse.results[]`（`app/schemas/customer_service.py:65`）已包含该 `data`，
**即链路数据已经到达前端，只是没有渲染。**

### 1.3 每次工具调用已落审计

`app/services/customer_model_tools.py:301`：

```python
action="customer_agent.tool_call",
resource_type="customer_service_conversation",
resource_id=conversation_id,
detail=json.dumps({
    "round", "tool", "arguments", "success", "error",
    "evidence_status", "citations", "read_only", "selection", "reasoning",
})
```

这是**历史回溯**的现成数据源：按 `resource_id = conversation_id` 即可查出某次对话的全部工具调用。

### 1.4 系统级指标——埋了没接线

| 已有资产 | 位置 | 状态 |
|---|---|---|
| TTFT / token 用量 / 耗时埋点 | `app/infrastructure/qwen.py:173`（非流式）、`:403`（流式） | ⚠️ 仅 `logger.info`，**未落库、未暴露** |
| trace_id 贯穿链路 | `app/core/trace.py`（`TraceIdMiddleware` / `get_trace_id`） | ✅ 可用 |
| `/metrics` 限流豁免 | `app/common/middleware/request_guard.py:46` | ⚠️ **已预留端点但从未实现** |
| 指标类 API 范式 | `app/api/admin.py:293` `/intent-shadow/metrics` | ✅ 可照抄 |
| 日志 → Worker → 落库模式 | `bad_case_worker` 消费 `logs/bad_cases.jsonl` | ✅ 成熟模式，可复用 |
| 前端 SSE 事件体系 | `CustomerRagView.vue:35-42` | ✅ 完整，含 `agent.task.completed` |

### 1.5 前端现状

`CustomerRagView.vue` 已消费完整的 SSE 事件流：

```
session.ready / agent.accepted / agent.progress / agent.output.chunk
agent.task.completed / agent.cancelled / agent.final / agent.error
```

"任务明细"区域**只渲染了 `intent` + `status` + `answer`**，未展开 `model_tool_trace`。
这是阶段一的主要工作。

---

## 二、认知前提：可观测性不是"上 Prometheus"

| 支柱 | 回答什么问题 | 本项目现状 | 优先级 |
|---|---|---|---|
| **Metrics** 指标 | 数值趋势如何？是否越界？ | 埋了没接线 | **P0** |
| **Logs** 日志 | 某个请求具体发生了什么？ | 已有 trace_id + 工具调用审计 | P2 |
| **Traces** 追踪 | 一次调用跨了哪些服务、慢在哪？ | 有 trace_id，未接 OTel | P3 |

### 一个容易被带偏的点

大多数 Prometheus 教程接完只给你 **HTTP 延迟 / QPS / 错误率**——通用指标，对本项目的差异化价值一般。

**真正值钱的是 AI 特有指标**：TTFT、token 成本、按 agent 类型的耗时分布、LLM 失败率。
而这些恰恰是本项目已经埋好的。

> 正确顺序：**先把自己的业务指标捞出来，再上通用基础设施。**
> 反过来做，会得到一个"有 Grafana 但没什么可看"的空壳。

---

## 三、阶段一：对话内链路追踪面板（约 1-1.5 天）

**演示价值最高的部分。** 答辩时点开一条问答看到完整的思维链与工具链，说服力远大于任何架构图。

> **✅ 已实现（2026-09-18）**
>
> 后端字段、采集逻辑、前端组件、单元测试均已完成，`pytest tests/` 548 通过、前端构建通过。
> 与下方原设计有一处**实现差异**，已在 3.1 标注；历史回溯接口（3.3）**未实现**，留待需要时补。

### 3.1 增量一：给 `ToolRound` 补字段

`app/services/customer_model_tools.py:221`：

```python
@dataclass
class ToolRound:
    round_index: int
    tool: str
    arguments: dict
    success: bool
    error: str | None = None
    evidence_status: str | None = None
    citations: int = 0
    reasoning: str = ""
    duration_ms: float | None = None        # 新增：本轮工具调用耗时
    prompt_tokens: int | None = None        # 新增：本轮 prompt token
    completion_tokens: int | None = None    # 新增：本轮 completion token
    output_preview: str = ""                # 新增：工具返回摘要（截断，默认 300 字）
```

计时方式：在调用工具前后各取一次 `time.monotonic()`。

### 3.1.1 实现说明：token 怎么归因到"轮次"

原设计说"从 `qwen.py` 的 usage 回传"，实际实现改为**在 provider 上挂 `last_call_usage` 属性**：

```python
# qwen.py chat_completions 末尾
self.last_call_usage = {"model": ..., "prompt_tokens": ..., "completion_tokens": ...,
                        "total_tokens": ..., "duration_ms": ...}
```

```python
# customer_model_tools.py，每轮决策后读取并清空
round_usage = self._consume_llm_usage()
```

**为什么不改 `request_tool_call` 的返回值**：

1. 既有调用方与测试断言依赖"返回 dict 或 None"，改返回结构会破坏多个测试；
2. `tests/test_llm_resource_guard.py` 用 `inspect.getsource` 断言该方法源码含 `timeout=`，
   签名改动风险外溢；
3. 测试里的假 provider 没有该属性，上层用 `getattr(..., None)` 读取即可自然降级
   ——观测字段缺失不应让整条工具链路失败。

**为什么读后即清空**：用量是"最近一次"语义，不清空会让下一轮沿用上一轮数字，导致 token 虚高。

### 3.1.2 其他实现要点

- **`duration_ms` 与 `llm_duration_ms` 分开记**：工具慢说明下游查询慢，LLM 慢说明模型侧慢，
  排障结论完全不同，合并记就分辨不出来。
- **规则补齐（`_fill_uncovered`）不记 token**：确定性路径没经过 LLM，
  记 0 会让成本看起来"免费"，记真实值又与事实不符，故留 `None`，前端显示"—"。
- **被拦截的轮次也要记 token**：决策 LLM 调用已经发生，开销真实存在，
  只给"执行成功"的轮次记账会让成本统计系统性偏低。
- **`output_preview` 是结构性摘要**：优先输出"命中 N 条知识块；返回 M 条记录"，
  无结构信息时才退化成文本，且截断到 160 字——避免把客户资产明细等敏感原文带进前端与日志。

### 3.2 增量二：确认 `model_tool_trace` 透传

`graph_agent_runner.py` 已注入 `rounds` / `decisions`，新字段随 `r.__dict__`
（`graph.py:430`）自动带上，无需改图。

需确认一点：`TaskExecutionResultResponse.data: dict` 是开放字典，新字段不会被 Pydantic 裁掉——✅ 无阻碍。

### 3.3 增量三：历史回溯接口 ✅ 已实现（2026-09-18 / 提交 `6cab5ff`）

新增（供刷新页面后仍能回看，以及管理端排查）：

```
GET /api/v1/customer-service/conversations/{conversation_id}/trace
```

数据源直接查 `AuditLog`：

```sql
SELECT created_at, detail
FROM audit_logs
WHERE action = 'customer_agent.tool_call'
  AND resource_id = :conversation_id
ORDER BY created_at;
```

权限沿用会话归属校验（只能看自己的会话）。

#### 实现补充（与上文设计的三处差异，以代码为准）

1. **审计 detail 已携带观测字段**。上文只查 `created_at, detail`，但当时 detail
   里只有 round/tool/arguments/success/error/reasoning，**没有耗时与 token**——
   那样回溯只能看到"调过什么"。故 `_audit` 新增 `metrics` 参数，由
   `_round_metrics(round)` 从**已构造好的 ToolRound** 抽取（避免在审计处重算导致
   口径漂移），落库字段：duration_ms / llm_duration_ms / prompt_tokens /
   completion_tokens / output_preview。
2. **权限拆成两条路径**，不是单一的"会话归属校验"：
   - 客户：仅 `AuditLog.actor_user_id == user.id`
   - 坐席/风控：凭 `audit:read`（或 super_admin）看任意会话——排查客诉时必须能看到
     客户那侧发生了什么
   - 无归属记录时返回**空列表**而非 403/404，避免用错误码差异泄露他人会话是否存在
3. **单独一个 router** `conversation_router`（prefix `/customer-service/conversations`）。
   因为 `conversation_id` 与 `session_id` 是两套 ID：前者客户端生成、贯穿整轮问答，
   后者只在转人工后存在。挂在既有的 `/sessions` 下会产生路径歧义。

解析逻辑抽成纯函数 `_trace_rounds(rows)`（app/api/customer_service.py），
字段名与实时 `model_tool_trace.rounds` 完全一致，前端复用同一套渲染；
空 detail 与非 JSON detail 跳过该条，不让历史脏数据炸掉接口。

### 3.4 增量四：前端 `AgentTracePanel.vue`

新建组件 `frontend/src/components/AgentTracePanel.vue`，挂到：
- `CustomerRagView.vue` 的"任务明细"每个 task 下
- `AgentBubble.vue` 的每条 assistant 消息下

#### UI 结构

```
┌─────────────────────────────────────────────────────────┐
│ ▸ 查看推理过程                                            │
│   第 2 层 · 3 次工具调用 · 1 次被拦截 · 1.8s · 2,340 tokens │
├─────────────────────────────────────────────────────────┤
│ ① search_customer_knowledge            ✅ 成功   420ms   │
│   入参：query="适当性管理"                                │
│   理由：用户询问适当性定义，需检索制度文档                  │
│   输出：命中 3 条知识块                                    │
├─────────────────────────────────────────────────────────┤
│ ② get_customer_risk_level              ✅ 成功   180ms   │
│   入参：customer_id=...                                  │
│   理由：适当性判断需先取客户风险等级                        │
│   输出：C3                                                │
├─────────────────────────────────────────────────────────┤
│ ③ execute_order                        ⛔ 被拦截   0ms   │
│   入参：...                                              │
│   拦截原因：tool_out_of_plan_scope（不在本次计划范围内）    │
├─────────────────────────────────────────────────────────┤
│ 停止原因：done · 计划意图：risk_inquiry, suitability      │
└─────────────────────────────────────────────────────────┘
```

#### 渲染要点

1. **默认折叠**，不干扰正常对话阅读
2. 顶部摘要一行给出四个数字：调用次数 / 被拦截次数 / 总耗时 / 总 token
3. 每个 round 一行，状态徽章三色：成功绿、失败红、**被拦截黄**
4. `reasoning` 单独成行并弱化显示——这是"思维链"的实体
5. `decisions` 里存在但 `rounds` 里没有的调用，单独归为"被拦截"分组
6. token 为 `None` 时显示"—"，不要显示 0

**第 5 点尤其重要**：它直观展示了安全护栏真的在工作。答辩时指给评委看，
比解释十句"我们有能力白名单"都管用。

### 3.5 阶段一验收标准

> ⚠️ **前置条件（2026-09-18 实测发现，必读）**：链路面板依赖的 `model_tool_trace`
> 只在 LangGraph 图路径下产生，需要同时开启两个**默认关闭**的开关：
>
> ```
> AGENT_GRAPH_ENABLED=true                    # settings.py:68  默认 False
> AGENT_MODEL_TOOL_SELECTION_ENABLED=true     # settings.py:82  默认 False
> ```
>
> 默认（两者全关）走确定性管线，产出的是另一套 `data.tool_calls`
> （结构仅 tool/iteration/success/error/evidence_status，无思维链与耗时），
> 面板 `v-if="rounds.length"` 恒假 → **页面什么都不显示**。
> 详见本节末《什么情况下才看得到链路》。

> 代码层面（3.1 / 3.2 / 3.3 / 3.4）已全部实现并提交：`5bb6e30`、 `6cab5ff`。
> 单测 558 passed、前端 build 通过。以下 5 条属**实机验收**，需启动服务走一遍，
> 目前尚未执行——单测能锁住字段契约，锁不住"端到端真的走到这条路"。

- [ ] 发起一次会触发工具调用的问答，响应 `results[].data.model_tool_trace.rounds` 含 `duration_ms`
- [ ] 前端展开"查看推理过程"能看到工具名、入参、理由、成功/失败、耗时
- [ ] 制造一次越界工具调用，前端能看到黄色"被拦截"条目及拦截原因
- [ ] 刷新页面后通过 `/trace` 接口仍能回看到工具调用记录
- [ ] `output_preview` 不含客户敏感字段原文

#### 实机验收结果（2026-09-18 晚，已执行）

前置条件已满足：compose 中 `AGENT_GRAPH_ENABLED` 与 `AGENT_MODEL_TOOL_SELECTION_ENABLED`
默认值改为 `true` 并重建容器；`reasoning` 改由 tool schema 必填参数承载（见下）。

实测 query「你们公司的主营业务是什么」：
```
task=company_info layer=1
#1 search_customer_knowledge ok=True   dur=2959ms tok=1554/49 reasoning=查询公司基本信息…
#2 search_customer_knowledge ok=True   dur=3708ms tok=1598/56 reasoning=为了获取公司基本信息…
#3 search_customer_knowledge ok=False duplicate_call_skipped  tok=1614/59 reasoning=…company_info 类别
#5 rule_backstop            ok=True   reasoning 为空、token 为空（确定性路径不经 LLM，符合设计）
```

**思维链的取值来源已修正**：`reasoning` 不再依赖 tool_call 同帧的 `message.content`
（DashScope 实测恒为空，且在 prompt 里请求模型"顺便说一句"也无效），
而是声明为 tool schema 的**必填参数**，从 `arguments` 取出后立即剥离。
剥离是必须的——否则既污染工具入参，又会让重复调用闸门失效
（每轮理由措辞不同 → 相同调用不被判重）。

#### ⚠️ 切流的对照验收未通过（待决策）

`docker exec wealth-p0-api-1 python scripts/compare_graph_vs_pipeline.py`
→ **9/14 完全一致，5 条差异**。其中 4 条仅 `answer` 文本不同（LLM 生成天然波动，可接受），
但 1 条是**结构性退化**：

| query | pipeline | graph |
|---|---|---|
| 开户流程是怎样的 | `completed`，完整回答 | `partially_answered` + "当前资料未完全覆盖该问题" |

按项目定义"有差异即为切流阻塞项"，严格讲此次切图未通过验收。
知识类问答上图偏保守/偏弱，是切流的已知代价，需知情决策是否回退。

---

#### 阶段一上线后实际发现的问题（价值验证）

链路面板不是为了"看起来有监控"，它上线当天就暴露了两个此前**完全不可见**的真实问题。
答辩时这一节比架构图更有说服力——因为它证明了"看得见"能直接换来"改得动"。

**发现一：同一意图的重复检索（已修，见 BUG_LOG `ISSUE-20260918-REPEATED-SAME-INTENT-LOOP`）**

面板显示 query=「公司客户规模」连续 4 次调用 `search_customer_knowledge`，
措辞各不相同、**命中同一批知识块**，一路调到 `budget_exhausted`：

```
第 1 层 · 4 次工具调用 · 28668ms · 6651 tokens      ← 面板顶部摘要直接暴露了浪费
#1 决策 2262ms · 执行 4173ms · 1552↑ 68↓   理由：用户询问公司客户规模…
#2 决策 1872ms · 执行 8017ms · 1596↑ 66↓   理由：属于公开的公司信息类问题…
#3 决策 2738ms · 执行 3119ms · 1612↑ 61↓
#4 决策 2315ms · 执行 4172ms · 1628↑ 68↓
停止原因：budget_exhausted
```

根因是三条「阻止重复」路径中缺了一条（"同一意图已成功过"只回灌相同内容、不提示模型）。
修复后同一 query 调用次数 4 → 2。

**发现二：模型越界调用被拦下，且理由完整留存（护栏可视化）**

```
#1 get_my_workorder_status   ok=True   reasoning=用户明确要求查询工单进展…
#2 explain_workorder_status  ok=False  tool_out_of_plan_scope
   reasoning=已获取工单状态为 open，需解释该状态的处理流程与后续预期…
```

模型自行"多调一个工具去解释"，越出计划范围被拦。**被拦下的那次调用连同它的自述动机**
都留在了面板上——这正是"安全护栏真的在工作"的直观证据，
比口头解释十句"我们有工具白名单"管用得多。

**两点经验**：
1. 面板顶部那行摘要（次数 / 被拦截数 / 总耗时 / 总 token）是最先暴露问题的部位——
   数字异常比逐条看更容易发现问题。
2. **耗时与 token 必须按轮归因**（决策 LLM 耗时 vs 工具执行耗时分开记），
   否则只能看到"这一轮慢"，看不出"是模型慢还是下游查询慢"。

---

## 四、阶段二：LLM 指标落库与管理端展示（约 1 天）

### 4.1 设计选择：为什么不同步写库

`qwen.py` 属于 `app/infrastructure/`，没有 db session，硬塞会破坏分层。

**复用本项目已有的飞轮异步落库模式**：写 jsonl → worker 异步消费 → 落库。

理由：`bad_case` 就是这个模式，架构一致；不阻塞主链路；LLM 超时失败时采集不受影响；
面试时可讲"我复用了数据飞轮的异步落库模式，指标采集与业务链路解耦"。

### 4.2 建表 `app/models/llm_metric.py`

```python
from datetime import datetime
from sqlalchemy import DateTime, Float, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LlmCallMetric(Base):
    """LLM 调用指标（TTFT / token 用量 / 耗时），供可观测与成本核算。

    由 metrics_worker 从 logs/llm_metrics.jsonl 异步消费落库，
    不参与主链路事务，采集失败不影响业务。
    """
    __tablename__ = "llm_call_metric"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_type: Mapped[str] = mapped_column(String(32), default="", nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    call_kind: Mapped[str] = mapped_column(String(16), default="chat", nullable=False)  # chat/stream/tool/classify
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)       # ok/failed
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ttft_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_llm_metric_created_kind", "created_at", "call_kind"),
    )
```

### 4.3 迁移 `migrations/versions/0026_llm_call_metric.py`

```python
"""add llm_call_metric table for TTFT / token / latency observability"""
from alembic import op
import sqlalchemy as sa


revision = "0026_llm_call_metric"
down_revision = "0025_knowledge_bm25_key_field"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_call_metric",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("agent_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("call_kind", sa.String(16), nullable=False, server_default="chat"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error_type", sa.String(64), nullable=True),
        sa.Column("ttft_ms", sa.Float(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_llm_call_metric_trace_id", "llm_call_metric", ["trace_id"])
    op.create_index("ix_llm_call_metric_conversation_id", "llm_call_metric", ["conversation_id"])
    op.create_index("ix_llm_call_metric_agent_type", "llm_call_metric", ["agent_type"])
    op.create_index("ix_llm_call_metric_created_at", "llm_call_metric", ["created_at"])
    op.create_index("ix_llm_metric_created_kind", "llm_call_metric", ["created_at", "call_kind"])


def downgrade():
    op.drop_table("llm_call_metric")
```

### 4.4 埋点改造 `app/infrastructure/qwen.py`

在现有 `logger.info` 处**追加** jsonl 写入（保留原日志，双写）：

```python
import json
import os
import uuid
from datetime import datetime, timezone

from app.core.trace import get_trace_id

_LLM_METRICS_PATH = os.getenv("LLM_METRICS_LOG_PATH", "logs/llm_metrics.jsonl")


def _emit_llm_metric(**fields) -> None:
    """把一次 LLM 调用指标追加到 jsonl，由 metrics_worker 异步落库。

    任何异常都吞掉：指标采集绝不能影响业务主链路。
    """
    try:
        record = {
            "id": str(uuid.uuid4()),
            "trace_id": get_trace_id(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        os.makedirs(os.path.dirname(_LLM_METRICS_PATH) or ".", exist_ok=True)
        with open(_LLM_METRICS_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
```

在三个位置调用：非流式成功、流式 `finally`、两处失败分支（记 `status="failed"` + `error_type`，
否则失败率统计不出来）。

#### 4.4.1 顺带修正 TTFT 口径

现有代码在**第一个 chunk** 就记录 `ttft_ms`，但流式接口首个 chunk 通常是
`delta={"role": "assistant", "content": ""}`（空包），量到的是"首个网络包"而非"首个可见 token"，偏乐观。

改法——把赋值移到拿到非空 `delta` 之后：

```python
                if not getattr(chunk, "choices", None):
                    continue
                delta = getattr(chunk.choices[0].delta, "content", None)
                if delta:
                    if ttft_ms is None:
                        ttft_ms = (time.monotonic() - t0) * 1000  # ← 首个可见 token
                    yield delta
```

### 4.5 Worker `workers/metrics_worker.py`

照 `workers/bad_case_worker.py` 结构：

1. 持续 tail `logs/llm_metrics.jsonl`（记录 offset，避免重复或丢读）
2. 批量 INSERT 进 `llm_call_metric`，按 `id` 去重（主键冲突跳过）
3. 消费成功的行标记/截断，失败保留重试

### 4.6 API 与前端

照 `/intent-shadow/metrics`（`admin.py:293`）新增：

```
GET /api/v1/admin/llm-metrics/summary?hours=24&group_by=model
```

分位数用 PostgreSQL 原生函数：

```sql
SELECT
  percentile_cont(0.50) WITHIN GROUP (ORDER BY ttft_ms) AS ttft_p50,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) AS ttft_p95,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY ttft_ms) AS ttft_p99
FROM llm_call_metric
WHERE call_kind = 'stream' AND status = 'ok' AND created_at >= NOW() - INTERVAL '24 hours';
```

> `ttft_ms` 只有流式调用才有，非流式必须排除，否则 NULL 污染分位计算。

前端新建 `ObservabilityView.vue`，挂管理端菜单。

### 4.7 阶段二验收标准

- [ ] 流式对话后 `logs/llm_metrics.jsonl` 新增含 `ttft_ms` 的记录
- [ ] worker 启动后 `llm_call_metric` 表出现对应行
- [ ] summary 接口返回非空 P50/P95 与 token 合计
- [ ] 管理端能渲染趋势图
- [ ] 停掉 worker 或写坏 jsonl，**业务对话不受任何影响**

---

## 五、阶段三：Prometheus + Grafana（约 2-3 天）

### 5.1 接入标准 HTTP 指标

```bash
pip install prometheus-fastapi-instrumentator
```

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

两行接管 HTTP 延迟 / QPS / 错误率——正好对应 `request_guard.py` 已预留的 `/metrics` 豁免。

### 5.2 自定义 AI 指标

| 指标名 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `llm_ttft_seconds` | Histogram | `model`, `agent_type` | 首 token 延迟，buckets `0.1/0.25/0.5/1/2/5` |
| `llm_duration_seconds` | Histogram | `model`, `call_kind` | 整体耗时 |
| `llm_tokens_total` | Counter | `model`, `direction` | `direction` 取 `prompt` / `completion` |
| `llm_calls_total` | Counter | `model`, `status` | 调用数与失败率的分母 |
| `llm_tool_calls_total` | Counter | `tool`, `blocked` | 工具调用，`blocked=true` 为被护栏拦截 |
| `outbox_pending_events` | Gauge | — | 异步队列积压 |
| `outbox_dead_events_total` | Counter | — | dead 事件累计 |

> `outbox` 两项比 LLM 指标更重要——异步系统的生命体征。
> 面试官问"异步任务失败了你怎么知道"，答的就是这两个。

### 5.3 `docker-compose.p0.yml` 新增服务

```yaml
  prometheus:
    image: prom/prometheus:v2.54.1
    restart: unless-stopped
    volumes:
      - ./deploy/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./deploy/prometheus/alert_rules.yml:/etc/prometheus/alert_rules.yml:ro
    ports:
      - "9090:9090"
    networks:
      - wealth-p0

  grafana:
    image: grafana/grafana:11.2.0
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-wealth_dev}
    ports:
      - "3000:3000"
    volumes:
      - wealth_grafanadata:/var/lib/grafana
    networks:
      - wealth-p0
```

并补 `deploy/prometheus/prometheus.yml` 与告警规则文件。
注意当前 `deploy/` 是**空目录**，需一并创建。

### 5.4 告警规则（至少 3 条）

1. LLM P95 延迟超阈值持续 5 分钟
2. `outbox_dead_events_total` 增长大于 0
3. `outbox_pending_events` 持续高于阈值

### 5.5 阶段三验收标准

- [ ] `curl http://127.0.0.1:8010/metrics` 能看到 `llm_ttft_seconds` 等指标
- [ ] Prometheus Targets 页面 `api` 为 UP
- [ ] Grafana 能画出 TTFT P95 与 token 消耗趋势
- [ ] 手工制造一次失败调用，告警规则能触发

---

## 六、阶段四：分布式追踪（可选）

`app/core/trace.py` 已有 trace_id，接 OpenTelemetry + Jaeger 可打通
API → Outbox → Worker → 外部 LLM 调用的 span 链路。

**优先级最低，可跳过。** 答辩时说明"trace_id 已贯穿全链路，OTel 是下一步规划"即可。

---

## 七、管理端应展示的四张图

| # | 图表 | 类型 | 数据来源 | 说明 |
|---|---|---|---|---|
| 1 | TTFT 趋势 | 折线（P50 + P95 双线，按小时） | `llm_call_metric.ttft_ms` | 最直接体感指标 |
| 2 | Token 消耗 | 堆叠柱（prompt / completion，按 model 分色） | `prompt_tokens` / `completion_tokens` | 成本核算依据 |
| 3 | 失败率与错误类型 | 折线 + 饼图 | `status` / `error_type` | 稳定性证据 |
| 4 | Outbox 积压与 dead 事件 | 折线 / 大数字卡片 | `outbox_event` 表 | **运营健康度，别漏** |

第 4 张技术含量最低，但回答问题时的价值最高。

---

## 八、时间预算与优先级

| 阶段 | 耗时 | 收益 | 建议 |
|---|---|---|---|
| 阶段一：对话内链路追踪面板 | 约 1-1.5 天 | **演示价值最高**，且后端已备好 90% 数据 | **最先做** |
| 阶段二：捞数据 + 管理端展示 | 约 1 天 | 把"我埋了点"变成"我有数据" | 其次 |
| 阶段三：Prometheus + Grafana | 约 2-3 天 | 让"生产级"说法站得住 | 再次 |
| 阶段四：Traces | 1-2 天 | 锦上添花 | 可跳过 |

若时间紧张，**只做阶段一也值回票价**：它直接解决"对话里看不见 Agent 在干什么"，
而这正是答辩演示时最抓眼的部分。

---

## 九、答辩与面试话术升级

| 场景 | 当前说法 | 补完后说法 |
|---|---|---|
| Agent 可解释性 | 「我们有多 Agent 编排」 | 「每次问答都能展开看思维链：模型为什么调这个工具、参数是什么、成功还是被拦截、耗时多少」 |
| 安全护栏 | 「有能力白名单和权限校验」 | 「被拦截的工具调用会单独标黄展示，能看到拦截原因和当时的计划范围」 |
| LLM 性能 | 「我埋了 TTFT 和 token 日志」 | 「我度量了 TTFT 的 P95 是 X ms，单次对话 token 成本 Y，失败率 Z%，并设了告警阈值」 |
| 异步可靠性 | 「用了 Outbox + Worker」 | 「Outbox 积压量和 dead 事件数是核心运营指标，超阈值告警」 |

**差距在于：从"我做了"到"我运营过、我能证明"。**

---

## 十、风险与注意事项

### 10.1 本文档不会被 Git 跟踪（重要）

`.gitignore` 中存在 `docs/*`。当前仓库仅强制跟踪 4 个文档：
`docs/API.md`、`docs/DATABASE_TABLE_REFERENCE.md`、`docs/ERROR_CODES.md`、`docs/README.md`。

```bash
# 方案 A：纳入版本管理（推荐，本文档是实施依据）
git add -f docs/OBSERVABILITY_IMPLEMENTATION_PLAN.md

# 方案 B：仅本地留存
```

### 10.2 `output_preview` 必须脱敏

工具返回可能含客户资产、身份信息。建议只保留结构摘要
（"命中 3 条知识块"、"返回 1 个产品"），不落原文。
前端展示的链路对客户本人可见，但也要防止越权与日志泄露。

### 10.3 指标采集不得影响业务

`_emit_llm_metric` 必须吞掉所有异常。指标是观测手段，一旦它能让业务失败，就是设计事故。

### 10.4 分位数计算要区分 `call_kind`

`ttft_ms` 仅流式调用有值，计算分位必须过滤 `call_kind = 'stream'`，否则 NULL 污染结果。

### 10.5 别把 token 用量当实时计费依据

上游 `usage` 可能延迟到达，或在流式中断时缺失。
token 统计适合趋势与成本估算，不适合对账。

### 10.6 前端 token 为 `None` 时不要显示 0

确定性路径（规则回退）不消耗 token，显示"—"而非 0，否则会误导成本判断。

### 10.7 别在阶段三之前跳过阶段二

Prometheus 只解决"采集与告警"，不解决"数据从哪来"。
顺序颠倒会得到一个有 Grafana、但没有业务指标可看的空壳。

---

## 附：相关文件清单

| 用途 | 文件 |
|---|---|
| **阶段一** | |
| 工具轮次结构（✅ 已加 4 字段） | `app/services/customer_model_tools.py` `ToolRound` |
| 采集与摘要（✅ 已实现） | `_usage_fields` / `_summarize_output` / `_consume_llm_usage` |
| provider 用量记录（✅ 已实现） | `app/infrastructure/qwen.py` `last_call_usage` |
| 轨迹注入（已实现，确认透传） | `app/services/graph_agent_runner.py:52-70`、`graph.py:430`（`r.__dict__` 自动带上新字段） |
| 工具调用审计（历史回溯数据源） | `app/services/customer_model_tools.py:301` |
| 新增回溯接口 | `app/api/customer_service.py`（追加，**未实现**） |
| 新增前端组件（✅ 已实现） | `frontend/src/components/AgentTracePanel.vue`（新建） |
| 接入点（✅ 已接入） | `CustomerRagView.vue` 任务明细、`AgentBubble.vue` 消息项 |
| 单元测试（✅ 已实现，7 例） | `tests/test_agent_trace_metrics.py`（新建） |
| **阶段二** | |
| 埋点改造 | `app/infrastructure/qwen.py` |
| 新增模型 | `app/models/llm_metric.py`（新建） |
| 新增迁移 | `migrations/versions/0026_llm_call_metric.py`（新建） |
| 新增 Worker | `workers/metrics_worker.py`（新建，照 `bad_case_worker.py`） |
| 新增 API | `app/api/admin.py`（追加，照 `:293`） |
| 新增前端 | `frontend/src/views/ObservabilityView.vue`（新建） |
| **阶段三** | |
| Prometheus 配置 | `deploy/prometheus/`（新建，当前 `deploy/` 为空目录） |
| 编排 | `docker-compose.p0.yml`（追加 prometheus / grafana） |
| 限流豁免（已就绪） | `app/common/middleware/request_guard.py:46` |
