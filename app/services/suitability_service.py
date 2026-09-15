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


# ---- 风险等级比对的**唯一权威实现**（收敛点）----
# ISSUE-20260914-RISK-RANK-RULES-DIVERGED
#
# 此前同一套"等级排名 + 比较"在 4 处各写一遍，且**默认值已经不一致**：
#   suitability_service.py:20  LEVEL / _level()，缺失默认 1
#   trading_service.py:100-101 内联 dict，缺失默认 1（下单硬门禁）
#   api/admin.py:135-136       内联 dict，**缺失默认 0** 并据此统计 missing_risk
#   api/profile.py:740         内联 risk_order（展示排序用）
# 风险：将来（如回答侧也要做"你能买什么"的推导）再写一份，就会出现
#       "回答说能买、下单被拒"这类口径不一致。
# 故把排名与比较集中到本模块，其余位置改为引用，保证只有一处定义。

def risk_rank(value: str | None) -> int:
    """等级归一化为 1–5。

    兼容 C1–C5（客户等级）与 R1–R5（通用风险等级表述）：两者同域，均映射为 1–5。
    未知/缺失返回 **0**（区别于 _level 的历史默认 1）——调用方据此可区分
    "缺失"与"最低等级"，这是 admin 统计 missing_risk 与门禁拒绝所需要的语义。
    """
    normalized = str(value or "").upper().strip()
    return LEVEL.get(normalized, RISK.get(normalized, 0))


def is_suitable(customer_level: str | None, product_level: str | None) -> bool:
    """客户等级是否满足产品要求：**同级或更高**即可（actual >= required）。

    这是全系统唯一的适当性判定口径（与 trading_service 的下单门禁一致：
    产品要求 C3 时，C3/C4/C5 客户可买，C1/C2 不可）。
    """
    actual = risk_rank(customer_level)
    required = risk_rank(product_level) or 1   # 产品等级缺失时按最低要求处理
    return actual >= required


class SuitabilityService:
    def __init__(self, risk_repository=None, product_repository=None, profile_repository=None, asset_service=None):
        self.risk_repository = risk_repository or SqlAlchemyRiskRepository()
        self.product_repository = product_repository or SqlAlchemyProductReadRepository()
        self.profile_repository = profile_repository
        self.asset_service = asset_service or AssetSummaryService()

    async def recommend(self, session, user_id: str, *, risk_operation=None, preferences: dict | None = None, top_n: int = 5) -> RecommendationResponse:
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
                    term_days=int(product.term_days or 0),
                    suitability_rule=rule_text,
                    matched=matched,
                    reasons=reasons,
                    recommendation_reason=self._recommendation_reason(product.risk_level, int(product.term_days or 0), preferences or {}),
                )
            )

        matches = [item for item in results if item.matched]
        excluded = [item for item in results if not item.matched]
        preference_note = ""
        pref = preferences or {}
        total_matched = len(matches)
        if matches and pref:
            score_fn = self._preference_score_mapper(pref)
            if score_fn is not None:
                matches = sorted(matches, key=lambda item: score_fn(item))
        # 精准单点：精选 Top N 并说明，避免一次性拉出全部通过门控的产品
        top_n = max(1, top_n)
        if top_n and len(matches) > top_n:
            note_items = "；".join(f"{m.product_name}（{m.recommendation_reason}）" for m in matches[:top_n])
            matches = matches[:top_n]
            preference_note = f"满足适当性的产品共 {total_matched} 个，已按您的风险/偏好精选前 {len(matches)}：{note_items}"
        elif matches:
            note_items = "；".join(f"{m.product_name}（{m.recommendation_reason}）" for m in matches)
            preference_note = f"以下 {len(matches)} 个产品通过适当性匹配：{note_items}"
        return RecommendationResponse(
            user_id=user_id,
            context=CustomerRecommendationContext(
                risk_level=risk.risk_level,
                risk_assessment_status=risk.status,
                total_asset=total_asset,
                investable_asset=investable_asset,
                risk_operation=risk_operation,
            ),
            matches=matches,
            excluded=excluded,
            warnings=warnings,
            preference_note=preference_note,
            generated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _recommendation_reason(risk_level: str, term_days: int, preferences: dict) -> str:
        """生成"为什么推荐这条"的理由：优先按偏好对齐，否则用适当性说明。"""
        risk_pref = (preferences.get("risk_preference") or "").strip().lower()
        product_pref = (preferences.get("product_preference") or "").strip().lower()
        horizon_pref = (preferences.get("investment_horizon_preference") or "").strip().lower()
        parts: list[str] = []
        if risk_pref in {"conservative", "balanced", "aggressive"}:
            label = {"conservative": "保守", "balanced": "稳健/平衡", "aggressive": "进取"}[risk_pref]
            parts.append(f"契合您{label}风险偏好")
        if product_pref == "steady":
            parts.append("符合您对稳健产品的偏好")
        if horizon_pref == "long_term" and term_days >= 180:
            parts.append(f"期限 {term_days} 天，适合您长期持有")
        if not parts:
            parts.append(f"满足 {risk_level} 适当性要求")
        return "；".join(parts)

    @staticmethod
    def _preference_score_mapper(preferences: dict) -> callable | None:
        """偏好 → (SuitabilityResult)->(risk_score, horizon_score) 排序打分；无可用偏好则 None。

        分数越低越靠前：先按风险/产品偏好对齐，再按期限偏好（长期持有偏好更长期限）。
        """
        risk_pref = (preferences.get("risk_preference") or "").strip().lower()
        product_pref = (preferences.get("product_preference") or "").strip().lower()
        horizon_pref = (preferences.get("investment_horizon_preference") or "").strip().lower()
        target: set[str] | None = None
        if risk_pref in {"conservative", "balanced", "aggressive"}:
            target = {"conservative": {"C1", "C2"}, "balanced": {"C2", "C3"}, "aggressive": {"C4", "C5"}}[risk_pref]
        elif product_pref == "steady":
            target = {"C1", "C2"}
        horizon_long = horizon_pref == "long_term"
        if target is None and not horizon_long:
            return None

        def score(item) -> tuple[int, int]:
            risk = 0
            if target is not None:
                risk = 0 if (item.product_risk_level or "").upper() in target else 1
            horizon = 0
            if horizon_long:
                term = int(getattr(item, "term_days", 0) or 0)
                horizon = 0 if term >= 180 else 1  # 长期持有：≥180 天算长期
            return (risk, horizon)

        return score
