from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T | None = None
    error: ErrorDetail | None = None
    trace_id: str | None = None
