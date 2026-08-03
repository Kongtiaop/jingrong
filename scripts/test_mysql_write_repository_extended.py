from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import aiomysql

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")

async def db(): return await aiomysql.connect(**MYSQL, autocommit=False)
async def ids(c):
    async with c.cursor(aiomysql.DictCursor) as cur:
        await cur.execute("SELECT id FROM users WHERE username='customer_demo'")
        u=await cur.fetchone(); await cur.execute("SELECT * FROM account WHERE user_id=%s",(u['id'],)); a=await cur.fetchone(); await cur.execute("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1"); p=await cur.fetchone(); return u,a,p

async def rollback_test():
    c=await db(); oid=str(uuid4())
    try:
        u,a,p=await ids(c)
        async with c.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("START TRANSACTION")
            await cur.execute("INSERT INTO orders(id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES(%s,%s,%s,%s,%s,1000,1000,'pending_confirmation','buy','','',%s)",(oid,'RB-'+oid[:10],u['id'],a['id'],p['id'],'rb-'+oid))
            await cur.execute("INSERT INTO trades(id,trade_no,order_id,user_id,product_id,amount,quantity) VALUES(%s,%s,%s,%s,%s,1000,1000)",(str(uuid4()),'RT-'+oid[:10],oid,u['id'],p['id']))
            await c.rollback()
            await cur.execute("SELECT COUNT(*) n FROM orders WHERE id=%s",(oid,)); o=(await cur.fetchone())['n']; await cur.execute("SELECT COUNT(*) n FROM trades WHERE order_id=%s",(oid,)); t=(await cur.fetchone())['n']
        return {'orders':o,'trades':t,'pass':o==0 and t==0}
    finally: c.close()

async def idempotency_test():
    c=await db(); oid=str(uuid4()); key='idem-'+oid
    try:
        u,a,p=await ids(c); sql="INSERT INTO orders(id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES(%s,%s,%s,%s,%s,1000,1000,'pending_confirmation','buy','','',%s)"
        async with c.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql,(oid,'ID-'+oid[:10],u['id'],a['id'],p['id'],key)); await c.commit(); error=None
            try: await cur.execute(sql,(str(uuid4()),'ID2-'+oid[:8],u['id'],a['id'],p['id'],key)); await c.commit()
            except Exception as e: error=type(e).__name__; await c.rollback()
            await cur.execute("SELECT COUNT(*) n FROM orders WHERE user_id=%s AND idempotency_key=%s",(u['id'],key)); n=(await cur.fetchone())['n']
        return {'error':error,'rows':n,'pass':error is not None and n==1}
    finally:
        c.close()

async def holding_asset_test():
    c=await db(); oid=str(uuid4()); hid=str(uuid4()); aid=str(uuid4())
    try:
        u,a,p=await ids(c)
        async with c.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("START TRANSACTION")
            await cur.execute("INSERT INTO orders(id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES(%s,%s,%s,%s,%s,1000,1000,'executed','buy','','',%s)",(oid,'HA-'+oid[:10],u['id'],a['id'],p['id'],'ha-'+oid))
            await cur.execute("INSERT INTO customer_holding(id,user_id,product_id,quantity,cost_amount,market_value,profit_loss,holding_days,status) VALUES(%s,%s,%s,1000,1000,1000,0,0,'active')",(hid,u['id'],p['id']))
            await cur.execute("INSERT INTO customer_asset_snapshot(id,user_id,total_asset,cash_balance,investable_asset,liability,net_asset,source_type) VALUES(%s,%s,201000,200000,201000,0,201000,'mysql_write_test')",(aid,u['id']))
            await c.commit()
            await cur.execute("SELECT COUNT(*) n FROM customer_holding WHERE id=%s",(hid,)); h=(await cur.fetchone())['n']; await cur.execute("SELECT COUNT(*) n FROM customer_asset_snapshot WHERE id=%s",(aid,)); a_count=(await cur.fetchone())['n']
        return {'holding':h,'asset':a_count,'pass':h==1 and a_count==1}
    finally:
        async with c.cursor() as cur:
            await cur.execute("DELETE FROM customer_asset_snapshot WHERE id=%s",(aid)); await cur.execute("DELETE FROM customer_holding WHERE id=%s",(hid)); await cur.execute("DELETE FROM orders WHERE id=%s",(oid)); await c.commit(); c.close()

async def concurrent_idempotency():
    c=await aiomysql.connect(**MYSQL,autocommit=True); u,a,p=await ids(c); c.close(); key='conc-'+uuid4().hex
    async def worker(i):
        x=await db(); oid=str(uuid4())
        try:
            async with x.cursor() as cur:
                await cur.execute("START TRANSACTION"); await cur.execute("INSERT INTO orders(id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES(%s,%s,%s,%s,%s,1000,1000,'pending_confirmation','buy','','',%s)",(oid,'CI-'+str(i)+'-'+oid[:7],u['id'],a['id'],p['id'],key)); await x.commit(); return 'created'
        except Exception as e: await x.rollback(); return type(e).__name__
        finally: x.close()
    results=await asyncio.gather(*[worker(i) for i in range(5)])
    c=await aiomysql.connect(**MYSQL,autocommit=True)
    async with c.cursor(aiomysql.DictCursor) as cur: await cur.execute("SELECT COUNT(*) n FROM orders WHERE user_id=%s AND idempotency_key=%s",(u['id'],key)); n=(await cur.fetchone())['n']; await cur.execute("DELETE FROM orders WHERE user_id=%s AND idempotency_key=%s",(u['id'],key))
    c.close(); return {'results':results,'rows':n,'pass':results.count('created')==1 and n==1}

async def main():
    r={'rollback':await rollback_test(),'idempotency':await idempotency_test(),'holding_asset':await holding_asset_test(),'concurrent_idempotency':await concurrent_idempotency()}; r['all_pass']=all(x['pass'] for x in r.values()); print(json.dumps(r,ensure_ascii=False,indent=2,default=str));
    if not r['all_pass']: raise SystemExit(1)
if __name__=='__main__': asyncio.run(main())
