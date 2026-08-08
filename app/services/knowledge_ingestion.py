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
    reused_chunks: int
    embedded_chunks: int
    status: str


def _chunks(text: str, *, size: int = 1000, overlap: int = 120) -> list[str]:
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        return []
    step = max(1, size - overlap)
    return [normalized[index:index + size] for index in range(0, len(normalized), step)]


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
            source_path = Path(document.source_path)
            parser_type = "plain_text"
            if document.file_type.lower() == "docx":
                try:
                    from docx import Document as DocxDocument
                except ImportError as exc:
                    raise RuntimeError("DOCX parsing requires python-docx") from exc
                parsed = DocxDocument(str(source_path))
                text = "\n".join(paragraph.text for paragraph in parsed.paragraphs)
                parser_type = "docx"
            else:
                text = source_path.read_text(encoding="utf-8")
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            version = await self.repository.create_version(
                document_id=document_id, content_hash=content_hash, parser_type=parser_type
            )
            prepared = [
                (index, content, hashlib.sha256(content.encode("utf-8")).hexdigest())
                for index, content in enumerate(_chunks(text))
            ]
            reusable = await self.repository.active_chunk_embeddings_by_hash(document_id=document_id)
            missing = [(index, content, chunk_hash) for index, content, chunk_hash in prepared if chunk_hash not in reusable]

            embedded: dict[str, list[float]] = {}
            for start in range(0, len(missing), 4):
                batch = missing[start:start + 4]
                vectors = await self.embedding.embed([content for _, content, _ in batch])
                if len(vectors) != len(batch):
                    raise ValueError("embedding response count does not match chunk count")
                embedded.update({chunk_hash: vector for (_, _, chunk_hash), vector in zip(batch, vectors, strict=True)})

            rows = [
                {
                    "chunk_index": index,
                    "content": content,
                    "content_hash": chunk_hash,
                    "title_path": "",
                    "metadata": {"parser_type": parser_type},
                    "embedding": reusable.get(chunk_hash) or embedded[chunk_hash],
                }
                for index, content, chunk_hash in prepared
            ]
            count = await self.repository.save_chunks(document_id=document_id, version_id=str(version.id), chunks=rows)
            await self.repository.activate_version(document_id=document_id, version_id=str(version.id), chunk_count=count)
            return IngestionResult(
                document_id=document_id,
                version_id=str(version.id),
                chunk_count=count,
                reused_chunks=sum(1 for _, _, chunk_hash in prepared if chunk_hash in reusable),
                embedded_chunks=len(missing),
                status="active",
            )
        except Exception as exc:
            await self.repository.mark_document_failed(
                document_id=document_id,
                version_id=str(version.id) if version else None,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise
