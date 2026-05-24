import { useEffect, useMemo, useState } from 'react';
import { Header } from './components/Header';
import { Hero } from './components/Hero';
import { MetricsCards } from './components/MetricsCards';
import { FiltersBar } from './components/FiltersBar';
import { SuggestionList } from './components/SuggestionList';
import { ValidationModal } from './components/ValidationModal';
import { ValidationHistory } from './components/ValidationHistory';
import { EmptyState } from './components/EmptyState';
import { LoadingState } from './components/LoadingState';
import { ErrorState } from './components/ErrorState';
import { Toast } from './components/Toast';

import { useSuggestions } from './hooks/useSuggestions';
import { useValidations } from './hooks/useValidations';
import { Suggestion, ValidationStatus, Workspace } from './types/faq';
import { faqApi } from './services/faqApi';

export default function App() {
  const { data, isLoading, isGenerating, error, fetchSuggestions, generateSuggestions } = useSuggestions();
  const { validations, validateSuggestion, isSubmitting, fetchValidations } = useValidations();

  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | undefined>();
  const [sinceDays, setSinceDays] = useState(90);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<ValidationStatus | 'all'>('all');
  const [selectedSuggestion, setSelectedSuggestion] = useState<Suggestion | null>(null);
  const [toast, setToast] = useState<{ isVisible: boolean; message: string; type: 'success' | 'error' }>({
    isVisible: false,
    message: '',
    type: 'success'
  });

  const selectedWorkspace = useMemo(
    () => workspaces.find(workspace => workspace.workspace_id === selectedWorkspaceId),
    [workspaces, selectedWorkspaceId]
  );

  useEffect(() => {
    let isMounted = true;

    faqApi.getWorkspaces()
      .then((items) => {
        if (!isMounted) return;
        setWorkspaces(items);
        setSelectedWorkspaceId(current => current ?? items[0]?.workspace_id);
      })
      .catch((err) => {
        if (!isMounted) return;
        setWorkspaceError(err instanceof Error ? err.message : 'No se pudieron cargar empresas');
      });

    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedWorkspaceId) return;
    fetchSuggestions(selectedWorkspaceId);
    fetchValidations(selectedWorkspaceId);
  }, [selectedWorkspaceId, fetchSuggestions, fetchValidations]);

  const showToast = (message: string, type: 'success' | 'error') => {
    setToast({ isVisible: true, message, type });
  };

  const handleWorkspaceChange = (workspaceId: number) => {
    setSelectedWorkspaceId(workspaceId);
    setSearch('');
    setStatusFilter('all');
  };

  const handleGenerate = async () => {
    if (!selectedWorkspaceId) return;
    await generateSuggestions(selectedWorkspaceId, sinceDays);
    await fetchValidations(selectedWorkspaceId);
  };

  const handleValidate = async (req: any) => {
    try {
      await validateSuggestion(req, selectedWorkspaceId);
      await fetchSuggestions(selectedWorkspaceId);
      showToast('Sugerencia evaluada exitosamente', 'success');
    } catch (e) {
      showToast('No se pudo enviar la evaluacion', 'error');
      throw e;
    }
  };

  const handleSaveSuggestion = async (suggestionId: string, question: string, answer: string, editor: string, notes?: string) => {
    try {
      await faqApi.updateSuggestion(suggestionId, { question, answer, editor, notes });
      setSelectedSuggestion(current => current && current.id === suggestionId
        ? {
          ...current,
          question,
          answer,
          was_human_edited: true,
          last_edited_by: editor,
          last_edited_at: new Date().toISOString(),
          edit_count: current.edit_count + 1
        }
        : current);
      await fetchSuggestions(selectedWorkspaceId);
      showToast('Cambios guardados', 'success');
    } catch (e) {
      showToast('No se pudieron guardar los cambios', 'error');
      throw e;
    }
  };

  const filteredSuggestions = useMemo(() => {
    if (!data?.suggestions) return [];

    return data.suggestions.filter(suggestion => {
      const textToSearch = `${suggestion.question} ${suggestion.answer}`.toLowerCase();
      if (search && !textToSearch.includes(search.toLowerCase())) return false;
      if (statusFilter !== 'all' && suggestion.status !== statusFilter) return false;
      return true;
    });
  }, [data, search, statusFilter]);

  const visibleError = workspaceError || error;

  return (
    <div className="h-screen bg-slate-50 flex flex-col font-sans overflow-hidden">
      <Header
        onGenerate={handleGenerate}
        isGenerating={isGenerating}
        workspaces={workspaces}
        selectedWorkspaceId={selectedWorkspaceId}
        onWorkspaceChange={handleWorkspaceChange}
        sinceDays={sinceDays}
        onSinceDaysChange={setSinceDays}
      />

      <main className="flex-1 flex overflow-hidden p-6 gap-6">
        <aside className="w-[280px] flex flex-col gap-6 overflow-y-auto pb-4 hide-scrollbar shrink-0">
          <Hero />
          {visibleError && (
            <ErrorState
              message={visibleError}
              onRetry={() => selectedWorkspaceId && fetchSuggestions(selectedWorkspaceId)}
            />
          )}
          {data && <MetricsCards metrics={data} />}
          <FiltersBar
            search={search}
            setSearch={setSearch}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
          />
        </aside>

        <section className="flex-1 flex flex-col gap-4 overflow-hidden relative">
          <div className="flex items-center justify-between shrink-0">
            <div>
              <p className="text-xs font-bold text-indigo-600 uppercase">
                {selectedWorkspace?.workspace_name || 'Selecciona una empresa'}
              </p>
              <h3 className="font-bold text-slate-800">
                Sugerencias {data ? `(${data.suggestions.length})` : ''}
              </h3>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto pb-6 pr-2 hide-scrollbar">
            {isLoading ? (
              <LoadingState />
            ) : !selectedWorkspaceId || !data || data.suggestions.length === 0 ? (
              <EmptyState />
            ) : (
              <SuggestionList
                suggestions={filteredSuggestions}
                onValidate={(suggestion) => setSelectedSuggestion(suggestion)}
              />
            )}
          </div>
        </section>

        <aside className="w-[280px] bg-white border border-slate-200 rounded-lg flex flex-col shadow-sm overflow-hidden shrink-0">
          <ValidationHistory validations={validations} />
        </aside>
      </main>

      <footer className="h-12 bg-indigo-50 border-t border-indigo-100 px-8 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse"></span>
          <p className="text-xs font-medium text-indigo-700 italic">Servicios FastAPI conectados</p>
        </div>
        <div className="text-[10px] font-bold text-indigo-300 uppercase">
          Everwod Technologies
        </div>
      </footer>

      <ValidationModal
        suggestion={selectedSuggestion}
        isOpen={!!selectedSuggestion}
        onClose={() => setSelectedSuggestion(null)}
        onSubmit={handleValidate}
        onSave={handleSaveSuggestion}
        isSubmitting={isSubmitting}
      />

      <Toast
        {...toast}
        onClose={() => setToast(prev => ({ ...prev, isVisible: false }))}
      />
    </div>
  );
}
