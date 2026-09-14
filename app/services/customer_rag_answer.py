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

    判定标准经过一次实测修正（ISSUE-20260914-JUDGE-MISJUDGES-RELEVANT-EVIDENCE）：
    原提示词要求"引用**足以回答**"才 yes，但 `query` 传进来的是**检索用的改写文本**
    （可能已被模型收窄成某个子问题），而引用是按用户原始意图召回的。实测：
      - query='客户存在待处理风险记录时，是否影响交易权限？...' + 引用讲"风险等级匹配/C4可买R5"
        → judge 严格按字面判 no，**误杀**了本可用于回答"我有没有风险限制"的依据；
      - 同样内容的引用换成另一次改写 → judge 判 yes。**同一批引用判定不一致**。
    故放宽为"引用是否包含与问题相关的制度依据"，避免因改写措辞差异而误杀。
    """
    async def judge(query: str, contexts: list[str]) -> bool:
        if provider._client is None:
            return False
        evidence = "\n\n".join(f"[引用 {index}] {content}" for index, content in enumerate(contexts, 1))
        prompt = (
            "你是财富管理客服助手。以下检索引用来自公司制度/客服知识库。\n"
            "请判断：**这些引用是否包含与用户问题相关的依据**（可用于组织回答该问题的素材）。\n\n"
            "判定要点：\n"
            "- 只要引用涉及该问题所属的制度、规则、流程、产品要素等，即回答 yes；\n"
            "- 检索问题可能只是用户疑虑的某一个侧面，只要引用对理解该主题有帮助就算相关；\n"
            "- 只有当引用与该问题**完全无关**（如问风险限制却召回理财产品收益率）时才回答 no；\n"
            "- 宁可从宽：信息不完整但方向相关 → yes。\n\n"
            f"检索问题：{query}\n\n检索引用：\n{evidence}\n\n只回答 yes 或 no。"
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
