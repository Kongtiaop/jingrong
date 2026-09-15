# -*- coding: utf-8 -*-
"""制度类 task 不得对客户个人事实做断言。

ISSUE-20260914-RULE-TASK-ASSERTS-PERSONAL-FACT

问题：`policy` / `risk_public` 这类 task 检索的是**制度文本**，它**无从知道客户个人状态**，
但它的 query 是用户原问句（如"我有没有风险限制"），模型因此顺着问句直接断言
"您有/您没有风险限制"——而账户实际状态由另一个 task（查库）决定。
实测症状（C4 客户）：
```
[graph] 您的账户当前未显示风险限制，……          <- 事实（另一 task 查库所得）
        是的，您有风险限制。                    <- 制度 task 顺着问句断言个人事实  ✗ 矛盾
```
且它举 C2/C4 的假设例子却不提客户真实等级（它确实不知道，也不该由它判断）。

**为何不靠改写检索 query 解决**（这是本修复的关键判断）：
实测把"我有没有风险限制"改成"风险限制的制度规定"，best_sim 从 **0.5820 掉到 0.5242**
（跌破 sufficient），检索质量反而下降。
故只在**生成提示**里加角色约束，**检索串保持不变**。
"""
import asyncio

from app.services.customer_rag_answer import CustomerRagAnswerService, _RULE_INTENTS
from app.services.customer_rag_service import CustomerRagResult
from app.services.knowledge_retrieval import KnowledgeCitation


class _FakeProvider:
    _client = object()

    class settings:
        qwen_chat_model = "qwen-flash"


def _rag(intent: str) -> CustomerRagResult:
    return CustomerRagResult(
        intent=intent, answer_mode="rag_answer", evidence_status="sufficient",
        citations=[KnowledgeCitation(chunk_id="c", document_id="d", version_id="v",
                                     content="制度/公司文本", score=1.0,
                                     title_path="", page_number=None, metadata={})],
        handoff_required=False, message="m")


def _prompt_for(intent: str, query: str = "我有没有风险限制") -> str:
    """捕获 generate() 实际发给模型的提示词。"""
    import types

    import app.services.customer_rag_answer as M

    captured: list[str] = []

    async def fake_call(provider, *, model, messages, temperature=0.0):
        captured.append(messages[0]["content"])
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="答案"))])

    orig = M._call_chat
    M._call_chat = fake_call
    try:
        asyncio.run(CustomerRagAnswerService(_FakeProvider()).generate(
            query=query, result=_rag(intent)))
    finally:
        M._call_chat = orig
    return captured[0]


def test_rule_intents_get_role_constraint():
    """制度类意图必须带角色约束，明确禁止对客户个人事实做断言。"""
    for intent in ("policy", "risk_public"):
        p = _prompt_for(intent)
        assert "角色约束" in p, f"{intent} 缺少角色约束"
        assert "绝不要" in p and "您有/您没有" in p, \
            f"{intent} 应明确禁止“您有/您没有”式个人事实断言"
        assert "假设例子" in p, "应禁止用假设例子（若您是 C2…）替代结论"


def test_non_rule_intents_have_no_role_constraint():
    """客观事实类意图不应被加上该约束（避免无谓改变其行为）。"""
    for intent in ("company_info", "company_business", "faq"):
        p = _prompt_for(intent, query="你们公司叫什么名字")
        assert "角色约束" not in p, f"{intent} 不应带制度角色约束"


def test_role_constraint_does_not_rewrite_original_query():
    """**原问句必须保留** —— 角色约束只影响生成，不改写检索串。

    实测改写检索 query（"我有没有风险限制"->"风险限制的制度规定"）会使
    best_sim 0.5820 -> 0.5242，故绝不能走改写路线。
    """
    p = _prompt_for("risk_public", query="我有没有风险限制")
    assert "客户问题：我有没有风险限制" in p, \
        "生成提示应保留用户原问句（改写会降低检索质量）"


def test_rule_intents_constant_is_minimal():
    """约束范围须精确：只含制度类，避免波及客观事实类意图。"""
    assert set(_RULE_INTENTS) == {"policy", "risk_public"}, \
        f"范围被改动，实际 {sorted(_RULE_INTENTS)}"
