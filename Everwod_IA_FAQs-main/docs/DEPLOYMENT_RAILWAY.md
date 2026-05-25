# Railway Deployment

## Services

- `faq-api`: permanent FastAPI service.
- `faq-monthly-job`: scheduled job that runs the monthly pipeline.
- PostgreSQL: stores `everwod_raw` and `faq_mvp`.

## API Command

```bash
uvicorn app.api.main:app --host 0.0.0.0 --port $PORT
```

Docker uses the same entrypoint and defaults to port `8003` when `PORT` is not set.

Healthcheck:

```text
GET /health
```

## Monthly Job

Command:

```bash
python scripts/run_monthly_pipeline.py
```

Suggested cron:

```cron
0 3 1 * *
```

The job does not ingest raw data by default. Set `FAQ_MONTHLY_RUN_INGEST=true` only after reviewing dry-run metrics and confirming the raw source.

`POST /ingest` is also safe by default in the API: omitted `dry_run` is treated as `true`. A normalized write to `faq_mvp` requires an explicit `dry_run=false`.

## Environment

Required baseline:

```env
DB_NAME=everwod_faq_mvp
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=<railway-postgres-host>
DB_PORT=5432
FAQ_SCHEMA=faq_mvp
FAQ_RAW_SCHEMA=everwod_raw
FAQ_INGEST_SOURCE=everwod_raw
FAQ_MONTHLY_RUN_INGEST=false
CORS_ALLOW_ORIGINS=https://your-frontend.example
FAQ_LLM_ENABLED=true
FAQ_LLM_MODEL=Qwen/Qwen3-1.7B
FAQ_LLM_DEVICE=cpu
FAQ_LLM_TORCH_DTYPE=auto
```

## Runtime Notes

- Docker installs CPU-only torch and does not require CUDA/NVIDIA.
- Qwen is loaded during generation, not as a separate always-on service.
- Monthly execution keeps cost low because the expensive path runs on schedule.
- Logs must not include raw conversations, phone numbers, emails, tokens, PINs, or secrets.

## Everwod Production Validation

Local/demo ingestion reads `everwod_raw`. Before production, Everwod must confirm:

- official source database or replica;
- JSONB shape for `chat_messages.message`;
- readonly credentials and network access;
- permission to process chat data for FAQ generation;
- policy for PII and sensitive fields.
