from __future__ import annotations

import argparse
import asyncio
import json

import httpx

REQUIRED_FIELDS = {
    'customer_name',
    'customer_username',
    'business_type',
    'business_status',
    'business_reference',
    'product_name',
}


async def main(base_url: str, username: str, password: str) -> None:
    async with httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=20) as client:
        login = await client.post('/auth/login', json={'username': username, 'password': password})
        login.raise_for_status()
        headers = {'Authorization': f"Bearer {login.json()['data']['access_token']}"}

        response = await client.get('/admin/audit-logs', params={'resource_type': 'business_operation', 'limit': 10, 'offset': 0}, headers=headers)
        response.raise_for_status()
        data = response.json()['data']
        assert isinstance(data.get('items'), list) and isinstance(data.get('total'), int)
        assert data['items'], 'business_operation audit fixtures are required for this contract check'

        first = data['items'][0]
        missing = sorted(REQUIRED_FIELDS - set(first))
        assert not missing, f'missing semantic audit fields: {missing}'
        assert first['business_type'] in {'redeem', 'transfer'}
        assert first['business_status'] in {'pending_review', 'settled', 'rejected'}
        assert first['business_reference'] == first['resource_id']
        assert first['customer_name'] and first['customer_username']
        if first['business_type'] == 'redeem':
            assert first['product_name'], 'redeem audit should resolve product_name'

        print(json.dumps({
            'status': response.status_code,
            'total': data['total'],
            'items_count': len(data['items']),
            'first_item': first,
            'semantic_fields': sorted(REQUIRED_FIELDS),
            'pass': True,
        }, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--username', default='super_admin_demo')
    parser.add_argument('--password', default='Demo@2026SuperAdmin')
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.username, args.password))
