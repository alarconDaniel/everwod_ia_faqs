# Everwod FAQ Architecture

## Overview

The backend is organized as a Python package under `app/`. The HTTP modules are intentionally thin; pipeline, repository, ingestion, and jobs contain the real implementation. Root modules are kept only as legacy entrypoints for old `uvicorn ...:app` commands.

Main flow:

```text
optional ingestion -> cleaning -> embeddings -> clustering -> Qwen generation -> validation -> persistence -> human review
```

## Modules

- `app.api`: FastAPI entrypoints. `app.api.main` is the unified Railway API; `suggestion_service`, `validation_service`, and `ingest_service` delegate to internal modules.
- `app.api.cors`: shared CORS setup for API apps.
- `app.core`: environment config, DB connection helpers, text utilities, and public Pydantic models.
- `app.pipeline`: real suggestion implementation split into cleaning, candidate filtering, embeddings, clustering, generation, quality gates, and orchestration.
- `app.repository`: real SQL persistence and reads for suggestions, runs, metrics, workspaces, normalized conversations, validation events, and edit events.
- `app.ingestion`: raw Everwod adapter plus idempotent normalized writer.
- `app.jobs`: scheduled monthly pipeline entrypoint, with optional raw ingestion controlled by environment.

The root files `suggestion_service.py`, `ingest_service.py`, `validation_service.py`, and `scheduler.py` are documented legacy entrypoints. They do not contain business logic.

## Schemas

- `everwod_raw`: raw restored dump from Everwod. It is read-only from this service perspective.
- `faq_mvp`: internal normalized FAQ schema used by the suggestion pipeline.
- `public`: not used as a source schema by the ingestion code.

The ingestion adapter reads:

- `everwod_raw.workspaces`
- `everwod_raw.agents`
- `everwod_raw.agent_chats`
- `everwod_raw.chat_messages`
- `everwod_raw.agent_faqs`

It writes append/update operations into:

- `faq_mvp.workspaces`
- `faq_mvp.agents`
- `faq_mvp.conversations`
- `faq_mvp.messages`
- `faq_mvp.existing_faqs`

`everwod_raw.agent_runs` is intentionally excluded from the main path because it is very large and stores heavy OpenAI run JSON. It can be analyzed separately only if Everwod confirms need, permissions, and privacy handling.

## Safety

`POST /ingest` defaults to `dry_run=true`. A write requires an explicit `dry_run=false`. The writer uses `ON CONFLICT` append/update behavior and does not delete candidates, runs, validations, approved/rejected FAQs, or pipeline artifacts.

Production integration still requires Everwod to confirm the official source, JSONB contract, and read permissions, preferably with a readonly user or replica.
