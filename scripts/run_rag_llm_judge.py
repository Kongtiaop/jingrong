#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAG 端到端答案质量评测（确定性 + 自建 LLM-judge）。

分层：
- **确定性检查（金融硬门控，纯规则）**：拒答正确率、合规（无禁词）、引用存在。
- **LLM-judge（自建，用 QwenProvider）**：给定【问题+引用+答案+金标准】，让 Qwen 按细则
  给忠实度 / 相关性 / 正确性打分（0-5），归一化为 pass/fail。

相比 RAGAS：零额外依赖、贴合本项目栈；但 LLM-judge 与 RAGAS 一样仍需人工 gold 校准（kappa）。

用法（需 DB 已跑 + DASHSCOPE key）：
  python -m scripts.run_rag_llm_judge
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.repositories.knowledge import KnowledgeRepository
from app.services.customer_rag_answer import CustomerRagAnswerService, build_answerability_judge
from app.services.customer_rag_service import CustomerRagService
from app.services.knowledge_retrieval import WealthKnowledgeRetriever
from app.services.rag_eval_metrics import evaluate_case

CASES = os.path.join(os.path.dirname(__file__), "rag_answer_eval_cases.json")

JUDGE_SYSTEM = (
    "你是 RAG 答案质量评审。给定【问题、检索引用、系统答案、金标准答案】，按 0-5 打分：\n"
    "faithfulness 忠实度：答案每个主张是否都在引用里，有无编造引用外的内容。\n"
    "relevancy 相关性：答案是否紧扣问题、不答非所问。\n"
    "correctness 正确性：对照金标准，关键事实是否答对。\n"
    "只输出 JSON：{\"faithfulness\":0-5,\"relevancy\":0-5,\"correctness\":0-5}"
)

# LLM-judge 通过阈值（>= 视为 pass）
PASS_THRESHOLD = 3


def judge_prompt(question: str, contexts: list[str], answer: str, gold: str) -> str:
    ctx = "\n".join(f"[引用 {i+1}] {c}" for i, c in enumerate(contexts)) or "（无引用）"
    return (
        f"问题：{question}\n\n引用：\n{ctx}\n\n"
        f"系统答案：{answer}\n\n金标准答案：{gold or '（无）'}"
    )


def score_judge_result(text: str) -> dict[str, bool]:
    """解析 LLM 返回的 JSON，归一化为 pass/fail（>=PASS_THRESHOLD 通过）。"""
    try:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
        return {
            k: int(data.get(k, 0)) >= PASS_THRESHOLD
            for k in ("faithfulness", "relevancy", "correctness")
        }
    except Exception:  # noqa: BLE001 - judge 返回异常按不过处理
        return {"faithfulness": False, "relevancy": False, "correctness": False}


async def _judge(provider: QwenProvider, question: str, contexts: list[str], answer: str, gold: str) -> dict[str, bool]:
    resp = await provider._client.chat.completions.create(
        model=provider.settings.qwen_max_model,  # judge 用更强模型（qwen-max），judge ≠ 生成模型
        temperature=0.0,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": judge_prompt(question, contexts, answer, gold)},
        ],
    )
    return score_judge_result(resp.choices[0].message.content or "")


async def run(out: str | None) -> None:
    settings = get_settings()
    if not settings.dashscope_api_key:
        print("无 DASHSCOPE_API_KEY。")
        return
    cases = json.load(open(CASES, encoding="utf-8"))["cases"]
    db = Database(settings)
    provider = QwenProvider(settings)
    try:
        repo = KnowledgeRepository(db)
        retriever = WealthKnowledgeRetriever(repo)
        service = CustomerRagService(retriever, embedding_provider=provider,
                                     ambiguous_judge=build_answerability_judge(provider))
        answerer = CustomerRagAnswerService(provider)
        results = []
        for case in cases:
            result = await service.answer(query=case["query"], intent=case["intent"])
            answer = ""
            if not result.handoff_required:
                answer = (await answerer.generate(query=case["query"], result=result)).answer
            det = evaluate_case(case, handoff_required=result.handoff_required,
                                answer=answer, citations_count=len(result.citations))
            judge = None
            if not result.handoff_required:
                judge = await _judge(provider, case["query"], [c.content for c in result.citations], answer, case.get("gold_answer", ""))
            results.append({
                "id": case["id"], "query": case["query"], "handoff": result.handoff_required,
                "answer": answer, "deterministic": det, "judge": judge,
            })
    finally:
        await provider.close()
        await db.dispose()

    n = len(results) or 1
    print("== 确定性检查 ==")
    for metric in ("correct_rejection", "compliance", "has_citations"):
        ok = sum(1 for r in results if r["deterministic"][metric])
        print(f"  [{metric}] {ok}/{len(results)} = {ok/n:.1%}")
    judged = [r for r in results if r["judge"]]
    if judged:
        print("== LLM-judge（faithfulness/relevancy/correctness，>=%d pass）==" % PASS_THRESHOLD)
        for metric in ("faithfulness", "relevancy", "correctness"):
            ok = sum(1 for r in judged if r["judge"].get(metric))
            print(f"  [{metric}] {ok}/{len(judged)} = {ok/len(judged):.1%}")
    print("\n逐条：")
    for r in results:
        det = r["deterministic"]
        flag = "✓" if (det["correct_rejection"] and det["compliance"] and det["has_citations"]) else "✗"
        j = r["judge"]
        js = "" if not j else f" judge={ {k: ('P' if v else 'F') for k, v in j.items()} }"
        print(f"  [{flag}] {r['id']} handoff={r['handoff']} 合规={det['compliance']} 引用={det['has_citations']}{js} | {r['query']}")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n已保存: {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    asyncio.run(run(a.out))


if __name__ == "__main__":
    main()
