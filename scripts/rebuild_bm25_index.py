#!/usr/bin/env python
"""重建 knowledge_chunk 的 BM25 索引（保留 pg_search 能力），并逐步验证。

背景（ISSUE-20260914-PGSEARCH-BM25-KEY-FIELD-BLOCKS-INSERT）：
  容器实测：
    - pg_search 0.25.6，索引 idx_knowledge_chunk_content_bm25 存在
    - reloptions = ['key_field=id']        → 定义里 key_field 有写
    - BM25 **查询可用**（content @@@ '公司' 命中）→ 索引读侧正常
    - 但**任何 INSERT 都失败**（裸 INSERT 也失败）→ 写侧报 `No key field defined`
  读写不对称 + key_field 明明存在，典型成因是**索引元数据与当前扩展版本不一致**
  （索引由更早版本创建/经扩展升级后未重建），故按当前版本重建索引。

安全设计（重要）：
  - 默认 `--dry-run`，只打印将执行的语句与当前状态，不做任何 DDL
  - `--apply` 才执行，且**分步执行、每步单独报告**
  - 重建前后各做一次写入探测，用**结果**证明修好了（而不是假设）
  - 失败时**不删除原索引**（先建临时索引验证，成功后再替换），避免把检索彻底弄坏

用法（容器内）：
  python scripts/rebuild_bm25_index.py                 # 只看计划
  python scripts/rebuild_bm25_index.py --apply         # 执行重建
  python scripts/rebuild_bm25_index.py --reindex-only  # 只试 REINDEX（更轻量）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

INDEX = "idx_knowledge_chunk_content_bm25"


async def probe_insert(session) -> tuple[bool, str]:
    """写入探测：用真实表结构插一行再回滚，返回 (是否成功, 错误信息)。"""
    from sqlalchemy import text
    try:
        row = (await session.execute(text(
            "SELECT d.id AS did, v.id AS vid FROM knowledge_document d "
            "JOIN knowledge_document_version v ON v.document_id = d.id LIMIT 1"))).fetchone()
        if row is None:
            return False, "库里没有 document/version，无法探测"
        await session.execute(text(
            "INSERT INTO knowledge_chunk (id, document_id, version_id, chunk_index, "
            "content, content_hash, title_path, metadata_json, status) "
            "VALUES (gen_random_uuid(), :d, :v, 88888, 'rebuild-probe', 'rebuild-hash', "
            "'', '{}'::jsonb, 'active')"), {"d": str(row.did), "v": str(row.vid)})
        await session.rollback()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:150]}"


async def probe_query(session) -> tuple[bool, str]:
    from sqlalchemy import text
    try:
        rows = (await session.execute(text(
            "SELECT id FROM knowledge_chunk WHERE content @@@ '公司' LIMIT 1"))).fetchall()
        await session.rollback()
        return True, f"命中 {len(rows)} 条"
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:150]}"


async def index_def(session) -> str | None:
    from sqlalchemy import text
    row = (await session.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": INDEX})).fetchone()
    return str(row.indexdef) if row else None


async def main() -> int:
    parser = argparse.ArgumentParser(description="重建 BM25 索引")
    parser.add_argument("--apply", action="store_true", help="真正执行 DDL（默认 dry-run）")
    parser.add_argument("--reindex-only", action="store_true",
                        help="只执行 REINDEX（保留索引定义，最轻量）")
    args = parser.parse_args()

    from sqlalchemy import text

    from app.core.settings import get_settings
    from app.db.session import Database

    settings = get_settings()
    database = Database(settings)

    async with database.session_factory() as session:
        print("=" * 92)
        print("STEP 1：当前状态")
        print("=" * 92)
        definition = await index_def(session)
        print(f"  FULLTEXT_BACKEND = {getattr(settings, 'fulltext_backend', '?')}")
        print(f"  索引定义 = {definition}")
        if definition is None:
            print(f"  索引 {INDEX} 不存在 —— 无需重建，直接创建即可。")

        q_ok, q_msg = await probe_query(session)
        w_ok, w_msg = await probe_insert(session)
        print(f"  查询探测: {'成功' if q_ok else '失败'}  {q_msg}")
        print(f"  写入探测: {'成功' if w_ok else '失败'}  {w_msg}")

        if w_ok:
            print()
            print("  写入已经正常，无需重建。")
            await database.dispose()
            return 0

        print()
        print("=" * 92)
        print("STEP 2：重建计划")
        print("=" * 92)
        if args.reindex_only:
            print("  将执行（REINDEX，保留原索引定义）：")
            print(f"    REINDEX INDEX {INDEX};")
            print("  说明：REINDEX 用**当前扩展版本**重新构建索引元数据，")
            print("        不动索引定义，是最轻量的修法，优先尝试。")
            ddl_statements = [f"REINDEX INDEX {INDEX}"]
        else:
            # 先建**临时**索引验证新语法能否成功，成功后再删除旧索引并改名替换。
            # 这样即使 CREATE 语法不对，也**不会**把现有索引弄坏。
            #
            # 定义依据（ISSUE-20260914-PGSEARCH-BM25-KEY-FIELD-BLOCKS-INSERT）：
            #   在独立临时表上实测 4 种候选定义，**只有 A 能写入**：
            #     A bm25 (id, content) WITH (key_field='id')  建索引✓ 写入✓  <== 采用
            #     B bm25 (content)    WITH (key_field='id')  建索引✓ 写入✗  <== 库里现有的（坏）
            #     C bm25 (id, content) 无 WITH               建索引✗
            #     D bm25 (content)    无 WITH                建索引✗
            #   关键：**key_field 指名的列必须出现在字段列表中**，
            #   只写在 WITH 里不够 —— 这正是现有索引不可写的原因。
            print("  将执行（安全替换，先建临时索引，成功后再替换）：")
            print(f"    1) CREATE INDEX {INDEX}__new ON knowledge_chunk USING bm25 (id, content)")
            print(f"         WITH (key_field = 'id');")
            print(f"    2) 写入探测（用新索引验证 INSERT 是否恢复）")
            print(f"    3) DROP INDEX {INDEX};")
            print(f"    4) ALTER INDEX {INDEX}__new RENAME TO {INDEX};")
            print("  说明：第 1 步失败则**直接中止**，原索引保持不变，检索不受影响。")
            ddl_statements = [
                f"CREATE INDEX {INDEX}__new ON knowledge_chunk USING bm25 (id, content) "
                f"WITH (key_field = 'id')",
            ]

        if not args.apply:
            print()
            print("当前是 **dry-run**，未执行任何 DDL。确认后加 --apply。")
            await database.dispose()
            return 0

        print()
        print("=" * 92)
        print("STEP 3：执行")
        print("=" * 92)
        for stmt in ddl_statements:
            print(f"  执行: {stmt[:100]}...")
            try:
                await session.execute(text(stmt))
                await session.commit()
                print("    -> 成功")
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                print(f"    -> 失败: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
                print()
                print("  **重建失败，已中止。原索引未被删除，检索能力不受影响。**")
                print("  下一步建议：改用 --reindex-only 试 REINDEX；")
                print("  或回退 tsvector 后端（FULLTEXT_BACKEND=tsvector + 删索引）。")
                await database.dispose()
                return 1

        if not args.reindex_only:
            # 用新索引验证写入
            w_ok2, w_msg2 = await probe_insert(session)
            print(f"  新索引下的写入探测: {'成功' if w_ok2 else '失败'}  {w_msg2}")
            if not w_ok2:
                print()
                print("  **新索引仍无法写入**，故不替换旧索引（避免白折腾）。")
                print(f"  已创建的临时索引 {INDEX}__new 保留，可手工 DROP。")
                await database.dispose()
                return 1
            print(f"  删除旧索引 {INDEX} ...")
            await session.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))
            print(f"  重命名 {INDEX}__new -> {INDEX} ...")
            await session.execute(text(f"ALTER INDEX {INDEX}__new RENAME TO {INDEX}"))
            await session.commit()
            print("  -> 完成")

        print()
        print("=" * 92)
        print("STEP 4：重建后验证")
        print("=" * 92)
        q_ok, q_msg = await probe_query(session)
        w_ok, w_msg = await probe_insert(session)
        print(f"  查询探测: {'成功' if q_ok else '失败'}  {q_msg}")
        print(f"  写入探测: {'成功' if w_ok else '失败'}  {w_msg}")
        print(f"  索引定义 = {await index_def(session)}")
        print()
        if q_ok and w_ok:
            print("  **读写均正常，重建成功。** 可以继续执行语料导入。")
        else:
            print("  仍未完全恢复，请把以上输出反馈。")

    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
