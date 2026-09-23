"""转人工的「建议 + 确认」契约。

背景（2026-09-23 实机）：用户说「转人工」，系统回
「可以为您转接人工客服。下一步：是否现在转接人工客服？」，用户答「是的」，
却得到「您想查询哪一类信息，例如订单、工单、资产、风险限制或公司业务？」——
既没转成人工，还顺带把澄清计数推高（答第二次"是"时升级成
clarification_exhausted，回一句"为避免反复追问，建议转接人工客服"）。

根因有两层，这里分别锁住：
1. 任务层：`human_handoff` 用 clarification 反问一句**系统自己处理不了**的话
   （两步式的第二步从未实现）。
2. 图编排层：建单判据是个兜底分支，会静默建单绕过确认（见 test_agent_graph.py）。
"""

import asyncio

from app.services.customer_intent import IntentPlan, IntentTask
from app.services.customer_task_executor import CustomerTaskExecutor


def _handoff_plan() -> IntentPlan:
    return IntentPlan(
        normalized_query="转人工",
        tasks=[IntentTask("t1", "human_handoff", "handoff", "转人工")],
        requires_handoff=True)


def _run_handoff():
    # human_handoff 分支不触库，故 database 传 None 即可（也顺带证明它确实不碰库）
    executor = CustomerTaskExecutor(database=None)
    return asyncio.run(executor.execute(customer_id="c1", plan=_handoff_plan()))


def test_handoff_task_does_not_ask_unanswerable_question():
    """不得反问——确认动作由前端卡片承担，那是可执行的；文本反问不是。"""
    results = _run_handoff()
    assert results[0].intent == "human_handoff"
    assert results[0].status == "succeeded"
    assert not results[0].clarification, "转人工不该产出 clarification"


def test_summarize_handoff_has_no_clarification_and_no_prompt():
    """聚合结果不得带 clarification，也不得出现「下一步：…」。"""
    results = _run_handoff()
    status, clarification, answer = CustomerTaskExecutor.summarize(results, requires_handoff=True)

    assert status == "handoff"
    assert clarification is None
    assert "是否现在转接" not in answer
    assert "下一步" not in answer
    # 答案要指向可执行的入口，否则用户不知道下一步能做什么
    assert "确认" in answer


def test_handoff_answer_does_not_claim_transfer_already_done():
    """未确认前不得声称已转接——那句「已为您转接，工单号：X」只能由确认接口之后出现。"""
    results = _run_handoff()
    _, _, answer = CustomerTaskExecutor.summarize(results, requires_handoff=True)
    assert "已为您转接" not in answer
    assert "工单号" not in answer
