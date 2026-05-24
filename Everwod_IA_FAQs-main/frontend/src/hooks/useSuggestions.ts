import { useState, useCallback } from 'react';
import { faqApi } from '../services/faqApi';
import { SuggestionsResponse } from '../types/faq';

export function useSuggestions() {
  const [data, setData] = useState<SuggestionsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchSuggestions = useCallback(async (workspaceId?: number) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await faqApi.getSuggestions(workspaceId);
      setData(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error desconocido al cargar sugerencias');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const generateSuggestions = useCallback(async (workspaceId: number, sinceDays: number) => {
    setIsGenerating(true);
    setError(null);
    try {
      const request = { since_days: sinceDays, workspace_id: workspaceId };
      await faqApi.ingest(request);
      const response = await faqApi.suggest(request);
      setData(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error al generar sugerencias');
    } finally {
      setIsGenerating(false);
    }
  }, []);

  return {
    data,
    isLoading,
    isGenerating,
    error,
    fetchSuggestions,
    generateSuggestions
  };
}
