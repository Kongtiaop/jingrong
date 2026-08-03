from __future__ import annotations
import asyncio,json,aiomysql

MYSQL=dict(host='127.0.0.1',port=3307,user='wealth',password='wealth_dev',db='wealth_manager')
EXPECTED={
 'users':{'id','username','display_name','status','password_hash','is_super_admin','created_at','updated_at','deleted_at'},
 'customer_profile':{'id','user_id','age','occupation','region','customer_type','customer_tier','investment_experience_years','investment_goal','investment_horizon_years','liquidity_preference','source_type','created_at','updated_at'},
 'customer_asset_snapshot':{'id','user_id','total_asset','cash_balance','investable_asset','liability','net_asset','snapshot_time','source_type','created_at'},
 'customer_holding':{'id','user_id','product_id','quantity','cost_amount','market_value','profit_loss','holding_days','status','created_at','updated_at'},
 'account':{'id','user_id','account_no','currency','available_balance','frozen_balance','status','created_at','updated_at'},
}
async def validate() -> dict:
 conn=await aiomysql.connect(**MYSQL,autocommit=True)
 try:
  async with conn.cursor() as c:
   result={}; passed=True
   for table,expected in EXPECTED.items():
    await c.execute(f'SHOW COLUMNS FROM `{table}`'); cols={row[0] for row in await c.fetchall()}; missing=sorted(expected-cols); legacy=sorted(cols & {'total_assets','cash_assets','investment_assets','liabilities','net_assets'}); result[table]={'missing':missing,'legacy_columns':legacy}; passed &= not missing and not legacy
   result['pass']=passed
   return result
 finally: conn.close()

async def main():
 result=await validate(); print(json.dumps(result,ensure_ascii=False,indent=2));
 if not result['pass']: raise SystemExit(1)

if __name__=='__main__': asyncio.run(main())
