import asyncio,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.main import app,lifespan
from app.models.data_catalog import DataCatalogExampleQuery,DataCatalogTable,DataCatalogColumn
async def main():
 async with lifespan(app):
  async with app.state.database.session_factory() as s:
   t=(await s.execute(select(DataCatalogExampleQuery).where(DataCatalogExampleQuery.intent_code=="customer_net_asset_trend"))).scalar_one()
   print("template",t.target_table_names_json,t.target_column_names_json,t.metadata_json)
   tables=(await s.execute(select(DataCatalogTable))).scalars().all(); print("tables",[(x.table_name,x.id,x.is_active,x.is_queryable) for x in tables if x.table_name in {"customer_asset_snapshot","users"}])
   cols=(await s.execute(select(DataCatalogColumn).join(DataCatalogTable).where(DataCatalogTable.table_name=="customer_asset_snapshot"))).scalars().all(); print("cols",[(c.column_name,c.is_queryable,c.is_sensitive) for c in cols])
asyncio.run(main())
