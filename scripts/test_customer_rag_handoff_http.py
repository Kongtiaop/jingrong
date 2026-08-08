from __future__ import annotations

import asyncio
import json

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
    created_id = None
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            customer = await login(client, 'customer_demo', 'Demo@2026Customer')
            first = await client.post('/customer-rag/answer', headers=customer, json={'query': '这是一条当前知识库没有覆盖的客服问题', 'intent': 'company_info'})
            first.raise_for_status()
            first_data = first.json()['data']
            assert first_data['handoff_required'] is True
            assert first_data['work_order_created'] is True
            created_id = first_data['work_order_id']
            second = await client.post('/customer-rag/answer', headers=customer, json={'query': '  这是一条当前知识库没有覆盖的客服问题  ', 'intent': 'company_info'})
            second.raise_for_status()
            second_data = second.json()['data']
            assert second_data['work_order_created'] is False
            assert second_data['work_order_id'] == created_id
            product = await client.post('/customer-rag/answer', headers=customer, json={'query': '给我推荐产品', 'intent': 'product_recommendation'})
            product.raise_for_status()
            product_data = product.json()['data']
            assert product_data['answer_mode'] == 'database_recommendation'
            assert product_data['work_order_id'] is None
        async with db.engine.connect() as conn:
            row = (await conn.execute(text('SELECT context_json FROM work_order WHERE id=:id'), {'id': created_id})).scalar_one()
            context = json.loads(row)
            updates = int((await conn.execute(text('SELECT count(*) FROM work_order_update WHERE work_order_id=:id'), {'id': created_id})).scalar_one())
            assert context['source'] == 'customer_rag' and updates >= 1
        print(json.dumps({'first_created': True, 'duplicate_reused': True, 'context_saved': True, 'update_appended': True, 'product_no_handoff': True, 'pass': True}, ensure_ascii=False))
    finally:
        if created_id:
            async with db.engine.begin() as conn:
                await conn.execute(text('DELETE FROM notification WHERE resource_type=\'work_order\' AND resource_id=:id'), {'id': created_id})
                await conn.execute(text('DELETE FROM work_order_update WHERE work_order_id=:id'), {'id': created_id})
                await conn.execute(text('DELETE FROM work_order WHERE id=:id'), {'id': created_id})
        await db.dispose()

if __name__ == '__main__':
    asyncio.run(main())
