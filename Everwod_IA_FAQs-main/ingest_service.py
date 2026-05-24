from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import FastAPI
from psycopg2.extras import RealDictCursor

from faq_common import (
    DATA_DIR,
    FAQ_SCHEMA,
    configure_cors,
    get_db_connection,
    get_int_env,
    normalize_text,
    save_json_lines,
)
from faq_models import IngestRequest, IngestResponse


app = FastAPI(title="Everwod FAQ Ingestion Service")
configure_cors(app)

OUTPUT_PATH = DATA_DIR / "conversations.jsonl"
FAQ_SAFE_FULL_ANALYSIS_LIMIT = max(1, get_int_env("FAQ_SAFE_FULL_ANALYSIS_LIMIT", 50000))


def resolve_analysis_limit(limit: Optional[int]) -> Tuple[int, str]:
    if limit is None:
        return FAQ_SAFE_FULL_ANALYSIS_LIMIT, "complete_if_fits"
    return int(limit), "explicit_limit"


def _event_sort_key(event: Dict[str, Any]) -> tuple:
    return (
        event.get("conversation_pk") or 0,
        event.get("created_at") or datetime.min,
        event.get("message_pk") or 0,
    )


def pair_user_assistant_messages_with_stats(rows: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    messages_by_conversation: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    stats = {
        "user_messages_considered": 0,
        "assistant_messages_considered": 0,
        "user_assistant_pairs_built": 0,
        "pairs_rejected_no_assistant_followup": 0,
        "pairs_rejected_empty_text": 0,
    }
    for row in rows:
        text = normalize_text(row.get("content_text"))
        role = normalize_text(row.get("role")).lower()
        conversation_pk = row.get("conversation_pk")
        if role == "user":
            stats["user_messages_considered"] += 1
        elif role == "assistant":
            stats["assistant_messages_considered"] += 1
        if not conversation_pk or role not in {"user", "assistant"}:
            continue
        if not text:
            stats["pairs_rejected_empty_text"] += 1
            continue
        clean_row = dict(row)
        clean_row["content_text"] = text
        clean_row["role"] = role
        messages_by_conversation[int(conversation_pk)].append(clean_row)

    pairs: List[Dict[str, Any]] = []
    for conversation_pk, events in messages_by_conversation.items():
        last_user: Optional[Dict[str, Any]] = None
        for event in sorted(events, key=_event_sort_key):
            if event["role"] == "user":
                last_user = event
                continue

            if event["role"] == "assistant" and last_user:
                workspace_id = last_user.get("workspace_id")
                pairs.append(
                    {
                        "company_id": str(workspace_id) if workspace_id is not None else "unknown",
                        "workspace_id": workspace_id,
                        "company_name": last_user.get("company_name"),
                        "agent_id": str(last_user["agent_id"]) if last_user.get("agent_id") else None,
                        "conversation_id": str(last_user.get("conversation_id") or ""),
                        "conversation_pk": conversation_pk,
                        "user_message_pk": last_user.get("message_pk"),
                        "assistant_message_pk": event.get("message_pk"),
                        "user_text": last_user["content_text"],
                        "assistant_text": event["content_text"],
                        "created_at": last_user.get("created_at").isoformat()
                        if last_user.get("created_at")
                        else None,
                        "assistant_created_at": event.get("created_at").isoformat()
                        if event.get("created_at")
                        else None,
                    }
                )
                stats["user_assistant_pairs_built"] += 1
                last_user = None
        if last_user:
            stats["pairs_rejected_no_assistant_followup"] += 1

    return pairs, stats


def pair_user_assistant_messages(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pairs, _stats = pair_user_assistant_messages_with_stats(rows)
    return pairs


def empty_ingest_metrics(
    limit: Optional[int],
    since_days: int,
    effective_limit: Optional[int] = None,
    analysis_limit_mode: str = "explicit_limit",
) -> Dict[str, Any]:
    return {
        "limit": limit,
        "effective_limit": effective_limit if effective_limit is not None else limit,
        "safe_full_analysis_limit": FAQ_SAFE_FULL_ANALYSIS_LIMIT,
        "analysis_limit_mode": analysis_limit_mode,
        "since_days": since_days,
        "raw_messages_found": 0,
        "raw_messages_processed": 0,
        "total_messages_found_before_limit": 0,
        "total_messages_processed_after_limit": 0,
        "conversations_found": 0,
        "conversations_processed": 0,
        "conversations_found_before_limit": 0,
        "conversations_processed_after_limit": 0,
        "truncation_applied": False,
        "truncation_ratio": 0.0,
        "oldest_message_processed_at": None,
        "newest_message_processed_at": None,
    }


def fetch_message_rows(
    limit: Optional[int] = None,
    since_days: int = 90,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows, _metrics = fetch_message_rows_with_metrics(
        limit=limit,
        since_days=since_days,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    return rows


def fetch_message_rows_with_metrics(
    limit: Optional[int] = None,
    since_days: int = 90,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    effective_limit, analysis_limit_mode = resolve_analysis_limit(limit)
    since = datetime.utcnow() - timedelta(days=since_days)
    params: List[Any] = [since]
    filters = [
        "m.created_at >= %s",
        "m.role IN ('user', 'assistant')",
        "m.content_text IS NOT NULL",
        "btrim(m.content_text) <> ''",
    ]

    if workspace_id is not None:
        filters.append("c.workspace_id = %s")
        params.append(workspace_id)
    if agent_id:
        filters.append("c.agent_id = %s")
        params.append(agent_id)

    filter_clause = " AND ".join(filters)
    query = f"""
        WITH eligible_messages AS (
            SELECT
                m.message_pk,
                m.conversation_pk,
                c.source_agent_chat_id::text AS conversation_id,
                c.workspace_id,
                w.name AS company_name,
                c.agent_id::text AS agent_id,
                m.role,
                m.content_text,
                m.created_at
            FROM {FAQ_SCHEMA}.messages AS m
            JOIN {FAQ_SCHEMA}.conversations AS c
              ON c.conversation_pk = m.conversation_pk
            JOIN {FAQ_SCHEMA}.workspaces AS w
              ON w.workspace_id = c.workspace_id
            WHERE {filter_clause}
        ),
        conversation_stats AS (
            SELECT
                conversation_pk,
                max(created_at) AS last_message_at,
                count(*) AS message_count
            FROM eligible_messages
            GROUP BY conversation_pk
        ),
        ordered_conversations AS (
            SELECT
                conversation_pk,
                last_message_at,
                message_count,
                row_number() OVER (ORDER BY last_message_at DESC NULLS LAST, conversation_pk DESC) AS rn,
                sum(message_count) OVER (
                    ORDER BY last_message_at DESC NULLS LAST, conversation_pk DESC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS running_message_count
            FROM conversation_stats
        ),
        selected_conversations AS (
            SELECT conversation_pk
            FROM ordered_conversations
            WHERE running_message_count <= %s OR rn = 1
        ),
        metrics AS (
            SELECT
                (SELECT count(*) FROM eligible_messages) AS raw_messages_found,
                (SELECT count(*) FROM eligible_messages em JOIN selected_conversations sc USING (conversation_pk)) AS raw_messages_processed,
                (SELECT count(*) FROM conversation_stats) AS conversations_found,
                (SELECT count(*) FROM selected_conversations) AS conversations_processed
        )
        SELECT
            em.message_pk,
            em.conversation_pk,
            em.conversation_id,
            em.workspace_id,
            em.company_name,
            em.agent_id,
            em.role,
            em.content_text,
            em.created_at,
            metrics.raw_messages_found,
            metrics.raw_messages_processed,
            metrics.conversations_found,
            metrics.conversations_processed
        FROM eligible_messages AS em
        JOIN selected_conversations AS sc
          ON sc.conversation_pk = em.conversation_pk
        CROSS JOIN metrics
        ORDER BY em.workspace_id, em.conversation_pk, em.created_at, em.message_pk
    """

    query_params = tuple(params + [effective_limit])
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, query_params)
            rows = [dict(row) for row in cursor.fetchall()]

    metrics = empty_ingest_metrics(
        limit=limit,
        since_days=since_days,
        effective_limit=effective_limit,
        analysis_limit_mode=analysis_limit_mode,
    )
    if rows:
        first = rows[0]
        raw_found = int(first.get("raw_messages_found") or 0)
        raw_processed = int(first.get("raw_messages_processed") or len(rows))
        conversations_found = int(first.get("conversations_found") or 0)
        conversations_processed = int(first.get("conversations_processed") or 0)
        for row in rows:
            row.pop("raw_messages_found", None)
            row.pop("raw_messages_processed", None)
            row.pop("conversations_found", None)
            row.pop("conversations_processed", None)
        created_dates = [row.get("created_at") for row in rows if row.get("created_at")]
        metrics.update(
            {
                "raw_messages_found": raw_found,
                "raw_messages_processed": raw_processed,
                "total_messages_found_before_limit": raw_found,
                "total_messages_processed_after_limit": raw_processed,
                "conversations_found": conversations_found,
                "conversations_processed": conversations_processed,
                "conversations_found_before_limit": conversations_found,
                "conversations_processed_after_limit": conversations_processed,
                "truncation_applied": raw_processed < raw_found,
                "truncation_ratio": round(1 - (raw_processed / raw_found), 4) if raw_found else 0.0,
                "oldest_message_processed_at": min(created_dates) if created_dates else None,
                "newest_message_processed_at": max(created_dates) if created_dates else None,
            }
        )
    else:
        count_query = f"""
            WITH eligible_messages AS (
                SELECT m.message_pk, m.conversation_pk
                FROM {FAQ_SCHEMA}.messages AS m
                JOIN {FAQ_SCHEMA}.conversations AS c
                  ON c.conversation_pk = m.conversation_pk
                WHERE {filter_clause}
            )
            SELECT count(*) AS raw_messages_found, count(DISTINCT conversation_pk) AS conversations_found
            FROM eligible_messages
        """
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(count_query, tuple(params))
                row = dict(cursor.fetchone() or {})
        metrics.update(
            {
                "raw_messages_found": int(row.get("raw_messages_found") or 0),
                "total_messages_found_before_limit": int(row.get("raw_messages_found") or 0),
                "conversations_found": int(row.get("conversations_found") or 0),
                "conversations_found_before_limit": int(row.get("conversations_found") or 0),
            }
        )

    return rows, metrics


def fetch_conversation_records(
    limit: Optional[int] = None,
    since_days: int = 90,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    records, _metrics = fetch_conversation_records_with_metrics(
        limit=limit,
        since_days=since_days,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    return records


def fetch_conversation_records_with_metrics(
    limit: Optional[int] = None,
    since_days: int = 90,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows, metrics = fetch_message_rows_with_metrics(
        limit=limit,
        since_days=since_days,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    pairs, pair_stats = pair_user_assistant_messages_with_stats(rows)
    metrics.update(pair_stats)
    return pairs, metrics


def ingest(
    limit: Optional[int] = None,
    since_days: int = 90,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
) -> IngestResponse:
    records, metrics = fetch_conversation_records_with_metrics(
        limit=limit,
        since_days=since_days,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    save_json_lines(records, OUTPUT_PATH)
    return IngestResponse(imported_records=len(records), output_file=str(OUTPUT_PATH), **metrics)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ingest", "source_schema": FAQ_SCHEMA}


@app.post("/ingest", response_model=IngestResponse)
def run_ingest(request: IngestRequest) -> IngestResponse:
    return ingest(
        limit=request.limit,
        since_days=request.since_days,
        workspace_id=request.workspace_id,
        agent_id=request.agent_id,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ingest_service:app", host="127.0.0.1", port=8001, log_level="info")
