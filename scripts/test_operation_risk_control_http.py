from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database

async def login(client, username, password):
    response = await client.post('/auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return {'Authorization': f"Bearer {response.json()['data']['access_token']}"}

def code(response):
    detail = response.json().get('detail') or {}
    return detail.get('code') if isinstance(detail, dict) else None

async def main():
    db = Database(get_settings())
    alert_ids=[]; operation_ids=[]; customer_id=None; holding_id=None; account_id=None; target_id=None
    try:
        async with db.engine.begin() as conn:
            customer_id=str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            account_id=str((await conn.execute(text('SELECT id FROM account WHERE user_id=:id'), {'id':customer_id})).scalar_one())
            holding=(await conn.execute(text("SELECT id, product_id, quantity FROM customer_holding WHERE user_id=:id AND status='active' ORDER BY quantity DESC LIMIT 1"), {'id':customer_id})).mappings().one()
            holding_id=str(holding['id']); product_id=str(holding['product_id'])
            target=(await conn.execute(text("SELECT id, account_no FROM account WHERE user_id<>:id ORDER BY account_no LIMIT 1"), {'id':customer_id})).mappings().one()
            target_id=str(target['id']); target_account=str(target['account_no'])
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8010/api/v1',timeout=30) as c:
            customer=await login(c,'customer_demo','Demo@2026Customer'); admin=await login(c,'super_admin_demo','Demo@2026SuperAdmin')
            before=await c.get('/profile/me',headers=customer); before.raise_for_status(); before_data=before.json()['data']; holding_before=next(x for x in before_data['holdings'] if x['id']==holding_id)
            account_before=(await c.get('/trading/account/me',headers=customer)).json()['data']
            checks=[]
            for op_type,payload in [('redeem',{'product_id':product_id,'quantity':1,'amount':1,'idempotency_key':f'risk-redeem-{uuid.uuid4()}'}),('transfer',{'to_account':target_account,'amount':1,'confirmed':True,'idempotency_key':f'risk-transfer-{uuid.uuid4()}'})]:
                path='/operations/redeem' if op_type=='redeem' else '/operations/transfer'
                created=await c.post(path,headers=customer,json=payload); created.raise_for_status(); oid=created.json()['data']['operation_id']; operation_ids.append(oid)
                alert_id=str(uuid.uuid4()); alert_ids.append(alert_id)
                async with db.engine.begin() as conn:
                    await conn.execute(text('''INSERT INTO risk_alert (id,customer_id,order_id,rule_code,alert_level,status,confidence,evidence_json,recommendation,handled_note,assessment_result,assessment_note,action_status,restriction_status,created_at) VALUES (:id,:customer,NULL,'RW-TEST-OP','high','acknowledged',1,'{}','测试风险','', 'confirmed_risk','测试确认','not_started','none',now())'''), {'id':alert_id,'customer':customer_id})
                review=await c.post(f'/operations/{op_type}/{oid}/review',headers=admin,json={'approve':True,'note':'应被风险门禁拦截'})
                checks.append({'name':f'{op_type}_blocked','status':review.status_code,'code':code(review),'pass':review.status_code==409 and code(review)=='RISK_CONTROL_BLOCKED'})
            after=(await c.get('/profile/me',headers=customer)).json()['data']; account_after=(await c.get('/trading/account/me',headers=customer)).json()['data']; holding_after=next(x for x in after['holdings'] if x['id']==holding_id)
            checks += [
                {'name':'balance_unchanged','pass':str(account_before['available_balance'])==str(account_after['available_balance']) and str(account_before['frozen_balance'])==str(account_after['frozen_balance'])},
                {'name':'holding_unchanged','pass':str(holding_before['quantity'])==str(holding_after['quantity']) and str(holding_before['cost_amount'])==str(holding_after['cost_amount'])},
            ]
            result={'checks':checks,'pass':all(x['pass'] for x in checks)}; print(json.dumps(result,ensure_ascii=False,indent=2)); raise SystemExit(0 if result['pass'] else 1)
    finally:
        async with db.engine.begin() as conn:
            for aid in alert_ids: await conn.execute(text('DELETE FROM risk_alert WHERE id=:id'),{'id':aid})
            for oid in operation_ids:
                await conn.execute(text('DELETE FROM audit_logs WHERE resource_id=:id'),{'id':oid}); await conn.execute(text('DELETE FROM business_operation WHERE id=:id'),{'id':oid})
        await db.dispose()
if __name__=='__main__': asyncio.run(main())
