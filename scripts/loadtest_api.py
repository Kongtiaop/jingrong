#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""wealth-manager API 负载压测（端到端 HTTP）。

用法：
  conda run -n langchain_env python -m scripts.loadtest_api \
      --base http://127.0.0.1:8010 --concurrency 100 --duration 10

采集：P50/P95/P99、错误率、RPS。端点默认 health + profile/me + recommendations。
"""
import argparse
import asyncio
import statistics
import time

import httpx

BASE = "http://127.0.0.1:8010"
USER = "customer_demo"
PASS = "Demo@2026Customer"
AUTH_ENDPOINTS = ["/api/v1/auth/me", "/api/v1/profile/me", "/api/v1/profile/me/recommendations"]


async def login(client: httpx.AsyncClient) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": USER, "password": PASS})
    r.raise_for_status()
    data = r.json().get("data") or r.json()
    return data["access_token"]


async def worker(concurrency_id: int, stop: asyncio.Event, results: list, errors: list, token: str, paths: list):
    headers = {"Authorization": f"Bearer {token}"}
    idx = 0
    async with httpx.AsyncClient(base_url=BASE, timeout=20, headers=headers) as client:
        while not stop.is_set():
            path = paths[idx % len(paths)]
            idx += 1
            t0 = time.perf_counter()
            try:
                r = await client.get(path)
                dt = (time.perf_counter() - t0) * 1000
                results.append(dt)
                if r.status_code >= 400:
                    errors.append((path, r.status_code))
            except Exception as e:
                errors.append((path, str(e)[:60]))


async def run(concurrency: int, duration: float, endpoints: list[str]):
    results: list = []
    errors: list = []
    stop = asyncio.Event()
    paths = ["/api/v1/health" if e == "health" else e for e in endpoints]
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as client:
        token = await login(client)
    tasks = [asyncio.create_task(worker(i, stop, results, errors, token, paths)) for i in range(concurrency)]
    start = time.perf_counter()
    await asyncio.sleep(duration)
    stop.set()
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    n = len(results)
    if n == 0:
        print("no successful samples"); return
    results.sort()
    def pct(p):
        return results[min(n - 1, int(n * p))]
    rps = n / elapsed
    print(f"--- 压测结果 (concurrency={concurrency}, duration={duration}s, endpoints={paths}) ---")
    print(f"样本数: {n}   耗时: {elapsed:.1f}s   RPS: {rps:.1f}")
    print(f"P50: {pct(0.50):.2f} ms | P95: {pct(0.95):.2f} ms | P99: {pct(0.99):.2f} ms | max: {results[-1]:.2f} ms")
    print(f"错误数: {len(errors)}")
    if errors[:10]:
        print("前10个错误:", errors[:10])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--concurrency", type=int, default=100)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--endpoint", action="append", default=None,
                    help="端点；可多次传。示例: --endpoint health --endpoint /api/v1/profile/me")
    a = ap.parse_args()
    BASE = a.base
    if a.endpoint:
        eps = a.endpoint
    else:
        eps = ["health"] + AUTH_ENDPOINTS
    asyncio.run(run(a.concurrency, a.duration, eps))
