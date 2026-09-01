from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app, lifespan
from app.models.auth import Role, User
from app.services.auth_service import hash_password
from sqlalchemy import select

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

            async def me(token: str) -> dict:
                response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                return response.json()["data"]

            super_token = await login(SUPER)
            target_token = await login(TARGET)
            target_user = await me(target_token)
            role_code = "risk_manager"
            assert role_code in target_user["roles"], target_user
            roles_response = await client.get("/admin/roles", headers={"Authorization": f"Bearer {super_token}"})
            roles_response.raise_for_status()
            role = next(item for item in roles_response.json()["data"] if item["code"] == role_code)

            async def analyze(token: str) -> None:
                response = await client.post(
                    "/customer-service/sessions/agent/execute",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"content": "现在有多少风险预警", "conversation_id": f"role-cache-{uuid.uuid4()}", "page_context": "/backoffice"},
                )
                response.raise_for_status()

            probe_id = str(uuid.uuid4())
            probe_username = f"risk_cache_probe_{uuid.uuid4().hex[:12]}"
            try:
                async with app.state.database.session_factory() as session:
                    risk_role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
                    probe = User(id=probe_id, username=probe_username, password_hash=hash_password("Probe@2026Cache"), display_name="Risk cache probe", status="active", is_super_admin=False)
                    probe.roles.append(risk_role)
                    session.add(probe)
                    await session.commit()
                probe_token = await login({"username": probe_username, "password": "Probe@2026Cache"})
                probe_user = await me(probe_token)
                users = [target_user, probe_user]
                await asyncio.gather(analyze(target_token), analyze(probe_token))
                version_keys = [f"wm:permission-context-version:{user['id']}" for user in users]
                before = [str(await redis.get(key) or "0") for key in version_keys]
                update = await client.put(
                    f"/admin/roles/{role['id']}/permissions",
                    headers={"Authorization": f"Bearer {super_token}"},
                    json={"permissions": role["permissions"]},
                )
                update.raise_for_status()
                after = [str(await redis.get(key) or "0") for key in version_keys]
                assert len(set(user["id"] for user in users)) == 2, users
                assert all(left != right for left, right in zip(before, after)), {"before": before, "after": after, "role": role_code}
                print(json.dumps({"role": role_code, "affected_users": [user["id"] for user in users], "versions_before": before, "versions_after": after, "pass": True}, ensure_ascii=False))
            finally:
                async with app.state.database.session_factory() as session:
                    probe = await session.get(User, probe_id)
                    if probe is not None:
                        await session.delete(probe)
                        await session.commit()
                await redis.delete(f"wm:permission-context-version:{probe_id}")


if __name__ == "__main__":
    asyncio.run(main())
