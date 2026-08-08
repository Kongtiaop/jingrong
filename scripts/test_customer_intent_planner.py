from app.services.customer_intent import CustomerIntentPlanner

def main():
    planner=CustomerIntentPlanner()
    plan=planner.plan("帮我查最近订单状态、账户风险限制，并解释为什么还在审核中")
    intents={x.intent for x in plan.tasks}
    assert "order_status" in intents and "risk_restriction" in intents
    assert len(plan.tasks) >= 2
    handoff=planner.plan("我想转人工客服")
    assert handoff.requires_handoff and handoff.tasks[0].route == "handoff"
    unknown=planner.plan("帮我处理一下")
    assert unknown.clarification
    print({"multi_intent":sorted(intents),"handoff":True,"clarification":True,"pass":True})
if __name__ == "__main__": main()
