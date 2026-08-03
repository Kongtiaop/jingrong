from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx


async def main(base_url: str, username: str, password: str, snapshot_file: str):
    async with httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=15) as client:
        login = await client.post('/auth/login', json={'username': username, 'password': password})
        login.raise_for_status()
        token = login.json()['data']['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        before = await client.get('/profile/me/enterprise-verification', headers=headers)
        before.raise_for_status()
        original = before.json().get('data')
        Path(snapshot_file).write_text(json.dumps({'enterprise_verification': original}, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        payload = {
            'company_name': 'HTTP Smoke Enterprise Ltd',
            'registration_no': 'SMOKE-20260801',
            'legal_representative': 'Smoke Tester',
            'contact_phone': '13800000000',
        }
        first = await client.post('/profile/me/enterprise-verification', headers=headers, json=payload)
        if first.status_code == 409:
            print(json.dumps({'skipped': 'customer_demo already enterprise/approved', 'status': first.status_code, 'detail': first.json().get('detail'), 'pass': True}, ensure_ascii=False))
            return
        first.raise_for_status()
        first_data = first.json()['data']
        assert first_data['status'] == 'pending'
        second_payload = {**payload, 'company_name': 'HTTP Smoke Enterprise Updated Ltd'}
        second = await client.post('/profile/me/enterprise-verification', headers=headers, json=second_payload)
        second.raise_for_status()
        second_data = second.json()['data']
        assert second_data['id'] == first_data['id']
        assert second_data['company_name'] == second_payload['company_name']
        current = await client.get('/profile/me/enterprise-verification', headers=headers)
        current.raise_for_status()
        current_data = current.json()['data']
        assert current_data['id'] == first_data['id']
        result = {'verification_id': first_data['id'], 'first_status': first_data['status'], 'resubmit_same_id': second_data['id'] == first_data['id'], 'current_matches': current_data['company_name'] == second_payload['company_name'], 'snapshot_file': snapshot_file, 'pass': True}
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--username', default='customer_demo')
    parser.add_argument('--password', default='Demo@2026Customer')
    parser.add_argument('--snapshot-file', default='logs/enterprise_http_smoke_snapshot.json')
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.username, args.password, args.snapshot_file))
