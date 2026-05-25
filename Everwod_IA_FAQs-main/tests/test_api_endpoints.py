from datetime import datetime

from fastapi.testclient import TestClient

import app.api.validation_service as svc
import app.api.suggestion_service as suggestion_svc
from app.core.models import SuggestionResponse, SuggestionSummary, ValidationResponse


def test_health_endpoint():
    client = TestClient(svc.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_get_suggestions_endpoint(monkeypatch):
    summary = SuggestionSummary(
        company_count=1,
        cluster_count=1,
        total_examples=3,
        average_cluster_size=3,
        silhouette_score=None,
        suggestions=[
            SuggestionResponse(
                id="11111111-1111-1111-1111-111111111111",
                company_id="74",
                company_name="Empire Box",
                workspace_id=74,
                question="Quiero reservar una clase",
                answer="La reserva se realiza por el canal oficial.",
                cluster_size=3,
                support_examples=["Quiero reservar una clase"],
                cluster_score=75.0,
            )
        ],
    )
    captured_kwargs = {}

    def fake_list_suggestions_from_db(**kwargs):
        captured_kwargs.update(kwargs)
        return summary

    monkeypatch.setattr(svc, "list_suggestions_from_db", fake_list_suggestions_from_db)
    client = TestClient(svc.app)

    response = client.get("/suggestions")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "11111111-1111-1111-1111-111111111111"
    assert captured_kwargs["include_all"] is False


def test_post_validate_endpoint(monkeypatch):
    response_model = ValidationResponse(
        suggestion_id="11111111-1111-1111-1111-111111111111",
        reviewer="Ana",
        status="approved",
        previous_status="pending",
        notes=None,
        reviewed_at=datetime(2026, 5, 14, 12, 0, 0),
        promoted_faq_id="11111111-1111-1111-1111-111111111111",
    )
    monkeypatch.setattr(svc, "apply_validation", lambda request: response_model)
    client = TestClient(svc.app)

    response = client.post(
        "/validate",
        json={
            "suggestion_id": "11111111-1111-1111-1111-111111111111",
            "reviewer": "Ana",
            "status": "approved",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["promoted_faq_id"] == "11111111-1111-1111-1111-111111111111"


def test_suggest_endpoint_uses_dynamic_since_days_and_workspace(monkeypatch):
    captured = {}

    def fake_fetch_conversation_records(**kwargs):
        captured.update(kwargs)
        return [], {
            "raw_messages_found": 0,
            "raw_messages_processed": 0,
            "conversations_found": 0,
            "conversations_processed": 0,
        }

    stats = {
        "company_count": 0,
        "conversations_analyzed": 0,
        "total_examples": 0,
        "clusters_valid": 0,
        "clusters_rejected": 0,
        "llm_rejected": 0,
        "validator_rejected": 0,
        "duplicates_omitted": 0,
        "silhouette_score": None,
        "average_cluster_size": 0.0,
    }

    def fake_run_suggestion_pipeline(request):
        fake_fetch_conversation_records(
            limit=request.limit,
            since_days=request.since_days,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
        )
        metric_payload = {
            key: value
            for key, value in stats.items()
            if key not in {"company_count", "total_examples", "silhouette_score", "average_cluster_size"}
        }
        return SuggestionSummary(
            company_count=0,
            cluster_count=0,
            total_examples=0,
            average_cluster_size=0,
            silhouette_score=None,
            suggestions=[],
            run_id="run-1",
            **metric_payload,
        )

    monkeypatch.setattr(suggestion_svc, "run_suggestion_pipeline", fake_run_suggestion_pipeline)

    client = TestClient(suggestion_svc.app)
    response = client.post("/suggest", json={"limit": 15000, "since_days": 30, "workspace_id": 74})

    assert response.status_code == 200
    assert captured["since_days"] == 30
    assert captured["workspace_id"] == 74

    response = client.post("/suggest", json={"limit": 15000, "since_days": 180, "workspace_id": 74})

    assert response.status_code == 200
    assert captured["since_days"] == 180
    assert captured["workspace_id"] == 74

    response = client.post("/suggest", json={"since_days": 365, "workspace_id": 74})

    assert response.status_code == 200
    assert captured["limit"] is None
    assert captured["since_days"] == 365
    assert captured["workspace_id"] == 74


def test_suggestions_endpoint_filters_by_selected_workspace(monkeypatch):
    summary = SuggestionSummary(
        company_count=1,
        cluster_count=0,
        total_examples=0,
        average_cluster_size=0,
        silhouette_score=None,
        suggestions=[],
    )
    captured_kwargs = {}

    def fake_list_suggestions_from_db(**kwargs):
        captured_kwargs.update(kwargs)
        return summary

    monkeypatch.setattr(suggestion_svc, "list_suggestions_from_db", fake_list_suggestions_from_db)
    client = TestClient(suggestion_svc.app)

    response = client.get("/suggestions?workspace_id=74")

    assert response.status_code == 200
    assert captured_kwargs["workspace_id"] == 74
    assert captured_kwargs["include_all"] is False
