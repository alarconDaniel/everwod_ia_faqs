from typing import List, Optional

from fastapi import FastAPI, HTTPException

from app.api.cors import configure_cors
from app.core.common import FAQ_SCHEMA
from app.core.models import SuggestionEditRequest, SuggestionEditResponse, ValidationRecord, ValidationRequest, ValidationResponse
from app.repository.faq_repository import (
    VALID_STATUSES,
    apply_validation,
    edit_candidate,
    list_suggestions_from_db,
    list_validation_events,
)


app = FastAPI(title="Everwod FAQ Validation Service")
configure_cors(app)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "validation", "source_schema": FAQ_SCHEMA}


@app.get("/suggestions")
def get_suggestions(
    status: Optional[str] = None,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    include_all: bool = False,
) -> List[dict]:
    if status and status not in {"pending", *VALID_STATUSES}:
        raise HTTPException(status_code=400, detail="Invalid status filter.")
    return [
        suggestion.model_dump()
        for suggestion in list_suggestions_from_db(
            status=status,
            workspace_id=workspace_id,
            agent_id=agent_id,
            include_all=include_all,
        ).suggestions
    ]


@app.get("/validations", response_model=List[ValidationRecord])
def get_validations(workspace_id: Optional[int] = None) -> List[ValidationRecord]:
    return list_validation_events(workspace_id=workspace_id)


@app.patch("/suggestions/{candidate_id}", response_model=SuggestionEditResponse)
def patch_suggestion(candidate_id: str, request: SuggestionEditRequest) -> SuggestionEditResponse:
    return edit_candidate(candidate_id, request)


@app.post("/validate", response_model=ValidationResponse)
def validate(request: ValidationRequest) -> ValidationResponse:
    return apply_validation(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api.validation_service:app", host="127.0.0.1", port=8004, log_level="info")
