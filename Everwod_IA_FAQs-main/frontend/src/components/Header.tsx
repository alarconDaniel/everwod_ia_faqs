import { Brain, Loader2, RefreshCw } from 'lucide-react';
import { Workspace } from '../types/faq';

interface HeaderProps {
  onGenerate: () => void;
  isGenerating: boolean;
  workspaces: Workspace[];
  selectedWorkspaceId?: number;
  onWorkspaceChange: (workspaceId: number) => void;
  sinceDays: number;
  onSinceDaysChange: (days: number) => void;
}

const SINCE_DAY_OPTIONS = [7, 30, 60, 90, 180, 365];

export function Header({
  onGenerate,
  isGenerating,
  workspaces,
  selectedWorkspaceId,
  onWorkspaceChange,
  sinceDays,
  onSinceDaysChange
}: HeaderProps) {
  return (
    <header className="min-h-20 bg-white border-b border-slate-200 px-8 py-3 flex items-center justify-between gap-6 shrink-0">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center">
          <Brain className="w-5 h-5 text-white" />
        </div>
        <div>
          <h1 className="text-lg font-bold text-slate-900 leading-none">Everwod FAQ Intelligence</h1>
          <p className="text-[10px] text-slate-500 uppercase tracking-widest mt-1">Panel de revision humana</p>
        </div>
      </div>
      <div className="flex flex-wrap items-end justify-end gap-3">
        <div className="min-w-[240px]">
          <label className="text-[10px] font-bold text-slate-500 uppercase block mb-1">Empresa</label>
          <select
            value={selectedWorkspaceId ?? ''}
            onChange={(event) => onWorkspaceChange(Number(event.target.value))}
            className="w-full h-9 px-3 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
          >
            {workspaces.length === 0 && <option value="">Sin empresas</option>}
            {workspaces.map(workspace => (
              <option key={workspace.workspace_id} value={workspace.workspace_id}>
                {workspace.workspace_name}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-[180px]">
          <label className="text-[10px] font-bold text-slate-500 uppercase block mb-1">Analizar chats de los ultimos</label>
          <select
            value={sinceDays}
            onChange={(event) => onSinceDaysChange(Number(event.target.value))}
            className="w-full h-9 px-3 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
          >
            {SINCE_DAY_OPTIONS.map(days => (
              <option key={days} value={days}>{days} dias</option>
            ))}
          </select>
        </div>
        <button
          onClick={onGenerate}
          disabled={isGenerating || !selectedWorkspaceId}
          className="h-9 px-4 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg shadow-sm transition-colors flex items-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
        >
          {isGenerating ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          {isGenerating ? 'Generando...' : 'Generar nuevas sugerencias'}
        </button>
      </div>
    </header>
  );
}
