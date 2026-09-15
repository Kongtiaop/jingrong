from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

IntentName = Literal[
    "greeting", "human_handoff", "workorder_status", "order_status", "risk_restriction",
    "asset_summary", "product_status", "company_info", "company_business", "faq",
    "workflow", "policy", "risk_public", "product_recommendation", "business_operation", "unknown"
]

@dataclass(frozen=True)
class IntentTask:
    task_id: str
    intent: str
    route: str
    query: str
    priority: str = "normal"
    depends_on: list[str] = field(default_factory=list)
    required: bool = True
    arguments: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    status: str = "planned"
    execution_mode: str = "controlled_workflow"

@dataclass(frozen=True)
class IntentPlan:
    normalized_query: str
    tasks: list[IntentTask]
    clarification: str | None = None
    requires_handoff: bool = False

REACT_INTENTS = {"company_info", "company_business", "faq", "workflow", "policy", "risk_public"}


def execution_mode_for_intent(intent: str) -> str:
    return "bounded_react" if intent in REACT_INTENTS else "controlled_workflow"


def extract_entities(query: str) -> dict[str, str]:
    """Extract explicit identifiers without guessing business meaning."""
    text = query.strip()
    entities: dict[str, str] = {}
    # 标识符形态必须覆盖**生产真实格式**，否则用户粘贴自己的单号会识别不出。
    # 实测（ISSUE-20260914-ORDER-NO-PATTERN-MISSES-REAL-FORMAT）：
    #   orders.order_no 实际形如 `O98E346E6347D4BCB`（见 trading_service.py 生成
    #   `f"O{uuid4().hex[:16].upper()}"`），而原正则只认 `ORD-` 前缀或"订单号:XXX"，
    #   于是"我的订单 O98E346E6347D4BCB 状态" -> entities={}，单号提取不到。
    # 现补上 `O` + 12~20 位十六进制（大小写均可），并保留原有形态以兼容历史/测试数据。
    patterns = {
        "order_no": r"\b(?:ORD[-_A-Z0-9]+|O[A-F0-9]{12,20}|订单号[:：]?\s*[A-Z0-9_-]{6,})\b",
        "ticket_no": r"\b(?:WO[-_A-Z0-9]+|工单号[:：]?\s*[A-Z0-9_-]{6,})\b",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(0).strip()
            value = re.sub(r"^(订单号|工单号)[:：]?\s*", "", value, flags=re.IGNORECASE)
            entities[key] = value
    product_match = re.search(r'(?:产品名称|产品)[:：]?\s*[“\"]?(.+?)(?=现在|目前|是否|状态|在售|停售|详情|$)', text)
    if product_match:
        candidate = product_match.group(1).strip()
        candidate = re.split(r'(?:现在|目前|是否|状态|在售|停售|详情)', candidate, maxsplit=1)[0].strip()
        if candidate:
            entities["product_name"] = candidate
    return entities


# 产品指代消解：句中的指代词/代词（排除“这个月/这个季度”等时间误判）
_PRODUCT_REFERENCE_RE = re.compile(
    r"(?:这个|那个|它|该|此|这款|这只)(?![月季周天年次些类])(?:理财|产品|基金|方案|保险|计划|组合)?"
)
# 时间/周期类查询（如"这个月收益怎么样"），不是产品评估
_TEMPORAL_RE = re.compile(r"(这个月|本月|上月|上个月|最近|本周|上周|年度|今年|去年|近期|每天|每日|一个月|一季度|本季度|这个季度)")


def looks_like_product_assessment(text: str) -> bool:
    return bool(re.search(r"(靠谱|怎么样|如何|好不好|安全|稳不稳|值得|能买|可买|风险|收益|到底)", text))


def is_bare_product_assessment(text: str) -> bool:
    """是否"裸产品评估句"（如"现金管理保本计划靠谱吗"）：非个人、非指代、非时间、像产品评估。

    供规划器与查询路由共用，避免两处守卫不一致。
    """
    norm = re.sub(r"\s+", "", text.strip().lower())
    return bool(
        looks_like_product_assessment(norm)
        and not norm.startswith("我")
        and not _PRODUCT_REFERENCE_RE.search(norm)
        and not _TEMPORAL_RE.search(norm)
    )


def extract_preference_statement(query: str) -> tuple[str, str] | None:
    """从用户明确的自述偏好里抽 (key, canonical_value)；无则 None。

    只认明确的表态（偏/喜欢/倾向/打算…），不猜、不把模糊话术当真。
    canonical：risk_preference ∈ conservative/balanced/aggressive；
    product_preference=steady；investment_horizon_preference=long_term。
    """
    text = re.sub(r"\s+", "", query.strip().lower())
    hedge = r"(?:我)?(?:比较|挺|很|就是|偏|偏好|倾向|喜欢|追求|想要)"
    # 产品偏好（明确提到产品/理财/基金/配置）
    if re.search(r"(?:喜欢|偏好|偏|倾向|只要|就要|想要).{0,3}(?:稳健|稳|保守|平衡)的?(?:产品|理财|基金|配置)", text):
        return ("product_preference", "steady")
    # 风险偏好（必须带偏好动词/程度词，避免把“稳健和激进有什么区别”误判）
    if re.search(hedge + r"(?:保守|保本|低风险)", text):
        return ("risk_preference", "conservative")
    if re.search(hedge + r"(?:稳健|平衡|稳)", text):
        return ("risk_preference", "balanced")
    if re.search(hedge + r"(?:激进|高收益|高风险)", text):
        return ("risk_preference", "aggressive")
    # 期限偏好
    if re.search(r"(?:我)?(?:可以|能|打算|习惯|愿意)(?:长期|中长期)持有", text) or re.search(r"(?:我)?(?:打算|可以|能)(?:长期|中长期)投资", text):
        return ("investment_horizon_preference", "long_term")
    return None


# 购买资格/推荐类歧义（"我能买什么/可以买哪些/有什么能买的"）→ 产品推荐，而非"购买"业务
_BUY_ELIGIBILITY_RE = re.compile(r"(能买什么|可以买什么|能买哪些|可以买哪些|有什么.{0,6}(能|可)买|哪些(我)?(能|可)买|哪些产品(能|可)买|适合(我)?(买|购买|投)什么|推荐(我)?(能|可以)买|买什么产品|能购买什么)")


class CustomerIntentPlanner:
    _patterns: list[tuple[str, str, str, float]] = [
        ("human_handoff", "人工", "handoff", .99), ("human_handoff", "客服接入", "handoff", .99),
        ("workorder_status", "工单", "business_fact", .95), ("order_status", "订单", "business_fact", .90),
        ("risk_restriction", "风险限制", "business_fact", .96), ("risk_restriction", "账户限制", "business_fact", .96),
        ("risk_restriction", "冻结", "business_fact", .92), ("asset_summary", "资产", "business_fact", .92),
        ("asset_summary", "持仓", "business_fact", .92), ("asset_summary", "余额", "business_fact", .90),
        ("product_status", "产品状态", "business_fact", .95), ("product_status", "是否在售", "business_fact", .94),
        ("product_recommendation", "推荐产品", "suitability", .96), ("product_recommendation", "适合我的产品", "suitability", .96), ("product_recommendation", "推荐", "suitability", .88),
        ("business_operation", "申购", "draft_operation", .98), ("business_operation", "购买", "draft_operation", .98), ("business_operation", "买入", "draft_operation", .98), ("business_operation", "赎回", "draft_operation", .98), ("business_operation", "转账", "draft_operation", .98), ("business_operation", "下单", "draft_operation", .98),
        ("company_info", "公司", "rag_explanation", .70), ("company_business", "业务", "rag_explanation", .75),
        ("faq", "怎么", "rag_explanation", .55), ("workflow", "流程", "rag_explanation", .80),
        # policy：**不能只靠"政策"一个词**（ISSUE-20260914-POLICY-INTENT-NEVER-TRIGGERED）。
        # 实测：唯一触发词是"政策"，导致 31 条候选问句里 0 条能命中该意图，
        # 而 policy 类目下确有 24 个 chunk（《个人投资者适当性管理指南》等）。
        # 用户实际不会问"什么是政策"，而是问**制度名词**：
        #   什么是双录 / 什么是投资者适当性管理 / 反洗钱相关规定 / 什么是合格投资者
        # 故按"该意图承载的内容"扩展触发词，而非按字面词。
        # 注意：本表的匹配是**收集式**（一个问句可命中多个意图，见 plan() 的 seen 去重），
        # 所以扩展触发词是**与 risk_public/faq 并存**，不会把它们挤掉。
        ("policy", "政策", "rag_explanation", .80),
        ("policy", "适当性", "rag_explanation", .88),
        ("policy", "双录", "rag_explanation", .88),
        ("policy", "反洗钱", "rag_explanation", .88),
        ("policy", "合格投资者", "rag_explanation", .88),
        ("policy", "冷静期", "rag_explanation", .85),
        ("policy", "风险揭示", "rag_explanation", .85),
        ("policy", "合规", "rag_explanation", .80),
        ("policy", "管理办法", "rag_explanation", .80),
        ("policy", "规定", "rag_explanation", .70),
        ("risk_public", "风险", "rag_explanation", .70),
    ]

    def plan(self, query: str, context: dict | None = None) -> IntentPlan:
        context = context or {}
        active_product = str((context.get("active_product") or "").strip())
        normalized = re.sub(r"\s+", "", query.strip().lower())
        if not normalized:
            return IntentPlan(normalized_query="", tasks=[], clarification="请描述您需要查询或办理的事项。")
        if re.match(r"^(你好|您好|嗨|hello|hi|谢谢|感谢|再见)[!！。？? ]*$", normalized):
            return IntentPlan(normalized_query=normalized, tasks=[IntentTask("t1", "greeting", "direct", query, confidence=.99, arguments=extract_entities(query), execution_mode=execution_mode_for_intent("greeting"))])
        # 购买资格/推荐歧义："我能买什么产品"是问"推荐我能买的"，不是"我要买"
        if _BUY_ELIGIBILITY_RE.search(normalized):
            return IntentPlan(normalized_query=normalized, tasks=[IntentTask("t1", "product_recommendation", "suitability", query, confidence=.9, execution_mode=execution_mode_for_intent("product_recommendation"))])
        if any(x in normalized for x in ("人工", "客服接入", "转人工")):
            return IntentPlan(normalized_query=normalized, tasks=[IntentTask("t1", "human_handoff", "handoff", query, priority="high", confidence=.99, arguments=extract_entities(query), execution_mode=execution_mode_for_intent("human_handoff"))], requires_handoff=True)
        tasks=[]; seen=set()
        for intent, marker, route, confidence in self._patterns:
            if marker in normalized and intent not in seen:
                task_id=f"t{len(tasks)+1}"; depends=[]
                if intent in {"workflow", "policy", "faq"} and any(t.intent in {"order_status","workorder_status"} for t in tasks):
                    depends=[next(t.task_id for t in tasks if t.intent in {"order_status","workorder_status"})]
                if intent == "business_operation" and any(t.intent == "product_recommendation" for t in tasks):
                    depends=[next(t.task_id for t in tasks if t.intent == "product_recommendation")]
                # 制度说明依赖风险事实（ISSUE-20260914-NO-TASK-ANSWER-PASSING）：
                # 用户问"我有没有风险限制"时，答案是"**你是什么等级 → 能买什么**"，
                # 这必须先用事实（客户风险等级，查库）再去套制度规则。
                # 若 risk_public 不依赖 risk_restriction，它就拿不到等级，
                # 只能空谈制度、甚至编出"是的，您有风险限制"这类与事实矛盾的事实断言
                # （实测：账户状态实为"未显示限制"，见 STATUS-MACHINE-LACKS-CONSISTENCY-DIMENSION）。
                # 注意：这里靠 patterns 的**先后顺序**保证 risk_restriction 已先入 tasks
                # （risk_restriction 在 _patterns 中早于 risk_public 出现）。
                if intent == "risk_public" and any(t.intent == "risk_restriction" for t in tasks):
                    depends=[next(t.task_id for t in tasks if t.intent == "risk_restriction")]
                task_query = query
                if intent == "product_recommendation" and any(marker in normalized for marker in ("申购", "购买", "买入", "下单")):
                    task_query = re.split(r"(?:并且|同时|然后|并|再|后)", query, maxsplit=1)[0]
                tasks.append(IntentTask(task_id, intent, route, task_query, confidence=confidence, depends_on=depends, arguments=extract_entities(task_query), execution_mode=execution_mode_for_intent(intent))); seen.add(intent)
        if "推荐" in normalized and any(marker in normalized for marker in ("申购", "购买", "买入", "下单")):
            recommendation_query = re.split(r"(?:并且|同时|然后|并|再|后)", query, maxsplit=1)[0]
            tasks = [
                IntentTask(
                    "t1", "product_recommendation", "suitability", recommendation_query,
                    confidence=.96, arguments=extract_entities(recommendation_query),
                    execution_mode=execution_mode_for_intent("product_recommendation"),
                ),
                IntentTask(
                    "t2", "business_operation", "draft_operation", query,
                    confidence=.98, depends_on=["t1"], arguments=extract_entities(query),
                    execution_mode=execution_mode_for_intent("business_operation"),
                ),
            ]
        # 裸产品名句（"X怎么样"）：被 faq 的"怎么"marker 抢占，转回 product_status
        if len(tasks) == 1 and tasks[0].intent == "faq" and "怎么样" in normalized and looks_like_product_assessment(normalized) \
                and not normalized.startswith("我") and not _PRODUCT_REFERENCE_RE.search(normalized) and not _TEMPORAL_RE.search(normalized) \
                and not any(h in normalized for h in ("开通", "办理", "申请", "注册", "操作", "设置", "预约", "解约", "提现", "转账", "取现")):
            tasks = [IntentTask("t1", "product_status", "business_fact", query, confidence=.85, arguments=extract_entities(query), execution_mode=execution_mode_for_intent("product_status"))]
        if not tasks:
            # 产品指代消解：当前句无显式意图 marker，但指向上下文中的产品
            entities = extract_entities(query)
            reference = _PRODUCT_REFERENCE_RE.search(normalized)
            product_name = entities.get("product_name")
            resolved_product = None
            if active_product and reference:
                resolved_product = active_product
            elif product_name and not reference and looks_like_product_assessment(normalized) and len(product_name) >= 2:
                resolved_product = product_name
            if resolved_product:
                # 把解析出的产品名拼进 query，_product_status 按 query 文本匹配产品
                resolved_query = f"{resolved_product} {query}".strip()
                return IntentPlan(
                    normalized_query=normalized,
                    tasks=[IntentTask(
                        "t1", "product_status", "business_fact", resolved_query,
                        confidence=.9, arguments={"product_name": resolved_product},
                        execution_mode=execution_mode_for_intent("product_status"),
                    )],
                )
            if is_bare_product_assessment(normalized):
                # 裸产品名句：交给 _product_status 从 query 文本匹配产品名；匹配不到会安全走澄清
                return IntentPlan(
                    normalized_query=normalized,
                    tasks=[IntentTask(
                        "t1", "product_status", "business_fact", query,
                        confidence=.85, arguments=entities,
                        execution_mode=execution_mode_for_intent("product_status"),
                    )],
                )
            return IntentPlan(normalized_query=normalized, tasks=[IntentTask("t1", "unknown", "handoff", query, confidence=.20, execution_mode=execution_mode_for_intent("unknown"))], clarification="您想查询哪一类信息，例如订单、工单、资产、风险限制或公司业务？")
        return IntentPlan(normalized_query=normalized, tasks=tasks, requires_handoff=any(t.intent == "human_handoff" for t in tasks))

