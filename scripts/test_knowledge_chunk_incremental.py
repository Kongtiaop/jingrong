from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.services.knowledge_ingestion import KnowledgeIngestionService


class FakeEmbedding:
    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(text)), float(index)] for index, text in enumerate(texts)]


class FakeRepository:
    def __init__(self, path: str):
        self.document = SimpleNamespace(id="doc-1", source_path=path)
        self.active_vectors: dict[str, list[float]] = {}
        self.saved: list[dict] = []
        self.version_no = 0

    async def get_document(self, document_id: str):
        return self.document if document_id == "doc-1" else None

    async def active_chunk_embeddings_by_hash(self, *, document_id: str, embedding_model: str | None = None):
        return dict(self.active_vectors)

    async def create_version(self, **_kwargs):
        self.version_no += 1
        return SimpleNamespace(id=f"version-{self.version_no}")

    async def save_chunks(self, *, chunks: list[dict], **_kwargs):
        self.saved = chunks
        return len(chunks)

    async def activate_version(self, **_kwargs):
        self.active_vectors = {chunk["content_hash"]: chunk["embedding"] for chunk in self.saved}

    async def mark_document_failed(self, **_kwargs):
        raise AssertionError("ingestion should not fail")


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "knowledge.txt"
        path.write_text("A" * 1000 + "\n" + "B" * 1000 + "\n" + "C" * 1000, encoding="utf-8")
        repository = FakeRepository(str(path))

        first_embedding = FakeEmbedding()
        first = await KnowledgeIngestionService(repository, first_embedding).ingest_document("doc-1")
        assert first.embedded_chunks == first.chunk_count and first.reused_chunks == 0
        assert sum(map(len, first_embedding.calls)) == first.chunk_count

        second_embedding = FakeEmbedding()
        second = await KnowledgeIngestionService(repository, second_embedding).ingest_document("doc-1")
        assert second.reused_chunks == second.chunk_count and second.embedded_chunks == 0
        assert second_embedding.calls == []

        path.write_text("A" * 1000 + "\n" + "B" * 1000 + "\n" + "C" * 999 + "D", encoding="utf-8")
        third_embedding = FakeEmbedding()
        third = await KnowledgeIngestionService(repository, third_embedding).ingest_document("doc-1")
        assert 0 < third.embedded_chunks < third.chunk_count
        assert third.reused_chunks + third.embedded_chunks == third.chunk_count
        assert sum(map(len, third_embedding.calls)) == third.embedded_chunks

        print({
            "first_all_embedded": True,
            "second_all_reused": True,
            "third_partial_embedded": True,
            "first": first,
            "second": second,
            "third": third,
            "pass": True,
        })


if __name__ == "__main__":
    asyncio.run(main())
