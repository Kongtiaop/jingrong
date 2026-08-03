from __future__ import annotations
import asyncio,json,uuid
from decimal import Decimal
import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User, CustomerAdvisorAuthorization
from app.models.trading import Account, MoneyFlow
from app.models.operations import BusinessOperation
from app.models.audit import AuditLog
BASE='http://127.0.0.1:8010/api/v1'
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 db=Database(get_settings())
 try:
  async with db.session_factory() as s:
   customer=(await s.execute(select(User).options(selectinload(User.roles)).where(User.username=='customer_demo'))).scalar_one(); staff=(await s.execute(select(User).options(selectinload(User.roles)).where(User.username=='super_admin_demo'))).scalar_one(); target_user=(await s.execute(select(User).options(selectinload(User.roles)).where(User.username=='customer_service_demo'))).scalar_one(); src=(await s.execute(select(Account).where(Account.user_id==customer.id))).scalar_one(); dst=(await s.execute(select(Account).where(Account.user_id==target_user.id))).scalar_one(); before_src=Decimal(str(src.available_balance)); before_dst=Decimal(str(dst.available_balance)); before_customer_flow_ids=set((await s.execute(select(MoneyFlow.id).where(MoneyFlow.customer_id==customer.id,MoneyFlow.channel=='business_operation',MoneyFlow.flow_type=='transfer'))).scalars().all()); before_flow_ids=set((await s.execute(select(MoneyFlow.id).where(MoneyFlow.channel=='business_operation',MoneyFlow.flow_type=='transfer'))).scalars().all()); auth=None
  async with httpx.AsyncClient(base_url=BASE,timeout=30) as c:
   ch=await login(c,'customer_demo','Demo@2026Customer'); sh=await login(c,'super_admin_demo','Demo@2026SuperAdmin'); grant=await c.post('/profile/me/advisor-authorizations',headers=ch,json={'advisor_id':staff.id,'allowed_actions':'transfer','product_scope':'*','max_single_amount':50000,'max_daily_amount':100000,'risk_level_ceiling':'C5','requires_customer_confirmation':True}); grant.raise_for_status(); auth_id=grant.json()['data']['id']; me=(await c.get('/auth/me',headers=ch)).json()['data']; live_before=(await c.get('/trading/account/me',headers=ch)).json()['data']; key='staff-transfer-'+uuid.uuid4().hex; body={'customer_id':me['id'],'to_account':dst.account_no,'amount':'13.00','customer_confirmed':True,'confirmed':True,'idempotency_key':key}
   submit=await c.post('/operations/staff/transfer',headers=sh,json=body); checks={'submit':submit.status_code==200 and submit.json().get('data',{}).get('status')=='pending_review'}
   if not checks['submit']: print(submit.text); raise SystemExit(1)
   oid=submit.json()['data']['operation_id']; idem=await c.post('/operations/staff/transfer',headers=sh,json=body); checks['idempotent']=idem.status_code==200 and idem.json().get('data',{}).get('operation_id')==oid
   staffq=await c.get(f'/operations/staff/{oid}',headers=sh); checks['staff_query']=staffq.status_code==200 and staffq.json()['data']['operator_user_id']==staff.id
   custq=await c.get(f'/operations/me/{oid}',headers=ch); checks['customer_query']=custq.status_code==200 and custq.json()['data']['authorization_id']==auth_id
   settle=await c.post(f'/operations/transfer/{oid}/review',headers=sh,json={'approve':True,'note':'staff transfer settle'}); checks['settle']=settle.status_code==200 and settle.json().get('data',{}).get('status')=='settled'; repeat=await c.post(f'/operations/transfer/{oid}/review',headers=sh,json={'approve':True}); checks['repeat_conflict']=repeat.status_code==409
  async with db.session_factory() as s:
   src=(await s.execute(select(Account).where(Account.user_id==customer.id))).scalar_one(); dst=(await s.execute(select(Account).where(Account.user_id==target_user.id))).scalar_one(); op=(await s.execute(select(BusinessOperation).where(BusinessOperation.id==oid))).scalar_one(); flows=list((await s.execute(select(MoneyFlow).where(MoneyFlow.channel=='business_operation',MoneyFlow.flow_type=='transfer',MoneyFlow.id.not_in(before_flow_ids),MoneyFlow.amount==Decimal('13.00'),MoneyFlow.counterparty_id.in_([src.id,dst.id])))).scalars().all()); audits=list((await s.execute(select(AuditLog).where(AuditLog.resource_id==oid))).scalars().all()); checks.update({'db_settled':op.status=='settled','source_once':Decimal(str(src.available_balance))==Decimal(str(live_before['available_balance']))-Decimal('13.00'),'target_once':Decimal(str(dst.available_balance))-before_dst==Decimal('13.00'),'out_once':len([x for x in flows if x.direction=='out' and x.account_id==src.id])==1,'in_once':len([x for x in flows if x.direction=='in' and x.account_id==dst.id])==1,'audit_submit':any(x.action=='submit' for x in audits),'audit_settle':any(x.action=='settle' for x in audits),'stored_operator':op.operator_user_id==staff.id,'stored_auth':op.authorization_id==auth_id,'stored_confirmation':op.confirmation_mode=='staff_customer_confirmed'}); result={'checks':checks,'operation_id':oid,'target_account':dst.account_no,'before_src':str(before_src),'after_src':str(src.available_balance),'before_dst':str(before_dst),'after_dst':str(dst.available_balance),'flows':[{'account_id':x.account_id,'customer_id':x.customer_id,'direction':x.direction,'amount':str(x.amount)} for x in flows],'pass':all(checks.values())}; print(json.dumps(result,ensure_ascii=False,indent=2)); raise SystemExit(0 if result['pass'] else 1)
 finally: await db.dispose()
if __name__=='__main__': asyncio.run(main())

