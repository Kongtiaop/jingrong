from __future__ import annotations
import asyncio,json
from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import create_postgres_unit_of_work
async def main():
 db=Database(get_settings()); uow=create_postgres_unit_of_work()
 try:
  result={'has_get_user':hasattr(uow.admin,'get_user'),'has_get_roles':hasattr(uow.admin,'get_roles'),'has_get_role_by_id':hasattr(uow.admin,'get_role_by_id'),'has_get_permissions':hasattr(uow.admin,'get_permissions'),'has_update_user_roles':hasattr(uow.admin,'update_user_roles'),'has_update_user_status':hasattr(uow.admin,'update_user_status'),'has_update_role_permissions':hasattr(uow.admin,'update_role_permissions'),'audit_save':hasattr(uow.audit_logs,'save')}
  result['pass']=all(result.values()); print(json.dumps(result,ensure_ascii=False,indent=2));
  if not result['pass']: raise SystemExit(1)
 finally: await db.dispose()
if __name__=='__main__': asyncio.run(main())
