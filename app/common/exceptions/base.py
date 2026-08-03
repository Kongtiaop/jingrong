from typing import Any


class AppException(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class ResourceNotFoundError(AppException):
    def __init__(self, message: str = "resource not found", *, code: str = "RESOURCE_NOT_FOUND") -> None:
        super().__init__(code, message, status_code=404)


class ConflictError(AppException):
    def __init__(self, message: str, *, code: str = "CONFLICT") -> None:
        super().__init__(code, message, status_code=409)


class RateLimitError(AppException):
    def __init__(self, message: str = "rate limit exceeded") -> None:
        super().__init__("RATE_LIMITED", message, status_code=429)


class DatabaseConflictError(ConflictError):
    def __init__(self, message: str = "resource already exists", *, code: str = "RESOURCE_CONFLICT") -> None:
        super().__init__(message, code=code)


def map_database_error(exc: Exception) -> AppException:
    """Map PostgreSQL/MySQL driver differences to stable business errors."""
    name = type(exc).__name__
    text = str(exc).lower()
    if "integrity" in name.lower() or "duplicate" in text or "unique" in text or "duplicate entry" in text:
        return DatabaseConflictError("idempotency key already used", code="IDEMPOTENCY_CONFLICT")
    if "foreign key" in text or "not-null" in text or "not null" in text:
        return AppException("DATABASE_CONSTRAINT_ERROR", "business data constraint failed", status_code=400)
    return AppException("DATABASE_ERROR", "database operation failed", status_code=500)
