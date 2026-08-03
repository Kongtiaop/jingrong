from __future__ import annotations
import asyncio,json
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database
async def main():
 db=Database(get_settings())
 async with db.engine.begin() as c:
  rows=(await c.execute(text("""select o.id,o.order_no,o.status, (select count(*) from trades t where t.order_id=o.id) trade_count, (select count(*) from money_flow mf where mf.order_id=o.id and mf.flow_type='cash' and mf.direction='out') flow_count, (select count(*) from order_status_history h where h.order_id=o.id) history_count from orders o where o.status='executed' and ((select count(*) from trades t where t.order_id=o.id)<>1 or (select count(*) from money_flow mf where mf.order_id=o.id and mf.flow_type='cash' and mf.direction='out')<>1) order by o.created_at"""))).mappings().all()
  print(json.dumps({'invalid_executed': [dict(r) for r in rows], 'pass': not rows},ensure_ascii=False,indent=2))
  raise SystemExit(0 if not rows else 1)
if __name__=='__main__': asyncio.run(main())
