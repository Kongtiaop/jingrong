from app.services.understanding_config import UnderstandingConfig, understanding_config


def _cfg():
    c = UnderstandingConfig()
    c._data = {
        "model_routing": {
            "default": "qwen-plus",
            "tiers": [
                {"max": "S", "model": "qwen-turbo"},
                {"max": "M", "model": "qwen-plus"},
                {"max": "L", "model": "qwen-max"},
            ],
        },
        "difficulty_weights": {"rule_confidence": 0.4, "intent_count": 0.2, "negation": 0.15, "conjunction": 0.15, "ambiguous": 0.1},
        "retrieval": {"top_k": 5, "rrf_k": 60},
    }
    return c


def test_simple_query_routes_to_small_model():
    c = _cfg()
    assert c.estimate_difficulty("帮我查一下我的订单状态", rule_confidence=0.95) == "S"
    assert c.model_for_query("帮我查一下我的订单状态", rule_confidence=0.95) == "qwen-turbo"


def test_negation_query_routes_to_large_model():
    c = _cfg()
    d = c.estimate_difficulty("先看看不要买，合适再说", rule_confidence=0.3)
    assert d in {"M", "L"}
    assert c.model_for_query("先看看不要买，合适再说", rule_confidence=0.3) in {"qwen-plus", "qwen-max"}


def test_multi_intent_conjunction_routes_to_medium_or_large():
    c = _cfg()
    d = c.estimate_difficulty("推荐并申购稳健产品", rule_confidence=0.5, intent_count=2)
    assert d in {"M", "L"}


def test_default_and_reload():
    c = UnderstandingConfig()
    assert c.resolve_model("Z") == "qwen-plus"   # 未知难度 → default
    c.reload()                                    # 从 yaml 加载不抛错
    assert isinstance(understanding_config.get_top_k(), int)
