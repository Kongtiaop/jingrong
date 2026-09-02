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

# intent_misroute（高置信误判 / 规则与影子分歧）专用阈值与模板"承诺级"。
RULE_MISROUTE_MIN_CONF = 0.8   # 规则必须够自信才算"confident misroute"
LLM_MISROUTE_MIN_CONF = 0.55   # 影子 LLM 也要够自信，避免把 LLM 乱猜当分歧
# 模板按"业务承诺/动作强度"分级：交易草稿最高，推荐/转人工其次，只读最低。
_TRADE = "trade"; _COMMIT = "commit"; _READ = "read"
_TEMPLATE_COMMIT = {
    "T3_RECOMMEND_THEN_PURCHASE_DRAFT": _TRADE, "T4_PURCHASE_DRAFT": _TRADE,
    "T9_MODIFY_DRAFT": _TRADE, "T10_CANCEL_DRAFT": _TRADE,
    "T2_PRODUCT_RECOMMENDATION": _COMMIT, "T7_HUMAN_HANDOFF": _COMMIT,
    "T1_INFORMATION_QUERY": _READ, "T5_ORDER_STATUS": _READ, "T6_RISK_EXPLANATION": _READ,
    "T8_MULTI_INTENT": _COMMIT, "T0_UNKNOWN": _READ,
}
# 线上 misroute 采集开关：与意图 unknown/低置信回流共用 BAD_CASE_COLLECT=1（默认关）。
_DEBUG_MISROUTE_COLLECT = os.environ.get("BAD_CASE_COLLECT") == "1"


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
    rule_template: str = ""
    llm_template: str = ""

    def to_json(self) -> dict:
        return {
            "query": self.query, "reasons": self.reasons, "intent": self.intent,
            "confidence": self.confidence, "evidence_status": self.evidence_status,
            "handoff": self.handoff, "clarifications": self.clarifications,
            "ts": self.ts, "query_hash": self.query_hash,
            "rule_template": self.rule_template, "llm_template": self.llm_template,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_query(query: str) -> str:
    return "".join((query or "").split()).lower()


def query_hash(query: str) -> str:
    return hashlib.md5(normalize_query(query).encode("utf-8")).hexdigest()


def misroute_collecting_enabled() -> bool:
    """线上 misroute 采集是否开启（BAD_CASE_COLLECT=1）。默认关，异常安全。"""
    return _DEBUG_MISROUTE_COLLECT


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


def _commit_level(template: str) -> str:
    return _TEMPLATE_COMMIT.get(template, _READ)


def detect_intent_misroute(query: str, *, rule_intent=None, llm_intent=None) -> BadCaseRecord | None:
    """检测"高置信误判"（confident misroute）：规则自信地选了一个与影子 LLM 明显分歧的意图。

    现有 detect_bad_case 只在 unknown/低置信/规则兜底等"弱势"信号上触发，抓不到
    "规则很自信但方向错了"这一类（例如把"我能购买什么产品"这类资格/推荐问句
    误路由进交易草稿）。这里用规则的确定性结果 与 影子 LLM 的高置信分歧来暴露它。

    触发条件（全中才算候选，供人工标注裁决，非自动执行）：
    - 规则模板非 unknown 且 confidence >= RULE_MISROUTE_MIN_CONF（规则确实自信）
    - 影子模板非 unknown 且 confidence >= LLM_MISROUTE_MIN_CONF（分歧可信，非乱猜）
    - 双方模板不同，且承诺级不同（read/commit/trade 跨级）→ 规则把意图路由错了"档位"
    """
    if rule_intent is None or llm_intent is None:
        return None
    rule_template = str(getattr(rule_intent, "template", "") or "")
    llm_template = str(getattr(llm_intent, "template", "") or "")
    rule_conf = getattr(rule_intent, "confidence", None)
    llm_conf = getattr(llm_intent, "confidence", None)
    if not rule_template or rule_template == "T0_UNKNOWN" or not llm_template or llm_template == "T0_UNKNOWN":
        return None
    if not isinstance(rule_conf, (int, float)) or not isinstance(llm_conf, (int, float)):
        return None
    if rule_conf < RULE_MISROUTE_MIN_CONF or llm_conf < LLM_MISROUTE_MIN_CONF:
        return None
    if rule_template == llm_template:
        return None
    if _commit_level(rule_template) == _commit_level(llm_template):
        return None  # 只读内的小分歧（如 order vs faq）无害，不当作 misroute
    return BadCaseRecord(
        query=query,
        reasons=["intent_misroute"],
        intent=str(getattr(rule_intent, "primary_intent", "") or ""),
        confidence=rule_conf,
        evidence_status="",
        handoff=False,
        clarifications=0,
        ts=_now(),
        query_hash=query_hash(query),
        rule_template=rule_template,
        llm_template=llm_template,
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

    def collect_misroute(self, query: str, *, rule_intent=None, llm_intent=None, force: bool = False) -> bool:
        rec = detect_intent_misroute(query, rule_intent=rule_intent, llm_intent=llm_intent)
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
