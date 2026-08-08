from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database
from app.models.trading import MoneyFlow
from app.repositories.unit_of_work import create_postgres_unit_of_work
from app.services.daily_aml_scan_service import DailyAmlScanService
from decimal import Decimal
from uuid import uuid4

BASE_URL = 'http://127.0.0.1:8010/api/v1'


async def login(client, username, password):
    response = await client.post('/auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return {'Authorization': f"Bearer {response.json()['data']['access_token']}"}


async def main():
    db = Database(get_settings())
    batch_id = None
    flow_ids: list[str] = []
    alert_ids: list[str] = []
    try:
        at = (datetime.now(timezone.utc) + timedelta(days=6)).replace(hour=12, minute=0, second=0, microsecond=0)
        async with db.engine.begin() as conn:
            customer_id = str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            account_id = str((await conn.execute(text('SELECT id FROM account WHERE user_id=:user_id'), {'user_id': customer_id})).scalar_one())
        async with db.session_factory() as session:
            cash = MoneyFlow(id=str(uuid4()), customer_id=customer_id, account_id=account_id, order_id=None, amount=Decimal('50000.00'), currency='CNY', flow_type='cash', direction='out', channel='aml_batch_api_test', occurred_at=at-timedelta(hours=1))
            session.add(cash); flow_ids.append(cash.id)
            for index in range(20):
                flow = MoneyFlow(id=str(uuid4()), customer_id=customer_id, account_id=account_id, order_id=None, amount=Decimal('5000.00'), currency='CNY', flow_type='transfer', direction='out', counterparty_id=f'aml-batch-api-{index}', channel='aml_batch_api_test', occurred_at=at-timedelta(hours=2, minutes=index))
                session.add(flow); flow_ids.append(flow.id)
            await session.commit()
            run, created = await DailyAmlScanService(create_postgres_unit_of_work()).run(
                session, as_of=at, days=7, username='customer_demo',
            )
            await session.commit()
            assert created is True
            batch_id = run.id
            alert_ids.extend((await session.execute(text("SELECT id FROM risk_alert WHERE customer_id=:customer_id AND evidence_json LIKE '%\"window_start\"%' AND created_at >= :started_at"), {'customer_id': customer_id, 'started_at': run.started_at})).scalars().all())

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            admin = await login(client, 'super_admin_demo', 'Demo@2026SuperAdmin')
            customer = await login(client, 'customer_demo', 'Demo@2026Customer')

            batches = await client.get('/risk-alerts/batches', params={'status': 'completed', 'limit': 1, 'offset': 0}, headers=admin)
            batches.raise_for_status()
            batch_payload = batches.json()['data']
            rows = batch_payload['items']
            assert batch_payload['total'] >= 1
            assert len(rows) == 1
            assert any(row['id'] == batch_id for row in rows)

            empty_status = await client.get('/risk-alerts/batches', params={'status': 'failed', 'limit': 1, 'offset': 0}, headers=admin)
            empty_status.raise_for_status()
            assert empty_status.json()['data']['items'] == []

            linked = await client.get(f'/risk-alerts/batches/{batch_id}/alerts', headers=admin)
            linked.raise_for_status()
            linked_data = linked.json()['data']
            assert linked_data['batch']['id'] == batch_id
            assert linked_data['total'] == len(linked_data['items'])
            assert {'RW-001', 'RW-002'} <= {item['rule_code'] for item in linked_data['items']}
            assert all(item['evidence'].get('scan_type') == 'customer_window' for item in linked_data['items'])

            forbidden = await client.get('/risk-alerts/batches', headers=customer)
            assert forbidden.status_code == 403

            missing = await client.get('/risk-alerts/batches/not-found/alerts', headers=admin)
            assert missing.status_code == 404
            detail = missing.json()['detail']
            assert detail['code'] == 'AML_BATCH_NOT_FOUND'

            print(json.dumps({
                'batch_list_status': batches.status_code,
                'filtered_total': batch_payload['total'],
                'failed_filter_items': len(empty_status.json()['data']['items']),
                'linked_alerts_status': linked.status_code,
                'linked_alert_total': linked_data['total'],
                'linked_rule_codes': sorted({item['rule_code'] for item in linked_data['items']}),
                'customer_forbidden_status': forbidden.status_code,
                'missing_status': missing.status_code,
                'pass': True,
            }, ensure_ascii=False, indent=2))
    finally:
        async with db.engine.begin() as conn:
            if alert_ids:
                await conn.execute(text('DELETE FROM risk_alert WHERE id = ANY(:ids)'), {'ids': alert_ids})
            if flow_ids:
                await conn.execute(text('DELETE FROM money_flow WHERE id = ANY(:ids)'), {'ids': flow_ids})
            if batch_id:
                await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='aml_batch_run' AND resource_id=:id"), {'id': batch_id})
                await conn.execute(text('DELETE FROM aml_batch_run WHERE id=:id'), {'id': batch_id})
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())
