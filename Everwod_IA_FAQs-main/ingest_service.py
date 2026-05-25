"""Legacy entrypoint for `uvicorn ingest_service:app`.

New code should import from `app.api.ingest_service`,
`app.ingestion`, or `app.repository.faq_repository`.
"""

from app.api.ingest_service import app, health, ingest, run_ingest
from app.repository.faq_repository import (
    fetch_conversation_records,
    fetch_conversation_records_with_metrics,
    fetch_message_rows,
    fetch_message_rows_with_metrics,
    pair_user_assistant_messages,
    pair_user_assistant_messages_with_stats,
)
