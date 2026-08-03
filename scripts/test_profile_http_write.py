from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import httpx


async def main(base_url: str, username: str, password: str, snapshot_file: str | None = None):
    async with httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=15) as client:
        login = await client.post('/auth/login', json={'username': username, 'password': password})
        login.raise_for_status()
        token = login.json()['data']['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        before = await client.get('/profile/me', headers=headers)
        before.raise_for_status()
        before_data = before.json()['data']
        original_profile = before_data.get('profile')
        original_asset = before_data.get('latest_asset')
        if snapshot_file:
            Path(snapshot_file).write_text(json.dumps({'profile': original_profile, 'latest_asset': original_asset}, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        profile_payload = {
            'age': 36,
            'occupation': 'HTTP smoke tester',
            'region': 'Shanghai',
            'investment_experience_years': 8,
            'investment_goal': 'balanced',
            'investment_horizon_years': 6,
            'liquidity_preference': 'medium',
        }
        profile = await client.put('/profile/me', headers=headers, json=profile_payload)
        profile.raise_for_status()
        profile_data = profile.json()['data']
        assert profile_data['occupation'] == profile_payload['occupation']
        asset_payload = {
            'total_asset': 880000,
            'cash_balance': 280000,
            'investable_asset': 800000,
            'liability': 50000,
            'net_asset': 830000,
            'source_type': 'http_smoke',
        }
        asset = await client.post('/profile/me/assets', headers=headers, json=asset_payload)
        asset.raise_for_status()
        asset_data = asset.json()['data']
        assert asset_data['source_type'] == 'http_smoke'
        summary = await client.get('/profile/me', headers=headers)
        summary.raise_for_status()
        summary_data = summary.json()['data']
        assert summary_data['profile']['occupation'] == profile_payload['occupation']
        assert summary_data['latest_asset'] is not None
        result = {
            'profile_id': profile_data['id'],
            'asset_id': asset_data['id'],
            'summary_profile_matches': summary_data['profile']['occupation'] == profile_payload['occupation'],
            'summary_asset_present': summary_data['latest_asset'] is not None,
            'snapshot_file': snapshot_file,
            'cleanup': 'snapshot_saved_for_restore',
            'pass': True,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--username', default='customer_demo')
    parser.add_argument('--password', default='Demo@2026Customer')
    parser.add_argument('--snapshot-file', default='logs/profile_http_smoke_snapshot.json')
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.username, args.password, args.snapshot_file))
