#!/usr/bin/env python
"""在**独立的临时表**上试验 BM25 索引定义，完全不触碰 knowledge_chunk。

为什么不在 knowledge_chunk 上试（上一版的错误）：
  该方案靠"事务内 DROP 索引 -> 建候选 -> 探测 -> ROLLBACK"来还原。
  但 PG 的 DDL 虽可事务化，**一旦 ROLLBACK 未按预期生效，原索引就真的没了、
  检索当场失效** —— 用一种会破坏现有能力的实验去诊断问题，风险不可接受。
  改为在临时表上复现同样的表结构，安全地找出可行定义。

本脚本：
  1. 建临时表 probe_bm25(id uuid pk, content text, + bm25 索引)，用完即 DROP
  2. 对每个候选定义：建索引 -> 插入一行 -> 记录成败 -> 删索引
  3. 汇总出"哪种定义能写入"，作为重建 knowledge_chunk 索引的依据

用法（容器内）：
  python scripts/diagnose_bm25_index_variants.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

TABLE = "probe_bm25_var"

CANDIDATES: list[tuple[str, str]] = [
    ("A 字段列表含 key_field: bm25 (id, content) + WITH key_field",
     f"CREATE INDEX probe_v ON {TABLE} USING bm25 (id, content) WITH (key_field = 'id')"),
    ("B 字段列表只有 content + WITH key_field（= 现有索引的形态）",
     f"CREATE INDEX probe_v ON {TABLE} USING bm25 (content) WITH (key_field = 'id')"),
    ("C 字段列表含 key_field，无 WITH",
     f"CREATE INDEX probe_v ON {TABLE} USING bm25 (id, content)"),
    ("D 仅 content，无 WITH",
     f"CREATE INDEX probe_v ON {TABLE} USING bm25 (content)"),
]


async def main() -> int:
    from sqlalchemy import text

    from app.core.settings import get_settings
    from app.db.session import Database

    settings = get_settings()
    database = Database(settings)

    async with database.session_factory() as session:
        print(f"FULLTEXT_BACKEND = {getattr(settings, 'fulltext_backend', '?')}")
        print("在独立临时表上实验，**不触碰 knowledge_chunk**\n")

        # 准备临时表（若上次异常退出残留，先清掉）
        await session.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        await session.execute(text(
            f"CREATE TABLE {TABLE} (id uuid PRIMARY KEY, content text NOT NULL)"))
        await session.commit()

        print("=" * 94)
        print(f"{'候选定义':<52}{'建索引':<10}{'写入':<10}")
        print("-" * 94)
        results: list[tuple[str, str, bool, bool, str]] = []
        for label, ddl in CANDIDATES:
            created = False
            wrote = False
            detail = ""
            try:
                await session.execute(text(ddl))
                await session.commit()
                created = True
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                detail = f"{type(exc).__name__}: {str(exc).splitlines()[0][:70]}"
            if created:
                try:
                    await session.execute(text(
                        f"INSERT INTO {TABLE} (id, content) "
                        f"VALUES (gen_random_uuid(), 'probe content 公司业务')"))
                    await session.commit()
                    wrote = True
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    detail = f"{type(exc).__name__}: {str(exc).splitlines()[0][:70]}"
                # 删掉该索引，试下一个
                try:
                    await session.execute(text("DROP INDEX IF EXISTS probe_v"))
                    await session.commit()
                except Exception:  # noqa: BLE001
                    await session.rollback()
            print(f"{label:<52}{'成功' if created else '失败':<10}"
                  f"{'成功' if wrote else ('失败' if created else '-'):<10}")
            if detail:
                print(f"    {detail}")
            results.append((label, ddl, created, wrote, detail))

        # 清理
        await session.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        await session.commit()

        print()
        print("=" * 94)
        print("结论")
        print("=" * 94)
        viable = [r for r in results if r[2] and r[3]]
        if viable:
            print(f"  可写入的定义（共 {len(viable)} 个）：")
            for label, ddl, _, _, _ in viable:
                print(f"    - {label}")
                print(f"      {ddl}")
            print()
            print("  => 用上述定义重建 knowledge_chunk 的索引即可。")
        else:
            print("  所有候选定义**都无法写入**。")
            print("  => 说明问题不在索引定义形态，而可能在于：")
            print("     - 该 ParadeDB 构建（0.25.6）本身对这张表的写入有缺陷/不兼容")
            print("     - 或写入路径涉及的表结构（如 vector 列/继承/分区）与 bm25 索引冲突")
            print("     此时建议：改用 tsvector 后端（FULLTEXT_BACKEND=tsvector + 删 bm25 索引），")
            print("     或升级/更换 ParadeDB 镜像版本。")
        print()
        print("  注：临时表只含 (id, content) 两列。若 knowledge_chunk 的失败")
        print("      与其它列（如 vector embedding）相关，本实验可能不覆盖。")

    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
