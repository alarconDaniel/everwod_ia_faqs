from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from psycopg2.extras import RealDictCursor

from faq_common import FAQ_SCHEMA, configure_cors, get_db_connection, normalize_text
from faq_models import SuggestionEditRequest, SuggestionEditResponse, ValidationRecord, ValidationRequest, ValidationResponse
from suggestion_service import list_suggestions_from_db


app = FastAPI(title="Everwod FAQ Validation Service")
configure_cors(app)

VALID_STATUSES = {"approved", "rejected", "needs_review"}


def _fetch_candidate_for_update(cursor: Any, candidate_id: str) -> Optional[Dict[str, Any]]:
    cursor.execute(
        f"""
        SELECT
            candidate_id::text,
            workspace_id,
            agent_id::text AS agent_id,
            normalized_question,
            suggested_answer,
            status,
            was_human_edited,
            last_edited_by,
            last_edited_at
        FROM {FAQ_SCHEMA}.faq_candidates
        WHERE candidate_id = %s
        FOR UPDATE
        """,
        (candidate_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def apply_candidate_edit(
    cursor: Any,
    candidate: Dict[str, Any],
    question: str,
    answer: str,
    editor: str,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    clean_question = normalize_text(question)
    clean_answer = normalize_text(answer)
    clean_editor = normalize_text(editor)
    if not clean_question:
        raise HTTPException(status_code=400, detail="Edited question cannot be empty.")
    if not clean_answer:
        raise HTTPException(status_code=400, detail="Edited answer cannot be empty.")
    if not clean_editor:
        raise HTTPException(status_code=400, detail="Editor cannot be empty.")

    previous_question = candidate.get("normalized_question")
    previous_answer = candidate.get("suggested_answer")
    changed = clean_question != normalize_text(previous_question) or clean_answer != normalize_text(previous_answer)
    if not changed:
        candidate["normalized_question"] = clean_question
        candidate["suggested_answer"] = clean_answer
        return candidate

    cursor.execute(
        f"""
        INSERT INTO {FAQ_SCHEMA}.faq_candidate_edit_events (
            candidate_id,
            previous_question,
            previous_answer,
            new_question,
            new_answer,
            editor,
            notes
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            candidate["candidate_id"],
            previous_question,
            previous_answer,
            clean_question,
            clean_answer,
            clean_editor,
            notes,
        ),
    )
    cursor.execute(
        f"""
        UPDATE {FAQ_SCHEMA}.faq_candidates
        SET normalized_question = %s,
            suggested_answer = %s,
            was_human_edited = true,
            last_edited_by = %s,
            last_edited_at = now(),
            updated_at = now()
        WHERE candidate_id = %s
        RETURNING
            candidate_id::text,
            workspace_id,
            agent_id::text AS agent_id,
            normalized_question,
            suggested_answer,
            status,
            was_human_edited,
            last_edited_by,
            last_edited_at
        """,
        (clean_question, clean_answer, clean_editor, candidate["candidate_id"]),
    )
    return dict(cursor.fetchone())


def edit_candidate(candidate_id: str, request: SuggestionEditRequest) -> SuggestionEditResponse:
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            candidate = _fetch_candidate_for_update(cursor, candidate_id)
            if not candidate:
                raise HTTPException(status_code=404, detail="Suggestion ID not found.")
            updated = apply_candidate_edit(
                cursor,
                candidate,
                question=request.question,
                answer=request.answer,
                editor=request.editor,
                notes=request.notes,
            )
    return SuggestionEditResponse(
        suggestion_id=updated["candidate_id"],
        question=updated["normalized_question"],
        answer=updated["suggested_answer"],
        editor=updated.get("last_edited_by") or request.editor,
        notes=request.notes,
        was_human_edited=bool(updated.get("was_human_edited")),
        last_edited_at=updated.get("last_edited_at") or datetime.utcnow(),
    )


def promote_candidate_to_existing_faq(cursor: Any, candidate: Dict[str, Any]) -> str:
    if not candidate.get("agent_id"):
        raise HTTPException(
            status_code=409,
            detail="Candidate cannot be approved because it has no agent_id for FAQ promotion.",
        )
    if not normalize_text(candidate.get("suggested_answer")):
        raise HTTPException(
            status_code=409,
            detail="Candidate cannot be approved because suggested_answer is empty.",
        )

    cursor.execute(
        f"""
        INSERT INTO {FAQ_SCHEMA}.existing_faqs (
            faq_id,
            source_agent_id,
            agent_id,
            workspace_id,
            question,
            answer,
            image,
            created_at,
            updated_at,
            deleted_at,
            is_active,
            loaded_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, NULL, now(), now(), NULL, true, now())
        ON CONFLICT (faq_id) DO UPDATE SET
            source_agent_id = EXCLUDED.source_agent_id,
            agent_id = EXCLUDED.agent_id,
            workspace_id = EXCLUDED.workspace_id,
            question = EXCLUDED.question,
            answer = EXCLUDED.answer,
            updated_at = now(),
            deleted_at = NULL,
            is_active = true,
            loaded_at = now()
        RETURNING faq_id::text
        """,
        (
            candidate["candidate_id"],
            candidate["agent_id"],
            candidate["agent_id"],
            candidate["workspace_id"],
            candidate["normalized_question"],
            candidate["suggested_answer"],
        ),
    )
    return str(cursor.fetchone()["faq_id"])


def apply_validation(request: ValidationRequest) -> ValidationResponse:
    if request.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid validation status.")

    reviewed_at = request.reviewed_at or datetime.utcnow()
    promoted_faq_id: Optional[str] = None

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            candidate = _fetch_candidate_for_update(cursor, request.suggestion_id)
            if not candidate:
                raise HTTPException(status_code=404, detail="Suggestion ID not found.")

            previous_status = candidate["status"]
            if request.edited_question is not None or request.edited_answer is not None:
                candidate = apply_candidate_edit(
                    cursor,
                    candidate,
                    question=request.edited_question or candidate.get("normalized_question") or "",
                    answer=request.edited_answer or candidate.get("suggested_answer") or "",
                    editor=request.reviewer,
                    notes=request.notes,
                )
            if request.status == "approved":
                promoted_faq_id = promote_candidate_to_existing_faq(cursor, candidate)

            cursor.execute(
                f"""
                UPDATE {FAQ_SCHEMA}.faq_candidates
                SET status = %s,
                    human_reviewed_by = %s,
                    human_reviewed_at = %s,
                    updated_at = now()
                WHERE candidate_id = %s
                """,
                (request.status, request.reviewer, reviewed_at, request.suggestion_id),
            )
            cursor.execute(
                f"""
                INSERT INTO {FAQ_SCHEMA}.faq_validation_events (
                    candidate_id,
                    event_type,
                    previous_status,
                    new_status,
                    reviewer_identifier,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    request.suggestion_id,
                    request.status,
                    previous_status,
                    request.status,
                    request.reviewer,
                    request.notes,
                ),
            )

    return ValidationResponse(
        suggestion_id=request.suggestion_id,
        reviewer=request.reviewer,
        status=request.status,
        previous_status=previous_status,
        notes=request.notes,
        reviewed_at=reviewed_at,
        promoted_faq_id=promoted_faq_id,
    )


def list_validation_events(workspace_id: Optional[int] = None) -> List[ValidationRecord]:
    filters = ["e.event_type <> 'created'"]
    params: List[Any] = []
    if workspace_id is not None:
        filters.append("c.workspace_id = %s")
        params.append(workspace_id)
    where_clause = " AND ".join(filters)
    query = f"""
        SELECT
            e.validation_event_id::text AS id,
            e.candidate_id::text AS suggestion_id,
            e.reviewer_identifier AS reviewer,
            e.new_status AS status,
            e.previous_status,
            e.notes,
            e.created_at,
            COALESCE(left(c.normalized_question, 180), 'Candidato eliminado') AS question_summary
        FROM {FAQ_SCHEMA}.faq_validation_events AS e
        LEFT JOIN {FAQ_SCHEMA}.faq_candidates AS c
          ON c.candidate_id = e.candidate_id
        WHERE {where_clause}
        ORDER BY e.created_at DESC
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, tuple(params))
            return [ValidationRecord(**dict(row)) for row in cursor.fetchall()]


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
    if status and status not in {"pending", "approved", "rejected", "needs_review"}:
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

    uvicorn.run("validation_service:app", host="127.0.0.1", port=8004, log_level="info")
