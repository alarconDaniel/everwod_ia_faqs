/*
  04_transform_raw_to_faq_mvp.sql

  Pobla faq_mvp desde everwod_raw usando solo datos reales del pg_dump.
  El script es idempotente: usa UPSERT y no elimina candidatos, metricas ni
  resultados futuros del pipeline.
*/

DO $$
BEGIN
    IF to_regclass('everwod_raw.workspaces') IS NULL
       OR to_regclass('everwod_raw.agents') IS NULL
       OR to_regclass('everwod_raw.agent_chats') IS NULL
       OR to_regclass('everwod_raw.chat_messages') IS NULL
       OR to_regclass('everwod_raw.agent_faqs') IS NULL THEN
        RAISE EXCEPTION
          'Faltan tablas raw. Restaure el pg_dump y mueva public -> everwod_raw con 03_load_or_restore_raw_instructions.sql antes de transformar.';
    END IF;
END $$;

BEGIN;

INSERT INTO faq_mvp.workspaces (
    workspace_id,
    name,
    category,
    active,
    source_created_at,
    source_updated_at,
    source_deleted_at,
    loaded_at
)
SELECT
    w.id,
    w.name,
    w.category,
    COALESCE(w.active, true) AND w.deleted_at IS NULL AS active,
    w.created_at,
    w.updated_at,
    w.deleted_at,
    now()
FROM everwod_raw.workspaces AS w
ON CONFLICT (workspace_id) DO UPDATE SET
    name = EXCLUDED.name,
    category = EXCLUDED.category,
    active = EXCLUDED.active,
    source_created_at = EXCLUDED.source_created_at,
    source_updated_at = EXCLUDED.source_updated_at,
    source_deleted_at = EXCLUDED.source_deleted_at,
    loaded_at = now();

INSERT INTO faq_mvp.agents (
    agent_id,
    workspace_id,
    name,
    model,
    prompt,
    start_message,
    model_vector_store_id,
    active,
    source_created_at,
    source_updated_at,
    source_deleted_at,
    loaded_at
)
SELECT
    a.id,
    a.workspace_id,
    a.name,
    a.model,
    a.prompt,
    a.start_message,
    a.model_vector_store_id,
    a.deleted_at IS NULL AS active,
    a.created_at,
    a.updated_at,
    a.deleted_at,
    now()
FROM everwod_raw.agents AS a
JOIN faq_mvp.workspaces AS w
  ON w.workspace_id = a.workspace_id
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
    loaded_at = now();

WITH agent_counts AS (
    SELECT
        workspace_id,
        count(*) AS agent_count,
		(array_agg(agent_id ORDER BY agent_id::text))[1] AS inferred_agent_id
    FROM faq_mvp.agents
    GROUP BY workspace_id
),
message_bounds AS (
    SELECT
        agent_chat_id,
        min(created_at) AS first_message_at,
        max(created_at) AS last_message_at
    FROM everwod_raw.chat_messages
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
        ac.phone AS phone_hash_or_obfuscated,
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
            WHEN COALESCE(ag.agent_count, 0) = 1
                THEN 'agent_id inferido porque el workspace tiene exactamente un agente en el dump.'
            WHEN COALESCE(ag.agent_count, 0) = 0
                THEN 'No existe agente en el dump para este workspace; agent_id queda NULL.'
            ELSE 'El workspace tiene multiples agentes; se requiere regla de negocio adicional.'
        END AS agent_inference_notes
    FROM everwod_raw.agent_chats AS ac
    LEFT JOIN agent_counts AS ag
      ON ag.workspace_id = ac.workspace_id
    LEFT JOIN message_bounds AS mb
      ON mb.agent_chat_id = ac.id
)
INSERT INTO faq_mvp.conversations (
    source_agent_chat_id,
    conversation_id,
    thread_id,
    workspace_id,
    agent_id,
    model,
    phone_hash_or_obfuscated,
    started_at,
    ended_at,
    raw_conversation,
    raw_thread,
    source_created_at,
    source_updated_at,
    agent_inference_method,
    agent_inference_confidence,
    agent_inference_notes,
    loaded_at
)
SELECT
    source_agent_chat_id,
    conversation_id,
    thread_id,
    workspace_id,
    agent_id,
    model,
    phone_hash_or_obfuscated,
    started_at,
    ended_at,
    raw_conversation,
    raw_thread,
    source_created_at,
    source_updated_at,
    agent_inference_method,
    agent_inference_confidence,
    agent_inference_notes,
    now()
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
    loaded_at = now();

WITH extracted_messages AS (
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
                THEN 'Texto extraido desde rutas JSON conocidas del mensaje.'
            WHEN cm.message ? 'content'
                THEN 'No se encontro texto en rutas JSON conocidas: content string, content[].text.value, content[].text, content[].content, text, message o value.'
            ELSE 'El JSON del mensaje no contiene la clave content.'
        END AS extraction_notes
    FROM everwod_raw.chat_messages AS cm
    JOIN faq_mvp.conversations AS c
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
                                elem.item #>> '{text,value}',
                                CASE
                                    WHEN jsonb_typeof(elem.item -> 'text') = 'string'
                                        THEN elem.item ->> 'text'
                                END,
                                elem.item #>> '{input_text,text}',
                                elem.item #>> '{output_text,text}',
                                elem.item #>> '{content,text}',
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
                    cm.message #>> '{content,text,value}',
                    cm.message #>> '{content,text}',
                    cm.message ->> 'text',
                    cm.message ->> 'message',
                    cm.message ->> 'value'
                )
            ),
            ''
        ) AS content_text
    ) AS ext ON true
)
INSERT INTO faq_mvp.messages (
    conversation_pk,
    source_chat_message_id,
    role,
    message_type,
    content_text,
    raw_message,
    raw_model_output,
    created_at,
    updated_at,
    extraction_status,
    extraction_notes,
    loaded_at
)
SELECT
    conversation_pk,
    source_chat_message_id,
    role,
    message_type,
    content_text,
    raw_message,
    raw_model_output,
    created_at,
    updated_at,
    extraction_status,
    extraction_notes,
    now()
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
    loaded_at = now();

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
    FROM everwod_raw.agent_faqs AS f
    LEFT JOIN faq_mvp.agents AS a
      ON a.agent_id = f.agent_id
)
INSERT INTO faq_mvp.existing_faqs (
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
SELECT
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
    now()
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
    loaded_at = now();

-- Hallazgos reproducibles de calidad de datos. No corrigen ni inventan datos.
INSERT INTO faq_mvp.data_quality_issues (
    source_table,
    source_pk,
    faq_mvp_table,
    faq_mvp_pk,
    issue_type,
    severity,
    details
)
SELECT
    'agent_chats',
    c.source_agent_chat_id::text,
    'conversations',
    c.conversation_pk::text,
    'agent_not_inferred',
    'warning',
    jsonb_build_object(
        'workspace_id', c.workspace_id,
        'agent_inference_method', c.agent_inference_method,
        'agent_inference_notes', c.agent_inference_notes
    )
FROM faq_mvp.conversations AS c
WHERE c.agent_id IS NULL
ON CONFLICT DO NOTHING;

INSERT INTO faq_mvp.data_quality_issues (
    source_table,
    source_pk,
    faq_mvp_table,
    faq_mvp_pk,
    issue_type,
    severity,
    details
)
SELECT
    'agent_faqs',
    ef.faq_id::text,
    'existing_faqs',
    ef.faq_id::text,
    'missing_agent_reference',
    'warning',
    jsonb_build_object(
        'source_agent_id', ef.source_agent_id,
        'reason', 'agent_faqs.agent_id no existe en everwod_raw.agents'
    )
FROM faq_mvp.existing_faqs AS ef
WHERE ef.agent_id IS NULL
ON CONFLICT DO NOTHING;

INSERT INTO faq_mvp.data_quality_issues (
    source_table,
    source_pk,
    faq_mvp_table,
    faq_mvp_pk,
    issue_type,
    severity,
    details
)
SELECT
    'chat_messages',
    m.source_chat_message_id,
    'messages',
    m.message_pk::text,
    'message_text_not_extracted',
    CASE WHEN m.extraction_status = 'empty' THEN 'info' ELSE 'warning' END,
    jsonb_build_object(
        'role', m.role,
        'message_type', m.message_type,
        'extraction_status', m.extraction_status,
        'extraction_notes', m.extraction_notes
    )
FROM faq_mvp.messages AS m
WHERE m.content_text IS NULL
ON CONFLICT DO NOTHING;

COMMIT;

/*
  Nota:
  faq_mvp.faq_candidates queda vacia en esta fase. Insertar candidatos requiere
  un pipeline posterior de embeddings/clustering/LLM o una regla deterministica
  acordada. Este script solo prepara la estructura y carga datos reales.
*/
