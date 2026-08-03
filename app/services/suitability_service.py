from app.services.asset_summary_service import AssetSummaryService
from app.services.risk_assessment_service import RiskAssessmentService
from app.repositories.trading import SqlAlchemyRiskRepository, SqlAlchemyProductReadRepository
from app.schemas.profile import RecommendationResponse, SuitabilityResult

LEVEL={"C1":1,"C2":2,"C3":3,"C4":4,"C5":5}
RISK={"R1":1,"R2":2,"R3":3,"R4":4,"R5":5}

class SuitabilityService:
 def __init__(self, risk_repository=None, product_repository=None, profile_repository=None, asset_service=None):
  self.risk_repository=risk_repository or SqlAlchemyRiskRepository()
  self.product_repository=product_repository or SqlAlchemyProductReadRepository()
  self.profile_repository=profile_repository
  self.asset_service=asset_service or AssetSummaryService()
 async def recommend(self,session,user_id:str)->RecommendationResponse:
  profile=await self.profile_repository.get(session,user_id) if self.profile_repository else None
  asset=await self.asset_service.latest_or_derived(session,user_id)
  products=await self.product_repository.list_active(session)
  results=[]
  risk=await self.risk_repository.get_active(session, user_id)
  if risk is None: risk=await RiskAssessmentService(repository=self.risk_repository).ensure_default(session,user_id)
  customer_level=LEVEL.get(risk.risk_level,1)
  for product in products:
   reasons=[]; matched=True
   product_level=LEVEL.get(product.risk_level, RISK.get(product.risk_level,1))
   if product_level>customer_level: matched=False; reasons.append(f"产品风险等级 {product.risk_level} 高于当前客户可承受等级 C{customer_level}")
   if profile and product.target_customer_type != "individual" and profile.customer_type != product.target_customer_type: matched=False; reasons.append("客户类型不满足产品适配要求")
   if asset and float(asset.investable_asset)<float(product.minimum_amount): matched=False; reasons.append("可投资资产未达到产品最低金额")
   if matched: reasons.append("风险等级和资产门槛满足基础匹配")
   results.append(SuitabilityResult(product_id=str(product.id),product_name=product.name,matched=matched,reasons=reasons))
  return RecommendationResponse(user_id=user_id,matches=[x for x in results if x.matched],excluded=[x for x in results if not x.matched])
