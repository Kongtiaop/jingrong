from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

from sqlalchemy import select

from app.models.profile import CustomerAssetSnapshot, CustomerHolding, Product
from app.models.trading import Account, Order, RiskAlert
from app.models.workorder import WorkOrder


RouteSource = Literal["business_fact", "rag_explanation", "handoff"]


@dataclass(frozen=True)
class CustomerQueryRouteResult:
    source_type: RouteSource
    route: str
    answer: str
    data: dict[str, Any] = field(default_factory=dict)
    evidence_status: str = "sufficient"
    next_action: str = ""


class CustomerQueryRouter:
    """Deterministic first-pass router for customer-owned real-time business facts.

    This router intentionally handles only facts that can be read from approved business
    tables under the authenticated customer's data scope. All other questions continue
    through the existing public customer RAG flow.
    """

    _WORKORDER = ("工单", "服务单", "ticket")
    _ORDER = ("订单", "申购", "交易")
    _RISK = ("风险限制", "账户限制", "风控", "冻结", "受限")
    _ASSET = ("资产", "持仓", "余额", "账户余额", "净资产")
    _PRODUCT = ("产品状态", "是否在售", "在售", "停售", "产品详情")

    @classmethod
    def matches(cls, query: str) -> bool:
        return cls.detect_route(query) is not None

    @classmethod
    def detect_route(cls, query: str) -> str | None:
        normalized = re.sub(r"\s+", "", query.lower())
        if any(word in normalized for word in cls._WORKORDER):
            return "workorder_status"
        if any(word in normalized for word in cls._ORDER):
            return "order_status"
        if any(word in normalized for word in cls._RISK):
            return "risk_restriction"
        if any(word in normalized for word in cls._ASSET):
            return "asset_summary"
        if any(word in normalized for word in cls._PRODUCT):
            return "product_status"
        return None

    async def route(self, *, session, customer_id: str, query: str, entities: dict[str, str] | None = None) -> CustomerQueryRouteResult | None:
        route = self.detect_route(query)
        if route is None:
            # 裸产品评估句（如"现金管理保本计划靠谱吗"）：兜底路由到产品状态
            from app.services.customer_intent import is_bare_product_assessment
            if is_bare_product_assessment(query):
                return await self._product_status(session=session, query=query, product_name=(entities or {}).get("product_name"))
            return None
        if route == "workorder_status":
            return await self._workorders(session=session, customer_id=customer_id, ticket_no=(entities or {}).get("ticket_no"))
        if route == "order_status":
            return await self._orders(session=session, customer_id=customer_id, order_no=(entities or {}).get("order_no"))
        if route == "risk_restriction":
            return await self._risk_restriction(session=session, customer_id=customer_id)
        if route == "asset_summary":
            return await self._asset_summary(session=session, customer_id=customer_id)
        return await self._product_status(session=session, query=query, product_name=(entities or {}).get("product_name"))

    async def _workorders(self, *, session, customer_id: str, ticket_no: str | None = None) -> CustomerQueryRouteResult:
        rows = list((await session.execute(
            select(WorkOrder)
            .where(WorkOrder.customer_id == customer_id)
            .where(WorkOrder.ticket_no == ticket_no if ticket_no else True)
            .order_by(WorkOrder.updated_at.desc(), WorkOrder.created_at.desc())
            .limit(5)
        )).scalars().all())
        if not rows:
            return CustomerQueryRouteResult(
                source_type="business_fact", route="workorder_status", evidence_status="not_found",
                answer="您当前没有可查询的工单记录。", data={"items": [], "total": 0},
                next_action="如需人工协助，您可以继续描述问题，系统会按规则创建服务工单。",
            )
        items = [
            {"id": row.id, "ticket_no": row.ticket_no, "status": row.status, "title": row.title,
             "priority": row.priority, "updated_at": row.updated_at.isoformat() if row.updated_at else None}
            for row in rows
        ]
        latest = items[0]
        return CustomerQueryRouteResult(
            source_type="business_fact", route="workorder_status", evidence_status="sufficient",
            answer=f"您最近的工单 {latest['ticket_no']} 当前状态为“{latest['status']}”。",
            data={"items": items, "total": len(items)},
            next_action="如需补充材料，请在“我的工单”中查看处理要求。",
        )

    async def _orders(self, *, session, customer_id: str, order_no: str | None = None) -> CustomerQueryRouteResult:
        rows = list((await session.execute(
            select(Order, Product.name)
            .join(Product, Product.id == Order.product_id)
            .where(Order.user_id == customer_id)
            .where(Order.order_no == order_no if order_no else True)
            .order_by(Order.updated_at.desc(), Order.created_at.desc())
            .limit(5)
        )).all())
        if not rows:
            return CustomerQueryRouteResult(
                source_type="business_fact", route="order_status", evidence_status="not_found",
                answer="您当前没有可查询的订单记录。", data={"items": [], "total": 0},
                next_action="如需发起业务操作，请通过受控业务页面完成确认。",
            )
        items = [
            {"id": order.id, "order_no": order.order_no, "product_id": order.product_id,
             "product_name": name, "status": order.status, "side": order.side,
             "amount": float(order.amount), "updated_at": order.updated_at.isoformat() if order.updated_at else None}
            for order, name in rows
        ]
        latest = items[0]
        return CustomerQueryRouteResult(
            source_type="business_fact", route="order_status", evidence_status="sufficient",
            answer=f"您最近的订单 {latest['order_no']} 当前状态为“{latest['status']}”。",
            data={"items": items, "total": len(items)},
            next_action="订单状态以系统实时记录为准；如需了解流程规则，可继续询问具体状态含义。",
        )

    async def _risk_restriction(self, *, session, customer_id: str) -> CustomerQueryRouteResult:
        account = (await session.execute(select(Account).where(Account.user_id == customer_id))).scalar_one_or_none()
        alert = (await session.execute(
            select(RiskAlert)
            .where(RiskAlert.customer_id == customer_id, RiskAlert.status.in_(["open", "acknowledged", "investigating", "report_pending"]))
            .order_by(RiskAlert.created_at.desc()).limit(1)
        )).scalars().first()
        restricted = account is not None and account.risk_restriction_status == "restricted"
        data = {
            "account_status": account.status if account else "not_found",
            "restriction_status": account.risk_restriction_status if account else "none",
            "restriction_reason": account.risk_restriction_reason if account else "",
            "restricted_until": account.risk_restricted_until.isoformat() if account and account.risk_restricted_until else None,
            "latest_alert": None if alert is None else {"id": alert.id, "rule_code": alert.rule_code, "status": alert.status, "level": alert.alert_level},
        }
        if restricted:
            return CustomerQueryRouteResult(
                source_type="business_fact", route="risk_restriction", evidence_status="sufficient",
                answer="您的账户当前存在风险限制，请以系统展示的限制原因和处理要求为准。",
                data=data, next_action="如需解除限制，请按风险处理要求补充材料或联系人工客服。",
            )
        if alert:
            return CustomerQueryRouteResult(
                source_type="business_fact", route="risk_restriction", evidence_status="sufficient",
                answer="您的账户当前未显示风险限制，但存在一条待处理风险记录。",
                data=data, next_action="请关注风险处理进度；如需说明，可继续咨询具体规则或联系人工客服。",
            )
        return CustomerQueryRouteResult(
            source_type="business_fact", route="risk_restriction", evidence_status="sufficient",
            answer="您的账户当前未显示风险限制。",
            data=data, next_action="如系统状态变化，请以最新账户与风险记录为准。",
        )

    async def _asset_summary(self, *, session, customer_id: str) -> CustomerQueryRouteResult:
        asset = (await session.execute(
            select(CustomerAssetSnapshot)
            .where(CustomerAssetSnapshot.user_id == customer_id)
            .order_by(CustomerAssetSnapshot.snapshot_time.desc()).limit(1)
        )).scalars().first()
        holdings = list((await session.execute(
            select(CustomerHolding, Product.name)
            .join(Product, Product.id == CustomerHolding.product_id)
            .where(CustomerHolding.user_id == customer_id, CustomerHolding.status == "active")
            .order_by(CustomerHolding.market_value.desc()).limit(10)
        )).all())
        holding_items = [
            {"id": holding.id, "product_id": holding.product_id, "product_name": name, "market_value": float(holding.market_value),
             "profit_loss": float(holding.profit_loss), "quantity": float(holding.quantity)}
            for holding, name in holdings
        ]
        data: dict[str, Any] = {"latest_asset": None, "holdings": holding_items}
        if asset:
            data["latest_asset"] = {
                "total_asset": float(asset.total_asset), "cash_balance": float(asset.cash_balance),
                "investable_asset": float(asset.investable_asset), "liability": float(asset.liability),
                "net_asset": float(asset.net_asset),
                "snapshot_time": asset.snapshot_time.isoformat() if asset.snapshot_time else None,
            }
            answer = f"您最近一次资产快照的总资产为 {float(asset.total_asset):,.2f} 元，当前展示 {len(holding_items)} 条有效持仓。"
            status = "sufficient"
        elif holding_items:
            answer = f"当前未找到资产快照，但查询到 {len(holding_items)} 条有效持仓记录。"
            status = "partial"
        else:
            answer = "当前未找到资产快照或有效持仓记录。"
            status = "not_found"
        return CustomerQueryRouteResult(
            source_type="business_fact", route="asset_summary", evidence_status=status,
            answer=answer, data=data,
            next_action="资产和持仓金额以系统实时业务数据为准，不构成投资建议。",
        )

    async def _product_status(self, *, session, query: str, product_name: str | None = None) -> CustomerQueryRouteResult:
        normalized = re.sub(r"\s+", "", query)
        rows = list((await session.execute(
            select(Product)
            .where(Product.deleted_at.is_(None), Product.status.in_(["active", "inactive", "paused", "sold_out"]))
            .order_by(Product.updated_at.desc()).limit(20)
        )).scalars().all())
        matched = [row for row in rows if row.name.replace(" ", "") in normalized or normalized in row.name.replace(" ", "")]
        if not matched:
            return CustomerQueryRouteResult(
                source_type="business_fact", route="product_status", evidence_status="needs_clarification",
                answer="我可以查询产品当前状态，但请提供明确的产品名称。",
                data={"items": []}, next_action="请补充产品名称；产品推荐仍需经过适当性服务判断。",
            )
        if len(matched) > 1 and product_name:
            return CustomerQueryRouteResult(
                source_type="business_fact", route="product_status", evidence_status="needs_clarification",
                answer="匹配到多个同名产品，请补充产品类型、风险等级或产品编号。",
                data={"items": [{"id": row.id, "name": row.name, "status": row.status, "risk_level": row.risk_level} for row in matched[:5]]},
                next_action="请补充更具体的产品信息。",
            )
        items = [
            {"id": row.id, "name": row.name, "status": row.status, "risk_level": row.risk_level,
             "minimum_amount": float(row.minimum_amount), "liquidity": row.liquidity}
            for row in matched[:5]
        ]
        first = items[0]
        return CustomerQueryRouteResult(
            source_type="business_fact", route="product_status", evidence_status="sufficient",
            answer=f"产品“{first['name']}”当前状态为“{first['status']}”，风险等级为 {first['risk_level']}。",
            data={"items": items},
            next_action="产品状态以实时系统记录为准；是否适合您仍需经过适当性评估。",
        )


