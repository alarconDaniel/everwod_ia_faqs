ALTER TABLE faq_mvp.faq_candidates
    ADD COLUMN IF NOT EXISTS was_human_edited boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS last_edited_by varchar(255),
    ADD COLUMN IF NOT EXISTS last_edited_at timestamptz;

CREATE TABLE IF NOT EXISTS faq_mvp.faq_candidate_edit_events (
    edit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id uuid NOT NULL REFERENCES faq_mvp.faq_candidates(candidate_id) ON UPDATE CASCADE ON DELETE CASCADE,
    previous_question text,
    previous_answer text,
    new_question text NOT NULL,
    new_answer text NOT NULL,
    editor varchar(255),
    notes text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_faq_candidate_edit_events_candidate_id
    ON faq_mvp.faq_candidate_edit_events(candidate_id);

CREATE INDEX IF NOT EXISTS idx_faq_candidate_edit_events_created_at
    ON faq_mvp.faq_candidate_edit_events(created_at);

COMMENT ON COLUMN faq_mvp.faq_candidates.was_human_edited IS
  'true cuando un revisor humano edito pregunta o respuesta sugerida.';
COMMENT ON COLUMN faq_mvp.faq_candidates.last_edited_by IS
  'Ultimo editor humano que ajusto pregunta o respuesta sugerida.';
COMMENT ON COLUMN faq_mvp.faq_candidates.last_edited_at IS
  'Fecha/hora de la ultima edicion humana.';
COMMENT ON TABLE faq_mvp.faq_candidate_edit_events IS
  'Historial auditable de ediciones humanas sobre candidatos FAQ antes de validacion.';
