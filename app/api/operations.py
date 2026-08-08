from decimal import Decimal
from typing import Annotated
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.common.response import ApiResponse
from app.common.security.auth import advisor_user, current_user, require_advisor_permission, require_staff_permission
from app.models.auth import User
from app.models.audit import AuditLog
from app.models.operations import BusinessOperation
import json
from app.models.profile import CustomerHolding, CustomerProfile, Product
from app.models.auth import CustomerAdvisorAuthorization
from app.services.asset_summary_service import AssetSummaryService
from app.models.trading import Account, MoneyFlow, RiskAlert
from app.schemas.operations import *

router=APIRouter(prefix='/operations',tags=['operations'])
staff_operation_read_user = require_staff_permission('operation:read_staff')
staff_operation_review_user = require_staff_permission('order:review')
staff_operation_audit_user = require_staff_permission('operation:audit_read')
staff_transfer_submit_user = require_staff_permission('transfer:submit_staff')
advisor_operation_submit_user = require_advisor_permission('order:submit_staff')

async def _operation_risk_block(s, operation):
    alert = (await s.execute(select(RiskAlert).where(
        RiskAlert.status.in_(['open','acknowledged','investigating','pending_customer','report_pending']),
        ((RiskAlert.order_id == operation.id) | ((RiskAlert.order_id.is_(None)) & (RiskAlert.customer_id == operation.user_id))),
    ).order_by(RiskAlert.created_at.desc()))).scalars().first()
    return alert

def _customer(user):
    if not any(role.code=='customer' for role in user.roles): raise HTTPException(403,detail={'code':'CUSTOMER_ACCOUNT_REQUIRED','message':'customer account required'})

@router.put('/contact',response_model=ApiResponse[dict])
async def update_contact(request:Request,payload:ContactUpdateRequest,user:Annotated[User,Depends(current_user)]):
    _customer(user)
    async with request.app.state.database.session_factory() as s:
        profile=(await s.execute(select(CustomerProfile).where(CustomerProfile.user_id==user.id).with_for_update())).scalar_one_or_none()
        if profile is None: raise HTTPException(404,detail={'code':'PROFILE_NOT_FOUND','message':'customer profile not found'})
        if payload.region is not None: profile.region=payload.region
        if payload.occupation is not None: profile.occupation=payload.occupation
        await s.flush(); await s.commit()
        return ApiResponse(data={'user_id':user.id,'region':profile.region,'occupation':profile.occupation})

@router.post('/redeem',response_model=ApiResponse[dict])
async def redeem(request:Request,payload:RedeemRequest,user:Annotated[User,Depends(current_user)]):
    _customer(user)
    async with request.app.state.database.session_factory() as s:
        if payload.idempotency_key:
            existing=(await s.execute(select(BusinessOperation).where(BusinessOperation.user_id==user.id,BusinessOperation.idempotency_key==payload.idempotency_key))).scalar_one_or_none()
            if existing is not None: return ApiResponse(data={'operation_id':existing.id,'operation':existing.operation_type,'status':existing.status,'amount':existing.amount,'quantity':existing.quantity,'product_id':existing.product_id})
        holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user.id,CustomerHolding.product_id==payload.product_id,CustomerHolding.status=='active').with_for_update())).scalar_one_or_none()
        if holding is None or holding.quantity < payload.quantity: raise HTTPException(409,detail={'code':'INSUFFICIENT_HOLDING','message':'insufficient holding quantity'})
        operation=BusinessOperation(id=str(uuid4()),user_id=user.id,operation_type='redeem',status='pending_review',idempotency_key=payload.idempotency_key,amount=payload.amount,quantity=payload.quantity,product_id=payload.product_id,payload_json=payload.model_dump_json())
        s.add(operation); s.add(AuditLog(id=str(uuid4()),actor_user_id=user.id,action='submit',resource_type='business_operation',resource_id=operation.id,detail='redeem submitted')); await s.flush(); await s.commit()
        return ApiResponse(data={'operation_id':operation.id,'operation':'redeem','status':operation.status,'product_id':payload.product_id,'quantity':payload.quantity,'amount':payload.amount})

@router.post('/redeem/{operation_id}/review',response_model=ApiResponse[dict])
async def review_redeem(request:Request,operation_id:str,payload:OperationReviewRequest,user:Annotated[User,Depends(staff_operation_review_user)]):
    if any(role.code=='customer' for role in user.roles): raise HTTPException(403,detail={'code':'STAFF_REQUIRED','message':'staff account required'})
    async with request.app.state.database.session_factory() as s:
        operation=(await s.execute(select(BusinessOperation).where(BusinessOperation.id==operation_id,BusinessOperation.operation_type=='redeem').with_for_update())).scalar_one_or_none()
        if operation is None: raise HTTPException(404,detail={'code':'OPERATION_NOT_FOUND','message':'operation not found'})
        if operation.status!='pending_review': raise HTTPException(409,detail={'code':'OPERATION_STATE_CONFLICT','message':'operation is not awaiting review'})
        risk_block = await _operation_risk_block(s, operation)
        if payload.approve and risk_block is not None:
            raise HTTPException(409, detail={'code':'RISK_CONTROL_BLOCKED','message':f'risk control blocks operation: {risk_block.rule_code}'})
        if not payload.approve:
            operation.status='rejected'; s.add(AuditLog(id=str(uuid4()),actor_user_id=user.id,action='reject',resource_type='business_operation',resource_id=operation.id,detail=payload.note)); await s.commit(); return ApiResponse(data={'operation_id':operation.id,'operation':'redeem','status':operation.status})
        holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==operation.user_id,CustomerHolding.product_id==operation.product_id,CustomerHolding.status=='active').with_for_update())).scalar_one_or_none()
        account=(await s.execute(select(Account).where(Account.user_id==operation.user_id).with_for_update())).scalar_one_or_none()
        if holding is None or holding.quantity < operation.quantity: raise HTTPException(409,detail={'code':'INSUFFICIENT_HOLDING','message':'insufficient holding quantity'})
        if account is None: raise HTTPException(404,detail={'code':'ACCOUNT_NOT_FOUND','message':'account not found'})
        holding.quantity-=operation.quantity; holding.market_value=max(Decimal('0'),Decimal(str(holding.market_value))-operation.amount); holding.cost_amount=max(Decimal('0'),Decimal(str(holding.cost_amount))-operation.amount)
        if holding.quantity==0: holding.status='closed'
        account.available_balance+=operation.amount
        s.add(MoneyFlow(id=str(uuid4()),customer_id=operation.user_id,account_id=account.id,amount=operation.amount,currency='CNY',flow_type='redeem',direction='in',channel='business_operation'))
        operation.status='settled'; await AssetSummaryService().snapshot(s, operation.user_id, source_type='redeem_settlement'); s.add(AuditLog(id=str(uuid4()),actor_user_id=user.id,action='settle',resource_type='business_operation',resource_id=operation.id,detail=payload.note)); await s.commit()
        return ApiResponse(data={'operation_id':operation.id,'operation':'redeem','status':operation.status,'amount':operation.amount,'quantity':operation.quantity,'product_id':operation.product_id})

@router.post('/staff/redeem',response_model=ApiResponse[dict])
async def staff_redeem(request:Request,payload:StaffRedeemRequest,user:Annotated[User,Depends(advisor_operation_submit_user)]):
    if any(role.code=='customer' for role in user.roles):
        raise HTTPException(403,detail={'code':'STAFF_REQUIRED','message':'staff account required'})
    if not payload.customer_confirmed:
        raise HTTPException(409,detail={'code':'CUSTOMER_CONFIRMATION_REQUIRED','message':'customer confirmation required'})
    async with request.app.state.database.session_factory() as s:
        customer=(await s.execute(select(User).options(selectinload(User.roles)).where(User.id==payload.customer_id))).scalar_one_or_none()
        if customer is None or not any(role.code=='customer' for role in customer.roles):
            raise HTTPException(404,detail={'code':'CUSTOMER_NOT_FOUND','message':'customer not found'})
        auth=(await s.execute(select(CustomerAdvisorAuthorization).where(CustomerAdvisorAuthorization.customer_id==payload.customer_id,CustomerAdvisorAuthorization.advisor_id==user.id,CustomerAdvisorAuthorization.status=='active'))).scalar_one_or_none()
        if auth is None: raise HTTPException(403,detail={'code':'ADVISOR_AUTHORIZATION_REQUIRED','message':'active advisor authorization required'})
        actions={x.strip() for x in (auth.allowed_actions or '').split(',')}
        if 'redeem' not in actions and '*' not in actions:
            raise HTTPException(403,detail={'code':'AUTHORIZED_ACTION_REQUIRED','message':'redeem is not authorized'})
        if auth.max_single_amount and payload.amount > Decimal(str(auth.max_single_amount)):
            raise HTTPException(409,detail={'code':'AUTHORIZATION_SINGLE_LIMIT_EXCEEDED','message':'single amount exceeds authorization limit'})
        existing=(await s.execute(select(BusinessOperation).where(BusinessOperation.user_id==payload.customer_id,BusinessOperation.idempotency_key==payload.idempotency_key))).scalar_one_or_none() if payload.idempotency_key else None
        if existing is not None:
            return ApiResponse(data={'operation_id':existing.id,'operation':existing.operation_type,'status':existing.status,'amount':existing.amount,'quantity':existing.quantity,'product_id':existing.product_id,'operator_user_id':existing.operator_user_id,'authorization_id':existing.authorization_id,'confirmation_mode':existing.confirmation_mode})
        holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==payload.customer_id,CustomerHolding.product_id==payload.product_id,CustomerHolding.status=='active').with_for_update())).scalar_one_or_none()
        if holding is None or holding.quantity < payload.quantity:
            raise HTTPException(409,detail={'code':'INSUFFICIENT_HOLDING','message':'insufficient holding quantity'})
        operation=BusinessOperation(id=str(uuid4()),user_id=payload.customer_id,operator_user_id=user.id,authorization_id=auth.id,confirmation_mode='staff_customer_confirmed',operation_type='redeem',status='pending_review',idempotency_key=payload.idempotency_key,amount=payload.amount,quantity=payload.quantity,product_id=payload.product_id,payload_json=payload.model_dump_json())
        s.add(operation); s.add(AuditLog(id=str(uuid4()),actor_user_id=user.id,action='submit',resource_type='business_operation',resource_id=operation.id,detail='staff redeem submitted')); await s.commit()
        return ApiResponse(data={'operation_id':operation.id,'operation':'redeem','status':operation.status,'amount':operation.amount,'quantity':operation.quantity,'product_id':operation.product_id,'operator_user_id':user.id,'authorization_id':auth.id,'confirmation_mode':operation.confirmation_mode})

@router.post('/transfer',response_model=ApiResponse[dict])
async def transfer(request:Request,payload:TransferRequest,user:Annotated[User,Depends(current_user)]):
    _customer(user)
    if payload.amount<=0: raise HTTPException(422,detail={'code':'INVALID_AMOUNT','message':'amount must be positive'})
    if payload.amount>100000 and not payload.confirmed: raise HTTPException(409,detail={'code':'SECOND_CONFIRMATION_REQUIRED','message':'second confirmation required'})
    async with request.app.state.database.session_factory() as s:
        if payload.idempotency_key:
            existing=(await s.execute(select(BusinessOperation).where(BusinessOperation.user_id==user.id,BusinessOperation.idempotency_key==payload.idempotency_key))).scalar_one_or_none()
            if existing is not None: return ApiResponse(data={'operation_id':existing.id,'operation':existing.operation_type,'status':existing.status,'amount':existing.amount,'to_account':existing.to_account})
        from app.api.trading import get_trading_service
        service=get_trading_service(request); account=await service.get_or_create_account(s,user.id)
        target=(await s.execute(select(Account).where(Account.account_no==payload.to_account))).scalar_one_or_none()
        if target is None: raise HTTPException(404,detail={'code':'TARGET_ACCOUNT_NOT_FOUND','message':'target account not found'})
        if target.id==account.id: raise HTTPException(409,detail={'code':'SELF_TRANSFER_FORBIDDEN','message':'cannot transfer to own account'})
        if account.available_balance<payload.amount: raise HTTPException(409,detail={'code':'INSUFFICIENT_BALANCE','message':'insufficient available balance'})
        operation=BusinessOperation(id=str(uuid4()),user_id=user.id,operation_type='transfer',status='pending_review',idempotency_key=payload.idempotency_key,amount=payload.amount,to_account=payload.to_account,payload_json=payload.model_dump_json())
        s.add(operation); s.add(AuditLog(id=str(uuid4()),actor_user_id=user.id,action='submit',resource_type='business_operation',resource_id=operation.id,detail='transfer submitted')); await s.flush(); await s.commit()
        return ApiResponse(data={'operation_id':operation.id,'operation':'transfer','status':operation.status,'amount':payload.amount,'to_account':payload.to_account})

@router.post('/staff/transfer',response_model=ApiResponse[dict])
async def staff_transfer(request:Request,payload:StaffTransferRequest,user:Annotated[User,Depends(staff_transfer_submit_user)]):
    if any(role.code=='customer' for role in user.roles):
        raise HTTPException(403,detail={'code':'STAFF_REQUIRED','message':'staff account required'})
    if not payload.customer_confirmed:
        raise HTTPException(409,detail={'code':'CUSTOMER_CONFIRMATION_REQUIRED','message':'customer confirmation required'})
    if payload.amount>100000 and not payload.confirmed:
        raise HTTPException(409,detail={'code':'SECOND_CONFIRMATION_REQUIRED','message':'second confirmation required'})
    async with request.app.state.database.session_factory() as s:
        customer=(await s.execute(select(User).options(selectinload(User.roles)).where(User.id==payload.customer_id))).scalar_one_or_none()
        if customer is None or not any(role.code=='customer' for role in customer.roles):
            raise HTTPException(404,detail={'code':'CUSTOMER_NOT_FOUND','message':'customer not found'})
        auth=(await s.execute(select(CustomerAdvisorAuthorization).where(CustomerAdvisorAuthorization.customer_id==payload.customer_id,CustomerAdvisorAuthorization.advisor_id==user.id,CustomerAdvisorAuthorization.status=='active'))).scalar_one_or_none()
        if auth is None:
            raise HTTPException(403,detail={'code':'ADVISOR_AUTHORIZATION_REQUIRED','message':'active advisor authorization required'})
        actions={x.strip() for x in (auth.allowed_actions or '').split(',')}
        if 'transfer' not in actions and '*' not in actions:
            raise HTTPException(403,detail={'code':'AUTHORIZED_ACTION_REQUIRED','message':'transfer is not authorized'})
        if auth.effective_to is not None:
            from datetime import datetime, timezone
            if auth.effective_to <= datetime.now(timezone.utc):
                raise HTTPException(403,detail={'code':'ADVISOR_AUTHORIZATION_EXPIRED','message':'advisor authorization expired'})
        if auth.max_single_amount and payload.amount > Decimal(str(auth.max_single_amount)):
            raise HTTPException(409,detail={'code':'AUTHORIZATION_SINGLE_LIMIT_EXCEEDED','message':'single amount exceeds authorization limit'})
        existing=(await s.execute(select(BusinessOperation).where(BusinessOperation.user_id==payload.customer_id,BusinessOperation.idempotency_key==payload.idempotency_key))).scalar_one_or_none() if payload.idempotency_key else None
        if existing is not None:
            return ApiResponse(data={'operation_id':existing.id,'operation':existing.operation_type,'status':existing.status,'amount':existing.amount,'to_account':existing.to_account,'operator_user_id':existing.operator_user_id,'authorization_id':existing.authorization_id,'confirmation_mode':existing.confirmation_mode})
        account=(await s.execute(select(Account).where(Account.user_id==payload.customer_id).with_for_update())).scalar_one_or_none()
        target=(await s.execute(select(Account).where(Account.account_no==payload.to_account))).scalar_one_or_none()
        if account is None: raise HTTPException(404,detail={'code':'ACCOUNT_NOT_FOUND','message':'customer account not found'})
        if target is None: raise HTTPException(404,detail={'code':'TARGET_ACCOUNT_NOT_FOUND','message':'target account not found'})
        if target.id==account.id: raise HTTPException(409,detail={'code':'SELF_TRANSFER_FORBIDDEN','message':'cannot transfer to own account'})
        if account.available_balance<payload.amount: raise HTTPException(409,detail={'code':'INSUFFICIENT_BALANCE','message':'insufficient available balance'})
        operation=BusinessOperation(id=str(uuid4()),user_id=payload.customer_id,operator_user_id=user.id,authorization_id=auth.id,confirmation_mode='staff_customer_confirmed',operation_type='transfer',status='pending_review',idempotency_key=payload.idempotency_key,amount=payload.amount,to_account=payload.to_account,payload_json=payload.model_dump_json())
        s.add(operation); s.add(AuditLog(id=str(uuid4()),actor_user_id=user.id,action='submit',resource_type='business_operation',resource_id=operation.id,detail='staff transfer submitted')); await s.commit()
        return ApiResponse(data={'operation_id':operation.id,'operation':'transfer','status':operation.status,'amount':operation.amount,'to_account':operation.to_account,'operator_user_id':user.id,'authorization_id':auth.id,'confirmation_mode':operation.confirmation_mode})

@router.post('/transfer/{operation_id}/review',response_model=ApiResponse[dict])
async def review_transfer(request:Request,operation_id:str,payload:OperationReviewRequest,user:Annotated[User,Depends(staff_operation_review_user)]):
    if any(role.code=='customer' for role in user.roles): raise HTTPException(403,detail={'code':'STAFF_REQUIRED','message':'staff account required'})
    async with request.app.state.database.session_factory() as s:
        operation=(await s.execute(select(BusinessOperation).where(BusinessOperation.id==operation_id,BusinessOperation.operation_type=='transfer').with_for_update())).scalar_one_or_none()
        if operation is None: raise HTTPException(404,detail={'code':'OPERATION_NOT_FOUND','message':'operation not found'})
        if operation.status!='pending_review': raise HTTPException(409,detail={'code':'OPERATION_STATE_CONFLICT','message':'operation is not awaiting review'})
        risk_block = await _operation_risk_block(s, operation)
        if payload.approve and risk_block is not None:
            raise HTTPException(409, detail={'code':'RISK_CONTROL_BLOCKED','message':f'risk control blocks operation: {risk_block.rule_code}'})
        if not payload.approve:
            operation.status='rejected'; s.add(AuditLog(id=str(uuid4()),actor_user_id=user.id,action='reject',resource_type='business_operation',resource_id=operation.id,detail=payload.note)); await s.commit(); return ApiResponse(data={'operation_id':operation.id,'operation':'transfer','status':'rejected'})
        account=(await s.execute(select(Account).where(Account.user_id==operation.user_id).with_for_update())).scalar_one_or_none()
        target=(await s.execute(select(Account).where(Account.account_no==operation.to_account).with_for_update())).scalar_one_or_none()
        if account is None or target is None: raise HTTPException(404,detail={'code':'ACCOUNT_NOT_FOUND','message':'account not found'})
        if account.available_balance<operation.amount: raise HTTPException(409,detail={'code':'INSUFFICIENT_BALANCE','message':'insufficient available balance'})
        account.available_balance-=operation.amount; target.available_balance+=operation.amount
        s.add(MoneyFlow(id=str(uuid4()),customer_id=operation.user_id,account_id=account.id,amount=operation.amount,currency='CNY',flow_type='transfer',direction='out',counterparty_id=target.id,channel='business_operation'))
        s.add(MoneyFlow(id=str(uuid4()),customer_id=target.user_id,account_id=target.id,amount=operation.amount,currency='CNY',flow_type='transfer',direction='in',counterparty_id=account.id,channel='business_operation'))
        operation.status='settled'; await AssetSummaryService().snapshot(s, operation.user_id, source_type='transfer_settlement'); await AssetSummaryService().snapshot(s, target.user_id, source_type='transfer_settlement'); s.add(AuditLog(id=str(uuid4()),actor_user_id=user.id,action='settle',resource_type='business_operation',resource_id=operation.id,detail=payload.note)); await s.commit()
        return ApiResponse(data={'operation_id':operation.id,'operation':'transfer','status':'settled','amount':operation.amount,'to_account':operation.to_account})



@router.get('/staff/pending',response_model=ApiResponse[dict])
async def list_staff_pending_operations(request:Request,operation_type:str='',limit:int=20,offset:int=0,user:Annotated[User,Depends(staff_operation_read_user)]=None):
    if any(role.code=='customer' for role in user.roles): raise HTTPException(403,detail={'code':'STAFF_REQUIRED','message':'staff account required'})
    if operation_type and operation_type not in {'redeem','transfer'}: raise HTTPException(422,detail={'code':'INVALID_OPERATION_TYPE','message':'operation_type must be redeem or transfer'})
    async with request.app.state.database.session_factory() as s:
        query=select(BusinessOperation).where(BusinessOperation.status=='pending_review',BusinessOperation.operation_type.in_(['redeem','transfer']))
        count_query=select(func.count()).select_from(BusinessOperation).where(BusinessOperation.status=='pending_review',BusinessOperation.operation_type.in_(['redeem','transfer']))
        if operation_type:
            query=query.where(BusinessOperation.operation_type==operation_type); count_query=count_query.where(BusinessOperation.operation_type==operation_type)
        total=int((await s.execute(count_query)).scalar_one())
        operations=list((await s.execute(query.order_by(BusinessOperation.created_at.asc()).offset(offset).limit(limit))).scalars().all())
        user_ids={op.user_id for op in operations}|{op.operator_user_id for op in operations if op.operator_user_id}
        users={row.id:row for row in (await s.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()} if user_ids else {}
        product_ids={op.product_id for op in operations if op.product_id}
        products={row.id:row for row in (await s.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()} if product_ids else {}
        customer_ids={op.user_id for op in operations}
        accounts={row.user_id:row for row in (await s.execute(select(Account).where(Account.user_id.in_(customer_ids)))).scalars().all()} if customer_ids else {}
        alerts={}
        if customer_ids:
            for alert in (await s.execute(select(RiskAlert).where(RiskAlert.customer_id.in_(customer_ids),RiskAlert.status.in_(['open','acknowledged','investigating','report_pending'])).order_by(RiskAlert.created_at.desc()))).scalars().all(): alerts.setdefault(alert.customer_id,alert)
        items=[]
        for op in operations:
            customer=users.get(op.user_id); operator=users.get(op.operator_user_id); alert=alerts.get(op.user_id); product=products.get(op.product_id); account=accounts.get(op.user_id)
            items.append({'operation_id':op.id,'operation':op.operation_type,'status':op.status,'amount':op.amount,'quantity':op.quantity,'product_id':op.product_id,'product_name':product.name if product else '', 'to_account':op.to_account,'customer_id':op.user_id,'customer_name':customer.display_name if customer else '', 'customer_username':customer.username if customer else '', 'operator_user_id':op.operator_user_id,'operator_name':operator.display_name if operator else '', 'authorization_id':op.authorization_id,'confirmation_mode':op.confirmation_mode,'created_at':op.created_at,'account_status':account.status if account else '', 'risk_status':'restricted' if account and account.risk_restriction_status=='restricted' else ('monitoring' if alert else 'normal'),'risk_rule_code':alert.rule_code if alert else '', 'risk_alert_level':alert.alert_level if alert else ''})
        return ApiResponse(data={'items':items,'total':total})

@router.get('/audit-logs',response_model=ApiResponse[dict])
async def list_operation_audits(request:Request,operation_id:str|None=None,action:str='',limit:int=50,offset:int=0,user:Annotated[User,Depends(staff_operation_audit_user)]=None):
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(422,detail={'code':'INVALID_PAGING','message':'invalid paging'})
    async with request.app.state.database.session_factory() as s:
        operation_filter = select(BusinessOperation.id).where(BusinessOperation.operation_type.in_(['redeem','transfer']))
        if operation_id:
            operation_filter = operation_filter.where(BusinessOperation.id == operation_id)
        if any(role.code == 'customer' for role in user.roles):
            operation_filter = operation_filter.where(BusinessOperation.user_id == user.id)
        else:
            # Operations staff review customer requests platform-wide. Advisor-originated
            # operations stay visible to their originating staff member as well.
            operation_filter = operation_filter.where((BusinessOperation.operator_user_id.is_(None)) | (BusinessOperation.operator_user_id == user.id) | (BusinessOperation.user_id == user.id))
        query=select(AuditLog).where(AuditLog.resource_type=='business_operation',AuditLog.resource_id.in_(operation_filter))
        count_query=select(func.count()).select_from(AuditLog).where(AuditLog.resource_type=='business_operation',AuditLog.resource_id.in_(operation_filter))
        if action.strip():
            query=query.where(AuditLog.action==action.strip()); count_query=count_query.where(AuditLog.action==action.strip())
        total=int((await s.execute(count_query)).scalar_one())
        rows=list((await s.execute(query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit))).scalars().all())
        operation_ids={row.resource_id for row in rows}
        operations={o.id:o for o in (await s.execute(select(BusinessOperation).where(BusinessOperation.id.in_(operation_ids)))).scalars().all()} if operation_ids else {}
        actor_ids={row.actor_user_id for row in rows if row.actor_user_id}
        actors={u.id:u for u in (await s.execute(select(User).where(User.id.in_(actor_ids)))).scalars().all()} if actor_ids else {}
        items=[]
        for row in rows:
            op=operations.get(row.resource_id); actor=actors.get(row.actor_user_id)
            items.append({'id':row.id,'operation_id':row.resource_id,'operation':op.operation_type if op else None,'operation_status':op.status if op else None,'customer_id':op.user_id if op else None,'operator_user_id':op.operator_user_id if op else None,'actor_user_id':row.actor_user_id,'actor_name':actor.display_name if actor else '系统','action':row.action,'detail':row.detail,'created_at':row.created_at})
        return ApiResponse(data={'items':items,'total':total})

@router.get('/me',response_model=ApiResponse[dict])
async def list_my_operations(request:Request,operation_type:str='',status:str='',limit:int=20,offset:int=0,user:Annotated[User,Depends(current_user)]=None):
    _customer(user)
    if operation_type and operation_type not in {'redeem','transfer'}: raise HTTPException(422,detail={'code':'INVALID_OPERATION_TYPE','message':'operation_type must be redeem or transfer'})
    if limit < 1 or limit > 100 or offset < 0: raise HTTPException(422,detail={'code':'INVALID_PAGING','message':'invalid paging'})
    async with request.app.state.database.session_factory() as s:
        query=select(BusinessOperation).where(BusinessOperation.user_id==user.id,BusinessOperation.operation_type.in_(['redeem','transfer']))
        count_query=select(func.count()).select_from(BusinessOperation).where(BusinessOperation.user_id==user.id,BusinessOperation.operation_type.in_(['redeem','transfer']))
        if operation_type:
            query=query.where(BusinessOperation.operation_type==operation_type); count_query=count_query.where(BusinessOperation.operation_type==operation_type)
        if status:
            query=query.where(BusinessOperation.status==status); count_query=count_query.where(BusinessOperation.status==status)
        total=int((await s.execute(count_query)).scalar_one())
        rows=list((await s.execute(query.order_by(BusinessOperation.created_at.desc()).offset(offset).limit(limit))).scalars().all())
        product_ids={row.product_id for row in rows if row.product_id}
        products={row.id:row for row in (await s.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()} if product_ids else {}
        operation_ids={row.id for row in rows}
        audits={}
        if operation_ids:
            for audit in (await s.execute(select(AuditLog).where(AuditLog.resource_type=='business_operation',AuditLog.resource_id.in_(operation_ids)).order_by(AuditLog.created_at.desc()))).scalars().all(): audits.setdefault(audit.resource_id,audit)
        items=[]
        for row in rows:
            audit=audits.get(row.id); product=products.get(row.product_id)
            items.append({'operation_id':row.id,'operation':row.operation_type,'status':row.status,'amount':row.amount,'quantity':row.quantity,'product_id':row.product_id,'product_name':product.name if product else '', 'to_account':row.to_account,'operator_user_id':row.operator_user_id,'authorization_id':row.authorization_id,'confirmation_mode':row.confirmation_mode,'latest_action':audit.action if audit else '', 'latest_note':audit.detail if audit else '', 'created_at':row.created_at,'updated_at':row.updated_at})
        return ApiResponse(data={'items':items,'total':total})

@router.get('/me/{operation_id}',response_model=ApiResponse[dict])
async def get_operation(request:Request,operation_id:str,user:Annotated[User,Depends(current_user)]):
    async with request.app.state.database.session_factory() as s:
        operation=(await s.execute(select(BusinessOperation).where(BusinessOperation.id==operation_id,BusinessOperation.user_id==user.id))).scalar_one_or_none()
        if operation is None: raise HTTPException(404,detail={'code':'OPERATION_NOT_FOUND','message':'operation not found'})
        return ApiResponse(data={'operation_id':operation.id,'operation':operation.operation_type,'status':operation.status,'amount':operation.amount,'quantity':operation.quantity,'product_id':operation.product_id,'operator_user_id':operation.operator_user_id,'authorization_id':operation.authorization_id,'confirmation_mode':operation.confirmation_mode})

@router.get('/staff/{operation_id}',response_model=ApiResponse[dict])
async def get_staff_operation(request:Request,operation_id:str,user:Annotated[User,Depends(staff_operation_read_user)]):
    if any(role.code=='customer' for role in user.roles): raise HTTPException(403,detail={'code':'STAFF_REQUIRED','message':'staff account required'})
    async with request.app.state.database.session_factory() as s:
        operation=(await s.execute(select(BusinessOperation).where(BusinessOperation.id==operation_id,BusinessOperation.operator_user_id==user.id))).scalar_one_or_none()
        if operation is None: raise HTTPException(404,detail={'code':'OPERATION_NOT_FOUND','message':'operation not found'})
        return ApiResponse(data={'operation_id':operation.id,'operation':operation.operation_type,'status':operation.status,'amount':operation.amount,'quantity':operation.quantity,'product_id':operation.product_id,'user_id':operation.user_id,'operator_user_id':operation.operator_user_id,'authorization_id':operation.authorization_id,'confirmation_mode':operation.confirmation_mode})


