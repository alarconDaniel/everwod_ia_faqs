export type ValidationStatus = 'pending' | 'approved' | 'rejected' | 'needs_review';

export interface Suggestion {
  id: string;
  company_id: string;
  company_name: string;
  workspace_id: number;
  agent_id?: string | null;
  question: string;
  answer: string;
  cluster_size: number;
  support_examples: string[];
  cluster_score: number;
  quality_tier?: 'high_confidence' | 'needs_review' | null;
  review_reason?: string | null;
  review_reason_code?: string | null;
  generation_confidence?: number | null;
  cluster_intent_statement?: string | null;
  knowledge_statement?: string | null;
  cluster_question_alignment?: 'strong_alignment' | 'partial_alignment' | 'misaligned' | null;
  candidate_kind?: 'full_faq' | 'question_with_answer_review';
  requires_answer_review?: boolean;
  support_level?: 'strong' | 'partial' | 'weak' | 'none' | null;
  repaired?: boolean;
  since_days_used?: number | null;
  was_human_edited: boolean;
  last_edited_by?: string | null;
  last_edited_at?: string | null;
  edit_count: number;
  status: ValidationStatus;
  created_at?: string;
  updated_at?: string;
  run_id?: string | null;
}

export interface SuggestionsResponse {
  company_count: number;
  cluster_count: number;
  total_examples: number;
  average_cluster_size: number;
  silhouette_score: number | null;
  suggestions: Suggestion[];
  run_id?: string | null;
  limit?: number | null;
  effective_limit?: number | null;
  analysis_limit_mode?: string | null;
  raw_messages_found?: number | null;
  raw_messages_processed?: number | null;
  total_messages_found_before_limit?: number | null;
  total_messages_processed_after_limit?: number | null;
  conversations_found?: number | null;
  conversations_processed?: number | null;
  conversations_found_before_limit?: number | null;
  conversations_processed_after_limit?: number | null;
  truncation_applied?: boolean;
  truncation_ratio?: number;
  hard_rejected?: number;
  persisted_high_confidence?: number;
  persisted_needs_review?: number;
  persisted_question_with_answer_review?: number;
  cluster_question_alignment_strong?: number;
  cluster_question_alignment_partial?: number;
  cluster_question_alignment_failed?: number;
  repair_alignment_attempts?: number;
  repair_alignment_successes?: number;
  support_examples_deduplicated?: number;
  candidates_rejected_due_to_cluster_misalignment?: number;
  candidates_recovered_after_alignment_repair?: number;
}

export interface ValidationRequest {
  suggestion_id: string;
  reviewer: string;
  status: 'approved' | 'rejected' | 'needs_review';
  notes?: string;
  edited_question?: string;
  edited_answer?: string;
}

export interface SuggestionEditRequest {
  question: string;
  answer: string;
  editor: string;
  notes?: string;
}

export interface ValidationRecord extends ValidationRequest {
  id: string;
  created_at: string;
  question_summary: string;
  previous_status?: ValidationStatus | null;
}

export interface IngestRequest {
  limit?: number | null;
  since_days: number;
  workspace_id?: number;
  agent_id?: string | null;
}

export interface Workspace {
  workspace_id: number;
  workspace_name: string;
  company_id: string;
}
