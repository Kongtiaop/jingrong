from uuid import uuid4
from sqlalchemy import select
from app.db.session import Database
from app.models.profile import Product, ProductSuitabilityRule

PRODUCTS = (
 # 个人 · C1（低风险现金/货基）
 {"name":"现金管理保本计划","product_type":"cash_management","risk_level":"C1","minimum_amount":1000,"daily_yield_rate":0.00005,"term_days":0,"liquidity":"high","target_customer_type":"all","description":"低风险现金管理产品，未完成正式测评的客户也可购买。"},
 {"name":"货币增强计划","product_type":"cash_management","risk_level":"C1","minimum_amount":1000,"daily_yield_rate":0.00006,"term_days":0,"liquidity":"high","target_customer_type":"individual","description":"货币型现金管理，流动性高，适合短期闲置资金。"},
 {"name":"同业存单指数基金","product_type":"fund","risk_level":"C1","minimum_amount":5000,"daily_yield_rate":0.00005,"term_days":0,"liquidity":"high","target_customer_type":"individual","description":"低波动现金类指数基金，风险极低。"},
 # 个人 · C2（固收）
 {"name":"稳健增值计划","product_type":"fixed_income","risk_level":"C2","minimum_amount":10000,"daily_yield_rate":0.00012,"term_days":90,"liquidity":"medium","target_customer_type":"individual","description":"稳健型固收配置，适合保守和稳健客户。"},
 {"name":"短债债券计划","product_type":"fixed_income","risk_level":"C2","minimum_amount":5000,"daily_yield_rate":0.00009,"term_days":30,"liquidity":"high","target_customer_type":"individual","description":"短久期债券，回撤小，流动性较好。"},
 {"name":"高等级信用债计划","product_type":"fixed_income","risk_level":"C2","minimum_amount":10000,"daily_yield_rate":0.00010,"term_days":90,"liquidity":"medium","target_customer_type":"individual","description":"高评级信用债组合，波动可控。"},
 {"name":"中短债增利计划","product_type":"fixed_income","risk_level":"C2","minimum_amount":10000,"daily_yield_rate":0.00011,"term_days":120,"liquidity":"medium","target_customer_type":"individual","description":"中短久期债券，兼顾收益与回撤控制。"},
 # 个人 · C3（平衡/固收+ /指数）
 {"name":"平衡配置组合","product_type":"balanced_fund","risk_level":"C3","minimum_amount":20000,"daily_yield_rate":0.00018,"term_days":180,"liquidity":"medium","target_customer_type":"individual","description":"股债均衡配置，适合平衡型客户。"},
 {"name":"固收+稳健计划","product_type":"balanced_fund","risk_level":"C3","minimum_amount":20000,"daily_yield_rate":0.00016,"term_days":180,"liquidity":"medium","target_customer_type":"individual","description":"以固收打底、少量权益增强的稳健策略。"},
 {"name":"多元资产配置计划","product_type":"balanced_fund","risk_level":"C3","minimum_amount":30000,"daily_yield_rate":0.00017,"term_days":365,"liquidity":"medium","target_customer_type":"individual","description":"跨资产多策略配置，波动分散。"},
 {"name":"红利低波指数","product_type":"fund","risk_level":"C3","minimum_amount":10000,"daily_yield_rate":0.00015,"term_days":0,"liquidity":"high","target_customer_type":"individual","description":"高股息低波动指数，防御性权益。"},
 # 个人 · C4（权益）
 {"name":"成长精选组合","product_type":"equity_fund","risk_level":"C4","minimum_amount":50000,"daily_yield_rate":0.00025,"term_days":0,"liquidity":"high","target_customer_type":"individual","description":"成长型权益配置，适合成长型客户。"},
 {"name":"沪深300指数增强","product_type":"equity_fund","risk_level":"C4","minimum_amount":50000,"daily_yield_rate":0.00022,"term_days":0,"liquidity":"high","target_customer_type":"individual","description":"跟踪沪深300并做增强的指数基金。"},
 {"name":"中证500指数","product_type":"equity_fund","risk_level":"C4","minimum_amount":50000,"daily_yield_rate":0.00023,"term_days":0,"liquidity":"high","target_customer_type":"individual","description":"中盘成长风格的指数基金。"},
 {"name":"科技成长主题基金","product_type":"equity_fund","risk_level":"C4","minimum_amount":50000,"daily_yield_rate":0.00028,"term_days":0,"liquidity":"high","target_customer_type":"individual","description":"聚焦科技板块的高弹性权益主题。"},
 # 个人 · C5（私行/对冲/全球）
 {"name":"私行进取策略","product_type":"private_strategy","risk_level":"C5","minimum_amount":500000,"daily_yield_rate":0.00035,"term_days":365,"liquidity":"low","target_customer_type":"individual","description":"高风险进取策略，仅供高风险承受能力客户。"},
 {"name":"量化对冲策略","product_type":"private_strategy","risk_level":"C5","minimum_amount":500000,"daily_yield_rate":0.00030,"term_days":365,"liquidity":"low","target_customer_type":"individual","description":"量化多空对冲，追求绝对收益、控波动。"},
 {"name":"全球配置进取计划","product_type":"private_strategy","risk_level":"C5","minimum_amount":1000000,"daily_yield_rate":0.00033,"term_days":730,"liquidity":"low","target_customer_type":"individual","description":"全球多资产进取配置，期限较长。"},
 # 企业
 {"name":"企业现金管理计划","product_type":"corporate_cash","risk_level":"C2","minimum_amount":100000,"daily_yield_rate":0.00008,"term_days":0,"liquidity":"high","target_customer_type":"enterprise","description":"企业客户现金管理方案，仅限企业客户。"},
 {"name":"企业流动现金管理","product_type":"corporate_cash","risk_level":"C1","minimum_amount":50000,"daily_yield_rate":0.00007,"term_days":0,"liquidity":"high","target_customer_type":"enterprise","description":"企业高流动性现金管理，随取随用。"},
 {"name":"企业固收增利计划","product_type":"corporate_cash","risk_level":"C2","minimum_amount":100000,"daily_yield_rate":0.00009,"term_days":90,"liquidity":"medium","target_customer_type":"enterprise","description":"企业固收增利，收益略高于现金管理。"},
)
async def ensure_demo_products(database: Database) -> None:
 async with database.session_factory() as session:
  for data in PRODUCTS:
   item=(await session.execute(select(Product).where(Product.name==data["name"]))).scalar_one_or_none()
   if item is None:
    product_data=data.copy()
    item=Product(id=str(uuid4()),source_type="synthetic_demo",status="active",**product_data); session.add(item); await session.flush()
   else:
    for key,value in data.items(): setattr(item,key,value)
    item.status="active"; item.deleted_at=None
   rule=(await session.execute(select(ProductSuitabilityRule).where(ProductSuitabilityRule.product_id==item.id))).scalars().first()
   customer_type=data["target_customer_type"]
   if rule is None:
    session.add(ProductSuitabilityRule(id=str(uuid4()),product_id=item.id,minimum_risk_level=item.risk_level,investor_type=customer_type,minimum_investable_asset=item.minimum_amount,rule_text=f"{item.risk_level}及以上且客户类型为{customer_type}"))
   else:
    rule.minimum_risk_level=item.risk_level; rule.investor_type=customer_type; rule.minimum_investable_asset=item.minimum_amount
  await session.commit()
