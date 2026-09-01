from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app, lifespan

BASE = "http://test/api/v1"
CUSTOMER = {"username": "customer_demo", "password": "Demo@2026Customer"}
ADVISOR = {"username": "advisor_demo", "password": "Demo@2026Advisor"}
RISK = {"username": "risk_manager_demo", "password": "Demo@2026RiskManager"}
SUPER_ADMIN = {"username": "super_admin_demo", "password": "Demo@2026SuperAdmin"}


async def main() -> None:
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
            async def headers_for(account: dict[str, str]):
                login = await client.post("/auth/login", json=account)
                login.raise_for_status()
                return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

            async def execute(account: dict[str, str], content: str, page_context: str):
                response = await client.post("/customer-service/sessions/agent/execute", headers=await headers_for(account), json={"content": content, "conversation_id": f"router-{uuid.uuid4()}", "page_context": page_context})
                response.raise_for_status()
                return response.json()["data"]

            customer_advice = await execute(CUSTOMER, "推荐适合我的产品", "/products")
            advisor_advice = await execute(ADVISOR, "推荐适合我的产品", "/backoffice")
            risk = await execute(RISK, "我的账户是否有风险限制", "/backoffice")
            super_admin_operation = await execute(SUPER_ADMIN, "我要赎回产品", "/backoffice")

            assert customer_advice["available_agents"] == ["CustomerServiceAgent"], customer_advice
            assert customer_advice["agents"] == ["CustomerServiceAgent"], customer_advice
            assert "AdvisorAgent" in customer_advice["denied_agents"], customer_advice
            assert advisor_advice["available_agents"] == ["DataAnalysisAgent", "AdvisorAgent"], advisor_advice
            assert advisor_advice["route"] == "advisor_risk" and advisor_advice["agents"] == ["AdvisorAgent"], advisor_advice
            assert risk["available_agents"] == ["DataAnalysisAgent", "RiskAgent"], risk
            assert risk["route"] == "risk" and risk["agents"] == ["RiskAgent"], risk
            assert set(super_admin_operation["available_agents"]) == {"CustomerServiceAgent", "DataAnalysisAgent", "AdvisorAgent", "RiskAgent", "BusinessAgent", "AdminAgent"}, super_admin_operation
            assert super_admin_operation["route"] == "operation_risk" and super_admin_operation["agents"] == ["BusinessAgent", "RiskAgent"], super_admin_operation
            assert all(item["overall_status"] != "executed" for item in [customer_advice, advisor_advice, risk, super_admin_operation])
            print({"customer_pre_filter": True, "advisor_pre_filter": True, "risk_pre_filter": True, "super_admin_all_agents": True, "no_direct_execution": True, "pass": True})


if __name__ == "__main__":
    asyncio.run(main())
