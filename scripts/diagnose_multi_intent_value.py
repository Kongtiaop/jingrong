#!/usr/bin/env python
"""多知识意图拆 task 的**信息增量**诊断：拆开到底是在补信息，还是在把同一份资料说两遍？

背景（来自真实运行，非模拟）：
  `你们公司的主营业务是什么` 规划出 company_info + company_business 两个 task，
  两条路径都产出两个 task，但两条答案**内容高度重复**（都在讲"基金销售 + 保险经纪"）。
  于是产生一个必须查清的问题：
    - 多意图拆分真的检索到了**不同证据**吗？还是命中同一批 chunk？
    - 已知答案重复时，拆 task 是不是在"凑数字"？

本脚本直接跑真实检索，输出**可判定的量化指标**：
  1. 各 intent 的命中 chunk id 集合 + 相似度
  2. 两两 intent 的 chunk 重叠率（Jaccard）
  3. 各 intent 的答案文本相似度（difflib，无需额外依赖）
  4. 结论判定：是否真有信息增量

用法：
  python scripts/diagnose_multi_intent_value.py --query "你们公司的主营业务是什么"
  python scripts/diagnose_multi_intent_value.py --query "..." --intents company_info company_business
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import itertools
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def _text_sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


async def main() -> int:
    parser = argparse.ArgumentParser(description="多知识意图拆分的信息增量诊断")
    parser.add_argument("--query", required=True)
    parser.add_argument("--intents", nargs="*", default=None,
                        help="要对比的意图；默认取规划器对该 query 的输出")
    args = parser.parse_args()

    from app.core.settings import get_settings
    from app.db.session import Database
    from app.services.customer_intent import CustomerIntentPlanner
    from app.services.customer_tools import CustomerReadTools

    settings = get_settings()
    database = Database(settings)
    tools = CustomerReadTools(database)

    from app.infrastructure.qwen import QwenProvider
    from app.repositories.knowledge import KnowledgeRepository
    from app.services.knowledge_retrieval import WealthKnowledgeRetriever
    embedding_provider = QwenProvider(settings)
    retriever = WealthKnowledgeRetriever(KnowledgeRepository(database))

    intents = args.intents
    if not intents:
        plan = CustomerIntentPlanner().plan(args.query)
        intents = [t.intent for t in plan.tasks]
        print(f"规划器输出 intents = {intents}")
    if not intents:
        print("没有可对比的意图", file=sys.stderr)
        await database.dispose()
        return 2

    print(f"query = {args.query!r}\n")

    # 先暴露"意图到底改变了什么"——这决定了重叠是否可预期。
    # 实测（本脚本设计依据）：intent 只影响 ① normalize_customer_query 的关键词扩展
    # ② scope（但公开知识类意图的 scope **完全相同**），并不做语料分区。
    from app.services.customer_rag_service import (
        CustomerRagService, normalize_customer_query, scope_for_customer_intent,
    )
    print("-" * 78)
    print("意图实际影响了什么")
    print("-" * 78)
    for intent in intents:
        print(f"  [{intent}]")
        print(f"    normalized = {normalize_customer_query(args.query, intent)}")
        print(f"    scope      = {scope_for_customer_intent(intent)}")
    if len(intents) > 1:
        scopes = {scope_for_customer_intent(i) for i in intents}
        print(f"    => scope 去重后共 {len(scopes)} 种"
              f"{'（完全相同 → 不做语料分区，检索同一批 chunk）' if len(scopes) == 1 else ''}")
    print()

    # 重要：必须按 **service 的真实口径** 复现，否则结论无效。
    # `CustomerRagService.answer` 的门控/向量排序用**原始 query** 的嵌入，
    # 只有文本排序用 normalized_query（见 customer_rag_service.py:123-133 的注释与实现）。
    # 若这里用 normalized_query 去嵌入，会得到偏高的相似度，与线上不一致。
    gate_embedding = None
    if embedding_provider is not None:
        try:
            gate_embedding = (await embedding_provider.embed([args.query]))[0]
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 嵌入失败（{type(exc).__name__}），相似度将不可用：{exc}")
    svc = CustomerRagService(retriever, embedding_provider=embedding_provider)
    print(f"min_score={svc.min_score}  min_similarity={svc.min_similarity}  "
          f"thresholds={svc.thresholds}\n")

    # ---- 嵌入口径 A/B（定位"跨环境判定翻转"）----
    # 同一批 chunk、同一检索串，只换**门控嵌入用的查询**，看 best_sim 是否跨越阈值。
    # 这能直接回答："拒答/作答的差别是嵌入口径造成的，还是知识库数据造成的。"
    print("-" * 78)
    print("嵌入口径 A/B（同一 intent=company_info）")
    print("-" * 78)
    probe_intent = "company_info"
    probe_normalized = normalize_customer_query(args.query, probe_intent)
    probe_scope = scope_for_customer_intent(probe_intent)
    for label, text_to_embed in (("原始 query", args.query),
                                 ("膨胀 normalized", probe_normalized)):
        try:
            vec = (await embedding_provider.embed([text_to_embed]))[0]
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] 嵌入失败：{type(exc).__name__}")
            continue
        hits = await retriever.search(query=probe_normalized, query_embedding=vec,
                                      top_k=max(svc._config.get_top_k(), 5), scope=probe_scope)
        sims = [float(h.vector_similarity) for h in hits if h.vector_similarity is not None]
        best = max(sims) if sims else None
        gate = ("放行(>=answer)" if best is not None and best >= svc.thresholds["answer"]
                else "拒答(<reject)" if best is not None and best < svc.thresholds["reject"]
                else "judge区间")
        print(f"  [{label:<16}] best_sim={best if best else 0:.4f}  门控={gate}")
        print(f"      嵌入口径串 = {text_to_embed!r}")
    print()
    per_intent: dict[str, dict] = {}
    for intent in intents:
        # 走**真实检索路径**（与 service 同口径），拿到 chunk 集合与相似度
        normalized = normalize_customer_query(args.query, intent)
        hits = await retriever.search(
            query=normalized, query_embedding=gate_embedding,
            top_k=max(svc._config.get_top_k(), 5),
            scope=scope_for_customer_intent(intent),
        )
        chunk_ids = {str(h.chunk_id) for h in hits}
        sims = sorted((round(float(h.vector_similarity), 4)
                       for h in hits if h.vector_similarity is not None), reverse=True)
        # 再走一次 service，拿到**它自己的判定**（证据是否足够）
        rag = await svc.answer(query=args.query, intent=intent)
        best = sims[0] if sims else None
        per_intent[intent] = {
            "success": not rag.handoff_required, "error": None,
            "evidence": rag.evidence_status,
            "chunks": chunk_ids, "sims": sims,
            "best": best,
            "gate": ("放行(>=answer)" if best is not None and best >= svc.thresholds["answer"]
                     else "拒答(<reject)" if best is not None and best < svc.thresholds["reject"]
                     else "judge区间" if best is not None else "无相似度"),
            "answer": rag.message or "",
        }
        print(f"{'=' * 78}")
        print(f"[{intent}] handoff={rag.handoff_required} evidence={rag.evidence_status} "
              f"citations={len(rag.citations)} 检索命中={len(chunk_ids)}")
        print(f"  best_sim = {best}  → 门控: {per_intent[intent]['gate']}")
        print(f"  similarities = {sims}")
        print(f"  chunk_ids    = {sorted(chunk_ids)}")
        print(f"  message({len(rag.message or '')} chars) = {(rag.message or '')[:110]!r}")
        # service 自己的 top_k（动态取值，可能与脚本用的不同）——对齐后可排除"参数不同"这一解释
        print(f"  service.top_k_for_query = {svc._config.get_top_k_for_query(args.query)}"
              f"  get_top_k = {svc._config.get_top_k()}")

    print()
    print("=" * 78)
    print("两两对比：证据重叠 + 答案相似")
    print("=" * 78)
    verdicts = []
    for a, b in itertools.combinations(intents, 2):
        ja = per_intent[a]["chunks"]
        jb = per_intent[b]["chunks"]
        # 关键防伪：两边都**没有证据**时不能判 REDUNDANT。
        # `_jaccard(set(), set())` 返回 1.0，若不加这层判断，会在"两个意图都检索失败"
        # 时输出"REDUNDANT：同一批证据"——而证据数其实是 0，这是纯粹的假阳性
        # （本脚本首版就踩了这个坑，故显式拦掉）。
        if not ja and not jb:
            print(f"[{a}] vs [{b}]")
            print("  => NO_EVIDENCE：两边都无引用（检索/判定失败），无法判断重叠，需先查为何无证据")
            verdicts.append((a, b, "NO_EVIDENCE", None, None))
            continue
        overlap = _jaccard(ja, jb)
        sim = _text_sim(per_intent[a]["answer"], per_intent[b]["answer"])
        only_a = len(ja - jb)
        only_b = len(jb - ja)
        print(f"[{a}] vs [{b}]")
        print(f"  chunk 重叠(Jaccard) = {overlap:.3f}   "
              f"仅 {a} 独有={only_a}  仅 {b} 独有={only_b}  共有={len(ja & jb)}")
        print(f"  答案文本相似度       = {sim:.3f}")
        # 判定：重叠高且答案像 → 拆开无信息增量（同一份资料说两遍）
        if overlap >= 0.8 and sim >= 0.6:
            v = "REDUNDANT"
            print("  => REDUNDANT：命中同一批证据且答案高度相似，拆 task 未带来信息增量")
        elif only_a or only_b:
            v = "INCREMENTAL"
            print(f"  => INCREMENTAL：各自检索到独有证据（{only_a}/{only_b} 条），拆 task 有增量")
        else:
            v = "AMBIGUOUS"
            print("  => AMBIGUOUS：重叠中等，需人工看内容")
        verdicts.append((a, b, v, overlap, sim))

    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    kinds = {v[2] for v in verdicts}
    if not verdicts:
        print("  只有一个意图，无对比。")
    elif kinds == {"NO_EVIDENCE"}:
        print("  ⚠️ 全部意图对都缺证据 → **本次无法判断信息增量**。")
        print("  这是检索/判定失败，不是'拆分无价值'的证据；先查为何无引用（见上）。")
    elif kinds == {"REDUNDANT"}:
        print("  全部意图对均 REDUNDANT → **多知识意图拆分未带来信息增量**，")
        print("  拆 task 只是把同一份资料复述两遍；补齐 task 数是在凑数字，不是提升答案质量。")
    elif "INCREMENTAL" in kinds:
        print("  存在 INCREMENTAL 对 → 拆分确实带来独有证据，拆 task 是合理的。")
    else:
        print("  需人工判读（见上方各 intent 的答案原文）。")

    await embedding_provider.close()
    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
