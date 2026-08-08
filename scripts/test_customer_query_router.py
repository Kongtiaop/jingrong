from app.services.customer_query_router import CustomerQueryRouter


def main():
    router = CustomerQueryRouter()
    cases = {
        "我的工单进度怎么样": "workorder_status",
        "我的订单审核到哪一步了": "order_status",
        "账户是不是被风险限制了": "risk_restriction",
        "我现在的资产和持仓": "asset_summary",
        "你好": None,
        "公司业务是什么": None,
    }
    for query, expected in cases.items():
        actual = router.detect_route(query)
        assert actual == expected, (query, actual, expected)
    assert router.matches("我的订单状态")
    assert not router.matches("公司客服电话是多少")
    print({"deterministic_routes": cases, "pass": True})


if __name__ == "__main__":
    main()
