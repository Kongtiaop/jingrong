from __future__ import annotations
import json
from app.common.exceptions import map_database_error

class PostgreSQLUniqueError(Exception): pass
class MySQLDuplicateError(Exception): pass
class ForeignKeyError(Exception): pass

def main():
 cases=[(PostgreSQLUniqueError("duplicate key value violates unique constraint"),"IDEMPOTENCY_CONFLICT",409),(MySQLDuplicateError("Duplicate entry for key"),"IDEMPOTENCY_CONFLICT",409),(ForeignKeyError("foreign key constraint fails"),"DATABASE_CONSTRAINT_ERROR",400),(RuntimeError("connection unavailable"),"DATABASE_ERROR",500)]
 result=[]
 for exc,code,status in cases:
  mapped=map_database_error(exc); result.append({"source":type(exc).__name__,"code":mapped.code,"status":mapped.status_code,"pass":mapped.code==code and mapped.status_code==status})
 output={"cases":result,"pass":all(x["pass"] for x in result)}; print(json.dumps(output,ensure_ascii=False,indent=2));
 if not output["pass"]: raise SystemExit(1)
if __name__=='__main__': main()
