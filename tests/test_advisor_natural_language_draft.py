# -*- coding: utf-8 -*-
"""投顾代客下单（自然语言）后端契约：schema 与决策分支映射。

不依赖 DB —— 只验证请求/响应 schema、决策常量与辅助纯函数，
覆盖能力②的“草稿+风控+客户确认”在 API 层的形状契约。
"""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.profile import (
    AdvisorNaturalLanguageDraftRequest,
    AdvisorNaturalLanguageDraftResponse,
    AdvisorNaturalLanguageProductOption,
)
from app.services.natural_language_order_draft import NaturalLanguageOrderDraftService


def test_nl_draft_request_requires_customer_and_query():
    req = AdvisorNaturalLanguageDraftRequest(customer_id="cust-1", query="代客户申购“稳健增值计划”10万元")
    assert req.customer_id == "cust-1"
    assert "稳健增值计划" in req.query


def test_nl_draft_request_rejects_empty_query():
    with pytest.raises(ValidationError):
        AdvisorNaturalLanguageDraftRequest(customer_id="cust-1", query="")


def test_nl_draft_response_statuses_and_draft_shape():
    draft = {
        "id": "d1", "customer_id": "cust-1", "advisor_id": "adv-1", "product_id": "p1",
        "side": "buy", "amount": 100000.0, "status": "pending_customer_confirmation", "note": "x",
    }
    resp = AdvisorNaturalLanguageDraftResponse(
        status="awaiting_customer_confirmation", customer_id="cust-1", advisor_id="adv-1",
        clarification="已生成草稿", amount=100000.0, draft=draft,
    )
    assert resp.status == "awaiting_customer_confirmation"
    assert resp.draft is not None and resp.draft.amount == 100000.0
    assert resp.draft.status == "pending_customer_confirmation"


def test_nl_draft_response_product_options():
    opt = AdvisorNaturalLanguageProductOption(id="p1", name="稳健增值计划", risk_level="C2", minimum_amount=10000.0)
    resp = AdvisorNaturalLanguageDraftResponse(
        status="needs_clarification", missing_fields=["amount"], amount=100000.0,
        product_options=[opt],
    )
    assert resp.product_options[0].name == "稳健增值计划"
    assert resp.missing_fields == ["amount"]


def test_nl_parse_reused_for_advisor_phrasing():
    # 能力②复用 customer NL parser：提取引号产品 + 金额（投顾“代客户申购…”句式也应解析）
    parsed = NaturalLanguageOrderDraftService.parse_purchase_intent("代客户申购“稳健增值计划”10万元")
    assert parsed.product_name == "稳健增值计划"
    assert parsed.amount == Decimal("100000")
