import { SuggestionsResponse } from '../types/faq';

interface MetricsCardsProps {
  metrics: Omit<SuggestionsResponse, 'suggestions'> | null;
}

export function MetricsCards({ metrics }: MetricsCardsProps) {
  if (!metrics) return null;

  const formatter = new Intl.NumberFormat('es-CO');
  const rawMessages = metrics.raw_messages_found ?? metrics.total_messages_found_before_limit ?? null;
  const processedMessages = metrics.raw_messages_processed ?? metrics.total_messages_processed_after_limit ?? null;
  const rawConversations = metrics.conversations_found ?? metrics.conversations_found_before_limit ?? null;
  const processedConversations = metrics.conversations_processed ?? metrics.conversations_processed_after_limit ?? null;
  const showTruncationNotice = Boolean(metrics.truncation_applied && rawMessages && processedMessages);

  return (
    <div className="shrink-0 space-y-3">
      {showTruncationNotice && (
        <div className="bg-amber-50 p-4 rounded-lg border border-amber-200 shadow-sm text-xs text-amber-900 leading-relaxed">
          <p className="font-bold">Analisis truncado</p>
          <p>Se analizaron {formatter.format(processedMessages || 0)} de {formatter.format(rawMessages || 0)} mensajes.</p>
          {rawConversations !== null && processedConversations !== null && (
            <p>Se procesaron {formatter.format(processedConversations)} de {formatter.format(rawConversations)} conversaciones.</p>
          )}
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-white p-4 rounded-lg border border-slate-200 shadow-sm">
          <p className="text-[10px] uppercase font-bold text-slate-400 tracking-tighter">Empresas</p>
          <p className="text-xl font-bold text-slate-800">{metrics.company_count}</p>
        </div>
        <div className="bg-white p-4 rounded-lg border border-slate-200 shadow-sm">
          <p className="text-[10px] uppercase font-bold text-slate-400 tracking-tighter">Clusters</p>
          <p className="text-xl font-bold text-slate-800">{metrics.cluster_count}</p>
        </div>
        <div className="bg-white p-4 rounded-lg border border-slate-200 shadow-sm">
          <p className="text-[10px] uppercase font-bold text-slate-400 tracking-tighter">Ejemplos</p>
          <p className="text-xl font-bold text-slate-800">
            {metrics.total_examples >= 1000 ? `${(metrics.total_examples / 1000).toFixed(1)}k` : metrics.total_examples}
          </p>
        </div>
        <div className="bg-white p-4 rounded-lg border border-slate-200 shadow-sm">
          <p
  className="text-[10px] uppercase font-bold text-slate-400 tracking-tighter"
  title="Métrica calculada sobre los clusters generados por el pipeline. Puede diferir del número de sugerencias visibles."
>
  Silhouette pipeline
</p>
          <p className="text-xl font-bold text-indigo-600">
            {typeof metrics.silhouette_score === 'number' && Number.isFinite(metrics.silhouette_score)
              ? metrics.silhouette_score.toFixed(2)
              : 'N/D'}
          </p>
        </div>
      </div>
    </div>
  );
}
