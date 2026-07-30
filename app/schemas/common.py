from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T
    error: dict | None = None
    trace_id: str | None = None
