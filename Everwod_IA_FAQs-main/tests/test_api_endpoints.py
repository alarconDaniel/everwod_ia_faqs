from datetime import datetime

from fastapi.testclient import TestClient

import validation_service as svc
import suggestion_service as suggestion_svc
from faq_models import SuggestionResponse, SuggestionSummary, ValidationResponse


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

    monkeypatch.setattr(suggestion_svc, "create_pipeline_run", lambda parameters, workspace_id=None: "run-1")
    monkeypatch.setattr(suggestion_svc, "finish_pipeline_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(suggestion_svc, "fetch_conversation_records_with_metrics", fake_fetch_conversation_records)
    monkeypatch.setattr(suggestion_svc, "load_existing_faqs_by_company", lambda: {})
    monkeypatch.setattr(suggestion_svc, "build_suggestion_candidates", lambda *args, **kwargs: ([], dict(stats)))
    monkeypatch.setattr(suggestion_svc, "persist_pipeline_results", lambda run_id, candidates, stats: [])
    monkeypatch.setattr(suggestion_svc, "save_json", lambda *args, **kwargs: None)

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
