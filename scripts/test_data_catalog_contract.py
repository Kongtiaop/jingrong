from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.services.data_catalog_service import CatalogSearchScope, DataCatalogService


@dataclass
class FakeDomain:
    code: str
    name: str


@dataclass
class FakeTable:
    id: str
    table_name: str
    display_name: str
    description: str
    business_purpose: str
    grain_description: str
    usage_guidance: str
    non_usage_guidance: str
    table_kind: str
    permission_level: str
    aliases_json: list[str]
    domain: FakeDomain
    columns: list = field(default_factory=list)


@dataclass
class FakeColumn:
    table_id: str
    column_name: str
    display_name: str
    description: str
    business_meaning: str
    semantic_type: str
    unit: str
    is_sensitive: bool
    allowed_aggregations_json: list[str]
    aliases_json: list[str]


@dataclass
class FakeRelation:
    from_table_id: str
    from_column_name: str
    to_table_id: str
    to_column_name: str
    cardinality: str
    description: str
    join_guidance: str


@dataclass
class FakeExample:
    question: str
    intent_code: str
    target_table_names_json: list[str]
    target_column_names_json: list[str]
    sql_template: str
    explanation: str


class FakeRepository:
    async def search_tables(self, **kwargs):
        assert kwargs["role_codes"] == ("advisor",)
        assert kwargs["query"] == "客户资产趋势"
        return [
            FakeTable(
                id="asset-table",
                table_name="customer_asset_snapshot",
                display_name="客户资产每日快照",
                description="历史资产估值。",
                business_purpose="资产趋势。",
                grain_description="一行一个客户一天。",
                usage_guidance="查询历史趋势。",
                non_usage_guidance="不表示实时订单。",
                table_kind="snapshot",
                permission_level="restricted",
                aliases_json=["资产趋势"],
                domain=FakeDomain(code="customer_profile", name="客户画像与资产"),
            )
        ]

    async def search_columns(self, **kwargs):
        assert kwargs["table_ids"] == ["asset-table"]
        return [
            FakeColumn(
                table_id="asset-table",
                column_name="net_asset",
                display_name="净资产",
                description="每日净资产。",
                business_meaning="资产扣除负债。",
                semantic_type="money",
                unit="CNY",
                is_sensitive=True,
                allowed_aggregations_json=["sum", "avg"],
                aliases_json=["资产净值"],
            )
        ]

    async def search_examples(self, **kwargs):
        return [
            FakeExample(
                question="查询客户过去三个月的净资产趋势",
                intent_code="customer_net_asset_trend",
                target_table_names_json=["customer_asset_snapshot"],
                target_column_names_json=["net_asset", "snapshot_date"],
                sql_template="SELECT ...",
                explanation="历史趋势使用快照表。",
            )
        ]

    async def relations_for_tables(self, **kwargs):
        return [
            FakeRelation(
                from_table_id="asset-table",
                from_column_name="user_id",
                to_table_id="outside-table",
                to_column_name="id",
                cardinality="many_to_one",
                description="fake relation intentionally outside selected table map",
                join_guidance="skip",
            )
        ]


class FakeRole:
    def __init__(self, code: str):
        self.code = code


class FakeUser:
    is_super_admin = False
    roles = [FakeRole("advisor")]


async def main():
    scope = CatalogSearchScope.from_user(FakeUser())
    assert scope.role_codes == ("advisor",)
    data = await DataCatalogService(FakeRepository()).search(query="客户资产趋势", scope=scope)
    assert data["nl2sql_ready"] is False
    assert data["tables"][0]["table_name"] == "customer_asset_snapshot"
    assert data["tables"][0]["columns"][0]["column_name"] == "net_asset"
    assert data["examples"][0]["intent_code"] == "customer_net_asset_trend"
    assert data["relations"] == []
    assert "SQL generation" in data["next_guard"]
    print({"catalog_table_context": True, "column_context": True, "nl2sql_disabled": True, "pass": True})


if __name__ == "__main__":
    asyncio.run(main())
