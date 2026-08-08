from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.common.knowledge_scope import KnowledgeScope
from app.services.knowledge_retrieval import KnowledgeCitation, WealthKnowledgeRetriever


CustomerKnowledgeIntent = Literal["company_info", "company_business", "faq", "workflow", "policy", "risk_public", "product_recommendation"]

_GREETING_PATTERNS = (
    r"^(你好|您好|嗨|哈喽|hello|hi|在吗|有人吗)[!！。？? ]*$",
    r"^(你是谁|你是做什么的|你能做什么)[？?！!。 ]*$",
    r"^(谢谢|感谢|多谢|好的|好哒|再见|拜拜|没事了|不用了)[!！。？? ]*$",
)


@dataclass(frozen=True)
class CustomerRagResult:
    intent: str
    answer_mode: str
    evidence_status: str
    citations: list[KnowledgeCitation]
    handoff_required: bool
    message: str


def scope_for_customer_intent(intent: CustomerKnowledgeIntent) -> KnowledgeScope:
    if intent == "product_recommendation":
        return KnowledgeScope(categories=())
    return KnowledgeScope.public_customer()


def greeting_message(query: str) -> str | None:
    normalized = re.sub(r"\s+", "", query).lower()
    if not normalized:
        return None
    if re.match(_GREETING_PATTERNS[0], normalized):
        return "您好，我是智能财富管家客服助手。您可以咨询公司业务、服务流程、常见问题、金融政策和资料补充规则。"
    if re.match(_GREETING_PATTERNS[1], normalized):
        return "我是智能财富管家客服助手，可以基于公开客服知识为您说明业务、流程、政策和资料规则；产品推荐、交易和账户操作仍会走受控业务流程。"
    if re.match(_GREETING_PATTERNS[2], normalized):
        return "不客气。如需继续咨询公司业务、服务流程、政策或资料规则，随时告诉我。"
    return None


def normalize_customer_query(query: str, intent: CustomerKnowledgeIntent) -> str:
    text = re.sub(r"\s+", "", query).strip()
    replacements = {
        "公司名字": "公司名称 公司叫什么 叫什么名字 公司全称",
        "公司名": "公司名称 公司叫什么 叫什么名字",
        "贵司名字": "公司名称 公司叫什么",
        "客户规模": "客户数量 客户总数 服务客户数量 管理客户规模",
        "客户有多少": "客户数量 客户总数 服务客户数量",
        "规模多大": "客户数量 客户总数 服务规模",
        "客服电话": "客服热线 联系电话 联系方式",
        "怎么联系": "客服热线 联系电话 联系方式",
        "你们做什么": "公司业务 公司服务 服务范围",
        "能提供什么": "公司业务 公司服务 服务范围",
    }
    expansions = [value for key, value in replacements.items() if key in text]
    if intent == "company_info":
        expansions.append("公司名称 公司介绍 联系方式 客户数量")
    elif intent == "company_business":
        expansions.append("公司业务 公司服务 服务范围 客户")
    return f"{text} {' '.join(expansions)}".strip()


class CustomerRagService:
    def __init__(self, retriever: WealthKnowledgeRetriever, *, min_score: float = 0.45) -> None:
        self.retriever = retriever
        self.min_score = min_score

    async def answer(self, *, query: str, intent: CustomerKnowledgeIntent, top_k: int = 5) -> CustomerRagResult:
        greeting = greeting_message(query)
        if greeting is not None:
            return CustomerRagResult(intent=intent, answer_mode="greeting", evidence_status="not_required", citations=[], handoff_required=False, message=greeting)
        if intent == "product_recommendation":
            return CustomerRagResult(intent=intent, answer_mode="database_recommendation", evidence_status="not_applicable", citations=[], handoff_required=False, message="产品推荐使用实时 Product 数据和适当性服务，不依赖当前缺失的产品 RAG 文档。")
        normalized_query = normalize_customer_query(query, intent)
        citations = await self.retriever.search(query=normalized_query, top_k=top_k, scope=scope_for_customer_intent(intent))
        useful = [item for item in citations if item.score >= self.min_score]
        if not useful:
            return CustomerRagResult(intent=intent, answer_mode="handoff", evidence_status="low_confidence", citations=[], handoff_required=True, message="当前知识库没有足够依据，建议转人工客服或创建工单。")
        return CustomerRagResult(intent=intent, answer_mode="rag_answer", evidence_status="sufficient", citations=useful, handoff_required=False, message="可基于客服知识库引用生成回答。")
