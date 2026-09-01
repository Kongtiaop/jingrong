import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.main import app,lifespan
from app.models.auth import User
from app.models.data_catalog import DataCatalogExampleQuery,DataCatalogTable,DataCatalogColumn
from app.services.data_analysis_agent import DataAnalysisAgent
from app.services.data_catalog_service import DataCatalogService,CatalogSearchScope
from app.repositories.data_catalog import DataCatalogRepository
from app.services.permission_scoped_cache import PermissionScopedCache
from app.services.subject_scope import SubjectScopeService
async def main():
 async with lifespan(app):
  async with app.state.database.session_factory() as s:
   users=(await s.execute(select(User).where(User.username.like("scope_advisor_%")))).scalars().all(); print("users",[(x.id,x.username,[r.code for r in x.roles]) for x in users])
   if not users:return
   u=users[-1]; t=(await s.execute(select(DataCatalogExampleQuery).where(DataCatalogExampleQuery.intent_code=="customer_net_asset_trend"))).scalar_one(); print("template",t.question,t.is_active,t.is_approved,t.allowed_roles_json,t.target_table_names_json,t.target_column_names_json,t.metadata_json)
   scope=await CatalogSearchScope.from_user(u,app.state.redis.client); cs=DataCatalogService(DataCatalogRepository(app.state.database),permission_cache=PermissionScopedCache(app.state.redis.client)); c=await cs.search(query=t.question,scope=scope,top_k=5); print("ctx",[(x["table_name"],[y["column_name"] for y in x["columns"]]) for x in c["tables"]])
   ids=[x["id"] for x in c.get("tables",[])]; ts=list((await s.execute(select(DataCatalogTable).where(DataCatalogTable.id.in_(ids),DataCatalogTable.is_active.is_(True),DataCatalogTable.is_queryable.is_(True)))).scalars().all()) if ids else []; cols=list((await s.execute(select(DataCatalogColumn).where(DataCatalogColumn.table_id.in_([x.id for x in ts]),DataCatalogColumn.is_queryable.is_(True),DataCatalogColumn.is_sensitive.is_(False)))).scalars().all()) if ts else []; auth=await SubjectScopeService.authorized_subject_ids(s,u); print("auth",auth,"refs",DataAnalysisAgent._extract_references(t.sql_template)); a=await DataAnalysisAgent().analyze_template_attempt(s,user=u,template=t,query=t.question,catalog_tables=ts,catalog_columns=cols,authorized_subject_ids=auth); print("attempt",a.reason,a.template_id)
asyncio.run(main())
