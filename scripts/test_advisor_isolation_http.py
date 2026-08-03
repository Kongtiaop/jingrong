from __future__ import annotations
import argparse, asyncio, uuid
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

async def login(c, username, password):
    r=await c.post('/auth/login',json={'username':username,'password':password}); r.raise_for_status()
    return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}

def code(r):
    d=r.json().get('detail') or {}
    return d.get('code') if isinstance(d,dict) else None

async def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); args=ap.parse_args()
    db=Database(get_settings()); ids={}
    async with db.engine.begin() as conn:
        for username in ('customer_demo','hNW_demo','super_admin_demo'):
            ids[username]=str((await conn.execute(text('SELECT id FROM users WHERE username=:u'),{'u':username})).scalar_one())
        ids['product']=str((await conn.execute(text("SELECT id FROM product WHERE status='active' ORDER BY minimum_amount LIMIT 1"))).scalar_one())
    checks=[]; auth_id=None
    async with httpx.AsyncClient(base_url=args.base_url.rstrip('/'),timeout=20) as c:
        customer=await login(c,'customer_demo','Demo@2026Customer'); other=await login(c,'hNW_demo','Demo@2026HighNetWorth'); advisor=await login(c,'super_admin_demo','Demo@2026SuperAdmin')
        r=await c.get('/profile/staff/advisor-customers',headers=advisor); checks.append({'name':'unauthorized_customer_list_empty','status':r.status_code,'items':len(r.json().get('data',{}).get('items',[])),'pass':r.status_code==200 and ids['customer_demo'] not in [x['id'] for x in r.json().get('data',{}).get('items',[])]})
        r=await c.post('/profile/staff/recommendations',headers=advisor,json={'customer_id':ids['customer_demo']}); checks.append({'name':'recommend_before_authorization','status':r.status_code,'code':code(r),'pass':r.status_code==403 and code(r)=='ADVISOR_AUTHORIZATION_REQUIRED'})
        r=await c.get(f"/profile/staff/customer/{ids['customer_demo']}",headers=advisor); checks.append({'name':'detail_before_authorization','status':r.status_code,'code':code(r),'pass':r.status_code==403 and code(r)=='ADVISOR_AUTHORIZATION_REQUIRED'})
        r=await c.post('/profile/me/advisor-authorizations',headers=customer,json={'advisor_id':ids['super_admin_demo'],'allowed_actions':'buy','product_scope':ids['product'],'max_single_amount':5000,'max_daily_amount':10000,'risk_level_ceiling':'C1','requires_customer_confirmation':True}); r.raise_for_status(); auth_id=r.json()['data']['id']
        r=await c.get('/profile/staff/advisor-customers',headers=advisor); ids_seen=[x['id'] for x in r.json()['data']['items']]; checks.append({'name':'authorized_customer_visible','status':r.status_code,'pass':r.status_code==200 and ids['customer_demo'] in ids_seen})
        r=await c.post('/profile/staff/recommendations',headers=advisor,json={'customer_id':ids['customer_demo']}); checks.append({'name':'recommend_after_authorization','status':r.status_code,'pass':r.status_code==200})
        r=await c.get(f"/profile/staff/customer/{ids['customer_demo']}",headers=advisor); checks.append({'name':'detail_after_authorization','status':r.status_code,'pass':r.status_code==200})
        r=await c.post('/profile/me/advisor-authorizations',headers=other,json={'advisor_id':ids['super_admin_demo'],'allowed_actions':'buy','product_scope':ids['product'],'max_single_amount':5000,'max_daily_amount':10000,'risk_level_ceiling':'C1','requires_customer_confirmation':True}); r.raise_for_status(); other_auth_id=r.json()['data']['id']; r=await c.delete(f'/profile/me/advisor-authorizations/{other_auth_id}',headers=other); r.raise_for_status()
        r=await c.post('/profile/staff/recommendations',headers=advisor,json={'customer_id':ids['hNW_demo']}); checks.append({'name':'cross_customer_isolation','status':r.status_code,'code':code(r),'pass':r.status_code==403 and code(r)=='ADVISOR_AUTHORIZATION_REQUIRED'})
        r=await c.delete(f'/profile/me/advisor-authorizations/{auth_id}',headers=customer); r.raise_for_status()
        r=await c.get('/profile/staff/advisor-customers',headers=advisor); checks.append({'name':'customer_hidden_after_revoke','status':r.status_code,'pass':r.status_code==200 and ids['customer_demo'] not in [x['id'] for x in r.json().get('data',{}).get('items',[])]})
        r=await c.post('/profile/staff/recommendations',headers=advisor,json={'customer_id':ids['customer_demo']}); checks.append({'name':'recommend_after_revoke','status':r.status_code,'code':code(r),'pass':r.status_code==403 and code(r)=='ADVISOR_AUTHORIZATION_REQUIRED'})
    print({'checks':checks,'pass':all(x['pass'] for x in checks)})
    raise SystemExit(0 if all(x['pass'] for x in checks) else 1)

if __name__=='__main__': asyncio.run(main())


