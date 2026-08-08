from __future__ import annotations

import asyncio

import httpx


BASE_URL = "http://127.0.0.1:8010/api/v1"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20.0) as client:
        login = await client.post("/auth/login", json={"username": "super_admin_demo", "password": "Demo@2026SuperAdmin"})
        login.raise_for_status()
        token = login.json()["data"]["access_token"]
        response = await client.post(
            "/data-catalog/search",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": "客户资产趋势", "top_k": 5},
        )
        response.raise_for_status()
        data = response.json()["data"]
        names = [item["table_name"] for item in data["tables"]]
        assert "customer_asset_snapshot" in names, names
        snapshot = next(item for item in data["tables"] if item["table_name"] == "customer_asset_snapshot")
        assert snapshot["grain_description"]
        assert any(column["column_name"] == "net_asset" for column in snapshot["columns"])
        assert data["nl2sql_ready"] is False
        denied_login = await client.post("/auth/login", json={"username": "customer_demo", "password": "Demo@2026Customer"})
        denied_login.raise_for_status()
        denied_token = denied_login.json()["data"]["access_token"]
        denied = await client.post(
            "/data-catalog/search",
            headers={"Authorization": f"Bearer {denied_token}"},
            json={"query": "客户资产趋势", "top_k": 5},
        )
        assert denied.status_code == 403, denied.text
        print({"catalog_http": True, "asset_snapshot_recalled": True, "nl2sql_disabled": True, "customer_access_denied": True, "pass": True})


if __name__ == "__main__":
    asyncio.run(main())
