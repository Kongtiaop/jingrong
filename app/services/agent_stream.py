from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class AgentStreamEvent:
    type: str
    conversation_id: str
    request_id: str
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def display_chunks(text: str, max_chars: int = 24) -> Iterable[str]:
    text = text or ""
    for offset in range(0, len(text), max_chars):
        yield text[offset:offset + max_chars]
