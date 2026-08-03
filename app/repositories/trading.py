

class SqlAlchemyCustomerQueryRepository:
 async def list_staff_customer_views(self,s,q="",limit=50,offset=0):
  from app.models.auth import User
  from app.models.profile import CustomerHolding, CustomerAssetSnapshot, CustomerRiskAssessment, CustomerProfile
  query=select(User).where(User.status=="active",User.roles.any(code="customer"))
  if q.strip():
   needle=f"%{q.strip()}%"; query=query.where(or_(User.username.ilike(needle),User.display_name.ilike(needle)))
  users=list((await s.execute(query.order_by(User.created_at).offset(offset).limit(limit))).scalars().all())
  ids=[u.id for u in users]
  if not ids: return []
  profiles={r.user_id:r for r in (await s.execute(select(CustomerProfile).where(CustomerProfile.user_id.in_(ids)))).scalars().all()}
  risks={}
  for r in (await s.execute(select(CustomerRiskAssessment).where(CustomerRiskAssessment.user_id.in_(ids),CustomerRiskAssessment.status.in_(["active","provisional"])).order_by(CustomerRiskAssessment.assessed_at.desc()))).scalars().all(): risks.setdefault(r.user_id,r)
  holding_counts=dict((await s.execute(select(CustomerHolding.user_id,func.count()).where(CustomerHolding.user_id.in_(ids),CustomerHolding.status=="active").group_by(CustomerHolding.user_id))).all())
  assets={}
  for a in (await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id.in_(ids)).order_by(CustomerAssetSnapshot.snapshot_time.desc(),CustomerAssetSnapshot.created_at.desc()))).scalars().all(): assets.setdefault(a.user_id,a)
  return [CustomerSummaryQueryRow(user=u, profile=profiles.get(u.id), risk=risks.get(u.id), holding_count=int(holding_counts.get(u.id,0)), asset=assets.get(u.id)) for u in users]

class SqlAlchemyAdminRepository:
 async def list_users(self,s,q="",role="",status="active",limit=20,offset=0):
  query=select(User); count_query=select(func.count()).select_from(User)
  if status=="active": query=query.where(User.deleted_at.is_(None),User.status!="deleted"); count_query=count_query.where(User.deleted_at.is_(None),User.status!="deleted")
  elif status=="deleted": query=query.where(User.deleted_at.is_not(None),User.status=="deleted"); count_query=count_query.where(User.deleted_at.is_not(None),User.status=="deleted")
  if q.strip():
   needle=f"%{q.strip()}%"; cond=or_(User.username.ilike(needle),User.display_name.ilike(needle)); query=query.where(cond); count_query=count_query.where(cond)
  if role.strip(): query=query.where(User.roles.any(code=role.strip())); count_query=count_query.where(User.roles.any(code=role.strip()))
  total=int((await s.execute(count_query)).scalar_one()); rows=list((await s.execute(query.options(selectinload(User.roles)).order_by(User.created_at.desc()).offset(offset).limit(limit))).scalars().all()); return total,rows
 async def get_user(self,s,user_id): return (await s.execute(select(User).options(selectinload(User.roles)).where(User.id==user_id))).scalar_one_or_none()
 async def get_roles(self,s,codes=None):
  query=select(Role).options(selectinload(Role.permissions))
  if codes is not None: query=query.where(Role.code.in_(codes))
  return list((await s.execute(query.order_by(Role.code))).scalars().all())
 async def list_roles(self,s): return await self.get_roles(s)
 async def get_role_by_id(self,s,role_id): return (await s.execute(select(Role).options(selectinload(Role.permissions)).where(Role.id==role_id))).scalar_one_or_none()
 async def list_permissions(self,s): return list((await s.execute(select(Permission).order_by(Permission.code))).scalars().all())
 async def get_permissions(self,s,codes): return list((await s.execute(select(Permission).where(Permission.code.in_(codes)))).scalars().all())
 async def update_user_roles(self,s,user,roles): user.roles=roles; user.is_super_admin=any(role.code=="super_admin" for role in roles); await s.flush(); return user
 async def update_user_status(self,s,user,status): user.status=status; await s.flush(); return user
 async def update_role_permissions(self,s,role,permissions): role.permissions=permissions; await s.flush(); return role

class SqlAlchemyEnterpriseVerificationRepository:
 async def get(self,s,user_id): return (await s.execute(select(CustomerEnterpriseVerification).where(CustomerEnterpriseVerification.user_id==user_id))).scalar_one_or_none()
 async def get_by_id(self,s,verification_id): return (await s.execute(select(CustomerEnterpriseVerification).where(CustomerEnterpriseVerification.id==verification_id))).scalar_one_or_none()
 async def list(self,s,status="pending"):
  query=select(CustomerEnterpriseVerification).order_by(CustomerEnterpriseVerification.created_at.desc())
  if status in {"pending","approved","rejected"}: query=query.where(CustomerEnterpriseVerification.status==status)
  return list((await s.execute(query)).scalars().all())
 async def save(self,s,verification): s.add(verification); await s.flush(); return verification
 async def update_values(self,s,verification): await s.flush(); return verification


class SqlAlchemyAuditLogRepository:
 async def save(self,s,log): s.add(log); await s.flush(); return log
 async def list_for_resource(self,s,resource_type,resource_id,limit=100):
  return list((await s.execute(select(AuditLog).where(AuditLog.resource_type==resource_type,AuditLog.resource_id==resource_id).order_by(AuditLog.created_at.desc()).limit(limit))).scalars().all())
 async def list(self,s,action="",resource_type="",limit=50,offset=0):
  query=select(AuditLog)
  count_query=select(func.count()).select_from(AuditLog)
  if action.strip():
   query=query.where(AuditLog.action==action.strip()); count_query=count_query.where(AuditLog.action==action.strip())
  if resource_type.strip():
   query=query.where(AuditLog.resource_type==resource_type.strip()); count_query=count_query.where(AuditLog.resource_type==resource_type.strip())
  total=int((await s.execute(count_query)).scalar_one())
  rows=list((await s.execute(query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit))).scalars().all())
  return total, rows

class SqlAlchemyProfileRepository:
 async def get(self,s,user_id): return (await s.execute(select(CustomerProfile).where(CustomerProfile.user_id==user_id))).scalar_one_or_none()
 async def list_customers(self,s,q="",limit=50,offset=0):
  from app.models.auth import User, Role, Permission
  query=select(User).where(User.status=="active", User.roles.any(code="customer"))
  count_query=select(func.count()).select_from(User).where(User.status=="active", User.roles.any(code="customer"))
  if q.strip():
   needle=f"%{q.strip()}%"; query=query.where(or_(User.username.ilike(needle),User.display_name.ilike(needle))); count_query=count_query.where(or_(User.username.ilike(needle),User.display_name.ilike(needle)))
  total=int((await s.execute(count_query)).scalar_one())
  rows=list((await s.execute(query.order_by(User.created_at).offset(offset).limit(limit))).scalars().all())
  return total, rows
 async def save(self,s,profile): s.add(profile); await s.flush(); return profile
 async def update_values(self,s,profile): await s.flush(); return profile

class SqlAlchemyRiskRepository:
 async def get_active(self,s,user_id): return (await s.execute(select(CustomerRiskAssessment).where(CustomerRiskAssessment.user_id==user_id,CustomerRiskAssessment.status.in_(["active","provisional"])).order_by(CustomerRiskAssessment.assessed_at.desc()).limit(1))).scalars().first()
 async def get_latest_active(self,s,user_id): return await self.get_active(s,user_id)
 async def supersede_active(self,s,user_id):
  rows=list((await s.execute(select(CustomerRiskAssessment).where(CustomerRiskAssessment.user_id==user_id,CustomerRiskAssessment.status=="active"))).scalars().all())
  for row in rows: row.status="superseded"
  await s.flush()
 async def save(self,s,assessment): s.add(assessment); await s.flush(); return assessment

from sqlalchemy import func, select, or_
from sqlalchemy.orm import selectinload
from app.models.auth import User, Role, Permission
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, CustomerProfile, CustomerRiskAssessment, CustomerEnterpriseVerification, Product
from app.models.audit import AuditLog
from app.models.trading import MoneyFlow, Account, Order, Trade, OrderStatusHistory, RiskAlert
from app.repositories.contracts import TradingUnitOfWorkProtocol, CustomerSummaryQueryRow

def locked(query, for_update): return query.with_for_update() if for_update else query

class SqlAlchemyProductReadRepository:
 async def get_by_id(self,s,product_id): return (await s.execute(select(Product).where(Product.id==product_id))).scalar_one_or_none()
 async def get_active(self,s,product_id): return (await s.execute(select(Product).where(Product.id==product_id,Product.status=="active",Product.deleted_at.is_(None)))).scalar_one_or_none()
 async def list_active(self,s): return list((await s.execute(select(Product).where(Product.status=="active",Product.deleted_at.is_(None)).order_by(Product.name))).scalars().all())
 async def save(self,s,product): s.add(product); await s.flush(); return product
 async def update_values(self,s,product): await s.flush(); return product

class SqlAlchemyAccountRepository:
 async def get_by_user(self,s,user_id,for_update=False): return (await s.execute(locked(select(Account).where(Account.user_id==user_id),for_update))).scalar_one_or_none()
 async def get_by_id(self,s,account_id,for_update=False): return (await s.execute(locked(select(Account).where(Account.id==account_id),for_update))).scalar_one_or_none()
 async def save(self,s,account): s.add(account); await s.flush(); return account
 async def create_default(self,s,user_id,account_no,available_balance):
  account=Account(id=str(__import__('uuid').uuid4()),user_id=user_id,account_no=account_no,available_balance=available_balance); s.add(account); await s.flush(); return account
 async def update_balances(self,s,account_id,available_balance,frozen_balance):
  account=await self.get_by_id(s,account_id,for_update=True); account.available_balance=available_balance; account.frozen_balance=frozen_balance; await s.flush(); return account
class SqlAlchemyOrderRepository:
 async def list_for_user(self,s,user_id,limit=None,offset=0):
  q=select(Order).where(Order.user_id==user_id).order_by(Order.created_at.desc())
  if offset: q=q.offset(offset)
  if limit is not None: q=q.limit(limit)
  return list((await s.execute(q)).scalars().all())
 async def list_pending(self,s,limit=20,offset=0):
  q=select(Order).where(Order.status=="pending_review").order_by(Order.created_at)
  total=int((await s.execute(select(func.count()).select_from(select(Order.id).where(Order.status=="pending_review").subquery()))).scalar_one())
  return total,list((await s.execute(q.offset(offset).limit(limit))).scalars().all())
 async def get(self,s,order_id,user_id=None,for_update=False):
  q=select(Order).where(Order.id==order_id);
  if user_id is not None:q=q.where(Order.user_id==user_id)
  return (await s.execute(locked(q,for_update))).scalar_one_or_none()
 async def get_by_idempotency(self,s,user_id,key): return (await s.execute(select(Order).where(Order.user_id==user_id,Order.idempotency_key==key).order_by(Order.created_at.desc()).limit(1))).scalars().first()
 async def save(self,s,order): s.add(order); await s.flush(); return order
 async def get_for_user(self,s,order_id,user_id,for_update=False): return (await s.execute(locked(select(Order).where(Order.id==order_id,Order.user_id==user_id),for_update))).scalar_one_or_none()
 async def get_for_update(self,s,order_id): return (await s.execute(select(Order).where(Order.id==order_id).with_for_update())).scalar_one_or_none()
class SqlAlchemyHoldingRepository:
 async def get_active(self,s,user_id,product_id,for_update=False): return (await s.execute(locked(select(CustomerHolding).where(CustomerHolding.user_id==user_id,CustomerHolding.product_id==product_id,CustomerHolding.status=="active"),for_update))).scalar_one_or_none()
 async def save(self,s,holding): s.add(holding); await s.flush(); return holding
 async def update_values(self,s,holding): await s.flush(); return holding
 async def sum_market_value(self,s,user_id): return (await s.execute(select(func.coalesce(func.sum(CustomerHolding.market_value),0)).where(CustomerHolding.user_id==user_id,CustomerHolding.status=="active"))).scalar_one()
 async def list_active(self,s,user_id): return list((await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user_id,CustomerHolding.status=="active").order_by(CustomerHolding.product_id))).scalars().all())
 async def count_active(self,s,user_id): return int((await s.execute(select(func.count()).select_from(CustomerHolding).where(CustomerHolding.user_id==user_id,CustomerHolding.status=="active"))).scalar_one())
 async def save(self,s,holding): s.add(holding); await s.flush(); return holding
class SqlAlchemyTradeRepository:
 async def save(self,s,trade): s.add(trade); await s.flush(); return trade
 async def list_for_user(self,s,user_id): return list((await s.execute(select(Trade).where(Trade.user_id==user_id).order_by(Trade.executed_at.desc()))).scalars().all())


class SqlAlchemyMoneyFlowRepository:
 async def list_window(self,s,customer_id,start_at,end_at=None,flow_types=None,direction=None):
  q=select(MoneyFlow).where(MoneyFlow.customer_id==customer_id, MoneyFlow.occurred_at>=start_at)
  if end_at is not None: q=q.where(MoneyFlow.occurred_at<end_at)
  if flow_types: q=q.where(MoneyFlow.flow_type.in_(flow_types))
  if direction: q=q.where(MoneyFlow.direction==direction)
  return list((await s.execute(q.order_by(MoneyFlow.occurred_at))).scalars().all())
 async def save(self,s,row): s.add(row); await s.flush(); return row
class SqlAlchemyRiskAlertRepository:
 async def list(self,s,customer_id=None,status="",alert_level="",limit=50,offset=0):
  q=select(RiskAlert); cq=select(func.count()).select_from(RiskAlert)
  if customer_id: q=q.where(RiskAlert.customer_id==customer_id); cq=cq.where(RiskAlert.customer_id==customer_id)
  if status: q=q.where(RiskAlert.status==status); cq=cq.where(RiskAlert.status==status)
  if alert_level: q=q.where(RiskAlert.alert_level==alert_level); cq=cq.where(RiskAlert.alert_level==alert_level)
  total=int((await s.execute(cq)).scalar_one())
  rows=list((await s.execute(q.order_by(RiskAlert.created_at.desc()).offset(offset).limit(limit))).scalars().all())
  return total, rows
 async def get_for_update(self,s,alert_id): return (await s.execute(select(RiskAlert).where(RiskAlert.id==alert_id).with_for_update())).scalar_one_or_none()
 async def save(self,s,alert): s.add(alert); await s.flush(); return alert

class SqlAlchemyStatusHistoryRepository:
 async def save(self,s,history): s.add(history); await s.flush(); return history
 async def list_for_order(self,s,order_id): return list((await s.execute(select(OrderStatusHistory).where(OrderStatusHistory.order_id==order_id).order_by(OrderStatusHistory.created_at))).scalars().all())

class SqlAlchemyAssetRepository:
 async def latest(self,s,user_id): return (await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user_id).order_by(CustomerAssetSnapshot.snapshot_time.desc().nullslast(),CustomerAssetSnapshot.created_at.desc().nullslast(),CustomerAssetSnapshot.id.desc()).limit(1))).scalars().first()
 async def save(self,s,snapshot): s.add(snapshot); await s.flush(); return snapshot
 async def update_values(self,s,snapshot): await s.flush(); return snapshot


class SqlAlchemyTradingUnitOfWork:
    """Small composition root for the current PostgreSQL transaction boundary.

    The service still receives the AsyncSession for transaction ownership, while
    all trading repository instances are constructed in one place so a MySQL
    implementation can be introduced without changing API call sites.
    """

    def __init__(self):
        self.users = __import__("app.repositories.profile", fromlist=["SqlAlchemyUserRepository"]).SqlAlchemyUserRepository()
        self.admin = SqlAlchemyAdminRepository()
        self.customer_queries = SqlAlchemyCustomerQueryRepository()
        self.profiles = SqlAlchemyProfileRepository()
        self.enterprise_verifications = SqlAlchemyEnterpriseVerificationRepository()
        self.audit_logs = SqlAlchemyAuditLogRepository()
        self.risks = SqlAlchemyRiskRepository()
        self.products = SqlAlchemyProductReadRepository()
        self.accounts = SqlAlchemyAccountRepository()
        self.orders = SqlAlchemyOrderRepository()
        self.holdings = SqlAlchemyHoldingRepository()
        self.assets = SqlAlchemyAssetRepository()
        self.trades = SqlAlchemyTradeRepository()
        self.status_history = SqlAlchemyStatusHistoryRepository()
        self.risk_alerts = SqlAlchemyRiskAlertRepository()
        self.money_flows = SqlAlchemyMoneyFlowRepository()

    async def commit(self, session):
        await session.commit()

    async def rollback(self, session):
        await session.rollback()




