# -*- coding: utf-8 -*-
"""线上 bad case 检测与积累（数据飞轮 · 从手工 bad case 迈向自动回流）。

在真实用户问答处理完后调用，根据运行时信号判断"这次问答是不是 bad case"，
若是则落入积累池 `logs/bad_cases.jsonl`（按归一化 query 去重），供后续人工标注
升级进评测集 → 再触发 auto_repair 修复。检测是纯函数、可离线单测。

信号来源（任一命中即判 bad case）：
- 意图层：primary_intent==unknown、confidence<0.5、source==rules_fallback_needed（LLM 解析失败走规则）
- RAG 层：evidence_status in {low_confidence, embedding_degraded, text_fallback}、handoff_required
- 对话层：澄清次数 >= 2（反复没问清）
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
DEFAULT_STORE = os.path.join(_LOG_DIR, "bad_cases.jsonl")

LOW_CONFIDENCE = 0.5
CLARIFY_STUCK = 2
BAD_EVIDENCE = {"low_confidence", "embedding_degraded", "text_fallback"}


@dataclass
class BadCaseRecord:
    query: str
    reasons: list[str] = field(default_factory=list)
    intent: str = ""
    confidence: float | None = None
    evidence_status: str = ""
    handoff: bool = False
    clarifications: int = 0
    ts: str = ""
    query_hash: str = ""

    def to_json(self) -> dict:
        return {
            "query": self.query, "reasons": self.reasons, "intent": self.intent,
            "confidence": self.confidence, "evidence_status": self.evidence_status,
            "handoff": self.handoff, "clarifications": self.clarifications,
            "ts": self.ts, "query_hash": self.query_hash,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_query(query: str) -> str:
    return "".join((query or "").split()).lower()


def query_hash(query: str) -> str:
    return hashlib.md5(normalize_query(query).encode("utf-8")).hexdigest()


def detect_bad_case(query: str, *, intent=None, rag=None, clarifications: int = 0) -> BadCaseRecord | None:
    """根据运行时结果判定是否 bad case；是则返回记录，否则 None。"""
    reasons: list[str] = []
    intent_code = ""
    confidence = None
    if intent is not None:
        intent_code = str(getattr(intent, "primary_intent", "") or "")
        confidence = getattr(intent, "confidence", None)
        if intent_code == "unknown":
            reasons.append("intent_unknown")
        if isinstance(confidence, (int, float)) and confidence < LOW_CONFIDENCE:
            reasons.append("intent_low_confidence")
        if getattr(intent, "source", None) == "rules_fallback_needed":
            reasons.append("intent_rules_fallback")

    evidence_status = ""
    handoff = False
    if rag is not None:
        evidence_status = str(getattr(rag, "evidence_status", "") or "")
        handoff = bool(getattr(rag, "handoff_required", False))
        if evidence_status in BAD_EVIDENCE:
            reasons.append(f"rag_{evidence_status}")
        if handoff:
            reasons.append("rag_handoff")

    if clarifications >= CLARIFY_STUCK:
        reasons.append("clarify_stuck")

    if not reasons:
        return None
    return BadCaseRecord(
        query=query, reasons=reasons, intent=intent_code, confidence=confidence,
        evidence_status=evidence_status, handoff=handoff, clarifications=clarifications,
        ts=_now(), query_hash=query_hash(query),
    )


class BadCaseStore:
    """JSONL 追加存储 + 按 query_hash 去重（加载现有 hash 集合）。"""

    def __init__(self, path: str = DEFAULT_STORE):
        self.path = path
        self._seen: set[str] = set()
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                            if obj.get("query_hash"):
                                self._seen.add(obj["query_hash"])
                        except Exception:  # noqa: BLE001
                            continue
            except Exception:  # noqa: BLE001
                pass

    def record(self, rec: BadCaseRecord, *, force: bool = False) -> bool:
        """去重后追加；返回是否真正写入。force=True 跳过去重。"""
        if not force and rec.query_hash in self._seen:
            return False
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_json(), ensure_ascii=False) + "\n")
        self._seen.add(rec.query_hash)
        return True

    def collect(self, query: str, *, intent=None, rag=None, clarifications: int = 0,
                force: bool = False) -> bool:
        rec = detect_bad_case(query, intent=intent, rag=rag, clarifications=clarifications)
        if rec is None:
            return False
        return self.record(rec, force=force)

    def load(self) -> list[dict]:
        out = []
        if not os.path.exists(self.path):
            return out
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
        return out
