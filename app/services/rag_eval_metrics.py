# -*- coding: utf-8 -*-
"""RAG 答案质量评测的确定性指标（纯函数、可离线单测）。

金融 RAG 的硬门控：拒答正确率、合规（无禁词）、引用存在。
确定性检查是核心——它不依赖 LLM judge，安全边界必须用规则兜底。
"""
from __future__ import annotations


def evaluate_case(case: dict, *, handoff_required: bool, answer: str, citations_count: int) -> dict:
    """对单条 RAG 答案做确定性检查（纯函数）。"""
    expected_reject = bool(case.get("requires_rejection"))
    checks = {
        "correct_rejection": handoff_required == expected_reject,
        "compliance": True,
        "has_citations": True if handoff_required else bool(citations_count),
    }
    if answer:
        checks["compliance"] = not any(t in answer for t in (case.get("forbidden_terms") or []))
    return checks


def decide_evidence(best_sim: float | None, *, reject: float, answer: float,
                    judge_result: bool | None = None, fallback: float | None = None) -> str:
    """分层证据决策（纯函数）：'reject' | 'answer'。

    - best_sim < reject      → 明显无关，直接拒（便宜）。
    - best_sim >= answer     → 明显相关，直接答（便宜）。
    - 中间带（ambiguous）：judge_result 给出则用；否则用 fallback 阈值兜底。
    """
    if best_sim is None:
        # 无向量相似度（纯文本降级 / 测试 fake）：没有相似度信号，不按相似度拒，
        # 交给 min_score（是否检索到）判断。
        return "answer"
    if best_sim < reject:
        return "reject"
    if best_sim >= answer:
        return "answer"
    if judge_result is not None:
        return "answer" if judge_result else "reject"
    return "answer" if best_sim >= (fallback if fallback is not None else reject) else "reject"
