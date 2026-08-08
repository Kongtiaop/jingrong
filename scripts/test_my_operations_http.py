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
            redeem = await client.post('/operations/redeem', headers=customer, json={'product_id': product_id, 'quantity': 1, 'amount': 1, 'idempotency_key': f'my-redeem-{uuid.uuid4()}'})
            redeem.raise_for_status(); operation_ids.append(redeem.json()['data']['operation_id'])
            transfer = await client.post('/operations/transfer', headers=customer, json={'to_account': target_account, 'amount': 1, 'confirmed': True, 'idempotency_key': f'my-transfer-{uuid.uuid4()}'})
            transfer.raise_for_status(); operation_ids.append(transfer.json()['data']['operation_id'])

            mine = await client.get('/operations/me', params={'limit': 50}, headers=customer)
            mine.raise_for_status(); rows = mine.json()['data']['items']
            selected = [row for row in rows if row['operation_id'] in operation_ids]
            assert {row['operation'] for row in selected} == {'redeem', 'transfer'}
            assert all(row['status'] == 'pending_review' for row in selected)

            redeem_only = await client.get('/operations/me', params={'operation_type': 'redeem', 'limit': 50}, headers=customer)
            redeem_only.raise_for_status(); assert any(row['operation_id'] == operation_ids[0] for row in redeem_only.json()['data']['items'])
            assert all(row['operation'] == 'redeem' for row in redeem_only.json()['data']['items'])

            approved = await client.post(f'/operations/redeem/{operation_ids[0]}/review', headers=admin, json={'approve': True, 'note': '客户窗口审核通过'})
            approved.raise_for_status()
            settled = await client.get('/operations/me', params={'status': 'settled', 'limit': 50}, headers=customer)
            settled.raise_for_status(); settled_row = next(row for row in settled.json()['data']['items'] if row['operation_id'] == operation_ids[0])
            assert settled_row['latest_action'] == 'settle'
            assert settled_row['latest_note'] == '客户窗口审核通过'

            invalid = await client.get('/operations/me', params={'operation_type': 'other'}, headers=customer)
            assert invalid.status_code == 422
            print(json.dumps({'mine_status': mine.status_code, 'selected_count': len(selected), 'redeem_filter_status': redeem_only.status_code, 'settled_status': settled.status_code, 'invalid_type': invalid.status_code, 'pass': True}, ensure_ascii=False, indent=2))
    finally:
        async with db.engine.begin() as conn:
            if operation_ids:
                await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='business_operation' AND resource_id = ANY(:ids)"), {'ids': operation_ids})
                await conn.execute(text('DELETE FROM business_operation WHERE id = ANY(:ids)'), {'ids': operation_ids})
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())
