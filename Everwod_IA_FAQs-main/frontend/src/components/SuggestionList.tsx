import { Suggestion } from '../types/faq';
import { SuggestionCard } from './SuggestionCard';

interface SuggestionListProps {
  suggestions: Suggestion[];
  onValidate: (suggestion: Suggestion) => void;
}

export function SuggestionList({ suggestions, onValidate }: SuggestionListProps) {
  if (suggestions.length === 0) {
    return (
      <div className="text-center py-12 bg-white rounded-xl border border-slate-200 border-dashed">
        <p className="text-slate-500 font-medium">No se encontraron sugerencias con los filtros actuales.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 overflow-hidden">
      {suggestions.map((suggestion) => (
        <SuggestionCard 
          key={suggestion.id} 
          suggestion={suggestion} 
          onValidate={onValidate}
        />
      ))}
    </div>
  );
}
