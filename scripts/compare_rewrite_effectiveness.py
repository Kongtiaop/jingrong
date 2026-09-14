#!/usr/bin/env python
"""根因验证：为什么模型的改写检索不到，而管线的固定改写能检索到？

背景（ISSUE-20260914-MODEL-REWRITE-WORSE-THAN-TEMPLATE）：
  "我有没有风险限制" 的 risk_public 子意图：
    pipeline 用固定改写 "请说明相关制度或流程，并给出依据"  → 第2次命中
    graph    用模型改写 "客户存在待处理风险记录时的含义及后续处理流程" → 3次全失败
  怀疑：模型改写过度"具体化用户场景"，偏离制度条文的表述方式。

本脚本对同一 query 的多种改写分别检索，打印 evidence_status / best_sim / 命中数，
用数据确认"哪种改写风格更贴近知识库"。

用法：
  python scripts/compare_rewrite_effectiveness.py --intent risk_public
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 三种改写风格对照
ORIGINAL = "我有没有风险限制"
TEMPLATE_STYLE = "我有没有风险限制 请说明相关制度或流程，并给出依据"          # 管线固定池
MODEL_SCENARIO = "客户存在待处理风险记录时的含义及后续处理流程"              # 模型改写（具体场景）
MODEL_QUESTION = "客户存在待处理风险记录时，是否影响交易权限？相关风控流程和客户须知是什么？"
GENERIC_TERMS = "风险限制 适当性管理 制度 流程"                              # 泛化术语


async def main() -> int:
    parser = argparse.ArgumentParser(description="改写风格检索效果对照")
    parser.add_argument("--intent", default="risk_public")
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

    cases = [
        ("原文（无改写）", ORIGINAL),
        ("管线固定改写", TEMPLATE_STYLE),
        ("模型改写·场景化", MODEL_SCENARIO),
        ("模型改写·问句式", MODEL_QUESTION),
        ("泛化术语", GENERIC_TERMS),
    ]

    try:
        # 必须与生产路径一致（customer_tools.py）地接上 ambiguous_judge——
        # 否则中间带(0.35~0.65)会走 fallback 死阈值而非 judge，结论会失真。
        # 实测差异：问句式改写 best_sim=0.5559
        #   无 judge -> >= 0.55 -> sufficient（脚本旧版结论）
        #   有 judge -> 中间带交给 judge -> 可能 reject（生产实际行为）
        for judge_mode in (False, True):
            service = CustomerRagService(
                WealthKnowledgeRetriever(KnowledgeRepository(database)),
                embedding_provider=provider,
                ambiguous_judge=build_answerability_judge(provider) if judge_mode else None,
            )
            print()
            print(f"===== ambiguous_judge = {judge_mode} "
                  f"({'生产路径' if judge_mode else '仅向量阈值'}) =====")
            print("=" * 92)
            print(f"{'改写风格':<18} {'evidence':<20} {'best_sim':<10} {'命中数':<6} 首个引用标题")
            print("=" * 92)
            for label, query in cases:
                result = await service.answer(query=query, intent=args.intent)
                sims = [c.vector_similarity for c in result.citations if c.vector_similarity is not None]
                best = max(sims) if sims else None
                title = (result.citations[0].title_path or "")[:24] if result.citations else "-"
                print(f"{label:<18} {result.evidence_status:<20} "
                      f"{(round(best, 6) if best is not None else 'None'):<10} "
                      f"{len(result.citations):<6} {title}")
        print("=" * 92)
        print("判读：")
        print("  1) 对比两种 judge 模式，确认 judge 是否是'同样改写结论不同'的原因；")
        print("  2) 对比各改写风格的 best_sim/命中数，确认模型改写风格是否偏离知识库语料。")
    finally:
        await provider.close()
        await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
