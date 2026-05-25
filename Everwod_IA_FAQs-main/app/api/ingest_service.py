from typing import Optional

from fastapi import FastAPI

from app.api.cors import configure_cors
from app.core.common import DATA_DIR, FAQ_INGEST_SOURCE, FAQ_RAW_SCHEMA, FAQ_SCHEMA, save_json_lines
from app.core.models import IngestRequest, IngestResponse
from app.ingestion.normalized_writer import run_everwod_ingestion
from app.repository.faq_repository import (
    fetch_conversation_records,
    fetch_conversation_records_with_metrics,
    fetch_message_rows,
    fetch_message_rows_with_metrics,
    pair_user_assistant_messages,
    pair_user_assistant_messages_with_stats,
)


app = FastAPI(title="Everwod FAQ Ingestion Service")
configure_cors(app)

OUTPUT_PATH = DATA_DIR / "conversations.jsonl"


def ingest(
    limit: Optional[int] = None,
    since_days: int = 90,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    dry_run: bool = True,
    source: Optional[str] = None,
) -> IngestResponse:
    ingest_source = (source or FAQ_INGEST_SOURCE or FAQ_RAW_SCHEMA).strip()
    if ingest_source == FAQ_RAW_SCHEMA or ingest_source == "everwod_raw":
        metrics = run_everwod_ingestion(
            limit=limit,
            since_days=since_days,
            workspace_id=workspace_id,
            agent_id=agent_id,
            dry_run=dry_run,
            raw_schema=FAQ_RAW_SCHEMA,
            target_schema=FAQ_SCHEMA,
        )
        return IngestResponse(
            imported_records=0 if dry_run else int(metrics.get("records_written") or 0),
            output_file="",
            **metrics,
        )

    records, metrics = fetch_conversation_records_with_metrics(
        limit=limit,
        since_days=since_days,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    if not dry_run:
        save_json_lines(records, OUTPUT_PATH)
    return IngestResponse(
        imported_records=0 if dry_run else len(records),
        output_file=str(OUTPUT_PATH),
        dry_run=dry_run,
        source=ingest_source,
        source_schema=FAQ_SCHEMA,
        target_schema=FAQ_SCHEMA,
        **metrics,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ingest", "source_schema": FAQ_SCHEMA, "raw_schema": FAQ_RAW_SCHEMA}


@app.post("/ingest", response_model=IngestResponse)
def run_ingest(request: IngestRequest) -> IngestResponse:
    return ingest(
        limit=request.limit,
        since_days=request.since_days,
        workspace_id=request.workspace_id,
        agent_id=request.agent_id,
        dry_run=request.dry_run,
        source=request.source,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api.ingest_service:app", host="127.0.0.1", port=8001, log_level="info")
