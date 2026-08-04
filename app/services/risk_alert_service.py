import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import func, select, text
from app.models.trading import Account, MoneyFlow, Order, OrderStatusHistory, RiskAlert, RiskInvestigation, RiskReportDraft

RULES = {
    "RW-001": {"name": "大额现金交易", "risk_level": "medium", "recommendation": "生成大额交易报告草稿，核实交易目的和资金来源。", "response_deadline": "1_business_day"},
    "RW-002": {"name": "频繁小额交易（蚂蚁搬家）", "risk_level": "high", "recommendation": "分析交易对手方和交易目的，确认可疑后合并提交报告。", "response_deadline": "1_business_day"},
    "RW-003": {"name": "资金快进快出", "risk_level": "high", "recommendation": "核实转入转出对手方关系、交易背景和商业合理性。", "response_deadline": "4_hours"},
    "RW-004": {"name": "分散转入集中转出", "risk_level": "high", "recommendation": "启动深度调查，核实转入来源并升级反洗钱调查。", "response_deadline": "2_hours"},
    "RW-005": {"name": "集中转入分散转出", "risk_level": "high", "recommendation": "分析转出对手方和资金分拆特征，启动深度调查。", "response_deadline": "2_hours"},
    "RW-006": {"name": "交易金额与客户身份严重不符", "risk_level": "high", "recommendation": "要求核实收入来源、资产证明和交易目的。", "response_deadline": "4_hours"},
}
class RiskAlertService:
    def __init__(self, uow): self.uow = uow

    async def evaluate_order(self, session, order):
        now = order.created_at or datetime.now(timezone.utc)
        start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_day = start_day + timedelta(days=1)
        start_7d = now - timedelta(days=7)
        flows = await self.uow.money_flows.list_window(session, order.user_id, start_7d, end_day)
        current = next((f for f in flows if f.order_id == order.id), None)
        if current is None:
            return []
        history = [f for f in flows if f.id != current.id]
        alerts = []
        cash_total = sum((Decimal(str(f.amount)) for f in flows if start_day <= f.occurred_at < end_day and f.flow_type == "cash"), Decimal("0"))
        if current.flow_type == "cash" and Decimal(str(current.amount)) >= Decimal("50000") and cash_total >= Decimal("50000"):
            alerts.append(await self._save_alert(session, order, "RW-001", {"cash_daily_amount": str(cash_total), "threshold": "50000", "flow_type": "cash"}))
        small = [f for f in history if f.occurred_at >= start_7d and f.direction == "out" and f.flow_type == "transfer" and Decimal(str(f.amount)) < Decimal("200000")]
        if current.flow_type == "transfer" and len(small) >= 19 and sum((Decimal(str(f.amount)) for f in small), Decimal("0")) + Decimal(str(current.amount)) >= Decimal("100000"):
            alerts.append(await self._save_alert(session, order, "RW-002", {"window_days": 7, "trade_count": len(small)+1, "amount": str(sum((Decimal(str(f.amount)) for f in small), Decimal("0")) + Decimal(str(current.amount))) }))
        inbound_24h = [f for f in history if f.direction == "in" and now - timedelta(hours=24) <= f.occurred_at <= now]
        in_total = sum((Decimal(str(f.amount)) for f in inbound_24h), Decimal("0"))
        current_out = Decimal(str(current.amount)) if current.direction == "out" else Decimal("0")
        if in_total > 0 and current_out >= Decimal("50000") and current_out >= in_total * Decimal("0.90") and current_out <= in_total * Decimal("1.10"):
            alerts.append(await self._save_alert(session, order, "RW-003", {"window_hours": 24, "inbound_amount": str(in_total), "outbound_amount": str(current_out), "outbound_ratio": str((current_out / in_total).quantize(Decimal("0.0001")))}))
        inbound_5d = [f for f in history if f.direction == "in" and f.occurred_at >= now - timedelta(days=5)]
        outbound_5d = [f for f in history if f.direction == "out" and f.occurred_at >= now - timedelta(days=5)] + ([current] if current.direction == "out" else [])
        sources = len({f.counterparty_id for f in inbound_5d if f.counterparty_id})
        out_total = sum((Decimal(str(f.amount)) for f in outbound_5d), Decimal("0"))
        by_cp = {}
        for f in outbound_5d:
            if f.counterparty_id:
                by_cp[f.counterparty_id] = by_cp.get(f.counterparty_id, Decimal("0")) + Decimal(str(f.amount))
        concentration = max(by_cp.values(), default=Decimal("0")) / out_total if out_total else Decimal("0")
        if sources >= 5 and out_total >= Decimal("200000") and concentration >= Decimal("0.80"):
            alerts.append(await self._save_alert(session, order, "RW-004", {"window_days": 5, "source_account_count": sources, "outbound_amount": str(out_total), "concentration": str(concentration.quantize(Decimal("0.0001")))}))
        large_in = [f for f in history if f.direction == "in" and f.occurred_at >= now - timedelta(days=5) and Decimal(str(f.amount)) >= Decimal("100000")]
        outs_3d = [f for f in history if f.direction == "out" and f.occurred_at >= now - timedelta(days=3) and Decimal(str(f.amount)) < Decimal("200000")] + ([current] if current.direction == "out" and Decimal(str(current.amount)) < Decimal("200000") else [])
        destinations = len({f.counterparty_id for f in outs_3d if f.counterparty_id})
        if large_in and destinations >= 5:
            alerts.append(await self._save_alert(session, order, "RW-005", {"large_inbound_amount": str(max(Decimal(str(f.amount)) for f in large_in)), "destination_account_count": destinations}))
        profile = await self.uow.profiles.get(session, order.user_id)
        income = getattr(profile, "annual_income", None)
        if income is not None:
            income = Decimal(str(income)); day_total = sum((Decimal(str(f.amount)) for f in flows if f.id == current.id and start_day <= f.occurred_at < end_day), Decimal("0"))
            if income > 0 and day_total >= Decimal("100000") and day_total >= income * Decimal("3"):
                alerts.append(await self._save_alert(session, order, "RW-006", {"daily_amount": str(day_total), "annual_income": str(income), "income_multiple": str((day_total / income).quantize(Decimal("0.01")))}))
        return alerts
    async def scan_customer_window(self, session, customer_id, as_of=None, days=5):
        as_of = as_of or datetime.now(timezone.utc)
        start = as_of - timedelta(days=days)
        end = as_of + timedelta(microseconds=1)
        flows = await self.uow.money_flows.list_window(session, customer_id, start, end)
        if not flows:
            return []
        inbound = [f for f in flows if f.direction == "in" and f.flow_type == "transfer"]
        outbound = [f for f in flows if f.direction == "out" and f.flow_type == "transfer"]
        created = []
        def total(items): return sum((Decimal(str(f.amount)) for f in items), Decimal("0"))
        by_cp = {}
        for f in outbound:
            if f.counterparty_id: by_cp[f.counterparty_id] = by_cp.get(f.counterparty_id, Decimal("0")) + Decimal(str(f.amount))
        out_total = total(outbound)
        concentration = max(by_cp.values(), default=Decimal("0")) / out_total if out_total else Decimal("0")
        source_count = len({f.counterparty_id for f in inbound if f.counterparty_id})
        if source_count >= 5 and out_total >= Decimal("200000") and concentration >= Decimal("0.80"):
            alert = await self._save_window_alert(session, customer_id, "RW-004", {"window_days": days, "source_account_count": source_count, "outbound_amount": str(out_total), "concentration": str(concentration.quantize(Decimal("0.0001")))})
            if alert: created.append(alert)
        large_in = [f for f in inbound if Decimal(str(f.amount)) >= Decimal("100000")]
        recent_out = [f for f in outbound if f.occurred_at >= as_of - timedelta(days=3) and Decimal(str(f.amount)) < Decimal("200000")]
        destinations = len({f.counterparty_id for f in recent_out if f.counterparty_id})
        if large_in and destinations >= 5:
            alert = await self._save_window_alert(session, customer_id, "RW-005", {"window_days_inbound": days, "window_days_outbound": 3, "large_inbound_amount": str(max(Decimal(str(f.amount)) for f in large_in)), "destination_account_count": destinations})
            if alert: created.append(alert)
        return created

    async def _save_window_alert(self, session, customer_id, rule_code, evidence):
        existing = (await session.execute(select(RiskAlert).where(RiskAlert.customer_id == customer_id, RiskAlert.rule_code == rule_code, RiskAlert.status.in_(["open", "acknowledged"]), RiskAlert.created_at >= datetime.now(timezone.utc) - timedelta(days=1)).limit(1))).scalar_one_or_none()
        if existing:
            return None
        rule = RULES[rule_code]
        alert = RiskAlert(id=str(uuid4()), customer_id=customer_id, order_id=None, rule_code=rule_code, alert_level=rule["risk_level"], status="open", confidence=1, evidence_json=json.dumps({**evidence, "rule_name": rule["name"], "response_deadline": rule["response_deadline"], "scan_type": "customer_window"}, ensure_ascii=False), recommendation=rule["recommendation"])
        await self.uow.risk_alerts.save(session, alert)
        return alert
    async def _save_alert(self, session, order, rule_code, evidence):
        rule=RULES[rule_code]
        alert=RiskAlert(id=str(uuid4()), customer_id=order.user_id, order_id=order.id, rule_code=rule_code, alert_level=rule["risk_level"], status="open", confidence=1, evidence_json=json.dumps({**evidence, "rule_name": rule["name"], "response_deadline": rule["response_deadline"]}, ensure_ascii=False), recommendation=rule["recommendation"])
        await self.uow.risk_alerts.save(session, alert)
        return alert

    async def apply_action(self, session, alert, actor, action_code, note=""):
        now = datetime.now(timezone.utc)
        order = await self.uow.orders.get_for_update(session, alert.order_id) if alert.order_id else None
        account = await self.uow.accounts.get_by_id(session, order.account_id, for_update=True) if order else None
        # Customer-window AML alerts may not be associated with a single order.
        # Restriction actions still operate on the customer's trading account.
        if account is None and action_code in {"RESTRICT_TRADING", "RELEASE_RESTRICTION"}:
            account = await self.uow.accounts.get_by_user(session, alert.customer_id, for_update=True)
        if action_code in {"HOLD_ORDER", "REJECT_ORDER"} and order is None:
            raise ValueError("risk action requires an order")
        if action_code == "HOLD_ORDER":
            if order.status not in {"pending_review", "pending_confirmation", "executing"}:
                raise ValueError("order cannot be held in current state")
            previous_status = order.status
            order.status = "risk_hold"
            order.review_note = note or "risk alert hold"
            await self.uow.status_history.save(session, OrderStatusHistory(
                id=str(uuid4()), order_id=order.id, from_status=previous_status, to_status="risk_hold",
                operator_user_id=actor.id, note=note or "risk alert hold",
            ))
            if previous_status in {"pending_confirmation", "executing"} and account and account.status == "active":
                account.status = "risk_hold"
        elif action_code == "REJECT_ORDER":
            if order.status not in {"pending_review", "risk_hold"}:
                raise ValueError("order cannot be rejected in current state")
            previous_status = order.status
            # Customer-direct orders reserve funds at confirmation. A risk hold changes
            # only the order status, so rejection must return the same frozen amount.
            if account and order.order_source == "customer_direct":
                if account.frozen_balance < order.amount:
                    raise ValueError("order frozen balance is inconsistent")
                account.available_balance += order.amount
                account.frozen_balance -= order.amount
            order.status = "rejected"
            order.failure_reason = note or "rejected by risk control"
            await self.uow.status_history.save(session, OrderStatusHistory(
                id=str(uuid4()), order_id=order.id, from_status=previous_status, to_status="rejected",
                operator_user_id=actor.id, note=note or "rejected by risk control",
            ))
        elif action_code == "RESTRICT_TRADING":
            if account is None:
                raise ValueError("risk action requires an account")
            account.status = "risk_restricted"
            account.risk_restriction_status = "restricted"
            account.risk_restriction_reason = note or alert.rule_code
            account.risk_restricted_until = None
            alert.restriction_status = "restricted"
        elif action_code == "RELEASE_RESTRICTION":
            if account is None:
                raise ValueError("risk action requires an account")
            account.status = "active"
            account.risk_restriction_status = "none"
            account.risk_restriction_reason = ""
            account.risk_restricted_until = None
            alert.restriction_status = "none"
        elif action_code == "CREATE_INVESTIGATION":
            investigation = RiskInvestigation(id=str(uuid4()), alert_id=alert.id, customer_id=alert.customer_id, assigned_to=actor.id, status="open")
            session.add(investigation)
            await session.flush()
            alert.investigation_id = investigation.id
        elif action_code == "CREATE_REPORT_DRAFT":
            report = RiskReportDraft(id=str(uuid4()), alert_id=alert.id, customer_id=alert.customer_id, report_type="large_transaction" if alert.rule_code == "RW-001" else "suspicious_transaction", payload_json=alert.evidence_json, created_by=actor.id)
            session.add(report)
            await session.flush()
            alert.report_draft_id = report.id
        elif action_code in {"REQUEST_MATERIALS", "ESCALATE"}:
            pass
        else:
            raise ValueError("unsupported risk action")
        alert.action_code = action_code
        alert.action_status = "completed"
        alert.action_note = note
        alert.handled_by = actor.id
        alert.handled_note = note
        alert.handled_at = now
        return alert































