# Analisis del pg_dump de Everwod

Fuente principal inspeccionada: `pgdump_production_08-04-2026-22-02-09.sql`.

El dump es un SQL plano generado desde PostgreSQL 17.7 con `pg_dump` 17.9. Usa `COPY ... FROM stdin`, crea objetos en `public`, contiene `DROP TABLE IF EXISTS public.*` y debe restaurarse con `psql` en una base limpia antes de mover las tablas a `everwod_raw`.

## Tablas encontradas

| Tabla origen | Filas | Relevancia para MVP |
|---|---:|---|
| accounting | 1.258 | Contexto operativo/financiero, no central para FAQs |
| agent_chats | 7.967 | Central: conversaciones/hilos por workspace |
| agent_faqs | 212 | Central: FAQs existentes reales |
| agent_files | 82 | Contexto documental de agentes |
| agent_mcps | 2 | Integraciones/herramientas de agentes |
| agent_runs | 8.215 | Ejecuciones de modelo por thread |
| agent_texts | 19 | Textos/base de conocimiento de agentes |
| agents | 28 | Central: agente por workspace |
| audiences | 7 | WhatsApp/campanas |
| chat_messages | 35.124 | Central: mensajes usuario/asistente |
| chat_responses | 0 | Tabla presente pero sin datos en el dump |
| events | 0 | Operativo, sin datos |
| financial_events | 9 | Catalogo financiero |
| lead_schedule | 213 | Operativo |
| leads | 3.714 | Clientes finales/leads, con PII obfuscada |
| mailboxes | 26 | Mensajes de mailbox |
| payment_methods | 10 | Catalogo |
| payment_options | 4 | Opciones de pago por workspace |
| payments | 11.894 | Operativo/financiero |
| plan_user | 0 | Operativo, sin datos |
| plans | 275 | Planes por workspace |
| product_pictures | 166 | Productos |
| products | 103 | Productos por workspace |
| roles | 4 | Catalogo de roles |
| schedule_configs | 1 | Configuracion agenda |
| schedules | 906 | Agendas por workspace |
| services | 7 | Servicios por workspace |
| terms | 7 | Catalogo |
| user_schedule | 78.616 | Reservas/agendas |
| user_workspace | 3.899 | Relacion usuarios-workspaces |
| users | 3.886 | Usuarios, con PII obfuscada/vacia |
| whatsapp_campaigns | 4 | Campanas WhatsApp |
| whatsapp_configs | 13 | Configuracion WhatsApp, tokens vacios/obfuscados |
| whatsapp_templates | 7 | Plantillas WhatsApp |
| workspace_configs | 73 | Configuracion por usuario/workspace |
| workspaces | 51 | Central: clientes/empresas |

## Relaciones reales o inferidas

Relaciones declaradas por FK en el dump:

- `agents.workspace_id -> workspaces.id`
- `agent_chats.workspace_id -> workspaces.id`
- `chat_messages.agent_chat_id -> agent_chats.id`
- `agent_mcps.agent_id -> agents.id`
- Multiples relaciones operativas hacia `workspaces`, `users`, `plans`, `products`, `services`, `schedules`, `whatsapp_templates` y `audiences`.

Relaciones relevantes no declaradas como FK:

- `agent_faqs.agent_id` apunta logicamente a `agents.id`, pero el dump no trae FK ni PK declarada para `agent_faqs`.
- `agent_files.agent_id` y `agent_texts.agent_id` tambien apuntan logicamente a `agents.id`, sin FK declarada.
- `agent_chats` no tiene `agent_id`; solo `workspace_id`.

Inferencia usada en `faq_mvp.conversations.agent_id`:

- Hay 28 workspaces con agentes y cada uno tiene exactamente 1 agente.
- 7.953 de 7.967 conversaciones caen en workspaces con exactamente 1 agente.
- 14 conversaciones pertenecen a workspaces sin agente en el dump; se conservan con `agent_id = NULL` y se registran en `faq_mvp.data_quality_issues`.
- No se encontraron workspaces con multiples agentes en este dump, pero el modelo deja la columna nullable por seguridad.

## Perfil de conversaciones y mensajes

`agent_chats`:

- 7.967 conversaciones.
- `thread_id` no nulo en 4.806 filas.
- `conversation_id` no nulo en 3.161 filas.
- `phone` no nulo en 7.321 filas; el archivo auxiliar lo marca como obfuscado.
- `thread` JSONB no nulo en 1.368 filas, con claves observadas: `id`, `object`, `metadata`, `created_at`, `tool_resources`.
- `conversation` JSONB no nulo en 3.161 filas, con claves observadas: `id`, `object`, `metadata`, `created_at`.
- Modelos observados: `gpt-4o` 4.805, `gpt-5` 2.736, `gpt-5.4` 420, `gpt-5.2` 6.

`chat_messages`:

- 35.124 mensajes.
- Roles observados dentro de `message`: `user` 17.748 y `assistant` 17.376.
- `message_type`: todas las filas observadas son `text`.
- Claves JSON de `message`: `role` y `content` en todas las filas.
- `content` es arreglo en todas las filas; 35.113 elementos tienen texto en `content[].text.value`.
- `model_output` esta nulo en todas las filas del dump.

El transform extrae texto desde rutas defensivas, priorizando:

- `content[].text.value`
- `content[].text`
- `content[].input_text.text`
- `content[].output_text.text`
- `content[].content.text`
- `content[].content`
- `content[].value`
- `content[].transcript`
- `content` como string
- `content.text.value`, `content.text`, `text`, `message`, `value`

Si no encuentra texto, deja `content_text = NULL` y registra estado/nota de extraccion.

## FAQs existentes

`agent_faqs` contiene 212 filas:

- 137 activas (`deleted_at IS NULL`).
- 75 eliminadas logicamente (`deleted_at IS NOT NULL`).
- `image` esta vacio/nulo en las filas perfiladas.
- 15 `agent_id` distintos en FAQs.
- 3 filas referencian `agent_id` que no existe en `agents`; se preservan usando `source_agent_id` y `agent_id = NULL` en `faq_mvp.existing_faqs`.

## Campos obfuscados, vacios o sensibles

Segun `everwod_db_structure.ts` y el dump:

- Obfuscados: telefonos, emails, nombres de usuarios/leads, direcciones, algunos identificadores de WhatsApp/Meta.
- Vacios o no necesarios para el MVP: passwords, remember_token, access_token, code, business_pin, imagenes en varias tablas, datos medicos/personales vacios.
- La solucion no intenta reconstruir PII ni usar secretos. Los valores obfuscados se conservan como obfuscados para trazabilidad.

## Inconsistencias y riesgos de calidad

- El documento del reto habla de formatos conversacionales mixtos. En este dump, `chat_messages.message` aparece bastante uniforme, pero `agent_chats.thread` y `agent_chats.conversation` son parcialmente nulos y los modelos mezclan `gpt-4o`, `gpt-5`, `gpt-5.2` y `gpt-5.4`.
- `chat_responses` existe pero esta vacia; no se usa para poblar mensajes.
- `agent_faqs`, `agent_files` y `agent_texts` no tienen PK/FK declaradas en el dump, aunque sus IDs son unicos en los datos inspeccionados.
- Algunas FAQs referencian agentes ausentes.
- No hay relacion directa `agent_chat -> agent`; se infiere por workspace unico.
- Los textos vistos desde consola Windows pueden verse con mojibake por codificacion de salida; el dump declara `client_encoding = 'UTF8'` y debe restaurarse como UTF-8.

## Oportunidades de normalizacion

- Separar `workspaces`, `agents`, `conversations`, `messages` y `existing_faqs` para consultas multiempresa limpias.
- Conservar `raw_message`, `raw_thread` y `raw_conversation` como JSONB para trazabilidad y para soportar cambios de formato.
- Registrar `extraction_status` y `data_quality_issues` para no perder mensajes dificiles.
- Preparar tablas vacias para `message_embeddings`, `question_clusters`, `faq_candidates`, ejemplos, metricas y eventos de validacion humana sin inventar candidatos.
