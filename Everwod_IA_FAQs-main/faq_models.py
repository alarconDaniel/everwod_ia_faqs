from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


ValidationStatus = Literal["approved", "rejected", "needs_review"]
CandidateStatus = Literal["pending", "approved", "rejected", "needs_review"]
QualityTier = Literal["high_confidence", "needs_review"]
CandidateKind = Literal["full_faq", "question_with_answer_review"]
SupportLevel = Literal["strong", "partial", "weak", "none"]
ClusterQuestionAlignment = Literal["strong_alignment", "partial_alignment", "misaligned"]


class IngestRequest(BaseModel):
    limit: Optional[int] = Field(None, ge=1)
    since_days: int = Field(90, ge=1, le=3650)
    workspace_id: Optional[int] = None
    agent_id: Optional[str] = None


class IngestResponse(BaseModel):
    imported_records: int
    output_file: str
    source_schema: str = "faq_mvp"
    limit: Optional[int] = None
    effective_limit: Optional[int] = None
    analysis_limit_mode: Optional[str] = None
    raw_messages_found: Optional[int] = None
    raw_messages_processed: Optional[int] = None
    total_messages_found_before_limit: Optional[int] = None
    total_messages_processed_after_limit: Optional[int] = None
    conversations_found: Optional[int] = None
    conversations_processed: Optional[int] = None
    conversations_found_before_limit: Optional[int] = None
    conversations_processed_after_limit: Optional[int] = None
    truncation_applied: bool = False
    truncation_ratio: float = 0.0


class EncodeRequest(BaseModel):
    texts: List[str]


class EncodeResponse(BaseModel):
    embeddings: List[List[float]]
    count: int


class SuggestionResponse(BaseModel):
    id: str
    company_id: str
    company_name: Optional[str] = None
    workspace_id: int
    agent_id: Optional[str] = None
    question: str
    answer: str
    cluster_size: int
    support_examples: List[str]
    cluster_score: float
    quality_tier: Optional[QualityTier] = None
    review_reason: Optional[str] = None
    review_reason_code: Optional[str] = None
    generation_confidence: Optional[float] = None
    cluster_intent_statement: Optional[str] = None
    knowledge_statement: Optional[str] = None
    cluster_question_alignment: Optional[ClusterQuestionAlignment] = None
    candidate_kind: CandidateKind = "full_faq"
    requires_answer_review: bool = False
    support_level: Optional[SupportLevel] = None
    repaired: bool = False
    since_days_used: Optional[int] = None
    was_human_edited: bool = False
    last_edited_by: Optional[str] = None
    last_edited_at: Optional[datetime] = None
    edit_count: int = 0
    status: CandidateStatus = "pending"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    run_id: Optional[str] = None


class SuggestionSummary(BaseModel):
    company_count: int
    cluster_count: int
    total_examples: int
    average_cluster_size: float
    silhouette_score: Optional[float]
    suggestions: List[SuggestionResponse]
    run_id: Optional[str] = None
    limit: Optional[int] = None
    effective_limit: Optional[int] = None
    analysis_limit_mode: Optional[str] = None
    raw_messages_found: Optional[int] = None
    raw_messages_processed: Optional[int] = None
    total_messages_found_before_limit: Optional[int] = None
    total_messages_processed_after_limit: Optional[int] = None
    conversations_found: Optional[int] = None
    conversations_processed: Optional[int] = None
    conversations_found_before_limit: Optional[int] = None
    conversations_processed_after_limit: Optional[int] = None
    truncation_applied: bool = False
    truncation_ratio: float = 0.0
    hard_rejected: int = 0
    persisted_high_confidence: int = 0
    persisted_needs_review: int = 0
    persisted_question_with_answer_review: int = 0
    cluster_question_alignment_strong: int = 0
    cluster_question_alignment_partial: int = 0
    cluster_question_alignment_failed: int = 0
    repair_alignment_attempts: int = 0
    repair_alignment_successes: int = 0
    support_examples_deduplicated: int = 0
    candidates_rejected_due_to_cluster_misalignment: int = 0
    candidates_recovered_after_alignment_repair: int = 0


class WorkspaceResponse(BaseModel):
    workspace_id: int
    workspace_name: str
    company_id: str


class ValidationRequest(BaseModel):
    suggestion_id: str
    reviewer: str = Field(..., min_length=1, max_length=255)
    status: ValidationStatus
    notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    edited_question: Optional[str] = None
    edited_answer: Optional[str] = None


class ValidationResponse(BaseModel):
    suggestion_id: str
    reviewer: str
    status: ValidationStatus
    previous_status: CandidateStatus
    notes: Optional[str] = None
    reviewed_at: datetime
    promoted_faq_id: Optional[str] = None


class ValidationRecord(BaseModel):
    id: str
    suggestion_id: str
    reviewer: Optional[str] = None
    status: CandidateStatus
    previous_status: Optional[CandidateStatus] = None
    notes: Optional[str] = None
    created_at: datetime
    question_summary: str


class SuggestionEditRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    answer: str = Field(..., min_length=3, max_length=2000)
    editor: str = Field(..., min_length=1, max_length=255)
    notes: Optional[str] = None


class SuggestionEditResponse(BaseModel):
    suggestion_id: str
    question: str
    answer: str
    editor: str
    notes: Optional[str] = None
    was_human_edited: bool
    last_edited_at: datetime
