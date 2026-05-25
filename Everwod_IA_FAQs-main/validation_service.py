"""Legacy entrypoint for `uvicorn validation_service:app`.

New code should import from `app.api.validation_service` or
`app.repository.faq_repository`.
"""

from app.api.validation_service import app, get_suggestions, get_validations, health, patch_suggestion, validate
from app.repository.faq_repository import (
    apply_candidate_edit,
    apply_validation,
    edit_candidate,
    list_validation_events,
    promote_candidate_to_existing_faq,
)
