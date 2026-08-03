from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.api.profile import my_risk_assessment
from app.repositories.unit_of_work import create_postgres_unit_of_work


async def main():
    uow = create_postgres_unit_of_work()
    assert hasattr(uow.risks, "get_latest_active")
    result = {
        "postgres_risk_repository": type(uow.risks).__name__,
        "has_get_latest_active": hasattr(uow.risks, "get_latest_active"),
        "has_save": hasattr(uow.risks, "save"),
        "has_supersede_active": hasattr(uow.risks, "supersede_active"),
    }
    result["pass"] = all(result.values())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
