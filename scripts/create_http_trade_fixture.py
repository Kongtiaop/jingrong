from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from decimal import Decimal

import httpx


def data(response):
    response.raise_for_status()
    payload = response.json()
    if not payload.get('success'):
        raise RuntimeError(payload)
    return payload['data']


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--customer-password', default='Demo@2026Customer')
    parser.add_argument('--operator-password', default='Demo@2026Operations')
    args = parser.parse_args()
    async with httpx.AsyncClient(base_url=args.base_url, timeout=15) as client:
        customer = data(await client.post('/auth/login', json={'username': 'customer_demo', 'password': args.customer_password}))
        operator = data(await client.post('/auth/login', json={'username': 'operations_demo', 'password': args.operator_password}))
        customer_headers = {'Authorization': f"Bearer {customer['access_token']}"}
        operator_headers = {'Authorization': f"Bearer {operator['access_token']}"}
        products = data(await client.get('/profile/products', headers=customer_headers))
        product = next(item for item in products if str(item.get('risk_level','C1')).replace('R','C') == 'C1' and item.get('target_customer_type','individual') in {'all','individual'})
        amount = str(max(Decimal(str(product.get('minimum_amount','1000'))), Decimal('1000')))
        idem = 'dual-fixture-' + uuid.uuid4().hex
        created = data(await client.post('/trading/orders', headers=customer_headers, json={'product_id': product['id'], 'amount': amount, 'idempotency_key': idem}))
        order_id = created['id']
        data(await client.post(f'/trading/orders/{order_id}/confirm', headers=customer_headers))
        executed = data(await client.post(f'/trading/orders/{order_id}/review', headers=operator_headers, json={'note':'non-empty dual-read fixture'}))
        print(json.dumps({'order_id': order_id, 'product_id': product['id'], 'amount': amount, 'final_status': executed.get('status'), 'idempotency_key': idem}, ensure_ascii=False, indent=2))

if __name__ == '__main__': asyncio.run(main())
