from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app, lifespan

BASE = "http://test/api/v1"
SUPER = {"username": "super_admin_demo", "password": "Demo@2026SuperAdmin"}
TARGET = {"username": "risk_manager_demo", "password": "Demo@2026RiskManager"}


async def main() -> None:
    async with lifespan(app):
        redis = app.state.redis.client
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=30) as client:
            async def login(account: dict[str, str]) -> str:
                response = await client.post("/auth/login", json=account)
                response.raise_for_status()
                return response.json()["data"]["access_token"]

            super_token = await login(SUPER)
            target_token = await login(TARGET)
            target_me = await client.get("/auth/me", headers={"Authorization": f"Bearer {target_token}"})
            target_me.raise_for_status()
            target_id = target_me.json()["data"]["id"]
            version_key = f"wm:permission-context-version:{target_id}"

            async def analyze() -> dict:
                response = await client.post(
                    "/customer-service/sessions/agent/execute",
                    headers={"Authorization": f"Bearer {target_token}"},
                    json={"content": "现在有多少风险预警", "conversation_id": f"cache-invalidate-{uuid.uuid4()}", "page_context": "/backoffice"},
                )
                response.raise_for_status()
                return response.json()["data"]

            await analyze()
            version_before = await redis.get(version_key) or "0"
            keys_before = [key async for key in redis.scan_iter(match="wm:data_catalog_search:*")]
            update = await client.put(
                f"/admin/users/{target_id}/roles",
                headers={"Authorization": f"Bearer {super_token}"},
                json={"roles": ["risk_manager"]},
            )
            update.raise_for_status()
            version_after = await redis.get(version_key) or "0"
            assert str(version_before) != str(version_after), {"before": version_before, "after": version_after}
            await analyze()
            keys_after = [key async for key in redis.scan_iter(match="wm:data_catalog_search:*")]
            assert len(keys_after) > len(keys_before), {"before": keys_before, "after": keys_after}
            print(json.dumps({"target_user_id": target_id, "version_before": str(version_before), "version_after": str(version_after), "keys_before": len(keys_before), "keys_after": len(keys_after), "pass": True}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
