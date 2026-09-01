from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database
from app.models.trading import MoneyFlow, Order
from app.repositories.unit_of_work import create_postgres_unit_of_work
from app.services.risk_alert_service import RiskAlertService


async def main() -> None:
    db = Database(get_settings())
    uow = create_postgres_unit_of_work()
    customer_id = None
    account_id = None
    order_id = None
    flow_id = None
    alert_ids: list[str] = []
    try:
        async with db.engine.begin() as conn:
            customer_id = str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            account_id = str((await conn.execute(text('SELECT id FROM account WHERE user_id=:user_id'), {'user_id': customer_id})).scalar_one())
            product_id = str((await conn.execute(text("SELECT id FROM product WHERE status='active' ORDER BY minimum_amount LIMIT 1"))).scalar_one())

        now = datetime.now(timezone.utc)
        async with db.session_factory() as session:
            order = Order(
                id=str(uuid.uuid4()), order_no=f'AMLRT{uuid.uuid4().hex[:16].upper()}', user_id=customer_id,
                account_id=account_id, product_id=product_id, amount=Decimal('200000.00'),
                order_source='customer_direct', idempotency_key=f'aml-rt-{uuid.uuid4()}', status='executed', created_at=now,
            )
            session.add(order)
            await session.flush()
            order_id = order.id
            flow = MoneyFlow(
                id=str(uuid.uuid4()), customer_id=customer_id, account_id=account_id, order_id=order.id,
                amount=Decimal('200000.00'), currency='CNY', flow_type='cash', direction='out',
                channel='aml_realtime_test', occurred_at=now,
            )
            session.add(flow)
            flow_id = flow.id
            await session.commit()

            service = RiskAlertService(uow)
            first = await service.evaluate_order(session, order)
            await session.commit()
            second = await service.evaluate_order(session, order)
            await session.commit()

            alerts = (await session.execute(text('SELECT id, rule_code, evidence_json FROM risk_alert WHERE order_id=:order_id'), {'order_id': order_id})).mappings().all()
            alert_ids.extend(str(row['id']) for row in alerts)
            assert [row['rule_code'] for row in alerts].count('RW-001') == 1, alerts
            evidence = json.loads(next(row['evidence_json'] for row in alerts if row['rule_code'] == 'RW-001'))
            assert evidence['scan_type'] == 'real_time_order'
            assert evidence['dedupe_key'] == f'real_time_order:{order_id}:RW-001'
            assert len(first) == 1
            assert len(second) == 0
            print(json.dumps({
                'first_evaluated_alerts': len(first),
                'second_evaluated_alerts': len(second),
                'persisted_rw001_alerts': [row['rule_code'] for row in alerts].count('RW-001'),
                'dedupe_key': evidence['dedupe_key'],
                'pass': True,
            }, ensure_ascii=False, indent=2))
    finally:
        async with db.engine.begin() as conn:
            if alert_ids:
                await conn.execute(text('DELETE FROM risk_alert WHERE id = ANY(:ids)'), {'ids': alert_ids})
            if flow_id:
                await conn.execute(text('DELETE FROM money_flow WHERE id=:id'), {'id': flow_id})
            if order_id:
                await conn.execute(text('DELETE FROM order_status_history WHERE order_id=:id'), {'id': order_id})
                await conn.execute(text('DELETE FROM orders WHERE id=:id'), {'id': order_id})
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())
