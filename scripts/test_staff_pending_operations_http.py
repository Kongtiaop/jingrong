from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database

BASE_URL = 'http://127.0.0.1:8010/api/v1'


async def login(client, username, password):
    response = await client.post('/auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return {'Authorization': f"Bearer {response.json()['data']['access_token']}"}


async def main():
    db = Database(get_settings())
    operation_ids: list[str] = []
    try:
        async with db.engine.connect() as conn:
            product_id = str((await conn.execute(text("SELECT id FROM product WHERE status='active' ORDER BY minimum_amount LIMIT 1"))).scalar_one())
            target_account = str((await conn.execute(text("SELECT account_no FROM account WHERE user_id <> (SELECT id FROM users WHERE username='customer_demo') ORDER BY account_no LIMIT 1"))).scalar_one())
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            customer = await login(client, 'customer_demo', 'Demo@2026Customer')
            admin = await login(client, 'super_admin_demo', 'Demo@2026SuperAdmin')

            redeem = await client.post('/operations/redeem', headers=customer, json={'product_id': product_id, 'quantity': 1, 'amount': 1, 'idempotency_key': f'pending-redeem-{uuid.uuid4()}'})
            redeem.raise_for_status(); operation_ids.append(redeem.json()['data']['operation_id'])
            transfer = await client.post('/operations/transfer', headers=customer, json={'to_account': target_account, 'amount': 1, 'confirmed': True, 'idempotency_key': f'pending-transfer-{uuid.uuid4()}'})
            transfer.raise_for_status(); operation_ids.append(transfer.json()['data']['operation_id'])

            all_pending = await client.get('/operations/staff/pending', params={'limit': 50}, headers=admin)
            all_pending.raise_for_status(); rows = all_pending.json()['data']['items']
            selected = [row for row in rows if row['operation_id'] in operation_ids]
            assert {row['operation'] for row in selected} == {'redeem', 'transfer'}
            assert all(row['status'] == 'pending_review' for row in selected)
            assert all(row['customer_username'] == 'customer_demo' for row in selected)
            assert all('risk_status' in row and 'created_at' in row for row in selected)

            redeem_only = await client.get('/operations/staff/pending', params={'operation_type': 'redeem', 'limit': 50}, headers=admin)
            redeem_only.raise_for_status(); redeem_rows = redeem_only.json()['data']['items']
            assert any(row['operation_id'] == operation_ids[0] for row in redeem_rows)
            assert all(row['operation'] == 'redeem' for row in redeem_rows)

            customer_forbidden = await client.get('/operations/staff/pending', headers=customer)
            assert customer_forbidden.status_code == 403
            invalid = await client.get('/operations/staff/pending', params={'operation_type': 'other'}, headers=admin)
            assert invalid.status_code == 422

            approve_redeem = await client.post(f'/operations/redeem/{operation_ids[0]}/review', headers=admin, json={'approve': True, 'note': '待办窗口通过赎回'})
            approve_redeem.raise_for_status()
            reject_transfer = await client.post(f'/operations/transfer/{operation_ids[1]}/review', headers=admin, json={'approve': False, 'note': '待办窗口拒绝转账'})
            reject_transfer.raise_for_status()
            after = await client.get('/operations/staff/pending', params={'limit': 50}, headers=admin)
            after.raise_for_status()
            assert not any(row['operation_id'] in operation_ids for row in after.json()['data']['items'])
            audit = await client.get('/operations/audit-logs', params={'operation_id': operation_ids[0], 'limit': 50}, headers=admin)
            audit.raise_for_status()
            assert any(row['action'] in {'settle', 'approve'} for row in audit.json()['data']['items']), audit.json()['data']['items']
            print(json.dumps({'all_status': all_pending.status_code, 'selected_count': len(selected), 'redeem_filter_status': redeem_only.status_code, 'customer_forbidden': customer_forbidden.status_code, 'invalid_type': invalid.status_code, 'redeem_review_status': approve_redeem.status_code, 'transfer_review_status': reject_transfer.status_code, 'queue_after_review': len([row for row in after.json()['data']['items'] if row['operation_id'] in operation_ids]), 'pass': True}, ensure_ascii=False, indent=2))
    finally:
        async with db.engine.begin() as conn:
            if operation_ids:
                await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='business_operation' AND resource_id = ANY(:ids)"), {'ids': operation_ids})
                await conn.execute(text('DELETE FROM business_operation WHERE id = ANY(:ids)'), {'ids': operation_ids})
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())
