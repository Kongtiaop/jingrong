# -*- coding: utf-8 -*-
"""advisor 分析助手的后端授权守卫 _can_analyze_target 单测。"""
import asyncio
from types import SimpleNamespace

from app.api.customer_service import _can_analyze_target


def _user(*, super_admin=False, roles=(), uid="u1"):
    return SimpleNamespace(is_super_admin=super_admin, roles=[SimpleNamespace(code=c) for c in roles], id=uid)


def _can(session, user, target):
    return asyncio.run(_can_analyze_target(session, user, target))


def test_super_admin_can_target_any():
    assert _can(None, _user(super_admin=True), "cust-x") is True


def test_customer_cannot_target_another():
    # 客户不能带 target 去读他人（连自己走 target 也不允许，避免被当 staff 分析）
    u = _user(roles=("customer",), uid="me")
    assert _can(None, u, "cust-x") is False


def test_no_target_rejected():
    assert _can(None, _user(super_admin=True), None) is False


def test_staff_not_assigned_cannot_target_other_customer():
    # 普通 staff（非 advisor/customer_manager、非超管）授权集只有自己 → 不能 target 他人
    u = _user(roles=("ops",), uid="staff-1")
    assert _can(None, u, "cust-x") is False


def test_advisor_only_within_assigned_customers():
    # advisor：授权集由 fake session 返回 assigned customer；在集合内放行、集合外拒绝
    assigned = {"cust-a", "cust-b"}

    class FakeRows:
        def scalars(self):
            return self

        def all(self):
            return assigned

    class FakeSession:
        async def execute(self, _stmt):
            return FakeRows()

    advisor = _user(roles=("advisor",), uid="adv-1")
    assert _can(FakeSession(), advisor, "cust-a") is True
    assert _can(FakeSession(), advisor, "cust-c") is False
