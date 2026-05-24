import { FileQuestion } from 'lucide-react';

export function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-20 px-4 bg-white rounded-2xl border border-slate-200 border-dashed mb-8">
      <div className="bg-indigo-50 p-4 rounded-full mb-4">
        <FileQuestion className="w-8 h-8 text-indigo-500" />
      </div>
      <h3 className="text-xl font-bold text-slate-900 mb-2">No hay sugerencias disponibles</h3>
      <p className="text-slate-500 text-center max-w-md">
        Aún no se han generado sugerencias de FAQs o no hay datos suficientes de los chats. 
        Haz clic en "Generar nuevas sugerencias" para intentar extraer conocimiento nuevo.
      </p>
    </div>
  );
}
