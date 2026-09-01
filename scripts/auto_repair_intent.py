#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据飞轮 Phase 2：半自动修复 + 候选自动验证（闭环核心）。

读取评测失败案例 → LLM 生成结构化修复候选（few_shot / 意图关键词映射）。
新增 **候选自动验证**：把候选注入理解层配置，在**调优集（train+val，不碰 test）**上
跑"基准 vs 带候选"的准确率对比；只有**提升或非负回归**的批次才标记 validated，
`--apply` 只会落地 validated 候选（未验证/验证不过则不落地，仍走人工门控）。

用法：
  仅生成候选（人工审核）： python -m scripts.auto_repair_intent --results logs/intent_eval.json
  生成并自动验证：         python -m scripts.auto_repair_intent --results logs/intent_eval.json --validate
  验证通过后落地：         python -m scripts.auto_repair_intent --results logs/intent_eval.json --validate --apply
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os

from app.core.settings import get_settings
from app.infrastructure.qwen import QwenProvider
from scripts import config_versioning

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "understanding.yaml")
EVAL_CASES = os.path.join(os.path.dirname(__file__), "intent_eval_cases.json")
FEEDBACK_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "repair_feedback.jsonl")

VALID_INTENTS = {"order_status", "workorder_status", "asset_summary", "product_status", "product_recommendation",
                 "business_operation", "risk_public", "risk_explanation", "risk_restriction", "policy", "faq",
                 "human_handoff", "unknown", "company_info", "company_business", "workflow"}

SYSTEM = (
    "你是意图识别系统的调优助手。给定失败案例，输出**结构化**修复候选，供配置落地。"
    "只输出如下 JSON：{\"candidates\":[{\"change_type\":\"few_shot\",\"query\":\"用户话术\",\"output\":\"extract_intent 参数 JSON 字符串\",\"fix\":\"说明\"},"
    "{\"change_type\":\"rule_mapping\",\"intent\":\"意图码\",\"keywords\":[\"关键词\"],\"fix\":\"说明\"}]}。"
    "意图码用 order_status/workorder_status/asset_summary/product_status/product_recommendation/"
    "business_operation/risk_public/risk_explanation/risk_restriction/policy/faq/human_handoff/unknown/company_info/company_business/workflow。"
    "few_shot 的 output 必须是**合法 JSON 字符串**，且包含 primary_intent 字段（规范意图码），不允许发明 schema/字段名。"
    "不得建议改动执行层（权限/风控/确认/幂等）。"
)


def _fmt_failures(results: list[dict]) -> str:
    return "\n".join(
        f"- query: {r['query']} | 期望: {r['expected']} | 实际: {r['actual']}"
        for r in results
    )


def _load_cfg() -> dict:
    import yaml
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_cfg(cfg: dict) -> None:
    import yaml
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


# ---- 人工反馈闭环：记录 accepted/rejected 候选 + 原因，喂回生成器 ----
def _record_feedback(candidate: dict, verdict: str, reason: str) -> None:
    import json
    os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"candidate": candidate, "verdict": verdict, "reason": reason}, ensure_ascii=False) + "\n")


def _load_feedback(limit: int = 10) -> dict:
    """读最近 feedback：accepted（照此风格）与 rejected（不要重犯）。"""
    accepted, rejected = [], []
    if not os.path.exists(FEEDBACK_PATH):
        return {"accepted": accepted, "rejected": rejected}
    rows = []
    with open(FEEDBACK_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    for row in rows[-limit:]:
        if row.get("verdict") == "accepted":
            accepted.append(row["candidate"])
        elif row.get("verdict") == "rejected":
            rejected.append({"candidate": row["candidate"], "reason": row.get("reason", "")})
    return {"accepted": accepted, "rejected": rejected}


def _validate_candidate(c: dict) -> tuple[bool, str]:
    """候选 schema 校验：格式错的自动标 rejected（不再等人工发现）。"""
    t = c.get("change_type")
    if t == "few_shot":
        if not c.get("query") or not c.get("output"):
            return False, "few_shot 缺 query 或 output"
        try:
            payload = json.loads(c["output"])
        except (json.JSONDecodeError, TypeError):
            return False, "few_shot output 不是合法 JSON"
        if not isinstance(payload, dict) or "primary_intent" not in payload:
            return False, "few_shot output 必须是含 primary_intent 的对象"
        return True, ""
    if t == "rule_mapping":
        if c.get("intent") not in VALID_INTENTS:
            return False, f"rule_mapping intent 非法: {c.get('intent')}"
        if not isinstance(c.get("keywords"), list) or not c["keywords"]:
            return False, "rule_mapping keywords 需为非空列表"
        return True, ""
    return False, f"未知 change_type: {t}"


def _apply_into(cfg: dict, candidates: list[dict]) -> int:
    """把候选合并进 cfg（原地）；返回落地条数。"""
    applied = 0
    cfg.setdefault("intent_prompt", {}).setdefault("few_shot", [])
    cfg.setdefault("intent_mappings", {}).setdefault("keywords", {})
    for c in candidates:
        t = c.get("change_type")
        if t == "few_shot" and c.get("query") and c.get("output"):
            fs = cfg["intent_prompt"]["few_shot"]
            if not any(x.get("query") == c["query"] for x in fs):
                fs.append({"query": c["query"], "output": c["output"]})
                applied += 1
        elif t == "rule_mapping" and c.get("intent") and c.get("keywords"):
            kws = cfg["intent_mappings"]["keywords"].setdefault(c["intent"], [])
            for kw in c["keywords"]:
                if kw and kw not in kws:
                    kws.append(kw)
                    applied += 1
    return applied


def _apply_copy(cfg: dict, candidates: list[dict]) -> dict:
    """候选应用到配置的**深拷贝**（用于验证，不改原文件）。"""
    new = copy.deepcopy(cfg)
    _apply_into(new, candidates)
    return new


def _score(results: list[dict]) -> dict:
    n = len(results) or 1
    intent = sum(1 for r in results if r["intent_match"])
    clarify = sum(1 for r in results if r["clarify_match"])
    both = sum(1 for r in results if r["intent_match"] and r["clarify_match"])
    return {"full": both / n, "intent": intent / n, "clarify": clarify / n, "n": len(results)}


def _fmt_score(s: dict) -> str:
    return f"完全{s['full']:.1%} 意图{s['intent']:.1%} 澄清{s['clarify']:.1%} (n={s['n']})"


async def _run_eval_on(splits: set[str]) -> list[dict]:
    """在指定 split 上跑 LLM 评测（读当前 understanding_config 单例的 _data）。"""
    from scripts.run_intent_eval import load_cases, run_llm
    cases = load_cases(EVAL_CASES)
    tune = [c for c in cases if c.get("split", "train") in splits]
    return await run_llm(tune)


async def _validate(candidates: list[dict], splits: set[str]) -> dict:
    """基准 vs 带候选：在调优集上对比，返回两组分数与是否 validated。

    通过注入模块单例 `understanding_config._data`（独立脚本进程，不碰配置文件、
    无跨进程污染）；用完恢复，避免影响后续。
    """
    from app.services.understanding_config import understanding_config

    original = understanding_config._data
    base_cfg = _load_cfg() or {}
    try:
        understanding_config._data = base_cfg
        base_res = await _run_eval_on(splits)
        base_score = _score(base_res)

        cand_cfg = _apply_copy(base_cfg, candidates)
        understanding_config._data = cand_cfg
        cand_res = await _run_eval_on(splits)
        cand_score = _score(cand_res)
    finally:
        understanding_config._data = original

    # 判定：完全准确率非降 且 意图非降（防御少数用例方差）
    good = (cand_score["full"] >= base_score["full"] - 1e-9) and (cand_score["intent"] >= base_score["intent"] - 1e-9)
    return {"base": base_score, "cand": cand_score, "validated": good,
            "delta_full": cand_score["full"] - base_score["full"],
            "delta_intent": cand_score["intent"] - base_score["intent"],
            "delta_clarify": cand_score["clarify"] - base_score["clarify"]}


async def run(results_path: str, out: str, apply: bool, validate: bool, max_cases: int,
              validate_split: str) -> None:
    data = json.load(open(results_path, encoding="utf-8"))
    failures = [r for r in data["results"] if not (r["intent_match"] and r["clarify_match"])][:max_cases]
    if not failures:
        print("无失败案例，无需修复。")
        return
    provider = QwenProvider(get_settings())
    user = f"以下是失败案例：\n{_fmt_failures(failures)}\n请输出结构化修复候选。"
    # 人工反馈闭环：把最近 accepted/rejected 候选喂回生成器，让它学到格式与避坑
    fb = _load_feedback()
    if fb["accepted"]:
        user += "\n\n[已采纳的修复，照此风格生成]：" + json.dumps(fb["accepted"], ensure_ascii=False)
    if fb["rejected"]:
        user += "\n\n[被拒的修复，不要重犯]：" + json.dumps(fb["rejected"], ensure_ascii=False)
    resp = await provider._client.chat.completions.create(
        model=provider.settings.qwen_chat_model, temperature=0.2,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    )
    content = resp.choices[0].message.content or "{}"
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        print("模型输出非合法 JSON；未落地。")
        print(content[:1500])
        await provider.close()
        return
    candidates = payload.get("candidates", [])
    # 候选 schema 校验：格式错的自动标 rejected（不再等人工发现）
    valid = []
    for c in candidates:
        ok, reason = _validate_candidate(c)
        if ok:
            valid.append(c)
        else:
            _record_feedback(c, "rejected", f"schema 校验失败: {reason}")
            print(f"  [auto-reject] {c.get('change_type')} {c.get('query') or c.get('intent')}: {reason}")
    candidates = valid
    if not candidates:
        print("候选均未通过 schema 校验，未落地。")
        await provider.close()
        return

    validation = None
    if validate:
        splits = {x for x in validate_split.split(",") if x}
        print(f"[validate] 在调优集 {sorted(splits)} 上跑基准 vs 候选对比…（需 DASHSCOPE_API_KEY）")
        try:
            validation = await _validate(candidates, splits)
            v = validation
            print(f"[validate] 基准   {_fmt_score(v['base'])}")
            print(f"[validate] 带候选 {_fmt_score(v['cand'])}")
            print(f"[validate] Δ 完全{v['delta_full']:+.1%} 意图{v['delta_intent']:+.1%} 澄清{v['delta_clarify']:+.1%}")
            print(f"[validate] → {'通过（可落地）' if v['validated'] else '未通过（不建议落地，仍走人工门控）'}")
        except Exception as exc:  # noqa: BLE001 - 无 key/网络异常则跳过验证，退回人工门控
            print(f"[validate] 验证失败（{type(exc).__name__}: {exc}），退回人工门控。")

    can_apply = not validate or (validation is not None and validation["validated"])
    payload["validation"] = None if validation is None else {
        "base": validation["base"], "cand": validation["cand"], "validated": validation["validated"],
        "validate_split": validate_split,
    }

    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"failure_count": len(failures), "candidates": candidates,
                       "validation": payload["validation"]}, f, ensure_ascii=False, indent=2)
    print(f"生成 {len(candidates)} 条候选 → {out or '(stdout)'}")

    if apply:
        if can_apply:
            # 版本化：先快照"改动前"作回滚点，落地后再快照"改动后"
            pre = config_versioning.snapshot(
                label="auto_repair_pre_apply", changes=["apply 前状态（回滚点）"])
            cfg = _load_cfg()
            n = _apply_into(cfg, candidates)
            _save_cfg(cfg)
            post = config_versioning.snapshot(
                label="auto_repair_apply", changes=[f"落地 {n} 条候选"])
            print(f"已落地 {n} 条到 {CONFIG_PATH}（理解层配置，运行时生效）")
            print(f"版本化：改动前=v{pre}、改动后=v{post}；如需撤销：python -m scripts.config_version rollback {pre}")
            print("建议随后跑 test 回归：python -m scripts.check_intent_regression")
        else:
            print("未落地：候选未通过自动验证（未 --validate 或验证不过）。请人工审核后再决定。")
    else:
        print("未落地（未加 --apply）。审核后加 --apply 落地，或人工合并进 config/understanding.yaml。")
    # 人工反馈闭环：记录本次 valid 候选的判定（accepted/rejected + 原因）
    verdict = "accepted" if can_apply else "rejected"
    reason = "批量验证通过并落地" if can_apply else "批量验证未通过（可能某条拖累），需人工逐条筛选"
    for c in candidates:
        _record_feedback(c, verdict, reason)
    await provider.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", default="logs/intent_repair_candidates.json")
    ap.add_argument("--apply", action="store_true", help="落地到 config/understanding.yaml（仅 validated 候选）")
    ap.add_argument("--validate", action="store_true", help="自动在调优集验证候选，只落地提升/非降的批次")
    ap.add_argument("--validate-split", default="train,val", help="验证用 split，默认 train,val（test 不用于调优）")
    ap.add_argument("--max-cases", type=int, default=30)
    a = ap.parse_args()
    asyncio.run(run(a.results, a.out, a.apply, a.validate, a.max_cases, a.validate_split))


if __name__ == "__main__":
    main()
