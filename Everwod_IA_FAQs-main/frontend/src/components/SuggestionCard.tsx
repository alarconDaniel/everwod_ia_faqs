import { useState } from 'react';
import { Suggestion } from '../types/faq';
import { cn } from '../utils/format';
import { ChevronDown, ChevronUp } from 'lucide-react';

interface SuggestionCardProps {
  suggestion: Suggestion;
  onValidate: (suggestion: Suggestion) => void;
}

const REVIEW_REASON_LABELS: Record<string, string> = {
  answer_partial_support: 'Respuesta con evidencia parcial',
  invalid_knowledge_statement: 'Proposicion de conocimiento a revisar',
  question_answer_misaligned: 'Pregunta detectada con respuesta a revisar',
  question_answer_misaligned_recovered_for_review: 'Respuesta prudente para revisar',
  auto_repaired: 'Pregunta/respuesta corregida automaticamente',
  prudent_answer_repair: 'Pregunta detectada con respuesta a revisar',
  answer_unsupported_recovered_for_review: 'Respuesta prudente para revisar',
  cluster_question_partial_alignment: 'Pregunta alineada parcialmente al cluster',
  auto_repaired_cluster_alignment: 'Pregunta reparada para el cluster',
};

function formatReviewReason(suggestion: Suggestion): string | null {
  if (suggestion.review_reason_code && REVIEW_REASON_LABELS[suggestion.review_reason_code]) {
    return REVIEW_REASON_LABELS[suggestion.review_reason_code];
  }
  const reason = (suggestion.review_reason || '').toLowerCase();
  if (!reason) return null;
  if (reason.includes('cluster de 2')) return 'Cluster pequeno: requiere validacion humana';
  if (reason.includes('soporte historico parcial') || reason.includes('soporte semantico')) return 'Respuesta con evidencia parcial';
  if (reason.includes('respuesta a revisar') || reason.includes('poca evidencia')) return 'Pregunta detectada con respuesta a revisar';
  if (reason.includes('corregida')) return 'Pregunta/respuesta corregida automaticamente';
  return suggestion.review_reason || null;
}

export function SuggestionCard({ suggestion, onValidate }: SuggestionCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const reviewReason = formatReviewReason(suggestion);

  return (
    <div className="bg-white border-2 border-indigo-100 rounded-lg p-5 shadow-sm relative group overflow-hidden">
      <div className="absolute top-5 right-5 flex flex-wrap gap-1 justify-end max-w-[150px]">
        <span className="px-2 py-1 bg-slate-100 text-[10px] font-bold text-slate-600 rounded">ID: {suggestion.id.slice(0,8)}</span>
        <span className="px-2 py-1 bg-green-50 text-[10px] font-bold text-green-700 rounded">Score: {suggestion.cluster_score.toFixed(1)}</span>
        {suggestion.quality_tier && (
          <span className={cn("px-2 py-1 text-[10px] font-bold rounded uppercase",
            suggestion.quality_tier === 'needs_review' ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800')}>
            {suggestion.quality_tier === 'needs_review' ? 'Review' : 'Alta'}
          </span>
        )}
        {suggestion.status !== 'pending' && (
          <span className={cn("px-2 py-1 text-[10px] font-bold rounded uppercase", 
            suggestion.status === 'approved' ? 'bg-emerald-100 text-emerald-800' : 
            suggestion.status === 'rejected' ? 'bg-rose-100 text-rose-800' : 'bg-amber-100 text-amber-800')}>
            {suggestion.status}
          </span>
        )}
        {suggestion.was_human_edited && (
          <span className="px-2 py-1 text-[10px] font-bold rounded uppercase bg-indigo-100 text-indigo-800">
            Editada
          </span>
        )}
        {suggestion.candidate_kind === 'question_with_answer_review' && (
          <span className="px-2 py-1 text-[10px] font-bold rounded uppercase bg-sky-100 text-sky-800">
            Respuesta
          </span>
        )}
      </div>

      <p className="text-[10px] font-bold text-indigo-600 uppercase mb-1 truncate pr-[160px]">{suggestion.company_name}</p>
      <h4 className="text-base font-bold text-slate-900 mb-2 truncate">{suggestion.question}</h4>
      <p className="text-sm text-slate-600 line-clamp-2 mb-4 leading-relaxed bg-slate-50 p-3 rounded-lg border border-slate-100">
        {suggestion.answer}
      </p>
      {suggestion.quality_tier === 'needs_review' && reviewReason && (
        <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 mb-4">
          {reviewReason}
        </p>
      )}
      {suggestion.knowledge_statement && (
        <p className="text-[11px] text-slate-500 mb-4">
          {suggestion.knowledge_statement}
        </p>
      )}
      {suggestion.cluster_intent_statement && (
        <p className="text-[11px] text-slate-500 mb-4">
          {suggestion.cluster_intent_statement}
        </p>
      )}

      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <button 
            onClick={() => setIsExpanded(!isExpanded)}
            className="flex items-center gap-2 group hover:opacity-80 transition-opacity focus:outline-none"
          >
             <div className="flex -space-x-2">
               <div className="w-6 h-6 rounded-full bg-slate-200 border-2 border-white flex items-center justify-center text-[8px] font-bold">+{suggestion.cluster_size}</div>
             </div>
             <span className="text-[10px] text-slate-400 font-medium tracking-tight flex items-center">
                Ejemplos en el cluster
                {isExpanded ? <ChevronUp className="w-3 h-3 ml-1" /> : <ChevronDown className="w-3 h-3 ml-1" />}
             </span>
          </button>
        </div>
        <div className="flex justify-end w-full sm:w-auto gap-2">
          <button 
            onClick={() => onValidate(suggestion)}
            className="px-3 py-1.5 bg-indigo-600 text-white text-xs font-bold rounded-lg hover:bg-indigo-700 shadow-sm whitespace-nowrap"
          >
            Validar FAQ
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className="mt-4 pt-4 border-t border-slate-100">
          <ul className="space-y-2">
            {suggestion.support_examples.map((example, idx) => (
              <li key={idx} className="text-[11px] text-slate-500 flex items-start">
                <span className="w-1.5 h-1.5 rounded-full bg-indigo-200 mt-1 mr-2 shrink-0" />
                {example}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
