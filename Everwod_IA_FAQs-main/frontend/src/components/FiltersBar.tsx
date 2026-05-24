import { ValidationStatus } from '../types/faq';

interface FiltersBarProps {
  search: string;
  setSearch: (val: string) => void;
  statusFilter: ValidationStatus | 'all';
  setStatusFilter: (val: ValidationStatus | 'all') => void;
}

export function FiltersBar({
  search,
  setSearch,
  statusFilter,
  setStatusFilter,
}: FiltersBarProps) {
  return (
    <div className="bg-white p-5 rounded-lg border border-slate-200 shadow-sm flex flex-col gap-4 shrink-0">
      <div>
        <label className="text-xs font-bold text-slate-500 uppercase block mb-2">Buscar sugerencia</label>
        <input
          type="text"
          placeholder="Ej: horario, reserva..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
        />
      </div>
      <div>
        <label className="text-xs font-bold text-slate-500 uppercase block mb-2">Filtrar por estado</label>
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value as ValidationStatus | 'all')}
          className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
        >
          <option value="all">Todos los estados</option>
          <option value="pending">Pendientes</option>
          <option value="approved">Aprobados</option>
          <option value="rejected">Rechazados</option>
          <option value="needs_review">Requiere revision</option>
        </select>
      </div>
    </div>
  );
}
