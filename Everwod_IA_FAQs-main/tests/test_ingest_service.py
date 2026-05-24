from datetime import datetime, timedelta

import ingest_service as svc
from ingest_service import pair_user_assistant_messages


def test_pairs_last_user_with_next_assistant_and_ignores_empty_messages():
    now = datetime(2026, 5, 14, 10, 0, 0)
    rows = [
        {
            "message_pk": 1,
            "conversation_pk": 10,
            "conversation_id": "chat-1",
            "workspace_id": 74,
            "company_name": "Empire Box",
            "agent_id": "agent-1",
            "role": "user",
            "content_text": "Hola",
            "created_at": now,
        },
        {
            "message_pk": 2,
            "conversation_pk": 10,
            "conversation_id": "chat-1",
            "workspace_id": 74,
            "company_name": "Empire Box",
            "agent_id": "agent-1",
            "role": "user",
            "content_text": "Quiero reservar una clase",
            "created_at": now + timedelta(seconds=1),
        },
        {
            "message_pk": 3,
            "conversation_pk": 10,
            "conversation_id": "chat-1",
            "workspace_id": 74,
            "company_name": "Empire Box",
            "agent_id": "agent-1",
            "role": "assistant",
            "content_text": "Claro, estas son las opciones.",
            "created_at": now + timedelta(seconds=2),
        },
        {
            "message_pk": 4,
            "conversation_pk": 10,
            "conversation_id": "chat-1",
            "workspace_id": 74,
            "company_name": "Empire Box",
            "agent_id": "agent-1",
            "role": "assistant",
            "content_text": "",
            "created_at": now + timedelta(seconds=3),
        },
    ]

    pairs = pair_user_assistant_messages(rows)

    assert len(pairs) == 1
    assert pairs[0]["user_text"] == "Quiero reservar una clase"
    assert pairs[0]["assistant_text"] == "Claro, estas son las opciones."
    assert pairs[0]["user_message_pk"] == 2
    assert pairs[0]["conversation_pk"] == 10


def test_pairs_keep_workspace_separation():
    now = datetime(2026, 5, 14, 10, 0, 0)
    rows = [
        {
            "message_pk": 1,
            "conversation_pk": 10,
            "conversation_id": "chat-1",
            "workspace_id": 74,
            "company_name": "Workspace A",
            "agent_id": "agent-a",
            "role": "user",
            "content_text": "Quiero informacion de planes",
            "created_at": now,
        },
        {
            "message_pk": 2,
            "conversation_pk": 10,
            "conversation_id": "chat-1",
            "workspace_id": 74,
            "company_name": "Workspace A",
            "agent_id": "agent-a",
            "role": "assistant",
            "content_text": "Respuesta A",
            "created_at": now + timedelta(seconds=1),
        },
        {
            "message_pk": 3,
            "conversation_pk": 20,
            "conversation_id": "chat-2",
            "workspace_id": 75,
            "company_name": "Workspace B",
            "agent_id": "agent-b",
            "role": "user",
            "content_text": "Quiero informacion de planes",
            "created_at": now,
        },
        {
            "message_pk": 4,
            "conversation_pk": 20,
            "conversation_id": "chat-2",
            "workspace_id": 75,
            "company_name": "Workspace B",
            "agent_id": "agent-b",
            "role": "assistant",
            "content_text": "Respuesta B",
            "created_at": now + timedelta(seconds=1),
        },
    ]

    pairs = pair_user_assistant_messages(rows)

    assert {pair["workspace_id"] for pair in pairs} == {74, 75}
    assert {pair["company_id"] for pair in pairs} == {"74", "75"}


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _query, params=None):
        self.params = params

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj


def test_complete_if_fits_uses_safe_limit_without_truncation(monkeypatch):
    now = datetime(2026, 5, 14, 10, 0, 0)
    cursor = FakeCursor(
        [
            {
                "message_pk": 1,
                "conversation_pk": 10,
                "conversation_id": "chat-1",
                "workspace_id": 126,
                "company_name": "Severas Medias",
                "agent_id": "agent-1",
                "role": "user",
                "content_text": "Hacen envios?",
                "created_at": now,
                "raw_messages_found": 4,
                "raw_messages_processed": 4,
                "conversations_found": 2,
                "conversations_processed": 2,
            }
        ]
    )
    monkeypatch.setattr(svc, "FAQ_SAFE_FULL_ANALYSIS_LIMIT", 50000)
    monkeypatch.setattr(svc, "get_db_connection", lambda: FakeConnection(cursor))

    _rows, metrics = svc.fetch_message_rows_with_metrics(limit=None, since_days=365, workspace_id=126)

    assert cursor.params[-1] == 50000
    assert metrics["analysis_limit_mode"] == "complete_if_fits"
    assert metrics["effective_limit"] == 50000
    assert metrics["raw_messages_found"] == 4
    assert metrics["raw_messages_processed"] == 4
    assert metrics["conversations_found"] == 2
    assert metrics["conversations_processed"] == 2
    assert metrics["truncation_applied"] is False
    assert metrics["truncation_ratio"] == 0.0


def test_complete_if_fits_reports_truncation_when_over_safe_limit(monkeypatch):
    now = datetime(2026, 5, 14, 10, 0, 0)
    cursor = FakeCursor(
        [
            {
                "message_pk": 1,
                "conversation_pk": 10,
                "conversation_id": "chat-1",
                "workspace_id": 126,
                "company_name": "Severas Medias",
                "agent_id": "agent-1",
                "role": "user",
                "content_text": "Hacen envios?",
                "created_at": now,
                "raw_messages_found": 6,
                "raw_messages_processed": 5,
                "conversations_found": 3,
                "conversations_processed": 2,
            }
        ]
    )
    monkeypatch.setattr(svc, "FAQ_SAFE_FULL_ANALYSIS_LIMIT", 5)
    monkeypatch.setattr(svc, "get_db_connection", lambda: FakeConnection(cursor))

    _rows, metrics = svc.fetch_message_rows_with_metrics(limit=None, since_days=365, workspace_id=126)

    assert cursor.params[-1] == 5
    assert metrics["analysis_limit_mode"] == "complete_if_fits"
    assert metrics["raw_messages_found"] == 6
    assert metrics["raw_messages_processed"] == 5
    assert metrics["conversations_found"] == 3
    assert metrics["conversations_processed"] == 2
    assert metrics["truncation_applied"] is True
    assert metrics["truncation_ratio"] == 0.1667
