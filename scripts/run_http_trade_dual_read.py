from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from decimal import Decimal

import httpx

from app.repositories.mysql_profile import MySQLRepository


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
        customer_login = data(await client.post('/auth/login', json={'username': 'customer_demo', 'password': args.customer_password}))
        operator_login = data(await client.post('/auth/login', json={'username': 'operations_demo', 'password': args.operator_password}))
        customer_headers = {'Authorization': f"Bearer {customer_login['access_token']}"}
        operator_headers = {'Authorization': f"Bearer {operator_login['access_token']}"}
        products = data(await client.get('/profile/products', headers=customer_headers))
        product = next(item for item in products if str(item.get('risk_level', 'C1')).replace('R', 'C') == 'C1' and item.get('target_customer_type', 'individual') in {'all', 'individual'})
        amount = str(max(Decimal(str(product.get('minimum_amount', '1000'))), Decimal('1000')))
        idem = 'dual-read-' + uuid.uuid4().hex
        created = data(await client.post('/trading/orders', headers=customer_headers, json={'product_id': product['id'], 'amount': amount, 'idempotency_key': idem}))
        confirmed = data(await client.post(f"/trading/orders/{created['id']}/confirm", headers=customer_headers))
        executed = data(await client.post(f"/trading/orders/{created['id']}/review", headers=operator_headers, json={'note': 'dual read test'}))
        api_account = data(await client.get('/trading/account/me', headers=customer_headers))
        api_orders = data(await client.get('/trading/orders/me', headers=customer_headers))
        api_trades = data(await client.get('/trading/trades/me', headers=customer_headers))
    mysql = MySQLRepository('mysql+aiomysql://wealth:wealth_dev@127.0.0.1:3307/wealth_manager')
    try:
        user = await mysql.fetch_one('SELECT id FROM users WHERE username=%s', ('customer_demo',))
        user_id = str(user['id'])
        mysql_account = await mysql.fetch_one('SELECT * FROM account WHERE user_id=%s', (user_id,))
        mysql_order = await mysql.fetch_one('SELECT * FROM orders WHERE id=%s', (created['id'],))
        if mysql_order is None:
            # Refresh MySQL from the PostgreSQL primary before comparing the just-created trade.
            raise RuntimeError('order is not yet present in MySQL; run migrate_core_to_mysql before dual-read comparison')
        mysql_trade = await mysql.fetch_one('SELECT * FROM trades WHERE order_id=%s', (created['id'],))
        mysql_history = await mysql.fetch_all('SELECT * FROM order_status_history WHERE order_id=%s ORDER BY created_at', (created['id'],))
        order_api = next(item for item in api_orders if item['id'] == created['id'])
        trade_api = next(item for item in api_trades if item['order_id'] == created['id'])
        field_diffs = {}
        fields = ['id','order_no','user_id','product_id','amount','quantity','status','side','review_note','failure_reason']
        for field in fields:
            left, right = str(order_api.get(field)), str(mysql_order.get(field))
            if left != right: field_diffs[f'order.{field}'] = {'api': left, 'mysql': right}
        trade_fields = ['id','trade_no','order_id','user_id','product_id','amount','quantity']
        for field in trade_fields:
            left, right = str(trade_api.get(field)), str(mysql_trade.get(field))
            if left != right: field_diffs[f'trade.{field}'] = {'api': left, 'mysql': right}
        result = {
            'order_id': created['id'],
            'api': {'account': api_account, 'order': order_api, 'trade': trade_api, 'history_count': len(order_api.get('history') or [])},
            'mysql': {'account': mysql_account, 'order': mysql_order, 'trade': mysql_trade, 'history_count': len(mysql_history)},
            'field_differences': field_diffs,
            'pass': not field_diffs and len(order_api.get('history') or []) == len(mysql_history),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if not result['pass']: raise SystemExit(1)
    finally:
        await mysql.close()


if __name__ == '__main__': asyncio.run(main())
