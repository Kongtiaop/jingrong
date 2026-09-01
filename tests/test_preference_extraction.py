"""偏好表态抽取 + 风险测评偏好持久化接线。"""
from app.services.customer_intent import extract_preference_statement


def test_risk_preference_conservative():
    assert extract_preference_statement("我偏保守，帮我推荐下") == ("risk_preference", "conservative")
    assert extract_preference_statement("我风险承受能力低，喜欢保本") == ("risk_preference", "conservative")


def test_risk_preference_balanced():
    assert extract_preference_statement("我喜欢稳健一点") == ("risk_preference", "balanced")


def test_risk_preference_aggressive():
    assert extract_preference_statement("我比较激进，想追求高收益") == ("risk_preference", "aggressive")


def test_product_preference_steady():
    assert extract_preference_statement("我喜欢稳健的产品") == ("product_preference", "steady")
    assert extract_preference_statement("只要稳健的理财产品") == ("product_preference", "steady")


def test_horizon_preference_long_term():
    assert extract_preference_statement("我可以长期持有") == ("investment_horizon_preference", "long_term")
    assert extract_preference_statement("我打算中长期投资") == ("investment_horizon_preference", "long_term")


def test_non_preference_returns_none():
    # 模糊/非自述偏好不该当真
    assert extract_preference_statement("帮我看看我的订单") is None
    assert extract_preference_statement("这个产品怎么样") is None
    assert extract_preference_statement("稳健和激进有什么区别") is None
