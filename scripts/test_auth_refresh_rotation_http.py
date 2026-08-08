from __future__ import annotations
import asyncio
import json
import httpx
from app.main import app, lifespan

BASE = "http://test/api/v1"

async def main():
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=20) as client:
            login = await client.post('/auth/login', json={'username':'customer_demo','password':'Demo@2026Customer'})
            login.raise_for_status()
            first = login.json()['data']
            refresh = await client.post('/auth/refresh', json={'refresh_token': first['refresh_token']})
            refresh.raise_for_status()
            second = refresh.json()['data']
            old_reuse = await client.post('/auth/refresh', json={'refresh_token': first['refresh_token']})
            current = await client.get('/auth/me', headers={'Authorization': f"Bearer {second['access_token']}"})
            result = {
                'login': login.status_code,
                'first_refresh': refresh.status_code,
                'old_refresh_reuse': old_reuse.status_code,
                'new_access_valid': current.status_code,
                'pass': login.status_code == 200 and refresh.status_code == 200 and old_reuse.status_code == 401 and current.status_code == 200,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(0 if result['pass'] else 1)

if __name__ == '__main__':
    asyncio.run(main())
