#!/usr/bin/env python
"""验证 RAG 检索判定是否可复现（同一 query 连续多次结果应完全一致）。

背景：ISSUE-20260914-RAG-EVIDENCE-THRESHOLD-MARGIN —— 同一 query 两次运行结论不同
（partially_completed vs needs_clarification），怀疑检索层存在非确定性排序，
导致 best_sim 在阈值边缘抖动。本脚本对同一 query 连续检索 N 次，
打印 evidence_status / best_sim / top citation 集合的哈希，用于确认是否已修复。

用法：
  python scripts/check_rag_determinism.py --query "你们公司的主营业务是什么" --intent company_info -n 5
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


async def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索判定可复现性检查")
    parser.add_argument("--query", required=True)
    parser.add_argument("--intent", default="company_info")
    parser.add_argument("-n", "--times", type=int, default=5)
    parser.add_argument("--with-judge", action="store_true",
                        help="启用 ambiguous_judge（默认关，以隔离检索层）")
    args = parser.parse_args()

    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.qwen import QwenProvider
    from app.repositories.knowledge import KnowledgeRepository
    from app.services.customer_rag_answer import build_answerability_judge
    from app.services.customer_rag_service import CustomerRagService
    from app.services.knowledge_retrieval import WealthKnowledgeRetriever

    settings = get_settings()
    database = Database(settings)
    provider = QwenProvider(settings)
    judge = build_answerability_judge(provider) if args.with_judge else None

    signatures: list[str] = []
    try:
        service = CustomerRagService(
            WealthKnowledgeRetriever(KnowledgeRepository(database)),
            embedding_provider=provider,
            ambiguous_judge=judge,
        )
        print(f"query  = {args.query!r}")
        print(f"intent = {args.intent}   judge={args.with_judge}   times={args.times}")
        print("-" * 78)
        for index in range(args.times):
            result = await service.answer(query=args.query, intent=args.intent)
            sims = [c.vector_similarity for c in result.citations if c.vector_similarity is not None]
            best = max(sims) if sims else None
            # 对"命中集合 + 顺序"做签名：顺序变化也会改变哈希，能暴露排序不稳定
            key = "|".join(c.chunk_id for c in result.citations)
            sig = hashlib.sha256(key.encode()).hexdigest()[:12]
            signatures.append(f"{result.evidence_status}:{best}:{sig}")
            print(f"  [{index}] evidence={result.evidence_status:20s} "
                  f"best_sim={best if best is None else round(best, 6)} "
                  f"n={len(result.citations):2d} hit_sig={sig}")
    finally:
        await provider.close()
        await database.dispose()

    print("-" * 78)
    unique = set(signatures)
    if len(unique) == 1:
        print(f"✅ 可复现：{args.times} 次结果完全一致")
        return 0
    print(f"❌ 不可复现：{args.times} 次出现 {len(unique)} 种结果")
    for item in sorted(unique):
        print(f"   {item}   x{signatures.count(item)}")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
