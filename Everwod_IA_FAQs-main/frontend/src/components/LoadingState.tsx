import { Loader2 } from 'lucide-react';

export function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center py-20 bg-white rounded-2xl border border-slate-200 shadow-sm mb-8">
      <Loader2 className="w-10 h-10 text-indigo-600 animate-spin mb-4" />
      <h3 className="text-lg font-medium text-slate-800">Cargando sugerencias...</h3>
      <p className="text-sm text-slate-500 mt-1">Por favor espera un momento.</p>
    </div>
  );
}
