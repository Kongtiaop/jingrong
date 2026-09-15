#!/usr/bin/env python
"""清理知识库中的无用/错配数据（可重复执行、默认 dry-run）。

为什么需要清理（见 BUG_LOG ISSUE-20260914-CATEGORY-PERMISSION-CORPUS-MISMATCH）：
  库里存在三类"有问题"的数据：
  1. **测试残留**：faq 类目下的 `Upload contract`（契约测试上传的占位文档），
     它让"客服电话是多少"这类问题在 faq 里查到无关内容、相似度暴跌。
  2. **类目名错配**：类目 `business`（代码与 scope 用的是 `company_business`），
     永远检索不到。
  3. **孤儿权限**：权限 `staff` 的文档——`staff` 不在任何 KnowledgeScope 的
     permission_levels 中（public_customer 允许 public/customer_visible，
     internal_advisor 允许 public/internal/advisor），故**没有任何 scope 能取到**。

用法（容器内）：
  python scripts/cleanup_knowledge_junk.py            # 只列出，不删（默认 dry-run）
  python scripts/cleanup_knowledge_junk.py --apply    # 真正执行删除
  python scripts/cleanup_knowledge_junk.py --apply --keep-staff-clones
      # 保留公司业务原始文档（若要改为 public 而非删除，见下方说明）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 判定为"测试残留"的文件名特征（大小写不敏感）。
# 只列**具体已知的坏数据标识**，不用 'contract' / 'demo' / 'test' 这类宽泛词——
# 它们会误伤将来合法的文档名（如 "合同条款 contract terms.md"、"demo 客户案例.md"）。
# 宁可少删、人工复核，也不要自动删掉真实语料。
TEST_MARKERS = ("upload contract", "p1-demo-doc")
# 已知的代码类目（用于识别错配类目）
KNOWN_CATEGORIES = {
    "company_info", "company_business", "faq", "policy", "workflow", "risk_public",
    "risk", "suitability",
}
KNOWN_PERMISSIONS = {"public", "customer_visible", "internal", "advisor"}


async def main() -> int:
    parser = argparse.ArgumentParser(description="清理知识库无用/错配数据")
    parser.add_argument("--apply", action="store_true", help="真正执行删除（默认只列出）")
    args = parser.parse_args()

    from sqlalchemy import text

    from app.core.settings import get_settings
    from app.db.session import Database

    settings = get_settings()
    database = Database(settings)

    async with database.session_factory() as session:
        rows = (await session.execute(text(
            "SELECT d.id::text AS id, d.file_name, d.category, d.permission_level, "
            "       d.status, count(c.id) AS chunks "
            "FROM knowledge_document d "
            "LEFT JOIN knowledge_chunk c ON c.document_id = d.id "
            "GROUP BY d.id, d.file_name, d.category, d.permission_level, d.status "
            "ORDER BY d.category, d.file_name"))).fetchall()

    print("=" * 96)
    print("当前知识库文档清单与判定")
    print("=" * 96)
    print(f"{'file_name':<34}{'category':<18}{'perm':<10}{'status':<11}{'chunks':<8}{'判定'}")
    print("-" * 96)

    to_delete: list[tuple[str, str, str]] = []   # (id, file_name, reason)
    for r in rows:
        name = str(r.file_name)
        cat = str(r.category)
        perm = str(r.permission_level)
        reasons: list[str] = []
        lowered = name.lower()
        if any(m in lowered for m in TEST_MARKERS):
            reasons.append("测试残留")
        if cat not in KNOWN_CATEGORIES:
            reasons.append(f"类目错配({cat})")
        if perm not in KNOWN_PERMISSIONS:
            reasons.append(f"孤儿权限({perm})")
        verdict = " / ".join(reasons) if reasons else "保留"
        print(f"{name:<34}{cat:<18}{perm:<10}{str(r.status):<11}{int(r.chunks):<8}{verdict}")
        if reasons:
            to_delete.append((str(r.id), name, verdict))

    print()
    if not to_delete:
        print("没有需要清理的文档。")
        await database.dispose()
        return 0

    print(f"共 {len(to_delete)} 个文档被判定为需清理：")
    for did, name, reason in to_delete:
        print(f"  - {name}  ({reason})")

    if not args.apply:
        print()
        print("当前是 **dry-run**，未删除任何数据。确认无误后加 --apply 执行。")
        await database.dispose()
        return 0

    print()
    print("=" * 96)
    print("执行删除（chunk → version → document）")
    print("=" * 96)
    async with database.session_factory() as session:
        for did, name, reason in to_delete:
            c1 = await session.execute(text("DELETE FROM knowledge_chunk WHERE document_id = :d"), {"d": did})
            c2 = await session.execute(text("DELETE FROM knowledge_document_version WHERE document_id = :d"), {"d": did})
            c3 = await session.execute(text("DELETE FROM knowledge_document WHERE id = :d"), {"d": did})
            print(f"  删除 {name}: chunks={c1.rowcount} versions={c2.rowcount} doc={c3.rowcount}  ({reason})")
        await session.commit()

    async with database.session_factory() as session:
        remain = (await session.execute(text(
            "SELECT d.category AS cat, d.permission_level AS pl, count(c.id) AS n "
            "FROM knowledge_document d LEFT JOIN knowledge_chunk c ON c.document_id = d.id "
            "GROUP BY d.category, d.permission_level ORDER BY n DESC"))).fetchall()
    print()
    print("清理后剩余：")
    for r in remain:
        print(f"  {str(r.cat):<18} pl={str(r.pl):<12} {int(r.n):>4} chunks")

    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
