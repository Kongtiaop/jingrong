from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app, lifespan
from app.models.data_catalog import DataCatalogDomain, DataCatalogExampleQuery

BASE = "http://test/api/v1"
CUSTOMER = {"username": "customer_demo", "password": "Demo@2026Customer"}
RISK = {"username": "risk_manager_demo", "password": "Demo@2026RiskManager"}
OPERATIONS = {"username": "operations_demo", "password": "Demo@2026Operations"}
SUPER = {"username": "super_admin_demo", "password": "Demo@2026SuperAdmin"}


async def main() -> None:
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
            probe_template = None
            async with app.state.database.session_factory() as session:
                domain = (await session.execute(select(DataCatalogDomain).where(DataCatalogDomain.code == "risk_management"))).scalar_one_or_none()
                if domain is None:
                    domain = (await session.execute(select(DataCatalogDomain).limit(1))).scalar_one()
                probe_template = DataCatalogExampleQuery(
                    id=str(uuid.uuid4()), domain_id=domain.id,
                    question="查询受控参数澄清测试风险预警",
                    normalized_question="查询受控参数澄清测试风险预警",
                    intent_code=f"clarification_probe_{uuid.uuid4().hex[:10]}",
                    target_table_names_json=["risk_alert"],
                    target_column_names_json=["risk_alert.status"],
                    sql_template="SELECT status FROM risk_alert WHERE status = 'open' LIMIT :limit",
                    explanation="parameter clarification probe",
                    allowed_roles_json=["risk_manager"], is_approved=True, is_active=True,
                    metadata_json={"parameters": {
                        "limit": {"type": "integer", "source": "natural_language", "min": 1, "max": 100, "default": 100},
                        "date_range": {"type": "date_range", "source": "natural_language", "max_range_days": 90},
                    }},
                )
                session.add(probe_template)
                await session.commit()
            async def execute(account: dict[str, str], query: str):
                login = await client.post("/auth/login", json=account)
                login.raise_for_status()
                response = await client.post("/customer-service/sessions/agent/execute", headers={"Authorization": f"Bearer {login.json()['data']['access_token']}"}, json={"content": query, "conversation_id": f"analysis-{uuid.uuid4()}", "page_context": "/backoffice"})
                response.raise_for_status()
                return response.json()["data"]

            risk_alerts = await execute(RISK, "现在有多少风险预警")
            pending_orders = await execute(OPERATIONS, "待审核订单有多少")
            excessive_limit = await execute(RISK, f"{probe_template.question} 前10000条")
            excessive_date_range = await execute(RISK, f"{probe_template.question} 过去365天")
            customer = await execute(CUSTOMER, "现在有多少风险预警")

            admin_login = await client.post("/auth/login", json=SUPER)
            admin_login.raise_for_status()
            audit_response = await client.get(
                "/admin/audit-logs",
                params={"action_prefix": "data_analysis.", "resource_type": "data_catalog_example_query", "limit": 100},
                headers={"Authorization": f"Bearer {admin_login.json()['data']['access_token']}"},
            )
            audit_response.raise_for_status()
            audits = audit_response.json()["data"]["items"]

            assert risk_alerts["route"] == "data_analysis" and risk_alerts["agents"] == ["DataAnalysisAgent"], risk_alerts
            assert risk_alerts["results"][0]["data"]["query_plan"]["free_sql"] is False, risk_alerts
            assert pending_orders["route"] == "data_analysis" and pending_orders["results"][0]["data"]["metric_code"] == "pending_order_count", pending_orders
            assert excessive_limit["overall_status"] == "needs_clarification" and excessive_limit["route"] == "data_analysis", excessive_limit
            assert "100" in excessive_limit["clarification"] and excessive_limit["results"][0]["error"] == "parameter_out_of_range:limit", excessive_limit
            assert excessive_date_range["overall_status"] == "needs_clarification" and excessive_date_range["route"] == "data_analysis", excessive_date_range
            assert "90 天" in excessive_date_range["clarification"] and excessive_date_range["results"][0]["error"] == "parameter_out_of_range:date_range", excessive_date_range
            assert customer["agents"] == ["CustomerServiceAgent"], customer
            assert "DataAnalysisAgent" in customer["denied_agents"], customer
            analysis_audits = [item for item in audits if item["action"] in {"data_analysis.template_executed", "data_analysis.template_rejected"}]
            assert analysis_audits, audits
            assert all("query_length" in item["detail"] and "free_sql" in item["detail"] for item in analysis_audits), analysis_audits
            async with app.state.database.session_factory() as session:
                await session.delete(probe_template)
                await session.commit()
            print({"risk_metric": True, "operations_metric": True, "parameter_limit_clarification": True, "parameter_date_range_clarification": True, "free_sql_blocked": True, "customer_denied": True, "analysis_audited": True, "pass": True})


if __name__ == "__main__":
    asyncio.run(main())
