"""客户本人数据的自助查询契约。

背景（2026-09-24 实测）：客户问「我的持仓有哪些」「我的资产有多少」「我的账户余额」，
三条全部得到「当前身份不具备该领域助手权限，已由客服助手为您提供受控说明或下一步指引。」
——一句权限拒绝，客户拿不到自己的数据。

根因是**接错线**：`AGENT_ROUTE_BY_INTENT` 把 `asset_summary` 映射到 `"analysis"`
（员工侧数据分析 Agent，需 `DataAnalysisAgent` 权限）。而图里的模型工具轮
其实已经把数据取到了，却在 Agent 权限守卫处因该权限对客户不可用
而**清空 results 并替换成那句拒绝**。

面向客户的只读工具 `get_my_asset_summary` 早已实现（返回资产快照 + 前 10 条持仓）。
故修复是让路由与既有实现对齐，而不是新写能力。
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from app.services.customer_agent_execution import AGENT_ROUTE_BY_INTENT, AGENTS_BY_ROUTE
from app.services.customer_intent import CustomerIntentPlanner
from app.services.customer_query_router import CustomerQueryRouter


# ---------- 1. 路由归属：客户的自有数据不得指派给员工侧 Agent ----------

def test_asset_summary_routes_to_customer_service_not_analysis():
    """回归点：`asset_summary` 曾被映射到 `analysis`（员工侧）。

    客户没有 `DataAnalysisAgent` 权限 → results 被清空 + 换成权限拒绝话术。
    这里直接锁住映射，避免以后有人"顺手"把它改回分析侧。
    """
    assert AGENT_ROUTE_BY_INTENT["asset_summary"] == "customer_service"
    agents = AGENTS_BY_ROUTE["customer_service"]
    assert "DataAnalysisAgent" not in agents


def test_employee_only_analysis_agent_still_exists_for_staff_paths():
    """`analysis` 路由与其 Agent 不能被删掉——员工侧的 NL2SQL 仍依赖它，
    本次只是不再把**客户的自有数据查询**派到那里去。"""
    assert AGENT_ROUTE_BY_INTENT.get("asset_summary") != "analysis"
    assert AGENTS_BY_ROUTE["analysis"] == ["DataAnalysisAgent"]


# ---------- 2. 触发词：客户的实际说法要能命中意图 ----------

def test_transaction_phrasing_maps_to_order_status():
    """客户很少说"订单"，实际说"交易记录/明细/成交记录"。

    实测修复前「我最近的交易记录」落到未知意图 → 回一句"您想查询哪一类信息…"。
    """
    planner = CustomerIntentPlanner()
    for query in ("我最近的交易记录", "我的交易明细", "查一下成交记录"):
        intents = {t.intent for t in planner.plan(query).tasks}
        assert "order_status" in intents, f"{query} 应识别为 order_status，实际 {intents}"


def test_holding_phrasing_maps_to_asset_summary():
    planner = CustomerIntentPlanner()
    for query in ("我的持仓有哪些", "我的资产有多少", "我的账户余额"):
        intents = {t.intent for t in planner.plan(query).tasks}
        assert "asset_summary" in intents, f"{query} 应识别为 asset_summary，实际 {intents}"


# ---------- 3. 答案必须带上持仓明细本身 ----------

class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)

    def all(self):
        return list(self._rows)


class _FakeSession:
    """按调用次序依次返回预置结果——_asset_summary 先查快照、再查持仓。"""

    def __init__(self, results):
        self._results = list(results)
        self._index = 0

    async def execute(self, statement):  # noqa: ARG002
        result = self._results[self._index]
        self._index += 1
        return result


def _asset():
    return SimpleNamespace(total_asset=Decimal("302543.82"), cash_balance=Decimal("200000"),
                           investable_asset=Decimal("300000"), liability=Decimal("0"),
                           net_asset=Decimal("302543.82"), snapshot_time=None)


def _holding(name: str, market_value: str, profit: str):
    return SimpleNamespace(id=f"h-{name}", product_id="p1", market_value=Decimal(market_value),
                           profit_loss=Decimal(profit), quantity=Decimal("1"))


def test_asset_summary_answer_lists_holdings_not_just_count():
    """只给"共 N 条持仓"是不够的：模型工具轮只把 `answer` 文本喂给模型
    （`data` 不参与生成），所以问"持仓有哪些"时必须把明细写进 answer。"""
    session = _FakeSession([
        _Result([_asset()]),
        _Result([(_holding("稳健增值计划", "100540.42", "541.42"),
                  "稳健增值计划"),
                 (_holding("现金管理保本计划", "2003.40", "4.40"),
                  "现金管理保本计划")]),
    ])
    result = asyncio.run(CustomerQueryRouter()._asset_summary(session=session, customer_id="c1"))

    assert "稳健增值计划" in result.answer and "现金管理保本计划" in result.answer
    assert "100,540.42" in result.answer
    assert "302,543.82" in result.answer
    # data 侧仍保留结构化明细，前端与后续 task 依赖它
    assert result.data["holdings"][0]["product_name"] == "稳健增值计划"
    assert result.data["latest_asset"]["total_asset"] == 302543.82


def test_asset_summary_without_holdings_says_so_instead_of_listing():
    session = _FakeSession([_Result([_asset()]), _Result([])])
    result = asyncio.run(CustomerQueryRouter()._asset_summary(session=session, customer_id="c1"))
    assert "没有有效持仓" in result.answer
    assert result.data["holdings"] == []
