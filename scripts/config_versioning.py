#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""理解层配置版本化与回滚（生产化门槛）。

`config/understanding.yaml` 是线上 live 配置；每次改动（auto_repair --apply 或手动
snapshot）都会在 `config/history/` 存一份**不可变**快照并登记进 `versions.json`。
rollback 会把指定版本恢复为 live，回滚前也会自动快照当前状态（可再回滚撤销）。

关键性质：
- 快照不可变：version 只增不减，内容不再改动。
- current 指向当前 live 对应的版本号。
- 回滚前先快照当前 → 回滚可逆。

用法见 `config_version.py`（list / rollback / snapshot / show）。
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_DIR = os.path.join(_DIR, "..", "config")
LIVE_PATH = os.path.join(_CONFIG_DIR, "understanding.yaml")
HISTORY_DIR = os.path.join(_CONFIG_DIR, "history")
MANIFEST_PATH = os.path.join(HISTORY_DIR, "versions.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_history() -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)


def _load_manifest() -> dict:
    _ensure_history()
    if not os.path.exists(MANIFEST_PATH):
        return {"current": 0, "versions": []}
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("versions"), list):
                return data
    except Exception:  # noqa: BLE001
        pass
    return {"current": 0, "versions": []}


def _save_manifest(manifest: dict) -> None:
    _ensure_history()
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def snapshot(label: str = "", changes: list[str] | None = None) -> int:
    """把当前 live 配置存为不可变快照，登记版本号并设为 current。"""
    _ensure_history()
    if not os.path.exists(LIVE_PATH):
        raise FileNotFoundError(f"live 配置不存在：{LIVE_PATH}")
    m = _load_manifest()
    version = max((v["version"] for v in m["versions"]), default=0) + 1
    fname = f"understanding_v{version}.yaml"
    shutil.copy2(LIVE_PATH, os.path.join(HISTORY_DIR, fname))
    m["versions"].append({
        "version": version,
        "created_at": _now(),
        "file": fname,
        "label": label or f"snapshot v{version}",
        "changes": list(changes or []),
    })
    m["current"] = version
    _save_manifest(m)
    return version


def list_versions() -> list[dict]:
    return _load_manifest()["versions"]


def current_version() -> int:
    return _load_manifest().get("current", 0)


def _version_file(version: int) -> str:
    return os.path.join(HISTORY_DIR, f"understanding_v{version}.yaml")


def show(version: int) -> str:
    """返回指定版本的配置文本；不存在则抛错。"""
    path = _version_file(version)
    if not os.path.exists(path):
        raise FileNotFoundError(f"版本 {version} 不存在于 {HISTORY_DIR}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def rollback_to(version: int) -> int:
    """把指定版本恢复为 live。回滚前先快照当前状态（可再撤销）。返回新 current。"""
    m = _load_manifest()
    versions = [v["version"] for v in m["versions"]]
    if version not in versions:
        raise ValueError(f"版本 {version} 不存在（可用：{versions}）")
    src = _version_file(version)
    if not os.path.exists(src):
        raise FileNotFoundError(f"版本文件缺失：{src}")

    # 先快照当前 live，保留可逆性
    before = snapshot(label=f"pre_rollback_to_v{version}",
                      changes=[f"自动快照：回滚前状态，可撤销本次回滚"])
    shutil.copy2(src, LIVE_PATH)
    m = _load_manifest()  # snapshot 已更新 manifest
    m["current"] = version
    _save_manifest(m)
    return m["current"]


def live_is_clean() -> bool:
    """live 配置是否为空 few_shot / 空关键词（用于提示是否需先建基线快照）。"""
    try:
        import yaml
        with open(LIVE_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        fs = ((data.get("intent_prompt") or {}).get("few_shot") or [])
        kws = ((data.get("intent_mappings") or {}).get("keywords") or {})
        return not fs and not kws
    except Exception:  # noqa: BLE001
        return True
