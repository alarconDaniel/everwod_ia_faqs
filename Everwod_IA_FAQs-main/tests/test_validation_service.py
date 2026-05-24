from datetime import datetime

import pytest
from fastapi import HTTPException

import validation_service as svc
from faq_models import ValidationRequest


class FakeCursor:
    def __init__(self, candidate):
        self.candidate = candidate
        self.queries = []
        self.next_row = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if "FROM faq_mvp.faq_candidates" in query and "FOR UPDATE" in query:
            self.next_row = self.candidate
        elif "INSERT INTO faq_mvp.existing_faqs" in query:
            self.next_row = {"faq_id": self.candidate["candidate_id"]}
        elif "INSERT INTO faq_mvp.faq_candidate_edit_events" in query:
            self.next_row = None
        elif "UPDATE faq_mvp.faq_candidates" in query and "RETURNING" in query:
            question, answer, editor, _candidate_id = params
            self.candidate = {
                **self.candidate,
                "normalized_question": question,
                "suggested_answer": answer,
                "was_human_edited": True,
                "last_edited_by": editor,
                "last_edited_at": datetime(2026, 5, 14, 12, 0, 0),
            }
            self.next_row = self.candidate
        else:
            self.next_row = None

    def fetchone(self):
        return self.next_row


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args, **kwargs):
        return self.fake_cursor


def test_approval_updates_candidate_inserts_event_and_promotes_faq(monkeypatch):
    candidate = {
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": 74,
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "normalized_question": "Quiero reservar una clase",
        "suggested_answer": "La reserva se realiza por el canal oficial.",
        "status": "pending",
    }
    cursor = FakeCursor(candidate)
    monkeypatch.setattr(svc, "get_db_connection", lambda: FakeConnection(cursor))

    response = svc.apply_validation(
        ValidationRequest(
            suggestion_id=candidate["candidate_id"],
            reviewer="Ana",
            status="approved",
            notes="Valida",
            reviewed_at=datetime(2026, 5, 14, 12, 0, 0),
        )
    )

    joined_queries = "\n".join(query for query, _ in cursor.queries)
    assert "INSERT INTO faq_mvp.existing_faqs" in joined_queries
    assert "UPDATE faq_mvp.faq_candidates" in joined_queries
    assert "INSERT INTO faq_mvp.faq_validation_events" in joined_queries
    assert response.previous_status == "pending"
    assert response.promoted_faq_id == candidate["candidate_id"]


def test_needs_review_candidate_uses_same_approval_contract(monkeypatch):
    candidate = {
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": 74,
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "normalized_question": "Â¿Hacen envios a Bucaramanga?",
        "suggested_answer": "La cobertura de envios puede variar segun la ciudad.",
        "status": "needs_review",
    }
    cursor = FakeCursor(candidate)
    monkeypatch.setattr(svc, "get_db_connection", lambda: FakeConnection(cursor))

    response = svc.apply_validation(
        ValidationRequest(
            suggestion_id=candidate["candidate_id"],
            reviewer="Ana",
            status="approved",
            edited_question="Â¿Hacen envios a Bucaramanga?",
            edited_answer="Los envios a Bucaramanga deben confirmarse antes de finalizar el pedido.",
            reviewed_at=datetime(2026, 5, 14, 12, 0, 0),
        )
    )

    assert response.previous_status == "needs_review"
    assert response.status == "approved"
    assert response.promoted_faq_id == candidate["candidate_id"]


def test_approval_without_agent_id_returns_clear_error():
    with pytest.raises(HTTPException) as exc_info:
        svc.promote_candidate_to_existing_faq(
            cursor=None,
            candidate={
                "candidate_id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": 74,
                "agent_id": None,
                "normalized_question": "Pregunta",
                "suggested_answer": "Respuesta",
            },
        )

    assert exc_info.value.status_code == 409
    assert "agent_id" in exc_info.value.detail


def test_validation_with_edit_promotes_edited_version(monkeypatch):
    candidate = {
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": 74,
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "normalized_question": "¿Qué vale el domicilio?",
        "suggested_answer": "El domicilio puede tener costo.",
        "status": "pending",
    }
    cursor = FakeCursor(candidate)
    monkeypatch.setattr(svc, "get_db_connection", lambda: FakeConnection(cursor))

    response = svc.apply_validation(
        ValidationRequest(
            suggestion_id=candidate["candidate_id"],
            reviewer="Ana",
            status="approved",
            notes="Aprobada con ajuste",
            edited_question="¿Desde qué valor el domicilio es gratis?",
            edited_answer="El domicilio es gratis en compras superiores a $130.000.",
            reviewed_at=datetime(2026, 5, 14, 12, 0, 0),
        )
    )

    joined_queries = "\n".join(query for query, _ in cursor.queries)
    assert "INSERT INTO faq_mvp.faq_candidate_edit_events" in joined_queries
    assert "INSERT INTO faq_mvp.existing_faqs" in joined_queries
    assert cursor.candidate["normalized_question"] == "¿Desde qué valor el domicilio es gratis?"
    assert cursor.candidate["suggested_answer"] == "El domicilio es gratis en compras superiores a $130.000."
    assert response.promoted_faq_id == candidate["candidate_id"]
