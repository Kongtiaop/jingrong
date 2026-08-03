from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx


async def main(base_url: str, username: str, password: str, snapshot_file: str):
    original = json.loads(Path(snapshot_file).read_text(encoding='utf-8')).get('enterprise_verification')
    if original is None:
        result = {'restored': False, 'reason': 'no_original_verification', 'pass': True}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    async with httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=15) as client:
        login = await client.post('/auth/login', json={'username': username, 'password': password})
        login.raise_for_status()
        token = login.json()['data']['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        payload = {key: original.get(key, '') for key in ('company_name','registration_no','legal_representative','contact_phone')}
        response = await client.post('/profile/me/enterprise-verification', headers=headers, json=payload)
        response.raise_for_status()
        current = response.json()['data']
        result = {'restored': current['company_name'] == original['company_name'], 'verification_id': current['id'], 'pass': current['company_name'] == original['company_name']}
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--username', default='customer_demo')
    parser.add_argument('--password', default='Demo@2026Customer')
    parser.add_argument('--snapshot-file', default='logs/enterprise_http_smoke_snapshot.json')
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.username, args.password, args.snapshot_file))
