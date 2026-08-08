from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database

BASE_URL = 'http://127.0.0.1:8010/api/v1'


async def login(client: httpx.AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post('/auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return {'Authorization': f"Bearer {response.json()['data']['access_token']}"}


async def main() -> None:
    db = Database(get_settings())
    alert_id: str | None = None
    account_id: str | None = None
    try:
        async with db.engine.begin() as conn:
            customer_id = str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            account_id = str((await conn.execute(text('SELECT id FROM account WHERE user_id=:user_id'), {'user_id': customer_id})).scalar_one())

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            customer = await login(client, 'customer_demo', 'Demo@2026Customer')
            admin = await login(client, 'super_admin_demo', 'Demo@2026SuperAdmin')

            async with db.engine.begin() as conn:
                alert_id = str(uuid.uuid4())
                await conn.execute(text("""INSERT INTO risk_alert
                    (id, customer_id, order_id, rule_code, alert_level, status, confidence, evidence_json,
                     recommendation, handled_note, assessment_result, assessment_note, action_status,
                     restriction_status, created_at)
                    VALUES (:id, :customer_id, NULL, 'RW-UI-STATUS', 'high', 'acknowledged', 1, '{}',
                            'ui status test', '', 'confirmed_risk', 'test', 'not_started', 'none', now())"""),
                    {'id': alert_id, 'customer_id': customer_id})

            restrict = await client.post(f'/risk-alerts/{alert_id}/action', headers=admin, json={
                'action_code': 'RESTRICT_TRADING', 'note': '客户前台展示回归限制',
            })
            restrict.raise_for_status()

            profile_restricted = await client.get('/profile/me', headers=customer)
            profile_restricted.raise_for_status()
            restricted = profile_restricted.json()['data']['risk_operation']
            assert restricted['status'] == 'restricted'
            assert restricted['restriction_status'] == 'restricted'
            assert restricted['latest_alert_id'] == alert_id
            assert restricted['latest_rule_code'] == 'RW-UI-STATUS'

            managed_restricted = await client.get(f'/profile/staff/customers/{customer_id}', headers=admin)
            managed_restricted.raise_for_status()
            assert managed_restricted.json()['data']['risk_operation']['status'] == 'restricted'

            handled = await client.post(f'/risk-alerts/{alert_id}/handle', headers=admin, json={
                'status': 'resolved', 'note': '风险复核完成，允许解除交易限制',
                'assessment_result': 'cleared_after_review',
                'assessment_note': '测试已完成风险复核。',
                'customer_consulted': True,
                'customer_consultation_note': '测试已完成客户沟通。',
            })
            handled.raise_for_status()

            release = await client.post(f'/risk-alerts/{alert_id}/action', headers=admin, json={
                'action_code': 'RELEASE_RESTRICTION', 'note': '后台客户详情解除限制回归',
            })
            release.raise_for_status()

            profile_monitoring = await client.get('/profile/me', headers=customer)
            profile_monitoring.raise_for_status()
            monitoring = profile_monitoring.json()['data']['risk_operation']
            assert monitoring['status'] == 'monitoring'
            assert monitoring['restriction_status'] == 'none'
            assert monitoring['latest_alert_id'] == alert_id

            print(json.dumps({
                'restricted_status': restricted['status'],
                'restricted_rule': restricted['latest_rule_code'],
                'managed_detail_status': managed_restricted.status_code,
                'after_release_status': monitoring['status'],
                'after_release_restriction_status': monitoring['restriction_status'],
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
