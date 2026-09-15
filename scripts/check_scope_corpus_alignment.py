#!/usr/bin/env python
"""语料与 scope 的对齐检查：scope 声明允许的类目，语料里到底有没有。

背景（ISSUE-20260914-INTENT-CATEGORY-FILTER-NEVER-APPLIED 的后续）：
  批量探针发现 `workflow` 意图的**语料为 0 chunks**，但
  `KnowledgeScope.public_customer()` 声明允许 6 个类目
  （company_info / company_business / faq / workflow / policy / risk_public）。
  即 **scope 声明了一个语料中不存在的类目**——这类"声明与数据脱节"会让
  "按类目收窄"的方案直接退化为"检索为空"，必须先把对齐情况查清。

本脚本输出：
  1. 语料中实际存在的类目 + chunk 数
  2. scope 声明允许的类目
  3. 两者的差集（声明了但没有 / 有但未声明）
  4. 每个类目的样例内容片段（人工确认语料语义是否与类目名相符）

用法（容器内）：
  python scripts/check_scope_corpus_alignment.py
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
    from app.common.knowledge_scope import KnowledgeScope

    settings = get_settings()
    database = Database(settings)

    scope = KnowledgeScope.public_customer()
    declared = set(scope.categories)
    print("=" * 84)
    print(f"scope 声明允许的类目 = {sorted(declared)}")
    print(f"scope 允许的权限级别   = {scope.permission_levels}")
    print("=" * 84)

    async with database.session_factory() as session:
        rows = (await session.execute(text(
            "SELECT d.category AS cat, d.permission_level AS pl, "
            "       count(c.id) AS n "
            "FROM knowledge_document d LEFT JOIN knowledge_chunk c ON c.document_id = d.id "
            "GROUP BY d.category, d.permission_level ORDER BY n DESC"))).fetchall()
        present = {str(r.cat): int(r.n) for r in rows}
        # 文档状态分布（看是否有文档存在但 status 非 active 而被过滤）
        status_rows = (await session.execute(text(
            "SELECT d.status AS st, count(*) AS n FROM knowledge_document d "
            "GROUP BY d.status"))).fetchall()
        statuses = {str(r.st): int(r.n) for r in status_rows}

        print("\n语料中实际存在的类目（按 chunk 数）:")
        for cat, n in present.items():
            mark = "" if cat in declared else "   <- 未在 scope 中声明（当前检索不到）"
            print(f"  {cat:<20} {n:>5} chunks{mark}")

        print(f"\n文档状态分布: {statuses}")
        print(f"注：permission_level 也参与过滤，以下是各类目下的分布")
        for r in rows:
            print(f"  {str(r.cat):<20} pl={str(r.pl):<18} {int(r.n):>5} chunks")

        missing = declared - set(present)
        print()
        print("-" * 84)
        if missing:
            print(f"**声明了但语料中不存在** 的类目 = {sorted(missing)}")
            print("  -> 任何映射到这些类目的意图，检索必然为空（收窄方案下会直接答不出）")
        else:
            print("scope 声明的类目在语料中均存在")

        extra = set(present) - declared
        if extra:
            print(f"**语料中存在但未声明** 的类目 = {sorted(extra)}")
            print("  -> 这些内容对客户侧检索**当前不可见**（可能是有意的权限隔离）")

        # 各类目样例，人工确认语义
        print()
        print("-" * 84)
        print("各类目样例（前 2 条，确认语料语义与类目名是否相符）")
        print("-" * 84)
        for cat in sorted(present):
            samples = (await session.execute(text(
                "SELECT c.content AS ct FROM knowledge_chunk c "
                "JOIN knowledge_document d ON d.id = c.document_id "
                "WHERE d.category = :cat LIMIT 2"), {"cat": cat})).fetchall()
            for s in samples:
                snippet = " ".join(str(s.ct).split())[:88]
                print(f"  [{cat}] {snippet}")

    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
