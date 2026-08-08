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
    customer_id = account_id = None
    original_income = None
    flow_ids: list[str] = []
    alert_ids: list[str] = []
    try:
        base = (datetime.now(timezone.utc) + timedelta(days=20)).replace(hour=12, minute=0, second=0, microsecond=0)
        cases = {
            'RW-003': base,
            'RW-004': base + timedelta(days=10),
            'RW-005': base + timedelta(days=20),
            'RW-006': base + timedelta(days=30),
        }
        async with db.engine.begin() as conn:
            customer_id = str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            account_id = str((await conn.execute(text('SELECT id FROM account WHERE user_id=:user_id'), {'user_id': customer_id})).scalar_one())
            original_income = (await conn.execute(text('SELECT annual_income FROM customer_profile WHERE user_id=:user_id'), {'user_id': customer_id})).scalar_one_or_none()
            await conn.execute(text('UPDATE customer_profile SET annual_income=:income WHERE user_id=:user_id'), {'income': Decimal('30000.00'), 'user_id': customer_id})

        def flow(case: str, amount: str, flow_type: str, direction: str, occurred_at: datetime, counterparty: str | None = None):
            row = MoneyFlow(id=str(uuid.uuid4()), customer_id=customer_id, account_id=account_id, order_id=None, amount=Decimal(amount), currency='CNY', flow_type=flow_type, direction=direction, counterparty_id=counterparty, channel=f'aml-{case.lower()}-isolated', occurred_at=occurred_at)
            flow_ids.append(row.id)
            return row

        async with db.session_factory() as session:
            # RW-003: isolated 24-hour equal inbound/outbound pair.
            rw3 = cases['RW-003']
            session.add(flow('RW-003', '100000.00', 'transfer', 'in', rw3 - timedelta(hours=20), 'rw3-source'))
            session.add(flow('RW-003', '100000.00', 'transfer', 'out', rw3 - timedelta(hours=4), 'rw3-destination'))

            # RW-004: five independent sources and one concentrated 250k outbound.
            rw4 = cases['RW-004']
            for index in range(5):
                session.add(flow('RW-004', '50000.00', 'transfer', 'in', rw4 - timedelta(days=3, hours=index), f'rw4-source-{index}'))
            session.add(flow('RW-004', '250000.00', 'transfer', 'out', rw4 - timedelta(days=1), 'rw4-concentrated'))

            # RW-005: a 120k inbound plus five recent destinations; keep total under RW-004's 200k threshold.
            rw5 = cases['RW-005']
            session.add(flow('RW-005', '120000.00', 'transfer', 'in', rw5 - timedelta(days=4), 'rw5-source'))
            for index in range(5):
                session.add(flow('RW-005', '10000.00', 'transfer', 'out', rw5 - timedelta(days=1, minutes=index), f'rw5-destination-{index}'))

            # RW-006: final-day cash flow above both 100k and 3x declared income.
            rw6 = cases['RW-006']
            session.add(flow('RW-006', '100000.00', 'cash', 'out', rw6 - timedelta(hours=3)))
            await session.commit()

            service = RiskAlertService(create_postgres_unit_of_work())
            created_by_case: dict[str, list[str]] = {}
            evidence: dict[str, dict] = {}
            for code, as_of in cases.items():
                first = await service.scan_customer_window(session, customer_id, as_of=as_of, days=7)
                await session.commit()
                matching = [alert for alert in first if alert.rule_code == code]
                assert len(matching) == 1, {'case': code, 'all_rules': [alert.rule_code for alert in first]}
                alert_ids.extend(alert.id for alert in first)
                created_by_case[code] = [alert.rule_code for alert in first]

                second = await service.scan_customer_window(session, customer_id, as_of=as_of, days=7)
                await session.commit()
                assert second == [], {'case': code, 'repeat_rules': [alert.rule_code for alert in second]}

            rows = (await session.execute(text('SELECT rule_code, evidence_json FROM risk_alert WHERE id = ANY(:ids)'), {'ids': alert_ids})).mappings().all()
            for row in rows:
                if row['rule_code'] in cases:
                    evidence[row['rule_code']] = json.loads(row['evidence_json'])
            for code in cases:
                assert evidence[code]['scan_type'] == 'customer_window'
                assert evidence[code]['dedupe_key']
                assert evidence[code]['window_start']
                assert evidence[code]['window_end']
            assert evidence['RW-003']['window_hours'] == 24
            assert evidence['RW-004']['source_account_count'] >= 5
            assert evidence['RW-005']['destination_account_count'] >= 5
            assert evidence['RW-006']['income_multiple'] == '3.33'
            print(json.dumps({'created_by_case': created_by_case, 'evidence_keys': {code: sorted(evidence[code].keys()) for code in cases}, 'pass': True}, ensure_ascii=False, indent=2))
    finally:
        async with db.engine.begin() as conn:
            if alert_ids:
                await conn.execute(text('DELETE FROM risk_alert WHERE id = ANY(:ids)'), {'ids': alert_ids})
            if flow_ids:
                await conn.execute(text('DELETE FROM money_flow WHERE id = ANY(:ids)'), {'ids': flow_ids})
            if customer_id:
                await conn.execute(text('UPDATE customer_profile SET annual_income=:income WHERE user_id=:user_id'), {'income': original_income, 'user_id': customer_id})
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())
