from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.infrastructure.qwen import QwenProvider
from app.repositories.knowledge import KnowledgeRepository
from app.services.understanding_config import understanding_config


@dataclass(frozen=True)
class IngestionResult:
    document_id: str
    version_id: str
    chunk_count: int
    reused_chunks: int
    embedded_chunks: int
    status: str


def _fixed_chunks(text: str, *, size: int = 400, overlap: int = 40) -> list[str]:
    """固定滑动窗口切块（向后兼容）。"""
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        return []
    step = max(1, size - overlap)
    return [normalized[index:index + size] for index in range(0, len(normalized), step)]


def _split_long(text: str, *, size: int) -> list[str]:
    """把超长块按句号/段落二次切到 <= size。"""
    parts = [p.strip() for p in re.split(r"(?<=[。！？；])|\n\n", text) if p.strip()]
    out: list[str] = []
    buf = ""
    for part in parts:
        if len(part) > size:
            if buf:
                out.append(buf)
                buf = ""
            for i in range(0, len(part), size):
                out.append(part[i:i + size])
        elif len(buf) + len(part) + 2 <= size:
            buf = f"{buf}\n\n{part}".strip() if buf else part
        else:
            if buf:
                out.append(buf)
            buf = part
    if buf:
        out.append(buf)
    return [c for c in out if c.strip()]


def _structure_chunks(text: str, *, size: int = 400) -> list[str]:
    """结构感知切块：按 Markdown 标题/表格/段落切，保住语义单元（字节验证效果好）。

    - 标题（#...）开头的整节为一个候选块
    - 表格（|...|）整块为一个候选
    - 段落（非空、非标题/表格）为一个候选
    候选合并到目标 size，超长候选再按句/段切。相比固定 1000 字符，显著提升
    语义聚焦度与向量相似度区分力（真/假查询相似度拉开）。
    """
    lines = [l.rstrip() for l in text.splitlines()]
    blocks: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("#"):
            content = [lines[i]]
            i += 1
            while i < n and not lines[i].strip().startswith("#"):
                content.append(lines[i])
                i += 1
            blocks.append("\n".join(content).strip())
        elif stripped.startswith("|") and stripped.endswith("|"):
            table = [lines[i]]
            i += 1
            while i < n and lines[i].strip().startswith("|"):
                table.append(lines[i])
                i += 1
            blocks.append("\n".join(table).strip())
        else:
            para = [lines[i]]
            i += 1
            while i < n and lines[i].strip() and not lines[i].strip().startswith("#") and not lines[i].strip().startswith("|"):
                para.append(lines[i])
                i += 1
            blocks.append("\n".join(para).strip())

    chunks: list[str] = []
    buf = ""
    for block in blocks:
        if len(block) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_long(block, size=size))
        elif len(buf) + len(block) + 2 <= size:
            buf = f"{buf}\n\n{block}".strip() if buf else block
        else:
            if buf:
                chunks.append(buf)
            buf = block
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


def chunk_text(text: str, *, mode: str = "structure", size: int = 400, overlap: int = 40) -> list[str]:
    """按配置切片文档文本。mode=structure（默认，结构感知）/ fixed（滑动窗口）。"""
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        return []
    if mode == "fixed":
        return _fixed_chunks(normalized, size=size, overlap=overlap)
    return _structure_chunks(normalized, size=size)


class KnowledgeIngestionService:
    def __init__(self, repository: KnowledgeRepository, embedding: QwenProvider) -> None:
        self.repository = repository
        self.embedding = embedding

    async def ingest_document(self, document_id: str) -> IngestionResult:
        document = await self.repository.get_document(document_id)
        if document is None:
            raise ValueError("knowledge document not found")
        # 当前 embedding 模型：写入 chunk 元数据，并在复用旧向量时校验模型一致
        embedding_model = getattr(getattr(self.embedding, "settings", None), "qwen_embedding_model", "") or "unknown"

        version = None
        try:
            source_path = Path(document.source_path)
            # 兼容 docker 相对路径（\app\uploads\...）在本地跑：按 basename 从项目 uploads 兜底
            if not source_path.exists():
                for candidate in (Path("uploads/knowledge") / source_path.name,
                                  Path.cwd() / "uploads" / "knowledge" / source_path.name):
                    if candidate.exists():
                        source_path = candidate
                        break
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
            ck = understanding_config.get_chunking()
            prepared = [
                (index, content, hashlib.sha256(content.encode("utf-8")).hexdigest())
                for index, content in enumerate(chunk_text(text, mode=ck["mode"], size=ck["size"], overlap=ck["overlap"]))
            ]
            reusable = await self.repository.active_chunk_embeddings_by_hash(document_id=document_id, embedding_model=embedding_model)
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
                    "metadata": {"parser_type": parser_type, "embedding_model": embedding_model},
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
