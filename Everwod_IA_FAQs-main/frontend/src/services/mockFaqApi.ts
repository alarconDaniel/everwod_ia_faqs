import { IngestRequest, Suggestion, SuggestionEditRequest, SuggestionsResponse, ValidationRecord, ValidationRequest, Workspace } from '../types/faq';

let mockSuggestions: Suggestion[] = [];
let mockValidations: ValidationRecord[] = [];
let hasIngested = false;

const generateMockSuggestions = (): Suggestion[] => [
  {
    id: 'uuid-1',
    company_id: '123',
    company_name: 'Gimnasio FitLife',
    workspace_id: 123,
    agent_id: '00000000-0000-0000-0000-000000000123',
    question: '¿Cómo puedo reservar una clase?',
    answer: 'Las clases pueden reservarse mediante la app o por el canal oficial de WhatsApp, segun disponibilidad del horario.',
    cluster_size: 24,
    support_examples: ['¿Cómo reservo una clase?', 'Quiero agendar una clase para mañana', '¿Puedo reservar por WhatsApp?'],
    cluster_score: 82.5,
    quality_tier: 'high_confidence',
    generation_confidence: 0.82,
    knowledge_statement: 'Las clases pueden reservarse segun disponibilidad.',
    was_human_edited: false,
    edit_count: 0,
    status: 'pending'
  },
  {
    id: 'uuid-2',
    company_id: '123',
    company_name: 'Gimnasio FitLife',
    workspace_id: 123,
    agent_id: '00000000-0000-0000-0000-000000000123',
    question: '¿Cuáles son los horarios de atención?',
    answer: 'Los horarios de atención deben confirmarse con el equipo del gimnasio antes de publicarse como FAQ definitiva.',
    cluster_size: 45,
    support_examples: ['¿A qué hora abren?', 'Horarios por favor', '¿Están abiertos los domingos?'],
    cluster_score: 95.0,
    quality_tier: 'needs_review',
    review_reason: 'confianza media del LLM',
    generation_confidence: 0.58,
    knowledge_statement: 'Los horarios deben confirmarse antes de publicarse.',
    was_human_edited: false,
    edit_count: 0,
    status: 'pending'
  }
];

const mockWorkspaces: Workspace[] = [
  { workspace_id: 123, workspace_name: 'Gimnasio FitLife', company_id: '123' },
];

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

function buildSummary(): SuggestionsResponse {
  return {
    company_count: new Set(mockSuggestions.map(s => s.company_id)).size,
    cluster_count: mockSuggestions.length,
    total_examples: mockSuggestions.reduce((acc, curr) => acc + curr.cluster_size, 0),
    average_cluster_size: mockSuggestions.length > 0
      ? Math.round(mockSuggestions.reduce((acc, curr) => acc + curr.cluster_size, 0) / mockSuggestions.length)
      : 0,
    silhouette_score: mockSuggestions.length > 1 ? 0.74 : null,
    suggestions: mockSuggestions,
    run_id: 'mock-run'
  };
}

export const mockFaqApi = {
  ingest: async (_req: IngestRequest): Promise<void> => {
    await delay(800);
    hasIngested = true;
  },

  suggest: async (_req: IngestRequest): Promise<SuggestionsResponse> => {
    await delay(1000);
    if (hasIngested && mockSuggestions.length === 0) {
      mockSuggestions = generateMockSuggestions();
    }
    return buildSummary();
  },

  getSuggestions: async (_workspaceId?: number): Promise<SuggestionsResponse> => {
    await delay(300);
    return buildSummary();
  },

  getWorkspaces: async (): Promise<Workspace[]> => {
    await delay(200);
    return mockWorkspaces;
  },

  getValidations: async (_workspaceId?: number): Promise<ValidationRecord[]> => {
    await delay(250);
    return [...mockValidations].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  },

  validate: async (req: ValidationRequest): Promise<void> => {
    await delay(300);
    const suggestion = mockSuggestions.find(s => s.id === req.suggestion_id);
    if (!suggestion) {
      throw new Error('Suggestion not found');
    }
    const previousStatus = suggestion.status;
    if (req.edited_question || req.edited_answer) {
      suggestion.question = req.edited_question || suggestion.question;
      suggestion.answer = req.edited_answer || suggestion.answer;
      suggestion.was_human_edited = true;
      suggestion.last_edited_by = req.reviewer;
      suggestion.last_edited_at = new Date().toISOString();
      suggestion.edit_count += 1;
    }
    suggestion.status = req.status;
    mockValidations.push({
      ...req,
      id: `val_${Date.now()}`,
      created_at: new Date().toISOString(),
      previous_status: previousStatus,
      question_summary: suggestion.question
    });
  },

  updateSuggestion: async (candidateId: string, req: SuggestionEditRequest): Promise<void> => {
    await delay(250);
    const suggestion = mockSuggestions.find(s => s.id === candidateId);
    if (!suggestion) {
      throw new Error('Suggestion not found');
    }
    suggestion.question = req.question;
    suggestion.answer = req.answer;
    suggestion.was_human_edited = true;
    suggestion.last_edited_by = req.editor;
    suggestion.last_edited_at = new Date().toISOString();
    suggestion.edit_count += 1;
  }
};
