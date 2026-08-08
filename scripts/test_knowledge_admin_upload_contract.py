from __future__ import annotations

import asyncio
import io
import json

import httpx

BASE_URL = "http://127.0.0.1:8010/api/v1"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        login = await client.post("/auth/login", json={"username": "super_admin_demo", "password": "Demo@2026SuperAdmin"})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        response = await client.post(
            "/knowledge/documents/upload",
            headers=headers,
            files={"file": ("knowledge-upload-contract.md", b"# Upload contract\n\nKnowledge document pipeline contract.", "text/markdown")},
            data={"category": "faq", "permission_level": "public"},
        )
        response.raise_for_status()
        data = response.json()["data"]
        assert data["pipeline_status"] == "queued"
        assert data["document"]["file_name"] == "knowledge-upload-contract.md"
        assert data["document"]["status"] == "processing"
        listing = await client.get("/knowledge/documents", headers=headers)
        listing.raise_for_status()
        assert any(item["id"] == data["document"]["id"] for item in listing.json()["data"]["items"])
        unsupported = await client.post(
            "/knowledge/documents/upload",
            headers=headers,
            files={"file": ("bad.pdf", b"not a pdf parser", "application/pdf")},
            data={"category": "faq", "permission_level": "public"},
        )
        assert unsupported.status_code == 400
        print(json.dumps({"upload_queued": True, "listed": True, "unsupported_rejected": True, "pass": True}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
