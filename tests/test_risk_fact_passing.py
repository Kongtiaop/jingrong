# -*- coding: utf-8 -*-
"""风险事实传递链路：等级读取 + 答案传递 + 依赖声明。

ISSUE-20260914-RISK-LEVEL-NOT-READ / NO-TASK-ANSWER-PASSING

背景（用户诉求）："我有没有风险限制"的正确答案是
  **先查库拿客户风险等级（事实）→ 再套制度规则 → 得出"你能买什么"**。
而此前：
  1. `_risk_restriction` 只读 `account.risk_restriction_status`（二进制 none/restricted），
     **从不读** `customer_risk_assessment.risk_level`（C1–C5）；
  2. `depends_on` 只做执行顺序门控，**从不传前序 task 的答案**，
     所以制度 task 拿不到等级，只能空谈规则，实测编出"是的，您有风险限制"这一
     与事实（账户未显示限制）矛盾的断言。
本文件固定修复后的三点行为。
"""
from dataclasses import replace

from app.services.customer_intent import CustomerIntentPlanner, IntentTask
from app.services.customer_task_executor import CustomerTaskExecutor, TaskExecutionResult


def test_risk_public_depends_on_risk_restriction():
    """规划器必须让"制度说明"依赖"风险事实"，否则等级传不过去。"""
    plan = CustomerIntentPlanner().plan("我有没有风险限制")
    by_intent = {t.intent: t for t in plan.tasks}
    assert "risk_restriction" in by_intent, f"应规划出风险事实 task，实际 {list(by_intent)}"
    assert "risk_public" in by_intent, f"应规划出制度说明 task，实际 {list(by_intent)}"
    dep = by_intent["risk_public"].depends_on
    assert dep == [by_intent["risk_restriction"].task_id], (
        f"risk_public 应依赖 risk_restriction，实际 depends_on={dep}")


def test_dependency_context_injects_prior_answer():
    """依赖上下文应把前序 task 的答案带进来（这是"用事实套规则"的前提）。"""
    item = IntentTask("t2", "risk_public", "rag_explanation", "我有没有风险限制",
                      depends_on=["t1"])
    results = {
        "t1": TaskExecutionResult("t1", "risk_restriction", "succeeded",
                                  answer="您的账户当前未显示风险限制。您的风险承受能力等级为 **C3**。"),
    }
    ctx = CustomerTaskExecutor._dependency_context(item, results)
    assert "C3" in ctx, f"等级未传入依赖上下文: {ctx!r}"
    assert "risk_restriction" in ctx, "应标明来源 intent，便于模型区分角色"
    assert "不要臆造" in ctx, "应显式禁止臆造，避免再次编出事实断言"


def test_dependency_context_empty_when_no_deps():
    """无依赖时必须返回空串（调用方原样使用 query），避免无谓改动 query。"""
    item = IntentTask("t1", "company_info", "rag_explanation", "公司叫什么")
    assert CustomerTaskExecutor._dependency_context(item, {}) == ""


def test_dependency_context_skips_missing_or_empty():
    """依赖结果缺失/为空时不得注入空块（否则会污染 query）。"""
    item = IntentTask("t2", "risk_public", "rag_explanation", "q", depends_on=["t1", "t9"])
    # t9 不存在；t1 答案为空
    results = {"t1": TaskExecutionResult("t1", "risk_restriction", "succeeded", answer="")}
    assert CustomerTaskExecutor._dependency_context(item, results) == ""


def test_failed_dependency_still_passes_its_answer():
    """依赖 task 失败（如 needs_clarification）但仍有文案时也应传递——
    否则制度 task 会失去"事实不可得"这一关键上下文而自由发挥。"""
    item = IntentTask("t2", "risk_public", "rag_explanation", "q", depends_on=["t1"])
    results = {
        "t1": TaskExecutionResult("t1", "risk_restriction", "needs_clarification",
                                  answer="您的账户当前未显示风险限制。系统尚未查询到您的风险测评结果。"),
    }
    ctx = CustomerTaskExecutor._dependency_context(item, results)
    assert "尚未查询到" in ctx, "失败态的事实说明同样需要传递"


def test_intent_task_is_replaceable():
    """执行器用 dataclasses.replace 注入上下文，IntentTask 必须保持 frozen 且可 replace。"""
    item = IntentTask("t1", "company_info", "rag_explanation", "q")
    new = replace(item, query="q + 上下文")
    assert new.query == "q + 上下文"
    assert item.query == "q", "replace 不应修改原对象"


def test_dependency_context_must_not_enter_retrieval_query():
    """事实上下文必须走独立字段，**绝不能拼进 query**。

    ISSUE-20260914-DEP-CONTEXT-POLLUTES-RETRIEVAL

    `query` 在知识类 task 里是**检索串**（直接传给 search_customer_knowledge）。
    初版把指令块拼进 `item.query`，导致"【前序查询结果（请基于这些事实作答，不要臆造）】"
    这段**指令文本本身被送去知识库检索**，实测 best_sim 从 0.4643 降到 0.3753
    （逼近 reject=0.35），检索质量明显下降。
    本测试固定：事实块只能存在于 `dependency_context` 字段。
    """
    # 1) 字段存在且默认空
    item = IntentTask("t1", "risk_public", "rag_explanation", "我有没有风险限制")
    assert item.dependency_context == "", "默认应为空，避免无依赖时污染生成提示"

    # 2) 执行器应注入到 dependency_context，而不是 query
    import inspect

    from app.services.customer_task_executor import CustomerTaskExecutor as E
    src = inspect.getsource(E._execute_one)
    assert "dependency_context=dep_context" in src, \
        "应把依赖上下文写入 dependency_context 字段"
    assert 'query=f"{item.query}{dep_context}"' not in src, \
        "不得再把依赖上下文拼进 query —— 那会被当作检索串送进知识库"

    # 3) 生成阶段才使用它
    react_src = inspect.getsource(E._bounded_react_knowledge)
    assert "dependency_context" in react_src, \
        "生成阶段应使用 dependency_context（否则事实传了却没用）"
    assert "gen_query" in react_src, "应构造独立的生成用 query，不改检索串"
