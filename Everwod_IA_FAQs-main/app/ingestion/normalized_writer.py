from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from psycopg2.extras import RealDictCursor

from app.core.common import FAQ_RAW_SCHEMA, FAQ_SCHEMA, get_db_connection
from app.ingestion.everwod_adapter import iter_chat_messages, pair_user_assistant_messages, safe_schema_name


def empty_metrics(
    *,
    raw_schema: str,
    target_schema: str,
    limit: Optional[int],
    since_days: int,
    dry_run: bool,
) -> Dict[str, Any]:
    return {
        "raw_schema": raw_schema,
        "target_schema": target_schema,
        "source_schema": target_schema,
        "source": raw_schema,
        "dry_run": dry_run,
        "limit": limit,
        "since_days": since_days,
        "workspaces_read": 0,
        "agents_read": 0,
        "chats_read": 0,
        "messages_read": 0,
        "faqs_read": 0,
        "user_messages": 0,
        "assistant_messages": 0,
        "user_assistant_pairs_built": 0,
        "messages_skipped_invalid_json": 0,
        "messages_skipped_empty_text": 0,
        "duplicates_omitted": 0,
        "records_written": 0,
        "records_omitted": 0,
    }


def _fetch_count(cursor: Any, query: str, params: tuple = ()) -> int:
    cursor.execute(query, params)
    row = cursor.fetchone()
    if isinstance(row, dict):
        return int(next(iter(row.values())) or 0)
    return int(row[0] if row else 0)


def _rowcount(cursor: Any) -> int:
    return max(int(getattr(cursor, "rowcount", 0) or 0), 0)


def get_table_columns(cursor: Any, schema: str, table: str) -> Set[str]:
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        """,
        (schema, table),
    )
    rows = cursor.fetchall()
    columns = set()
    for row in rows:
        columns.add(row["column_name"] if isinstance(row, dict) else row[0])
    return columns


def _has_columns(cursor: Any, schema: str, table: str, required: Set[str]) -> bool:
    return required.issubset(get_table_columns(cursor, schema, table))


def collect_dry_run_metrics(
    cursor: Any,
    *,
    raw_schema: str,
    target_schema: str,
    limit: Optional[int],
    since_days: int,
    workspace_id: Optional[int],
    agent_id: Optional[str],
) -> Dict[str, Any]:
    raw = safe_schema_name(raw_schema)
    target = safe_schema_name(target_schema)
    metrics = empty_metrics(raw_schema=raw, target_schema=target, limit=limit, since_days=since_days, dry_run=True)
    workspace_filter = "WHERE id = %s" if workspace_id is not None else ""
    workspace_params = (workspace_id,) if workspace_id is not None else ()
    agent_filters: List[str] = []
    agent_params: List[Any] = []
    if workspace_id is not None:
        agent_filters.append("workspace_id = %s")
        agent_params.append(workspace_id)
    if agent_id:
        agent_filters.append("id::text = %s")
        agent_params.append(agent_id)
    agent_where = f"WHERE {' AND '.join(agent_filters)}" if agent_filters else ""

    metrics["workspaces_read"] = _fetch_count(cursor, f"SELECT count(*) FROM {raw}.workspaces {workspace_filter}", workspace_params)
    metrics["agents_read"] = _fetch_count(cursor, f"SELECT count(*) FROM {raw}.agents {agent_where}", tuple(agent_params))
    metrics["chats_read"] = _fetch_count(
        cursor,
        f"""
        SELECT count(*)
        FROM {raw}.agent_chats AS ac
        WHERE (%s IS NULL OR ac.workspace_id = %s)
          AND (
              %s IS NULL OR EXISTS (
                  SELECT 1 FROM {raw}.agents AS a
                  WHERE a.workspace_id = ac.workspace_id
                    AND a.id::text = %s
              )
          )
        """,
        (workspace_id, workspace_id, agent_id, agent_id),
    )
    metrics["faqs_read"] = _fetch_count(
        cursor,
        f"""
        SELECT count(*)
        FROM {raw}.agent_faqs AS f
        LEFT JOIN {raw}.agents AS a
          ON a.id = f.agent_id
        WHERE (%s IS NULL OR a.workspace_id = %s)
          AND (%s IS NULL OR f.agent_id::text = %s)
        """,
        (workspace_id, workspace_id, agent_id, agent_id),
    )

    rows = list(
        iter_chat_messages(
            cursor,
            raw_schema=raw,
            limit=limit,
            since_days=since_days,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
    )
    metrics["messages_read"] = len(rows)
    pair_metrics: Dict[str, int] = defaultdict(int)
    pair_user_assistant_messages(rows, pair_metrics)
    metrics.update(pair_metrics)
    return metrics


def _upsert_workspaces(cursor: Any, raw: str, target: str, workspace_id: Optional[int]) -> int:
    if not _has_columns(cursor, target, "workspaces", {"workspace_id", "name"}):
        return 0
    cursor.execute(
        f"""
        INSERT INTO {target}.workspaces (
            workspace_id, name, category, active, source_created_at,
            source_updated_at, source_deleted_at, loaded_at
        )
        SELECT
            w.id, w.name, w.category,
            COALESCE(w.active, true) AND w.deleted_at IS NULL,
            w.created_at, w.updated_at, w.deleted_at, now()
        FROM {raw}.workspaces AS w
        WHERE (%s IS NULL OR w.id = %s)
        ON CONFLICT (workspace_id) DO UPDATE SET
            name = EXCLUDED.name,
            category = EXCLUDED.category,
            active = EXCLUDED.active,
            source_created_at = EXCLUDED.source_created_at,
            source_updated_at = EXCLUDED.source_updated_at,
            source_deleted_at = EXCLUDED.source_deleted_at,
            loaded_at = now()
        """,
        (workspace_id, workspace_id),
    )
    return _rowcount(cursor)


def _upsert_agents(cursor: Any, raw: str, target: str, workspace_id: Optional[int], agent_id: Optional[str]) -> int:
    if not _has_columns(cursor, target, "agents", {"agent_id", "workspace_id", "model"}):
        return 0
    cursor.execute(
        f"""
        INSERT INTO {target}.agents (
            agent_id, workspace_id, name, model, prompt, start_message,
            model_vector_store_id, active, source_created_at, source_updated_at,
            source_deleted_at, loaded_at
        )
        SELECT
            a.id, a.workspace_id, a.name, a.model, a.prompt, a.start_message,
            a.model_vector_store_id, a.deleted_at IS NULL,
            a.created_at, a.updated_at, a.deleted_at, now()
        FROM {raw}.agents AS a
        JOIN {target}.workspaces AS w
          ON w.workspace_id = a.workspace_id
        WHERE (%s IS NULL OR a.workspace_id = %s)
          AND (%s IS NULL OR a.id::text = %s)
        ON CONFLICT (agent_id) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            name = EXCLUDED.name,
            model = EXCLUDED.model,
            prompt = EXCLUDED.prompt,
            start_message = EXCLUDED.start_message,
            model_vector_store_id = EXCLUDED.model_vector_store_id,
            active = EXCLUDED.active,
            source_created_at = EXCLUDED.source_created_at,
            source_updated_at = EXCLUDED.source_updated_at,
            source_deleted_at = EXCLUDED.source_deleted_at,
            loaded_at = now()
        """,
        (workspace_id, workspace_id, agent_id, agent_id),
    )
    return _rowcount(cursor)


def _upsert_conversations(cursor: Any, raw: str, target: str, workspace_id: Optional[int], agent_id: Optional[str]) -> int:
    required = {"source_agent_chat_id", "workspace_id", "conversation_id", "thread_id"}
    if not _has_columns(cursor, target, "conversations", required):
        return 0
    cursor.execute(
        f"""
        WITH agent_counts AS (
            SELECT
                workspace_id,
                count(*) AS agent_count,
                (array_agg(agent_id ORDER BY agent_id::text))[1] AS inferred_agent_id
            FROM {target}.agents
            GROUP BY workspace_id
        ),
        message_bounds AS (
            SELECT
                agent_chat_id,
                min(created_at) AS first_message_at,
                max(created_at) AS last_message_at
            FROM {raw}.chat_messages
            WHERE agent_chat_id IS NOT NULL
            GROUP BY agent_chat_id
        ),
        source_conversations AS (
            SELECT
                ac.id AS source_agent_chat_id,
                ac.conversation_id,
                ac.thread_id,
                ac.workspace_id,
                CASE WHEN COALESCE(ag.agent_count, 0) = 1 THEN ag.inferred_agent_id END AS agent_id,
                ac.model,
                CASE
                    WHEN NULLIF(btrim(COALESCE(ac.phone, '')), '') IS NULL THEN NULL
                    ELSE 'md5:' || md5(ac.phone)
                END AS phone_hash_or_obfuscated,
                COALESCE(mb.first_message_at, ac.created_at) AS started_at,
                COALESCE(mb.last_message_at, ac.updated_at, ac.created_at) AS ended_at,
                ac.conversation AS raw_conversation,
                ac.thread AS raw_thread,
                ac.created_at AS source_created_at,
                ac.updated_at AS source_updated_at,
                CASE
                    WHEN COALESCE(ag.agent_count, 0) = 1 THEN 'workspace_single_agent'
                    WHEN COALESCE(ag.agent_count, 0) = 0 THEN 'no_agent_for_workspace'
                    ELSE 'multiple_agents_for_workspace'
                END AS agent_inference_method,
                CASE
                    WHEN COALESCE(ag.agent_count, 0) = 1 THEN 1.000
                    WHEN COALESCE(ag.agent_count, 0) = 0 THEN 0.000
                    ELSE 0.400
                END AS agent_inference_confidence,
                CASE
                    WHEN COALESCE(ag.agent_count, 0) = 1 THEN 'agent inferred because workspace has exactly one agent.'
                    WHEN COALESCE(ag.agent_count, 0) = 0 THEN 'no agent found for workspace.'
                    ELSE 'workspace has multiple agents; business rule required.'
                END AS agent_inference_notes
            FROM {raw}.agent_chats AS ac
            LEFT JOIN agent_counts AS ag
              ON ag.workspace_id = ac.workspace_id
            LEFT JOIN message_bounds AS mb
              ON mb.agent_chat_id = ac.id
            WHERE (%s IS NULL OR ac.workspace_id = %s)
              AND (
                  %s IS NULL OR EXISTS (
                      SELECT 1 FROM {target}.agents AS af
                      WHERE af.workspace_id = ac.workspace_id
                        AND af.agent_id::text = %s
                  )
              )
        )
        INSERT INTO {target}.conversations (
            source_agent_chat_id, conversation_id, thread_id, workspace_id,
            agent_id, model, phone_hash_or_obfuscated, started_at, ended_at,
            raw_conversation, raw_thread, source_created_at, source_updated_at,
            agent_inference_method, agent_inference_confidence,
            agent_inference_notes, loaded_at
        )
        SELECT
            source_agent_chat_id, conversation_id, thread_id, workspace_id,
            agent_id, model, phone_hash_or_obfuscated, started_at, ended_at,
            raw_conversation, raw_thread, source_created_at, source_updated_at,
            agent_inference_method, agent_inference_confidence,
            agent_inference_notes, now()
        FROM source_conversations
        ON CONFLICT (source_agent_chat_id) DO UPDATE SET
            conversation_id = EXCLUDED.conversation_id,
            thread_id = EXCLUDED.thread_id,
            workspace_id = EXCLUDED.workspace_id,
            agent_id = EXCLUDED.agent_id,
            model = EXCLUDED.model,
            phone_hash_or_obfuscated = EXCLUDED.phone_hash_or_obfuscated,
            started_at = EXCLUDED.started_at,
            ended_at = EXCLUDED.ended_at,
            raw_conversation = EXCLUDED.raw_conversation,
            raw_thread = EXCLUDED.raw_thread,
            source_created_at = EXCLUDED.source_created_at,
            source_updated_at = EXCLUDED.source_updated_at,
            agent_inference_method = EXCLUDED.agent_inference_method,
            agent_inference_confidence = EXCLUDED.agent_inference_confidence,
            agent_inference_notes = EXCLUDED.agent_inference_notes,
            loaded_at = now()
        """,
        (workspace_id, workspace_id, agent_id, agent_id),
    )
    return _rowcount(cursor)


def _upsert_messages(
    cursor: Any,
    raw: str,
    target: str,
    limit: Optional[int],
    since_days: int,
    workspace_id: Optional[int],
    agent_id: Optional[str],
) -> int:
    required = {"conversation_pk", "source_chat_message_id", "role", "message_type", "raw_message", "extraction_status"}
    if not _has_columns(cursor, target, "messages", required):
        return 0
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=since_days)
    limit_clause = "LIMIT %s" if limit is not None else ""
    params: List[Any] = [since, workspace_id, workspace_id, agent_id, agent_id]
    if limit is not None:
        params.append(limit)
    cursor.execute(
        f"""
        WITH source_messages AS (
            SELECT cm.*
            FROM {raw}.chat_messages AS cm
            JOIN {raw}.agent_chats AS ac
              ON ac.id = cm.agent_chat_id
            WHERE cm.created_at >= %s
              AND (%s IS NULL OR ac.workspace_id = %s)
              AND (
                  %s IS NULL OR EXISTS (
                      SELECT 1 FROM {target}.agents AS af
                      WHERE af.workspace_id = ac.workspace_id
                        AND af.agent_id::text = %s
                  )
              )
            ORDER BY ac.workspace_id, cm.agent_chat_id, cm.created_at, cm.id
            {limit_clause}
        ),
        extracted_messages AS (
            SELECT
                c.conversation_pk,
                cm.id AS source_chat_message_id,
                CASE
                    WHEN lower(cm.message ->> 'role') IN ('user', 'assistant', 'system', 'tool')
                        THEN lower(cm.message ->> 'role')
                    ELSE 'unknown'
                END AS role,
                cm.message_type,
                ext.content_text,
                cm.message AS raw_message,
                cm.model_output AS raw_model_output,
                cm.created_at,
                cm.updated_at,
                CASE
                    WHEN ext.content_text IS NOT NULL THEN 'extracted'
                    WHEN cm.message ? 'content'
                         AND jsonb_typeof(cm.message -> 'content') = 'array'
                        THEN CASE
                            WHEN jsonb_array_length(cm.message -> 'content') = 0 THEN 'empty'
                            ELSE 'failed'
                        END
                    WHEN NOT (cm.message ? 'content') THEN 'empty'
                    ELSE 'failed'
                END AS extraction_status,
                CASE
                    WHEN ext.content_text IS NOT NULL
                        THEN 'Text extracted from known JSON paths.'
                    WHEN cm.message ? 'content'
                        THEN 'No text found in known JSON paths.'
                    ELSE 'Message JSON has no content key.'
                END AS extraction_notes
            FROM source_messages AS cm
            JOIN {target}.conversations AS c
              ON c.source_agent_chat_id = cm.agent_chat_id
            LEFT JOIN LATERAL (
                SELECT NULLIF(
                    btrim(
                        COALESCE(
                            (
                                SELECT string_agg(NULLIF(btrim(part.part_text), ''), E'\n' ORDER BY elem.ordinality)
                                FROM jsonb_array_elements(
                                    CASE
                                        WHEN jsonb_typeof(cm.message -> 'content') = 'array'
                                            THEN cm.message -> 'content'
                                        ELSE '[]'::jsonb
                                    END
                                ) WITH ORDINALITY AS elem(item, ordinality)
                                CROSS JOIN LATERAL (
                                    SELECT COALESCE(
                                        elem.item #>> '{{text,value}}',
                                        CASE
                                            WHEN jsonb_typeof(elem.item -> 'text') = 'string'
                                                THEN elem.item ->> 'text'
                                        END,
                                        elem.item #>> '{{input_text,text}}',
                                        elem.item #>> '{{output_text,text}}',
                                        elem.item #>> '{{content,text}}',
                                        elem.item ->> 'content',
                                        elem.item ->> 'value',
                                        elem.item ->> 'transcript'
                                    ) AS part_text
                                ) AS part
                                WHERE part.part_text IS NOT NULL
                            ),
                            CASE
                                WHEN jsonb_typeof(cm.message -> 'content') = 'string'
                                    THEN cm.message ->> 'content'
                            END,
                            cm.message #>> '{{content,text,value}}',
                            cm.message #>> '{{content,text}}',
                            cm.message ->> 'text',
                            cm.message ->> 'message',
                            cm.message ->> 'value'
                        )
                    ),
                    ''
                ) AS content_text
            ) AS ext ON true
        )
        INSERT INTO {target}.messages (
            conversation_pk, source_chat_message_id, role, message_type,
            content_text, raw_message, raw_model_output, created_at, updated_at,
            extraction_status, extraction_notes, loaded_at
        )
        SELECT
            conversation_pk, source_chat_message_id, role, message_type,
            content_text, raw_message, raw_model_output, created_at, updated_at,
            extraction_status, extraction_notes, now()
        FROM extracted_messages
        ON CONFLICT (source_chat_message_id) DO UPDATE SET
            conversation_pk = EXCLUDED.conversation_pk,
            role = EXCLUDED.role,
            message_type = EXCLUDED.message_type,
            content_text = EXCLUDED.content_text,
            raw_message = EXCLUDED.raw_message,
            raw_model_output = EXCLUDED.raw_model_output,
            created_at = EXCLUDED.created_at,
            updated_at = EXCLUDED.updated_at,
            extraction_status = EXCLUDED.extraction_status,
            extraction_notes = EXCLUDED.extraction_notes,
            loaded_at = now()
        """,
        tuple(params),
    )
    return _rowcount(cursor)


def _upsert_existing_faqs(cursor: Any, raw: str, target: str, workspace_id: Optional[int], agent_id: Optional[str]) -> int:
    if not _has_columns(cursor, target, "existing_faqs", {"faq_id", "source_agent_id", "question"}):
        return 0
    cursor.execute(
        f"""
        WITH source_faqs AS (
            SELECT
                f.id AS faq_id,
                f.agent_id AS source_agent_id,
                a.agent_id,
                a.workspace_id,
                f.question,
                f.answer,
                f.image,
                f.created_at,
                f.updated_at,
                f.deleted_at,
                f.deleted_at IS NULL AS is_active
            FROM {raw}.agent_faqs AS f
            LEFT JOIN {target}.agents AS a
              ON a.agent_id = f.agent_id
            WHERE (%s IS NULL OR a.workspace_id = %s)
              AND (%s IS NULL OR f.agent_id::text = %s)
        )
        INSERT INTO {target}.existing_faqs (
            faq_id, source_agent_id, agent_id, workspace_id, question, answer,
            image, created_at, updated_at, deleted_at, is_active, loaded_at
        )
        SELECT
            faq_id, source_agent_id, agent_id, workspace_id, question, answer,
            image, created_at, updated_at, deleted_at, is_active, now()
        FROM source_faqs
        ON CONFLICT (faq_id) DO UPDATE SET
            source_agent_id = EXCLUDED.source_agent_id,
            agent_id = EXCLUDED.agent_id,
            workspace_id = EXCLUDED.workspace_id,
            question = EXCLUDED.question,
            answer = EXCLUDED.answer,
            image = EXCLUDED.image,
            created_at = EXCLUDED.created_at,
            updated_at = EXCLUDED.updated_at,
            deleted_at = EXCLUDED.deleted_at,
            is_active = EXCLUDED.is_active,
            loaded_at = now()
        """,
        (workspace_id, workspace_id, agent_id, agent_id),
    )
    return _rowcount(cursor)


def _run_with_connection(
    conn: Any,
    *,
    raw_schema: str,
    target_schema: str,
    limit: Optional[int],
    since_days: int,
    workspace_id: Optional[int],
    agent_id: Optional[str],
    dry_run: bool,
) -> Dict[str, Any]:
    raw = safe_schema_name(raw_schema)
    target = safe_schema_name(target_schema)
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        metrics = collect_dry_run_metrics(
            cursor,
            raw_schema=raw,
            target_schema=target,
            limit=limit,
            since_days=since_days,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        metrics["dry_run"] = dry_run
        if dry_run:
            return metrics

        written = 0
        written += _upsert_workspaces(cursor, raw, target, workspace_id)
        written += _upsert_agents(cursor, raw, target, workspace_id, agent_id)
        written += _upsert_conversations(cursor, raw, target, workspace_id, agent_id)
        written += _upsert_messages(cursor, raw, target, limit, since_days, workspace_id, agent_id)
        written += _upsert_existing_faqs(cursor, raw, target, workspace_id, agent_id)
        metrics["records_written"] = written
        return metrics


def run_everwod_ingestion(
    *,
    limit: Optional[int] = None,
    since_days: int = 90,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    dry_run: bool = True,
    raw_schema: str = FAQ_RAW_SCHEMA,
    target_schema: str = FAQ_SCHEMA,
    connection: Optional[Any] = None,
) -> Dict[str, Any]:
    if connection is not None:
        return _run_with_connection(
            connection,
            raw_schema=raw_schema,
            target_schema=target_schema,
            limit=limit,
            since_days=since_days,
            workspace_id=workspace_id,
            agent_id=agent_id,
            dry_run=dry_run,
        )

    with get_db_connection() as conn:
        return _run_with_connection(
            conn,
            raw_schema=raw_schema,
            target_schema=target_schema,
            limit=limit,
            since_days=since_days,
            workspace_id=workspace_id,
            agent_id=agent_id,
            dry_run=dry_run,
        )
