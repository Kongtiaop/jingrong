#!/usr/bin/env python
"""本轮改动的回归探针（需要 DB + 真实检索，故用脚本而非 pytest）。

覆盖本轮 12 个生产文件的改动面，重点验证"改动没有破坏原有能力"：
  A. scope 收窄（customer_rag_service）—— 3 个意图收窄后仍能检索；workflow/risk_public 保持全开
  B. policy 触发词扩展（customer_intent）—— 制度类可命中，且不抢占流程/交易类
  C. 事实传递（customer_task_executor / model_tools / graph 分层）—— 各依赖场景按层执行
  D. 确定性路径未受影响 —— 订单/工单/资产等仍正常
  E. 等级读取（customer_query_router）—— 各等级客户正确读出
  F. 等级比对口径收敛（suitability_service）—— 门禁判定与既有语义一致

用法（容器内）：
  python scripts/regression_probe.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 各等级客户（均有 active 风险测评）
CUSTOMERS = {
    "risk_c1_demo": "9e9d1de7-71ec-49d4-8948-7432b841edf1",
    "risk_c2_demo": "73fda73f-c9f3-4e1a-a06b-6f6bb3307c91",
    "risk_c3_demo": "eb172fae-a980-4afd-a0ac-32ce4d6df59e",
    "risk_c4_demo": "94b0b3b9-f514-40b2-9373-f187aa63482a",
    "risk_c5_demo": "8886f88a-27b1-465f-9f15-a86487a7f7f6",
}

# 知识类回归用例：覆盖各意图（scope 收窄后必须仍可检索）
KNOWLEDGE_CASES = [
    ("company_info", "你们公司叫什么名字"),
    ("company_info", "客服电话是多少"),
    ("company_business", "你们公司的主营业务是什么"),
    ("company_business", "公司提供哪些服务"),
    ("faq", "怎么联系你们"),
    ("policy", "什么是双录"),
    ("policy", "风险等级怎么划分"),
    ("workflow", "开户流程是怎样的"),
    ("risk_public", "投资者适当性管理是什么"),
]

# 确定性事实路径：不应受知识检索改动影响
FACT_CASES = [
    ("我的资产有多少", "asset_summary"),
    ("我的持仓", "asset_summary"),
    ("我的订单 ORD-123456 状态", "order_status"),
    ("我的工单进展如何", "workorder_status"),
    ("我有没有风险限制", "risk_restriction"),
    ("现金管理保本计划在售吗", "product_status"),
]


def _line(char="-", n=98):
    return char * n


async def main() -> int:
    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.qwen import QwenProvider
    from app.repositories.knowledge import KnowledgeRepository
    from app.services.customer_intent import CustomerIntentPlanner
    from app.services.customer_query_router import CustomerQueryRouter
    from app.services.customer_rag_service import (
        CustomerRagService, normalize_customer_query, scope_for_customer_intent,
    )
    from app.services.knowledge_retrieval import WealthKnowledgeRetriever
    from app.services.suitability_service import is_suitable, risk_rank

    settings = get_settings()
    db = Database(settings)
    provider = QwenProvider(settings)
    retriever = WealthKnowledgeRetriever(KnowledgeRepository(db))
    svc = CustomerRagService(retriever, embedding_provider=provider)
    ok = True

    # ---- A. scope 收窄回归 ----
    print(_line("="))
    print("A. scope 收窄：3 个意图收窄 / 2 个保持全开")
    print(_line("="))
    wide = ("company_info", "company_business", "faq", "workflow", "policy", "risk_public")
    for intent in wide:
        scope = scope_for_customer_intent(intent)
        print(f"  {intent:<18} categories={scope.categories}")
    for intent in ("company_info", "company_business", "faq"):
        assert scope_for_customer_intent(intent).categories == (intent,), f"{intent} 应收窄"
    for intent in ("workflow", "risk_public"):
        assert scope_for_customer_intent(intent).categories == wide, \
            f"{intent} 语料为 0，必须保持全开（否则检索为空）"
    print("  => 收窄集合正确")

    print()
    print(_line("="))
    print("B. 知识检索：各意图收窄后是否仍可检索（命中>=1 且非 handoff）")
    print(_line("="))
    for intent, query in KNOWLEDGE_CASES:
        normalized = normalize_customer_query(query, intent)
        emb = (await provider.embed([query]))[0]
        hits = await retriever.search(query=normalized, query_embedding=emb, top_k=5,
                                      scope=scope_for_customer_intent(intent))
        sims = [float(h.vector_similarity) for h in hits if h.vector_similarity is not None]
        best = max(sims) if sims else None
        rag = await svc.answer(query=query, intent=intent)
        flag = "OK " if hits else "**空**"
        if not hits:
            ok = False
        print(f"  [{flag}] {intent:<18}{query:<22}命中={len(hits)} best="
              f"{best if best else 0:.4f} {rag.evidence_status}")

    # ---- C. 意图识别回归 ----
    print()
    print(_line("="))
    print("C. 意图识别：制度类命中 policy，流程/交易类不被抢占")
    print(_line("="))
    planner = CustomerIntentPlanner()
    checks = [
        ("什么是双录", "policy"),
        ("反洗钱相关规定有哪些", "policy"),
        ("监管规定有哪些", "policy"),
        ("开户有什么规定", "workflow"),
        ("开户需要什么材料", "workflow"),
        ("赎回有什么规定", "business_operation"),
    ]
    for query, want in checks:
        intents = [t.intent for t in planner.plan(query).tasks]
        good = want in intents
        if not good:
            ok = False
        print(f"  [{'OK ' if good else '**差**'}] {query:<22} want={want:<20} got={intents}")

    print()
    print("  依赖声明（分层执行的前提）：")
    for query in ("我有没有风险限制", "我的订单 ORD-123456 的处理流程是什么"):
        plan = planner.plan(query)
        print(f"    {query:<34} -> "
              f"{[(t.task_id, t.intent, t.depends_on) for t in plan.tasks]}")

    # ---- D. 确定性路径 ----
    print()
    print(_line("="))
    print("D. 确定性事实路径未受影响")
    print(_line("="))
    demo = CUSTOMERS["risk_c4_demo"]
    for query, want_route in FACT_CASES:
        async with db.session_factory() as s:
            try:
                res = await CustomerQueryRouter().route(
                    session=s, customer_id=demo, query=query, entities=None)
                route = getattr(res, "route", None)
                flag = "OK " if route == want_route else "**差**"
                if route != want_route:
                    ok = False
                print(f"  [{flag}] {query:<24} route={route} (期望 {want_route})")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"  [ERR] {query:<24} {type(exc).__name__}: {str(exc)[:70]}")

    # ---- E. 等级读取 ----
    print()
    print(_line("="))
    print("E. 风险等级读取（各等级客户）")
    print(_line("="))
    for name, uid in CUSTOMERS.items():
        expect = name.split("_")[1].upper()
        async with db.session_factory() as s:
            res = await CustomerQueryRouter().route(
                session=s, customer_id=uid, query="我有没有风险限制", entities=None)
        got = (res.data or {}).get("risk_level")
        flag = "OK " if got == expect else "**差**"
        if got != expect:
            ok = False
        print(f"  [{flag}] {name:<16} 期望={expect} 实得={got}")

    # ---- F. 等级比对口径 ----
    print()
    print(_line("="))
    print("F. 等级比对口径（收敛后的单一实现）")
    print(_line("="))
    cases = [("C4", "C3", True), ("C3", "C4", False), ("C5", "C5", True),
             ("C1", "C5", False), (None, "C1", False)]
    for cust, prod, expect in cases:
        got = is_suitable(cust, prod)
        flag = "OK " if got == expect else "**差**"
        if got != expect:
            ok = False
        print(f"  [{flag}] 客户{str(cust):<5} vs 产品{prod:<4} -> {got} (期望 {expect})")
    print(f"  缺失等级 risk_rank(None)={risk_rank(None)}（应为 0，区分'缺失'与'最低等级'）")

    print()
    print(_line("="))
    print(f"回归结果：{'全部通过' if ok else '**存在失败项，见上方 ** 标记**'}")
    print(_line("="))

    await provider.close()
    await db.dispose()
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
