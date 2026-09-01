from __future__ import annotations

import re
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.profile import Product
from app.models.trading import NaturalLanguageOrderDraft
from app.schemas.trading import (
    NaturalLanguageOrderDraftConfirmRequest,
    NaturalLanguageOrderDraftPersistedResponse,
    NaturalLanguageOrderDraftResponse,
    OrderDraftProductOption,
)
from app.services.suitability_service import SuitabilityService


@dataclass(frozen=True)
class ParsedPurchaseIntent:
    product_name: str | None
    amount: Decimal | None


class NaturalLanguageOrderDraftService:
    """Create a non-executable purchase draft from explicit customer language.

    This service deliberately accepts only purchase intents in the MVP. It never creates
    an Order, freezes funds, or performs a state transition.
    """

    BUY_MARKERS = ("申购", "购买", "买入", "下单")
    UNSUPPORTED_MARKERS = ("赎回", "卖出", "转换", "转出")

    def __init__(self, trading_service, suitability_service: SuitabilityService | None = None):
        self.trading_service = trading_service
        self.suitability_service = suitability_service or SuitabilityService(
            risk_repository=trading_service.unit_of_work.risks,
            product_repository=trading_service.unit_of_work.products,
            profile_repository=trading_service.unit_of_work.profiles,
        )

    @classmethod
    def parse_purchase_intent(cls, query: str) -> ParsedPurchaseIntent:
        text = re.sub(r"\s+", "", query)
        product_name = None
        quoted = re.search(r"[“\"]([^”\"]+)[”\"]", query)
        if quoted:
            product_name = quoted.group(1).strip()
        else:
            # Support both explicit orderings without guessing: "申购 产品 1000元"
            # and "申购 1000元 产品". Amount tokens are excluded from the product span.
            action_match = re.search(r"(?:申购|购买|买入|下单)", text)
            if action_match:
                tail = text[action_match.end():]
                tail_without_amount = re.sub(r"[0-9]+(?:\.[0-9]+)?(?:万|元)", "", tail)
                candidate = tail_without_amount.strip("：:-，,。；;的")
                if candidate:
                    product_name = candidate
            if not product_name:
                before_action = re.search(r"(.+?)(?:申购|购买|买入|下单)", text)
                if before_action:
                    candidate = before_action.group(1).strip("：:-，,。；;的我要帮我")
                    if candidate:
                        product_name = candidate

        amount = None
        amount_match = re.search(r"([0-9]+(?:\.[0-9]+)?)(万|元)", text)
        if amount_match:
            try:
                value = Decimal(amount_match.group(1))
                amount = value * Decimal("10000") if amount_match.group(2) == "万" else value
            except InvalidOperation:
                amount = None
        return ParsedPurchaseIntent(product_name=product_name, amount=amount)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _fingerprint(*, customer_id: str, product_id: str, amount: Decimal, version: int) -> str:
        raw = f"{customer_id}|{product_id}|buy|{amount:.2f}|{version}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _persist_confirmable_draft(self, *, session, user_id: str, response: NaturalLanguageOrderDraftResponse) -> NaturalLanguageOrderDraftPersistedResponse:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        version = 1
        fingerprint = self._fingerprint(customer_id=user_id, product_id=response.product_id, amount=response.amount, version=version)
        snapshot = {
            "product_id": response.product_id,
            "product_name": response.product_name,
            "amount": str(response.amount),
            "side": response.side,
            "suitable": response.suitable,
            "suitability_reasons": response.suitability_reasons,
            "warnings": response.warnings,
            "precheck_passed": response.precheck_passed,
        }
        draft = NaturalLanguageOrderDraft(
            id=str(uuid4()), customer_id=user_id, product_id=response.product_id, side="buy", amount=response.amount,
            version=version, status="awaiting_user_confirmation", input_fingerprint=fingerprint,
            confirmation_token_hash=self._token_hash(token), precheck_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            expires_at=expires_at,
        )
        session.add(draft)
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user_id, action="create_draft", resource_type="natural_language_order_draft", resource_id=draft.id, detail=json.dumps({"fingerprint": fingerprint, "product_id": response.product_id, "amount": str(response.amount)}, ensure_ascii=False)))
        await session.flush()
        return NaturalLanguageOrderDraftPersistedResponse(
            **response.model_dump(), draft_id=draft.id, version=version, confirmation_token=token, expires_at=expires_at
        )

    async def draft(self, *, session, user_id: str, query: str) -> NaturalLanguageOrderDraftResponse:
        normalized = re.sub(r"\s+", "", query)
        if any(marker in normalized for marker in self.UNSUPPORTED_MARKERS):
            return NaturalLanguageOrderDraftResponse(
                status="needs_clarification",
                clarification="当前自然语言交易 MVP 仅支持申购。赎回、卖出和转换需通过受控业务页面办理。",
                warnings=["本次不会创建订单或执行交易。"],
            )
        if not any(marker in normalized for marker in self.BUY_MARKERS):
            return NaturalLanguageOrderDraftResponse(
                status="needs_clarification",
                clarification="请明确是否要申购，以及产品名称和申购金额。",
                warnings=["本次不会创建订单或执行交易。"],
            )

        intent = self.parse_purchase_intent(query)
        missing: list[str] = []
        if not intent.product_name:
            missing.append("product_name")
        if intent.amount is None:
            missing.append("amount")
        if missing:
            message = "请补充" + ("产品名称" if "product_name" in missing else "")
            if len(missing) == 2:
                message += "和"
            if "amount" in missing:
                message += "申购金额"
            message += "，例如：申购“稳健精选基金”10万元。"
            return NaturalLanguageOrderDraftResponse(
                status="needs_clarification",
                missing_fields=missing,
                clarification=message,
                warnings=["关键交易参数必须由客户明确提供，系统不会猜测。"],
            )

        rows = list((await session.execute(
            select(Product).where(Product.deleted_at.is_(None), Product.status == "active")
        )).scalars().all())
        normalized_name = intent.product_name.replace(" ", "")
        matches = [p for p in rows if normalized_name == p.name.replace(" ", "")]
        if not matches:
            partial = [p for p in rows if normalized_name in p.name.replace(" ", "") or p.name.replace(" ", "") in normalized_name]
            options = [OrderDraftProductOption(id=p.id, name=p.name, risk_level=p.risk_level, minimum_amount=p.minimum_amount) for p in partial[:5]]
            return NaturalLanguageOrderDraftResponse(
                status="needs_clarification", amount=intent.amount,
                missing_fields=["product_name"],
                clarification="未找到唯一匹配的在售产品，请从候选产品中选择或补充完整产品名称。",
                product_options=options, warnings=["本次不会创建订单或执行交易。"],
            )
        if len(matches) > 1:
            options = [OrderDraftProductOption(id=p.id, name=p.name, risk_level=p.risk_level, minimum_amount=p.minimum_amount) for p in matches[:5]]
            return NaturalLanguageOrderDraftResponse(
                status="needs_clarification", amount=intent.amount,
                missing_fields=["product_name"],
                clarification="匹配到多个在售产品，请选择具体产品。", product_options=options,
                warnings=["本次不会创建订单或执行交易。"],
            )

        product = matches[0]
        warnings: list[str] = ["本结果为订单草稿和预检，不会创建订单、冻结资金或执行交易。"]
        if intent.amount < Decimal(str(product.minimum_amount)):
            return NaturalLanguageOrderDraftResponse(
                status="precheck_failed", product_id=product.id, product_name=product.name, amount=intent.amount,
                suitable=None, precheck_passed=False,
                clarification=f"该产品最低申购金额为 {product.minimum_amount} 元，请调整金额后重新确认。",
                warnings=warnings,
            )

        account = await self.trading_service.get_or_create_account(session, user_id)
        if account.status != "active" or getattr(account, "risk_restriction_status", "none") == "restricted":
            return NaturalLanguageOrderDraftResponse(
                status="precheck_failed", product_id=product.id, product_name=product.name, amount=intent.amount,
                suitable=None, precheck_passed=False,
                clarification="账户当前受风险控制限制，不能提交交易草稿，请先处理风险限制。", warnings=warnings,
            )
        if account.available_balance < intent.amount:
            return NaturalLanguageOrderDraftResponse(
                status="precheck_failed", product_id=product.id, product_name=product.name, amount=intent.amount,
                suitable=None, precheck_passed=False,
                clarification="账户可用余额不足，无法形成可提交的申购草稿。", warnings=warnings,
            )

        recommendation = await self.suitability_service.recommend(session, user_id)
        item = next((row for row in (recommendation.matches + recommendation.excluded) if row.product_id == product.id), None)
        suitable = bool(item and item.matched)
        reasons = list(item.reasons) if item else ["未找到该产品的适当性结果。"]
        warnings.extend(w.message for w in recommendation.warnings)
        if not suitable:
            return NaturalLanguageOrderDraftResponse(
                status="precheck_failed", product_id=product.id, product_name=product.name, amount=intent.amount,
                suitable=False, suitability_reasons=reasons, precheck_passed=False,
                clarification="该产品当前未通过适当性校验，系统不能继续生成可确认订单。", warnings=warnings,
            )

        response = NaturalLanguageOrderDraftResponse(
            status="awaiting_user_confirmation", product_id=product.id, product_name=product.name, amount=intent.amount,
            suitable=True, suitability_reasons=reasons, precheck_passed=True, warnings=warnings,
            clarification="预检通过。请在确认页核对产品、金额、风险提示和适当性结果；确认后系统才会创建待确认订单。",
        )
        return await self._persist_confirmable_draft(session=session, user_id=user_id, response=response)

    async def confirm(self, *, session, user, draft_id: str, payload: NaturalLanguageOrderDraftConfirmRequest):
        from sqlalchemy import select

        draft = (await session.execute(
            select(NaturalLanguageOrderDraft).where(
                NaturalLanguageOrderDraft.id == draft_id,
                NaturalLanguageOrderDraft.customer_id == user.id,
            ).with_for_update()
        )).scalar_one_or_none()
        if draft is None:
            raise ValueError("draft not found")
        if draft.order_id:
            order = await self.trading_service.unit_of_work.orders.get_for_user(session, draft.order_id, user.id)
            if order is not None:
                return order
        if draft.status != "awaiting_user_confirmation":
            raise ValueError("draft cannot be confirmed")
        now = datetime.now(timezone.utc)
        expires_at = draft.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            draft.status = "expired"
            raise ValueError("draft confirmation expired")
        if not secrets.compare_digest(self._token_hash(payload.confirmation_token), draft.confirmation_token_hash):
            raise ValueError("invalid draft confirmation token")

        # Re-run the deterministic controls immediately before an order is created.
        product = await self.trading_service.unit_of_work.products.get_active(session, draft.product_id)
        if product is None:
            raise ValueError("product unavailable")
        account = await self.trading_service.get_or_create_account(session, user.id)
        if account.status != "active" or getattr(account, "risk_restriction_status", "none") == "restricted":
            raise ValueError("account restricted by risk control")
        if account.available_balance < draft.amount:
            raise ValueError("insufficient available balance")
        recommendation = await self.suitability_service.recommend(session, user.id)
        item = next((row for row in (recommendation.matches + recommendation.excluded) if row.product_id == draft.product_id), None)
        if item is None or not item.matched:
            raise ValueError("product is not suitable for customer")

        idempotency_key = payload.idempotency_key or f"nl-draft:{draft.id}:v{draft.version}"
        order, _ = await self.trading_service.create_order(
            session, user, draft.product_id, draft.amount, idempotency_key,
            order_source="customer_direct", confirmation_mode="nl_draft",
        )
        draft.order_id = order.id
        draft.status = "confirmed_order_created"
        draft.confirmed_at = now
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="confirm_draft", resource_type="natural_language_order_draft", resource_id=draft.id, detail=json.dumps({"order_id": order.id, "fingerprint": draft.input_fingerprint}, ensure_ascii=False)))
        await session.flush()
        return order

