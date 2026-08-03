from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from app.repositories.mysql_profile import (
    MySQLAccountReadRepository,
    MySQLOrderReadRepository,
    MySQLRepository,
    MySQLStatusHistoryReadRepository,
    MySQLTradeReadRepository,
)


def norm(value):
    if value is None:
        return None
    return str(value)


def compare_fields(api_row, mysql_row, fields):
    differences = {}
    for field in fields:
        left = norm(api_row.get(field))
        right = norm(mysql_row.get(field))
        if left != right:
            differences[field] = {"api": left, "mysql": right}
    return differences


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--username', default='customer_demo')
    parser.add_argument('--password', default='Demo@2026Customer')
    parser.add_argument('--order-id', default=None)
    parser.add_argument('--allow-api-missing-order', action='store_true')
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.base_url, timeout=10) as client:
        health = await client.get('/health')
        health.raise_for_status()
        login = await client.post('/auth/login', json={'username': args.username, 'password': args.password})
        login.raise_for_status()
        token = login.json()['data']['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        api_account = (await client.get('/trading/account/me', headers=headers)).json()['data']
        api_orders = (await client.get('/trading/orders/me', headers=headers)).json()['data']
        api_trades = (await client.get('/trading/trades/me', headers=headers)).json()['data']
        if args.order_id:
            detail = await client.get(f'/trading/orders/{args.order_id}', headers=headers)
            if detail.status_code == 200:
                api_orders = [detail.json()['data']]
            else:
                if not args.allow_api_missing_order:
                    raise RuntimeError(f'order detail unavailable: {detail.status_code} {detail.text[:200]}')
                api_orders = []
            api_trades = [row for row in api_trades if row.get('order_id') == args.order_id]

    mysql = MySQLRepository('mysql+aiomysql://wealth:wealth_dev@127.0.0.1:3307/wealth_manager')
    try:
        user = await mysql.fetch_one('SELECT id FROM users WHERE username=%s', (args.username,))
        if not user:
            raise RuntimeError('user not found in MySQL migration database')
        user_id = str(user['id'])
        account_repo = MySQLAccountReadRepository(mysql)
        order_repo = MySQLOrderReadRepository(mysql)
        trade_repo = MySQLTradeReadRepository(mysql)
        history_repo = MySQLStatusHistoryReadRepository(mysql)
        mysql_account = await account_repo.get_by_user(user_id)
        mysql_orders = await order_repo.list_for_user(user_id)
        mysql_trades = await trade_repo.list_for_user(user_id)
        if args.order_id:
            mysql_orders = [row for row in mysql_orders if str(row['id']) == args.order_id]
            mysql_trades = [row for row in mysql_trades if str(row['order_id']) == args.order_id]

        mysql_orders_by_id = {str(row['id']): row for row in mysql_orders}
        mysql_trades_by_id = {str(row['id']): row for row in mysql_trades}
        order_diffs = {}
        history_diffs = {}
        for api_order in api_orders:
            row = mysql_orders_by_id.get(str(api_order['id']))
            if row is None:
                order_diffs[str(api_order['id'])] = {'missing_in_mysql': True}
                continue
            diff = compare_fields(api_order, row, ['id','order_no','product_id','amount','quantity','status','side','review_note','failure_reason'])
            api_history = api_order.get('history') or []
            mysql_history = await history_repo.list_for_order(str(api_order['id']))
            if len(api_history) != len(mysql_history):
                diff['history_count'] = {'api': len(api_history), 'mysql': len(mysql_history)}
            if diff:
                order_diffs[str(api_order['id'])] = diff
        for api_trade in api_trades:
            row = mysql_trades_by_id.get(str(api_trade['id']))
            if row is None:
                order_diffs[str(api_trade['id'])] = {'trade_missing_in_mysql': True}
                continue
            diff = compare_fields(api_trade, row, ['id','trade_no','order_id','product_id','amount','quantity'])
            if diff:
                order_diffs[str(api_trade['id'])] = diff
        api_order_ids = {str(row['id']) for row in api_orders}
        api_trade_ids = {str(row['id']) for row in api_trades}
        mysql_order_ids = set(mysql_orders_by_id)
        mysql_trade_ids = set(mysql_trades_by_id)
        result = {
            'api': {'account_id': api_account.get('id'), 'available_balance': norm(api_account.get('available_balance')), 'frozen_balance': norm(api_account.get('frozen_balance')), 'order_count': len(api_orders), 'trade_count': len(api_trades)},
            'mysql': {'account_id': mysql_account.get('id') if mysql_account else None, 'available_balance': norm(mysql_account.get('available_balance')) if mysql_account else None, 'frozen_balance': norm(mysql_account.get('frozen_balance')) if mysql_account else None, 'order_count': len(mysql_orders), 'trade_count': len(mysql_trades)},
            'differences': {
                'account': compare_fields(api_account, mysql_account or {}, ['id','account_no','currency','available_balance','frozen_balance','status']),
                'missing_orders_in_mysql': sorted(api_order_ids - mysql_order_ids),
                'extra_orders_in_mysql': sorted(mysql_order_ids - api_order_ids),
                'missing_trades_in_mysql': sorted(api_trade_ids - mysql_trade_ids),
                'extra_trades_in_mysql': sorted(mysql_trade_ids - api_trade_ids),
                'order_or_trade_fields': order_diffs,
                'order_history': history_diffs,
            },
        }
        result['pass'] = not any(result['differences'].values())
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if not result['pass']:
            raise SystemExit(1)
    finally:
        await mysql.close()


if __name__ == '__main__':
    asyncio.run(main())
