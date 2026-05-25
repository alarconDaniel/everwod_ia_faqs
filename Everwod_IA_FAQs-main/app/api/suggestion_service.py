from typing import List, Optional

from fastapi import FastAPI, HTTPException

from app.api.cors import configure_cors
from app.core.common import FAQ_SCHEMA
from app.core.config import FAQ_LLM_MODEL, FAQ_LLM_THINKING_DISABLED, MODEL_NAME
from app.core.models import IngestRequest, SuggestionSummary, WorkspaceResponse
from app.pipeline.embeddings import current_embedding_model_label
from app.pipeline.pipeline import run_suggestion_pipeline
from app.repository.faq_repository import list_suggestions_from_db, list_workspaces_from_db
import app.pipeline.generation as generation


app = FastAPI(title="Everwod FAQ Suggestion Service")
configure_cors(app)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "suggestion",
        "source_schema": FAQ_SCHEMA,
        "embedding_model": MODEL_NAME,
        "embedding_backend": current_embedding_model_label(),
        "answer_model": FAQ_LLM_MODEL if generation.ANSWER_GENERATOR else "historical_fallback",
        "thinking_disabled": FAQ_LLM_THINKING_DISABLED,
    }


@app.get("/workspaces", response_model=List[WorkspaceResponse])
def get_workspaces() -> List[WorkspaceResponse]:
    return list_workspaces_from_db()


@app.post("/suggest", response_model=SuggestionSummary)
def suggest(request: Optional[IngestRequest] = None) -> SuggestionSummary:
    try:
        return run_suggestion_pipeline(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo generar sugerencias: {exc}") from exc


@app.get("/suggestions", response_model=SuggestionSummary)
def get_suggestions(
    status: Optional[str] = None,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    include_all: bool = False,
) -> SuggestionSummary:
    if status and status not in {"pending", "approved", "rejected", "needs_review"}:
        raise HTTPException(status_code=400, detail="Invalid status filter.")
    return list_suggestions_from_db(
        status=status,
        workspace_id=workspace_id,
        agent_id=agent_id,
        include_all=include_all,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api.suggestion_service:app", host="127.0.0.1", port=8003, log_level="info")
