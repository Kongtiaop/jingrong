import ast
from pathlib import Path


def test_ingestion_status_endpoint_and_retry_contract_are_present():
    knowledge_api = Path("app/api/knowledge.py").read_text(encoding="utf-8")
    admin_api = Path("app/api/admin.py").read_text(encoding="utf-8")
    assert "/documents/{document_id}/ingestion-status" in knowledge_api
    assert "DocumentIngestionStatusResponse" in knowledge_api
    assert "row.event_type.startswith(" in admin_api
    ast.parse(knowledge_api)
    ast.parse(admin_api)
