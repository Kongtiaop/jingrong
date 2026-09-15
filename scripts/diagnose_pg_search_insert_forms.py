#!/usr/bin/env python
"""定位 BM25 索引下 INSERT 失败的确切触发条件（索引本身健康：查询可用、key_field 存在）。

已确认事实：
  - reloptions = ['key_field=id']            → key_field 存在且格式正确
  - BM25 查询可用（content @@@ '公司' 命中）  → 索引读写引擎本身正常
  - 最小 INSERT 失败                          → 问题**只在写入路径**
  - FULLTEXT_BACKEND = pg_search

因此"旧版 DDL 不兼容"的推断**不成立**（key_field 能被解析，否则查询也用不了）。
真正要区分的是：到底是哪种写入形态触发 pg_search 的 key field 校验。
本脚本逐个变量对照测试（每次都在事务内执行后回滚，不留数据）：

  T1 裸 INSERT（无 RETURNING、无 ORM 批量）
  T2 INSERT ... RETURNING id
  T3 INSERT ... RETURNING created_at, id   （SQLAlchemy ORM 的默认形态）
  T4 多行 INSERT ... VALUES (...),(...) + RETURNING（add_all 批量形态）

区别出哪一项触发后，修法就有了明确依据：
  - 若 T1 成功而 T2/T3 失败 → RETURNING 触发；修法是写入改用不带 RETURNING 的方式
  - 若 T1 失败           → 该表上任何 INSERT 都不行；只能重建索引或换后端
  - 若 T4 失败而 T1~T3 成功 → 批量（insertmanyvalues）触发；修法是分批单行写入

用法（容器内）：
  python scripts/diagnose_pg_search_insert_forms.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

BASE_COLS = ("id, document_id, version_id, chunk_index, content, content_hash, "
             "title_path, metadata_json, status")


async def _pick_refs(session):
    from sqlalchemy import text
    row = (await session.execute(text(
        "SELECT d.id AS did, v.id AS vid FROM knowledge_document d "
        "JOIN knowledge_document_version v ON v.document_id = d.id LIMIT 1"))).fetchone()
    return (str(row.did), str(row.vid)) if row else (None, None)


async def main() -> int:
    from sqlalchemy import text

    from app.core.settings import get_settings
    from app.db.session import Database

    settings = get_settings()
    database = Database(settings)

    async with database.session_factory() as session:
        did, vid = await _pick_refs(session)
        if did is None:
            print("库里没有可用的 document/version，无法构造插入测试", file=sys.stderr)
            await database.dispose()
            return 2
        print(f"使用 document_id={did[:8]} version_id={vid[:8]}\n")

        async def probe(label: str, sql: str, params: dict) -> bool:
            try:
                await session.execute(text(sql), params)
                await session.rollback()
                print(f"  [OK ] {label}")
                return True
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                msg = str(exc).splitlines()[0][:120]
                print(f"  [ERR] {label}")
                print(f"        {type(exc).__name__}: {msg}")
                return False

        print("=" * 92)
        print("对照测试（每次执行后回滚，不留数据）")
        print("=" * 92)

        t1 = await probe(
            "T1 裸 INSERT（无 RETURNING）",
            f"INSERT INTO knowledge_chunk ({BASE_COLS}) "
            f"VALUES (gen_random_uuid(), :d, :v, 9001, 'p1', 'h1', '', '{{}}'::jsonb, 'active')",
            {"d": did, "v": vid})

        t2 = await probe(
            "T2 INSERT ... RETURNING id",
            f"INSERT INTO knowledge_chunk ({BASE_COLS}) "
            f"VALUES (gen_random_uuid(), :d, :v, 9002, 'p2', 'h2', '', '{{}}'::jsonb, 'active') "
            f"RETURNING id",
            {"d": did, "v": vid})

        t3 = await probe(
            "T3 INSERT ... RETURNING created_at, id  (ORM 默认形态)",
            f"INSERT INTO knowledge_chunk ({BASE_COLS}) "
            f"VALUES (gen_random_uuid(), :d, :v, 9003, 'p3', 'h3', '', '{{}}'::jsonb, 'active') "
            f"RETURNING created_at, id",
            {"d": did, "v": vid})

        t4 = await probe(
            "T4 多行 INSERT + RETURNING (add_all 批量形态)",
            f"INSERT INTO knowledge_chunk ({BASE_COLS}) VALUES "
            f"(gen_random_uuid(), :d, :v, 9004, 'p4a', 'h4a', '', '{{}}'::jsonb, 'active'), "
            f"(gen_random_uuid(), :d, :v, 9005, 'p4b', 'h4b', '', '{{}}'::jsonb, 'active') "
            f"RETURNING created_at, id",
            {"d": did, "v": vid})

        # pgvector 的字符串字面量形态（'[...]'::vector）是文档写法；
        # 不用 array_fill(...)::vector —— 那是未经验证的写法，
        # 若它自身语法有误，T5 会因**别的原因**失败，从而误导判定。
        zero_vec = "[" + ",".join(["0"] * 1024) + "]"
        t5 = await probe(
            "T5 带 embedding 的单行 INSERT（验证是否与向量列有关）",
            f"INSERT INTO knowledge_chunk ({BASE_COLS}, embedding) "
            f"VALUES (gen_random_uuid(), :d, :v, 9006, 'p6', 'h6', '', '{{}}'::jsonb, 'active', "
            f":emb::vector)",
            {"d": did, "v": vid, "emb": zero_vec})

        print()
        print("=" * 92)
        print("判定")
        print("=" * 92)
        results = {"T1 裸INSERT": t1, "T2 RETURNING id": t2,
                   "T3 RETURNING created_at,id": t3, "T4 多行+RETURNING": t4,
                   "T5 带embedding": t5}
        for k, v in results.items():
            print(f"  {k:<30} {'成功' if v else '失败'}")

        print()
        if not t1:
            print("  => 连**裸 INSERT 都失败**：该表上任何写入都不行。")
            print("     只能【重建索引】或【换 tsvector 后端】，写入方式改写无效。")
        elif not (t2 and t3 and t4):
            print("  => 裸 INSERT 成功，但**带 RETURNING 的形态失败**。")
            print("     修法：写入路径避开 RETURNING（如用不带 RETURNING 的 INSERT，")
            print("     或 save_chunks 改为 flush 前显式构造 id、commit 后不取回默认值）。")
        elif not t5:
            print("  => 与 embedding 列有关。")
        else:
            print("  => 本组对照**全部成功**，与导入时的失败不一致。")
            print("     说明触发条件还依赖其它因素（如参数数量、语句长度、payload 大小）。")
            print("     建议复现时逐步逼近：先 1 行、再 5 行、再 20 行，看从第几行开始失败。")

    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
