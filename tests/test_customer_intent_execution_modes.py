from __future__ import annotations

from app.services.customer_intent import CustomerIntentPlanner


def test_knowledge_intent_is_planned_as_bounded_react():
    plan = CustomerIntentPlanner().plan("适当性政策是什么")
    assert plan.tasks
    assert any(task.intent == "policy" and task.execution_mode == "bounded_react" for task in plan.tasks)


def test_business_intent_is_planned_as_controlled_workflow():
    plan = CustomerIntentPlanner().plan("我要赎回产品")
    assert plan.tasks
    assert any(task.intent == "business_operation" and task.execution_mode == "controlled_workflow" for task in plan.tasks)
