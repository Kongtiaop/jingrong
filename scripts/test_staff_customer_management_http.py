from __future__ import annotations

import argparse
import asyncio
import json
from time import perf_counter

import httpx


async def main(base_url: str, username: str, password: str) -> None:
    async with httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=30) as client:
        login = await client.post('/auth/login', json={'username': username, 'password': password})
        login.raise_for_status()
        headers = {'Authorization': f"Bearer {login.json()['data']['access_token']}"}

        started = perf_counter()
        customers = await client.get('/profile/staff/customers', params={'limit': 10, 'offset': 0}, headers=headers)
        customer_elapsed_ms = round((perf_counter() - started) * 1000, 2)
        customers.raise_for_status()
        customer_data = customers.json()['data']
        assert isinstance(customer_data['items'], list)
        assert isinstance(customer_data['total'], int)
        assert customer_data['total'] >= len(customer_data['items'])
        if customer_data['items']:
            first = customer_data['items'][0]
            assert {'id', 'username', 'display_name', 'customer_tier', 'holding_count'} <= set(first)

        started = perf_counter()
        advisor_customers = await client.get('/profile/staff/advisor-customers', params={'limit': 10, 'offset': 0}, headers=headers)
        advisor_customer_elapsed_ms = round((perf_counter() - started) * 1000, 2)
        advisor_customers.raise_for_status()
        advisor_customer_data = advisor_customers.json()['data']
        assert isinstance(advisor_customer_data['items'], list)
        assert isinstance(advisor_customer_data['total'], int)

        started = perf_counter()
        advisors = await client.get('/profile/staff/advisors', params={'limit': 10, 'offset': 0}, headers=headers)
        advisor_elapsed_ms = round((perf_counter() - started) * 1000, 2)
        advisors.raise_for_status()
        advisor_data = advisors.json()['data']
        assert isinstance(advisor_data['items'], list)
        assert isinstance(advisor_data['total'], int)

        print(json.dumps({
            'customer_status': customers.status_code,
            'customer_total': customer_data['total'],
            'customer_items': len(customer_data['items']),
            'customer_elapsed_ms': customer_elapsed_ms,
            'advisor_customer_status': advisor_customers.status_code,
            'advisor_customer_total': advisor_customer_data['total'],
            'advisor_customer_items': len(advisor_customer_data['items']),
            'advisor_customer_elapsed_ms': advisor_customer_elapsed_ms,
            'advisor_status': advisors.status_code,
            'advisor_total': advisor_data['total'],
            'advisor_items': len(advisor_data['items']),
            'advisor_elapsed_ms': advisor_elapsed_ms,
            'pass': True,
        }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--username', default='super_admin_demo')
    parser.add_argument('--password', default='Demo@2026SuperAdmin')
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.username, args.password))
