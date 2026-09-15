#!/usr/bin/env python
"""诊断 pg_search / BM25 索引状态，定位 `No key field defined` 的根因。

背景（ISSUE-20260914-PGSEARCH-BM25-KEY-FIELD-BLOCKS-INSERT）：
  在 ParadeDB pg16 容器内导入语料时，`INSERT INTO knowledge_chunk ... RETURNING` 报
  `asyncpg.exceptions.InternalServerError: No key field defined.`
  该错误**不是** PostgreSQL 原生错误，而是 **pg_search（ParadeDB BM25）** 在写入
  带 BM25 索引的表时，无法解析索引的 key_field 所致。
  本脚本查清三件事：
    1. 该库是否装了 pg_search、版本多少
    2. knowledge_chunk 上到底有哪些索引（是否是 bm25 索引、其定义/参数）
    3. FULLTEXT_BACKEND 配置与实际索引是否一致（不一致本身就是问题）

用法（容器内）：
  python scripts/diagnose_pg_search_index.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def main() -> int:
    from sqlalchemy import text

    from app.core.settings import get_settings
    from app.db.session import Database

    settings = get_settings()
    database = Database(settings)

    print("=" * 92)
    print("STEP 0：配置")
    print("=" * 92)
    backend = getattr(settings, "fulltext_backend", "?")
    print(f"  FULLTEXT_BACKEND = {backend}")
    print("  （tsvector = 内置全文检索；pg_search = ParadeDB BM25；两者对索引的要求不同）")

    async with database.session_factory() as session:
        print()
        print("=" * 92)
        print("STEP 1：pg_search 扩展是否安装")
        print("=" * 92)
        rows = (await session.execute(text(
            "SELECT extname, extversion FROM pg_extension ORDER BY extname"))).fetchall()
        for r in rows:
            print(f"  {str(r.extname):<24} {r.extversion}")
        has_pg_search = any(str(r.extname) == "pg_search" for r in rows)
        print(f"  => pg_search 已安装: {has_pg_search}")

        print()
        print("=" * 92)
        print("STEP 2：knowledge_chunk 上的索引（重点看 bm25 与其参数）")
        print("=" * 92)
        idx = (await session.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'knowledge_chunk' ORDER BY indexname"))).fetchall()
        bm25_indexes: list[str] = []
        for r in idx:
            name = str(r.indexname)
            definition = str(r.indexdef)
            tag = ""
            if "bm25" in definition.lower():
                bm25_indexes.append(name)
                tag = "   <== BM25 索引（会触发 key field 校验）"
            print(f"  {name}{tag}")
            print(f"      {definition}")

        print()
        print("=" * 92)
        print("STEP 3：判定")
        print("=" * 92)
        if bm25_indexes:
            print(f"  发现 {len(bm25_indexes)} 个 BM25 索引: {bm25_indexes}")
            print("  -> 写入 knowledge_chunk 时 pg_search 会对其建/更新索引条目，")
            print("     若该索引缺少有效的 key_field，就会报 'No key field defined'。")
            print("  -> 但注意：若 FULLTEXT_BACKEND=tsvector，这些 BM25 索引**根本没被使用**，")
            print("     却依然会在写入时阻拦 INSERT —— 即'不用的索引仍在制造故障'。")
            if backend == "tsvector":
                print()
                print("  **建议**：既然配置用的是 tsvector，这些 BM25 索引属于遗留物，")
                print("     可直接 DROP；或改回 tsvector 后重建索引。")
                print("     DROP INDEX 语句（确认后执行）：")
                for name in bm25_indexes:
                    print(f"       DROP INDEX IF EXISTS {name};")
        else:
            print("  knowledge_chunk 上**没有** BM25 索引。")
            print("  -> 'No key field defined' 可能来自其它表的 BM25 索引，")
            print("     或来自 pg_search 对其它对象的写入。请检查全库 BM25 索引：")
            all_bm25 = (await session.execute(text(
                "SELECT indexname, tablename, indexdef FROM pg_indexes "
                "WHERE indexdef ILIKE '%bm25%' ORDER BY tablename, indexname"))).fetchall()
            for r in all_bm25:
                print(f"     [{r.tablename}] {r.indexname}")
                print(f"        {r.indexdef}")
            if not all_bm25:
                print("     全库没有任何 bm25 索引 -> 错误可能另有来源（见下方提示）")

        print()
        print("=" * 92)
        print("STEP 4：尝试一次最小写入（区分是'写就失败'还是'特定行失败'）")
        print("=" * 92)
        try:
            await session.execute(text(
                "INSERT INTO knowledge_chunk (id, document_id, version_id, chunk_index, "
                "content, content_hash, title_path, metadata_json, status) "
                "SELECT gen_random_uuid(), d.id, v.id, 9999, 'diagnose-probe', 'probe-hash', "
                "'', '{}'::jsonb, 'active' "
                "FROM knowledge_document d JOIN knowledge_document_version v ON v.document_id = d.id "
                "LIMIT 1"))
            await session.rollback()
            print("  最小写入**成功**（已回滚）-> 说明不是'所有写入都失败'，")
            print("  需对比最小写入与实际写入的差异（如 embedding/大量参数）。")
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            print(f"  最小写入也**失败**: {type(exc).__name__}: {str(exc)[:200]}")
            print("  -> 说明是该表/索引层面的结构性问题，与具体数据无关。")

    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
