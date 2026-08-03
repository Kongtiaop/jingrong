from __future__ import annotations

import argparse
import asyncio
import json

import httpx

TODO_KEYS = {'pending_orders', 'pending_redeems', 'pending_transfers', 'restricted_accounts', 'open_alerts'}
SUITABILITY_KEYS = {'suitable', 'mismatch', 'restricted', 'missing_risk'}
FLOW_KEYS = {'orders', 'redeems', 'transfers'}


async def login(client: httpx.AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post('/auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return {'Authorization': f"Bearer {response.json()['data']['access_token']}"}


async def main(base_url: str) -> None:
    async with httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=30) as client:
        admin_headers = await login(client, 'super_admin_demo', 'Demo@2026SuperAdmin')
        customer_headers = await login(client, 'customer_demo', 'Demo@2026Customer')

        admin_response = await client.get('/admin/dashboard/risk-workbench', headers=admin_headers)
        admin_response.raise_for_status()
        data = admin_response.json()['data']
        assert TODO_KEYS <= set(data['todo'])
        assert SUITABILITY_KEYS <= set(data['suitability'])
        assert FLOW_KEYS <= set(data['flows'])
        assert {item['level'] for item in data['risk_distribution']} == {'C1', 'C2', 'C3', 'C4', 'C5'}
        assert isinstance(data['alert_status'], dict)
        assert {'customer_total', 'executed_trade_amount'} <= set(data['supporting'])
        assert all(isinstance(data['todo'][key], int) and data['todo'][key] >= 0 for key in TODO_KEYS)
        assert all(isinstance(data['suitability'][key], int) and data['suitability'][key] >= 0 for key in SUITABILITY_KEYS)
        assert all(isinstance(item['count'], int) and item['count'] >= 0 for item in data['risk_distribution'])

        customer_response = await client.get('/admin/dashboard/risk-workbench', headers=customer_headers)
        assert customer_response.status_code == 403
        detail = customer_response.json().get('detail') or {}
        customer_code = detail.get('code') if isinstance(detail, dict) else None
        assert customer_code == 'STAFF_REQUIRED'

        print(json.dumps({
            'admin_status': admin_response.status_code,
            'customer_status': customer_response.status_code,
            'customer_error_code': customer_code,
            'todo': data['todo'],
            'risk_distribution': data['risk_distribution'],
            'flows': data['flows'],
            'alert_status': data['alert_status'],
            'supporting': data['supporting'],
            'pass': True,
        }, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    args = parser.parse_args()
    asyncio.run(main(args.base_url))
