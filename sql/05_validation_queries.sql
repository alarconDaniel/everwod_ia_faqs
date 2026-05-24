/*
  05_validation_queries.sql

  Consultas de validacion despues de ejecutar:
  - 01_create_schemas.sql
  - restaurar pg_dump y moverlo a everwod_raw con 03_load_or_restore_raw_instructions.sql
  - 02_create_tables.sql
  - 04_transform_raw_to_faq_mvp.sql
*/

-- 1. Conteos principales raw vs faq_mvp.
SELECT 'raw.workspaces' AS metric, count(*) AS value FROM everwod_raw.workspaces
UNION ALL SELECT 'mvp.workspaces', count(*) FROM faq_mvp.workspaces
UNION ALL SELECT 'raw.agents', count(*) FROM everwod_raw.agents
UNION ALL SELECT 'mvp.agents', count(*) FROM faq_mvp.agents
UNION ALL SELECT 'raw.agent_chats', count(*) FROM everwod_raw.agent_chats
UNION ALL SELECT 'mvp.conversations', count(*) FROM faq_mvp.conversations
UNION ALL SELECT 'raw.chat_messages', count(*) FROM everwod_raw.chat_messages
UNION ALL SELECT 'mvp.messages', count(*) FROM faq_mvp.messages
UNION ALL SELECT 'raw.agent_faqs', count(*) FROM everwod_raw.agent_faqs
UNION ALL SELECT 'mvp.existing_faqs', count(*) FROM faq_mvp.existing_faqs
ORDER BY metric;

-- 2. Workspaces y agentes.
SELECT
    w.workspace_id,
    w.name,
    w.category,
    w.active,
    count(DISTINCT a.agent_id) AS agents_count,
    count(DISTINCT c.conversation_pk) AS conversations_count,
    count(m.message_pk) AS messages_count
FROM faq_mvp.workspaces AS w
LEFT JOIN faq_mvp.agents AS a
  ON a.workspace_id = w.workspace_id
LEFT JOIN faq_mvp.conversations AS c
  ON c.workspace_id = w.workspace_id
LEFT JOIN faq_mvp.messages AS m
  ON m.conversation_pk = c.conversation_pk
GROUP BY w.workspace_id, w.name, w.category, w.active
ORDER BY messages_count DESC, conversations_count DESC, w.workspace_id;

-- 3. Conversaciones por workspace y metodo de inferencia de agente.
SELECT
    workspace_id,
    agent_inference_method,
    count(*) AS conversations_count
FROM faq_mvp.conversations
GROUP BY workspace_id, agent_inference_method
ORDER BY conversations_count DESC, workspace_id;

-- 4. Mensajes por rol y estado de extraccion.
SELECT
    role,
    extraction_status,
    count(*) AS messages_count,
    count(*) FILTER (WHERE content_text IS NULL) AS without_text
FROM faq_mvp.messages
GROUP BY role, extraction_status
ORDER BY role, extraction_status;

-- 5. Mensajes sin texto extraible.
SELECT
    m.message_pk,
    m.source_chat_message_id,
    c.source_agent_chat_id,
    c.workspace_id,
    c.agent_id,
    m.role,
    m.message_type,
    m.extraction_status,
    m.extraction_notes,
    m.created_at
FROM faq_mvp.messages AS m
JOIN faq_mvp.conversations AS c
  ON c.conversation_pk = m.conversation_pk
WHERE m.content_text IS NULL
ORDER BY m.created_at NULLS LAST
LIMIT 100;

-- 6. FAQs existentes por workspace/agente.
SELECT
    ef.workspace_id,
    ef.agent_id,
    ef.source_agent_id,
    count(*) AS total_faqs,
    count(*) FILTER (WHERE ef.is_active) AS active_faqs,
    count(*) FILTER (WHERE NOT ef.is_active) AS deleted_faqs,
    count(*) FILTER (WHERE ef.answer IS NULL OR btrim(ef.answer) = '') AS faqs_without_answer
FROM faq_mvp.existing_faqs AS ef
GROUP BY ef.workspace_id, ef.agent_id, ef.source_agent_id
ORDER BY total_faqs DESC, ef.workspace_id, ef.agent_id;

-- 7. Conversaciones por workspace con ventana temporal.
SELECT
    workspace_id,
    agent_id,
    count(*) AS conversations_count,
    min(started_at) AS first_started_at,
    max(ended_at) AS last_ended_at
FROM faq_mvp.conversations
GROUP BY workspace_id, agent_id
ORDER BY conversations_count DESC;

-- 8. Distribucion por modelo de conversacion.
SELECT
    model,
    count(*) AS conversations_count
FROM faq_mvp.conversations
GROUP BY model
ORDER BY conversations_count DESC, model;

-- 9. Distribucion por message_type y rol.
SELECT
    message_type,
    role,
    count(*) AS messages_count
FROM faq_mvp.messages
GROUP BY message_type, role
ORDER BY messages_count DESC, message_type, role;

-- 10. Duplicados potenciales de IDs de origen en el modelo.
SELECT 'conversations.source_agent_chat_id' AS key_name, source_agent_chat_id::text AS key_value, count(*) AS duplicates
FROM faq_mvp.conversations
GROUP BY source_agent_chat_id
HAVING count(*) > 1
UNION ALL
SELECT 'messages.source_chat_message_id', source_chat_message_id, count(*)
FROM faq_mvp.messages
GROUP BY source_chat_message_id
HAVING count(*) > 1
UNION ALL
SELECT 'existing_faqs.faq_id', faq_id::text, count(*)
FROM faq_mvp.existing_faqs
GROUP BY faq_id
HAVING count(*) > 1;

-- 11. Integridad referencial esperada en el modelo.
SELECT 'messages_without_conversation' AS issue, count(*) AS value
FROM faq_mvp.messages AS m
LEFT JOIN faq_mvp.conversations AS c
  ON c.conversation_pk = m.conversation_pk
WHERE c.conversation_pk IS NULL
UNION ALL
SELECT 'conversations_without_workspace', count(*)
FROM faq_mvp.conversations AS c
LEFT JOIN faq_mvp.workspaces AS w
  ON w.workspace_id = c.workspace_id
WHERE w.workspace_id IS NULL
UNION ALL
SELECT 'agents_without_workspace', count(*)
FROM faq_mvp.agents AS a
LEFT JOIN faq_mvp.workspaces AS w
  ON w.workspace_id = a.workspace_id
WHERE w.workspace_id IS NULL
UNION ALL
SELECT 'faqs_with_unresolved_agent', count(*)
FROM faq_mvp.existing_faqs
WHERE agent_id IS NULL;

-- 12. Hallazgos de calidad de datos registrados por la transformacion.
SELECT
    issue_type,
    severity,
    source_table,
    count(*) AS issue_count
FROM faq_mvp.data_quality_issues
GROUP BY issue_type, severity, source_table
ORDER BY severity DESC, issue_count DESC, issue_type;

-- 13. Chats raw sin mensajes y mensajes raw sin chat asociado.
SELECT 'raw_agent_chats_without_messages' AS metric, count(*) AS value
FROM everwod_raw.agent_chats AS ac
LEFT JOIN everwod_raw.chat_messages AS cm
  ON cm.agent_chat_id = ac.id
WHERE cm.id IS NULL
UNION ALL
SELECT 'raw_chat_messages_without_agent_chat', count(*)
FROM everwod_raw.chat_messages AS cm
LEFT JOIN everwod_raw.agent_chats AS ac
  ON ac.id = cm.agent_chat_id
WHERE ac.id IS NULL;

-- 14. Posibles preguntas repetidas de usuario, sin insertar candidatos.
-- Esta consulta es solo exploratoria y deterministica; el pipeline semantico
-- posterior debe comparar contra FAQs existentes antes de crear candidatos.
WITH normalized_user_messages AS (
    SELECT
        c.workspace_id,
        c.agent_id,
        lower(regexp_replace(btrim(m.content_text), '[[:space:]]+', ' ', 'g')) AS normalized_question,
        min(m.created_at) AS first_seen_at,
        max(m.created_at) AS last_seen_at,
        count(*) AS recurrence_count
    FROM faq_mvp.messages AS m
    JOIN faq_mvp.conversations AS c
      ON c.conversation_pk = m.conversation_pk
    WHERE m.role = 'user'
      AND m.content_text IS NOT NULL
      AND length(btrim(m.content_text)) >= 3
    GROUP BY c.workspace_id, c.agent_id, lower(regexp_replace(btrim(m.content_text), '[[:space:]]+', ' ', 'g'))
)
SELECT
    workspace_id,
    agent_id,
    left(normalized_question, 180) AS normalized_question_preview,
    recurrence_count,
    first_seen_at,
    last_seen_at
FROM normalized_user_messages
WHERE recurrence_count >= 3
ORDER BY recurrence_count DESC, last_seen_at DESC
LIMIT 100;

-- 15. Estado de tablas futuras del pipeline.
SELECT 'faq_candidates' AS table_name, count(*) AS rows_count FROM faq_mvp.faq_candidates
UNION ALL SELECT 'faq_candidate_examples', count(*) FROM faq_mvp.faq_candidate_examples
UNION ALL SELECT 'message_embeddings', count(*) FROM faq_mvp.message_embeddings
UNION ALL SELECT 'question_clusters', count(*) FROM faq_mvp.question_clusters
UNION ALL SELECT 'pipeline_runs', count(*) FROM faq_mvp.pipeline_runs
UNION ALL SELECT 'pipeline_metrics', count(*) FROM faq_mvp.pipeline_metrics
UNION ALL SELECT 'faq_validation_events', count(*) FROM faq_mvp.faq_validation_events
ORDER BY table_name;
