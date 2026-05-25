# Quality Notes: ISO/IEC 25010

## Maintainability

The code is packaged under `app/` by responsibility: API, core, ingestion, pipeline, repository, and jobs. Pipeline and repository modules now contain real implementation rather than internal reexports. Legacy root modules remain only as thin service entrypoints.

## Reliability

The current suggestion flow is preserved. Ingestion is safe by default with `dry_run=true`, and write mode uses idempotent append/update statements.

## Performance Efficiency

The API is a single Railway service. The monthly job runs separately, so the LLM-heavy path is not permanently active. Docker uses CPU-only torch.

## Compatibility

Modern imports use `app.*`. Legacy commands such as `uvicorn suggestion_service:app` continue to work through explicit root entrypoints without `sys.modules` aliasing.

## Usability

The review frontend keeps the same endpoints and workflows: list suggestions, edit candidates, approve, reject, and mark `needs_review`.

## Security

The ingestion adapter avoids `public`, excludes `agent_runs` from the main path, and logs only identifiers/reasons rather than raw text, phones, emails, tokens, or secrets.

## Testability

The ingestion layer has unit tests with synthetic data and fake cursors. Tests do not require a real PostgreSQL write. Existing suggestion and validation tests remain active.

## Clean Code And SOLID

The ingestion adapter reads and normalizes raw data; the normalized writer handles idempotent persistence. API modules expose HTTP behavior, pipeline modules own FAQ generation, repository modules own SQL, and core modules own shared config/models. This keeps responsibilities separated without rewriting working business logic.
