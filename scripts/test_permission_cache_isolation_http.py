from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from app.main import app, lifespan
except ModuleNotFoundError as exc:
    if exc.name == "pgvector":
        raise SystemExit("BLOCKED: install project dependencies (pgvector) before running the real Redis/HTTP regression") from exc
    raise

BASE = "http://test/api/v1"
ACCOUNTS = [
    {"username": "risk_manager_demo", "password": "Demo@2026RiskManager", "label": "risk"},
    {"username": "operations_demo", "password": "Demo@2026Operations", "label": "operations"},
]


async def main() -> None:
    async with lifespan(app):
        redis = app.state.redis.client
        await redis.ping()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=30) as client:
            tokens = {}
            for account in ACCOUNTS:
                response = await client.post("/auth/login", json={"username": account["username"], "password": account["password"]})
                response.raise_for_status()
                tokens[account["label"]] = response.json()["data"]["access_token"]

            async def execute(label: str) -> dict:
                response = await client.post(
                    "/customer-service/sessions/agent/execute",
                    headers={"Authorization": f"Bearer {tokens[label]}"},
                    json={"content": "现在有多少风险预警", "conversation_id": f"cache-isolation-{label}-{uuid.uuid4()}", "page_context": "/backoffice"},
                )
                response.raise_for_status()
                return response.json()["data"]

            first_results = await asyncio.gather(execute("risk"), execute("operations"))
            second_results = await asyncio.gather(execute("risk"), execute("operations"))
            keys = [key async for key in redis.scan_iter(match="wm:data_catalog_search:*")]
            assert len(keys) >= 2, keys
            assert all("risk:alert_read" not in key and "order:review" not in key for key in keys), keys
            assert all(result["route"] in {"data_analysis", "customer_service"} for result in first_results + second_results), first_results + second_results
            analysis_results = [item for result in first_results + second_results for item in result.get("results", []) if item.get("intent") == "data_analysis"]
            assert all(item.get("data", {}).get("query_plan", {}).get("free_sql") is False for item in analysis_results), analysis_results
            print(json.dumps({"redis_reachable": True, "concurrent_requests": 4, "permission_scoped_keys": len(keys), "plaintext_permission_in_key": False, "analysis_results": len(analysis_results), "pass": True}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
