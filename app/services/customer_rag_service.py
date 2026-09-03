from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.common.knowledge_scope import KnowledgeScope
from app.infrastructure.qwen import QwenProvider
from app.services.knowledge_retrieval import KnowledgeCitation, WealthKnowledgeRetriever
from app.services.understanding_config import understanding_config

# 线上 bad case 回流开关：生产设 BAD_CASE_COLLECT=1 时，低证据/转人工的 RAG 问答会落积累池。
_COLLECT_BAD_CASE = os.environ.get("BAD_CASE_COLLECT") == "1"


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
    def __init__(self, retriever: WealthKnowledgeRetriever, embedding_provider: QwenProvider | None = None, *,
                 min_score: float | None = None, min_similarity: float | None = None, embedding_timeout: float = 8.0,
                 config: Any | None = None, ambiguous_judge=None) -> None:
        self.retriever = retriever
        self.embedding_provider = embedding_provider
        # 理解层配置：min_score/top_k/min_similarity/分层阈值走 config/understanding.yaml（模型可自调）
        self._config = config or understanding_config
        self.min_score = min_score if min_score is not None else self._config.get_min_score()
        self.min_similarity = min_similarity if min_similarity is not None else self._config.get_min_similarity()
        self.thresholds = self._config.get_rejection_thresholds()
        self.embedding_timeout = embedding_timeout
        # 可选：ambiguous 带的 LLM-judge（async judge_answerable(query, contexts)->bool）；None 则退回 min_similarity 单阈值
        self.ambiguous_judge = ambiguous_judge

    def _evidence_decision(self, best_sim: float | None, judge_result: bool | None = None) -> str:
        from app.services.rag_eval_metrics import decide_evidence
        t = self.thresholds
        return decide_evidence(best_sim, reject=t["reject"], answer=t["answer"],
                               judge_result=judge_result, fallback=self.min_similarity)

    def _maybe_collect_bad_case(self, query: str, result: CustomerRagResult) -> None:
        """线上 bad case 回流（默认关；异常安全，绝不阻塞/破坏响应）。"""
        if not _COLLECT_BAD_CASE:
            return
        try:
            from app.services.bad_case_collector import BadCaseStore
            BadCaseStore().collect(query, rag=result)
        except Exception:  # noqa: BLE001 - 采集失败不影响主流程
            pass

    async def answer(self, *, query: str, intent: CustomerKnowledgeIntent, top_k: int | None = None) -> CustomerRagResult:
        # RAG 检索参数走理解层配置（top_k 默认按 query 难度动态给定，规则驱动；调用方可覆盖）。
        top_k = top_k if top_k is not None else self._config.get_top_k_for_query(query)
        greeting = greeting_message(query)
        if greeting is not None:
            return CustomerRagResult(intent=intent, answer_mode="greeting", evidence_status="not_required", citations=[], handoff_required=False, message=greeting)
        if intent == "product_recommendation":
            return CustomerRagResult(intent=intent, answer_mode="database_recommendation", evidence_status="not_applicable", citations=[], handoff_required=False, message="产品推荐使用实时 Product 数据和适当性服务，不依赖当前缺失的产品 RAG 文档。")
        normalized_query = normalize_customer_query(query, intent)
        scope = scope_for_customer_intent(intent)
        evidence_status = "sufficient"
        query_embedding = None
        if self.embedding_provider is not None:
            try:
                # 门控/向量排序用【原始 query】的相似度；normalized_query 仅用于文本排序。
                # 若用 normalized_query 嵌入，company_info 等会强制追加"公司名称/联系方式"等膨胀词，
                # 把无关查询（如天气）的相似度抬高，导致相似度门控失效。
                query_embedding = (await asyncio.wait_for(self.embedding_provider.embed([query]), timeout=self.embedding_timeout))[0]
            except Exception:
                evidence_status = "embedding_degraded"

        async def _do_search(effective_top_k: int):
            nonlocal evidence_status
            try:
                return await self.retriever.search(query=normalized_query, query_embedding=query_embedding, top_k=effective_top_k, scope=scope)
            except Exception:
                # embedding 降级：退纯文本再查一次，并标记降级证据状态
                evidence_status = "embedding_degraded" if query_embedding is not None else "text_fallback"
                try:
                    return await self.retriever.search(query=normalized_query, top_k=effective_top_k, scope=scope)
                except Exception:
                    return []

        # 先按当前 top_k 检索
        top_k_max = int(self._config.get_top_k_max())
        top_k_bump = int(self._config.get_top_k_bump())
        citations = await _do_search(top_k)
        useful = [item for item in citations if item.score >= self.min_score]
        # C 兜底：证据不足(无 ≥min_score 引用) → 扩 top_k 重查，最多到 top_k_max，避免
        # RRF 在 top_k 太紧时把某一路单独命中的 gold 挤出。
        if not useful and top_k < top_k_max:
            effective = min(top_k + top_k_bump, top_k_max)
            if effective > top_k:
                top_k = effective
                citations = await _do_search(top_k)
                useful = [item for item in citations if item.score >= self.min_score]
        if not useful:
            result = CustomerRagResult(intent=intent, answer_mode="handoff", evidence_status="low_confidence", citations=[], handoff_required=True, message="当前知识库没有足够依据，建议转人工客服或创建工单。")
            self._maybe_collect_bad_case(query, result)
            return result
        # 分层证据门控：明显无关(低于 reject)直接拒、明显相关(高于 answer)直接答、
        # 中间带(ambiguous)用 LLM-judge 判证据充分性（无 judge 则退回 min_similarity 单阈值）。
        sims = [c.vector_similarity for c in useful if c.vector_similarity is not None]
        best_sim = max(sims) if sims else None
        # ambiguous 带：若配置用 LLM-judge 且提供了 judge，则调用判证据充分性
        judge_result = None
        if self.thresholds.get("judge_ambiguous") and self.ambiguous_judge is not None and best_sim is not None \
                and self.thresholds["reject"] <= best_sim < self.thresholds["answer"]:
            try:
                judge_result = await self.ambiguous_judge(query, [c.content for c in useful])
            except Exception:  # noqa: BLE001 - judge 失败不阻塞，按不通过处理
                judge_result = False
        decision = self._evidence_decision(best_sim, judge_result=judge_result)
        if decision == "reject":
            result = CustomerRagResult(intent=intent, answer_mode="handoff", evidence_status="low_confidence", citations=[], handoff_required=True, message="当前知识库没有足够依据，建议转人工客服或创建工单。")
            self._maybe_collect_bad_case(query, result)
            return result
        result = CustomerRagResult(intent=intent, answer_mode="rag_answer", evidence_status=evidence_status, citations=useful, handoff_required=False, message="可基于客服知识库引用生成回答。")
        if evidence_status != "sufficient":
            self._maybe_collect_bad_case(query, result)
        return result
