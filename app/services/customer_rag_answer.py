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
    # 生成的答案是否**自述资料不足**（半成功：拿到了引用，但引用不足以完整回答）
    # 依据 generate() 的提示词要求："如果引用不足以回答，必须明确说资料不足并建议转人工"。
    # 这是 ISSUE-20260914-NO-PARTIAL-STATE 的中间态信号：此前只有 succeeded/failed 两态，
    # 导致"引用了相关制度但答不出具体流程"被标成 succeeded+completed，用户误以为问题已解决。
    insufficient: bool = False


# 制度/规则类意图：检索的是**制度文本**，无从知道客户个人状态。
# 这些 task 的生成需要额外的"角色约束"，避免顺着用户个人问句断言个人事实
# （ISSUE-20260914-RULE-TASK-ASSERTS-PERSONAL-FACT）。
# company_info / company_business / faq 不在此列：它们本就是在陈述客观事实，
# 不存在"把制度说成个人状态"的风险。
_RULE_INTENTS = frozenset({"policy", "risk_public"})


# 模型被要求"引用不足时明确说资料不足"，故用固定表述做结构化识别。
_INSUFFICIENT_MARKERS = (
    "资料不足", "资料中未", "资料未", "引用中未", "引用资料中未", "引用内容中未",
    "未包含", "未提供", "未明确", "未提及", "无法提供准确", "无法准确回答",
    "没有足够依据", "未找到",
)


def answer_reports_insufficient(text: str) -> bool:
    """答案是否自述"资料不足/引用未涵盖"——用于判定半成功。"""
    body = (text or "").strip()
    if not body:
        return False
    return any(marker in body for marker in _INSUFFICIENT_MARKERS)


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
        # 制度类意图的**角色约束**（ISSUE-20260914-RULE-TASK-ASSERTS-PERSONAL-FACT）：
        # 这些 task 检索的是**制度/规则文本**，它**无从知道客户个人的状态**。
        # 但它的 query 是用户原问句（如"我有没有风险限制"），模型因此容易顺着问句
        # 直接断言"您有/您没有风险限制"——而账户实际状态由另一个 task（查库）决定。
        # 实测症状：同一答案里既有"您的账户当前未显示风险限制"又有"是的，您有风险限制"，
        # **自相矛盾**；且它举 C2/C4 的例子却不提客户真实等级（它确实不知道）。
        # 注意：**不能靠改写检索 query 来解决**——实测把"我有没有风险限制"改成
        # "风险限制的制度规定"会让 best_sim 从 0.5820 掉到 0.5242（跌破 sufficient）。
        # 故只在**生成提示**里加角色约束，检索串保持不变。
        role_block = ""
        if result.intent in _RULE_INTENTS:
            role_block = (
                "\n\n[本次回答的角色约束] 你这一步负责的是**制度/规则说明**，不是查询客户个人状态。"
                "引用资料只包含制度规定，**不包含**该客户的具体等级、额度或限制状态。因此：\n"
                "1. 只陈述**制度如何规定**，**绝不要**使用“您有/您没有……”“您的账户……”这类"
                "对客户个人事实的断言；\n"
                "2. 若上文已给出客户的事实（如风险等级），可据此说明该规则对其**意味着什么**"
                "（例如“您当前为 C1，对应可购买 R1 产品”）；若没有，就只讲规则本身，"
                "并说明具体适用需以客户本人评估结果为准；\n"
                "3. **不要**列举与客户无关的假设例子（如“若您是 C2……若您是 C4……”）"
                "来替代结论。"
            )
        prompt = (
            "你是财富管理平台客服助手。只能根据提供的引用回答，不得编造、承诺收益或执行任何交易。"
            "如果引用不足以回答，必须明确说资料不足并建议转人工。使用清晰、谨慎、客户可理解的中文。"
            f"{role_block}\n\n"
            f"客户问题：{query}\n\n引用资料：\n{evidence}{history_block}"
        )
        try:
            response = await _call_chat(
                self.provider,
                model=self.provider.settings.qwen_chat_model,
                messages=[{"role": "user", "content": prompt}],
                # temperature=0：本任务是"**基于引用复述事实**"，不需要创作性随机。
                # 此前为 0.1，实测同一 query 两次生成出现**事实相反**的断言
                # （"我有没有风险限制" 一次答"是的，您有风险限制"、一次答"无法判断"），
                # 属金融场景不可接受的合规风险，也让对照/评测不可复现
                # （ISSUE-20260914-GENERATION-TEMPERATURE-INSTABILITY）。
                temperature=0.0,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                return CustomerRagAnswer(answer=result.message, generation_status="empty_response",
                                         insufficient=True)
            return CustomerRagAnswer(answer=text, generation_status="generated",
                                     insufficient=answer_reports_insufficient(text))
        except Exception:  # noqa: BLE001
            return CustomerRagAnswer(answer="当前回答服务暂时不可用，已保留检索依据并建议转人工客服。", generation_status="generation_failed")
