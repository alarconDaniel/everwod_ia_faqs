"""Legacy entrypoint for `uvicorn suggestion_service:app`.

New code should import from `app.api.suggestion_service`,
`app.pipeline.pipeline`, or `app.repository.faq_repository`.
"""

from app.api.suggestion_service import app, get_suggestions, get_workspaces, health, suggest
from app.pipeline.pipeline import run_suggestion_pipeline
from app.repository.faq_repository import list_suggestions_from_db, list_workspaces_from_db
