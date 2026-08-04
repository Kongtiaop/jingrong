from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database


async def login(client, username, password):
    response = await client.post('/auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return {'Authorization': f"Bearer {response.json()['data']['access_token']}"}


async def main():
    db = Database(get_settings())
    alert_id = None
    account_id = None
    try:
        async with db.engine.begin() as conn:
            user_id = str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            account_id = str((await conn.execute(text('SELECT id FROM account WHERE user_id=:user_id'), {'user_id': user_id})).scalar_one())

        async with httpx.AsyncClient(base_url='http://127.0.0.1:8010/api/v1', timeout=30) as client:
            customer = await login(client, 'customer_demo', 'Demo@2026Customer')
            admin = await login(client, 'super_admin_demo', 'Demo@2026SuperAdmin')

            initial = await client.get('/profile/me', headers=customer)
            initial.raise_for_status()
            assert initial.json()['data']['risk_operation']['status'] == 'normal'

            async with db.engine.begin() as conn:
                alert_id = str(uuid.uuid4())
                await conn.execute(text("""INSERT INTO risk_alert
                    (id, customer_id, order_id, rule_code, alert_level, status, confidence, evidence_json,
                     recommendation, handled_note, assessment_result, assessment_note, action_status,
                     restriction_status, created_at)
                    VALUES (:id, :customer_id, NULL, 'RW-STATUS', 'high', 'acknowledged', 1, '{}',
                            'test', '', 'confirmed_risk', 'test', 'not_started', 'none', now())"""),
                    {'id': alert_id, 'customer_id': user_id})

            monitoring = await client.get('/profile/me', headers=customer)
            monitoring.raise_for_status()
            monitoring_data = monitoring.json()['data']['risk_operation']
            assert monitoring_data['status'] == 'monitoring'
            assert monitoring_data['latest_rule_code'] == 'RW-STATUS'

            restricted = await client.post(f'/risk-alerts/{alert_id}/action', headers=admin, json={
                'action_code': 'RESTRICT_TRADING', 'note': '状态测试限制',
            })
            restricted.raise_for_status()

            restricted_me = await client.get('/profile/me', headers=customer)
            restricted_me.raise_for_status()
            restricted_data = restricted_me.json()['data']['risk_operation']
            assert restricted_data['status'] == 'restricted'
            assert restricted_data['restriction_status'] == 'restricted'

            customers = await client.get('/profile/staff/customers', params={'q': 'customer_demo', 'limit': 10}, headers=admin)
            customers.raise_for_status()
            row = customers.json()['data']['items'][0]
            assert row['risk_operation_status'] == 'restricted'
            assert row['latest_risk_rule_code'] == 'RW-STATUS'

            released = await client.post(f'/risk-alerts/{alert_id}/action', headers=admin, json={
                'action_code': 'RELEASE_RESTRICTION', 'note': '状态测试解除',
            })
            released.raise_for_status()

            after_release = await client.get('/profile/me', headers=customer)
            after_release.raise_for_status()
            after_release_data = after_release.json()['data']['risk_operation']
            assert after_release_data['status'] == 'monitoring'

            handled = await client.post(f'/risk-alerts/{alert_id}/handle', headers=admin, json={
                'status': 'resolved',
                'note': '风险状态测试结案',
                'assessment_result': 'cleared_after_review',
                'assessment_note': '复核后未发现持续风险',
                'customer_consulted': True,
                'customer_consultation_note': '已完成客户沟通',
            })
            handled.raise_for_status()

            closed = await client.get('/profile/me', headers=customer)
            closed.raise_for_status()
            closed_data = closed.json()['data']['risk_operation']
            assert closed_data['status'] == 'normal'

            # Staff customer detail is advisor-scoped and requires a separate active authorization fixture.
            # This test verifies the all-customer management list plus the customer's own profile endpoint.

            customers_after = await client.get('/profile/staff/customers', params={'q': 'customer_demo', 'limit': 10}, headers=admin)
            customers_after.raise_for_status()
            assert customers_after.json()['data']['items'][0]['risk_operation_status'] == 'normal'

            print(json.dumps({
                'initial': 'normal',
                'after_alert': monitoring_data['status'],
                'after_restrict': restricted_data['status'],
                'list_status': row['risk_operation_status'],
                'after_release': after_release_data['status'],
                'after_resolved': closed_data['status'],
                'pass': True,
            }, ensure_ascii=False, indent=2))
    finally:
        async with db.engine.begin() as conn:
            if alert_id:
                await conn.execute(text('DELETE FROM risk_alert WHERE id=:id'), {'id': alert_id})
            if account_id:
                await conn.execute(text("UPDATE account SET status='active', risk_restriction_status='none', risk_restriction_reason='', risk_restricted_until=NULL WHERE id=:id"), {'id': account_id})
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())
