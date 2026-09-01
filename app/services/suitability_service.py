from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.profile import ProductSuitabilityRule
from app.repositories.trading import SqlAlchemyProductReadRepository, SqlAlchemyRiskRepository
from app.schemas.profile import (
    CustomerRecommendationContext,
    RecommendationResponse,
    RecommendationWarning,
    RiskOperationStatusResponse,
    SuitabilityResult,
)
from app.services.asset_summary_service import AssetSummaryService
from app.services.risk_assessment_service import RiskAssessmentService


LEVEL = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}
RISK = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}


def _level(value: str | None) -> int:
    normalized = (value or "C1").upper()
    return LEVEL.get(normalized, RISK.get(normalized, 1))


class SuitabilityService:
    def __init__(self, risk_repository=None, product_repository=None, profile_repository=None, asset_service=None):
        self.risk_repository = risk_repository or SqlAlchemyRiskRepository()
        self.product_repository = product_repository or SqlAlchemyProductReadRepository()
        self.profile_repository = profile_repository
        self.asset_service = asset_service or AssetSummaryService()

    async def recommend(self, session, user_id: str, *, risk_operation=None) -> RecommendationResponse:
        profile = await self.profile_repository.get(session, user_id) if self.profile_repository else None
        asset = await self.asset_service.latest_or_derived(session, user_id)
        products = await self.product_repository.list_active(session)
        risk = await self.risk_repository.get_active(session, user_id)
        if risk is None:
            risk = await RiskAssessmentService(repository=self.risk_repository).ensure_default(session, user_id)

        rules = list(
            (
                await session.execute(
                    select(ProductSuitabilityRule).where(ProductSuitabilityRule.product_id.in_([product.id for product in products]))
                )
            ).scalars().all()
        ) if products else []
        rules_by_product: dict[str, list[ProductSuitabilityRule]] = {}
        for rule in rules:
            rules_by_product.setdefault(str(rule.product_id), []).append(rule)

        risk_operation = risk_operation or RiskOperationStatusResponse()
        total_asset = float(asset.total_asset) if asset else 0.0
        investable_asset = float(asset.investable_asset) if asset else 0.0
        warnings: list[RecommendationWarning] = []
        if risk_operation.restriction_status == "restricted":
            warnings.append(RecommendationWarning(code="RISK_RESTRICTION_ACTIVE", message="账户当前存在风控限制；匹配结果仅供查看，不可据此提交交易。"))
        elif risk_operation.status == "monitoring":
            warnings.append(RecommendationWarning(code="RISK_ALERT_MONITORING", message="账户存在待处理风险预警；请结合风险处理结论审慎参考匹配结果。"))
        if risk.status != "active":
            warnings.append(RecommendationWarning(code="RISK_ASSESSMENT_NOT_ACTIVE", message="当前风险测评不是正式有效状态，匹配结果使用默认或临时风险等级，仅供参考。"))
        if asset is None:
            warnings.append(RecommendationWarning(code="ASSET_SNAPSHOT_MISSING", message="未找到最新资产快照，资产门槛按 0 计算。"))

        results: list[SuitabilityResult] = []
        customer_level = _level(risk.risk_level)
        for product in products:
            product_rules = rules_by_product.get(str(product.id), [])
            required_level = max([_level(product.risk_level), *[_level(rule.minimum_risk_level) for rule in product_rules]])
            required_investable_asset = max([float(product.minimum_amount), *[float(rule.minimum_investable_asset) for rule in product_rules]])
            required_investor_types = {rule.investor_type for rule in product_rules if rule.investor_type and rule.investor_type != "all"}
            target_customer_type = product.target_customer_type or "individual"
            reasons: list[str] = []
            matched = True

            if required_level > customer_level:
                matched = False
                reasons.append(f"产品要求风险等级不高于 C{required_level}，高于当前客户风险等级 {risk.risk_level}。")
            else:
                reasons.append(f"客户当前风险等级 {risk.risk_level} 满足产品要求 C{required_level}。")

            if target_customer_type not in {"", "all"} and (profile is None or profile.customer_type != target_customer_type):
                matched = False
                reasons.append("客户类型不满足产品适配要求。")
            elif required_investor_types and (profile is None or profile.customer_type not in required_investor_types):
                matched = False
                reasons.append("客户类型不满足产品适当性规则。")
            else:
                reasons.append("客户类型满足产品适配要求。")

            if investable_asset < required_investable_asset:
                matched = False
                reasons.append(f"当前可投资资产 ¥{investable_asset:,.2f}，低于产品要求的 ¥{required_investable_asset:,.2f}。")
            else:
                reasons.append(f"当前可投资资产 ¥{investable_asset:,.2f}，满足产品门槛 ¥{required_investable_asset:,.2f}。")

            if risk_operation.restriction_status == "restricted":
                reasons.append("账户存在风控限制，结果仅供查看，不可提交交易。")

            rule_text = "；".join(rule.rule_text for rule in product_rules if rule.rule_text)
            results.append(
                SuitabilityResult(
                    product_id=str(product.id),
                    product_name=product.name,
                    product_type=product.product_type,
                    product_risk_level=product.risk_level,
                    minimum_amount=float(product.minimum_amount),
                    minimum_investable_asset=required_investable_asset,
                    liquidity=product.liquidity,
                    suitability_rule=rule_text,
                    matched=matched,
                    reasons=reasons,
                )
            )

        return RecommendationResponse(
            user_id=user_id,
            context=CustomerRecommendationContext(
                risk_level=risk.risk_level,
                risk_assessment_status=risk.status,
                total_asset=total_asset,
                investable_asset=investable_asset,
                risk_operation=risk_operation,
            ),
            matches=[item for item in results if item.matched],
            excluded=[item for item in results if not item.matched],
            warnings=warnings,
            generated_at=datetime.now(timezone.utc),
        )
