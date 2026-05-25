from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from psycopg2.extras import Json, RealDictCursor

from app.core.common import FAQ_SCHEMA, get_db_connection, normalize_question, normalize_text
from app.core.config import (
    FAQ_DUPLICATE_THRESHOLD,
    FAQ_EMBEDDING_BACKEND,
    FAQ_SAFE_FULL_ANALYSIS_LIMIT,
    FAQ_SKIP_EXISTING,
    FAQ_SKIP_REJECTED,
    MODEL_NAME,
)
from app.core.models import (
    SuggestionEditRequest,
    SuggestionEditResponse,
    SuggestionResponse,
    SuggestionSummary,
    ValidationRecord,
    ValidationRequest,
    ValidationResponse,
    WorkspaceResponse,
)
from app.pipeline.cleaning import deduplicate_support_example_texts
from app.pipeline.embeddings import _dot, current_embedding_model_label, encode_texts
from app.pipeline.quality import QUALITY_METRIC_KEYS
SUMMARY_METRIC_FIELDS = tuple(
    field
    for field in SuggestionSummary.model_fields
    if field
    not in {
        "company_count",
        "cluster_count",
        "total_examples",
        "average_cluster_size",
        "silhouette_score",
        "suggestions",
        "run_id",
    }
)


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
        if not text:
            stats["pairs_rejected_empty_text"] += 1
            continue
        if conversation_pk is None:
            continue
        messages_by_conversation[int(conversation_pk)].append({**row, "content_text": text, "role": role})

    pairs: List[Dict[str, Any]] = []
    for _conversation_pk, events in messages_by_conversation.items():
        pending_user: Optional[Dict[str, Any]] = None
        for event in sorted(events, key=_event_sort_key):
            role = event.get("role")
            if role == "user":
                if pending_user is not None:
                    stats["pairs_rejected_no_assistant_followup"] += 1
                pending_user = event
                continue
            if role == "assistant" and pending_user is not None:
                pairs.append(
                    {
                        "workspace_id": pending_user.get("workspace_id"),
                        "company_id": str(pending_user.get("workspace_id")),
                        "company_name": pending_user.get("company_name"),
                        "agent_id": pending_user.get("agent_id"),
                        "conversation_pk": pending_user.get("conversation_pk"),
                        "conversation_id": pending_user.get("conversation_id"),
                        "user_message_pk": pending_user.get("message_pk"),
                        "assistant_message_pk": event.get("message_pk"),
                        "user_text": pending_user.get("content_text"),
                        "assistant_text": event.get("content_text"),
                        "created_at": pending_user.get("created_at"),
                    }
                )
                stats["user_assistant_pairs_built"] += 1
                pending_user = None
        if pending_user is not None:
            stats["pairs_rejected_no_assistant_followup"] += 1

    return pairs, stats


def pair_user_assistant_messages(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pairs, _stats = pair_user_assistant_messages_with_stats(rows)
    return pairs


def empty_ingest_metrics(
    *,
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
        created_dates = [
            value
            for row in rows
            if isinstance((value := row.get("created_at")), datetime)
        ]
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


def load_existing_faqs_by_company() -> Dict[str, List[str]]:
    if not FAQ_SKIP_EXISTING:
        return {}

    query = f"""
        SELECT workspace_id, question
        FROM {FAQ_SCHEMA}.existing_faqs
        WHERE is_active = true
          AND deleted_at IS NULL
          AND question IS NOT NULL
          AND btrim(question) <> ''
    """

    faqs_by_company: Dict[str, List[str]] = defaultdict(list)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                for workspace_id, question in cursor.fetchall():
                    clean = normalize_text(question)
                    if clean:
                        faqs_by_company[str(workspace_id)].append(clean)
    except Exception as exc:
        print(f"No se pudieron cargar FAQs existentes. Se generaran sugerencias sin deduplicar. Error: {exc}")
    return faqs_by_company

def load_previous_candidates_by_company() -> Dict[str, List[str]]:
    """
    Carga candidatos previos para evitar duplicidad entre corridas.

    Importante:
    Incluye rejected cuando FAQ_SKIP_REJECTED=true, porque el cliente pidió que
    las FAQs rechazadas no vuelvan a generarse en siguientes corridas.
    """
    statuses = ["pending", "approved", "needs_review"]
    if FAQ_SKIP_REJECTED:
        statuses.append("rejected")

    placeholders = ", ".join(["%s"] * len(statuses))
    query = f"""
        SELECT workspace_id, normalized_question
        FROM {FAQ_SCHEMA}.faq_candidates
        WHERE status IN ({placeholders})
          AND normalized_question IS NOT NULL
          AND btrim(normalized_question) <> ''
    """

    candidates_by_company: Dict[str, List[str]] = defaultdict(list)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, tuple(statuses))
                for workspace_id, question in cursor.fetchall():
                    clean = normalize_text(question)
                    if clean:
                        candidates_by_company[str(workspace_id)].append(clean)

    except Exception as exc:
        print(f"No se pudieron cargar candidatos previos para deduplicacion. Error: {exc}")

    return candidates_by_company

def is_existing_faq(question: str, existing_questions: List[str]) -> bool:
    if not existing_questions:
        return False

    normalized = normalize_question(question)
    if normalized in {normalize_question(existing) for existing in existing_questions}:
        return True

    embeddings = encode_texts([question, *existing_questions])
    similarities = [_dot(existing_embedding, embeddings[0]) for existing_embedding in embeddings[1:]]
    return bool(similarities and max(similarities) >= FAQ_DUPLICATE_THRESHOLD)

def _candidate_to_response(candidate: Dict[str, Any]) -> SuggestionResponse:
    metadata = candidate.get("candidate_metadata") or {}
    support_examples, _deduplicated = deduplicate_support_example_texts(candidate.get("support_examples") or [], limit=3)
    return SuggestionResponse(
        id=str(candidate["candidate_id"]),
        company_id=str(candidate.get("company_id") or candidate.get("workspace_id")),
        company_name=candidate.get("company_name"),
        workspace_id=int(candidate["workspace_id"]),
        agent_id=str(candidate["agent_id"]) if candidate.get("agent_id") else None,
        question=candidate["normalized_question"],
        answer=candidate.get("suggested_answer") or "",
        cluster_size=int(candidate.get("recurrence_count") or candidate.get("cluster_size") or 0),
        support_examples=support_examples,
        cluster_score=float(candidate.get("cluster_score") or 0.0),
        quality_tier=metadata.get("quality_tier"),
        review_reason=metadata.get("review_reason"),
        review_reason_code=metadata.get("review_reason_code"),
        generation_confidence=metadata.get("generation_confidence"),
        cluster_intent_statement=metadata.get("cluster_intent_statement"),
        knowledge_statement=metadata.get("knowledge_statement"),
        cluster_question_alignment=metadata.get("cluster_question_alignment"),
        candidate_kind=metadata.get("candidate_kind") or "full_faq",
        requires_answer_review=bool(metadata.get("requires_answer_review") or False),
        support_level=metadata.get("support_level"),
        repaired=bool(metadata.get("repaired") or False),
        since_days_used=metadata.get("since_days"),
        was_human_edited=bool(candidate.get("was_human_edited") or False),
        last_edited_by=candidate.get("last_edited_by"),
        last_edited_at=candidate.get("last_edited_at"),
        edit_count=int(candidate.get("edit_count") or 0),
        status=candidate.get("status", "pending"),
        created_at=candidate.get("created_at"),
        updated_at=candidate.get("updated_at"),
        run_id=candidate.get("run_id"),
    )

def summary_metric_payload(stats: Dict[str, Any]) -> Dict[str, Any]:
    return {field: stats.get(field) for field in SUMMARY_METRIC_FIELDS if field in stats}

def create_pipeline_run(parameters: Dict[str, Any], workspace_id: Optional[int] = None) -> str:
    query = f"""
        INSERT INTO {FAQ_SCHEMA}.pipeline_runs (workspace_id, status, parameters, notes)
        VALUES (%s, 'running', %s, %s)
        RETURNING run_id::text
    """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    workspace_id,
                    Json(parameters),
                    "Pipeline manual/API iniciado.",
                ),
            )

            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("No se pudo crear pipeline_run: PostgreSQL no devolvió run_id.")

            return str(row[0])

def finish_pipeline_run(run_id: str, status: str, notes: Optional[str] = None) -> None:
    query = f"""
        UPDATE {FAQ_SCHEMA}.pipeline_runs
        SET status = %s, finished_at = now(), notes = COALESCE(%s, notes)
        WHERE run_id = %s
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (status, notes, run_id))

def insert_metric(cursor: Any, run_id: str, name: str, value: Optional[float], metadata: Optional[Dict[str, Any]] = None) -> None:
    cursor.execute(
        f"""
        INSERT INTO {FAQ_SCHEMA}.pipeline_metrics (run_id, metric_name, metric_value, metric_metadata)
        VALUES (%s, %s, %s, %s)
        """,
        (run_id, name, value, Json(metadata or {})),
    )

def persist_embedding(cursor: Any, candidate: Dict[str, Any], example: Dict[str, Any]) -> int:
    message_pk = example.get("message_pk")
    embedding = example.get("embedding")
    if not message_pk or not embedding:
        return 0

    cursor.execute(
        f"""
        INSERT INTO {FAQ_SCHEMA}.message_embeddings (
            message_pk, workspace_id, agent_id, embedding_model, embedding_dimension, embedding, embedding_metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (message_pk, embedding_model) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            agent_id = EXCLUDED.agent_id,
            embedding_dimension = EXCLUDED.embedding_dimension,
            embedding = EXCLUDED.embedding,
            embedding_metadata = EXCLUDED.embedding_metadata
        """,
        (
            message_pk,
            candidate["workspace_id"],
            candidate.get("agent_id"),
            current_embedding_model_label(),
            len(embedding),
            embedding,
            Json({"normalized": True, "source": "faq_suggestion_pipeline"}),
        ),
    )
    return 1

def find_existing_candidate(cursor: Any, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cursor.execute(
        f"""
        SELECT candidate_id::text, status
        FROM {FAQ_SCHEMA}.faq_candidates
        WHERE workspace_id = %s
          AND COALESCE(agent_id::text, '') = COALESCE(%s, '')
          AND lower(normalized_question) = lower(%s)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate["workspace_id"], candidate.get("agent_id"), candidate["normalized_question"]),
    )
    row = cursor.fetchone()
    return dict(row) if row else None

def persist_pipeline_results(run_id: str, candidates: List[Dict[str, Any]], stats: Dict[str, Any]) -> List[SuggestionResponse]:
    responses: List[SuggestionResponse] = []
    embeddings_persisted = 0

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            for candidate in candidates:
                if candidate["workspace_id"] is None:
                    continue

                cursor.execute(
                    f"""
                    INSERT INTO {FAQ_SCHEMA}.question_clusters (
                        run_id, workspace_id, agent_id, cluster_label, algorithm,
                        representative_question, centroid_embedding, cluster_size, cluster_metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING cluster_id::text
                    """,
                    (
                        run_id,
                        candidate["workspace_id"],
                        candidate.get("agent_id"),
                        candidate["cluster_label"],
                        candidate["algorithm"],
                        candidate["normalized_question"],
                        candidate["centroid_embedding"],
                        candidate["cluster_size"],
                        Json(candidate["cluster_metadata"]),
                    ),
                )
                cluster_row = cursor.fetchone()
                if cluster_row is None:
                    raise RuntimeError("No se pudo crear question_cluster: PostgreSQL no devolvió cluster_id.")

                cluster_id = str(cluster_row["cluster_id"])
                candidate["cluster_id"] = cluster_id

                existing = find_existing_candidate(cursor, candidate)
                if existing:
                    candidate_id = existing["candidate_id"]
                    next_status = (
                        "needs_review"
                        if existing["status"] == "pending" and candidate.get("status") == "needs_review"
                        else existing["status"]
                    )
                    cursor.execute(
                        f"""
                        UPDATE {FAQ_SCHEMA}.faq_candidates
                        SET cluster_id = %s,
                            suggested_answer = %s,
                            cluster_label = %s,
                            recurrence_count = %s,
                            first_seen_at = %s,
                            last_seen_at = %s,
                            status = %s,
                            updated_at = now(),
                            candidate_metadata = COALESCE(candidate_metadata, '{{}}'::jsonb) || %s::jsonb
                        WHERE candidate_id = %s
                        """,
                        (
                            cluster_id,
                            candidate["suggested_answer"],
                            candidate["cluster_label"],
                            candidate["recurrence_count"],
                            candidate["first_seen_at"],
                            candidate["last_seen_at"],
                            next_status,
                            Json(candidate["candidate_metadata"]),
                            candidate_id,
                        ),
                    )
                    candidate["candidate_id"] = candidate_id
                    candidate["status"] = next_status
                else:
                    cursor.execute(
                        f"""
                        INSERT INTO {FAQ_SCHEMA}.faq_candidates (
                            candidate_id, workspace_id, agent_id, cluster_id, normalized_question,
                            suggested_answer, cluster_label, recurrence_count, first_seen_at,
                            last_seen_at, status, candidate_metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING candidate_id::text, status
                        """,
                        (
                            candidate["candidate_id"],
                            candidate["workspace_id"],
                            candidate.get("agent_id"),
                            cluster_id,
                            candidate["normalized_question"],
                            candidate["suggested_answer"],
                            candidate["cluster_label"],
                            candidate["recurrence_count"],
                            candidate["first_seen_at"],
                            candidate["last_seen_at"],
                            candidate.get("status", "pending"),
                            Json(candidate["candidate_metadata"]),
                        ),
                    )
                    created = cursor.fetchone()
                    if created is None:
                        raise RuntimeError("No se pudo crear faq_candidate: PostgreSQL no devolvió candidate_id.")

                    candidate["candidate_id"] = str(created["candidate_id"])
                    candidate["status"] = str(created["status"])
                    cursor.execute(
                        f"""
                        INSERT INTO {FAQ_SCHEMA}.faq_validation_events (
                            candidate_id, event_type, previous_status, new_status, reviewer_identifier, notes
                        )
                        VALUES (%s, 'created', NULL, %s, 'pipeline', %s)
                        """,
                        (
                            candidate["candidate_id"],
                            candidate.get("status", "pending"),
                            f"Generado por pipeline_run={run_id}",
                        ),
                    )

                cursor.execute(
                    f"DELETE FROM {FAQ_SCHEMA}.faq_candidate_examples WHERE candidate_id = %s",
                    (candidate["candidate_id"],),
                )
                for example in candidate["examples"]:
                    embeddings_persisted += persist_embedding(cursor, candidate, example)
                    if not example.get("message_pk") or not example.get("conversation_pk"):
                        continue
                    cursor.execute(
                        f"""
                        INSERT INTO {FAQ_SCHEMA}.faq_candidate_examples (
                            candidate_id, message_pk, conversation_pk, original_user_message,
                            assistant_response, similarity_score
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            candidate["candidate_id"],
                            example["message_pk"],
                            example["conversation_pk"],
                            example["original_user_message"],
                            example["assistant_response"],
                            example["similarity_score"],
                        ),
                    )
                responses.append(_candidate_to_response(candidate))

            metric_values = {
                "conversations_analyzed": stats.get("conversations_analyzed"),
                "faq_candidate_messages": stats.get("total_examples"),
                "candidates_generated": len(responses),
                "clusters_valid": stats.get("clusters_valid"),
                "clusters_rejected": stats.get("clusters_rejected"),
                "llm_rejected": stats.get("llm_rejected"),
                "validator_rejected": stats.get("validator_rejected"),
                "average_cluster_size": stats.get("average_cluster_size"),
                "duplicates_omitted": stats.get("duplicates_omitted"),
                "workspaces_processed": stats.get("company_count"),
                "embeddings_persisted": embeddings_persisted,
                "since_days": stats.get("since_days"),
            }
            metric_values.update({key: stats.get(key, 0) for key in QUALITY_METRIC_KEYS})
            if stats.get("silhouette_score") is not None:
                metric_values["silhouette_score"] = stats.get("silhouette_score")
            for name, value in metric_values.items():
                insert_metric(
                    cursor,
                    run_id,
                    name,
                    value,
                    {
                        "embedding_backend": FAQ_EMBEDDING_BACKEND,
                        "embedding_model": current_embedding_model_label(),
                        "embedding_model_configured": MODEL_NAME,
                        "workspace_id": stats.get("workspace_id"),
                        "since_days": stats.get("since_days"),
                    },
                )

    return responses

def list_suggestions_from_db(
    status: Optional[str] = None,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    include_all: bool = False,
) -> SuggestionSummary:
    filters = []
    params: List[Any] = []
    latest_params: List[Any] = []
    latest_workspace_filter = ""
    latest_order = "finished_at DESC NULLS LAST, started_at DESC"
    if workspace_id is not None:
        latest_workspace_filter = "AND (workspace_id = %s OR workspace_id IS NULL)"
        latest_params.append(workspace_id)
    if not include_all:
        filters.append("qc.run_id = (SELECT run_id FROM latest_successful_run)")
    if status:
        filters.append("fc.status = %s")
        params.append(status)
    if workspace_id is not None:
        filters.append("fc.workspace_id = %s")
        params.append(workspace_id)
    if agent_id:
        filters.append("fc.agent_id = %s")
        params.append(agent_id)
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    query = f"""
        WITH latest_successful_run AS (
            SELECT run_id
            FROM {FAQ_SCHEMA}.pipeline_runs
            WHERE status = 'succeeded'
            {latest_workspace_filter}
            ORDER BY {latest_order}
            LIMIT 1
        )
        SELECT
            fc.candidate_id::text AS candidate_id,
            fc.workspace_id,
            w.name AS company_name,
            fc.agent_id::text AS agent_id,
            fc.normalized_question,
            fc.suggested_answer,
            fc.recurrence_count,
            fc.status,
            fc.created_at,
            fc.updated_at,
            COALESCE(fc.was_human_edited, false) AS was_human_edited,
            fc.last_edited_by,
            fc.last_edited_at,
            fc.candidate_metadata,
            qc.run_id::text AS run_id,
            MAX(pm_silhouette.metric_value) AS silhouette_score,
            COALESCE(ec.edit_count, 0) AS edit_count,
            COALESCE(
                array_remove(array_agg(fce.original_user_message ORDER BY fce.similarity_score DESC NULLS LAST), NULL),
                ARRAY[]::text[]
            ) AS support_examples
        FROM {FAQ_SCHEMA}.faq_candidates AS fc
        JOIN {FAQ_SCHEMA}.workspaces AS w
          ON w.workspace_id = fc.workspace_id
        LEFT JOIN {FAQ_SCHEMA}.question_clusters AS qc
          ON qc.cluster_id = fc.cluster_id
        LEFT JOIN {FAQ_SCHEMA}.pipeline_metrics AS pm_silhouette
          ON pm_silhouette.run_id = qc.run_id
          AND pm_silhouette.metric_name = 'silhouette_score'
        LEFT JOIN {FAQ_SCHEMA}.faq_candidate_examples AS fce
          ON fce.candidate_id = fc.candidate_id
        LEFT JOIN (
            SELECT candidate_id, count(*) AS edit_count
            FROM {FAQ_SCHEMA}.faq_candidate_edit_events
            GROUP BY candidate_id
        ) AS ec
          ON ec.candidate_id = fc.candidate_id
        {where_clause}
        GROUP BY
            fc.candidate_id, fc.workspace_id, w.name, fc.agent_id, fc.normalized_question,
            fc.suggested_answer, fc.recurrence_count, fc.status, fc.created_at, fc.updated_at,
            fc.was_human_edited, fc.last_edited_by, fc.last_edited_at,
            fc.candidate_metadata, qc.run_id, ec.edit_count
        ORDER BY fc.created_at DESC
    """

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, tuple(latest_params + params))
            rows = [dict(row) for row in cursor.fetchall()]

    suggestions = []
    for row in rows:
        metadata = row.get("candidate_metadata") or {}
        support_examples, _deduplicated = deduplicate_support_example_texts(row.get("support_examples") or [], limit=3)
        suggestions.append(
            SuggestionResponse(
                id=row["candidate_id"],
                company_id=str(row["workspace_id"]),
                company_name=row.get("company_name"),
                workspace_id=int(row["workspace_id"]),
                agent_id=row.get("agent_id"),
                question=row["normalized_question"],
                answer=row.get("suggested_answer") or "",
                cluster_size=int(row.get("recurrence_count") or 0),
                support_examples=support_examples,
                cluster_score=float(metadata.get("cluster_score") or 0.0),
                quality_tier=metadata.get("quality_tier"),
                review_reason=metadata.get("review_reason"),
                review_reason_code=metadata.get("review_reason_code"),
                generation_confidence=metadata.get("generation_confidence"),
                cluster_intent_statement=metadata.get("cluster_intent_statement"),
                knowledge_statement=metadata.get("knowledge_statement"),
                cluster_question_alignment=metadata.get("cluster_question_alignment"),
                candidate_kind=metadata.get("candidate_kind") or "full_faq",
                requires_answer_review=bool(metadata.get("requires_answer_review") or False),
                support_level=metadata.get("support_level"),
                repaired=bool(metadata.get("repaired") or False),
                since_days_used=metadata.get("since_days"),
                was_human_edited=bool(row.get("was_human_edited")),
                last_edited_by=row.get("last_edited_by"),
                last_edited_at=row.get("last_edited_at"),
                edit_count=int(row.get("edit_count") or 0),
                status=row.get("status", "pending"),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
                run_id=row.get("run_id"),
            )
        )

    company_count = len({item.company_id for item in suggestions})
    total_examples = sum(item.cluster_size for item in suggestions)

    silhouette_values = [
        float(row["silhouette_score"])
        for row in rows
        if row.get("silhouette_score") is not None
    ]
    silhouette_score = round(silhouette_values[0], 4) if silhouette_values else None

    return SuggestionSummary(
        company_count=company_count,
        cluster_count=len(suggestions),
        total_examples=total_examples,
        average_cluster_size=round(total_examples / len(suggestions), 2) if suggestions else 0.0,
        silhouette_score=silhouette_score,
        suggestions=suggestions,
        run_id=suggestions[0].run_id if suggestions else None,
    )

def list_workspaces_from_db() -> List[WorkspaceResponse]:
    query = f"""
        SELECT DISTINCT w.workspace_id, w.name AS workspace_name
        FROM {FAQ_SCHEMA}.workspaces AS w
        JOIN {FAQ_SCHEMA}.conversations AS c
          ON c.workspace_id = w.workspace_id
        ORDER BY w.name, w.workspace_id
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            rows = [dict(row) for row in cursor.fetchall()]
    return [
        WorkspaceResponse(
            workspace_id=int(row["workspace_id"]),
            workspace_name=row.get("workspace_name") or f"Workspace {row['workspace_id']}",
            company_id=str(row["workspace_id"]),
        )
        for row in rows
    ]


VALID_STATUSES = {"approved", "rejected", "needs_review"}


def _fetch_candidate_for_update(cursor: Any, candidate_id: str) -> Optional[Dict[str, Any]]:
    cursor.execute(
        f"""
        SELECT
            candidate_id::text,
            workspace_id,
            agent_id::text AS agent_id,
            normalized_question,
            suggested_answer,
            status,
            was_human_edited,
            last_edited_by,
            last_edited_at
        FROM {FAQ_SCHEMA}.faq_candidates
        WHERE candidate_id = %s
        FOR UPDATE
        """,
        (candidate_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def apply_candidate_edit(
    cursor: Any,
    candidate: Dict[str, Any],
    question: str,
    answer: str,
    editor: str,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    clean_question = normalize_text(question)
    clean_answer = normalize_text(answer)
    clean_editor = normalize_text(editor)
    if not clean_question:
        raise HTTPException(status_code=400, detail="Edited question cannot be empty.")
    if not clean_answer:
        raise HTTPException(status_code=400, detail="Edited answer cannot be empty.")
    if not clean_editor:
        raise HTTPException(status_code=400, detail="Editor cannot be empty.")

    previous_question = candidate.get("normalized_question")
    previous_answer = candidate.get("suggested_answer")
    changed = clean_question != normalize_text(previous_question) or clean_answer != normalize_text(previous_answer)
    if not changed:
        candidate["normalized_question"] = clean_question
        candidate["suggested_answer"] = clean_answer
        return candidate

    cursor.execute(
        f"""
        INSERT INTO {FAQ_SCHEMA}.faq_candidate_edit_events (
            candidate_id,
            previous_question,
            previous_answer,
            new_question,
            new_answer,
            editor,
            notes
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            candidate["candidate_id"],
            previous_question,
            previous_answer,
            clean_question,
            clean_answer,
            clean_editor,
            notes,
        ),
    )
    cursor.execute(
        f"""
        UPDATE {FAQ_SCHEMA}.faq_candidates
        SET normalized_question = %s,
            suggested_answer = %s,
            was_human_edited = true,
            last_edited_by = %s,
            last_edited_at = now(),
            updated_at = now()
        WHERE candidate_id = %s
        RETURNING
            candidate_id::text,
            workspace_id,
            agent_id::text AS agent_id,
            normalized_question,
            suggested_answer,
            status,
            was_human_edited,
            last_edited_by,
            last_edited_at
        """,
        (clean_question, clean_answer, clean_editor, candidate["candidate_id"]),
    )
    return dict(cursor.fetchone())


def edit_candidate(candidate_id: str, request: SuggestionEditRequest) -> SuggestionEditResponse:
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            candidate = _fetch_candidate_for_update(cursor, candidate_id)
            if not candidate:
                raise HTTPException(status_code=404, detail="Suggestion ID not found.")
            updated = apply_candidate_edit(
                cursor,
                candidate,
                question=request.question,
                answer=request.answer,
                editor=request.editor,
                notes=request.notes,
            )
    return SuggestionEditResponse(
        suggestion_id=updated["candidate_id"],
        question=updated["normalized_question"],
        answer=updated["suggested_answer"],
        editor=updated.get("last_edited_by") or request.editor,
        notes=request.notes,
        was_human_edited=bool(updated.get("was_human_edited")),
        last_edited_at=updated.get("last_edited_at") or datetime.utcnow(),
    )


def promote_candidate_to_existing_faq(cursor: Any, candidate: Dict[str, Any]) -> str:
    if not candidate.get("agent_id"):
        raise HTTPException(
            status_code=409,
            detail="Candidate cannot be approved because it has no agent_id for FAQ promotion.",
        )
    if not normalize_text(candidate.get("suggested_answer")):
        raise HTTPException(
            status_code=409,
            detail="Candidate cannot be approved because suggested_answer is empty.",
        )

    cursor.execute(
        f"""
        INSERT INTO {FAQ_SCHEMA}.existing_faqs (
            faq_id,
            source_agent_id,
            agent_id,
            workspace_id,
            question,
            answer,
            image,
            created_at,
            updated_at,
            deleted_at,
            is_active,
            loaded_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, NULL, now(), now(), NULL, true, now())
        ON CONFLICT (faq_id) DO UPDATE SET
            source_agent_id = EXCLUDED.source_agent_id,
            agent_id = EXCLUDED.agent_id,
            workspace_id = EXCLUDED.workspace_id,
            question = EXCLUDED.question,
            answer = EXCLUDED.answer,
            updated_at = now(),
            deleted_at = NULL,
            is_active = true,
            loaded_at = now()
        RETURNING faq_id::text
        """,
        (
            candidate["candidate_id"],
            candidate["agent_id"],
            candidate["agent_id"],
            candidate["workspace_id"],
            candidate["normalized_question"],
            candidate["suggested_answer"],
        ),
    )
    return str(cursor.fetchone()["faq_id"])


def apply_validation(request: ValidationRequest) -> ValidationResponse:
    if request.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid validation status.")

    reviewed_at = request.reviewed_at or datetime.utcnow()
    promoted_faq_id: Optional[str] = None

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            candidate = _fetch_candidate_for_update(cursor, request.suggestion_id)
            if not candidate:
                raise HTTPException(status_code=404, detail="Suggestion ID not found.")

            previous_status = candidate["status"]
            if request.edited_question is not None or request.edited_answer is not None:
                candidate = apply_candidate_edit(
                    cursor,
                    candidate,
                    question=request.edited_question or candidate.get("normalized_question") or "",
                    answer=request.edited_answer or candidate.get("suggested_answer") or "",
                    editor=request.reviewer,
                    notes=request.notes,
                )
            if request.status == "approved":
                promoted_faq_id = promote_candidate_to_existing_faq(cursor, candidate)

            cursor.execute(
                f"""
                UPDATE {FAQ_SCHEMA}.faq_candidates
                SET status = %s,
                    human_reviewed_by = %s,
                    human_reviewed_at = %s,
                    updated_at = now()
                WHERE candidate_id = %s
                """,
                (request.status, request.reviewer, reviewed_at, request.suggestion_id),
            )
            cursor.execute(
                f"""
                INSERT INTO {FAQ_SCHEMA}.faq_validation_events (
                    candidate_id,
                    event_type,
                    previous_status,
                    new_status,
                    reviewer_identifier,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    request.suggestion_id,
                    request.status,
                    previous_status,
                    request.status,
                    request.reviewer,
                    request.notes,
                ),
            )

    return ValidationResponse(
        suggestion_id=request.suggestion_id,
        reviewer=request.reviewer,
        status=request.status,
        previous_status=previous_status,
        notes=request.notes,
        reviewed_at=reviewed_at,
        promoted_faq_id=promoted_faq_id,
    )


def list_validation_events(workspace_id: Optional[int] = None) -> List[ValidationRecord]:
    filters = ["e.event_type <> 'created'"]
    params: List[Any] = []
    if workspace_id is not None:
        filters.append("c.workspace_id = %s")
        params.append(workspace_id)
    where_clause = " AND ".join(filters)
    query = f"""
        SELECT
            e.validation_event_id::text AS id,
            e.candidate_id::text AS suggestion_id,
            e.reviewer_identifier AS reviewer,
            e.new_status AS status,
            e.previous_status,
            e.notes,
            e.created_at,
            COALESCE(left(c.normalized_question, 180), 'Candidato eliminado') AS question_summary
        FROM {FAQ_SCHEMA}.faq_validation_events AS e
        LEFT JOIN {FAQ_SCHEMA}.faq_candidates AS c
          ON c.candidate_id = e.candidate_id
        WHERE {where_clause}
        ORDER BY e.created_at DESC
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, tuple(params))
            return [ValidationRecord(**dict(row)) for row in cursor.fetchall()]
