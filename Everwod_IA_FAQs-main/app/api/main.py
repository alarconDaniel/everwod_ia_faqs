from typing import List, Optional

from fastapi import FastAPI

from app.api import ingest_service, suggestion_service, validation_service
from app.api.cors import configure_cors
from app.core.common import FAQ_RAW_SCHEMA, FAQ_SCHEMA
from app.core.models import (
    IngestRequest,
    IngestResponse,
    SuggestionEditRequest,
    SuggestionEditResponse,
    SuggestionSummary,
    ValidationRecord,
    ValidationRequest,
    ValidationResponse,
    WorkspaceResponse,
)


app = FastAPI(
    title="Everwod FAQ API",
    description="Unified FAQ suggestions, human validation, and raw ingestion API.",
    version="1.0.0",
)
configure_cors(app)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "faq-api",
        "source_schema": FAQ_SCHEMA,
        "raw_schema": FAQ_RAW_SCHEMA,
        "components": {
            "suggestion": suggestion_service.health(),
            "validation": validation_service.health(),
            "ingest": ingest_service.health(),
        },
    }


app.add_api_route(
    "/workspaces",
    suggestion_service.get_workspaces,
    methods=["GET"],
    response_model=List[WorkspaceResponse],
)
app.add_api_route(
    "/suggest",
    suggestion_service.suggest,
    methods=["POST"],
    response_model=SuggestionSummary,
)
app.add_api_route(
    "/suggestions",
    suggestion_service.get_suggestions,
    methods=["GET"],
    response_model=SuggestionSummary,
)
app.add_api_route(
    "/validations",
    validation_service.get_validations,
    methods=["GET"],
    response_model=List[ValidationRecord],
)
app.add_api_route(
    "/suggestions/{candidate_id}",
    validation_service.patch_suggestion,
    methods=["PATCH"],
    response_model=SuggestionEditResponse,
)
app.add_api_route(
    "/validate",
    validation_service.validate,
    methods=["POST"],
    response_model=ValidationResponse,
)
app.add_api_route(
    "/ingest",
    ingest_service.run_ingest,
    methods=["POST"],
    response_model=IngestResponse,
)


__all__ = ["app", "health"]
