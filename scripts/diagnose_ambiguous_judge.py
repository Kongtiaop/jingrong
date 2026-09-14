#!/usr/bin/env python
"""诊断 ambiguous 带的 LLM-judge：为什么 0.5559 被判 no，而 0.6349 被判 yes？

背景（ISSUE-20260914-JUDGE-REJECTS-BORDERLINE-EVIDENCE）：
  开启 judge 后，"模型改写·问句式"（best_sim=0.5559）从 sufficient 翻转为 low_confidence，
  而"泛化术语"（0.6349）与"管线固定改写"（0.5610）保持 sufficient。
  需要确认 judge 是"合理拒绝无关引用"还是"过度保守误杀"。

本脚本打印：每个用例的检索引用前 N 条 + judge 的原始回答，便于人工判读。

用法：
  python scripts/diagnose_ambiguous_judge.py --intent risk_public
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CASES = [
    ("原文（无改写）", "我有没有风险限制"),
    ("管线固定改写", "我有没有风险限制 请说明相关制度或流程，并给出依据"),
    ("模型改写·场景化", "客户存在待处理风险记录时的含义及后续处理流程"),
    ("模型改写·问句式", "客户存在待处理风险记录时，是否影响交易权限？相关风控流程和客户须知是什么？"),
    ("泛化术语", "风险限制 适当性管理 制度 流程"),
]


async def main() -> int:
    parser = argparse.ArgumentParser(description="ambiguous 带 judge 行为诊断")
    parser.add_argument("--intent", default="risk_public")
    parser.add_argument("--preview", type=int, default=2, help="每个用例预览几条引用")
    args = parser.parse_args()

    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.qwen import QwenProvider
    from app.repositories.knowledge import KnowledgeRepository
    from app.services.customer_rag_answer import build_answerability_judge, _call_chat
    from app.services.customer_rag_service import CustomerRagService
    from app.services.knowledge_retrieval import WealthKnowledgeRetriever

    settings = get_settings()
    database = Database(settings)
    provider = QwenProvider(settings)
    judge = build_answerability_judge(provider)

    try:
        # 无 judge 的 service：只用来拿"检索到的引用"（复现生产 top_k/scope）
        retriever_service = CustomerRagService(
            WealthKnowledgeRetriever(KnowledgeRepository(database)),
            embedding_provider=provider, ambiguous_judge=None)

        for label, query in CASES:
            print("=" * 92)
            print(f"[{label}]")
            print(f"  query = {query!r}")
            result = await retriever_service.answer(query=query, intent=args.intent)
            sims = [c.vector_similarity for c in result.citations if c.vector_similarity is not None]
            best = max(sims) if sims else None
            print(f"  检索: evidence={result.evidence_status} "
                  f"best_sim={round(best, 6) if best is not None else None} n={len(result.citations)}")

            if not result.citations:
                print("  -> 无引用，judge 不会被调用（直接 reject）")
                continue

            contexts = [c.content for c in result.citations]
            verdict = await judge(query, contexts)
            print(f"  judge verdict = {verdict}")

            # 直接问一次，拿到原始文本，确认不是解析问题
            evidence = "\n\n".join(f"[引用 {i}] {c}" for i, c in enumerate(contexts, 1))
            prompt = (
                "你是财富管理客服助手。基于以下检索引用，判断能否回答用户的问题。\n"
                "如果引用足以回答，回答 yes；如果引用与问题无关或不足，回答 no。\n\n"
                f"用户问题：{query}\n\n检索引用：\n{evidence}\n\n只回答 yes 或 no。"
            )
            try:
                resp = await _call_chat(provider, model=settings.qwen_max_model,
                                        messages=[{"role": "user", "content": prompt}],
                                        temperature=0.0)
                raw = (resp.choices[0].message.content or "").strip()
                print(f"  judge 原始回答 = {raw[:80]!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"  judge 调用异常 = {type(exc).__name__}: {exc}")

            for c in result.citations[:args.preview]:
                snippet = c.content[:110].replace("\n", " ")
                print(f"    引用 sim={round(c.vector_similarity or 0, 4)} :: {snippet}")

        print("=" * 92)
        print("判读：")
        print("  若 judge 对'与问题相关但表述不同'的引用判 no → 属过度保守（误杀），")
        print("  应调整 judge 提示词（放宽到'是否包含可用于回答的制度依据'）或降低其介入门槛。")
        print("  若引用确实与问题无关 → judge 判断正确，问题在检索改写而非 judge。")
    finally:
        await provider.close()
        await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
