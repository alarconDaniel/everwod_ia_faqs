# Diccionario de datos - `faq_mvp`

Convencion de fuente:

- **pg_dump**: valor copiado desde `everwod_raw`.
- **derivado**: valor derivado directamente desde datos reales del dump.
- **calculado**: valor generado por la base o por el pipeline tecnico.
- **pendiente**: sera llenado por pipeline futuro, LLM/embeddings o validacion humana.

## `faq_mvp.workspaces`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| workspace_id | bigint | pg_dump | `everwod_raw.workspaces.id`. |
| name | varchar(255) | pg_dump | Nombre del workspace/cliente. |
| category | varchar(255) | pg_dump | Categoria del negocio si existe. |
| active | boolean | derivado | `workspaces.active AND deleted_at IS NULL`. |
| source_created_at | timestamp | pg_dump | Fecha original de creacion. |
| source_updated_at | timestamp | pg_dump | Fecha original de actualizacion. |
| source_deleted_at | timestamp | pg_dump | Eliminacion logica original. |
| loaded_at | timestamptz | calculado | Momento de carga al modelo MVP. |

## `faq_mvp.agents`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| agent_id | uuid | pg_dump | `everwod_raw.agents.id`. |
| workspace_id | bigint | pg_dump | Workspace del agente. |
| name | varchar(255) | pg_dump | Nombre del agente si existe. |
| model | varchar(255) | pg_dump | Modelo configurado en el agente. |
| prompt | text | pg_dump | Prompt del agente; en el dump esta nulo. |
| start_message | varchar(5000) | pg_dump | Mensaje inicial configurado. |
| model_vector_store_id | varchar(255) | pg_dump | Vector store asociado en proveedor/modelo. |
| active | boolean | derivado | `deleted_at IS NULL`. |
| source_created_at | timestamp | pg_dump | Fecha original de creacion. |
| source_updated_at | timestamp | pg_dump | Fecha original de actualizacion. |
| source_deleted_at | timestamp | pg_dump | Eliminacion logica original. |
| loaded_at | timestamptz | calculado | Momento de carga al modelo MVP. |

## `faq_mvp.conversations`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| conversation_pk | bigint identity | calculado | Clave interna del modelo MVP. |
| source_agent_chat_id | uuid | pg_dump | `everwod_raw.agent_chats.id`. |
| conversation_id | varchar(255) | pg_dump | ID de conversacion externo si existe. |
| thread_id | varchar(255) | pg_dump | ID de thread externo si existe. |
| workspace_id | bigint | pg_dump | Workspace original de `agent_chats`. |
| agent_id | uuid | derivado | Inferido por workspace cuando hay exactamente un agente. |
| model | varchar(255) | pg_dump | Modelo registrado en `agent_chats`. |
| phone_hash_or_obfuscated | varchar(255) | pg_dump | Telefono obfuscado del dump; no se desanonimiza. |
| started_at | timestamp | derivado | Primer mensaje de la conversacion o `agent_chats.created_at`. |
| ended_at | timestamp | derivado | Ultimo mensaje de la conversacion o `agent_chats.updated_at`. |
| raw_conversation | jsonb | pg_dump | JSONB original `agent_chats.conversation`. |
| raw_thread | jsonb | pg_dump | JSONB original `agent_chats.thread`. |
| source_created_at | timestamp | pg_dump | Fecha original de creacion. |
| source_updated_at | timestamp | pg_dump | Fecha original de actualizacion. |
| agent_inference_method | varchar(100) | derivado | Regla usada para inferir agente. |
| agent_inference_confidence | numeric(4,3) | derivado | Confianza de inferencia entre 0 y 1. |
| agent_inference_notes | text | derivado | Explicacion de la inferencia o ausencia. |
| loaded_at | timestamptz | calculado | Momento de carga al modelo MVP. |

## `faq_mvp.messages`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| message_pk | bigint identity | calculado | Clave interna del mensaje. |
| conversation_pk | bigint | derivado | FK a conversacion MVP por `agent_chat_id`. |
| source_chat_message_id | varchar(255) | pg_dump | `everwod_raw.chat_messages.id`. |
| role | varchar(20) | derivado | `message->>'role'` normalizado a user/assistant/system/tool/unknown. |
| message_type | varchar(255) | pg_dump | Tipo original del mensaje. |
| content_text | text | derivado | Texto extraido desde rutas JSON conocidas; NULL si no existe. |
| raw_message | jsonb | pg_dump | JSONB original `chat_messages.message`. |
| raw_model_output | jsonb | pg_dump | JSONB original `chat_messages.model_output`; nulo en el dump perfilado. |
| created_at | timestamp | pg_dump | Fecha original del mensaje. |
| updated_at | timestamp | pg_dump | Fecha original de actualizacion. |
| extraction_status | varchar(20) | derivado | `extracted`, `empty` o `failed`. |
| extraction_notes | text | derivado | Nota sobre ruta de extraccion o fallo. |
| loaded_at | timestamptz | calculado | Momento de carga al modelo MVP. |

## `faq_mvp.existing_faqs`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| faq_id | uuid | pg_dump / calculado | `everwod_raw.agent_faqs.id`; para FAQs aprobadas por el MVP se usa el `candidate_id` promovido. |
| source_agent_id | uuid | pg_dump / calculado | `agent_faqs.agent_id` original; para FAQs aprobadas se usa el `agent_id` del candidato validado. |
| agent_id | uuid | derivado | FK resuelta contra `faq_mvp.agents`; NULL si falta el agente. |
| workspace_id | bigint | derivado | Workspace del agente resuelto. |
| question | text | pg_dump | Pregunta frecuente real. |
| answer | text | pg_dump | Respuesta frecuente real. |
| image | varchar(255) | pg_dump | Imagen asociada si existe. |
| created_at | timestamp | pg_dump | Fecha original de creacion. |
| updated_at | timestamp | pg_dump | Fecha original de actualizacion. |
| deleted_at | timestamp | pg_dump | Eliminacion logica. |
| is_active | boolean | derivado | `deleted_at IS NULL`. |
| loaded_at | timestamptz | calculado | Momento de carga al modelo MVP o de promocion desde validacion humana. |

## `faq_mvp.faq_candidates`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| candidate_id | uuid | calculado | ID generado por PostgreSQL. |
| workspace_id | bigint | pendiente | Workspace del candidato generado por pipeline. |
| agent_id | uuid | pendiente | Agente relacionado si el pipeline puede asignarlo. |
| cluster_id | uuid | pendiente | Cluster que origina la sugerencia. |
| normalized_question | text | pendiente | Pregunta representativa normalizada. |
| suggested_answer | text | pendiente | Respuesta sugerida; puede quedar NULL hasta LLM/validacion. |
| cluster_label | varchar(255) | pendiente | Etiqueta humana o tecnica del cluster. |
| recurrence_count | integer | pendiente | Numero de mensajes/evidencias similares. |
| first_seen_at | timestamp | pendiente | Primera aparicion real entre mensajes. |
| last_seen_at | timestamp | pendiente | Ultima aparicion real entre mensajes. |
| status | varchar(30) | calculado | Estado de revision: pending/approved/rejected/needs_review. |
| human_reviewed_by | varchar(255) | pendiente | Identificador del revisor humano autorizado. |
| human_reviewed_at | timestamptz | pendiente | Momento de revision humana. |
| created_at | timestamptz | calculado | Creacion del candidato. |
| updated_at | timestamptz | calculado | Ultima actualizacion del candidato. |
| candidate_metadata | jsonb | pendiente | Parametros, scores o explicaciones del pipeline. |

## `faq_mvp.faq_candidate_examples`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| example_id | bigint identity | calculado | Clave interna del ejemplo. |
| candidate_id | uuid | pendiente | Candidato asociado. |
| message_pk | bigint | pendiente | Mensaje de usuario real usado como ejemplo. |
| conversation_pk | bigint | pendiente | Conversacion del ejemplo. |
| original_user_message | text | pendiente | Texto real del usuario. |
| assistant_response | text | pendiente | Respuesta del asistente asociada si puede inferirse. |
| similarity_score | numeric(7,6) | pendiente | Similitud del ejemplo al cluster/candidato. |
| created_at | timestamptz | calculado | Momento de insercion. |

## `faq_mvp.pipeline_runs`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| run_id | uuid | calculado | ID de ejecucion del pipeline. |
| workspace_id | bigint | pendiente | Workspace procesado; NULL si es corrida global. |
| started_at | timestamptz | calculado | Inicio de ejecucion. |
| finished_at | timestamptz | pendiente | Fin de ejecucion. |
| status | varchar(30) | calculado | pending/running/succeeded/failed/cancelled. |
| parameters | jsonb | pendiente | Parametros de embeddings, clustering, filtros, modelos. |
| notes | text | pendiente | Observaciones tecnicas. |

## `faq_mvp.pipeline_metrics`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| metric_id | bigint identity | calculado | Clave interna. |
| run_id | uuid | pendiente | Ejecucion relacionada. |
| metric_name | varchar(150) | pendiente | Nombre de metrica. |
| metric_value | numeric | pendiente | Valor numerico. |
| metric_metadata | jsonb | pendiente | Detalle adicional. |
| created_at | timestamptz | calculado | Momento de registro. |

## `faq_mvp.message_embeddings`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| message_embedding_id | uuid | calculado | ID del embedding. |
| message_pk | bigint | pendiente | Mensaje real embebido. |
| workspace_id | bigint | pendiente | Workspace del mensaje. |
| agent_id | uuid | pendiente | Agente del mensaje si existe. |
| embedding_model | varchar(255) | pendiente | Modelo usado para embeddings. |
| embedding_dimension | integer | pendiente | Dimension del vector. |
| embedding | double precision[] | pendiente | Vector; se usa arreglo para no depender de pgvector. |
| embedding_metadata | jsonb | pendiente | Normalizacion, version de modelo, hashes o scores. |
| created_at | timestamptz | calculado | Momento de insercion. |

## `faq_mvp.question_clusters`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| cluster_id | uuid | calculado | ID del cluster. |
| run_id | uuid | pendiente | Corrida que genero el cluster. |
| workspace_id | bigint | pendiente | Workspace del cluster. |
| agent_id | uuid | pendiente | Agente relacionado si aplica. |
| cluster_label | varchar(255) | pendiente | Etiqueta del cluster. |
| algorithm | varchar(100) | pendiente | Algoritmo usado, por ejemplo HDBSCAN/KMeans. |
| representative_question | text | pendiente | Pregunta representativa real o sintetizada por pipeline. |
| centroid_embedding | double precision[] | pendiente | Centroide si el algoritmo lo produce. |
| cluster_size | integer | pendiente | Cantidad de mensajes/preguntas del cluster. |
| cluster_metadata | jsonb | pendiente | Parametros y scores del cluster. |
| created_at | timestamptz | calculado | Momento de creacion. |

## `faq_mvp.faq_validation_events`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| validation_event_id | uuid | calculado | ID del evento. |
| candidate_id | uuid | pendiente | Candidato validado/comentado. |
| event_type | varchar(40) | pendiente | Tipo de evento de validacion. |
| previous_status | varchar(30) | pendiente | Estado anterior. |
| new_status | varchar(30) | pendiente | Estado nuevo. |
| reviewer_identifier | varchar(255) | pendiente | Usuario/revisor autorizado. |
| notes | text | pendiente | Comentarios de revision. |
| created_at | timestamptz | calculado | Momento del evento. |

## `faq_mvp.data_quality_issues`

| Columna | Tipo | Fuente | Descripcion |
|---|---|---|---|
| issue_id | uuid | calculado | ID del hallazgo. |
| source_schema | varchar(100) | calculado | Schema origen, por defecto `everwod_raw`. |
| source_table | varchar(100) | derivado | Tabla origen relacionada. |
| source_pk | text | derivado | Clave origen como texto. |
| faq_mvp_table | varchar(100) | derivado | Tabla MVP relacionada si aplica. |
| faq_mvp_pk | text | derivado | Clave MVP relacionada si aplica. |
| issue_type | varchar(100) | derivado | Tipo de problema reproducible. |
| severity | varchar(20) | derivado | info/warning/error. |
| details | jsonb | derivado | Detalles del hallazgo. |
| created_at | timestamptz | calculado | Momento de registro. |
