from __future__ import annotations

import json

from app.core.settings import Settings


def validate_http_backend(settings: Settings):
    if settings.database_backend.lower() == "mysql":
        raise RuntimeError("DATABASE_BACKEND=mysql is validation-only and cannot serve HTTP trading routes yet")


def main():
    postgres = Settings(database_backend="postgresql", database_write_backend="postgresql")
    validate_http_backend(postgres)
    mysql_error = None
    try:
        mysql = Settings(database_backend="mysql", database_write_backend="postgresql", mysql_validation_writes=True)
        validate_http_backend(mysql)
    except RuntimeError as exc:
        mysql_error = str(exc)
    result = {"postgres_allowed": True, "mysql_blocked": mysql_error is not None, "message": mysql_error}
    result["pass"] = result["postgres_allowed"] and result["mysql_blocked"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["pass"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
