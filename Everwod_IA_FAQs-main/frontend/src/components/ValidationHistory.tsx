import { ValidationRecord } from '../types/faq';
import { formatDate } from '../utils/format';

interface ValidationHistoryProps {
  validations: ValidationRecord[];
}

export function ValidationHistory({ validations }: ValidationHistoryProps) {
  return (
    <>
      <div className="p-4 border-b border-slate-100 shrink-0">
        <h3 className="text-sm font-bold text-slate-800">Historial de Validaciones</h3>
      </div>
      <div className="flex-1 p-4 flex flex-col gap-4 overflow-y-auto">
        {validations.length === 0 ? (
           <p className="text-xs text-slate-500 text-center py-4">No hay validaciones recientes</p>
        ) : validations.map((val, idx) => (
          <div key={val.id} className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                val.status === 'approved' ? 'text-green-600 bg-green-50' :
                val.status === 'rejected' ? 'text-red-600 bg-red-50' : 
                'text-amber-600 bg-amber-50'
              }`}>
                {val.status}
              </span>
              <span className="text-[10px] text-slate-400" title={formatDate(val.created_at)}>
                {new Date(val.created_at).toLocaleDateString()}
              </span>
            </div>
            <p className="text-xs font-semibold text-slate-800 line-clamp-2" title={val.question_summary}>{val.question_summary}</p>
            <p className="text-[10px] text-slate-500">Revisor: {val.reviewer}</p>
            {val.notes && (
               <p className="text-[10px] text-slate-500 italic mt-0.5">"{val.notes}"</p>
            )}
            
            {idx < validations.length - 1 && (
              <div className="h-px bg-slate-100 w-full mt-3"></div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
