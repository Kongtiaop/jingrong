from __future__ import annotations

import asyncio
from time import monotonic

import httpx


BASE_URL = "http://127.0.0.1:8010/api/v1"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=25.0) as client:
        login = await client.post("/auth/login", json={"username": "super_admin_demo", "password": "Demo@2026SuperAdmin"})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        queued = await client.post("/data-catalog/embeddings", headers=headers, params={"limit": 200})
        queued.raise_for_status()
        event_id = queued.json()["data"]["event_id"]
        latest: dict = {}
        deadline = monotonic() + 40
        while monotonic() < deadline:
            response = await client.post("/data-catalog/search", headers=headers, json={"query": "客户资产趋势", "top_k": 5})
            response.raise_for_status()
            latest = response.json()["data"]
            assert any(item["table_name"] == "customer_asset_snapshot" for item in latest["tables"])
            if latest["retrieval_mode"] == "catalog-hybrid-text-pgvector":
                break
            await asyncio.sleep(3)
        assert latest["retrieval_mode"] in {"catalog-hybrid-text-pgvector", "catalog-text-fallback", "catalog-text-first"}
        print({"event_id": event_id, "catalog_embedding_async": True, "search_stays_available": True, "retrieval_mode": latest["retrieval_mode"], "asset_snapshot_recalled": True, "pass": True})


if __name__ == "__main__":
    asyncio.run(main())
