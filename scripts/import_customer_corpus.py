#!/usr/bin/env python
"""把 knowledge_corpus/ 下的客户可见语料导入知识库（可重复执行、带校验）。

为什么需要这个脚本（而不是直接调上传接口）：
  1. **校验类目与权限**。历史上语料里出现过类目 `business`（≠ 代码用的 `company_business`）、
     权限 `staff`（不在任何 KnowledgeScope 的 permission_levels 中）——
     这类数据"进得来、查不到、也不报错"，静默变成孤儿内容
     （见 BUG_LOG ISSUE-20260914-CATEGORY-PERMISSION-CORPUS-MISMATCH）。
     本脚本在导入前即校验：类目必须是代码已知值，权限必须是某个 scope 允许的值。
  2. **可重复执行**。同一文件重复导入时先删旧版本，避免堆积重复 chunk。
  3. **同步入库**（不等 outbox/worker），导入完立刻可检索，便于验证。

用法（容器内）：
  python scripts/import_customer_corpus.py --dry-run     # 只看计划，不写库
  python scripts/import_customer_corpus.py               # 真正导入
  python scripts/import_customer_corpus.py --verify-only # 只做导入后检索验证
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CORPUS_DIR = _ROOT / "knowledge_corpus"
# 政策类语料目录。**必须一并校验**：`policy` 类目的 R1–R5 曾经就是从这里进入线上答案的
# （"C4客户能买什么"命中的正是《个人投资者适当性管理指南》的匹配矩阵），
# 只校验 knowledge_corpus/ 会漏掉一半语料。
POLICY_DIR = _ROOT / "金融政策"

# 语料文件 → (类目, 权限)。两者都必须通过下方校验。
CORPUS_PLAN: dict[str, tuple[str, str]] = {
    "企业信息-客户版.md": ("company_info", "public"),
    "公司业务介绍-客户版.md": ("company_business", "public"),
    "高频问答对-客户版.md": ("faq", "public"),
    "产品服务手册-客户版.md": ("company_business", "public"),
}


def _known_categories() -> set[str]:
    """代码中真实使用的类目集合（取自各 scope 与意图枚举的并集）。"""
    from app.common.knowledge_scope import KnowledgeScope
    cats: set[str] = set()
    for scope in (KnowledgeScope.public_customer(), KnowledgeScope.internal_advisor()):
        cats.update(scope.categories)
    # 意图枚举（部分意图名与类目名不同，如 risk_public / product_recommendation 不建类目）
    return cats


def _known_permissions() -> set[str]:
    """所有 scope 允许的权限级别并集。`staff` 这类不在其中的值会被拒绝。"""
    from app.common.knowledge_scope import KnowledgeScope
    perms: set[str] = set()
    for scope in (KnowledgeScope.public_customer(), KnowledgeScope.internal_advisor()):
        perms.update(scope.permission_levels)
    return perms


# 数据模型中真实存在的风险等级标签（`product.risk_level` 与
# `customer_risk_assessment.risk_level` 实测均为 C1–C5）。
# 见 ISSUE-20260915-RISK-LEVEL-VOCAB-MISMATCH-R-AND-C。
_VALID_RISK_LEVELS = frozenset({"C1", "C2", "C3", "C4", "C5"})
# 禁止出现的等级写法 → 出现即视为语料与数据模型脱节。
# **为什么用正则而不是简单子串**：`R1` 会误伤 `R10`、英文缩写（如 `R&D`）等；
# 用 `R` 后紧跟单个 1-5 且非字母数字的边界，才能精确匹配"风险等级 R3"这类写法。
import re as _re  # noqa: E402
_FORBIDDEN_LEVEL_PATTERN = _re.compile(r"(?<![A-Za-z0-9])R[1-5](?![0-9A-Za-z])")


def validate_risk_level_vocabulary(text: str, filename: str) -> list[str]:
    """校验语料中的风险等级词汇必须与数据模型一致（**纯函数，便于单测**）。

    为什么需要这条校验（真实缺陷，已复现到用户答案层）：
    语料曾用 **R1–R5** 描述产品风险等级，而 `product.risk_level` 实际是 **C1–C5**。
    后果：答案是"C4进取型投资者可购买 **R1至R4**"——一句话混用两套编号，
    而 **R1–R5 在数据模型中根本不存在**，用户据此在 App 里找不到对应产品。

    为什么之前没被发现：检索只看语义相似度（R3 与 C3 语义等价 → 命中正常）、
    `evidence_status=sufficient`、答案通顺有引用 —— **所有流程指标都是绿的**，
    只有人肉读答案才能发现术语是错的。
    故必须在**导入期**用确定性校验拦住，而不是靠人工校对
    （原则：能靠校验保证的，不要只靠人工核对）。
    """
    errors: list[str] = []
    found = sorted({m.group(0) for m in _FORBIDDEN_LEVEL_PATTERN.finditer(text)})
    if found:
        errors.append(
            f"{filename}: 出现数据模型中不存在的风险等级写法 {found} —— "
            f"`product.risk_level` / `customer_risk_assessment.risk_level` 实测只有 "
            f"{sorted(_VALID_RISK_LEVELS)}；R1–R5 是历史语料的错误说法，"
            f"会让答案里混用两套编号（历史问题：用户被告知可买 'R1至R4'，而 App 里没有 R 级产品）")
    return errors


def _validate_corpus_text(path, text: str) -> list[str]:
    """语料**内容**校验（与类目/权限校验并列）。"""
    return validate_risk_level_vocabulary(text, path.name)


def _validate(category: str, permission: str) -> list[str]:
    """返回错误列表（空表示通过）。"""
    errors: list[str] = []
    cats = _known_categories()
    perms = _known_permissions()
    if category not in cats:
        errors.append(
            f"类目 {category!r} 不在代码已知类目中 {sorted(cats)} —— "
            f"导入后检索端无法命中（历史问题：语料里出现过 'business' 而代码用 'company_business'）")
    if permission not in perms:
        errors.append(
            f"权限 {permission!r} 不在任何 scope 允许的权限中 {sorted(perms)} —— "
            f"导入后将成为**孤儿数据**（历史问题：39 个 company_business chunk 权限为 'staff'，"
            f"public_customer 与 internal_advisor 都取不到）")
    # 面向客户的语料必须客户可见
    from app.common.knowledge_scope import KnowledgeScope
    if not KnowledgeScope.public_customer().allows(
            knowledge_base_id="x", category=category, permission_level=permission):
        errors.append(
            f"类目 {category!r} + 权限 {permission!r} 的组合对**客户侧不可见** —— "
            f"既然放进客户语料目录，就应当客户可见")
    return errors


async def main() -> int:
    parser = argparse.ArgumentParser(description="导入客户可见语料")
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印计划")
    parser.add_argument("--verify-only", action="store_true", help="只做检索验证")
    args = parser.parse_args()

    from app.core.settings import get_settings
    from app.db.session import Database

    settings = get_settings()
    database = Database(settings)

    if not args.verify_only:
        print("=" * 88)
        print("STEP 1：校验语料文件（类目 / 权限 / 风险等级词汇 / 文件存在）")
        print("=" * 88)
        problems: list[str] = []
        files: list[tuple[Path, str, str]] = []
        for name, (category, permission) in CORPUS_PLAN.items():
            path = CORPUS_DIR / name
            if not path.exists():
                problems.append(f"{name}: 文件不存在 ({path})")
                print(f"  [x] {name}: 文件不存在")
                continue
            errs = _validate(category, permission)
            # **内容校验**：风险等级词汇必须与数据模型一致（见 validate_risk_level_vocabulary）。
            # 这是"流程指标全绿但术语错误"这类缺陷的唯一拦截点 ——
            # 检索相似度、evidence_status、引用数都发现不了 R3 与 C3 的差别。
            errs.extend(_validate_corpus_text(path, path.read_text(encoding="utf-8")))
            if errs:
                problems.extend(f"{name}: {e}" for e in errs)
                print(f"  [x] {name}: 校验失败")
                for e in errs:
                    print(f"       - {e}")
            else:
                size = path.stat().st_size
                print(f"  [v] {name:<28} 类目={category:<18} 权限={permission:<8} {size} bytes")
                files.append((path, category, permission))

        if problems:
            print()
            print("校验未通过，**未写入任何数据**。请先修正上述问题。")
            if all("文件不存在" in p for p in problems):
                # 这些文件都在仓库里，容器内缺失只可能是**镜像没带上**：
                # Dockerfile.api 需要 `COPY knowledge_corpus`，且 .dockerignore 的 `*.md`
                # 会把它排除掉（必须配 `!knowledge_corpus/**` 例外）。
                print()
                print("提示：语料文件在仓库中存在但容器内没有 —— 说明**镜像未包含该目录**。")
                print("  检查 Dockerfile.api 是否 `COPY knowledge_corpus ./knowledge_corpus`，")
                print("  以及 .dockerignore 是否用 `!knowledge_corpus/**` 放行了 `*.md`。")
                print("  改完后需重新 build：docker compose -f docker-compose.p0.yml build api")
            await database.dispose()
            return 2

        print()
        print(f"共 {len(files)} 个文件通过校验。")

        if args.dry_run:
            print()
            print("=" * 88)
            print("附加校验：政策类语料（金融政策/）的风险等级词汇")
            print("=" * 88)
            pol_problems: list[str] = []
            if POLICY_DIR.exists():
                for p in sorted(POLICY_DIR.glob("*.md")):
                    errs = validate_risk_level_vocabulary(
                        p.read_text(encoding="utf-8"), p.name)
                    if errs:
                        pol_problems.extend(errs)
                        print(f"  [x] {p.name}")
                        for e in errs:
                            print(f"       - {e}")
                    else:
                        print(f"  [v] {p.name}")
            else:
                print(f"  （{POLICY_DIR} 不存在，跳过）")
            if pol_problems:
                print()
                print("政策类语料校验未通过 —— 该类目语料由 "
                      "`scripts/fix_rag_knowledge_meta.py --ingest` 导入，"
                      "请先修正后再导入。")
                await database.dispose()
                return 2
            print("--dry-run：仅校验，不写库。")
            await database.dispose()
            return 0

        print()
        print("=" * 88)
        print("STEP 2：删除同名旧版本（保证可重复执行，不堆积重复 chunk）")
        print("=" * 88)
        from sqlalchemy import text
        async with database.session_factory() as session:
            base_id = (await session.execute(text(
                "SELECT id FROM knowledge_base WHERE status = 'active' ORDER BY created_at LIMIT 1"))).scalar()
            if base_id is None:
                print("  [x] 找不到可用的知识库（knowledge_base 无 active 记录）")
                await database.dispose()
                return 2
            base_id = str(base_id)
            print(f"  目标知识库: {base_id}")
            for path, _cat, _pl in files:
                rows = (await session.execute(text(
                    "SELECT id::text AS id, file_name FROM knowledge_document WHERE file_name = :fn"),
                    {"fn": path.name})).fetchall()
                for row in rows:
                    did = str(row.id)
                    await session.execute(text("DELETE FROM knowledge_chunk WHERE document_id = :d"), {"d": did})
                    await session.execute(text("DELETE FROM knowledge_document_version WHERE document_id = :d"), {"d": did})
                    await session.execute(text("DELETE FROM knowledge_document WHERE id = :d"), {"d": did})
                    print(f"  删除旧版本: {row.file_name} ({did[:8]})")
            await session.commit()

        print()
        print("=" * 88)
        print("STEP 3：写入文档并同步切块入库")
        print("=" * 88)
        from app.infrastructure.qwen import QwenProvider
        from app.repositories.knowledge import KnowledgeRepository
        from app.services.knowledge_ingestion import KnowledgeIngestionService

        repository = KnowledgeRepository(database)
        provider = QwenProvider(settings)
        ingestion = KnowledgeIngestionService(repository, provider)
        inserted: list[str] = []
        try:
            for path, category, permission in files:
                raw = path.read_bytes()
                content_hash = hashlib.sha256(raw).hexdigest()
                # 用 repository 的正式入口建文档（会自动写 outbox 事件，保持与上传接口一致）
                document, _event = await repository.create_document(
                    knowledge_base_id=base_id,
                    document_key=f"corpus:{path.name}",
                    file_name=path.name,
                    source_path=str(path.resolve()),
                    file_type="md",
                    file_size=len(raw),
                    content_hash=content_hash,
                    category=category,
                    permission_level=permission,
                )
                # 同步执行切块+嵌入（不等 worker），导入完即可检索验证
                result = await ingestion.ingest_document(str(document.id))
                print(f"  [v] {path.name:<28} 类目={category:<18} "
                      f"chunks={result.chunk_count} (新增嵌入 {result.embedded_chunks})")
                inserted.append(str(document.id))
        finally:
            await provider.close()
        print(f"\n导入完成，共 {len(inserted)} 个文档。")

    # ---- 验证 ----
    print()
    print("=" * 88)
    print("STEP 4：检索验证（每个类目实际能否被客户侧检索到）")
    print("=" * 88)
    from sqlalchemy import text
    async with database.session_factory() as session:
        rows = (await session.execute(text(
            "SELECT d.category AS cat, d.permission_level AS pl, count(c.id) AS n "
            "FROM knowledge_document d LEFT JOIN knowledge_chunk c ON c.document_id = d.id "
            "GROUP BY d.category, d.permission_level ORDER BY n DESC"))).fetchall()
    print("  语料现状:")
    for r in rows:
        ok = _validate(str(r.cat), str(r.pl))
        mark = "[v]" if not ok else "[!]"
        note = "" if not ok else "  <- 客户侧不可见/孤儿"
        print(f"    {mark} {str(r.cat):<18} pl={str(r.pl):<12} {int(r.n):>4} chunks{note}")

    probes = [
        ("company_info", "你们公司叫什么名字"),
        ("company_info", "客服电话是多少"),
        ("company_business", "你们公司的主营业务是什么"),
        ("company_business", "公司提供哪些服务"),
        ("faq", "怎么联系你们"),
        ("faq", "基金申购后多久确认"),
    ]
    from app.infrastructure.qwen import QwenProvider
    from app.repositories.knowledge import KnowledgeRepository
    from app.services.customer_rag_service import (
        CustomerRagService, normalize_customer_query, scope_for_customer_intent,
    )
    from app.services.knowledge_retrieval import WealthKnowledgeRetriever

    provider = QwenProvider(settings)
    retriever = WealthKnowledgeRetriever(KnowledgeRepository(database))
    service = CustomerRagService(retriever, embedding_provider=provider)

    print()
    print(f"  {'intent':<18}{'query':<24}{'命中':<8}{'best':<9}{'判定'}")
    print("  " + "-" * 74)
    for intent, query in probes:
        normalized = normalize_customer_query(query, intent)
        emb = (await provider.embed([query]))[0]
        hits = await retriever.search(query=normalized, query_embedding=emb, top_k=5,
                                      scope=scope_for_customer_intent(intent), category=intent)
        sims = [float(h.vector_similarity) for h in hits if h.vector_similarity is not None]
        best = max(sims) if sims else None
        rag = await service.answer(query=query, intent=intent)
        print(f"  {intent:<18}{query:<24}{len(hits):<8}"
              f"{(f'{best:.4f}' if best else '-'):<9}{rag.evidence_status}"
              f"{' / 转人工' if rag.handoff_required else ''}")

    await provider.close()
    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
