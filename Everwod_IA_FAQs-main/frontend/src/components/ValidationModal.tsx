import { useEffect, useState } from 'react';
import { X, Check, AlertTriangle, XCircle, Loader2, Save } from 'lucide-react';
import { Suggestion, ValidationRequest } from '../types/faq';
import { cn } from '../utils/format';

interface ValidationModalProps {
  suggestion: Suggestion | null;
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (req: ValidationRequest) => Promise<void>;
  onSave: (suggestionId: string, question: string, answer: string, editor: string, notes?: string) => Promise<void>;
  isSubmitting: boolean;
}

export function ValidationModal({ suggestion, isOpen, onClose, onSubmit, onSave, isSubmitting }: ValidationModalProps) {
  const [reviewer, setReviewer] = useState('');
  const [notes, setNotes] = useState('');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [status, setStatus] = useState<ValidationRequest['status'] | null>(null);
  const [error, setError] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (!suggestion || !isOpen) return;
    setQuestion(suggestion.question);
    setAnswer(suggestion.answer);
    setNotes('');
    setStatus(null);
    setError('');
  }, [suggestion, isOpen]);

  if (!isOpen || !suggestion) return null;

  const hasEdits = question.trim() !== suggestion.question.trim() || answer.trim() !== suggestion.answer.trim();

  const ensureReviewerAndContent = () => {
    if (!reviewer.trim()) {
      setError('Por favor, ingresa tu nombre como revisor.');
      return false;
    }
    if (!question.trim() || !answer.trim()) {
      setError('La pregunta y la respuesta no pueden estar vacias.');
      return false;
    }
    return true;
  };

  const handleSave = async () => {
    if (!ensureReviewerAndContent()) return;
    setIsSaving(true);
    try {
      await onSave(suggestion.id, question.trim(), answer.trim(), reviewer.trim(), notes.trim() || undefined);
      setError('');
    } catch {
      setError('Ocurrio un error al guardar los cambios.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSubmit = async () => {
    if (!ensureReviewerAndContent()) return;
    if (!status) {
      setError('Debes seleccionar una accion de evaluacion.');
      return;
    }

    try {
      await onSubmit({
        suggestion_id: suggestion.id,
        reviewer: reviewer.trim(),
        status,
        notes: notes.trim(),
        edited_question: hasEdits ? question.trim() : undefined,
        edited_answer: hasEdits ? answer.trim() : undefined
      });
      setReviewer('');
      setNotes('');
      setStatus(null);
      setError('');
      onClose();
    } catch {
      setError('Ocurrio un error al enviar la evaluacion.');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[90vh]">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between bg-slate-50 shrink-0">
          <div>
            <h2 className="text-lg font-bold text-slate-800">Validar FAQ</h2>
            {(suggestion.was_human_edited || hasEdits) && (
              <p className="text-xs font-semibold text-indigo-600">
                {hasEdits ? 'Cambios sin guardar' : 'Ajuste humano guardado'}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-200/50 rounded-full transition-colors"
            aria-label="Cerrar"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 overflow-y-auto">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Tu nombre (revisor) *</label>
              <input
                type="text"
                value={reviewer}
                onChange={(e) => { setReviewer(e.target.value); setError(''); }}
                placeholder="Ej. Ana Garcia"
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-shadow"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Pregunta editable *</label>
              <textarea
                value={question}
                onChange={(e) => { setQuestion(e.target.value); setError(''); }}
                rows={2}
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-shadow resize-none text-sm"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Respuesta editable *</label>
              <textarea
                value={answer}
                onChange={(e) => { setAnswer(e.target.value); setError(''); }}
                rows={4}
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-shadow resize-none text-sm"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-2">Accion de evaluacion *</label>
              <div className="grid grid-cols-3 gap-3">
                <button
                  type="button"
                  onClick={() => { setStatus('approved'); setError(''); }}
                  className={cn(
                    'flex flex-col items-center justify-center p-3 rounded-lg border transition-all',
                    status === 'approved'
                      ? 'bg-emerald-50 border-emerald-500 text-emerald-700 ring-1 ring-emerald-500'
                      : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'
                  )}
                >
                  <Check className="w-5 h-5 mb-1" />
                  <span className="text-xs font-semibold">Aprobar</span>
                </button>
                <button
                  type="button"
                  onClick={() => { setStatus('needs_review'); setError(''); }}
                  className={cn(
                    'flex flex-col items-center justify-center p-3 rounded-lg border transition-all',
                    status === 'needs_review'
                      ? 'bg-amber-50 border-amber-500 text-amber-700 ring-1 ring-amber-500'
                      : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'
                  )}
                >
                  <AlertTriangle className="w-5 h-5 mb-1" />
                  <span className="text-xs font-semibold">Revisar</span>
                </button>
                <button
                  type="button"
                  onClick={() => { setStatus('rejected'); setError(''); }}
                  className={cn(
                    'flex flex-col items-center justify-center p-3 rounded-lg border transition-all',
                    status === 'rejected'
                      ? 'bg-rose-50 border-rose-500 text-rose-700 ring-1 ring-rose-500'
                      : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'
                  )}
                >
                  <XCircle className="w-5 h-5 mb-1" />
                  <span className="text-xs font-semibold">Rechazar</span>
                </button>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Notas (opcional)</label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Detalle por que aprobo, rechazo o necesita ajustes..."
                rows={3}
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-shadow resize-none"
              />
            </div>

            {error && (
              <div className="p-3 bg-red-50 text-red-700 text-sm rounded-lg flex items-start">
                <AlertTriangle className="w-4 h-4 mr-2 shrink-0 mt-0.5" />
                {error}
              </div>
            )}
          </div>
        </div>

        <div className="p-6 border-t border-slate-200 bg-slate-50 flex flex-wrap justify-end gap-3 shrink-0">
          <button
            onClick={onClose}
            disabled={isSubmitting || isSaving}
            className="px-5 py-2 font-medium text-slate-600 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors"
          >
            Cancelar
          </button>
          <button
            onClick={handleSave}
            disabled={isSubmitting || isSaving || !hasEdits || !reviewer.trim()}
            className="px-5 py-2 font-medium text-indigo-700 bg-white border border-indigo-200 rounded-lg hover:bg-indigo-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
          >
            {isSaving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
            Guardar cambios
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSubmitting || isSaving || !status || !reviewer.trim()}
            className="px-5 py-2 font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
          >
            {isSubmitting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            Confirmar evaluacion
          </button>
        </div>
      </div>
    </div>
  );
}
