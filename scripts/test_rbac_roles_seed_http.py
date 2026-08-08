from __future__ import annotations
import argparse
import asyncio
import json
import httpx

ACCOUNTS = {
    "customer_service": ("customer_service_demo", "Demo@2026CustomerService"),
    "customer_manager": ("customer_manager_demo", "Demo@2026CustomerManager"),
    "advisor": ("advisor_demo", "Demo@2026Advisor"),
    "product_manager": ("product_manager_demo", "Demo@2026ProductManager"),
    "operations": ("operations_demo", "Demo@2026Operations"),
    "risk_manager": ("risk_manager_demo", "Demo@2026RiskManager"),
    "auditor": ("auditor_demo", "Demo@2026Auditor"),
}


async def login(client: httpx.AsyncClient, username: str, password: str) -> tuple[dict[str, str], dict]:
    response = await client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    token = response.json()["data"]["access_token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    me.raise_for_status()
    return {"Authorization": f"Bearer {token}"}, me.json()["data"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010/api/v1")
    args = parser.parse_args()
    checks: list[dict] = []
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=20) as client:
        sessions = {}
        for name, (username, password) in ACCOUNTS.items():
            headers, me = await login(client, username, password)
            sessions[name] = headers
            expected_role = "advisor" if name == "advisor" else name
            expected_permissions = {
                "customer_service": {"customer:read_all", "customer:write"},
                "customer_manager": {"customer:read_all", "customer:write", "transfer:submit_staff"},
                "advisor": {"customer:read_assigned", "order:submit_staff"},
                "product_manager": {"product:read", "product:write"},
                "operations": {"order:review", "operation:read_staff", "operation:audit_read", "task:create", "task:read"},
                "risk_manager": {"risk:alert_read", "risk:alert_handle", "risk:alert_action", "risk:scan_execute"},
                "auditor": {"risk:alert_read", "audit:read", "operation:audit_read", "task:read"},
            }[name]
            checks.append({
                "name": f"{name}_me_role",
                "roles": me["roles"],
                "pass": expected_role in me["roles"],
            })
            checks.append({
                "name": f"{name}_me_permissions",
                "permissions": me["permissions"],
                "pass": expected_permissions.issubset(set(me["permissions"])),
            })

        checks.append({
            "name": "advisor_workbench_access",
            "status": (response := await client.get("/profile/staff/advisor-customers?limit=1", headers=sessions["advisor"])).status_code,
            "pass": response.status_code == 200,
        })
        checks.append({
            "name": "product_manager_cannot_use_advisor_workbench",
            "status": (response := await client.get("/profile/staff/advisor-customers?limit=1", headers=sessions["product_manager"])).status_code,
            "pass": response.status_code == 403,
        })
        checks.append({
            "name": "customer_service_cannot_use_advisor_workbench",
            "status": (response := await client.get("/profile/staff/advisor-customers?limit=1", headers=sessions["customer_service"])).status_code,
            "pass": response.status_code == 403,
        })
        checks.append({
            "name": "customer_manager_staff_customer_access",
            "status": (response := await client.get("/profile/staff/customers?limit=1", headers=sessions["customer_manager"])).status_code,
            "pass": response.status_code == 200,
        })
        checks.append({
            "name": "customer_service_cannot_review_staff_operations",
            "status": (response := await client.get("/operations/staff/pending?limit=1", headers=sessions["customer_service"])).status_code,
            "pass": response.status_code == 403,
        })
        checks.append({
            "name": "customer_manager_cannot_read_staff_operations",
            "status": (response := await client.get("/operations/staff/pending?limit=1", headers=sessions["customer_manager"])).status_code,
            "pass": response.status_code == 403,
        })
        staff_redeem_payload = {
            "customer_id": "not-a-customer",
            "product_id": "not-a-product",
            "quantity": "1",
            "amount": "1",
            "customer_confirmed": True,
        }
        checks.append({
            "name": "advisor_staff_redeem_reaches_business_validation",
            "status": (response := await client.post("/operations/staff/redeem", headers=sessions["advisor"], json=staff_redeem_payload)).status_code,
            "pass": response.status_code == 404,
        })
        checks.append({
            "name": "customer_manager_cannot_staff_redeem",
            "status": (response := await client.post("/operations/staff/redeem", headers=sessions["customer_manager"], json=staff_redeem_payload)).status_code,
            "pass": response.status_code == 403,
        })
        staff_transfer_payload = {
            "customer_id": "not-a-customer",
            "to_account": "not-an-account",
            "amount": "1",
            "confirmed": True,
            "customer_confirmed": True,
        }
        checks.append({
            "name": "customer_manager_staff_transfer_reaches_business_validation",
            "status": (response := await client.post("/operations/staff/transfer", headers=sessions["customer_manager"], json=staff_transfer_payload)).status_code,
            "pass": response.status_code == 404,
        })
        checks.append({
            "name": "operations_cannot_staff_transfer",
            "status": (response := await client.post("/operations/staff/transfer", headers=sessions["operations"], json=staff_transfer_payload)).status_code,
            "pass": response.status_code == 403,
        })
        review_payload = {"approve": False, "note": "permission matrix"}
        checks.append({
            "name": "operations_review_reaches_business_validation",
            "status": (response := await client.post("/operations/redeem/not-found/review", headers=sessions["operations"], json=review_payload)).status_code,
            "pass": response.status_code == 404,
        })
        checks.append({
            "name": "advisor_cannot_review_business_operation",
            "status": (response := await client.post("/operations/redeem/not-found/review", headers=sessions["advisor"], json=review_payload)).status_code,
            "pass": response.status_code == 403,
        })
        risk_scan_path = "/risk-alerts/scan?customer_id=not-a-customer&days=5"
        checks.append({
            "name": "risk_manager_can_execute_risk_scan",
            "status": (response := await client.post(risk_scan_path, headers=sessions["risk_manager"])).status_code,
            "pass": response.status_code in {200, 404},
        })
        checks.append({
            "name": "auditor_cannot_execute_risk_scan",
            "status": (response := await client.post(risk_scan_path, headers=sessions["auditor"])).status_code,
            "pass": response.status_code == 403,
        })
        checks.append({
            "name": "risk_manager_can_read_alerts",
            "status": (response := await client.get("/risk-alerts?limit=1", headers=sessions["risk_manager"])).status_code,
            "pass": response.status_code == 200,
        })
        checks.append({
            "name": "auditor_can_read_alerts",
            "status": (response := await client.get("/risk-alerts?limit=1", headers=sessions["auditor"])).status_code,
            "pass": response.status_code == 200,
        })
        checks.append({
            "name": "risk_manager_can_handle_alert",
            "status": (response := await client.post("/risk-alerts/not-found/handle", headers=sessions["risk_manager"], json={"status":"investigating","note":"matrix"})).status_code,
            "pass": response.status_code == 404,
        })
        checks.append({
            "name": "auditor_cannot_handle_alert",
            "status": (response := await client.post("/risk-alerts/not-found/handle", headers=sessions["auditor"], json={"status":"investigating","note":"matrix"})).status_code,
            "pass": response.status_code == 403,
        })
        checks.append({
            "name": "auditor_can_read_system_audit",
            "status": (response := await client.get("/admin/audit-logs?limit=1", headers=sessions["auditor"])).status_code,
            "pass": response.status_code == 200,
        })
        checks.append({
            "name": "operations_can_create_task",
            "status": (response := await client.post("/tasks", headers=sessions["operations"], json={"task_type":"permission_matrix","aggregate_type":"test","payload":{},"idempotency_key":"rbac-task-test"})).status_code,
            "pass": response.status_code == 202,
        })
        checks.append({
            "name": "auditor_cannot_create_task",
            "status": (response := await client.post("/tasks", headers=sessions["auditor"], json={"task_type":"permission_matrix","aggregate_type":"test","payload":{}})).status_code,
            "pass": response.status_code == 403,
        })
        checks.append({
            "name": "auditor_can_read_operation_audit",
            "status": (response := await client.get("/operations/audit-logs?limit=1", headers=sessions["auditor"])).status_code,
            "pass": response.status_code == 200,
        })
        checks.append({
            "name": "auditor_cannot_read_staff_pending",
            "status": (response := await client.get("/operations/staff/pending?limit=1", headers=sessions["auditor"])).status_code,
            "pass": response.status_code == 403,
        })
    passed = all(check["pass"] for check in checks)
    print(json.dumps({"checks": checks, "pass": passed}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
