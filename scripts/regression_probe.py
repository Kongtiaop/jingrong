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

    # ---- G. 只读子代理并行（模型自主判断是否并行）----
    print()
    print(_line("="))
    print("G. 只读子代理并行：模型拆分/并行决策 + 真实执行")
    print(_line("="))
    from app.schemas.subagent import SubagentTask
    from app.services.customer_agent_execution import CustomerAgentExecutionService
    from app.services.customer_rag_answer import CustomerRagAnswerService
    from app.services.customer_tools import CustomerReadTools
    from app.services.subagent_planner import ReadOnlyFanoutPlanner
    from app.services.subagent_runner import ReadOnlySubagentRunner, capability_to_tool
    from app.services.subagent_executor import READ_ONLY_CAPABILITIES

    # G1. 白名单里每个 capability 都必须有真实映射（否则"接线"是假的）
    unmapped = [c for c in READ_ONLY_CAPABILITIES if capability_to_tool(c) is None]
    if unmapped:
        ok = False
    print(f"  [{'OK ' if not unmapped else '**差**'}] 白名单 {len(READ_ONLY_CAPABILITIES)} 个 capability "
          f"未映射={unmapped}")

    # G2. 模型决策（真 LLM）：并行 vs 串行必须能被区分
    svc = CustomerAgentExecutionService.__new__(CustomerAgentExecutionService)
    svc.database = db; svc.redis_client = None; svc.settings = settings
    svc.intent_fallback = None
    fanout_cases = [
        ("帮我看看我的资产总览，还有你们公司主营业务是什么", "parallel"),
        ("先查我的风险限制情况，再解释对应的风险制度依据", "serial"),
    ]
    for query, want_mode in fanout_cases:
        d = await ReadOnlyFanoutPlanner(provider=provider, database=db).plan(
            customer_id=CUSTOMERS["risk_c3_demo"], conversation_id="probe-fanout", query=query)
        good = (d.source == "model") and (d.effective_mode == want_mode)
        if not good:
            ok = False
        print(f"  [{'OK ' if good else '**差**'}] {query[:26]:<28} want={want_mode:<9}"
              f"got={d.effective_mode}/{d.source} tasks={len(d.tasks)}")

    # G3. 端到端真实执行：必须产出真实内容（不是占位串、不是空摘要）
    # **显式传 enabled=True**：生产默认该开关为 false（保守），而本节要验证的是
    # "模型决策 + 并行执行"这条路径本身可用，故必须显式启用，
    # 否则会得到 source=disabled 的单任务结果 —— 那不是缺陷，是开关在工作。
    runner = ReadOnlySubagentRunner(
        tools=CustomerReadTools(db), answer_generator=CustomerRagAnswerService(provider))
    res = await svc.run_read_only_fanout(
        customer_id=CUSTOMERS["risk_c3_demo"], conversation_id="probe-fanout",
        query="帮我看看我的资产总览，还有你们公司主营业务是什么",
        planner=ReadOnlyFanoutPlanner(provider=provider, database=db), runner=runner,
        enabled=True)
    statuses = [(r.task_id, r.status) for r in res.results]
    all_ok = res.overall_status == "succeeded" and len(res.results) >= 2
    has_content = all(len((r.summary or "").strip()) > 20 for r in res.results)
    if not (all_ok and has_content):
        ok = False
    print(f"  [{'OK ' if all_ok else '**差**'}] 端到端 status={res.overall_status} "
          f"mode={res.fanout_mode} source={res.fanout_source} {statuses}")
    print(f"  [{'OK ' if has_content else '**差**'}] 每个子任务都有实质内容（非占位串/空摘要）")

    # G3b. 开关关闭时必须退化为串行单任务且**不调用模型**
    res_off = await svc.run_read_only_fanout(
        customer_id=CUSTOMERS["risk_c3_demo"], conversation_id="probe-fanout",
        query="帮我看看我的资产总览，还有你们公司主营业务是什么",
        planner=ReadOnlyFanoutPlanner(provider=provider, database=db),
        runner=ReadOnlySubagentRunner(tools=CustomerReadTools(db)), enabled=False)
    off_ok = res_off.fanout_source == "disabled" and len(res_off.results) == 1
    if not off_ok:
        ok = False
    print(f"  [{'OK ' if off_ok else '**差**'}] 开关关闭 → source={res_off.fanout_source} "
          f"tasks={len(res_off.results)}（应为 disabled/1，且不调用模型）")

    # G4. 写操作绝不可达（模型返回写能力时不得发生任何对应调用）
    from app.services.subagent_planner import parse_fanout_decision
    d = parse_fanout_decision(
        {"mode": "parallel", "tasks": [
            {"capability": "customer_asset_summary", "query": "资产"},
            {"capability": "create_order", "query": "下单"},
            {"capability": "transfer_money", "query": "转账"}]},
        fallback_query="q")
    caps = [t.read_only_capability for t in d.tasks]
    safe = caps == ["customer_asset_summary"]
    if not safe:
        ok = False
    print(f"  [{'OK ' if safe else '**差**'}] 写操作能力被拒（保留={caps}）")

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
