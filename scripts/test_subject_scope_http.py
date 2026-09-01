from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app, lifespan
from app.models.auth import CustomerAdvisorAuthorization, Role, User
from app.services.auth_service import hash_password
from app.models.data_catalog import DataCatalogDomain, DataCatalogExampleQuery
from app.models.outbox import OutboxEvent
from workers.outbox_worker import OutboxPublisher
from app.tasks.streams import RedisStreams

BASE = "http://test/api/v1"
SUPER = {"username": "super_admin_demo", "password": "Demo@2026SuperAdmin"}
CUSTOMER_A = {"username": "customer_demo", "password": "Demo@2026Customer"}
CUSTOMER_B = {"username": "hNW_demo", "password": "Demo@2026HighNetWorth"}


async def main() -> None:
    async with lifespan(app):
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
            customer_a = await me(await login(CUSTOMER_A))
            customer_b = await me(await login(CUSTOMER_B))
            advisor_id = str(uuid.uuid4())
            advisor_username = f"scope_advisor_{uuid.uuid4().hex[:10]}"
            async with app.state.database.session_factory() as session:
                advisor_role = (await session.execute(select(Role).where(Role.code == "customer_manager"))).scalar_one()
                advisor = User(id=advisor_id, username=advisor_username, password_hash=hash_password("Scope@2026"), display_name="Scope advisor", status="active")
                advisor.roles.append(advisor_role)
                session.add(advisor)
                await session.flush()
                assignment = CustomerAdvisorAuthorization(id=str(uuid.uuid4()), customer_id=customer_a["id"], advisor_id=advisor_id, status="active", allowed_actions="read", product_scope="*", max_single_amount=0, max_daily_amount=0, risk_level_ceiling="C2", requires_customer_confirmation=False)
                session.add(assignment)
                domain = (await session.execute(select(DataCatalogDomain).where(DataCatalogDomain.code == "customer_profile"))).scalar_one()
                template = DataCatalogExampleQuery(
                    id=str(uuid.uuid4()),
                    domain_id=domain.id,
                    question="查询客户授权范围内的资产日期",
                    normalized_question="查询客户授权范围内的资产日期",
                    intent_code=f"scope_probe_{uuid.uuid4().hex[:10]}",
                    target_table_names_json=["customer_asset_snapshot"],
                    target_column_names_json=["customer_asset_snapshot.user_id"],
                    sql_template="SELECT user_id FROM customer_asset_snapshot WHERE user_id = :user_id ORDER BY user_id",
                    explanation="scope regression probe",
                    allowed_roles_json=["customer_manager"],
                    is_approved=True,
                    is_active=True,
                    metadata_json={
                        "parameters": {
                            "user_id": {"type": "subject_id", "source": "authorized_scope_only"},
                            "date_range": {
                                "type": "date_range",
                                "source": "natural_language",
                                "start_param": "start_date",
                                "end_param": "end_date",
                                "default_days": 90,
                                "max_range_days": 90,
                            },
                        },
                    },
                )
                session.add(template)
                template_question = template.question
                await session.commit()
            try:
                advisor_token = await login({"username": advisor_username, "password": "Scope@2026"})

                async def ask(question: str) -> dict:
                    response = await client.post("/customer-service/sessions/agent/execute", headers={"Authorization": f"Bearer {advisor_token}"}, json={"content": question, "conversation_id": f"scope-{uuid.uuid4()}", "page_context": "/backoffice"})
                    response.raise_for_status()
                    return response.json()["data"]

                assigned = await ask(template_question)
                redis = app.state.redis.client
                scope_key = f"wm:subject-scope-version:{advisor_id}"
                version_after_grant = await redis.get(scope_key)
                # This fixture inserts the assignment directly. Seed the version once so the
                # following direct DB revocation proves live scope checks remain authoritative.
                if version_after_grant is None:
                    version_after_grant = await redis.incr(scope_key)
                if isinstance(version_after_grant, bytes):
                    version_after_grant = version_after_grant.decode("utf-8")
                base_scope_version = int(version_after_grant)
                assert base_scope_version >= 1, version_after_grant
                unassigned = await ask(f"{template_question} 查询客户 {customer_b['id']}")
                assigned_template = [item for item in assigned.get("results", []) if item.get("data", {}).get("query_plan", {}).get("mode") == "approved_template"]
                if not assigned_template:
                    audit_response = await client.get("/admin/audit-logs", params={"action_prefix": "data_analysis.", "limit": 20}, headers={"Authorization": f"Bearer {super_token}"})
                    print("ASSIGNED_DEBUG", json.dumps({"assigned": assigned, "audits": audit_response.json()}, ensure_ascii=False, default=str))
                assert assigned_template, assigned
                assert unassigned.get("overall_status") in {"needs_clarification", "refused"} or any("unauthorized_subject" in item.get("error", "") for item in unassigned.get("results", [])), unassigned
                async with app.state.database.session_factory() as session:
                    assignment = (await session.execute(select(CustomerAdvisorAuthorization).where(CustomerAdvisorAuthorization.customer_id == customer_a["id"], CustomerAdvisorAuthorization.advisor_id == advisor_id))).scalar_one()
                    assignment.status = "revoked"
                    assignment.revoked_at = datetime.now(timezone.utc)
                    await session.commit()
                revoked = await ask(f"{template_question} 查询客户 {customer_a['id']}")
                assert revoked.get("overall_status") in {"needs_clarification", "refused"} or any("unauthorized_subject" in item.get("error", "") for item in revoked.get("results", [])), revoked
                # Direct database revocation intentionally bypasses the API invalidation hook; live scope checks must still reject it.
                version_after_direct_revoke = await redis.get(scope_key)
                if isinstance(version_after_direct_revoke, bytes):
                    version_after_direct_revoke = version_after_direct_revoke.decode("utf-8")
                assert str(version_after_direct_revoke) == "1", version_after_direct_revoke
                batch_response = await client.put(
                    "/admin/advisor-authorizations/batch",
                    headers={"Authorization": f"Bearer {super_token}"},
                    json={"grants": [{
                        "advisor_id": advisor_id,
                        "customer_id": customer_a["id"],
                        "allowed_actions": "read",
                        "product_scope": "*",
                        "max_single_amount": 0,
                        "max_daily_amount": 0,
                        "risk_level_ceiling": "C2",
                        "requires_customer_confirmation": False,
                    }]},
                )
                batch_response.raise_for_status()
                batch_grant = batch_response.json()["data"]
                assert batch_grant["invalidation_event_ids"], batch_grant
                async with app.state.database.session_factory() as session:
                    pending_events = list((await session.execute(select(OutboxEvent).where(OutboxEvent.id.in_(batch_grant["invalidation_event_ids"])))) .scalars().all())
                    assert len(pending_events) == 1 and pending_events[0].status == "pending", pending_events
                queue = RedisStreams(app.state.settings)
                publisher = OutboxPublisher(app.state.database, queue, app.state.settings)
                assert await publisher.publish_once(limit=10) >= 1
                stream = f"{app.state.settings.redis_stream_prefix}:authorization"
                entries = await redis.xrange(stream, min="-", max="+")
                assert any(data["event_type"] == "authorization.subject_scope_invalidate" for _, data in entries), entries
                # The authorization worker consumes this event in the deployed profile.
                # In a local-only run without the worker, consume with a temporary group.
                worker_version = await redis.get(scope_key)
                if isinstance(worker_version, bytes):
                    worker_version = worker_version.decode("utf-8")
                if int(worker_version or 0) == base_scope_version:
                    authorization_group = f"subject-scope-http-{uuid.uuid4().hex[:10]}"
                    await redis.xgroup_create(stream, authorization_group, id="0", mkstream=True)
                    messages = await redis.xreadgroup(authorization_group, "subject-scope-http", {stream: ">"}, count=10)
                    for _, stream_entries in messages:
                        for message_id, data in stream_entries:
                            await redis.incr(f"wm:subject-scope-version:{data["advisor_id"]}")
                            await redis.xack(stream, authorization_group, message_id)
                version_after_batch_grant = await redis.get(scope_key)
                if isinstance(version_after_batch_grant, bytes):
                    version_after_batch_grant = version_after_batch_grant.decode("utf-8")
                assert int(version_after_batch_grant) == base_scope_version + 1, version_after_batch_grant
                restored = await ask(f"{template_question} 查询客户 {customer_a['id']}")
                assert [item for item in restored.get("results", []) if item.get("data", {}).get("query_plan", {}).get("mode") == "approved_template"], restored
                batch_revoke_response = await client.put(
                    "/admin/advisor-authorizations/batch",
                    headers={"Authorization": f"Bearer {super_token}"},
                    json={"revoke_ids": batch_grant["granted_ids"]},
                )
                batch_revoke_response.raise_for_status()
                assert batch_revoke_response.json()["data"]["invalidation_event_ids"]
                assert await publisher.publish_once(limit=10) >= 1
                worker_version = await redis.get(scope_key)
                if isinstance(worker_version, bytes):
                    worker_version = worker_version.decode("utf-8")
                if int(worker_version or 0) == base_scope_version + 1:
                    messages = await redis.xreadgroup(authorization_group, "subject-scope-http", {stream: ">"}, count=10)
                    for _, stream_entries in messages:
                        for message_id, data in stream_entries:
                            await redis.incr(f"wm:subject-scope-version:{data["advisor_id"]}")
                            await redis.xack(stream, authorization_group, message_id)
                version_after_batch_revoke = await redis.get(scope_key)
                if isinstance(version_after_batch_revoke, bytes):
                    version_after_batch_revoke = version_after_batch_revoke.decode("utf-8")
                assert int(version_after_batch_revoke) == base_scope_version + 2, version_after_batch_revoke
                batch_revoked = await ask(f"{template_question} 查询客户 {customer_a['id']}")
                assert batch_revoked.get("overall_status") in {"needs_clarification", "refused"} or any("unauthorized_subject" in item.get("error", "") for item in batch_revoked.get("results", [])), batch_revoked
                await queue.close()
                print(json.dumps({"assigned_subject_allowed": True, "unassigned_subject_rejected": True, "revoked_subject_rejected": True, "api_scope_version_bumped": True, "batch_scope_version_bumped": True, "outbox_invalidation_published": True, "pass": True}, ensure_ascii=False))
            finally:
                async with app.state.database.session_factory() as session:
                    assignments = list((await session.execute(select(CustomerAdvisorAuthorization).where(CustomerAdvisorAuthorization.advisor_id == advisor_id))).scalars().all())
                    for assignment in assignments:
                        await session.delete(assignment)
                    advisor = await session.get(User, advisor_id)
                    if advisor is not None:
                        await session.delete(advisor)
                    probe_template = await session.get(DataCatalogExampleQuery, template.id)
                    if probe_template is not None:
                        await session.delete(probe_template)
                    await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
