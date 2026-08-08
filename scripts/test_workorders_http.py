from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app, lifespan
from app.core.settings import get_settings
from app.db.session import Database
from app.models.audit import AuditLog
from app.models.workorder import WorkOrder
from app.models.workorder_attachment import WorkOrderAttachment
from app.models.notification import Notification
from app.models.workorder_update import WorkOrderUpdate

BASE = "http://127.0.0.1:8010/api/v1"


async def login(client: httpx.AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    body = response.json()["data"]
    return {"Authorization": f"Bearer {body['access_token']}"}


async def me(client: httpx.AsyncClient, headers: dict[str, str]) -> dict:
    response = await client.get("/auth/me", headers=headers)
    response.raise_for_status()
    return response.json()["data"]


def ids(items: list[dict]) -> set[str]:
    return {item["id"] for item in items}


async def main() -> None:
    checks: dict[str, bool] = {}
    db = Database(get_settings())
    customer_order_id = ""
    staff_order_id = ""
    service_id = ""
    manager_id = ""
    advisor_id = ""
    try:
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=30) as client:
                customer = await login(client, "customer_demo", "Demo@2026Customer")
                service = await login(client, "customer_service_demo", "Demo@2026CustomerService")
                manager = await login(client, "customer_manager_demo", "Demo@2026CustomerManager")
                auditor = await login(client, "auditor_demo", "Demo@2026Auditor")
                advisor = await login(client, "advisor_demo", "Demo@2026Advisor")
                admin = await login(client, "super_admin_demo", "Demo@2026SuperAdmin")
                customer_me = await me(client, customer)
                service_me = await me(client, service)
                manager_me = await me(client, manager)
                advisor_me = await me(client, advisor)
                service_id = service_me["id"]
                manager_id = manager_me["id"]
                advisor_id = advisor_me["id"]

                created = await client.post(
                    "/workorders",
                    headers=customer,
                    json={"category": "complaint", "priority": "normal", "title": "客户投诉测试", "description": "customer scope"},
                )
                checks["customer_create"] = created.status_code == 201
                customer_order_id = created.json()["data"]["id"]
                checks["customer_list_own"] = (await client.get("/workorders", headers=customer)).status_code == 200
                checks["customer_get_own"] = (await client.get(f"/workorders/{customer_order_id}", headers=customer)).status_code == 200
                checks["customer_cannot_handle"] = (
                    await client.post(
                        f"/workorders/{customer_order_id}/handle",
                        headers=customer,
                        json={"status": "in_progress", "note": "no"},
                    )
                ).status_code == 403

                assignees_response = await client.get("/workorders/assignees", headers=admin)
                assignees = assignees_response.json()["data"] if assignees_response.status_code == 200 else []
                assignee_ids = ids(assignees)
                checks["admin_assignees"] = assignees_response.status_code == 200
                checks["assignee_list_includes_handle_staff"] = service_id in assignee_ids and manager_id in assignee_ids
                checks["assignee_list_excludes_non_handler"] = advisor_id not in assignee_ids
                checks["customer_cannot_list_assignees"] = (await client.get("/workorders/assignees", headers=customer)).status_code == 403

                checks["cannot_assign_non_handler"] = (
                    await client.post(
                        f"/workorders/{customer_order_id}/assign",
                        headers=admin,
                        json={"assigned_to": advisor_id},
                    )
                ).status_code == 404
                admin_assign = await client.post(
                    f"/workorders/{customer_order_id}/assign",
                    headers=admin,
                    json={"assigned_to": service_id},
                )
                admin_assign_data = admin_assign.json().get("data", {})
                checks["admin_assign"] = admin_assign.status_code == 200
                assignee_notifications = await client.get("/notifications?unread_only=true&limit=100", headers=service)
                assignee_notification_items = assignee_notifications.json().get("data", {}).get("items", []) if assignee_notifications.status_code == 200 else []
                assignment_notification = next((item for item in assignee_notification_items if item.get("resource_id") == customer_order_id and item.get("notification_type") == "workorder_assigned"), None)
                checks["assignee_receives_assignment_notification"] = assignee_notifications.status_code == 200 and assignment_notification is not None
                if assignment_notification:
                    mark_read = await client.post(f"/notifications/{assignment_notification['id']}/read", headers=service)
                    checks["assignee_can_mark_own_notification_read"] = mark_read.status_code == 200 and mark_read.json()["data"]["unread_count"] >= 0
                    checks["other_user_cannot_mark_notification_read"] = (await client.post(f"/notifications/{assignment_notification['id']}/read", headers=manager)).status_code == 403
                else:
                    checks["assignee_can_mark_own_notification_read"] = False
                    checks["other_user_cannot_mark_notification_read"] = False
                checks["admin_assign_response_persisted"] = (
                    admin_assign.status_code == 200
                    and admin_assign_data.get("assigned_to") == service_id
                    and admin_assign_data.get("status") == "assigned"
                )
                assigned_scope = await client.get("/workorders?scope=assigned&limit=100", headers=service)
                assigned_items = assigned_scope.json().get("data", {}).get("items", []) if assigned_scope.status_code == 200 else []
                checks["assignee_can_see_assigned_work"] = assigned_scope.status_code == 200 and customer_order_id in ids(assigned_items)
                assigned_summary = await client.get("/workorders/summary?scope=assigned", headers=service)
                checks["assigned_work_summary"] = assigned_summary.status_code == 200 and assigned_summary.json()["data"]["total"] >= 1
                urgent_filtered = await client.get("/workorders?scope=assigned&priority=urgent&limit=100", headers=service)
                checks["priority_filter_contract"] = urgent_filtered.status_code == 200 and all(item["priority"] == "urgent" for item in urgent_filtered.json()["data"]["items"])
                invalid_priority = await client.get("/workorders?priority=invalid", headers=service)
                checks["invalid_priority_rejected"] = invalid_priority.status_code == 422
                manager_scope = await client.get("/workorders?scope=assigned&limit=100", headers=manager)
                manager_items = manager_scope.json().get("data", {}).get("items", []) if manager_scope.status_code == 200 else []
                checks["non_assignee_assigned_scope_excludes_work"] = manager_scope.status_code == 200 and customer_order_id not in ids(manager_items)
                checks["non_assignee_cannot_handle"] = (
                    await client.post(
                        f"/workorders/{customer_order_id}/handle",
                        headers=manager,
                        json={"status": "in_progress", "note": "not assigned"},
                    )
                ).status_code == 403

                staff_created = await client.post(
                    "/workorders",
                    headers=manager,
                    json={
                        "customer_id": customer_me["id"],
                        "category": "business_exception",
                        "priority": "high",
                        "title": "业务异常测试",
                        "description": "staff scope",
                    },
                )
                checks["manager_create"] = staff_created.status_code == 201
                staff_order_id = staff_created.json()["data"]["id"]
                manager_assign = await client.post(
                    f"/workorders/{staff_order_id}/assign",
                    headers=manager,
                    json={"assigned_to": service_id},
                )
                manager_assign_data = manager_assign.json().get("data", {})
                checks["manager_assign"] = manager_assign.status_code == 200
                checks["manager_assign_response_persisted"] = (
                    manager_assign.status_code == 200
                    and manager_assign_data.get("assigned_to") == service_id
                    and manager_assign_data.get("status") == "assigned"
                )
                checks["advisor_cannot_handle"] = (
                    await client.post(
                        f"/workorders/{staff_order_id}/handle",
                        headers=advisor,
                        json={"status": "in_progress", "note": "no workorder permission"},
                    )
                ).status_code == 403
                checks["assigned_service_can_start"] = (
                    await client.post(
                        f"/workorders/{staff_order_id}/handle",
                        headers=service,
                        json={"status": "in_progress", "note": "客服接单"},
                    )
                ).status_code == 200
                checks["pending_customer_requires_feedback"] = (
                    await client.post(
                        f"/workorders/{staff_order_id}/handle",
                        headers=service,
                        json={"status": "pending_customer", "note": "   "},
                    )
                ).status_code == 422
                checks["pending_customer_with_feedback"] = (
                    await client.post(
                        f"/workorders/{staff_order_id}/handle",
                        headers=service,
                        json={"status": "pending_customer", "note": "请补充交易时间和凭证"},
                    )
                ).status_code == 200
                customer_updates_before = await client.get(f"/workorders/{staff_order_id}/updates", headers=customer)
                customer_update_items_before = customer_updates_before.json().get("data", []) if customer_updates_before.status_code == 200 else []
                checks["customer_can_see_staff_follow_up_request"] = customer_updates_before.status_code == 200 and any(item.get("author_role") == "staff" and item.get("content") == "请补充交易时间和凭证" for item in customer_update_items_before)
                checks["customer_can_submit_supplement"] = (
                    await client.post(
                        f"/workorders/{staff_order_id}/supplements",
                        headers=customer,
                        json={"content": "交易发生在 2026-08-04 10:00，已补充凭证编号 ABC-001。"},
                    )
                ).status_code == 201
                refreshed_after_supplement = await client.get(f"/workorders/{staff_order_id}", headers=service)
                checks["customer_supplement_returns_to_processing"] = refreshed_after_supplement.status_code == 200 and refreshed_after_supplement.json()["data"]["status"] == "in_progress"
                attachment_order = await client.post(f"/workorders/{staff_order_id}/handle", headers=service, json={"status":"pending_customer","note":"请上传凭证"})
                attachment_upload = await client.post(f"/workorders/{staff_order_id}/attachments", headers=customer, files={"file": ("proof.txt", b"not allowed", "text/plain")})
                checks["attachment_type_rejected"] = attachment_upload.status_code == 415
                attachment_upload = await client.post(f"/workorders/{staff_order_id}/attachments", headers=customer, files={"file": ("proof.pdf", b"%PDF-1.4 test", "application/pdf")})
                attachment_data = attachment_upload.json().get("data", {}) if attachment_upload.status_code == 201 else {}
                checks["customer_can_upload_attachment"] = attachment_upload.status_code == 201 and bool(attachment_data.get("id"))
                listed_attachments = await client.get(f"/workorders/{staff_order_id}/attachments", headers=service)
                checks["staff_can_list_attachment"] = listed_attachments.status_code == 200 and any(item.get("id") == attachment_data.get("id") for item in listed_attachments.json().get("data", []))
                downloaded_attachment = await client.get(f"/workorders/{staff_order_id}/attachments/{attachment_data.get('id')}/download", headers=service) if attachment_data.get("id") else None
                checks["staff_can_download_attachment"] = downloaded_attachment is not None and downloaded_attachment.status_code == 200 and downloaded_attachment.content.startswith(b"%PDF")
                checks["other_customer_cannot_download_attachment"] = (await client.get(f"/workorders/{staff_order_id}/attachments/{attachment_data.get('id')}/download", headers=advisor)).status_code in {403, 404} if attachment_data.get("id") else False
                staff_updates_after = await client.get(f"/workorders/{staff_order_id}/updates", headers=service)
                staff_update_items_after = staff_updates_after.json().get("data", []) if staff_updates_after.status_code == 200 else []
                checks["assigned_staff_can_see_customer_supplement"] = staff_updates_after.status_code == 200 and any(item.get("author_role") == "customer" and "ABC-001" in item.get("content", "") for item in staff_update_items_after)
                staff_notifications_after_supplement = await client.get("/notifications?unread_only=true&limit=100", headers=service)
                staff_notification_items_after_supplement = staff_notifications_after_supplement.json().get("data", {}).get("items", []) if staff_notifications_after_supplement.status_code == 200 else []
                checks["assigned_staff_receives_customer_supplement_notification"] = staff_notifications_after_supplement.status_code == 200 and any(item.get("resource_id") == staff_order_id and item.get("notification_type") == "workorder_customer_supplemented" for item in staff_notification_items_after_supplement)
                checks["staff_cannot_submit_customer_supplement"] = (
                    await client.post(
                        f"/workorders/{staff_order_id}/supplements",
                        headers=service,
                        json={"content": "越权补充"},
                    )
                ).status_code == 403
                checks["resolve_requires_feedback"] = (
                    await client.post(
                        f"/workorders/{staff_order_id}/handle",
                        headers=service,
                        json={"status": "resolved", "note": "   "},
                    )
                ).status_code == 422
                checks["assigned_service_can_resolve_with_feedback"] = (
                    await client.post(
                        f"/workorders/{staff_order_id}/handle",
                        headers=service,
                        json={"status": "resolved", "note": "已处理并反馈客户"},
                    )
                ).status_code == 200
                customer_notifications = await client.get("/notifications?unread_only=true&limit=100", headers=customer)
                customer_notification_items = customer_notifications.json().get("data", {}).get("items", []) if customer_notifications.status_code == 200 else []
                checks["customer_receives_resolution_notification"] = customer_notifications.status_code == 200 and any(item.get("resource_id") == staff_order_id and item.get("notification_type") == "workorder_resolved" for item in customer_notification_items)
                checks["resolved_cannot_reopen"] = (
                    await client.post(
                        f"/workorders/{staff_order_id}/handle",
                        headers=service,
                        json={"status": "in_progress", "note": "不能重新打开"},
                    )
                ).status_code == 409
                checks["assigned_service_can_close"] = (await client.post(f"/workorders/{staff_order_id}/close", headers=service)).status_code == 200
                customer_after_close = await client.get("/notifications?unread_only=true&limit=100", headers=customer)
                customer_after_close_items = customer_after_close.json().get("data", {}).get("items", []) if customer_after_close.status_code == 200 else []
                checks["customer_receives_close_notification"] = customer_after_close.status_code == 200 and any(item.get("resource_id") == staff_order_id and item.get("notification_type") == "workorder_closed" for item in customer_after_close_items)
                checks["auditor_read"] = (await client.get("/workorders?limit=20", headers=auditor)).status_code == 200
                checks["auditor_cannot_assign"] = (
                    await client.post(
                        f"/workorders/{customer_order_id}/assign",
                        headers=auditor,
                        json={"assigned_to": service_id},
                    )
                ).status_code == 403
                checks["auditor_cannot_handle"] = (
                    await client.post(
                        f"/workorders/{customer_order_id}/handle",
                        headers=auditor,
                        json={"status": "in_progress", "note": "no"},
                    )
                ).status_code == 403
                checks["unknown_not_found"] = (await client.get(f"/workorders/{uuid.uuid4()}", headers=manager)).status_code == 404

                # Super admin is the only exception to the assigned-to restriction.
                checks["super_admin_can_handle_other_assignee"] = (
                    await client.post(
                        f"/workorders/{customer_order_id}/handle",
                        headers=admin,
                        json={"status": "resolved", "note": "管理员代处理"},
                    )
                ).status_code == 200
                checks["super_admin_can_close_other_assignee"] = (await client.post(f"/workorders/{customer_order_id}/close", headers=admin)).status_code == 200

        async with db.session_factory() as session:
            tracked_ids = [customer_order_id, staff_order_id]
            audit_count = int(
                (
                    await session.execute(
                        select(func.count()).select_from(AuditLog).where(
                            AuditLog.resource_type == "work_order",
                            AuditLog.resource_id.in_(tracked_ids),
                        )
                    )
                ).scalar_one()
            )
            customer_row = await session.get(WorkOrder, customer_order_id)
            staff_row = await session.get(WorkOrder, staff_order_id)
            assign_audits = list(
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.resource_type == "work_order",
                            AuditLog.resource_id.in_(tracked_ids),
                            AuditLog.action == "assign",
                        )
                    )
                ).scalars().all()
            )
            checks["audit_events"] = audit_count >= 8
            checks["database_assignment_persisted"] = (
                customer_row is not None
                and staff_row is not None
                and customer_row.assigned_to == service_id
                and staff_row.assigned_to == service_id
            )
            checks["assignment_audit_persisted"] = len(assign_audits) >= 2
            notification_count = int((await session.execute(select(func.count()).select_from(Notification).where(Notification.resource_type == "work_order", Notification.resource_id.in_(tracked_ids)))).scalar_one())
            update_count = int((await session.execute(select(func.count()).select_from(WorkOrderUpdate).where(WorkOrderUpdate.work_order_id == staff_order_id))).scalar_one())
            checks["resolution_feedback_persisted"] = staff_row is not None and staff_row.resolution_note == "已处理并反馈客户"
            checks["notifications_persisted"] = notification_count >= 5
            checks["supplement_updates_persisted"] = update_count >= 2
            attachment_count = int((await session.execute(select(func.count()).select_from(WorkOrderAttachment).where(WorkOrderAttachment.work_order_id == staff_order_id))).scalar_one())
            checks["attachments_persisted"] = attachment_count >= 1

        result = {"checks": checks, "pass": all(checks.values())}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["pass"] else 1)
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())








