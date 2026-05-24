import { useState, useCallback } from 'react';
import { faqApi } from '../services/faqApi';
import { ValidationRecord, ValidationRequest } from '../types/faq';

export function useValidations() {
  const [validations, setValidations] = useState<ValidationRecord[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchValidations = useCallback(async (workspaceId?: number) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await faqApi.getValidations(workspaceId);
      setValidations(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error al cargar validaciones');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const validateSuggestion = useCallback(async (req: ValidationRequest, workspaceId?: number) => {
    setIsSubmitting(true);
    setError(null);
    try {
      await faqApi.validate(req);
      await fetchValidations(workspaceId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error al validar sugerencia');
      throw err;
    } finally {
      setIsSubmitting(false);
    }
  }, [fetchValidations]);

  return {
    validations,
    isLoading,
    isSubmitting,
    error,
    fetchValidations,
    validateSuggestion
  };
}
