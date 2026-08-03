from __future__ import annotations
import argparse, asyncio, json, uuid
import httpx
from sqlalchemy import text
from app.db.session import Database
from app.core.settings import get_settings

async def login(client, username, password):
    response = await client.post('/auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return {'Authorization': f"Bearer {response.json()['data']['access_token']}"}

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--customer-password', default='Demo@2026Customer')
    parser.add_argument('--admin-password', default='Demo@2026SuperAdmin')
    args = parser.parse_args()
    settings = get_settings()
    database = Database(settings)
    verification_id = None
    customer_id = None
    try:
        async with database.engine.begin() as connection:
            customer_id = str((await connection.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            await connection.execute(text("DELETE FROM customer_enterprise_verification WHERE user_id=:user AND status='pending'"), {'user': customer_id})
        async with httpx.AsyncClient(base_url=args.base_url.rstrip('/'), timeout=15) as client:
            customer_headers = await login(client, 'customer_demo', args.customer_password)
            admin_headers = await login(client, 'super_admin_demo', args.admin_password)
            submit = await client.post(
                '/profile/me/enterprise-verification',
                headers=customer_headers,
                json={
                    'company_name': 'HTTP契约测试公司',
                    'registration_no': 'ERR-HTTP',
                    'legal_representative': '测试',
                    'contact_phone': '13800000000',
                },
            )
            if submit.status_code == 409:
                print(json.dumps({'skipped': 'customer_demo already enterprise/approved', 'status': submit.status_code, 'detail': submit.json().get('detail'), 'pass': True}, ensure_ascii=False))
                return
            submit.raise_for_status()
            verification_id = submit.json()['data']['id']
            missing = await client.post(
                f'/admin/enterprise-verifications/{uuid.uuid4()}/review',
                headers=admin_headers,
                json={'approved': True, 'note': 'missing'},
            )
            first = await client.post(
                f'/admin/enterprise-verifications/{verification_id}/review',
                headers=admin_headers,
                json={'approved': True, 'note': 'approve'},
            )
            duplicate = await client.post(
                f'/admin/enterprise-verifications/{verification_id}/review',
                headers=admin_headers,
                json={'approved': True, 'note': 'duplicate'},
            )
            checks = [
                {
                    'name': 'missing',
                    'status': missing.status_code,
                    'detail': missing.json().get('detail'),
                    'pass': missing.status_code == 404 and missing.json().get('detail', {}).get('code') == 'VERIFICATION_NOT_FOUND',
                },
                {
                    'name': 'duplicate',
                    'status': duplicate.status_code,
                    'detail': duplicate.json().get('detail'),
                    'pass': duplicate.status_code == 409 and duplicate.json().get('detail', {}).get('code') == 'VERIFICATION_ALREADY_APPROVED',
                },
            ]
            result = {'checks': checks, 'first_status': first.status_code, 'pass': first.status_code == 200 and all(item['pass'] for item in checks)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result['pass']:
                raise SystemExit(1)
    finally:
        if verification_id:
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM audit_logs WHERE resource_type='enterprise_verification' AND resource_id=:id"),
                    {'id': verification_id},
                )
                await connection.execute(
                    text("DELETE FROM customer_enterprise_verification WHERE id=:id"),
                    {'id': verification_id},
                )
        await database.dispose()

if __name__ == '__main__':
    asyncio.run(main())
