# -*- coding: utf-8 -*-
"""受控只读子代理的**模型自主拆分/并行决策**（受控子代理并行层设计方案 P0-3）。

## 与既有的「模型自主工具调度」是什么关系

`CustomerModelToolLoop`（`customer_model_tools.py`）回答的是
**"调哪个工具、传什么参数、要不要再调一轮"** —— 串行的多轮工具选择。

本模块回答的是 **"这一个问题该不该拆成多个子任务、这些子任务能不能并发"**
—— 即用户要求的"模型通过任务复杂度判断是否并行子代理"。

两者互补而非替代：先由本模块决定"拆不拆、并能不能并行"，再由每个子代理各自
跑自己的只读查询。本模块**不替代**任何确定性路径。

## 安全边界（全部是硬约束，不靠提示词）

1. **单一工具**：只声明 `dispatch_read_tasks` 一个工具。模型**不选工具**（无从越界），
   只能决定"拆几个 / 能不能并行 / 每个子任务是哪种只读能力"。
2. **capability 是 enum**：值域直接取 `READ_ONLY_CAPABILITIES`，模型**物理上无法**
   编造一个写操作能力名（即使编造，executor 侧还有第二道白名单校验）。
3. **越界即拒**：模型返回的任何不在白名单内的 capability，该子任务被丢弃并记审计，
   不让它进执行层。
4. **模式以 `depends_on` 为准**：不采信模型自报的 `mode`（实测模型会把有依赖的
   情况误填 parallel）——见 `FanoutDecision.effective_mode`。
5. **零依赖边界**：本模块只做**只读查询**的拆分。任何写操作意图不进此层。
6. **优雅降级**：模型不可用/未调用/参数非法 → 回退为**串行单任务**（等价于今天的行为），
   绝不因为"决策层失败"而让用户拿不到答案。

## 为什么必须留痕

历史教训（`ISSUE-20260914-MODEL-EARLY-STOP-NEEDS-RULE-BACKSTOP` 等）反复证明：
**丢弃模型的决策理由 = 事后只能靠猜**。故每轮决策落
`AuditLog(action="customer_agent.subagent_fanout")`，含 mode/reason/每个子任务的
capability 与依赖——用于回答"为什么这次没并行"。
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.common.logging.config import get_logger
from app.schemas.subagent import FanoutDecision, SubagentTask
from app.services.subagent_executor import READ_ONLY_CAPABILITIES

logger = get_logger(__name__)

# 子任务数上限：超过这个数量的"拆分"基本都是模型过度拆解（把一个查询拆成同义的几个），
# 只会放大 LLM 调用与延迟，不会提升答案质量。硬上限而非提示词上限。
MAX_SUBTASKS = 4

# 可以按 capability 折叠重复的**事实类**能力（返回只取决于 customer_id、与 query 无关）。
# **刻意不包含** `customer_rag_knowledge` / `customer_risk_explanation`：
# 知识检索的返回**取决于 query**，两个不同的知识问题折叠成一个会**丢答案**。
# 详见 `parse_fanout_decision` 中的折叠逻辑说明。
_DEDUPABLE_FACT_CAPABILITIES = frozenset({
    "customer_asset_summary",
    "customer_product_status",
    "customer_order_status",
    "customer_workorder_status",
    "customer_risk_restriction",
})

# capability → 中文说明。**由服务端提供**（而不是让模型自己想象能力语义），
# 否则模型只能从能力名猜用途，容易把"风险限制"当成"风险解释"用错。
_CAPABILITY_DESC: dict[str, str] = {
    "customer_asset_summary": "查该客户自己的资产总览（总资产、持仓数量）",
    "customer_product_status": "查某个产品是否在售及其状态",
    "customer_order_status": "查该客户自己的订单状态",
    "customer_workorder_status": "查该客户自己的工单进展",
    "customer_risk_restriction": "查该客户自己是否被风控限制（事实）",
    "customer_rag_knowledge": "检索公开知识库（公司业务/服务流程/政策制度/FAQ）",
    "customer_risk_explanation": "基于已查到的风险事实，解释风险制度依据",
}

_DISPATCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "dispatch_read_tasks",
        "description": (
            "把一个客户问题拆成若干**独立**的只读查询子任务并下发。"
            "mode=parallel 表示子任务互不依赖、可同时执行（更快）；"
            "mode=serial 表示后一个子任务需要前一个的结果才能构造，必须按序执行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["parallel", "serial"],
                    "description": "parallel=子任务彼此独立可并发；serial=存在前后依赖必须串行",
                },
                "reason": {"type": "string", "description": "为什么这样拆、这样排序"},
                "tasks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_SUBTASKS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "capability": {
                                "type": "string",
                                "enum": sorted(READ_ONLY_CAPABILITIES),
                                "description": "该子任务用哪种只读能力",
                            },
                            "query": {"type": "string", "description": "该子任务要查什么"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "依赖的子任务下标（从 0 开始）；无依赖留空数组",
                            },
                        },
                        "required": ["capability", "query"],
                    },
                },
            },
            "required": ["mode", "tasks"],
        },
    },
}

_SYSTEM = (
    "你是财富管理客服的只读查询编排器。判断用户问题需要哪些**独立**的只读查询，"
    "并决定这些查询能否**并行**下发。\n"
    "规则：\n"
    "1. 只拆只读查询，绝不拆出任何写操作（下单/转账/赎回/修改数据）。\n"
    "2. **若某子任务必须先拿到另一子任务的结果才能构造 → 必须用 depends_on 标明依赖**，"
    "此时 mode 填 serial。\n"
    "3. **若各子任务各自独立、互不需要对方结果 → mode 填 parallel**（并行更快）。\n"
    "4. 只要一个只读查询就够时，就只给 1 个 task，不要为了并行而硬拆。\n"
    "5. 同一个问题的多个方面（如公司业务与开户流程）是**独立**的，用同一个能力但分开列。\n"
    "6. 只能从枚举里选 capability，不要臆造。\n"
)


def _capability_lines() -> str:
    return "\n".join(
        f"- {name}：{_CAPABILITY_DESC.get(name, name)}"
        for name in sorted(READ_ONLY_CAPABILITIES)
    )


def parse_fanout_decision(raw: dict | None, *, fallback_query: str,
                          method: str = "model") -> FanoutDecision:
    """把模型的工具调用返回值解析为受控的 `FanoutDecision`（**纯函数，便于单测**）。

    **入参有两种形态，必须都支持**（这个坑真实踩过）：
      - `QwenProvider.request_tool_call` 返回的是**包装体**
        `{"name": "dispatch_read_tasks", "arguments": {...}, "reasoning": "..."}`；
      - 工具参数本身是 `{"mode": ..., "tasks": [...]}`。

    初版只认后者，于是 `plan()` 把包装体直接传进来 → `raw.get("tasks")` 恒为 None
    → **每一次决策都静默降级成串行单任务**。而输出看起来完全正常（有答案、状态 succeeded），
    唯一的线索是 `source=fallback_serial` —— 若不核对这个字段，会误以为"模型不会并行"。
    故此处统一**先剥壳**，去掉"调用方必须记得拆包装"这个隐形契约。

    其余越界/非法输入都在此收敛：
    - 非白名单 capability → 丢弃该子任务（不报错，其余子任务继续）；
    - 空 tasks / 无有效子任务 → 回退为**单个串行 task**（等价今天的行为）；
    - depends_on 指向越界下标/自身 → 剔除，避免执行层拿到无法满足的依赖。
    """
    args = raw or {}
    # 剥壳：工具调用包装体 → 真实参数。两形态都支持，避免调用方踩坑。
    if isinstance(args.get("arguments"), dict):
        args = args["arguments"]
    raw_tasks = args.get("tasks")
    if not isinstance(raw_tasks, list):
        raw_tasks = []

    tasks: list[SubagentTask] = []
    depends_on: list[list[int]] = []
    for index, item in enumerate(raw_tasks[:MAX_SUBTASKS]):
        if not isinstance(item, dict):
            continue
        capability = str(item.get("capability") or "").strip()
        if capability not in READ_ONLY_CAPABILITIES:
            # 越界能力：直接丢弃。这不是"模型的自由发挥空间"，是安全边界。
            logger.warning("subagent.fanout_capability_rejected capability=%s", capability)
            continue
        query = str(item.get("query") or fallback_query).strip() or fallback_query
        tasks.append(SubagentTask(
            task_id=f"sub-{len(tasks) + 1}",
            query=query,
            read_only_capability=capability,
            tool_whitelist=[capability],
        ))
        deps = item.get("depends_on")
        deps = deps if isinstance(deps, list) else []
        cleaned: list[int] = []
        for dep in deps:
            ok = isinstance(dep, int) and 0 <= dep < len(raw_tasks) and dep != index
            if ok and dep not in cleaned:
                cleaned.append(dep)
        depends_on.append(cleaned)

    if not tasks:
        # 模型没给出任何可用子任务 → 降级为串行单任务，绝不空转。
        # `method` 用于区分**降级原因**：`disabled`=开关关闭（预期行为），
        # `fallback_serial`=模型没给可用任务（非预期，需关注）。
        # 二者都退化为串行单任务，但**必须可区分**，否则"开关关着"会看起来像"模型又失败了"。
        return FanoutDecision(
            mode="serial",
            tasks=[SubagentTask(task_id="sub-1", query=fallback_query,
                                read_only_capability="customer_rag_knowledge",
                                tool_whitelist=["customer_rag_knowledge"])],
            reason=("模型决策已关闭（开关关闭），退化为串行单任务" if method == "disabled"
                    else "模型未产出可用只读子任务，降级为串行单任务"),
            depends_on=[[]],
            source=method if method == "disabled" else "fallback_serial",
        )

    mode = str(args.get("mode") or "parallel").strip().lower()
    if mode not in {"parallel", "serial"}:
        mode = "parallel"

    # ---- 同一份数据只读一次：按 capability 折叠重复的**事实类**子任务 ----
    # 为什么需要（实测依据，2026-09-15）：模型会把**同一个数据源**拆成多个子任务，
    # 例如"我的总资产"与"我的持仓数量"都映射到 `customer_asset_summary`，
    # 实测导致 `get_my_asset_summary` 被调用 **2 次**、融合答案里
    # "总资产 3,000,000.00 元" 出现 **2 次** —— 同一份数据读两遍并重复呈现。
    #
    # 关键设计判断：**只折叠"确定性事实工具"，不折叠 `customer_rag_knowledge`**。理由：
    #   - 事实工具（资产/订单/工单/风险限制）的返回**只取决于 customer_id**，与 query 无关
    #     → 同一 capability 查两次**必然**是同一份数据，折叠无信息损失；
    #   - 知识检索的返回**取决于 query**："公司业务"与"开户流程"虽同属
    #     `customer_rag_knowledge`，但检索的是**不同知识**，折叠会**丢答案**。
    # 这个区分必须做对：笼统地"按 capability 去重"会把两个不同的知识问题合并成一个，
    # 属于**用去重引入正确性缺陷**——比重复本身更糟。
    folded: list[SubagentTask] = []
    fold_index: dict[str, int] = {}                 # capability → 折叠后下标
    old_to_new: dict[int, int] = {}                 # 原始下标 → 折叠后下标
    folded_count = 0
    for old_index, task in enumerate(tasks):
        cap = task.read_only_capability
        if cap in _DEDUPABLE_FACT_CAPABILITIES and cap in fold_index:
            old_to_new[old_index] = fold_index[cap]
            folded_count += 1
            continue
        old_to_new[old_index] = len(folded)
        fold_index[cap] = len(folded)
        folded.append(task)

    if folded_count:
        logger.info("subagent.fanout_folded_duplicate_facts folded=%d", folded_count)
        tasks = folded
        # 依赖下标必须**重新映射**后再过滤：折叠会让下标前移，不映射就会指向错位的任务。
        #
        # 注意：结果必须按**新下标**组织（长度 == 折叠后任务数），
        # 而不是按旧下标 enumerate 一遍——后者会产出与 tasks 不等长的 depends_on，
        # 下游按 index 取值时就会读到错误/缺失的依赖（这个错我第一版就写出来了，
        # 被 test_folding_remaps_depends_on_correctly 当场抓到）。
        new_deps: list[set[int]] = [set() for _ in tasks]
        for old_index, deps in enumerate(depends_on):
            if old_index not in old_to_new:
                continue
            target = old_to_new[old_index]
            for d in deps:
                if d in old_to_new:
                    mapped = old_to_new[d]
                    if mapped != target:          # 折叠成同一个任务时不算依赖自身
                        new_deps[target].add(mapped)
        depends_on = [sorted(s) for s in new_deps]
        # 重新编号 task_id，保持与 depends_on 下标一致
        for i, t in enumerate(tasks):
            t.task_id = f"sub-{i + 1}"

    return FanoutDecision(
        mode=mode,
        tasks=tasks,
        reason=str(args.get("reason") or "").strip(),
        depends_on=depends_on,
        source=method,
    )


def order_subtasks_by_dependency(
    tasks: list[SubagentTask], depends_on: list[list[int]],
) -> tuple[list[SubagentTask], dict[str, list[str]]]:
    """按 `depends_on` 把子任务拓扑排序，并返回「task_id → 它直接依赖的 task_id 列表」。

    为什么需要（真实踩坑）：`SubagentExecutor` 只用 `Semaphore(max_concurrent)` 限流，
    **不保证顺序** —— 串行模式下 `max_concurrent=1` 只是"一次跑一个"，
    谁先抢到信号量谁先跑（按 `gather` 创建顺序，而非依赖顺序）。
    若不显式排序，后序子任务会拿到空的前序上下文，退化成"碰巧正确"。

    依赖下标是相对**原始下标**的，排序后必须重新映射回 task_id，
    否则依赖关系会在排序后错位（这是最容易写错的一步）。
    """
    id_by_index = {i: t.task_id for i, t in enumerate(tasks)}
    deps_by_id: dict[str, list[str]] = {}
    for i, task in enumerate(tasks):
        deps_by_id[task.task_id] = [
            id_by_index[d] for d in (depends_on[i] if i < len(depends_on) else [])
            if d in id_by_index
        ]

    ordered: list[SubagentTask] = []
    done: set[str] = set()
    remaining = list(tasks)
    while remaining:
        ready = [t for t in remaining if all(d in done for d in deps_by_id[t.task_id])]
        if not ready:
            # 循环依赖/依赖缺失 → 剩余按原序追加，保证不丢任务
            ordered.extend(remaining)
            break
        ordered.extend(ready)
        done.update(t.task_id for t in ready)
        remaining = [t for t in remaining if t not in ready]
    return ordered, deps_by_id


class ReadOnlyFanoutPlanner:
    """让模型按任务复杂度决定「是否并行子代理」的只读编排器。

    用法：
        planner = ReadOnlyFanoutPlanner(provider=QwenProvider(settings), database=db)
        decision = await planner.plan(customer_id=..., conversation_id=..., query=...)
    """

    def __init__(self, *, provider, database=None, model: str | None = None,
                 max_subtasks: int = MAX_SUBTASKS) -> None:
        self.provider = provider
        self.database = database
        self.max_subtasks = max(1, int(max_subtasks))
        if model is None:
            try:
                from app.core.settings import get_settings
                model = getattr(get_settings(), "qwen_tool_model", None)
            except Exception:  # noqa: BLE001 - 配置不可读时回退 provider 默认
                model = None
        self.model = model

    async def _audit(self, *, customer_id: str, conversation_id: str, query: str,
                     decision: FanoutDecision, error: str | None = None) -> None:
        """决策留痕。失败不影响主流程。"""
        if self.database is None:
            return
        try:
            from app.models.audit import AuditLog
            async with self.database.session_factory() as session:
                session.add(AuditLog(
                    id=str(uuid4()), actor_user_id=customer_id, action="customer_agent.subagent_fanout",
                    resource_type="customer_service_conversation", resource_id=conversation_id,
                    detail=json.dumps({
                        "query_length": len(query or ""),
                        "mode": decision.mode,
                        "effective_mode": decision.effective_mode,
                        "reason": decision.reason,
                        "source": decision.source,
                        "error": error,
                        "subtasks": [
                            {"task_id": t.task_id, "capability": t.read_only_capability,
                             "depends_on": decision.depends_on[i] if i < len(decision.depends_on) else []}
                            for i, t in enumerate(decision.tasks)
                        ],
                        "read_only": True,
                        "max_concurrent": 2 if decision.effective_mode == "parallel" else 1,
                    }, ensure_ascii=False),
                ))
                await session.commit()
        except Exception:  # noqa: BLE001 - 审计失败不阻塞
            pass

    async def plan(self, *, customer_id: str, conversation_id: str, query: str) -> FanoutDecision:
        """请模型决策；任何失败都降级为串行单任务。"""
        fallback = parse_fanout_decision(None, fallback_query=query)
        if self.provider is None or getattr(self.provider, "_client", None) is None:
            await self._audit(customer_id=customer_id, conversation_id=conversation_id,
                              query=query, decision=fallback, error="provider_unavailable")
            return fallback
        system = _SYSTEM + "\n（可选 capability 及含义：\n" + _capability_lines() + "\n）"
        try:
            raw = await self.provider.request_tool_call(
                system=system, user=f"用户问题：{query}",
                tools=[_DISPATCH_TOOL], model=self.model,
            )
        except Exception as exc:  # noqa: BLE001 - 决策层绝不阻断主流程
            logger.warning("subagent.fanout_error err=%s", type(exc).__name__)
            await self._audit(customer_id=customer_id, conversation_id=conversation_id,
                              query=query, decision=fallback, error=type(exc).__name__)
            return fallback

        if not raw:
            # 模型没调工具（例如问题太简单，它认为不需要拆）→ 串行单任务。
            await self._audit(customer_id=customer_id, conversation_id=conversation_id,
                              query=query, decision=fallback, error="no_tool_call")
            return fallback

        decision = parse_fanout_decision(raw, fallback_query=query)
        await self._audit(customer_id=customer_id, conversation_id=conversation_id,
                          query=query, decision=decision)
        return decision
