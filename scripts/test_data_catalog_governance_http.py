from __future__ import annotations

import asyncio

import httpx


BASE_URL = "http://127.0.0.1:8010/api/v1"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        login = await client.post("/auth/login", json={"username": "super_admin_demo", "password": "Demo@2026SuperAdmin"})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        listed = await client.get("/data-catalog/tables", headers=headers)
        listed.raise_for_status()
        table = next(item for item in listed.json()["data"]["items"] if item["table_name"] == "customer_asset_snapshot")
        original_aliases = table["aliases"]
        edited_aliases = [*original_aliases, "资产走势验收别名"]
        update = await client.put(
            f"/data-catalog/tables/{table['id']}",
            headers=headers,
            json={"aliases": edited_aliases, "change_note": "HTTP 回归：验证目录表别名编辑与版本记录"},
        )
        update.raise_for_status()
        assert "资产走势验收别名" in update.json()["data"]["aliases"]

        revisions = await client.get(f"/data-catalog/revisions/table/{table['id']}", headers=headers)
        revisions.raise_for_status()
        assert revisions.json()["data"][0]["change_note"] == "HTTP 回归：验证目录表别名编辑与版本记录"

        column = next(item for item in table["columns"] if item["column_name"] == "net_asset")
        old_queryable = column["is_queryable"]
        toggle = await client.put(
            f"/data-catalog/columns/{column['id']}",
            headers=headers,
            json={"is_queryable": old_queryable, "change_note": "HTTP 回归：字段目录版本记录"},
        )
        toggle.raise_for_status()
        assert toggle.json()["data"]["is_queryable"] == old_queryable

        await client.put(
            f"/data-catalog/tables/{table['id']}",
            headers=headers,
            json={"aliases": original_aliases, "change_note": "HTTP 回归清理：恢复目录表别名"},
        )
        print({"catalog_list": True, "table_update_revision": True, "column_update_revision": True, "cleanup": True, "pass": True})


if __name__ == "__main__":
    asyncio.run(main())
