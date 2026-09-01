"""bad case 数据标注接口（数据飞轮 · 标注闭环）。

提供：bad case 列表/筛选、人工标注正确意图、跳过、以及把已标注结果导出成
黄金评测集格式（可下载后并入 scripts/intent_eval_cases.json）。

全部接口仅限超级管理员（模型治理，不开放普通员工）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB

from app.common.response import ApiResponse
from app.common.security.auth import current_user
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.bad_case import BadCase
from app.schemas.annotation import (
    AnnotationSummaryResponse,
    BadCaseListResponse,
    BadCaseResponse,
    LabelBadCaseRequest,
    LabelBadCaseResponse,
)

router = APIRouter(prefix="/admin/annotation", tags=["admin", "annotation"])

VALID_INTENTS = {
    "order_status", "workorder_status", "asset_summary", "product_status",
    "product_recommendation", "business_operation", "risk_public", "risk_explanation",
    "risk_restriction", "policy", "faq", "human_handoff", "unknown",
    "company_info", "company_business", "workflow",
}


async def super_admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_super_admin and not any(role.code == "super_admin" for role in user.roles):
        raise HTTPException(status_code=403, detail={"code": "SUPER_ADMIN_REQUIRED", "message": "super admin required"})
    return user


def _bad_case_response(row: BadCase) -> BadCaseResponse:
    return BadCaseResponse(
        id=str(row.id), query=row.query, intent=row.intent, confidence=row.confidence,
        evidence_status=row.evidence_status, handoff=row.handoff, clarifications=row.clarifications,
        reasons=list(row.reasons or []), status=row.status, created_at=row.created_at,
        labeled_intent=row.labeled_intent or "", requires_clarification=bool(row.requires_clarification),
        category=row.category or "", note=row.note or "", split=row.split or "train",
        labeled_by=row.labeled_by, labeled_at=row.labeled_at,
    )


@router.get("/summary", response_model=ApiResponse[AnnotationSummaryResponse])
async def annotation_summary(request: Request, user: Annotated[User, Depends(super_admin_user)]):
    async with request.app.state.database.session_factory() as session:
        total = int((await session.execute(select(func.count()).select_from(BadCase))).scalar_one())
        pending = int((await session.execute(select(func.count()).select_from(BadCase).where(BadCase.status == "pending"))).scalar_one())
        reviewed = int((await session.execute(select(func.count()).select_from(BadCase).where(BadCase.status == "reviewed"))).scalar_one())
        labeled = int((await session.execute(select(func.count()).select_from(BadCase).where(BadCase.status == "reviewed", BadCase.labeled_intent != ""))).scalar_one())
        discarded = int((await session.execute(select(func.count()).select_from(BadCase).where(BadCase.status == "reviewed", BadCase.labeled_intent == ""))).scalar_one())
        return ApiResponse(data=AnnotationSummaryResponse(pending=pending, reviewed=reviewed, labeled=labeled, discarded=discarded))


@router.get("/bad-cases", response_model=ApiResponse[BadCaseListResponse])
async def list_bad_cases(
    request: Request,
    status: str = Query("pending", pattern="^(|pending|reviewed|all)$"),
    reason: str = Query(""),
    q: str = Query(""),
    split: str = Query(""),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: Annotated[User, Depends(super_admin_user)] = None,
):
    async with request.app.state.database.session_factory() as session:
        query = select(BadCase)
        count_query = select(func.count()).select_from(BadCase)
        criteria = []
        if status and status != "all":
            criteria.append(BadCase.status == status)
        if reason:
            criteria.append(BadCase.reasons.cast(JSONB).contains([reason]))
        if q.strip():
            criteria.append(BadCase.query.contains(q.strip()))
        if split.strip():
            criteria.append(BadCase.split == split.strip())
        if criteria:
            query = query.where(*criteria)
            count_query = count_query.where(*criteria)
        total = int((await session.execute(count_query)).scalar_one())
        rows = list((await session.execute(
            query.order_by(BadCase.created_at.desc()).offset(offset).limit(limit))).scalars().all())
        return ApiResponse(data=BadCaseListResponse(items=[_bad_case_response(r) for r in rows], total=total))


@router.post("/bad-cases/{bad_case_id}/label", response_model=ApiResponse[LabelBadCaseResponse])
async def label_bad_case(
    request: Request,
    bad_case_id: str,
    body: LabelBadCaseRequest,
    user: Annotated[User, Depends(super_admin_user)],
):
    now = datetime.now(timezone.utc)
    async with request.app.state.database.session_factory() as session:
        row = await session.get(BadCase, bad_case_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "BAD_CASE_NOT_FOUND", "message": "bad case not found"})
        discard = bool(body.discard)
        intent = (body.intent or "").strip()
        if not discard:
            if not intent:
                raise HTTPException(status_code=422, detail={"code": "INTENT_REQUIRED", "message": "labeled intent is required (or set discard=true)"})
            if intent not in VALID_INTENTS:
                raise HTTPException(status_code=422, detail={"code": "INVALID_INTENT", "message": f"intent must be one of: {','.join(sorted(VALID_INTENTS))}"})
            if body.split not in {"train", "val", "test"}:
                raise HTTPException(status_code=422, detail={"code": "INVALID_SPLIT", "message": "split must be train/val/test"})
            row.labeled_intent = intent
            row.requires_clarification = bool(body.requires_clarification)
            row.category = (body.category or "").strip()
            row.note = (body.note or "").strip()
            row.split = body.split
        else:
            row.labeled_intent = ""
        row.status = "reviewed"
        row.labeled_by = user.id
        row.labeled_at = now
        session.add(AuditLog(
            id=str(uuid4()), actor_user_id=user.id, action="annotation.bad_case_labeled",
            resource_type="bad_case", resource_id=str(row.id),
            detail=f"intent={row.labeled_intent};split={row.split};discard={discard}",
        ))
        await session.commit()
        await session.refresh(row)
        return ApiResponse(data=LabelBadCaseResponse(
            id=str(row.id), status=row.status, labeled_intent=row.labeled_intent, split=row.split))


@router.post("/bad-cases/{bad_case_id}/skip", response_model=ApiResponse[LabelBadCaseResponse])
async def skip_bad_case(request: Request, bad_case_id: str, user: Annotated[User, Depends(super_admin_user)]):
    now = datetime.now(timezone.utc)
    async with request.app.state.database.session_factory() as session:
        row = await session.get(BadCase, bad_case_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "BAD_CASE_NOT_FOUND", "message": "bad case not found"})
        row.status = "reviewed"
        row.labeled_by = user.id
        row.labeled_at = now
        session.add(AuditLog(
            id=str(uuid4()), actor_user_id=user.id, action="annotation.bad_case_skipped",
            resource_type="bad_case", resource_id=str(row.id), detail=f"query={row.query[:80]}",
        ))
        await session.commit()
        await session.refresh(row)
        return ApiResponse(data=LabelBadCaseResponse(
            id=str(row.id), status=row.status, labeled_intent=row.labeled_intent or "", split=row.split))


@router.get("/export-labeled", response_model=ApiResponse[list[dict]])
async def export_labeled(request: Request, user: Annotated[User, Depends(super_admin_user)]):
    """导出已标注为黄金评测集格式（intent_eval_cases.json 的 cases 数组）。"""
    async with request.app.state.database.session_factory() as session:
        rows = list((await session.execute(
            select(BadCase).where(BadCase.status == "reviewed", BadCase.labeled_intent != "")
            .order_by(BadCase.labeled_at.asc()))).scalars().all())
        cases = [
            {
                "id": f"annotated_{str(r.id)[:8]}",
                "split": r.split or "train",
                "query": r.query,
                "category": (r.category or "annotated"),
                "expected": {"intents": [r.labeled_intent], "requires_clarification": bool(r.requires_clarification)},
                "note": r.note or "线上 bad case 人工标注",
            }
            for r in rows
        ]
        return ApiResponse(data=cases)


@router.get("/export-pending", response_model=ApiResponse[list[dict]])
async def export_pending(request: Request, limit: int = Query(2000, ge=1, le=10000), user: Annotated[User, Depends(super_admin_user)] = None):
    """导出未标注 bad case（原始字段，供外部标注或留存）。"""
    async with request.app.state.database.session_factory() as session:
        rows = list((await session.execute(
            select(BadCase).where(BadCase.status == "pending").order_by(BadCase.created_at.asc()).limit(limit))).scalars().all())
        return ApiResponse(data=[_bad_case_response(r).model_dump() for r in rows])
