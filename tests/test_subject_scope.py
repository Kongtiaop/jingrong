from __future__ import annotations

import asyncio

from app.services.data_analysis_agent import DataAnalysisAgent


def test_authorized_subject_scope_allows_assigned_and_rejects_unassigned_subject():
    class Template:
        metadata_json = {"parameters": {"user_id": {"type": "subject_id", "source": "authorized_scope_only"}}}

    class User:
        id = "advisor-1"

    params, reason = DataAnalysisAgent.bind_template_parameters("SELECT 1", "查询 customer-a 的资产", template=Template(), user=User(), authorized_subject_ids={"customer-a", "customer-b"})
    assert params and params["user_id"] == "customer-a" and reason == ""
    params, reason = DataAnalysisAgent.bind_template_parameters("SELECT 1", "查询 customer-x 的资产", template=Template(), user=User(), authorized_subject_ids={"customer-a", "customer-b"})
    assert params is None and reason == "unauthorized_subject:user_id"


def test_multiple_authorized_subjects_require_explicit_subject():
    class Template:
        metadata_json = {"parameters": {"user_id": {"type": "subject_id", "source": "authorized_scope_only"}}}

    class User:
        id = "advisor-1"

    params, reason = DataAnalysisAgent.bind_template_parameters("SELECT 1", "查询资产", template=Template(), user=User(), authorized_subject_ids={"customer-a", "customer-b"})
    assert params is None and reason == "missing_authorized_subject:user_id"
