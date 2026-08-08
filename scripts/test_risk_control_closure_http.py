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


def error_code(response):
    detail = response.json().get('detail') or {}
    return detail.get('code') if isinstance(detail, dict) else None


async def main():
    db = Database(get_settings())
    alert_id = order_id = restricted_order_id = None
    customer_id = product_id = account_id = None
    try:
        async with db.engine.begin() as conn:
            customer_id = str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            product_id = str((await conn.execute(text("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1"))).scalar_one())
            account_id = str((await conn.execute(text('SELECT id FROM account WHERE user_id=:id'), {'id': customer_id})).scalar_one())

        async with httpx.AsyncClient(base_url='http://127.0.0.1:8010/api/v1', timeout=30) as client:
            customer = await login(client, 'customer_demo', 'Demo@2026Customer')
            admin = await login(client, 'super_admin_demo', 'Demo@2026SuperAdmin')

            # Create a pending-review order and an explicit confirmed-risk alert tied to the customer/order.
            created = await client.post('/trading/orders', headers=customer, json={
                'product_id': product_id,
                'amount': '50000.00',
                'idempotency_key': f'risk-control-{uuid.uuid4()}',
            })
            created.raise_for_status()
            order_id = created.json()['data']['id']
            confirmed = await client.post(f'/trading/orders/{order_id}/confirm', headers=customer)
            confirmed.raise_for_status()

            # A detected pre-review alert must block approval before a risk officer closes it.
            detected = await client.post(f'/trading/orders/{order_id}/review', headers=admin, json={'note': '预审风险检测应拦截'})
            assert detected.status_code == 409 and error_code(detected) == 'RISK_CONTROL_BLOCKED'
            async with db.engine.begin() as conn:
                detected_alert = (await conn.execute(text('SELECT id, status, rule_code FROM risk_alert WHERE order_id=:id ORDER BY created_at DESC LIMIT 1'), {'id': order_id})).mappings().one()
                assert detected_alert['status'] == 'open'
                assert detected_alert['rule_code'] == 'RW-001'

            material = await client.post(f"/risk-alerts/{detected_alert['id']}/action", headers=admin, json={
                'action_code': 'REQUEST_MATERIALS', 'note': '请补充本次交易目的、资金来源说明和相关凭证。',
            })
            material.raise_for_status()
            material_handle = await client.post(f"/risk-alerts/{detected_alert['id']}/handle", headers=admin, json={
                'status': 'pending_customer', 'note': '已向客户发起资料补充。',
                'assessment_result': 'insufficient_information',
                'assessment_note': '当前资料不足以完成风险判断。',
                'disposition_code': 'REQUEST_MATERIALS',
                'disposition_note': '请补充本次交易目的、资金来源说明和相关凭证。',
            })
            assert material_handle.status_code == 200, material_handle.text
            async with db.engine.begin() as conn:
                material_work_order = (await conn.execute(text('SELECT id, status, category, risk_alert_id FROM work_order WHERE risk_alert_id=:id'), {'id': detected_alert['id']})).mappings().one()
                assert material_work_order['status'] == 'pending_customer'
                assert material_work_order['category'] == 'risk_investigation'
            customer_supplement = await client.post(f"/workorders/{material_work_order['id']}/supplements", headers=customer, json={
                'content': '本次交易用于家庭教育支出，资金来自工资收入，相关凭证已通过工单附件补充。',
            })
            assert customer_supplement.status_code == 201, customer_supplement.text
            alerts_after_supplement = await client.get('/risk-alerts', headers=admin)
            alerts_after_supplement.raise_for_status()
            material_alert = next(item for item in alerts_after_supplement.json()['data']['items'] if item['id'] == detected_alert['id'])
            assert material_alert['status'] == 'investigating'
            assert material_alert['work_order_id'] == material_work_order['id']
            assert material_alert['work_order_status'] == 'in_progress'
            assert '家庭教育支出' in material_alert['latest_customer_supplement']
            async with db.engine.begin() as conn:
                await conn.execute(text('DELETE FROM work_order WHERE id=:id'), {'id': material_work_order['id']})
                await conn.execute(text('DELETE FROM risk_alert WHERE id=:id'), {'id': detected_alert['id']})

            # Directly seed a deterministic confirmed-risk alert fixture; the test targets the post-investigation gate.
            async with db.engine.begin() as conn:
                alert_id = str(uuid.uuid4())
                await conn.execute(text('''INSERT INTO risk_alert
                    (id, customer_id, order_id, rule_code, alert_level, status, confidence, evidence_json, recommendation,
                     handled_note, assessment_result, assessment_note, action_status, restriction_status, created_at)
                    VALUES (:id, :customer_id, :order_id, 'RW-TEST', 'high', 'acknowledged', 1,
                            '{"source":"risk_control_closure_test"}', '人工风控处置', '', 'confirmed_risk',
                            '测试确认风险', 'not_started', 'none', now())'''), {
                    'id': alert_id, 'customer_id': customer_id, 'order_id': order_id,
                })

            blocked = await client.post(f'/trading/orders/{order_id}/review', headers=admin, json={'note': '应被风险门禁拦截'})
            assert blocked.status_code == 409 and error_code(blocked) == 'RISK_CONTROL_BLOCKED'

            action = await client.post(f'/risk-alerts/{alert_id}/action', headers=admin, json={
                'action_code': 'RESTRICT_TRADING', 'note': '确认风险，限制交易',
            })
            action.raise_for_status()
            assert action.json()['data']['restriction_status'] == 'restricted'

            async with db.engine.begin() as conn:
                account_row = (await conn.execute(text('SELECT status, risk_restriction_status FROM account WHERE id=:id'), {'id': account_id})).mappings().one()
                assert account_row['risk_restriction_status'] == 'restricted'

            restricted_order = await client.post('/trading/orders', headers=customer, json={
                'product_id': product_id,
                'amount': '1000.00',
                'idempotency_key': f'risk-restricted-{uuid.uuid4()}',
            })
            assert restricted_order.status_code == 400
            assert error_code(restricted_order) == 'TRADING_ERROR' or 'restricted' in restricted_order.text.lower()
            premature_release = await client.post(f"/risk-alerts/{alert_id}/action", headers=admin, json={
                "action_code": "RELEASE_RESTRICTION", "note": "缺少复核结论时不能解除限制",
            })
            assert premature_release.status_code == 409
            reviewed = await client.post(f"/risk-alerts/{alert_id}/handle", headers=admin, json={
                "status": "investigating", "note": "完成风险复核，准备解除限制",
                "assessment_result": "cleared_after_review", "assessment_note": "测试确认已解除风险",
                "customer_consulted": True, "customer_consultation_note": "已完成客户沟通",
            })
            reviewed.raise_for_status()
            released = await client.post(f"/risk-alerts/{alert_id}/action", headers=admin, json={
                "action_code": "RELEASE_RESTRICTION", "note": "风险复核后解除限制",
            })
            released.raise_for_status()
            assert released.json()["data"]["restriction_status"] == "none"
            handled = await client.post(f"/risk-alerts/{alert_id}/handle", headers=admin, json={
                "status": "resolved", "note": "已完成风险复核并解除限制",
                "assessment_result": "cleared_after_review", "assessment_note": "测试确认已解除风险",
                "customer_consulted": True, "customer_consultation_note": "已完成客户沟通",
            })
            handled.raise_for_status()

            async with db.engine.begin() as conn:
                account_row = (await conn.execute(text('SELECT status, risk_restriction_status FROM account WHERE id=:id'), {'id': account_id})).mappings().one()
                assert account_row['risk_restriction_status'] == 'none'

            result = {
                'detected_alert_block_status': detected.status_code,
                'material_request_work_order_status': 'pending_customer',
                'customer_supplement_alert_status': 'investigating',
                'customer_supplement_visible_in_alert_list': True,
                'blocked_review_status': blocked.status_code,
                'blocked_review_code': error_code(blocked),
                'restricted_account_status': 'restricted',
                'restricted_order_status': restricted_order.status_code,
                'released_account_status': 'none',
                'pass': True,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        async with db.engine.begin() as conn:
            if alert_id:
                await conn.execute(text('DELETE FROM risk_alert WHERE id=:id'), {'id': alert_id})
            if order_id:
                await conn.execute(text('DELETE FROM order_status_history WHERE order_id=:id'), {'id': order_id})
                await conn.execute(text('DELETE FROM orders WHERE id=:id'), {'id': order_id})
            if restricted_order_id:
                await conn.execute(text('DELETE FROM order_status_history WHERE order_id=:id'), {'id': restricted_order_id})
                await conn.execute(text('DELETE FROM orders WHERE id=:id'), {'id': restricted_order_id})
            if account_id:
                await conn.execute(text("UPDATE account SET status='active', risk_restriction_status='none', risk_restriction_reason='', risk_restricted_until=NULL WHERE id=:id"), {'id': account_id})
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())

