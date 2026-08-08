from __future__ import annotations

from dataclasses import dataclass

from app.infrastructure.qwen import QwenProvider
from app.services.customer_rag_service import CustomerRagResult


@dataclass(frozen=True)
class CustomerRagAnswer:
    answer: str
    generation_status: str


class CustomerRagAnswerService:
    def __init__(self, provider: QwenProvider) -> None:
        self.provider = provider

    async def generate(self, *, query: str, result: CustomerRagResult) -> CustomerRagAnswer:
        if result.handoff_required or not result.citations:
            return CustomerRagAnswer(answer=result.message, generation_status="not_generated")
        if self.provider._client is None:
            return CustomerRagAnswer(answer="已找到相关知识依据，但当前暂未配置回答模型。请联系人工客服。", generation_status="provider_unavailable")
        evidence = "\n\n".join(f"[引用 {index}] {item.content}" for index, item in enumerate(result.citations, 1))
        prompt = (
            "你是财富管理平台客服助手。只能根据提供的引用回答，不得编造、承诺收益或执行任何交易。"
            "如果引用不足以回答，必须明确说资料不足并建议转人工。使用清晰、谨慎、客户可理解的中文。\n\n"
            f"客户问题：{query}\n\n引用资料：\n{evidence}"
        )
        try:
            response = await self.provider._client.chat.completions.create(
                model=self.provider.settings.qwen_chat_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                return CustomerRagAnswer(answer=result.message, generation_status="empty_response")
            return CustomerRagAnswer(answer=text, generation_status="generated")
        except Exception:
            return CustomerRagAnswer(answer="当前回答服务暂时不可用，已保留检索依据并建议转人工客服。", generation_status="generation_failed")
