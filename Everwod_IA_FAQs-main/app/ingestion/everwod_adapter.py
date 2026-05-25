import json
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from app.core.common import FAQ_RAW_SCHEMA, normalize_text


logger = logging.getLogger(__name__)
SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_schema_name(schema: str) -> str:
    if not SCHEMA_NAME_PATTERN.match(schema or ""):
        raise ValueError(f"Invalid PostgreSQL schema name: {schema!r}")
    return schema


def _coerce_json(payload: Any) -> Tuple[Any, bool]:
    if payload is None:
        return None, False
    if isinstance(payload, (dict, list)):
        return payload, True
    if isinstance(payload, str):
        stripped = payload.strip()
        if not stripped:
            return "", True
        try:
            return json.loads(stripped), True
        except json.JSONDecodeError:
            return stripped, False
    return payload, True


def _first_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return normalize_text(payload)
    if isinstance(payload, list):
        parts = [_first_text(item) for item in payload]
        return normalize_text(" ".join(part for part in parts if part))
    if isinstance(payload, dict):
        for path in (
            ("text", "value"),
            ("input_text", "text"),
            ("output_text", "text"),
            ("content", "text"),
        ):
            value: Any = payload
            for key in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(key)
            text = _first_text(value)
            if text:
                return text
        for key in ("text", "content", "message", "value", "transcript"):
            text = _first_text(payload.get(key))
            if text:
                return text
    return ""


def extract_message_role(message_json: Any) -> Optional[str]:
    payload, valid = _coerce_json(message_json)
    if not valid or not isinstance(payload, dict):
        return None
    role = normalize_text(payload.get("role")).lower()
    return role or None


def extract_message_text(message_json: Any) -> str:
    payload, _valid = _coerce_json(message_json)
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") not in {None, "text", "input_text", "output_text"}:
                    continue
                text = _first_text(item)
                if text:
                    parts.append(text)
            return normalize_text(" ".join(parts))
        text = _first_text(content)
        if text:
            return text
        return _first_text(payload)
    return _first_text(payload)


def _warn_skip(row: Dict[str, Any], reason: str) -> None:
    message_id = row.get("message_id") or row.get("id") or "<unknown>"
    logger.warning("Skipping Everwod chat message id=%s reason=%s", message_id, reason)


def _increment(metrics: Dict[str, int], key: str) -> None:
    metrics[key] = int(metrics.get(key, 0)) + 1

def _to_iso(value: Any) -> Any:
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    return value

def normalize_everwod_chat_record(row: Dict[str, Any], metrics: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
    metrics = metrics if metrics is not None else defaultdict(int)
    payload, valid = _coerce_json(row.get("message"))
    if not valid:
        _increment(metrics, "messages_skipped_invalid_json")
        _warn_skip(row, "invalid_json")
        return None

    role = extract_message_role(payload)
    text = extract_message_text(payload)
    if role not in {"user", "assistant"}:
        _increment(metrics, "messages_skipped_unsupported_role")
        return None
    if not text:
        _increment(metrics, "messages_skipped_empty_text")
        _warn_skip(row, "empty_text")
        return None

    if role == "user":
        _increment(metrics, "user_messages")
    elif role == "assistant":
        _increment(metrics, "assistant_messages")

    agent_chat_id = row.get("agent_chat_id") or row.get("source_agent_chat_id")
    message_id = row.get("message_id") or row.get("id") or row.get("source_chat_message_id")
    workspace_id = row.get("workspace_id")
    return {
        "company_id": str(workspace_id) if workspace_id is not None else "unknown",
        "workspace_id": workspace_id,
        "company_name": row.get("company_name") or row.get("workspace_name"),
        "agent_id": str(row["agent_id"]) if row.get("agent_id") else None,
        "conversation_id": str(row.get("conversation_id") or ""),
        "agent_chat_id": str(agent_chat_id) if agent_chat_id else None,
        "message_id": str(message_id) if message_id else None,
        "role": role,
        "content_text": text,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "message_type": row.get("message_type"),
    }


def _event_sort_key(event: Dict[str, Any]) -> tuple:
    return (
        event.get("agent_chat_id") or "",
        event.get("created_at") or datetime.min,
        event.get("message_id") or "",
    )


def pair_user_assistant_messages(
    rows: Iterable[Dict[str, Any]],
    metrics: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    metrics = metrics if metrics is not None else defaultdict(int)
    by_chat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        normalized = normalize_everwod_chat_record(row, metrics)
        if normalized and normalized.get("agent_chat_id"):
            by_chat[str(normalized["agent_chat_id"])].append(normalized)

    pairs: List[Dict[str, Any]] = []
    for chat_id, events in by_chat.items():
        last_user: Optional[Dict[str, Any]] = None
        for event in sorted(events, key=_event_sort_key):
            if event["role"] == "user":
                last_user = event
                continue
            if event["role"] == "assistant" and last_user:
                pairs.append(
                    {
                        "company_id": last_user["company_id"],
                        "workspace_id": last_user.get("workspace_id"),
                        "company_name": last_user.get("company_name"),
                        "agent_id": last_user.get("agent_id"),
                        "conversation_id": last_user.get("conversation_id") or chat_id,
                        "agent_chat_id": chat_id,
                        "user_message_id": last_user.get("message_id"),
                        "assistant_message_id": event.get("message_id"),
                        "user_text": last_user["content_text"],
                        "assistant_text": event["content_text"],
                        "created_at": _to_iso(last_user.get("created_at")),
                        "assistant_created_at": _to_iso(event.get("created_at")),
                    }
                )
                _increment(metrics, "user_assistant_pairs_built")
                last_user = None
    return pairs


def fetch_workspaces(cursor: Any, raw_schema: str = FAQ_RAW_SCHEMA) -> List[Dict[str, Any]]:
    schema = safe_schema_name(raw_schema)
    cursor.execute(f"SELECT * FROM {schema}.workspaces ORDER BY id")
    return [dict(row) for row in cursor.fetchall()]


def fetch_agents(cursor: Any, raw_schema: str = FAQ_RAW_SCHEMA) -> List[Dict[str, Any]]:
    schema = safe_schema_name(raw_schema)
    cursor.execute(f"SELECT * FROM {schema}.agents ORDER BY workspace_id, id")
    return [dict(row) for row in cursor.fetchall()]


def fetch_chats(cursor: Any, raw_schema: str = FAQ_RAW_SCHEMA) -> List[Dict[str, Any]]:
    schema = safe_schema_name(raw_schema)
    cursor.execute(f"SELECT * FROM {schema}.agent_chats ORDER BY workspace_id, created_at, id")
    return [dict(row) for row in cursor.fetchall()]


def fetch_existing_faqs(cursor: Any, raw_schema: str = FAQ_RAW_SCHEMA) -> List[Dict[str, Any]]:
    schema = safe_schema_name(raw_schema)
    cursor.execute(f"SELECT * FROM {schema}.agent_faqs ORDER BY created_at, id")
    return [dict(row) for row in cursor.fetchall()]


def iter_chat_messages(
    cursor: Any,
    raw_schema: str = FAQ_RAW_SCHEMA,
    *,
    limit: Optional[int] = None,
    since_days: int = 90,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    schema = safe_schema_name(raw_schema)
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=since_days)
    filters = ["cm.created_at >= %s"]
    params: List[Any] = [since]

    if workspace_id is not None:
        filters.append("ac.workspace_id = %s")
        params.append(workspace_id)
    if agent_id:
        filters.append(
            f"""
            EXISTS (
                SELECT 1
                FROM {schema}.agents AS af
                WHERE af.workspace_id = ac.workspace_id
                  AND af.id::text = %s
            )
            """
        )
        params.append(agent_id)

    query = f"""
        SELECT
            cm.id AS message_id,
            cm.message,
            cm.created_at,
            cm.updated_at,
            cm.agent_chat_id,
            cm.model_output,
            cm.message_type,
            ac.workspace_id,
            ac.conversation_id,
            ac.thread_id,
            ac.model,
            w.name AS company_name
        FROM {schema}.chat_messages AS cm
        JOIN {schema}.agent_chats AS ac
          ON ac.id = cm.agent_chat_id
        LEFT JOIN {schema}.workspaces AS w
          ON w.id = ac.workspace_id
        WHERE {" AND ".join(filters)}
        ORDER BY ac.workspace_id, cm.agent_chat_id, cm.created_at, cm.id
    """
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)

    cursor.execute(query, tuple(params))
    for row in cursor.fetchall():
        yield dict(row)
