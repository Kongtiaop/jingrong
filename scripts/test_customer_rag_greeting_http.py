from __future__ import annotations

import asyncio

import httpx


BASE_URL = "http://127.0.0.1:8010/api/v1"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        login = await client.post("/auth/login", json={"username": "customer_demo", "password": "Demo@2026Customer"})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        response = await client.post("/customer-rag/answer", headers=headers, json={"query": "你好", "intent": "faq", "top_k": 5})
        response.raise_for_status()
        data = response.json()["data"]
        assert data["answer_mode"] == "greeting", data
        assert data["evidence_status"] == "not_required", data
        assert data["handoff_required"] is False, data
        assert data["work_order_id"] is None, data
        assert not data["citations"], data
        print({"greeting_http": True, "no_retrieval_handoff": True, "no_work_order": True, "pass": True})


if __name__ == "__main__":
    asyncio.run(main())
