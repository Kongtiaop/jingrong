from __future__ import annotations

import argparse
import asyncio
import json
import httpx


async def main(base_url: str, username: str, password: str, profile_json: str, asset_id: str | None = None):
    original = json.loads(open(profile_json, encoding='utf-8').read())
    async with httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=15) as client:
        login = await client.post('/auth/login', json={'username': username, 'password': password})
        login.raise_for_status()
        token = login.json()['data']['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        profile = original.get('profile')
        if profile:
            payload = {key: profile.get(key) for key in ('age','occupation','region','investment_experience_years','investment_goal','investment_horizon_years','liquidity_preference')}
            response = await client.put('/profile/me', headers=headers, json=payload)
            response.raise_for_status()
        result = {'profile_restored': bool(profile), 'asset_cleanup': 'manual_required', 'pass': bool(profile)}
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--username', default='customer_demo')
    parser.add_argument('--password', default='Demo@2026Customer')
    parser.add_argument('--profile-json', required=True)
    parser.add_argument('--asset-id')
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.username, args.password, args.profile_json, args.asset_id))
