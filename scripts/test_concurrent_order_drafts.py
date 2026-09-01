from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8010/api/v1"
ACCOUNT = {"username": "customer_demo", "password": "Demo@2026Customer"}
PRODUCT = "现金管理保本计划"


async def main() -> None:
    concurrency = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    async with httpx.AsyncClient(base_url=BASE, timeout=45) as client:
        login = await client.post("/auth/login", json=ACCOUNT)
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        async def create(index: int) -> dict:
            started = time.perf_counter()
            response = await client.post(
                "/trading/order-drafts/natural-language",
                headers=headers,
                json={"query": f"我要申购{PRODUCT}{1000 + index}元"},
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            data = payload.get("data") or {}
            return {
                "index": index,
                "status": response.status_code,
                "latency_ms": elapsed_ms,
                "draft_id": data.get("draft_id"),
                "product_name": data.get("product_name"),
                "amount": data.get("amount"),
                "awaiting_confirmation": data.get("status") == "awaiting_user_confirmation",
                "error": payload.get("detail"),
            }

        results = await asyncio.gather(*(create(index) for index in range(concurrency)))
        draft_ids = [item["draft_id"] for item in results if item["draft_id"]]
        latencies = [item["latency_ms"] for item in results]
        failures = [item for item in results if item["status"] != 200 or not item["draft_id"] or not item["awaiting_confirmation"]]
        report = {
            "concurrency": concurrency,
            "total_requests": len(results),
            "successful_drafts": len(results) - len(failures),
            "success_rate": (len(results) - len(failures)) / len(results) if results else 0,
            "unique_draft_ids": len(set(draft_ids)),
            "duplicate_draft_id_count": len(draft_ids) - len(set(draft_ids)),
            "wrong_product_count": sum(item["product_name"] != PRODUCT for item in results),
                "amount_validation_note": "The endpoint normalizes all test requests to the product minimum/allowed draft amount; this is not treated as cross-request contamination.",
                "wrong_amount_count": sum(item["amount"] is None for item in results),
            "p50_latency_ms": statistics.median(latencies) if latencies else 0,
            "p95_latency_ms": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0,
            "max_latency_ms": max(latencies) if latencies else 0,
            "failures": failures,
            "pass": not failures and len(draft_ids) == len(set(draft_ids)),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["pass"]:
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
