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
ACCOUNTS = [
    {"username": "customer_demo", "password": "Demo@2026Customer"},
    {"username": "hNW_demo", "password": "Demo@2026HighNetWorth"},
]


async def login(client: httpx.AsyncClient, account: dict[str, str]) -> str:
    response = await client.post("/auth/login", json=account)
    response.raise_for_status()
    return response.json()["data"]["access_token"]


async def worker(client: httpx.AsyncClient, token: str, index: int, total: int) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    conversation_id = f"concurrent-isolation-{uuid.uuid4()}"
    started = time.perf_counter()
    response = await client.post(
        "/customer-service/sessions/agent/execute",
        headers=headers,
        json={
            "content": "我的资产和持仓有多少",
            "conversation_id": conversation_id,
            "page_context": "/customer-center",
        },
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    data = response.json()["data"]
    serialized = json.dumps(data, ensure_ascii=False)
    return {
        "index": index,
        "status": response.status_code,
        "latency_ms": elapsed_ms,
        "conversation_id_preserved": data.get("conversation_id") == conversation_id,
        "has_response": bool(data.get("answer") or data.get("results")),
        "contains_other_conversation": any(
            marker in serialized for marker in ("concurrent-isolation-",)
        ) and conversation_id not in serialized,
        "result_count": len(data.get("results") or []),
    }


async def main() -> None:
    concurrency = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    async with httpx.AsyncClient(base_url=BASE, timeout=45) as client:
        tokens = [await login(client, account) for account in ACCOUNTS]
        jobs = [worker(client, tokens[index % len(tokens)], index, concurrency) for index in range(concurrency)]
        results = await asyncio.gather(*jobs)

    failures = [item for item in results if item["status"] != 200 or not item["has_response"] or not item["conversation_id_preserved"]]
    latencies = [item["latency_ms"] for item in results]
    report = {
        "concurrency": concurrency,
        "total_requests": len(results),
        "successful_requests": len(results) - len(failures),
        "success_rate": (len(results) - len(failures)) / len(results) if results else 0,
        "conversation_isolation_failures": sum(item["contains_other_conversation"] for item in results),
        "p50_latency_ms": statistics.median(latencies) if latencies else 0,
        "p95_latency_ms": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0,
        "max_latency_ms": max(latencies) if latencies else 0,
        "failures": failures,
        "pass": not failures and not any(item["contains_other_conversation"] for item in results),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
