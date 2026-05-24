# Everwod FAQ MVP - PostgreSQL

Solucion reproducible para organizar el pg_dump anonimizado de Everwod y preparar una base PostgreSQL para el MVP de deteccion automatica de patrones conversacionales y sugerencias de FAQs.

La prioridad del diseño es: trazabilidad, cero datos inventados, separacion multiworkspace y preparacion para un pipeline posterior de embeddings, clustering, LLM y validacion humana.

## Archivos generados

```text
sql/
  00_create_database_optional.sql
  01_create_schemas.sql
  02_create_tables.sql
  03_load_or_restore_raw_instructions.sql
  04_transform_raw_to_faq_mvp.sql
  05_validation_queries.sql
docs/
  data_dictionary.md
  source_schema_analysis.md
README.md
```

## Orden recomendado de ejecucion

El dump crea tablas en `public`, contiene `DROP TABLE IF EXISTS public.*` y usa `COPY ... FROM stdin`, por lo que debe restaurarse con `psql` 17+ o con el PSQL Tool de pgAdmin4, no con el Query Tool normal.

1. Crear una base limpia.

   ```powershell
   psql -U postgres -d postgres -f "D:\U\SeptimoSemestre\IA\Proyecto Integrador\sql\00_create_database_optional.sql"
   ```

   El archivo `00` viene comentado. En pgAdmin4 tambien puede crear la base manualmente y omitirlo.

2. Crear schemas y extension.

   ```powershell
   psql -U postgres -d everwod_faq_mvp -f "D:\U\SeptimoSemestre\IA\Proyecto Integrador\sql\01_create_schemas.sql"
   ```

3. Restaurar el pg_dump en la base limpia.

   ```powershell
   psql -U postgres -d everwod_faq_mvp -f "D:\U\SeptimoSemestre\IA\Proyecto Integrador\pgdump_production_08-04-2026-22-02-09.sql"
   ```

4. Mover las tablas restauradas desde `public` hacia `everwod_raw`.

   Ejecute en pgAdmin4 Query Tool o psql:

   ```powershell
   psql -U postgres -d everwod_faq_mvp -f "D:\U\SeptimoSemestre\IA\Proyecto Integrador\sql\03_load_or_restore_raw_instructions.sql"
   ```

5. Crear el modelo ordenado.

   ```powershell
   psql -U postgres -d everwod_faq_mvp -f "D:\U\SeptimoSemestre\IA\Proyecto Integrador\sql\02_create_tables.sql"
   ```

6. Transformar datos reales al modelo `faq_mvp`.

   ```powershell
   psql -U postgres -d everwod_faq_mvp -f "D:\U\SeptimoSemestre\IA\Proyecto Integrador\sql\04_transform_raw_to_faq_mvp.sql"
   ```

7. Validar carga y calidad.

   ```powershell
   psql -U postgres -d everwod_faq_mvp -f "D:\U\SeptimoSemestre\IA\Proyecto Integrador\sql\05_validation_queries.sql"
   ```

En pgAdmin4, si no usa consola, abra Tools > PSQL Tool para restaurar el dump y use Query Tool para los scripts SQL normales.

## Diseño

La solucion tiene dos capas.

`everwod_raw` conserva el dump como staging. El dump se restaura primero en `public` porque viene generado asi; luego `03_load_or_restore_raw_instructions.sql` mueve tablas y secuencias a `everwod_raw` sin reescribir los datos.

`faq_mvp` es el modelo ordenado para el reto:

- `workspaces`: clientes/empresas reales.
- `agents`: agentes reales asociados a workspaces.
- `conversations`: conversaciones desde `agent_chats`, con `raw_thread`, `raw_conversation` e inferencia documentada de agente.
- `messages`: mensajes normalizados desde `chat_messages`, con texto extraido defensivamente y JSON original preservado.
- `existing_faqs`: FAQs reales desde `agent_faqs`, incluidas eliminadas logicamente.
- `faq_candidates` y `faq_candidate_examples`: estructura vacia para sugerencias futuras.
- `pipeline_runs` y `pipeline_metrics`: auditoria/metricas de jobs periodicos.
- `message_embeddings` y `question_clusters`: soporte para embeddings y clustering sin requerir `pgvector`.
- `faq_validation_events`: auditoria de validacion humana.
- `data_quality_issues`: hallazgos reproducibles de calidad de datos.

## Tablas que vienen del dump

Las tablas fuente relevantes para el MVP son:

- `workspaces`
- `agents`
- `agent_chats`
- `chat_messages`
- `agent_faqs`
- `agent_texts`
- `agent_files`
- `agent_runs`
- `whatsapp_configs`
- `whatsapp_templates`

Tambien se preservan en raw las tablas operativas restantes: usuarios, leads, pagos, planes, productos, servicios, agendas, campañas y catalogos. No se copian al modelo MVP salvo que sean necesarias para conversaciones/FAQs.

## Trazabilidad

Cada entidad MVP mantiene claves de origen:

- `workspaces.workspace_id = everwod_raw.workspaces.id`
- `agents.agent_id = everwod_raw.agents.id`
- `conversations.source_agent_chat_id = everwod_raw.agent_chats.id`
- `messages.source_chat_message_id = everwod_raw.chat_messages.id`
- `existing_faqs.faq_id = everwod_raw.agent_faqs.id`
- `existing_faqs.source_agent_id = everwod_raw.agent_faqs.agent_id`

Los JSON originales se preservan en:

- `conversations.raw_conversation`
- `conversations.raw_thread`
- `messages.raw_message`
- `messages.raw_model_output`

## Supuestos y decisiones

- El dump es la fuente de verdad para SQL y datos.
- Los documentos del reto justifican preparar tablas para embeddings, clustering, jobs periodicos, sugerencias y validacion humana.
- `agent_chats` no trae `agent_id`. Se infiere solo cuando el workspace tiene exactamente un agente en el dump. En este dump, 7.953 de 7.967 conversaciones cumplen esa condicion; las restantes quedan con `agent_id = NULL`.
- Se usan `timestamp without time zone` para fechas copiadas del dump, porque el origen usa ese tipo.
- Se usan `timestamptz` para eventos operativos nuevos del pipeline.
- `message_embeddings.embedding` usa `double precision[]` para mantener compatibilidad sin instalar `pgvector`. Si el ambiente tiene `pgvector`, puede agregarse despues.
- No se insertan `faq_candidates` en esta fase: generar sugerencias requiere embeddings/clustering/LLM o una regla deterministica acordada.

## Datos no inventados / limitaciones

- No hay inserts manuales con conversaciones, FAQs o ejemplos inventados.
- Si un dato no existe o no puede inferirse con evidencia del dump, queda `NULL` o se registra en `data_quality_issues`.
- `chat_responses` existe pero no trae filas.
- `chat_messages.model_output` esta nulo en el perfil del dump.
- Algunas FAQs referencian agentes ausentes; se preservan con `source_agent_id` y `agent_id = NULL`.
- Telefonos, emails, nombres, direcciones y tokens se tratan como obfuscados/vacios segun el dump y `everwod_db_structure.ts`. La solucion no desanonimiza ni reconstruye PII.

## Siguientes pasos del pipeline

1. Leer `faq_mvp.messages` filtrando `role = 'user'` y `content_text IS NOT NULL`.
2. Normalizar texto: lowercase, limpieza de espacios, remocion opcional de ruido.
3. Generar embeddings por workspace/agente y guardar en `faq_mvp.message_embeddings`.
4. Agrupar preguntas similares y guardar clusters en `faq_mvp.question_clusters`.
5. Comparar clusters contra `faq_mvp.existing_faqs` para evitar duplicar FAQs ya cubiertas.
6. Crear registros en `faq_mvp.faq_candidates` solo para preguntas recurrentes no cubiertas.
7. Guardar ejemplos reales en `faq_mvp.faq_candidate_examples`.
8. Permitir aprobacion/rechazo humano y auditar en `faq_mvp.faq_validation_events`.
9. Registrar cada corrida en `pipeline_runs` y metricas en `pipeline_metrics`.

## Validacion esperada del dump inspeccionado

Conteos observados antes de ejecutar SQL en PostgreSQL:

- `workspaces`: 51
- `agents`: 28
- `agent_chats`: 7.967
- `chat_messages`: 35.124
- `agent_faqs`: 212
- `chat_responses`: 0

Perfil conversacional observado:

- Roles: 17.748 `user`, 17.376 `assistant`.
- `message_type`: 35.124 `text`.
- Modelos en conversaciones: `gpt-4o`, `gpt-5`, `gpt-5.4`, `gpt-5.2`.
- FAQs activas: 137; eliminadas logicamente: 75.

Use `sql/05_validation_queries.sql` para comprobar estos conteos despues de la carga real.
