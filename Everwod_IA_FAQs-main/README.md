# Everwod FAQ Intelligence

Prototipo backend/frontend para detectar patrones conversacionales recurrentes en chats historicos de Everwod y convertirlos en sugerencias de FAQs validadas por una persona.

El sistema trabaja sobre el modelo limpio `faq_mvp` definido en `../sql/` y evita depender de APIs pagas. Es un sistema de sugerencias con revision humana: no intenta autopublicar FAQs perfectas, sino proponer candidatos razonables para que una persona los apruebe, ajuste o rechace. Los embeddings usan un modelo multilingue de `sentence-transformers` recomendado para conversaciones en espanol o un fallback hash local. La generacion con Qwen local produce la FAQ completa en JSON estructurado: decision de publicacion, pregunta canonica, respuesta canonica, confianza y razon.

## Arquitectura

- `ingest_service.py` lee mensajes normalizados desde `faq_mvp.messages`, los une con `faq_mvp.conversations` y `faq_mvp.workspaces`, y empareja ultimo mensaje de usuario con la siguiente respuesta del asistente.
- `suggestion_service.py` ejecuta embeddings, clustering, deduplicacion contra `faq_mvp.existing_faqs`, sintesis completa con Qwen, validadores de calidad y persistencia en `pipeline_runs`, `pipeline_metrics`, `question_clusters`, `faq_candidates`, `faq_candidate_examples` y `message_embeddings`.
- `validation_service.py` lista candidatos desde PostgreSQL, registra eventos en `faq_validation_events` y, al aprobar, promueve la sugerencia a `faq_mvp.existing_faqs`.
- `scheduler.py` ejecuta el flujo persistente de sugerencias cada 7 dias por defecto.
- `frontend/` contiene el panel React/Vite para generar sugerencias, revisar candidatos y consultar historial de validaciones.

## Preparar la base de datos

Desde la carpeta raiz del proyecto padre:

```bash
psql -U postgres -d everwod_faq_mvp -f sql/01_create_schemas.sql
psql -U postgres -d everwod_faq_mvp -f pgdump_production_08-04-2026-22-02-09.sql
psql -U postgres -d everwod_faq_mvp -f sql/03_load_or_restore_raw_instructions.sql
psql -U postgres -d everwod_faq_mvp -f sql/02_create_tables.sql
psql -U postgres -d everwod_faq_mvp -f sql/04_transform_raw_to_faq_mvp.sql
psql -U postgres -d everwod_faq_mvp -f sql/05_validation_queries.sql
psql -U postgres -d everwod_faq_mvp -f sql/06_human_edits_and_pipeline_metrics.sql
```

No es necesario procesar el dump desde Python. El backend consume el resultado normalizado en `faq_mvp`.

## Configuracion

```bash
cp .env.example .env
```

Variables principales:

```env
DB_NAME=everwod_faq_mvp
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
FAQ_SCHEMA=faq_mvp

CORS_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

FAQ_EMBEDDING_BACKEND=sentence-transformers
FAQ_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
FAQ_LLM_ENABLED=true
FAQ_LLM_MODEL=Qwen/Qwen3-1.7B

FAQ_CLUSTER_EPS=0.34
FAQ_MIN_CLUSTER_SIZE=3
FAQ_MIN_CLUSTER_SUPPORT=0.58
FAQ_MIN_CLUSTER_COHESION=0.60
FAQ_MIN_GENERATION_CONFIDENCE=0.20
FAQ_SKIP_EXISTING=true
FAQ_DUPLICATE_THRESHOLD=0.78
FAQ_CANDIDATE_HARVEST_MODE=broad
FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS=true
FAQ_TWO_EXAMPLE_MIN_COHESION=0.80
FAQ_ENABLE_HIERARCHICAL_FALLBACK=true
FAQ_SAFE_FULL_ANALYSIS_LIMIT=50000

FAQ_SCHEDULE_INTERVAL_DAYS=7
```

Para entornos sin modelo descargado, se puede usar `FAQ_EMBEDDING_BACKEND=hash`. `all-MiniLM-L6-v2` sigue siendo una alternativa mas ligera, pero para este caso se recomienda `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` porque el dataset real esta mayoritariamente en espanol y necesita mejor similitud semantica entre preguntas equivalentes. Si Qwen no carga o devuelve JSON invalido, el pipeline descarta el cluster en vez de publicar una respuesta historica cruda.

El modelo recomendado de generacion es `Qwen/Qwen3-1.7B`. Es mas lento que `Qwen/Qwen2.5-0.5B-Instruct`, pero en el benchmark real de `workspace_id=126` genero JSON valido para todos los clusters y produjo salidas mucho mas alineadas. El backend llama `apply_chat_template(..., enable_thinking=False)` cuando el tokenizer lo soporta y ademas limpia cualquier bloque `<think>...</think>` antes de parsear JSON. Alternativas livianas:

- `Qwen/Qwen2.5-0.5B-Instruct`: modo ultraliviano; rapido, pero fallo en calidad en el benchmark real.
- `Qwen/Qwen2.5-1.5B-Instruct`: fallback intermedio si Qwen3-1.7B no cabe en el equipo.

Los thresholds son calibrables. Los valores por defecto estan pensados para sugerencias con revision humana, no para publicacion automatica:

- `FAQ_MIN_CLUSTER_SUPPORT`: soporte semantico promedio minimo antes de marcar o rechazar clusters.
- `FAQ_MIN_CLUSTER_COHESION`: cohesion minima recomendada del cluster.
- `FAQ_MIN_GENERATION_CONFIDENCE`: confianza minima de generacion; la confianza de Qwen es una senal mas, combinada con recurrencia, cohesion y evidencia limpia.

Tiers de calidad:

- `high_confidence`: intencion clara, buena cohesion y respuesta solida.
- `needs_review`: intencion clara, pero confianza media, cohesion media, evidencia parcial, repair automatico o respuesta prudente que requiere revision; se persiste como `status=needs_review` y sigue el mismo flujo de aprobacion, edicion o rechazo.
- `question_with_answer_review`: subtipo en `candidate_metadata.candidate_kind` para preguntas fuertes cuya respuesta debe revisarse antes de aprobar.
- `hard_reject`: no se persiste; aplica para trazas internas, intenciones mezcladas, falta total de evidencia util, JSON irrecuperable o pregunta/respuesta ininteligible.

## Grounding por intencion del cluster

El LLM devuelve ahora dos afirmaciones separadas:

- `cluster_intent_statement`: resume la intencion dominante de las preguntas reales del usuario. Se deriva principalmente de mensajes de usuario del cluster.
- `knowledge_statement`: resume el conocimiento util para responder. Puede usar respuestas historicas del asistente, pero no puede cambiar el tema de la pregunta FAQ.

Regla critica: la `canonical_question` debe estar alineada con `cluster_intent_statement` y con las preguntas del cluster. Las respuestas historicas pueden aportar datos para redactar la respuesta, pero no pueden convertir un cluster de "Quiero el box mujer" en una FAQ sobre entrega en Fusagasuga.

El validador `is_canonical_question_aligned_with_cluster(...)` clasifica la pregunta como:

- `strong_alignment`: puede avanzar.
- `partial_alignment`: se persiste como `needs_review`.
- `misaligned`: se rechaza o se intenta reparar antes de persistir.

La validacion combina categorias de intencion, solapamiento de terminos centrales y similitud semantica contra el centroide del cluster. Si la pregunta introduce una intencion primaria nueva, por ejemplo domicilio/envio en un cluster de productos, se considera desalineada aunque aparezca como dato colateral en una respuesta historica.

Cuando la pregunta o respuesta se desplaza, el repair pass recibe preguntas reales, `cluster_intent_statement`, `knowledge_statement`, pregunta/respuesta originales y razon de rechazo. Si no logra alinear la pregunta al cluster, no se persiste. Si la pregunta esta alineada pero la respuesta tiene soporte parcial, se conserva como `question_with_answer_review` con respuesta prudente editable.

`support_examples` se deduplican antes de devolverlos al frontend y se priorizan por diversidad, representatividad y alineacion con la pregunta final. Si un mismo texto aparece repetido, se muestra una sola vez; la recurrencia queda reflejada en `cluster_size`.

Metricas nuevas en `pipeline_metrics` y logs:

- `cluster_question_alignment_strong`
- `cluster_question_alignment_partial`
- `cluster_question_alignment_failed`
- `repair_alignment_attempts`
- `repair_alignment_successes`
- `support_examples_deduplicated`
- `candidates_rejected_due_to_cluster_misalignment`
- `candidates_recovered_after_alignment_repair`

## Causa raiz 90 vs 365 dias

El bug observado en `workspace_id=126` no era que `since_days=365` tuviera menos datos utiles. La causa raiz fue el muestreo: el pipeline anterior aplicaba `LIMIT 15000` sobre mensajes sueltos. Para 90 dias habia 7.966 mensajes y no habia truncamiento. Para 365 dias habia 20.980 mensajes; el limite cortaba 5.980 mensajes y, por el ordenamiento anterior, dejaba por fuera conversaciones recientes completas. Eso contaminaba el embudo: cambiaban los pares usuario/asistente, se diluian clusters utiles y los rechazos del LLM parecian incoherentes.

La ingesta ahora limita por conversaciones completas, ordenadas por actividad reciente (`last_message_at`). Primero selecciona conversaciones, luego trae todos sus mensajes. Asi se evitan pares cortados y el caso de 365 dias conserva patrones recientes visibles en 90 dias.

Para corridas manuales por empresa, el frontend ya no envia `limit=15000`. Si `limit` llega vacio, el backend usa la estrategia `complete_if_fits`: analiza todo el rango solicitado cuando los mensajes elegibles del workspace son menores o iguales a `FAQ_SAFE_FULL_ANALYSIS_LIMIT` (default `50000`). Solo si el rango supera ese maximo seguro se aplica truncamiento por conversaciones completas. En ese caso la API y el frontend muestran:

- `raw_messages_found` y `raw_messages_processed`;
- `conversations_found` y `conversations_processed`;
- `truncation_applied` y `truncation_ratio`;
- `user_assistant_pairs_built`;
- `pairs_rejected_no_assistant_followup`;
- `pairs_rejected_empty_text`.

Validacion real en PostgreSQL para `workspace_id=126`:

- 90 dias: 7.966 mensajes encontrados/procesados, 1.734 conversaciones, 1.139 candidatos para embedding, 15 clusters.
- 365 dias con el antiguo limite explicito de 15.000: 20.980 mensajes encontrados, 14.990 procesados, 3.209 conversaciones procesadas, 2.417 candidatos para embedding, 18 clusters, `truncation_ratio=0.2855`.
- Despues del cambio, 365 dias dejo de producir 0 por razones opacas; la corrida real `1bf8ca49-cd98-4184-93d3-dec669821c1a` genero 3 candidatos `needs_review`.

## Instalacion backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python test_connection.py
```

En Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python test_connection.py
```

## Levantar servicios

```bash
uvicorn ingest_service:app --reload --port 8001
uvicorn suggestion_service:app --reload --port 8003
uvicorn validation_service:app --reload --port 8004
```

El servicio de embeddings independiente sigue disponible si se necesita:

```bash
uvicorn embed_service:app --reload --port 8002
```

## Frontend

```bash
cd frontend
cp .env.example .env
npm ci
npm run dev
```

Abrir `http://localhost:5173`.

La experiencia principal se organiza por empresa desde la cabecera. La UI carga `GET /workspaces`, selecciona una empresa disponible y solo muestra sugerencias de ese `workspace_id`. Junto al selector de empresa aparece el control `Analizar chats de los ultimos`, que envia `since_days` al flujo `POST /ingest` y `POST /suggest`.

## Flujo manual

1. Ingesta:

   ```bash
   curl -X POST http://127.0.0.1:8001/ingest \
     -H "Content-Type: application/json" \
     -d '{"since_days":90,"workspace_id":74}'
   ```

2. Generar y persistir sugerencias:

   ```bash
   curl -X POST http://127.0.0.1:8003/suggest \
     -H "Content-Type: application/json" \
     -d '{"since_days":90,"workspace_id":74}'
   ```

3. Consultar candidatos de la ultima corrida exitosa:

   ```bash
   curl "http://127.0.0.1:8003/suggestions?status=pending&workspace_id=74"
   ```

   Para inspeccionar todo el historial de candidatos:

   ```bash
   curl "http://127.0.0.1:8003/suggestions?include_all=true"
   ```

   Empresas disponibles:

   ```bash
   curl "http://127.0.0.1:8003/workspaces"
   ```

4. Validar:

   ```bash
   curl -X POST http://127.0.0.1:8004/validate \
     -H "Content-Type: application/json" \
     -d '{"suggestion_id":"UUID","reviewer":"Nombre","status":"approved","notes":"Valida"}'
   ```

5. Editar una sugerencia sin aprobar:

   ```bash
   curl -X PATCH http://127.0.0.1:8004/suggestions/UUID \
     -H "Content-Type: application/json" \
     -d '{"question":"¿Desde que valor el domicilio es gratis?","answer":"El domicilio es gratis en compras superiores a $130.000 dentro de la zona de cobertura.","editor":"Juan Esteban","notes":"Pregunta alineada con la respuesta."}'
   ```

6. Aprobar con edicion incluida:

   ```bash
   curl -X POST http://127.0.0.1:8004/validate \
     -H "Content-Type: application/json" \
     -d '{"suggestion_id":"UUID","reviewer":"Juan Esteban","status":"approved","notes":"Aprobada con ajuste humano.","edited_question":"¿Desde que valor el domicilio es gratis?","edited_answer":"El domicilio es gratis en compras superiores a $130.000 dentro de la zona de cobertura."}'
   ```

Cuando el estado es `approved`, el candidato se inserta o actualiza como FAQ persistente en `faq_mvp.existing_faqs`. Si hay campos editados, primero se guarda el evento en `faq_candidate_edit_events`, luego se valida y la version editada es la que se promueve. Si el candidato no tiene `agent_id` o no tiene respuesta sugerida, la aprobacion devuelve error `409` y no escribe una FAQ incompleta.

`GET /suggestions` incluye ahora `was_human_edited`, `last_edited_by`, `last_edited_at`, `edit_count`, `knowledge_statement`, `quality_tier`, `review_reason`, `generation_confidence`, `since_days_used` y `workspace_id`.

## Debug y benchmark

Para inspeccionar una corrida completa sin adivinar:

```bash
python scripts/debug_workspace_pipeline.py --workspace-id 126 --since-days 90
python scripts/debug_workspace_pipeline.py --workspace-id 126 --since-days 365
```

El script genera `data/debug_workspace_<workspace>_<days>.json` e incluye mensajes, pares usuario/asistente, candidatos iniciales, clusters, cohesion, soporte, evidencia, prompt real, JSON LLM, salida parseada, validador y razon de rechazo o aceptacion.

Para comparar modelos sobre los mismos datos:

```bash
python scripts/benchmark_faq_llms.py --workspace-id 126 --since-days 90
python scripts/benchmark_faq_llms.py --workspace-id 126 --since-days 365
```

El benchmark compara por defecto `Qwen/Qwen2.5-0.5B-Instruct` y `Qwen/Qwen3-1.7B`. Reporta modelo, pregunta, respuesta, `knowledge_statement`, `publish`, `confidence`, JSON valido, alineacion pregunta/respuesta, texto `<think>`, longitudes y tiempo de generacion.

Resultado real de referencia en 90 dias para `workspace_id=126`:

- `Qwen/Qwen2.5-0.5B-Instruct`: 15/15 JSON validos, 14 `publish=true`, 0 aceptados; los rechazos fueron principalmente `rejected_invalid_knowledge_statement`.
- `Qwen/Qwen3-1.7B`: 15/15 JSON validos, 15 `publish=true`, 6 aceptables en benchmark; sin texto `<think>`.

## Recuperacion, clusters de 2 y deduplicacion

`FAQ_CANDIDATE_HARVEST_MODE=broad` recupera mas preguntas plausibles antes del clustering: signos de pregunta, estructura interrogativa, intencion transaccional y palabras como precio, costo, domicilio, envio, pagar, reserva, horarios, talla, color, descuento, Nequi, Daviplata, PSE y Efecty. Los saludos, emojis solos, mensajes vacios y ruido tecnico siguen fuera.

`FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS=true` permite clusters de 2 ejemplos como `needs_review` si tienen cohesion minima `FAQ_TWO_EXAMPLE_MIN_COHESION=0.80`. Nunca se marcan como `high_confidence`.

La deduplicacion ya no es silenciosa. Las corridas persisten:

- `duplicates_against_existing_faqs`;
- `duplicates_against_previous_candidates`;
- `candidates_skipped_existing`;
- `candidates_superseded`.

Si `FAQ_SKIP_EXISTING=true`, los duplicados contra FAQs existentes se omiten y quedan contados en `pipeline_metrics`. Los candidatos previos exactos se actualizan, y los similares a candidatos previos se vuelven a persistir en la corrida actual para no desaparecer de la vista latest; quedan marcados como `superseded_previous_candidate` o `similar_to_previous_candidate` en `candidate_metadata.deduplication_status`.

## Scheduler semanal

```bash
python scheduler.py
```

Por defecto ejecuta inmediatamente y luego cada 7 dias. Ajustar con:

```env
FAQ_SCHEDULE_INTERVAL_DAYS=7
FAQ_SCHEDULE_LIMIT=15000
FAQ_SCHEDULE_SINCE_DAYS=90
```

## Pruebas

```bash
pytest
python -m py_compile faq_common.py faq_models.py ingest_service.py suggestion_service.py validation_service.py scheduler.py
cd frontend && npm run build
```

Las pruebas mockean PostgreSQL/modelos pesados y cubren emparejamiento, `since_days`, patron conservado al ampliar ventana, filtros broad/strict, clusters de 2 como `needs_review`, deduplicacion y metricas de supresion, limpieza de PII, parsing JSON de Qwen, limpieza de thinking tags, pregunta alineada con `knowledge_statement`, rechazo de respuestas conversacionales, fallback sin LLM, validacion, edicion humana, promocion de version editada y endpoints basicos.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

El contenedor de PostgreSQL queda vacio hasta restaurar el dump y ejecutar los scripts SQL. El volumen `../sql:/sql:ro` se monta para facilitar esa carga.

## Metricas guardadas

Cada corrida queda explicable en `pipeline_metrics`. Los contadores principales son:

- ingesta: `raw_messages_found`, `raw_messages_processed`, `conversations_found`, `conversations_processed`, `truncation_applied`, `truncation_ratio`;
- pares: `user_messages_considered`, `assistant_messages_considered`, `user_assistant_pairs_built`, `pairs_rejected_no_assistant_followup`, `pairs_rejected_empty_text`;
- candidatos: `candidate_questions_detected`, `candidate_questions_rejected_initial_filter`, `candidate_questions_kept_for_embedding`, `embeddings_generated`;
- clusters: `clusters_detected_before_quality_gates`, `clusters_rejected_low_size`, `clusters_rejected_low_support`, `clusters_rejected_low_cohesion`, `clusters_rejected_mixed_intent`;
- LLM: `llm_calls_attempted`, `llm_parse_failures`, `llm_publish_false`, `rejected_generation_confidence`;
- validadores: `rejected_invalid_canonical_question`, `rejected_invalid_canonical_answer`, `rejected_question_answer_misaligned`, `rejected_answer_not_supported_by_evidence`, `rejected_llm_output_contaminated`;
- deduplicacion: `duplicates_against_existing_faqs`, `duplicates_against_previous_candidates`, `candidates_skipped_existing`, `candidates_superseded`;
- salida: `accepted_high_confidence_candidates`, `accepted_needs_review_candidates`, `accepted_candidates_total`.

Para diagnosticar pocas o cero FAQs, primero revisar truncamiento, candidatos despues del filtro inicial, clusters antes de gates, rechazos por soporte/cohesion/intencion, rechazos del LLM, validadores y deduplicacion. El objetivo es que un cero tenga una razon cuantitativa, no una sospecha.

## Decisiones tecnicas

- `company_id` se conserva como alias de `workspace_id` para compatibilidad con el frontend y la API previa.
- `faq_mvp.existing_faqs` contiene FAQs heredadas y FAQs promovidas desde candidatos aprobados; esto mantiene una sola tabla de FAQs activas para deduplicacion futura.
- `message_embeddings.embedding` usa `double precision[]` para no requerir `pgvector` en el prototipo academico.
- Qwen local genera la FAQ completa, no solo la respuesta. El formato obligatorio es JSON con `publish`, `canonical_question`, `canonical_answer`, `confidence` y `reason`.
- Qwen recibe instrucciones para redactar preguntas y respuestas cortas, claras y directas. La pregunta canonica apunta a una sola oracion breve; la respuesta debe mantenerse compacta, normalmente 1 o 2 oraciones.
- El fallback sin Qwen es conservador: no publica candidatos para evitar reciclar texto conversacional del dump.
- El soporte de evidencia es gradual (`strong`, `partial`, `weak`, `none`). `partial` y `weak` se guardan como `needs_review` cuando la pregunta es clara y la respuesta es prudente; `none` solo se descarta si no hay conexion util o hay contradiccion fuerte.
- Antes de un hard reject por pregunta invalida, `knowledge_statement` invalido o desalineacion pregunta/respuesta, el pipeline hace un repair pass con el mismo Qwen. Si no queda perfecto pero es revisable, se persiste como `needs_review`.
- `GET /suggestions` muestra por defecto la ultima corrida exitosa y puede filtrarse por `workspace_id` para que el frontend no mezcle empresas.
- El frontend carga empresas con `GET /workspaces`, selecciona una empresa como contexto principal y envia `workspace_id` y `since_days` al generar nuevas sugerencias. No envia `limit=15000`; el backend aplica `complete_if_fits` con `FAQ_SAFE_FULL_ANALYSIS_LIMIT`.
- `since_days` sigue teniendo default de 90 dias, pero el usuario puede escoger 7, 30, 60, 90, 180 o 365 dias desde la UI.

## Limitaciones

- La calidad del clustering depende del volumen de mensajes por workspace y del umbral `FAQ_CLUSTER_EPS`.
- El fallback hash de embeddings es economico y reproducible, pero menos semantico que el modelo multilingue recomendado. Cambiar `FAQ_EMBEDDING_MODEL` crea una clave distinta en `message_embeddings` por `(message_pk, embedding_model)`, evitando reutilizar embeddings incompatibles.
- La promocion a FAQ requiere `agent_id`; las conversaciones sin agente inferido deben resolverse con una regla de negocio antes de aprobarse.
- El prototipo registra metricas tecnicas; una evaluacion experimental formal de precision/resolucion debe hacerse con una muestra revisada por humanos.
