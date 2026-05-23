import { useState, useEffect } from 'react'
import { API } from '../App'

interface AuditEntry {
  id: number
  ts: string
  agent: string
  action: string
  channel: string
  detail: string | null
}

const ACTION_COLOR: Record<string, string> = {
  byok_save:    'text-amber-400',
  byok_delete:  'text-red-400',
  llm_call:     'text-blue-400',
  config_patch: 'text-purple-400',
}

function fmtTime(ts: string) {
  try {
    return new Date(ts).toLocaleString('es-PE', { hour12: false, month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch { return ts }
}

export default function AuditLogView() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    const q = search ? `?action=${encodeURIComponent(search)}` : ''
    fetch(`${API}/api/audit-log${q}&limit=100`)
      .then(r => r.json())
      .then(d => setEntries(d.entries ?? []))
      .catch(() => setEntries([]))
      .finally(() => setLoading(false))
  }, [search])

  return (
    <div className="h-full flex flex-col p-4 gap-3 overflow-hidden">
      <div className="flex items-center gap-3 shrink-0">
        <h2 className="text-sm font-semibold text-slate-200">Registro de actividad</h2>
        <span className="text-xs text-seal-muted">{entries.length} entradas</span>
      </div>

      <input
        type="text"
        placeholder="Buscar acción…"
        value={search}
        onChange={e => setSearch(e.target.value)}
        className="shrink-0 bg-seal-surface border border-seal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder:text-seal-muted focus:outline-none focus:border-blue-500"
      />

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-seal-muted text-sm">Cargando…</div>
      ) : entries.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center text-seal-muted gap-2">
          <span className="text-2xl">📋</span>
          <p className="text-sm">Sin actividad registrada aún</p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto space-y-1">
          {entries.map(e => (
            <div key={e.id} className="bg-seal-surface border border-seal-border rounded-lg px-3 py-2 flex items-start gap-3">
              <span className={`text-xs font-mono pt-0.5 shrink-0 ${ACTION_COLOR[e.action] ?? 'text-slate-400'}`}>
                {e.action}
              </span>
              <div className="flex-1 min-w-0">
                {e.detail && <p className="text-xs text-slate-300 truncate">{e.detail}</p>}
                <p className="text-xs text-seal-muted">{e.channel} · {fmtTime(e.ts)}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
