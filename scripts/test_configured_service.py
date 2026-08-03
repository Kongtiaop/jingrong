from __future__ import annotations

import json
from app.core.settings import Settings
from app.repositories.unit_of_work import create_configured_unit_of_work

def main():
    pg = Settings(database_backend="postgresql", database_write_backend="postgresql")
    mysql = Settings(database_backend="mysql", database_write_backend="postgresql", mysql_validation_writes=True)
    pg_uow = create_configured_unit_of_work(pg)
    mysql_uow = create_configured_unit_of_work(mysql)
    result={"postgres":type(pg_uow).__name__,"mysql":type(mysql_uow).__name__,"default_pg":type(pg_uow).__name__=="SqlAlchemyTradingUnitOfWork","mysql_validation":type(mysql_uow).__name__=="MySQLServiceUnitOfWork"}
    result["pass"]=all(result.values())
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result["pass"]: raise SystemExit(1)
if __name__=='__main__': main()
