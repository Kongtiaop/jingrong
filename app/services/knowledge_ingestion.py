from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.infrastructure.qwen import QwenProvider
from app.repositories.knowledge import KnowledgeRepository


@dataclass(frozen=True)
class IngestionResult:
    document_id: str
    version_id: str
    chunk_count: int
    status: str


def _chunks(text: str, *, size: int = 1000, overlap: int = 120) -> list[str]:
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        return []
    step = max(1, size - overlap)
    return [normalized[i:i + size] for i in range(0, len(normalized), step)]


class KnowledgeIngestionService:
    def __init__(self, repository: KnowledgeRepository, embedding: QwenProvider) -> None:
        self.repository = repository
        self.embedding = embedding

    async def ingest_document(self, document_id: str) -> IngestionResult:
        document = await self.repository.get_document(document_id)
        if document is None:
            raise ValueError("knowledge document not found")
        version = None
        try:
            path = Path(document.source_path)
            text = path.read_text(encoding="utf-8")
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            version = await self.repository.create_version(document_id=document_id, content_hash=content_hash, parser_type="plain_text")
            parts = _chunks(text)
            vectors: list[list[float]] = []
            for start in range(0, len(parts), 4):
                vectors.extend(await self.embedding.embed(parts[start:start + 4]))
            if len(vectors) != len(parts):
                raise ValueError("embedding response count does not match chunk count")
            rows = [{
                "chunk_index": index,
                "content": content,
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "title_path": "",
                "metadata": {"parser_type": "plain_text"},
                "embedding": vector,
            } for index, (content, vector) in enumerate(zip(parts, vectors))]
            count = await self.repository.save_chunks(document_id=document_id, version_id=str(version.id), chunks=rows)
            await self.repository.activate_version(document_id=document_id, version_id=str(version.id), chunk_count=count)
            return IngestionResult(document_id=document_id, version_id=str(version.id), chunk_count=count, status="active")
        except Exception as exc:
            await self.repository.mark_document_failed(document_id=document_id, version_id=str(version.id) if version else None, error_message=f"{type(exc).__name__}: {exc}")
            raise
