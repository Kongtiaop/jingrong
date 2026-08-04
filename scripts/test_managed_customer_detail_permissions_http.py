from __future__ import annotations

import asyncio
import json

import httpx
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database

BASE_URL = 'http://127.0.0.1:8010/api/v1'


async def login(client: httpx.AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post('/auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return {'Authorization': f"Bearer {response.json()['data']['access_token']}"}


def error_code(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get('detail')
    except ValueError:
        return None
    return detail.get('code') if isinstance(detail, dict) else None


async def main() -> None:
    db = Database(get_settings())
    try:
        async with db.engine.connect() as conn:
            customer_id = str((await conn.execute(text(
                "SELECT id FROM users WHERE username='customer_demo'"
            ))).scalar_one())

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            admin = await login(client, 'super_admin_demo', 'Demo@2026SuperAdmin')
            customer = await login(client, 'customer_demo', 'Demo@2026Customer')

            managed = await client.get(f'/profile/staff/customers/{customer_id}', headers=admin)
            managed.raise_for_status() if managed.status_code != 422 else (_ for _ in ()).throw(AssertionError(managed.text))
            managed_data = managed.json()['data']
            assert managed_data['profile']['user_id'] == customer_id
            assert 'risk_operation' in managed_data

            no_permission = await client.get(f'/profile/staff/customers/{customer_id}', headers=customer)
            assert no_permission.status_code == 403, no_permission.text

            advisor_scoped = await client.get(f'/profile/staff/customer/{customer_id}', headers=admin)
            assert advisor_scoped.status_code == 403
            assert error_code(advisor_scoped) == 'ADVISOR_AUTHORIZATION_REQUIRED'

            print(json.dumps({
                'managed_customer_detail_status': managed.status_code,
                'managed_customer_id': managed_data['profile']['user_id'],
                'managed_customer_risk_status': managed_data['risk_operation']['status'],
                'missing_customer_read_status': no_permission.status_code,
                'advisor_scope_status': advisor_scoped.status_code,
                'advisor_scope_error_code': error_code(advisor_scoped),
                'pass': True,
            }, ensure_ascii=False, indent=2))
    finally:
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())



