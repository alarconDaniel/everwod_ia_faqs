from datetime import datetime, timedelta

import pytest

import app.api.ingest_service as ingest_api
from app.core.models import IngestRequest
from app.ingestion import everwod_adapter as adapter
from app.ingestion.normalized_writer import run_everwod_ingestion


def raw_message(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": {"value": text}}]}


def test_extracts_role_and_text_from_everwod_json():
    payload = raw_message("user", "Hola, necesito información")

    assert adapter.extract_message_role(payload) == "user"
    assert adapter.extract_message_text(payload) == "Hola, necesito información"
    assert adapter.extract_message_role(raw_message("assistant", "Respuesta")) == "assistant"


def test_invalid_json_and_empty_text_are_skipped_without_sensitive_logs(caplog):
    metrics = {}
    sensitive = "cliente@example.com 3001234567"

    invalid = adapter.normalize_everwod_chat_record({"message_id": "msg-1", "message": sensitive}, metrics)
    empty = adapter.normalize_everwod_chat_record({"message_id": "msg-2", "message": raw_message("user", "")}, metrics)

    assert invalid is None
    assert empty is None
    assert metrics["messages_skipped_invalid_json"] == 1
    assert metrics["messages_skipped_empty_text"] == 1
    assert "cliente@example.com" not in caplog.text
    assert "3001234567" not in caplog.text


def test_pairs_user_to_next_assistant_in_temporal_order():
    now = datetime(2026, 5, 24, 9, 0, 0)
    rows = [
        {
            "message_id": "u1",
            "agent_chat_id": "chat-1",
            "workspace_id": 74,
            "company_name": "Empire Box",
            "message": raw_message("user", "Hola"),
            "created_at": now,
        },
        {
            "message_id": "u2",
            "agent_chat_id": "chat-1",
            "workspace_id": 74,
            "company_name": "Empire Box",
            "message": raw_message("user", "Quiero reservar una clase"),
            "created_at": now + timedelta(seconds=1),
        },
        {
            "message_id": "a1",
            "agent_chat_id": "chat-1",
            "workspace_id": 74,
            "company_name": "Empire Box",
            "message": raw_message("assistant", "Claro, estas son las opciones."),
            "created_at": now + timedelta(seconds=2),
        },
    ]
    metrics = {}

    pairs = adapter.pair_user_assistant_messages(rows, metrics)

    assert len(pairs) == 1
    assert pairs[0]["user_message_id"] == "u2"
    assert pairs[0]["assistant_message_id"] == "a1"
    assert pairs[0]["workspace_id"] == 74
    assert metrics["user_assistant_pairs_built"] == 1


def test_normalized_record_keeps_raw_chat_structure_without_text_leakage():
    row = {
        "workspace_id": 126,
        "agent_chat_id": "chat-raw",
        "conversation_id": "conv-raw",
        "message_id": "msg-raw",
        "message": raw_message("user", "Necesito horarios"),
        "created_at": datetime(2026, 5, 24, 9, 0, 0),
    }

    normalized = adapter.normalize_everwod_chat_record(row, {})

    assert normalized["workspace_id"] == 126
    assert normalized["agent_chat_id"] == "chat-raw"
    assert normalized["conversation_id"] == "conv-raw"
    assert normalized["message_id"] == "msg-raw"
    assert normalized["content_text"] == "Necesito horarios"


def test_adapter_queries_raw_tables_and_not_agent_runs():
    source = adapter.iter_chat_messages.__code__.co_consts
    source_text = "\n".join(str(item) for item in source)

    assert "chat_messages" in source_text
    assert "agent_chats" in source_text
    assert "agent_faqs" not in source_text
    assert "agent_runs" not in source_text


class FakeCursor:
    def __init__(self):
        self.queries = []
        self.params = []
        self.current_rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.queries.append(query)
        self.params.append(params)
        compact = " ".join(str(query).split())
        self.rowcount = 0
        if "information_schema.columns" in compact:
            self.current_rows = [{"column_name": column} for column in TARGET_COLUMNS]
        elif "FROM everwod_raw.chat_messages AS cm" in compact and "SELECT cm.*" not in compact:
            self.current_rows = [
                {
                    "message_id": "raw-user",
                    "message": raw_message("user", "Quiero reservar"),
                    "created_at": datetime(2026, 5, 24, 9, 0, 0),
                    "updated_at": datetime(2026, 5, 24, 9, 0, 0),
                    "agent_chat_id": "chat-1",
                    "model_output": None,
                    "message_type": "message",
                    "workspace_id": 74,
                    "conversation_id": "conv-1",
                    "thread_id": "thread-1",
                    "model": "gpt",
                    "company_name": "Empire Box",
                },
                {
                    "message_id": "raw-assistant",
                    "message": raw_message("assistant", "Puedes reservar por WhatsApp."),
                    "created_at": datetime(2026, 5, 24, 9, 0, 1),
                    "updated_at": datetime(2026, 5, 24, 9, 0, 1),
                    "agent_chat_id": "chat-1",
                    "model_output": None,
                    "message_type": "message",
                    "workspace_id": 74,
                    "conversation_id": "conv-1",
                    "thread_id": "thread-1",
                    "model": "gpt",
                    "company_name": "Empire Box",
                },
            ]
        elif compact.upper().startswith("INSERT INTO"):
            self.current_rows = []
            self.rowcount = 1
        else:
            self.current_rows = [{"count": 1}]

    def fetchall(self):
        return self.current_rows

    def fetchone(self):
        return self.current_rows[0] if self.current_rows else None


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def cursor(self, *args, **kwargs):
        return self.cursor_obj


TARGET_COLUMNS = {
    "workspace_id",
    "name",
    "agent_id",
    "model",
    "source_agent_chat_id",
    "conversation_id",
    "thread_id",
    "conversation_pk",
    "source_chat_message_id",
    "role",
    "message_type",
    "raw_message",
    "extraction_status",
    "faq_id",
    "source_agent_id",
    "question",
}


def test_dry_run_returns_metrics_without_writes():
    cursor = FakeCursor()
    metrics = run_everwod_ingestion(connection=FakeConnection(cursor), limit=10, dry_run=True)
    joined_queries = "\n".join(cursor.queries)

    assert metrics["dry_run"] is True
    assert metrics["messages_read"] == 2
    assert metrics["user_messages"] == 1
    assert metrics["assistant_messages"] == 1
    assert metrics["user_assistant_pairs_built"] == 1
    assert "INSERT INTO" not in joined_queries.upper()
    assert "everwod_raw.chat_messages" in joined_queries
    assert "public." not in joined_queries


def test_write_mode_uses_target_schema_idempotently_without_deletes():
    cursor = FakeCursor()
    metrics = run_everwod_ingestion(connection=FakeConnection(cursor), limit=10, dry_run=False)
    joined_queries = "\n".join(cursor.queries)
    upper_queries = joined_queries.upper()

    assert metrics["dry_run"] is False
    assert metrics["records_written"] >= 1
    assert "faq_mvp.messages" in joined_queries
    assert "faq_mvp.conversations" in joined_queries
    assert "faq_mvp.existing_faqs" in joined_queries
    assert "ON CONFLICT" in upper_queries
    assert "DELETE FROM" not in upper_queries
    assert "TRUNCATE" not in upper_queries
    assert "agent_runs" not in joined_queries
    assert "public." not in joined_queries


def test_ingest_endpoint_defaults_to_dry_run(monkeypatch):
    captured = {}

    def fake_run_everwod_ingestion(**kwargs):
        captured.update(kwargs)
        return {
            "raw_schema": "everwod_raw",
            "target_schema": "faq_mvp",
            "source_schema": "faq_mvp",
            "source": "everwod_raw",
            "dry_run": kwargs["dry_run"],
            "records_written": 0,
        }

    monkeypatch.setattr(ingest_api, "run_everwod_ingestion", fake_run_everwod_ingestion)

    response = ingest_api.run_ingest(IngestRequest(limit=1))

    assert captured["dry_run"] is True
    assert response.dry_run is True
    assert response.imported_records == 0


def test_ingest_endpoint_requires_explicit_false_to_write(monkeypatch):
    captured = {}

    def fake_run_everwod_ingestion(**kwargs):
        captured.update(kwargs)
        return {
            "raw_schema": "everwod_raw",
            "target_schema": "faq_mvp",
            "source_schema": "faq_mvp",
            "source": "everwod_raw",
            "dry_run": kwargs["dry_run"],
            "records_written": 3,
        }

    monkeypatch.setattr(ingest_api, "run_everwod_ingestion", fake_run_everwod_ingestion)

    response = ingest_api.run_ingest(IngestRequest(limit=1, dry_run=False))

    assert captured["dry_run"] is False
    assert response.dry_run is False
    assert response.imported_records == 3
