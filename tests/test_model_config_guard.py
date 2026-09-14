# -*- coding: utf-8 -*-
"""模型配置守卫：禁止把已知无权限的模型写进路由/工具轮配置。

ISSUE-20260914-TIER-S-ROUTES-TO-UNAUTHORIZED-MODEL：
  `config/understanding.yaml` 曾把简单 query（tier S）路由到 `qwen-turbo`，
  而该模型在本账号 **无权限**（`PermissionDeniedError`）。
  后果：**简单 query 的 LLM 意图兜底从未真正生效**——调用失败被 `except` 吞掉、
  静默回退规则规划器；且每次白耗一次失败调用。这类问题**不会报错、只在运行时静默降级**，
  故用测试钉住「配置里不得出现已知不可用模型」。

注意：本测试只校验**静态配置**，不做真实网络探测（避免测试依赖外部 API）。
真实可用性由 `scripts/compare_tool_model_latency.py` 在需要时人工确认。
"""
from app.services.understanding_config import understanding_config

# 2026-09-14 实测在本账号返回 PermissionDeniedError 的模型
UNAUTHORIZED_MODELS = {
    "qwen-turbo",
    "qwen3-max",
    "qwen2.5-7b-instruct",
}

# 实测可用（2026-09-14）
AUTHORIZED_MODELS = {
    "qwen-plus",
    "qwen-max",
    "qwen-flash",
}


def test_model_routing_tiers_use_authorized_models():
    """understanding.yaml 的每个 tier 都必须指向已确认可用的模型。"""
    understanding_config.reload()
    data = understanding_config._data.get("model_routing", {})
    for tier in data.get("tiers", []):
        model = tier.get("model", "")
        assert model not in UNAUTHORIZED_MODELS, (
            f"tier {tier.get('max')} 使用了无权限模型 {model}——"
            "会导致调用失败并静默降级（见 ISSUE-20260914-TIER-S-ROUTES-TO-UNAUTHORIZED-MODEL）"
        )


def test_model_routing_default_is_authorized():
    understanding_config.reload()
    default = understanding_config._data.get("model_routing", {}).get("default", "")
    assert default not in UNAUTHORIZED_MODELS, f"默认模型 {default} 无权限"


def test_authorized_models_list_matches_reality():
    """可用模型清单应与实测一致；若账号权限变化需同步更新本清单。"""
    understanding_config.reload()
    tiers = {t.get("model") for t in understanding_config._data.get("model_routing", {}).get("tiers", [])}
    assert tiers, "model_routing.tiers 不应为空"
    assert tiers <= AUTHORIZED_MODELS, \
        f"出现了清单外的模型 {tiers - AUTHORIZED_MODELS}，请在实测确认后更新 AUTHORIZED_MODELS"


def test_tool_model_is_authorized():
    """模型工具轮使用的模型必须在可用清单内。"""
    from app.core.settings import get_settings
    model = get_settings().qwen_tool_model
    assert model not in UNAUTHORIZED_MODELS, f"工具轮模型 {model} 无权限"
    assert model in AUTHORIZED_MODELS, f"工具轮模型 {model} 不在已确认可用清单内"


def test_tool_timeout_exceeds_observed_p95():
    """工具轮超时应显著高于实测 p95，避免正常调用被判超时。

    实测（8 条 query）：qwen-flash p95=1092ms / qwen-plus p95=1398ms / qwen-max p95=1589ms。
    取 3s 作为观测上界，超时至少应是它的数倍。
    """
    from app.core.settings import get_settings
    timeout = get_settings().qwen_tool_call_timeout_seconds
    observed_p95_ceiling_ms = 3000
    assert timeout * 1000 >= observed_p95_ceiling_ms * 3, (
        f"工具轮超时 {timeout}s 相对实测 p95（~1.6s）余量不足；"
        "不过再紧也不能低于此，否则正常调用会被误判超时"
    )
