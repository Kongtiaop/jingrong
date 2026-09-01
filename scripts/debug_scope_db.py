import asyncio,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.main import app,lifespan
from app.models.auth import User
from app.models.data_catalog import DataCatalogTable,DataCatalogColumn
async def main():
 async with lifespan(app):
  async with app.state.database.session_factory() as s:
   users=(await s.execute(select(User).where(User.username.like('scope_advisor_%')))).scalars().all(); print('users',[(u.id,u.username,[r.code for r in u.roles]) for u in users])
   tables=(await s.execute(select(DataCatalogTable).where(DataCatalogTable.table_name=='customer_asset_snapshot'))).scalars().all(); print('tables',[(t.id,t.is_active,t.is_queryable,t.allowed_roles_json) for t in tables])
   for t in tables:
    cols=(await s.execute(select(DataCatalogColumn).where(DataCatalogColumn.table_id==t.id))).scalars().all(); print('cols',[(c.column_name,c.is_queryable,c.is_sensitive) for c in cols])
asyncio.run(main())
