import { AlertTriangle, RefreshCw } from 'lucide-react';

interface ErrorStateProps {
  message: string;
  onRetry: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4 bg-rose-50 rounded-2xl border border-rose-200 mb-8 text-center">
      <div className="bg-rose-100 p-3 rounded-full mb-4">
        <AlertTriangle className="w-8 h-8 text-rose-600" />
      </div>
      <h3 className="text-xl font-bold text-slate-900 mb-2">Ocurrió un error</h3>
      <p className="text-rose-700 max-w-md mb-6">{message}</p>
      <button
        onClick={onRetry}
        className="flex items-center px-4 py-2 bg-rose-600 text-white rounded-lg hover:bg-rose-700 transition-colors font-medium text-sm"
      >
        <RefreshCw className="w-4 h-4 mr-2" />
        Reintentar
      </button>
    </div>
  );
}
