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


class _ProbeRollback(Exception):
    """内部信号：探测写入成功，用异常退出 SAVEPOINT 块以触发局部回滚。"""


_PROBE_INSERT = (
    "INSERT INTO knowledge_chunk (id, document_id, version_id, chunk_index, "
    "content, content_hash, title_path, metadata_json, status) "
    "VALUES (gen_random_uuid(), :d, :v, 88888, 'rebuild-probe', 'rebuild-hash', "
    "'', '{}'::jsonb, 'active')"
)


async def probe_insert(session, *, use_savepoint: bool = False) -> tuple[bool, str]:
    """写入探测：插一行后回滚，返回 (是否成功, 错误信息)。

    **重要（踩过的坑）**：探测若直接 `session.rollback()`，会把调用方**同一事务里
    已执行的 DDL 一起回滚掉**。重建场景正是"DROP+CREATE 与探测在同一事务"，
    若无脑 rollback，重建会被静默撤销，而脚本却报告"写入探测成功" —— 假成功。

    故 `use_savepoint=True` 时用 SAVEPOINT 包住探测：只回滚探测本身，
    **不影响外层事务的 DDL**。
    """
    from sqlalchemy import text
    try:
        row = (await session.execute(text(
            "SELECT d.id AS did, v.id AS vid FROM knowledge_document d "
            "JOIN knowledge_document_version v ON v.document_id = d.id LIMIT 1"))).fetchone()
        if row is None:
            return False, "库里没有 document/version，无法探测"
        params = {"d": str(row.did), "v": str(row.vid)}
        if use_savepoint:
            try:
                # 写入成功 -> 主动抛异常，让 begin_nested 只回滚这个 SAVEPOINT
                async with session.begin_nested():
                    await session.execute(text(_PROBE_INSERT), params)
                    raise _ProbeRollback
            except _ProbeRollback:
                return True, ""
            except Exception as exc:  # noqa: BLE001
                # SAVEPOINT 已回滚，外层 DDL 完好
                return False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:150]}"
        await session.execute(text(_PROBE_INSERT), params)
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
            # 为什么不能"先建新索引再替换"（上一版的做法，实测失败）：
            #   ParadeDB 限制 **一张表只能有一个 bm25 索引**，多建会报
            #   `a relation may only have one ParadeDB index`。
            #   故替换只能 DROP 后 CREATE，没有"并存再切换"的余地。
            #
            # 失败如何保证安全：把 DROP+CREATE **放进同一个事务**。
            #   PostgreSQL 的 DDL 可事务化，若 CREATE 失败（语法/版本不符），
            #   整个事务回滚，**旧索引自动恢复**，不会出现"索引没了、检索也挂了"。
            #
            # 定义依据（ISSUE-20260914-PGSEARCH-BM25-KEY-FIELD-BLOCKS-INSERT）：
            #   独立临时表实测 4 种候选，**只有 A 能写入**：
            #     A bm25 (id, content) WITH (key_field='id')  建索引✓ 写入✓  <== 采用
            #     B bm25 (content)    WITH (key_field='id')  建索引✓ 写入✗  <== 库里现有（坏）
            #     C bm25 (id, content) 无 WITH               建索引✗
            #     D bm25 (content)    无 WITH                建索引✗
            #   关键：**key_field 指名的列必须出现在字段列表中**。
            print("  将执行（单事务内 DROP+CREATE，失败自动回滚恢复旧索引）：")
            print(f"    BEGIN;")
            print(f"      DROP INDEX IF EXISTS {INDEX};")
            print(f"      CREATE INDEX {INDEX} ON knowledge_chunk USING bm25 (id, content)")
            print(f"          WITH (key_field = 'id');")
            print(f"      -- 写入探测；失败则整体回滚，旧索引恢复")
            print(f"    COMMIT;")
            print("  说明：ParadeDB 限制一表只能有一个 bm25 索引，故无法'先建再切'；")
            print("        用事务保证原子性，是本场景下唯一安全的方式。")
            ddl_statements = [
                f"DROP INDEX IF EXISTS {INDEX}",
                f"CREATE INDEX {INDEX} ON knowledge_chunk USING bm25 (id, content) "
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

        if args.reindex_only:
            # REINDEX 单独走：不动定义，改错后无需回滚语义
            try:
                await session.execute(text(ddl_statements[0]))
                await session.commit()
                print("  REINDEX -> 成功")
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                print(f"  REINDEX -> 失败: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
                await database.dispose()
                return 1
        else:
            # DROP + CREATE + 写入探测，**全部放在同一事务**里。
            # 任何一步失败 -> 整体回滚 -> 旧索引自动恢复（不会出现"索引没了"）。
            # 之所以必须同事务：ParadeDB 限制一表只能有一个 bm25 索引，
            # 无法"先建新索引再切换"，只能先删后建；若不原子化，
            # CREATE 失败就会留下"没有 BM25 索引"的状态。
            try:
                print(f"  DROP INDEX IF EXISTS {INDEX} ...")
                await session.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))
                print("  CREATE INDEX (id, content) WITH (key_field='id') ...")
                await session.execute(text(ddl_statements[1]))
                # 在同一事务内探测写入（未提交前即可见新索引的效果）。
                # 必须 use_savepoint=True：否则探测内部的 rollback 会把
                # 上面刚执行的 DROP+CREATE 一起撤销，造成"假成功"。
                w_ok2, w_msg2 = await probe_insert(session, use_savepoint=True)
                if not w_ok2:
                    await session.rollback()
                    print(f"  写入探测: 失败  {w_msg2}")
                    print()
                    print("  **新定义仍无法写入，已回滚。旧索引恢复，检索不受影响。**")
                    await database.dispose()
                    return 1
                print("  写入探测: 成功")
                await session.commit()
                print("  -> 已提交，索引重建完成")
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                print(f"  失败: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
                print()
                print("  **DDL 失败，事务已回滚 —— 旧索引自动恢复，检索不受影响。**")
                await database.dispose()
                return 1

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
