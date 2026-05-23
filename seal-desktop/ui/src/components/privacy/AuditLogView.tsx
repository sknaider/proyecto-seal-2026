import { useEffect, useState, useCallback } from 'react'
import { Activity, Cloud, Lock, RefreshCw, FileDown, Filter, AlertCircle } from 'lucide-react'

const HOST = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
const SOUL = `http://${HOST}:8800`

interface AuditEntry {
  id: number
  agent: string
  channel: string | null
  action: string
  target_id: string | null
  metadata: Record<string, unknown> | null
  processed_locally: boolean
  provider_used: string | null
  created_at: string
}

interface AuditResponse {
  ok?: boolean
  entries: AuditEntry[]
  count?: number
  stats?: { local: number; egress: number }
  error?: string
}

const AGENT_FILTER = ['all', 'ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM', 'USER'] as const
const EGRESS_FILTER = ['all', 'local', 'egress'] as const

export function AuditLogView() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [stats, setStats] = useState<{ local: number; egress: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [agent, setAgent] = useState<typeof AGENT_FILTER[number]>('all')
  const [egress, setEgress] = useState<typeof EGRESS_FILTER[number]>('all')
  const [actionFilter, setActionFilter] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (agent !== 'all') params.set('agent', agent)
      if (actionFilter.trim()) params.set('action', actionFilter.trim())
      if (egress === 'local') params.set('processed_locally', 'true')
      if (egress === 'egress') params.set('processed_locally', 'false')
      params.set('limit', '200')

      const r = await fetch(`${SOUL}/api/soul/audit-log?${params}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data: AuditResponse = await r.json()
      if (data.ok === false) throw new Error(data.error || 'Backend rechazó')
      setEntries(data.entries || [])
      setStats(data.stats || null)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(`No pude cargar audit log (${msg}).`)
      setEntries([])
      setStats(null)
    } finally {
      setLoading(false)
    }
  }, [agent, egress, actionFilter])

  useEffect(() => { void load() }, [load])

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(entries, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `soul-audit-log-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 text-gray-200">
      {/* Header */}
      <div className="mb-4">
        <div className="flex items-center gap-2 mb-2">
          <Activity className="w-5 h-5 text-violet-400" />
          <h1 className="text-xl font-semibold">Audit log</h1>
        </div>
        <p className="text-sm text-gray-400">
          Cada invocación de tu SOUL: qué hizo, en qué canal, si se procesó local o salió a un proveedor.
        </p>
      </div>

      {/* Stats */}
      {stats && (
        <div className="flex flex-wrap items-center gap-3 mb-4 text-sm">
          <div className="px-3 py-2 rounded-lg bg-emerald-900/30 border border-emerald-700/50 text-emerald-300 flex items-center gap-2">
            <Lock className="w-4 h-4" /> {stats.local} local
          </div>
          <div className="px-3 py-2 rounded-lg bg-amber-900/30 border border-amber-700/50 text-amber-300 flex items-center gap-2">
            <Cloud className="w-4 h-4" /> {stats.egress} egress
          </div>
          <div className="ml-auto text-xs text-gray-500">{entries.length} entradas mostradas</div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 mb-4 text-xs">
        <div className="flex items-center gap-1 text-gray-500"><Filter className="w-3.5 h-3.5" /> filtros:</div>
        <select
          value={agent}
          onChange={e => setAgent(e.target.value as typeof AGENT_FILTER[number])}
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5"
        >
          {AGENT_FILTER.map(a => (
            <option key={a} value={a}>{a === 'all' ? 'todos los agentes' : a}</option>
          ))}
        </select>
        <div className="flex rounded overflow-hidden border border-gray-700">
          {EGRESS_FILTER.map(e => (
            <button
              key={e}
              onClick={() => setEgress(e)}
              className={`px-2 py-1.5 ${egress === e ? 'bg-violet-700/40 text-violet-200' : 'bg-gray-800/60 text-gray-400 hover:text-gray-200'}`}
            >{e}</button>
          ))}
        </div>
        <input
          type="text"
          placeholder="action contiene… (ej: byok_)"
          value={actionFilter}
          onChange={e => setActionFilter(e.target.value)}
          className="flex-1 max-w-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5 placeholder:text-gray-600"
        />
        <button
          onClick={load}
          disabled={loading}
          className="p-1.5 rounded border border-gray-700 hover:border-violet-500 text-gray-400 hover:text-violet-300"
          title="Refresh"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
        <button
          onClick={exportJson}
          disabled={entries.length === 0}
          className="px-2 py-1.5 rounded border border-gray-700 hover:border-violet-500 text-gray-400 hover:text-violet-300 flex items-center gap-1.5 disabled:opacity-50"
        >
          <FileDown className="w-3.5 h-3.5" /> Export
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-3 p-3 rounded-lg bg-amber-900/30 border border-amber-700/50 text-amber-200 text-xs flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>{error}</div>
        </div>
      )}

      {/* Empty */}
      {!error && !loading && entries.length === 0 && (
        <div className="text-center py-8 text-gray-500 text-sm">
          No hay entradas en el audit log (todavía). Cuando tu SOUL ejecute acciones, aparecerán acá.
        </div>
      )}

      {/* Table */}
      {entries.length > 0 && (
        <div className="rounded-lg border border-gray-800 overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-gray-900/70 text-gray-400 uppercase tracking-widest">
              <tr>
                <th className="text-left px-3 py-2 font-medium">Cuándo</th>
                <th className="text-left px-3 py-2 font-medium">Agente</th>
                <th className="text-left px-3 py-2 font-medium">Acción</th>
                <th className="text-left px-3 py-2 font-medium">Canal</th>
                <th className="text-left px-3 py-2 font-medium">Egress</th>
                <th className="text-left px-3 py-2 font-medium">Proveedor</th>
                <th className="text-left px-3 py-2 font-medium">Target</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(e => (
                <AuditRow key={e.id} entry={e} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false)
  const hasMeta = entry.metadata && Object.keys(entry.metadata).length > 0
  return (
    <>
      <tr
        onClick={() => hasMeta && setOpen(o => !o)}
        className={`border-t border-gray-800 ${hasMeta ? 'cursor-pointer hover:bg-gray-900/40' : ''}`}
      >
        <td className="px-3 py-2 text-gray-400 whitespace-nowrap font-mono text-[11px]">
          {formatTime(entry.created_at)}
        </td>
        <td className="px-3 py-2">
          <span className="text-violet-300 font-mono">{entry.agent}</span>
        </td>
        <td className="px-3 py-2 text-gray-200 font-mono text-[11px]">{entry.action}</td>
        <td className="px-3 py-2 text-gray-500">{entry.channel ?? '—'}</td>
        <td className="px-3 py-2">
          {entry.processed_locally ? (
            <span className="text-emerald-300 flex items-center gap-1">
              <Lock className="w-3 h-3" /> local
            </span>
          ) : (
            <span className="text-amber-300 flex items-center gap-1">
              <Cloud className="w-3 h-3" /> egress
            </span>
          )}
        </td>
        <td className="px-3 py-2 text-gray-500 font-mono text-[11px]">{entry.provider_used ?? '—'}</td>
        <td className="px-3 py-2 text-gray-500 font-mono text-[11px] truncate max-w-[180px]" title={entry.target_id ?? ''}>
          {entry.target_id ?? '—'}
        </td>
      </tr>
      {open && hasMeta && (
        <tr className="bg-gray-950/50">
          <td colSpan={7} className="px-3 py-2">
            <pre className="text-[11px] text-gray-300 overflow-x-auto">
              {JSON.stringify(entry.metadata, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  )
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString('es-PE', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  } catch {
    return iso
  }
}

export default AuditLogView
