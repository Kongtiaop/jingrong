from __future__ import annotations
import asyncio,json
from scripts.check_mysql_version import get_version
from scripts.validate_mysql_schema import validate
async def main():
 v=await get_version(); s=await validate(); result={'mysql_version':v['version'],'major_supported':v['version'].startswith('8.'),'schema_validation':s['pass'],'pass':v['version'].startswith('8.') and s['pass']}; print(json.dumps(result,ensure_ascii=False,indent=2));
 if not result['pass']: raise SystemExit(1)
if __name__=='__main__': asyncio.run(main())
