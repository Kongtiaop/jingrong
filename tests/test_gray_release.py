import json
import os
import shutil
import uuid

import pytest
import yaml

from app.services import understanding_config as uc


@pytest.fixture()
def gray_env(monkeypatch):
    """把 understanding_config 指向工作区内临时目录，准备 live + 灰度版本 + gray.json。"""
    tmp_path = os.path.join(os.getcwd(), "logs", "_graytest_" + uuid.uuid4().hex[:8])
    os.makedirs(tmp_path, exist_ok=True)
    live = os.path.join(tmp_path, "understanding.yaml")
    gray_file = os.path.join(tmp_path, "gray_ver.yaml")
    gj = os.path.join(tmp_path, "gray.json")
    with open(live, "w", encoding="utf-8") as f:
        f.write("intent_prompt:\n  few_shot: []\n"
                "model_routing:\n  default: qwen-plus\n  tiers:\n    - {max: \"L\", model: \"qwen-plus\"}\n")
    with open(gray_file, "w", encoding="utf-8") as f:
        f.write("intent_prompt:\n  few_shot: [{query: GRAYMARK, output: X}]\n"
                "model_routing:\n  default: qwen-gray\n  tiers:\n    - {max: \"L\", model: \"qwen-gray\"}\n")
    monkeypatch.setattr(uc, "_CONFIG_PATH", live)
    monkeypatch.setattr(uc, "_GRAY_JSON", gj)
    yield {"live": live, "gray": gray_file, "gray_json": gj}
    shutil.rmtree(tmp_path, ignore_errors=True)


def _set_gray(gray_env, percent, active=True):
    json.dump({"active": active, "version": 9, "percent": percent, "file": str(gray_env["gray"])},
              open(gray_env["gray_json"], "w", encoding="utf-8"))


def _has_marker(c: uc.UnderstandingConfig, query: str) -> bool:
    return any(f.get("query") == "GRAYMARK" for f in c.get_intent_prompt(query).get("few_shot"))


def test_gray_zero_percent_uses_live(gray_env):
    c = uc.UnderstandingConfig()
    _set_gray(gray_env, 0)
    c.reload()
    assert not _has_marker(c, "任意查询")
    assert not c.gray_info()["active"] or c.gray_info()["percent"] == 0


def test_gray_hundred_percent_uses_gray(gray_env):
    c = uc.UnderstandingConfig()
    _set_gray(gray_env, 100)
    c.reload()
    assert c.gray_info()["active"]
    assert _has_marker(c, "任意查询")
    assert c.model_for_query("简单查询", rule_confidence=0.95) == "qwen-gray"


def test_gray_hash_split_is_deterministic(gray_env):
    c = uc.UnderstandingConfig()
    _set_gray(gray_env, 50)
    c.reload()
    queries = ["查余额", "查订单", "买基金", "转人工", "看持仓", "查工单"]
    routed = [q for q in queries if _has_marker(c, q)]
    assert 0 < len(routed) < len(queries)  # 确实分流
    # 同一 query 恒走同一版本
    assert _has_marker(c, "查余额") == _has_marker(c, "查余额")


def test_gray_inactive_uses_live(gray_env):
    c = uc.UnderstandingConfig()
    _set_gray(gray_env, 100, active=False)
    c.reload()
    assert not c.gray_info()["active"]
    assert not _has_marker(c, "任意查询")
