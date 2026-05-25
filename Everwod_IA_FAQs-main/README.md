# Everwod IA FAQs

Microservice for detecting recurring FAQ opportunities from Everwod conversations, generating candidate FAQs, and supporting human review before approval.

## Quickstart

```powershell
.\venv\Scripts\python -m pytest -p no:cacheprovider
cd frontend
npm run lint
npm run dev
```

Standalone services still work:

```powershell
uvicorn suggestion_service:app --reload --port 8003
uvicorn ingest_service:app --reload --port 8001
uvicorn validation_service:app --reload --port 8004
```

Unified API:

```powershell
uvicorn app.api.main:app --reload --port 8003
```

Monthly job:

```powershell
python scripts/run_monthly_pipeline.py
```

## Structure

```text
app/
  api/          thin FastAPI services and unified API
  core/         config, DB helpers, shared models
  ingestion/    Everwod raw adapter and normalized writer
  jobs/         scheduler entrypoints
  pipeline/     real cleaning, embeddings, clustering, generation, quality, orchestration
  repository/   real SQL persistence, normalized reads, validation/edit events
frontend/       review/demo UI
scripts/        one-off and monthly pipeline commands
docs/           architecture, deployment, quality notes
```

## Ingestion

`POST /ingest` is safe by default. If `dry_run` is omitted, it is treated as `true`. Writing to `faq_mvp` requires an explicit request body with `dry_run=false`.

Local raw ingestion reads from `FAQ_RAW_SCHEMA=everwod_raw` and writes idempotently to `FAQ_SCHEMA=faq_mvp`. It does not read `public` and does not use `agent_runs` in the main path.

## Docker

The Docker image is CPU-only:

```bash
docker build -t everwod-faq-api .
docker run -p 8003:8003 --env-file .env everwod-faq-api
```

Default command:

```bash
uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8003}
```

## Railway

Deploy one permanent API service (`faq-api`) and one scheduled monthly job (`faq-monthly-job`). Keep `FAQ_MONTHLY_RUN_INGEST=false` until `/ingest` dry-run metrics are reviewed.

More detail:

- [Architecture](docs/ARCHITECTURE.md)
- [Railway Deployment](docs/DEPLOYMENT_RAILWAY.md)
- [Quality ISO 25010](docs/QUALITY_ISO25010.md)

Legacy root entrypoints are retained for service commands only. Internal code and tests import `app.*` modules directly.
