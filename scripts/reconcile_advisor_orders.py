from __future__ import annotations
import argparse, asyncio, json
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

async def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); args=ap.parse_args(); db=Database(get_settings())
    async with db.engine.begin() as c:
        query="""select o.id,o.order_no,o.user_id,u.username,o.amount,o.status,o.idempotency_key,o.created_at,a.available_balance,a.frozen_balance,mf.id flow_id,mf.amount flow_amount from orders o join users u on u.id=o.user_id join account a on a.user_id=o.user_id left join money_flow mf on mf.order_id=o.id and mf.flow_type='cash' and mf.direction='out' where o.idempotency_key like 'advisor-draft:%' and o.status='executed' order by o.created_at"""
        rows=(await c.execute(text(query))).mappings().all()
        report=[]
        for r in rows:
            item=dict(r); item['created_at']=str(item['created_at']); item['amount']=str(item['amount']); item['available_balance']=str(item['available_balance']); item['frozen_balance']=str(item['frozen_balance']); item['flow_amount']=str(item['flow_amount']) if item['flow_amount'] is not None else None; item['classification']='matched' if item['flow_id'] and item['flow_amount'] is not None and float(item['flow_amount']) == float(r['amount']) else 'missing_or_mismatch'; report.append(item)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        print({'total':len(report),'matched':sum(x['classification']=='matched' for x in report),'missing_or_mismatch':sum(x['classification']=='missing_or_mismatch' for x in report),'apply_requested':args.apply})
        if args.apply: raise SystemExit('refusing automatic correction; review report and explicitly define per-order correction')
if __name__=='__main__': asyncio.run(main())
