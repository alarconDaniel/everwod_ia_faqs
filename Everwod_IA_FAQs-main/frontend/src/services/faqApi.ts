import { IngestRequest, SuggestionEditRequest, SuggestionsResponse, ValidationRecord, ValidationRequest, Workspace } from '../types/faq';
import { mockFaqApi } from './mockFaqApi';

const INGEST_API_URL = import.meta.env.VITE_INGEST_API_URL || 'http://127.0.0.1:8001';
const SUGGESTION_API_URL = import.meta.env.VITE_SUGGESTION_API_URL || 'http://127.0.0.1:8003';
const VALIDATION_API_URL = import.meta.env.VITE_VALIDATION_API_URL || 'http://127.0.0.1:8004';
const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true';

function withQuery(baseUrl: string, params: Record<string, string | number | boolean | undefined | null>) {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      searchParams.set(key, String(value));
    }
  });
  const query = searchParams.toString();
  return query ? `${baseUrl}?${query}` : baseUrl;
}

async function parseApiError(res: Response, fallback: string): Promise<Error> {
  try {
    const payload = await res.json();
    return new Error(payload.detail || fallback);
  } catch {
    return new Error(fallback);
  }
}

const realFaqApi = {
  ingest: async (req: IngestRequest): Promise<void> => {
    const res = await fetch(`${INGEST_API_URL}/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req)
    });
    if (!res.ok) throw await parseApiError(res, 'Error en ingesta');
  },

  suggest: async (req: IngestRequest): Promise<SuggestionsResponse> => {
    const res = await fetch(`${SUGGESTION_API_URL}/suggest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req)
    });
    if (!res.ok) throw await parseApiError(res, 'Error al sugerir FAQs');
    return res.json();
  },

  getSuggestions: async (workspaceId?: number): Promise<SuggestionsResponse> => {
    const res = await fetch(withQuery(`${SUGGESTION_API_URL}/suggestions`, { workspace_id: workspaceId }));
    if (!res.ok) throw await parseApiError(res, 'Error al obtener sugerencias');
    return res.json();
  },

  getWorkspaces: async (): Promise<Workspace[]> => {
    const res = await fetch(`${SUGGESTION_API_URL}/workspaces`);
    if (!res.ok) throw await parseApiError(res, 'Error al obtener empresas');
    return res.json();
  },

  getValidations: async (workspaceId?: number): Promise<ValidationRecord[]> => {
    const res = await fetch(withQuery(`${VALIDATION_API_URL}/validations`, { workspace_id: workspaceId }));
    if (!res.ok) throw await parseApiError(res, 'Error al obtener validaciones');
    return res.json();
  },

  validate: async (req: ValidationRequest): Promise<void> => {
    const res = await fetch(`${VALIDATION_API_URL}/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req)
    });
    if (!res.ok) throw await parseApiError(res, 'Error en validacion');
  },

  updateSuggestion: async (candidateId: string, req: SuggestionEditRequest): Promise<void> => {
    const res = await fetch(`${VALIDATION_API_URL}/suggestions/${candidateId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req)
    });
    if (!res.ok) throw await parseApiError(res, 'Error al guardar cambios');
  }
};

export const faqApi = USE_MOCKS ? mockFaqApi : realFaqApi;
