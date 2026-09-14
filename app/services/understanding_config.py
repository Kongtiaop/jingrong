#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""可配置理解层：加载 config/understanding.yaml，提供模型路由与检索参数，支持灰度放量。

模型可自调理解层配置（提示词/few-shot/top_k/模型路由）；执行层闸门不在配置域。
文件缺失或字段缺失时回退到代码内置默认值，保证系统不依赖配置文件也能跑。

**灰度发布**：`config/gray.json` 控制"放量到历史某版本 + 百分比"。运行时按查询哈希
把 query 分流到 live 或 gray 版本（同一 query 恒走同一版本，保证确定性）。
影响面：`get_intent_prompt(query)` / `model_for_query(query,...)`；检索参数与关键词
映射（无 query 上下文）保持 live。`config_version gray` 管理灰度（start/status/promote/abort）。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import yaml

try:
    from yaml import safe_load as _load
except ImportError:  # pragma: no cover
    _load = yaml.load

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "config")
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "understanding.yaml")
_GRAY_JSON = os.path.join(_CONFIG_DIR, "gray.json")

# 难度判定信号：命中即加分
_NEGATION_MARKERS = ["不要", "别", "不买", "不赎", "不看", "先别", "不需要"]
_CONJUNCTION_MARKERS = ["并且", "同时", "然后", "顺便", "和", "再", "并", "以及"]
_AMBIGUOUS_MARKERS = ["怎么办", "咋办", "咋样", "怎么样", "是什么", "啥", "那个", "这个"]
_ADVERSARIAL_MARKERS = ["忽略系统规则", "绕过", "越权", "别人的", "另一个客户", "其他客户", "改成", "修改余额", "删除", "注销", "直接下单", "直接成交"]


def _load_yaml() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = _load(f)
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 配置缺失不阻塞
        return {}


def _load_gray_file() -> dict:
    """读 config/gray.json 指向的灰度配置内容；无效则返回空。"""
    try:
        if not os.path.exists(_GRAY_JSON):
            return {}
        with open(_GRAY_JSON, encoding="utf-8") as f:
            g = json.load(f)
        if g.get("active") and g.get("file") and os.path.exists(g["file"]):
            with open(g["file"], encoding="utf-8") as f:
                data = _load(f)
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001 - 灰度配置缺失不阻塞
        pass
    return {}


class UnderstandingConfig:
    """运行时加载理解层配置；模型自调即改 config/understanding.yaml 后 reload。"""

    def __init__(self) -> None:
        self._data: dict = {}
        self._gray_active: bool = False
        self._gray_percent: float = 0.0
        self._gray_version: int | None = None
        self._gray_data: dict = {}

    def reload(self) -> None:
        self._data = _load_yaml()
        self._load_gray()

    def _load_gray(self) -> None:
        self._gray_active = False
        self._gray_percent = 0.0
        self._gray_version = None
        self._gray_data = {}
        try:
            if not os.path.exists(_GRAY_JSON):
                return
            with open(_GRAY_JSON, encoding="utf-8") as f:
                g = json.load(f)
            if not g.get("active"):
                return
            self._gray_percent = max(0.0, min(100.0, float(g.get("percent", 0) or 0)))
            self._gray_version = g.get("version")
            self._gray_data = _load_gray_file()
            self._gray_active = bool(self._gray_data)
        except Exception:  # noqa: BLE001 - 灰度配置异常不阻塞
            self._gray_active = False
            self._gray_data = {}

    def gray_info(self) -> dict:
        return {"active": self._gray_active, "percent": self._gray_percent,
                "version": self._gray_version}

    def _use_gray(self, query: str) -> bool:
        if not self._gray_active or not self._gray_data:
            return False
        if not query:  # 无 query 上下文 → live
            return False
        if self._gray_percent >= 100:
            return True
        if self._gray_percent <= 0:
            return False
        h = int(hashlib.md5((query or "").encode("utf-8")).hexdigest(), 16) % 100
        return h < self._gray_percent

    def _src(self, query: str) -> dict:
        return self._gray_data if self._use_gray(query) else self._data

    # ---- 难度估计 ----
    def estimate_difficulty(self, query: str, *, rule_confidence: float | None = None,
                            intent_count: int | None = None) -> str:
        q = query or ""
        # 对抗/越权/非法操作 → 一律最复杂，走大模型 + 执行层门禁
        if any(m in q for m in _ADVERSARIAL_MARKERS):
            return "L"
        weights = self._src(query).get("difficulty_weights", {})
        score = 0.0
        if rule_confidence is not None:
            score += (1.0 - min(rule_confidence, 1.0)) * weights.get("rule_confidence", 0.4)
        if intent_count is not None and intent_count > 1:
            score += weights.get("intent_count", 0.2)
        if any(m in q for m in _NEGATION_MARKERS):
            score += weights.get("negation", 0.15)
        if any(m in q for m in _CONJUNCTION_MARKERS):
            score += weights.get("conjunction", 0.15)
        if any(m in q for m in _AMBIGUOUS_MARKERS):
            score += weights.get("ambiguous", 0.1)
        if score >= 0.6:
            return "L"
        if score >= 0.3:
            return "M"
        return "S"

    # ---- 模型路由 ----
    _DIFF_RANK = {"S": 1, "M": 2, "L": 3}

    def resolve_model(self, difficulty: str) -> str:
        rank = self._DIFF_RANK.get(difficulty, 2)
        tiers = self._data.get("model_routing", {}).get("tiers", [])
        for tier in tiers:
            if rank <= self._DIFF_RANK.get(tier.get("max", ""), 2):
                return tier.get("model", "")
        return self._data.get("model_routing", {}).get("default", "qwen-plus")

    def model_for_query(self, query: str, *, rule_confidence: float | None = None,
                        intent_count: int | None = None) -> str:
        src = self._src(query)
        difficulty = self.estimate_difficulty(query, rule_confidence=rule_confidence, intent_count=intent_count)
        rank = self._DIFF_RANK.get(difficulty, 2)
        tiers = src.get("model_routing", {}).get("tiers", [])
        for tier in tiers:
            if rank <= self._DIFF_RANK.get(tier.get("max", ""), 2):
                return tier.get("model", "")
        return src.get("model_routing", {}).get("default", "qwen-plus")

    # ---- 检索参数（无 query 上下文，保持 live） ----
    def get_top_k(self) -> int:
        return int(self._data.get("retrieval", {}).get("top_k", 5))

    # 枚举/多实体 query 信号：答案需覆盖多个并列项 → 主动给更大 top_k，避免 RRF 在
    # top_k 太紧时把"某一路单独命中的 gold"挤出（如"有哪些牌照/哪几个业务线/分别是什么"）。
    _ENUM_MARKERS = (
        "哪些", "哪几个", "哪些部门", "分别", "有哪些", "哪几类", "哪些业务", "哪些产品",
        "牌照", "业务线", "事业部", "分类", "包括什么", "包含哪些", "分别是什么", "各是什么",
        "列举", "有几个", "几类", "列表",
    )

    def get_top_k_for_query(self, query: str, *, rule_confidence: float | None = None,
                            intent_count: int | None = None) -> int:
        """返回 query 的动态 top_k（规则驱动，改 yaml 即调参）。

        优先级：
          1) 枚举/多实体 query（需覆盖多个并列答案）→ top_k_enum（默认 10）。
          2) 否则按难度：简单(S)/中等(M)/复杂(L) 取 retrieval.top_k_by_difficulty。
          3) 缺省回退到 get_top_k()。
        目的是在"答案分布多处、需要多召回"时降低 RRF top_k 截断把 gold 挤出的概率。
        """
        retrieval = self._data.get("retrieval", {}) or {}
        q = query or ""
        try:
            if any(m in q for m in self._ENUM_MARKERS):
                enum_val = int(retrieval.get("top_k_enum", 0))
                if enum_val > 0:
                    return enum_val
        except Exception:  # noqa: BLE001 - 动态 top_k 失败不阻塞
            pass
        by_diff = retrieval.get("top_k_by_difficulty") or {}
        try:
            difficulty = self.estimate_difficulty(query, rule_confidence=rule_confidence, intent_count=intent_count)
            val = int(by_diff.get(difficulty, 0))
            if val > 0:
                return val
        except Exception:  # noqa: BLE001 - 动态 top_k 失败不阻塞，回退默认
            pass
        return self.get_top_k()

    def get_top_k_max(self) -> int:
        """C 兜底扩召回的硬上限（防过度膨胀）。"""
        return int(self._data.get("retrieval", {}).get("top_k_max", 15))

    def get_top_k_bump(self) -> int:
        """证据不足时单次扩召回的增量（C 兜底用）。"""
        return int(self._data.get("retrieval", {}).get("top_k_bump", 3))

    def get_rrf_k(self) -> int:
        return int(self._data.get("retrieval", {}).get("rrf_k", 60))

    def get_min_score(self) -> float:
        return float(self._data.get("retrieval", {}).get("min_score", 0.0001))

    def get_min_similarity(self) -> float:
        """无 LLM-judge 时的兜底拒答/转人工相似度阈值（ambiguous 带）。"""
        return float(self._data.get("retrieval", {}).get("min_similarity", 0.55))

    def get_rejection_thresholds(self) -> dict:
        """分层决策阈值：reject(低于此直接拒)/answer(高于此直接答)/judge_ambiguous(中间带是否用 LLM-judge)。

        judge_reject/judge_answer：**judge 介入的子区间**（默认沿用完整 ambiguous 带，向后兼容）。
        设置该子区间的理由（ISSUE-20260914-JUDGE-MISJUDGES-RELEVANT-EVIDENCE）：
        judge 是"LLM 裁决"，本身有误判风险——实测它把明知相关的制度引用判成 no。
        把 judge 限定在**低分区**（如 0.35~0.55），高分区（0.55~0.65）直接放行，
        既保留"拦截明显无关引用"的能力，又避免误杀边缘相关引用。
        """
        r = self._data.get("retrieval", {}) or {}
        reject = float(r.get("reject_similarity", 0.35))
        answer = float(r.get("answer_similarity", 0.65))
        return {
            "reject": reject,
            "answer": answer,
            "judge_ambiguous": bool(r.get("judge_ambiguous", True)),
            "judge_reject": float(r.get("judge_reject_similarity", reject)),
            "judge_answer": float(r.get("judge_answer_similarity", answer)),
        }

    def get_chunking(self) -> dict:
        """切片参数（结构感知切块；改配置即改切片，不用改代码）。"""
        c = self._data.get("chunking", {}) or {}
        return {
            "mode": c.get("mode", "structure"),
            "size": int(c.get("size", 400)),
            "overlap": int(c.get("overlap", 40)),
        }

    # ---- 意图提示词 / 关键词映射（灰度按 query 分流） ----
    def get_intent_prompt(self, query: str = "") -> dict:
        """理解层可配置的意图提示词/few-shot；空则用代码内置。gray 时按 query 分流。"""
        p = self._src(query).get("intent_prompt", {}) or {}
        return {
            "system_template": p.get("system_template", "") or "",
            "few_shot": p.get("few_shot", []) or [],
        }

    def get_intent_mappings(self, query: str = "") -> dict:
        """理解层可配置的额外意图关键词映射 {intent: [keywords]}；合并进代码内置。"""
        return self._src(query).get("intent_mappings", {}) or {}


# 模块级单例（可 reload）
understanding_config = UnderstandingConfig()
