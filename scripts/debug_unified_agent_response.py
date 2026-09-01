from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app, lifespan


async def main() -> None:
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1", timeout=45) as client:
            login = await client.post("/auth/login", json={"username": "customer_demo", "password": "Demo@2026Customer"})
            login.raise_for_status()
            token = login.json()["data"]["access_token"]
            response = await client.post(
                "/customer-service/sessions/agent/execute",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "推荐一个产品并申购1000元", "conversation_id": f"debug-{uuid.uuid4()}", "page_context": "/customer-center"},
            )
            print(response.status_code)
            output = json.dumps(response.json(), ensure_ascii=False, indent=2)
            Path("logs/debug_unified_agent_response.json").write_text(output, encoding="utf-8")
            print(output.encode("unicode_escape").decode("ascii"))


if __name__ == "__main__":
    asyncio.run(main())
