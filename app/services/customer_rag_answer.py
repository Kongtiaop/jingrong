from __future__ import annotations

from dataclasses import dataclass

from app.infrastructure.qwen import QwenProvider
from app.services.customer_rag_service import CustomerRagResult


async def _call_chat(provider, **kwargs):
    """兼容真实 provider（chat_completions，带受控超时）与测试 fake（仅有 _client.chat…）。"""
    caller = getattr(provider, "chat_completions", None)
    if caller is not None:
        return await caller(**kwargs)
    return await provider._client.chat.completions.create(**kwargs)


@dataclass(frozen=True)
class CustomerRagAnswer:
    answer: str
    generation_status: str


def build_answerability_judge(provider: QwenProvider):
    """构造"能否从引用作答"的 LLM-judge（分层门控的 ambiguous 带用）。

    返回 async judge(query, contexts) -> bool：基于引用能否回答用户问题。
    """
    async def judge(query: str, contexts: list[str]) -> bool:
        if provider._client is None:
            return False
        evidence = "\n\n".join(f"[引用 {index}] {content}" for index, content in enumerate(contexts, 1))
        prompt = (
            "你是财富管理客服助手。基于以下检索引用，判断能否回答用户的问题。\n"
            "如果引用足以回答，回答 yes；如果引用与问题无关或不足，回答 no。\n\n"
            f"用户问题：{query}\n\n检索引用：\n{evidence}\n\n只回答 yes 或 no。"
        )
        try:
            response = await _call_chat(
                provider,
                model=provider.settings.qwen_max_model,  # judge 用更强模型（qwen-max），避免自评偏置
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            return "yes" in (response.choices[0].message.content or "").strip().lower()
        except Exception:  # noqa: BLE001 - judge 失败按不可作答处理（保守拒答）
            return False
    return judge


class CustomerRagAnswerService:
    def __init__(self, provider: QwenProvider) -> None:
        self.provider = provider

    async def generate(self, *, query: str, result: CustomerRagResult, conversation_summary: str = "") -> CustomerRagAnswer:
        if result.handoff_required or not result.citations:
            return CustomerRagAnswer(answer=result.message, generation_status="not_generated")
        if self.provider._client is None:
            return CustomerRagAnswer(answer="已找到相关知识依据，但当前暂未配置回答模型。请联系人工客服。", generation_status="provider_unavailable")
        evidence = "\n\n".join(f"[引用 {index}] {item.content}" for index, item in enumerate(result.citations, 1))
        history_block = ""
        if conversation_summary:
            history_block = (
                "\n\n[客户近期对话摘要，仅用于理解语境，**不得据此编造事实**，回答仍只能依据引用资料]："
                f"{conversation_summary[:1200]}"
            )
        prompt = (
            "你是财富管理平台客服助手。只能根据提供的引用回答，不得编造、承诺收益或执行任何交易。"
            "如果引用不足以回答，必须明确说资料不足并建议转人工。使用清晰、谨慎、客户可理解的中文。\n\n"
            f"客户问题：{query}\n\n引用资料：\n{evidence}{history_block}"
        )
        try:
            response = await _call_chat(
                self.provider,
                model=self.provider.settings.qwen_chat_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                return CustomerRagAnswer(answer=result.message, generation_status="empty_response")
            return CustomerRagAnswer(answer=text, generation_status="generated")
        except Exception:  # noqa: BLE001
            return CustomerRagAnswer(answer="当前回答服务暂时不可用，已保留检索依据并建议转人工客服。", generation_status="generation_failed")
