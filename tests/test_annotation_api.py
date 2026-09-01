"""bad case 标注接口：路由注册 + 标注合法性校验（纯逻辑，不依赖 DB）。"""
from app.api.annotation import VALID_INTENTS, router


def test_routes_registered():
    paths = {f"{r.path}__{','.join(sorted(r.methods or []))}" for r in router.routes}
    assert "/admin/annotation/summary__GET" in paths
    assert "/admin/annotation/bad-cases__GET" in paths
    assert "/admin/annotation/bad-cases/{bad_case_id}/label__POST" in paths
    assert "/admin/annotation/bad-cases/{bad_case_id}/skip__POST" in paths
    assert "/admin/annotation/export-labeled__GET" in paths
    assert "/admin/annotation/export-pending__GET" in paths


def test_valid_intents_cover_eval_schema():
    # 评测集 intent_codes 都应被标注允许
    import json
    import os
    with open(os.path.join("scripts", "intent_eval_cases.json"), encoding="utf-8") as f:
        codes = set(json.load(f)["intent_codes"])
    assert codes <= VALID_INTENTS, codes - VALID_INTENTS


def test_known_intents_present():
    for code in ("order_status", "product_recommendation", "human_handoff", "unknown", "policy"):
        assert code in VALID_INTENTS
