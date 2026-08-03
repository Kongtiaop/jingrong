from __future__ import annotations
import asyncio,json
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

SQL="""EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT u.id,u.username,u.display_name FROM users u WHERE u.status='active' AND EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=u.id AND r.code='customer') ORDER BY u.created_at LIMIT 50 OFFSET 0"""
async def main():
 db=Database(get_settings())
 try:
  async with db.session_factory() as s:
   row=(await s.execute(text(SQL))).scalar_one()
   print(json.dumps(row,ensure_ascii=False,indent=2,default=str))
 finally: await db.dispose()
if __name__=='__main__': asyncio.run(main())
