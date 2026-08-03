from __future__ import annotations

import argparse
import asyncio

import aiomysql

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")

async def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--order-id', required=True); parser.add_argument('--yes', action='store_true'); args=parser.parse_args()
    if not args.yes: raise SystemExit('Refusing cleanup without --yes')
    c=await aiomysql.connect(**MYSQL, autocommit=False)
    try:
        async with c.cursor() as cur:
            for table,column in [('order_status_history','order_id'),('trades','order_id'),('orders','id')]:
                await cur.execute(f'DELETE FROM `{table}` WHERE `{column}`=%s',(args.order_id,))
            await c.commit(); print({'cleaned_order_id':args.order_id})
    except Exception:
        await c.rollback(); raise
    finally: c.close()
if __name__=='__main__': asyncio.run(main())
