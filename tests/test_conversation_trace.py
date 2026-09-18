# -*- coding: utf-8 -*-
"""对话链路历史回溯（阶段一 3.3）。

实时链路随请求结束就消失了，刷新页面后唯一还能还原真相的是**审计**。
`/customer-service/conversations/{id}/trace` 因此按 `customer_agent.tool_call`
审计还原轮次，供前端"查看推理过程"面板回看。

这里锁定三条容易悄悄坏掉的契约：

1. **字段与实时链路同构**——前端不必为"历史"和"实时"写两套渲染逻辑。
   一旦给 ToolRound 加了新观测字段而回溯没跟上，第一条测试会失败。
2. **缺失的耗时/token 保持 None**——前端靠 None 显示"—"而不是 0。
   填成 0 会让确定性路径看起来"免费"，成本统计失真。
3. **脏审计不能炸整个接口**——历史数据里存在非 JSON 的 detail。
"""
import json
from types import SimpleNamespace

from app.api.customer_service import _trace_rounds, conversation_router
from app.services.customer_model_tools import ToolRound


def _row(detail: dict) -> SimpleNamespace:
    return SimpleNamespace(detail=json.dumps(detail, ensure_ascii=False),
                           created_at="2026-09-18T10:00:00+00:00")


# ---------- 路由 ----------

def test_trace_route_registered():
    paths = {r.path for r in conversation_router.routes}
    assert "/customer-service/conversations/{conversation_id}/trace" in paths


def test_trace_route_mounted_under_api_prefix():
    """回溯接口必须真的挂到 /api/v1 下，否则前端按文档调用会 404。

    不能直接遍历 api_router.routes：新版 FastAPI 用 _IncludedRouter 延迟展开子路由，
    需要顺着 original_router 递归才能拿到合并后的完整路径。
    """
    from app.api import api_router

    def collect(router, prefix: str = "") -> set:
        found = set()
        for route in router.routes:
            if hasattr(route, "path"):
                found.add(prefix + route.path)
            else:
                sub = getattr(route, "original_router", None)
                if sub is not None:
                    # 子路由的 path 已含自身 prefix，此处只继承外层前缀
                    found |= collect(sub, prefix)
        return found

    paths = collect(api_router, getattr(api_router, "prefix", ""))
    assert "/api/v1/customer-service/conversations/{conversation_id}/trace" in paths


# ---------- 结构：与实时链路同构 ----------

def test_trace_rounds_cover_every_live_round_field():
    """回溯输出必须覆盖 ToolRound 的全部字段。

    ToolRound 是实时链路的契约，回溯一旦少一个字段，前端就会在历史模式下
    出现空白。用 ToolRound 的字段集合作断言，后续加字段会自动被这条守住。
    """
    live_fields = set(ToolRound.__dataclass_fields__)
    out = _trace_rounds([_row({"round": 1, "tool": "get_my_order_status",
                               "arguments": {"query": "订单"}, "success": True})])
    assert live_fields <= set(out[0]), f"回溯缺少字段：{live_fields - set(out[0])}"


def test_trace_rounds_keeps_timing_and_tokens():
    """耗时与 token 是回溯的核心价值——没有它们就只剩"调过什么"。"""
    out = _trace_rounds([_row({
        "round": 2, "tool": "search_customer_knowledge", "arguments": {"query": "适当性"},
        "success": True, "error": None, "evidence_status": "sufficient", "citations": 3,
        "reasoning": "用户问适当性定义", "duration_ms": 420.0, "llm_duration_ms": 210.5,
        "prompt_tokens": 120, "completion_tokens": 30, "output_preview": "命中 3 条知识块",
    })])
    r = out[0]
    assert r["duration_ms"] == 420.0
    assert r["llm_duration_ms"] == 210.5
    assert r["prompt_tokens"] == 120
    assert r["completion_tokens"] == 30
    assert r["output_preview"] == "命中 3 条知识块"
    assert r["created_at"] == "2026-09-18T10:00:00+00:00"


def test_trace_rounds_keeps_blocked_reason_and_tokens():
    """被拦截的轮次要能看出"为什么被拦"——这是安全护栏生效的证据。"""
    out = _trace_rounds([_row({
        "round": 3, "tool": "create_order", "arguments": {"amount": 1000},
        "success": False, "error": "tool_not_allowed", "reasoning": "尝试下单",
        "prompt_tokens": 80, "completion_tokens": 10,
        "output_preview": "未执行：该工具不在只读白名单内",
    })])
    r = out[0]
    assert r["success"] is False
    assert r["error"] == "tool_not_allowed"
    assert r["reasoning"] == "尝试下单"
    assert r["prompt_tokens"] == 80, "被拦截轮次的决策开销同样要回溯得到"


# ---------- 缺失值：None 而不是 0 ----------

def test_trace_rounds_missing_metrics_stay_none():
    """确定性路径 / 老审计没有 token 时必须保持 None。

    前端据此显示"—"：记成 0 会让未走模型的路径看起来零成本。
    """
    out = _trace_rounds([_row({"round": 1, "tool": "get_my_risk_level",
                               "arguments": {}, "success": True})])
    r = out[0]
    assert r["prompt_tokens"] is None
    assert r["completion_tokens"] is None
    assert r["duration_ms"] is None
    assert r["output_preview"] == ""
    assert r["citations"] == 0


# ---------- 健壮性 ----------

def test_trace_rounds_skips_malformed_detail():
    """历史脏数据（非 JSON 的 detail）跳过该条，不能让整个接口 500。"""
    rows = [
        SimpleNamespace(detail="not-json", created_at=None),
        _row({"round": 1, "tool": "get_my_order_status", "arguments": {}, "success": True}),
        SimpleNamespace(detail=None, created_at=None),
    ]
    out = _trace_rounds(rows)
    assert len(out) == 1
    assert out[0]["tool"] == "get_my_order_status"


def test_trace_rounds_empty_when_no_audit():
    assert _trace_rounds([]) == []
