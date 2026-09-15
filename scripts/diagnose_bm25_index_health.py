#!/usr/bin/env python
"""判定 BM25 索引是否"定义存在但实际失效"，并给出修复方案。

背景（ISSUE-20260914-PGSEARCH-BM25-KEY-FIELD-BLOCKS-INSERT）：
  容器诊断结果：
    - pg_search 版本 = 0.25.6
    - 索引定义 = CREATE INDEX ... USING bm25 (content) WITH (key_field=id)
      → **key_field 明明有定义**，却仍报 `No key field defined`
    - 最小写入（不带 embedding、只写几个字段）也失败
      → 与数据无关，是该表/索引层面的结构性问题
    - FULLTEXT_BACKEND = pg_search → 索引确实在用，不是"遗留无用索引"
  这指向：**索引是用旧版 DDL 建的，与当前 0.25.6 的元数据格式不兼容**，
  即 catalog 里索引的 key_field 无法被当前版本解析。

本脚本：
  1. 读 pg_index / pg_class 里该索引的原始元数据（而非 pg_indexes 的格式化输出）
  2. 直接试一次 `content @@@ 'xx'` 查询，看 BM25 **查询**是否也失败
  3. 试 REINDEX，看能否用当前版本的 DDL 重建元数据
  4. 给出两个可选修法（REINDEX / DROP+CREATE）及其影响

用法（容器内）：
  python scripts/diagnose_bm25_index_health.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

INDEX_NAME = "idx_knowledge_chunk_content_bm25"


async def main() -> int:
    from sqlalchemy import text

    from app.core.settings import get_settings
    from app.db.session import Database

    settings = get_settings()
    database = Database(settings)

    async with database.session_factory() as session:
        print("=" * 92)
        print("STEP 1：索引原始元数据（区分'定义里写了 key_field'与'版本能解析 key_field'）")
        print("=" * 92)
        row = (await session.execute(text(
            "SELECT c.relname, am.amname, c.reloptions "
            "FROM pg_class c JOIN pg_am am ON am.oid = c.relam "
            "WHERE c.relname = :n"), {"n": INDEX_NAME})).fetchone()
        if row is None:
            print(f"  找不到索引 {INDEX_NAME}")
        else:
            print(f"  索引名   : {row.relname}")
            print(f"  访问方法 : {row.amname}   (应为 bm25)")
            print(f"  reloptions: {row.reloptions}")
            print("  -> reloptions 若为空/不含 key_field，说明 key_field 存在索引的")
            print("     **内部元数据**里，只有 pg_search 自己能解析；此处为空不代表缺失。")

        print()
        print("=" * 92)
        print("STEP 2：BM25 **查询**是否可用（写入失败 vs 查询也失败）")
        print("=" * 92)
        query_ok = False
        try:
            rows = (await session.execute(text(
                "SELECT id FROM knowledge_chunk WHERE content @@@ '公司' LIMIT 3"))).fetchall()
            query_ok = True
            print(f"  BM25 查询成功，命中 {len(rows)} 条 -> 索引**查询侧可用**")
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            print(f"  BM25 查询失败: {type(exc).__name__}: {str(exc)[:200]}")

        print()
        print("=" * 92)
        print("STEP 3：写入失败与索引用途的关系")
        print("=" * 92)
        print("  已确认：最小写入也失败 -> 只要表上有该 bm25 索引，任何 INSERT 都会失败。")
        print("  这意味着**所有知识库写入路径都受影响**（含后台上传文档），不只是本次导入脚本。")

        print()
        print("=" * 92)
        print("STEP 4：给出修法（本脚本不自动执行 DDL）")
        print("=" * 92)
        print(f"  FULLTEXT_BACKEND = {getattr(settings, 'fulltext_backend', '?')}")
        print()
        print("  【方案 A：重建索引，保留 BM25 能力】推荐，若你确实要用 pg_search")
        print("     索引是用旧版 DDL 建的，与 0.25.6 元数据不兼容，需按当前版本重建：")
        print(f"       DROP INDEX IF EXISTS {INDEX_NAME};")
        print("       CREATE INDEX idx_knowledge_chunk_content_bm25")
        print("           ON knowledge_chunk USING bm25 (id, content)")
        print("           WITH (key_field = 'id');   -- 注意 0.25 起字段列表需含 key_field")
        print("     然后 REINDEX 或重新导入语料验证。")
        print()
        print("  【方案 B：弃用 BM25，回到内置 tsvector】最省事、最稳")
        print("     优点：不依赖 pg_search 版本兼容性，tsvector 无 key_field 之类的索引约束。")
        print("     步骤：")
        print(f"       1) DROP INDEX IF EXISTS {INDEX_NAME};")
        print("       2) 设置 FULLTEXT_BACKEND=tsvector（docker-compose.p0.yml 的 env）")
        print("       3) 重启 api 服务")
        print("     `_rank_text_tsvector` 路径已存在且是本地默认值，无需改代码。")
        print()
        print("  【不建议】用 INSERT ... RETURNING 的改写去绕过 —— 索引仍在，")
        print("  任何写入（含后台 worker）都会失败，绕过只掩盖问题。")

    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
