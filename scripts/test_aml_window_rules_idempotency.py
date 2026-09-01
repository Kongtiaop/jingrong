from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database
from app.models.trading import MoneyFlow
from app.repositories.unit_of_work import create_postgres_unit_of_work
from app.services.risk_alert_service import RiskAlertService


async def main() -> None:
    db = Database(get_settings())
    uow = create_postgres_unit_of_work()
    customer_id = None
    account_id = None
    flow_ids: list[str] = []
    alert_ids: list[str] = []
    try:
        async with db.engine.begin() as conn:
            customer_id = str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            account_id = str((await conn.execute(text('SELECT id FROM account WHERE user_id=:user_id'), {'user_id': customer_id})).scalar_one())

        as_of = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
        day_start = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
        service = RiskAlertService(uow)
        async with db.session_factory() as session:
            cash_flow = MoneyFlow(
                id=str(uuid.uuid4()), customer_id=customer_id, account_id=account_id, order_id=None,
                amount=Decimal('200000.00'), currency='CNY', flow_type='cash', direction='out',
                channel='aml_window_test', occurred_at=as_of - timedelta(hours=1),
            )
            session.add(cash_flow)
            flow_ids.append(cash_flow.id)
            for index in range(20):
                flow = MoneyFlow(
                    id=str(uuid.uuid4()), customer_id=customer_id, account_id=account_id, order_id=None,
                    amount=Decimal('5000.00'), currency='CNY', flow_type='transfer', direction='out',
                    counterparty_id=f'aml-test-counterparty-{index}', channel='aml_window_test',
                    occurred_at=as_of - timedelta(hours=2, minutes=index),
                )
                session.add(flow)
                flow_ids.append(flow.id)
            await session.commit()

            first = await service.scan_customer_window(session, customer_id, as_of=as_of, days=7)
            await session.commit()
            alert_ids.extend(alert.id for alert in first)
            first_rules = {alert.rule_code for alert in first}
            assert {'RW-001', 'RW-002'} <= first_rules, first_rules

            second = await service.scan_customer_window(session, customer_id, as_of=as_of, days=7)
            await session.commit()
            assert second == [], [alert.rule_code for alert in second]

            next_as_of = as_of + timedelta(days=1)
            next_window = await service.scan_customer_window(session, customer_id, as_of=next_as_of, days=7)
            await session.commit()
            alert_ids.extend(alert.id for alert in next_window)
            next_rules = {alert.rule_code for alert in next_window}
            assert 'RW-002' in next_rules, next_rules
            assert 'RW-001' not in next_rules, next_rules

            third_as_of = as_of + timedelta(days=2)
            next_cash = MoneyFlow(
                id=str(uuid.uuid4()), customer_id=customer_id, account_id=account_id, order_id=None,
                amount=Decimal('200000.00'), currency='CNY', flow_type='cash', direction='out',
                channel='aml_window_test', occurred_at=third_as_of.replace(hour=12, minute=0),
            )
            session.add(next_cash)
            flow_ids.append(next_cash.id)
            await session.commit()
            third_window = await service.scan_customer_window(session, customer_id, as_of=third_as_of, days=7)
            await session.commit()
            alert_ids.extend(alert.id for alert in third_window)
            third_rules = {alert.rule_code for alert in third_window}
            assert 'RW-001' in third_rules, third_rules

            third_repeat = await service.scan_customer_window(session, customer_id, as_of=third_as_of, days=7)
            await session.commit()
            assert third_repeat == [], [alert.rule_code for alert in third_repeat]

            rows = (await session.execute(text("SELECT rule_code, evidence_json FROM risk_alert WHERE id = ANY(:ids) ORDER BY created_at"), {'ids': alert_ids})).mappings().all()
            evidence = [json.loads(row['evidence_json']) for row in rows]
            assert all(item['scan_type'] == 'customer_window' and item['dedupe_key'] for item in evidence)
            print(json.dumps({
                'first_rules': sorted(first_rules),
                'same_window_created': len(second),
                'next_window_rules': sorted(next_rules),
                'third_window_rules': sorted(third_rules),
                'third_repeat_created': len(third_repeat),
                'alert_count': len(alert_ids),
                'pass': True,
            }, ensure_ascii=False, indent=2))
    finally:
        async with db.engine.begin() as conn:
            if alert_ids:
                await conn.execute(text('DELETE FROM risk_alert WHERE id = ANY(:ids)'), {'ids': alert_ids})
            if flow_ids:
                await conn.execute(text('DELETE FROM money_flow WHERE id = ANY(:ids)'), {'ids': flow_ids})
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())
