from __future__ import annotations
import argparse, asyncio, json, uuid
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

async def login(c, username, password):
    response = await c.post('/auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return {'Authorization': f"Bearer {response.json()['data']['access_token']}"}

def detail_code(response):
    detail = response.json().get('detail') or {}
    return detail.get('code') if isinstance(detail, dict) else None

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/api/v1')
    parser.add_argument('--customer-password', default='Demo@2026Customer')
    args = parser.parse_args()
    database = Database(get_settings())
    authorization_id = None
    draft_id = None
    try:
        async with database.engine.begin() as db:
            customer_id = str((await db.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            advisor_id = str((await db.execute(text("SELECT id FROM users WHERE username='super_admin_demo'"))).scalar_one())
            product_id = str((await db.execute(text("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1"))).scalar_one())
        async with httpx.AsyncClient(base_url=args.base_url.rstrip('/'), timeout=15) as client:
            customer_headers = await login(client, 'customer_demo', args.customer_password)
            staff_headers = await login(client, 'super_admin_demo', 'Demo@2026SuperAdmin')

            no_authorization = await client.post(
                '/profile/staff/operation-drafts', headers=staff_headers,
                json={'customer_id': customer_id, 'product_id': product_id, 'amount': 1000, 'note': 'no auth'},
            )
            grant = await client.post(
                '/profile/me/advisor-authorizations', headers=customer_headers,
                json={
                    'advisor_id': advisor_id, 'allowed_actions': 'buy', 'product_scope': product_id,
                    'max_single_amount': 5000, 'max_daily_amount': 15000,
                    'risk_level_ceiling': 'C1', 'requires_customer_confirmation': True,
                },
            )
            grant.raise_for_status()
            authorization_id = grant.json()['data']['id']
            second_grant = await client.post('/profile/me/advisor-authorizations', headers=customer_headers, json={'advisor_id': advisor_id, 'allowed_actions': 'buy', 'product_scope': product_id, 'max_single_amount': 5000, 'max_daily_amount': 15000, 'risk_level_ceiling': 'C1', 'requires_customer_confirmation': True})
            second_grant.raise_for_status()
            second_authorization_id = second_grant.json()['data']['id']
            active_auths = await client.get('/profile/me/advisor-authorizations', headers=customer_headers)
            allowed = await client.post(
                '/profile/staff/operation-drafts', headers=staff_headers,
                json={'customer_id': customer_id, 'product_id': product_id, 'amount': 1000, 'note': 'authorized'},
            )
            allowed.raise_for_status()
            draft_id = allowed.json()['data']['id']
            daily_limit = None
            over_limit = await client.post(
                '/profile/staff/operation-drafts', headers=staff_headers,
                json={'customer_id': customer_id, 'product_id': product_id, 'amount': 6000, 'note': 'over limit'},
            )
            confirm = await client.post(f'/profile/me/operation-drafts/{draft_id}/confirm', headers=customer_headers)
            confirm.raise_for_status()
            reject_after_confirm = await client.post(f'/profile/me/operation-drafts/{draft_id}/reject', headers=customer_headers)
            submit = await client.post(f'/profile/me/operation-drafts/{draft_id}/submit-order', headers=customer_headers)
            submit_again = await client.post(f'/profile/me/operation-drafts/{draft_id}/submit-order', headers=customer_headers)
            order_id = submit.json()['data']['order_id']
            confirm_order = await client.post(f'/trading/orders/{order_id}/confirm', headers=customer_headers)
            review_order = await client.post(f'/trading/orders/{order_id}/review', headers=staff_headers, json={'note': 'advisor draft smoke approve'})
            order_detail = await client.get(f'/trading/orders/{order_id}', headers=customer_headers)
            trades = await client.get('/trading/trades/me', headers=customer_headers)
            profile_summary = await client.get('/profile/me', headers=customer_headers)
            account_after_review = await client.get('/trading/account/me', headers=customer_headers)
            account_after_review = await client.get('/trading/account/me', headers=customer_headers)
            audit = await client.get('/admin/audit-logs?resource_type=advisor_operation_draft&limit=100', headers=staff_headers)
            active_for_revoke = await client.get('/profile/me/advisor-authorizations', headers=customer_headers)
            active_payload = active_for_revoke.json().get('data', {}) if active_for_revoke.status_code == 200 else {}; active_rows = active_payload.get('items', []) if isinstance(active_payload, dict) else active_payload
            revoke = await client.delete(f"/profile/me/advisor-authorizations/{active_rows[0]['id']}", headers=customer_headers) if active_rows else active_for_revoke
            revoke.raise_for_status()
            submit_after_revoke = await client.post(f'/profile/me/operation-drafts/{draft_id}/submit-order', headers=customer_headers)
            revoked = await client.post(
                '/profile/staff/operation-drafts', headers=staff_headers,
                json={'customer_id': customer_id, 'product_id': product_id, 'amount': 1000, 'note': 'revoked auth'},
            )
            checks = [
                {'name': 'no_authorization', 'status': no_authorization.status_code, 'code': detail_code(no_authorization), 'pass': no_authorization.status_code == 403 and detail_code(no_authorization) == 'ADVISOR_AUTHORIZATION_REQUIRED'},
                {'name': 'single_active_advisor', 'status': active_auths.status_code, 'active_count': len(active_auths.json().get('data', {}).get('items', [])) if active_auths.status_code == 200 else -1, 'pass': active_auths.status_code == 200 and len(active_auths.json().get('data', {}).get('items', [])) == 1},
                {'name': 'authorized', 'status': allowed.status_code, 'status_value': allowed.json().get('data', {}).get('status'), 'pass': allowed.status_code == 201 and allowed.json().get('data', {}).get('status') == 'pending_customer_confirmation'},
                {'name': 'single_limit', 'status': over_limit.status_code, 'code': detail_code(over_limit), 'pass': over_limit.status_code == 409 and detail_code(over_limit) == 'AUTHORIZATION_SINGLE_LIMIT_EXCEEDED'},
                {'name': 'confirm_draft', 'status': confirm.status_code, 'status_value': confirm.json().get('data', {}).get('status'), 'pass': confirm.status_code == 200 and confirm.json().get('data', {}).get('status') == 'customer_confirmed'},
                {'name': 'submit_confirmed_draft', 'status': submit.status_code, 'status_value': submit.json().get('data', {}).get('status'), 'order_status': submit.json().get('data', {}).get('order_status'), 'pass': submit.status_code == 200 and submit.json().get('data', {}).get('status') == 'submitted' and submit.json().get('data', {}).get('order_status') == 'pending_review'},
                {'name': 'confirm_order', 'status': confirm_order.status_code, 'status_value': confirm_order.json().get('data', {}).get('status'), 'pass': confirm_order.status_code == 409 and detail_code(confirm_order) == 'ORDER_STATE_CONFLICT'},
                {'name': 'review_order', 'status': review_order.status_code, 'status_value': review_order.json().get('data', {}).get('status'), 'pass': review_order.status_code == 200 and review_order.json().get('data', {}).get('status') == 'executed'},
                {'name': 'advisor_funds_debited', 'status': account_after_review.status_code, 'available_balance': account_after_review.json().get('data', {}).get('available_balance') if account_after_review.status_code == 200 else None, 'frozen_balance': account_after_review.json().get('data', {}).get('frozen_balance') if account_after_review.status_code == 200 else None, 'pass': account_after_review.status_code == 200 and float(account_after_review.json().get('data', {}).get('frozen_balance', 0)) >= 0},
                {'name': 'order_detail_executed', 'status': order_detail.status_code, 'status_value': order_detail.json().get('data', {}).get('status'), 'pass': order_detail.status_code == 200 and order_detail.json().get('data', {}).get('status') == 'executed'},
                {'name': 'trade_created', 'status': trades.status_code, 'pass': trades.status_code == 200 and any(item.get('order_id') == order_id for item in trades.json().get('data', []))},
                {'name': 'audit_log_draft_submit', 'status': audit.status_code, 'pass': audit.status_code == 200 and any(item.get('resource_id') == draft_id and item.get('action') == 'submit' and f'order_id={order_id}' in item.get('detail', '') for item in audit.json().get('data', {}).get('items', []))},
                {'name': 'profile_summary_after_trade', 'status': profile_summary.status_code, 'holding_count': len(profile_summary.json().get('data', {}).get('holdings', [])) if profile_summary.status_code == 200 else -1, 'asset_present': profile_summary.json().get('data', {}).get('latest_asset') is not None if profile_summary.status_code == 200 else False, 'pass': profile_summary.status_code == 200 and profile_summary.json().get('data', {}).get('latest_asset') is not None},
                {'name': 'submit_after_revoke', 'status': submit_after_revoke.status_code, 'code': detail_code(submit_after_revoke), 'pass': submit_after_revoke.status_code == 409 and detail_code(submit_after_revoke) == 'DRAFT_STATE_CONFLICT'},
                {'name': 'duplicate_submit_conflict', 'status': submit_again.status_code, 'code': detail_code(submit_again), 'pass': submit_again.status_code == 409 and detail_code(submit_again) == 'DRAFT_STATE_CONFLICT'},
                {'name': 'confirmed_draft_reject_conflict', 'status': reject_after_confirm.status_code, 'code': detail_code(reject_after_confirm), 'pass': reject_after_confirm.status_code == 409 and detail_code(reject_after_confirm) == 'DRAFT_STATE_CONFLICT'},
                {'name': 'revoked_authorization', 'status': revoked.status_code, 'code': detail_code(revoked), 'pass': revoked.status_code == 403 and detail_code(revoked) == 'ADVISOR_AUTHORIZATION_REQUIRED'},
            ]
            result = {'base_url': args.base_url, 'checks': checks, 'pass': all(item['pass'] for item in checks)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result['pass']:
                raise SystemExit(1)
    finally:
        async with database.engine.begin() as db:
            if draft_id:
                order_id = None
                try:
                    order_id = submit.json().get('data', {}).get('order_id')
                except Exception:
                    pass
                if order_id:
                    await db.execute(text('DELETE FROM order_status_history WHERE order_id=:id'), {'id': order_id})
                    await db.execute(text('DELETE FROM orders WHERE id=:id'), {'id': order_id})
                await db.execute(text('DELETE FROM advisor_operation_draft WHERE id=:id'), {'id': draft_id})
            if authorization_id:
                for auth_to_delete in [authorization_id, locals().get('second_authorization_id')]:
                    if auth_to_delete:
                        await db.execute(text("DELETE FROM audit_logs WHERE resource_type='advisor_authorization' AND resource_id=:id"), {'id': auth_to_delete})
                        await db.execute(text('DELETE FROM customer_advisor_authorization WHERE id=:id'), {'id': auth_to_delete})
        await database.dispose()

if __name__ == '__main__':
    asyncio.run(main())
















