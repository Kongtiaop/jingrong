# -*- coding: utf-8 -*-
"""投顾开放问句的库端只读工具层（advisor-ask grounding, 无 LLM）。

供 advisor-ask 有界函数调用使用：从“在售产品 + 客户适当性”真实业务数据里取出结构化事实，
让 LLM 只能基于真实 product.risk_level / min_amount / type 与对客户的真实适当性命中(reasons)
作答，避免对具体产品凭空编 R2/R3、收益率或适配结论。

只读：不改任何表，不为某客户写记忆，不越权；授权(角色/授权范围)由上层 profile 端点负责。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.suitability_service import SuitabilityService
from app.services.asset_summary_service import AssetSummaryService


@dataclass(frozen=True)
class ProductFact:
    id: str
    name: str
    product_type: str
    risk_level: str
    minimum_amount: float
    liquidity: str
    term_days: int = 0
    description: str = ""


@dataclass(frozen=True)
class SuitabilityFact:
    product_id: str
    product_name: str
    product_risk_level: str
    minimum_amount: float
    matched: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict
    facts: list  # list[ProductFact] | list[SuitabilityFact]
    text: str  #  可注入 prompt 的浓缩文本


# 工具白名单（只读）；本轮 LLM 允许调用的集合
ADVISOR_TOOL_ALLOWLIST: frozenset[str] = frozenset({"find_products", "product_suitability"})


# OpenAI-style function schemas, used for the LLM tool-selection round
ADVISOR_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "find_products",
            "description": "按关键词从数据库召回在售产品，返回库内真实 risk_level / 起购金额 / 类型 / 期限，用于回答'有没有某产品、库里它是什么样的'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "业务词/产品名相关关键词，如‘中短债’‘稳健’"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 6"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "product_suitability",
            "description": "给定被服务客户，查对该客户在售产品的真实适当性命中/排除（含系统 reasons 判定依据），用于回答'某产品适不适合这位客户'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "要查适配的具体产品名（可选，缺省返回全部命中/排除）"},
                    "top_k": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


class AdvisorReadTools:
    """投顾只读工具：基于现有 in-sales 产品 + 客户适当性计算真实事实。

    用法：resolve uow 后，按需调用 find_products / suitability_for；
    工具调用是否被允许、由上层按 ADVISOR_TOOL_ALLOWLIST 判定。
    """

    def __init__(self, uow) -> None:
        self.uow = uow
        self._suitability = SuitabilityService(
            risk_repository=getattr(uow, "risks", None),
            product_repository=getattr(uow, "products", None),
            profile_repository=getattr(uow, "profiles", None),
            asset_service=AssetSummaryService(
                account_repository=getattr(uow, "accounts", None),
                holding_repository=getattr(uow, "holdings", None),
                asset_repository=getattr(uow, "assets", None),
            ),
        )

    # 命中 TopK 在售产品（真实 risk/min/type/期限）—— 关键词匹配，不做语义猜档
    async def find_products(self, session, *, query: str = "", top_k: int = 6) -> list[ProductFact]:
        row_writer = getattr(self.uow, "products", None)
        if row_writer is None:
            return []
        rows = await row_writer.list_active(session)
        needle = _clean(query)
        ranked = []
        for p in rows:
            pname = _clean(getattr(p, "name", ""))
            score = 0
            if needle and (needle in pname or pname in needle):
                score = 3
            lowered = (getattr(p, "name", "") or "").lower()
            tokens = set(re.findall(r"[A-Za-z0-9]+", lowered))
            if tokens & set(re.findall(r"[A-Za-z0-9]+", needle.lower())):
                score = max(score, 2)
            if needle and not score:
                continue
            ranked.append(
                (score, p)
            )
        ranked.sort(key=lambda item: (-item[0], getattr(item[1], "name", "")))
        picked = ranked[:top_k]
        out = []
        for _, p in picked:
            out.append(
                ProductFact(
                    id=str(p.id),
                    name=getattr(p, "name", ""),
                    product_type=getattr(p, "product_type", ""),
                    risk_level=getattr(p, "risk_level", ""),
                    minimum_amount=float(getattr(p, "minimum_amount", 0) or 0),
                    liquidity=getattr(p, "liquidity", ""),
                    term_days=int(getattr(p, "term_days", 0) or 0),
                    description=getattr(p, "description", "") or "",
                )
            )
        return out

    # 该客户在这一(或某)在售产品上的真实适当性命中/排除 + reasons
    async def product_suitability(self, session, *, customer_id: str, product_id: str | None = None, query: str = "") -> list[SuitabilityFact]:
        rec = await self._run_recommend_no_throw(session, customer_id, query, product_id)
        if rec is None:
            return []
        matched, excluded = rec
        if product_id:
            # 单一产品：精确给 matched / excluded 对应项，给最相关真实结论
            hit = next((m for m in matched if m.product_id == product_id), None)
            if hit is None:
                hit = next((e for e in excluded if e.product_id == product_id), None)
            if hit is None:
                return []
            return [SuitabilityFact(product_id=hit.product_id, product_name=hit.product_name,
                                    product_risk_level=hit.product_risk_level, minimum_amount=hit.minimum_amount,
                                    matched=bool(hit.matched), reasons=list(hit.reasons))]
        out: list[SuitabilityFact] = []
        for item in matched[:6]:
            out.append(SuitabilityFact(product_id=item.product_id, product_name=item.product_name,
                                       product_risk_level=item.product_risk_level, minimum_amount=item.minimum_amount,
                                       matched=True, reasons=list(item.reasons)))
        for item in excluded[:4]:
            out.append(SuitabilityFact(product_id=item.product_id, product_name=item.product_name,
                                       product_risk_level=item.product_risk_level, minimum_amount=item.minimum_amount,
                                       matched=False, reasons=list(item.reasons)))
        return out

    async def _run_recommend_no_throw(self, session, customer_id, query="", product_id=None):
        try:
            rec = await self._suitability.recommend(session, customer_id, preferences={})
            return rec.matches, rec.excluded
        except Exception:  # noqa: BLE001 - 工具失败以空结果返回，不抛崩主流程
            return None

    async def execute_tool(self, session, *, name: str, arguments: dict, customer_id: str) -> tuple[list, str]:
        """执行单个只读工具，返回 (facts, 可注入 prompt 的文本)。

        fallthrough：工具名不在白名单 → ([], '')。
        """
        arguments = arguments or {}
        if name == "find_products":
            facts = await self.find_products(session, query=arguments.get("query", ""), top_k=int(arguments.get("top_k") or 6))
            return facts, product_facts_to_text(facts)
        if name == "product_suitability":
            pname = arguments.get("product_name") or ""
            if pname:
                cands = await self.find_products(session, query=pname, top_k=1)
                target_pid = cands[0].id if cands else None
            else:
                target_pid = None
            if target_pid:
                facts = await self.product_suitability(session, customer_id=customer_id, product_id=target_pid)
            else:
                facts = await self.product_suitability(session, customer_id=customer_id, product_id=None)
            return facts, suitability_facts_to_text(facts)
        return [], ""


def product_facts_to_text(facts: list[ProductFact]) -> str:
    """把真实产品事实浓缩成注入 LLM 的文本，仅此，可防模型凭空编档位。"""
    if not facts:
        return ""
    lines = [
        f"- {f.name} | 风险档级 {f.risk_level} | 类型 {f.product_type} | 起购 ¥{f.minimum_amount:,.0f}"
        + ("" if not f.term_days else f" | 期限 {f.term_days} 天")
        + ("" if not f.description else f" | {f.description[:80]}")
        for f in facts
    ]
    return "在售产品（库内真实信息，风险/金额以库为准，勿臆测）：\n" + "\n".join(lines)


def suitability_facts_to_text(facts: list[SuitabilityFact]) -> str:
    if not facts:
        return ""
    head = "该客户对以下产品的真实适当性命中/排除（来自数据库规则，reasons 为系统判定依据）：\n"
    parts = []
    for f in facts:
        mark = "命中" if f.matched else "排除"
        reason = next((r for r in f.reasons if "不满足" in r or "低于" in r), None) or (f.reasons[0] if f.reasons else "")
        parts.append(f"- {f.product_name}（库内 risk {f.product_risk_level}）：{mark}。" + (f"判定理由：{reason}" if reason else ""))
    return head + "\n".join(parts)


def tool_result_text(name: str, facts) -> str:
    if name == "find_products":
        return product_facts_to_text(facts)
    return suitability_facts_to_text(facts)
