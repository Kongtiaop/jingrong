from __future__ import annotations
import argparse
import asyncio
import json
from sqlalchemy import select
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import Permission, Role

LEGACY = {"customer:read", "order:write", "risk:read", "risk:write"}

async def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect legacy RBAC permission assignments")
    parser.add_argument("--apply", action="store_true", help="reserved; does not delete data without an explicit future migration")
    args = parser.parse_args()
    db = Database(get_settings())
    try:
        async with db.session_factory() as session:
            permissions = list((await session.execute(select(Permission).where(Permission.code.in_(LEGACY)))).scalars().all())
            rows = []
            for permission in permissions:
                role_rows = list((await session.execute(select(Role).where(Role.permissions.any(id=permission.id)))).scalars().all())
                rows.append({"permission": permission.code, "permission_id": permission.id, "roles": [role.code for role in role_rows]})
            result = {"apply_requested": args.apply, "legacy_assignments": rows, "safe_to_remove": all(not row["roles"] for row in rows)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await db.dispose()

if __name__ == "__main__":
    asyncio.run(main())
