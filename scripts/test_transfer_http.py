from __future__ import annotations
import asyncio, json, uuid
from decimal import Decimal
import httpx
from sqlalchemy import select, func
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.trading import Account, MoneyFlow
from app.models.operations import BusinessOperation
from app.models.audit import AuditLog

BASE = "http://127.0.0.1:8010/api/v1"

def payload(r):
    r.raise_for_status(); body=r.json()
    if not body.get("success"): raise RuntimeError(body)
    return body["data"]

async def login(c, username, password):
    return {"Authorization": f"Bearer {payload(await c.post('/auth/login', json={'username': username, 'password': password}))['access_token']}"}

async def main():
    settings=get_settings(); db=Database(settings)
    try:
        async with db.session_factory() as s:
            customer=(await s.execute(select(User).where(User.username=='customer_demo'))).scalar_one()
            target_user=(await s.execute(select(User).where(User.username=='customer_service_demo'))).scalar_one()
            source=(await s.execute(select(Account).where(Account.user_id==customer.id))).scalar_one()
            target=(await s.execute(select(Account).where(Account.user_id==target_user.id))).scalar_one_or_none()
            if target is None:
                target=Account(id=str(uuid.uuid4()), user_id=target_user.id, account_no=f"AC{target_user.id[:10].upper()}", currency='CNY', available_balance=Decimal('50000.00'), frozen_balance=Decimal('0.00'), status='active')
                s.add(target); await s.commit()
            target_no=target.account_no
            customer_id=customer.id; target_user_id=target_user.id; before_target_balance=Decimal(str(target.available_balance)); before_flow_ids=set((await s.execute(select(MoneyFlow.id).where(MoneyFlow.channel=='business_operation',MoneyFlow.flow_type=='transfer'))).scalars().all())
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
            ch=await login(c,'customer_demo','Demo@2026Customer')
            sh=await login(c,'operations_demo','Demo@2026Operations')
            before_src=payload(await c.get('/trading/account/me',headers=ch))
            amount=Decimal('10.00'); key='transfer-http-'+uuid.uuid4().hex
            submitted=await c.post('/operations/transfer',headers=ch,json={'to_account':target_no,'amount':str(amount),'idempotency_key':key})
            checks={'submit_pending': submitted.status_code==200 and submitted.json().get('data',{}).get('status')=='pending_review'}
            oid=submitted.json()['data']['operation_id']
            idem=await c.post('/operations/transfer',headers=ch,json={'to_account':target_no,'amount':str(amount),'idempotency_key':key})
            checks['idempotent']=idem.status_code==200 and idem.json().get('data',{}).get('operation_id')==oid
            denied=await c.post(f'/operations/transfer/{oid}/review',headers=ch,json={'approve':True})
            checks['customer_forbidden']=denied.status_code==403
            rejected_key='transfer-reject-'+uuid.uuid4().hex
            s2=await c.post('/operations/transfer',headers=ch,json={'to_account':target_no,'amount':'11.00','idempotency_key':rejected_key})
            oid2=s2.json()['data']['operation_id']
            rejected=await c.post(f'/operations/transfer/{oid2}/review',headers=sh,json={'approve':False,'note':'transfer reject matrix'})
            checks['reject']=rejected.status_code==200 and rejected.json().get('data',{}).get('status')=='rejected'
            repeat_reject=await c.post(f'/operations/transfer/{oid2}/review',headers=sh,json={'approve':True})
            checks['reject_repeat_conflict']=repeat_reject.status_code==409
            settled=await c.post(f'/operations/transfer/{oid}/review',headers=sh,json={'approve':True,'note':'transfer settle matrix'})
            checks['settle']=settled.status_code==200 and settled.json().get('data',{}).get('status')=='settled'
            repeat_settle=await c.post(f'/operations/transfer/{oid}/review',headers=sh,json={'approve':True})
            checks['settle_repeat_conflict']=repeat_settle.status_code==409
            customer_query=await c.get(f'/operations/me/{oid}',headers=ch)
            checks['customer_query']=customer_query.status_code==200 and customer_query.json().get('data',{}).get('status')=='settled'
        async with db.session_factory() as s:
            src=(await s.execute(select(Account).where(Account.user_id==customer_id))).scalar_one()
            dst=(await s.execute(select(Account).where(Account.user_id==target_user_id))).scalar_one()
            ops=(await s.execute(select(BusinessOperation).where(BusinessOperation.id.in_([oid,oid2])))).scalars().all()
            flows=(await s.execute(select(MoneyFlow).where(MoneyFlow.channel=='business_operation',MoneyFlow.flow_type=='transfer',MoneyFlow.id.not_in(before_flow_ids),MoneyFlow.account_id.in_([src.id,dst.id])))).scalars().all()
            audits=(await s.execute(select(AuditLog).where(AuditLog.resource_type=='business_operation',AuditLog.resource_id.in_([oid,oid2])))).scalars().all()
            checks['settled_status_db']=next(o for o in ops if o.id==oid).status=='settled'
            checks['rejected_status_db']=next(o for o in ops if o.id==oid2).status=='rejected'
            checks['source_balance_once']=Decimal(str(src.available_balance))==Decimal(str(before_src['available_balance']))-amount
            checks['target_balance_once']=Decimal(str(dst.available_balance))==before_target_balance+amount
            checks['exact_transfer_out']=len([f for f in flows if f.account_id==src.id and f.direction=='out' and Decimal(str(f.amount))==amount])==1
            checks['exact_transfer_in']=len([f for f in flows if f.account_id==dst.id and f.direction=='in' and Decimal(str(f.amount))==amount])==1
            checks['reject_no_flow']=not any(f.channel=='business_operation' and f.flow_type=='transfer' and f.amount==Decimal('11.00') for f in flows)
            checks['submit_audit']=any(a.resource_id==oid and a.action=='submit' for a in audits)
            checks['settle_audit']=any(a.resource_id==oid and a.action=='settle' for a in audits)
            checks['reject_audit']=any(a.resource_id==oid2 and a.action=='reject' for a in audits)
            result={'checks':checks,'operation_id':oid,'reject_operation_id':oid2,'target_account':target_no,'source_balance':str(src.available_balance),'pass':all(checks.values())}
            print(json.dumps(result,ensure_ascii=False,indent=2))
            raise SystemExit(0 if result['pass'] else 1)
    finally:
        await db.dispose()

if __name__=='__main__': asyncio.run(main())

