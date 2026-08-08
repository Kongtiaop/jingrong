from __future__ import annotations
import asyncio
import json
import httpx

BASE_URL = "http://127.0.0.1:8010/api/v1"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20) as client:
        login = await client.post("/auth/login", json={"username": "customer_demo", "password": "Demo@2026Customer"})
        login.raise_for_status()
        token_data = login.json()["data"]
        headers = {"Authorization": f"Bearer {token_data['access_token']}"}
        paths = [
            "/auth/me",
            "/profile/me",
            "/profile/me/assets/history?limit=1",
            "/operations/me?limit=1",
            "/trading/account/me",
            "/trading/orders/me?limit=1",
        ]
        checks = {}
        for path in paths:
            response = await client.get(path, headers=headers)
            checks[path] = response.status_code == 200
        invalid_refresh = await client.post("/auth/refresh", json={"refresh_token": "invalid-refresh-token-for-test"})
        checks["invalid_refresh_is_401"] = invalid_refresh.status_code == 401
        result = {"checks": checks, "pass": all(checks.values())}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
