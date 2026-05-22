import { useEffect, useState, useCallback } from 'react'
import { Moon, Sun, Sparkles, RefreshCw, AlertCircle } from 'lucide-react'

const HOST = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
const SOUL = `http://${HOST}:8800`

interface Dream {
  id: number
  agent: string
  date: string
  cycle: 'morning' | 'evening'
  narrative: string
  key_events: string[]
  emotional_arc: Record<string, unknown>
  learnings: string[]
  pending_threads: string[]
  model: string | null
  source_memory_ids: number[]
  inject_to_prompt: boolean
  created_at: string
}

interface DreamsResponse {
  dreams: Dream[]
  total?: number
}

const AGENT_FILTER = ['all', 'ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM'] as const

export function DreamsPanel() {
  const [dreams, setDreams] = useState<Dream[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [agent, setAgent] = useState<typeof AGENT_FILTER[number]>('all')
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const q = agent === 'all' ? '' : `?agent=${agent}`
      const r = await fetch(`${SOUL}/api/soul/dreams${q}`)
      if (!r.ok) {
        // Endpoint puede no existir aún — fallback: usar /api/postgres-passthrough si está
        throw new Error(`HTTP ${r.status}`)
      }
      const data: DreamsResponse = await r.json()
      setDreams(data.dreams || [])
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(`No pude cargar dreams (${msg}). El endpoint /api/soul/dreams aún no está conectado.`)
      setDreams([])
    } finally {
      setLoading(false)
    }
  }, [agent])

  useEffect(() => { void load() }, [load])

  return (
    <div className="p-4 text-gray-200">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-violet-400" />
          <h2 className="text-lg font-semibold">Sueños</h2>
          <span className="text-xs text-gray-500">
            (2x/día — narrativa consolidada generada por dream_cycle.py)
          </span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={agent}
            onChange={e => setAgent(e.target.value as typeof AGENT_FILTER[number])}
            className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5"
          >
            {AGENT_FILTER.map(a => (
              <option key={a} value={a}>{a === 'all' ? 'Todos los agentes' : a}</option>
            ))}
          </select>
          <button
            onClick={load}
            className="p-1.5 rounded border border-gray-700 hover:border-violet-500 text-gray-400 hover:text-violet-300"
            title="Refresh"
            disabled={loading}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* States */}
      {error && (
        <div className="mb-3 p-3 rounded-lg bg-amber-900/30 border border-amber-700/50 text-amber-200 text-xs flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            {error}
            <div className="mt-1 text-amber-200/70">
              Mientras se conecta el endpoint, podés correr en terminal:
              <code className="ml-1 px-1 py-0.5 bg-amber-950/50 rounded font-mono text-[10px]">
                python3 memory/dream_cycle.py --inject-preview --agent ALICE
              </code>
            </div>
          </div>
        </div>
      )}

      {!error && !loading && dreams.length === 0 && (
        <div className="text-center py-8 text-gray-500 text-sm">
          Aún no hay sueños registrados. El cron 2x/día (7AM y 11PM Lima) los generará automáticamente.
        </div>
      )}

      {/* Dreams list */}
      <div className="space-y-3">
        {dreams.map(d => (
          <DreamCard
            key={d.id}
            dream={d}
            expanded={expandedId === d.id}
            onToggle={() => setExpandedId(expandedId === d.id ? null : d.id)}
          />
        ))}
      </div>
    </div>
  )
}

function DreamCard({ dream, expanded, onToggle }: { dream: Dream; expanded: boolean; onToggle: () => void }) {
  const cycleIcon = dream.cycle === 'morning' ? <Sun className="w-3.5 h-3.5 text-amber-400" /> : <Moon className="w-3.5 h-3.5 text-indigo-400" />
  const cycleLabel = dream.cycle === 'morning' ? 'Matinal' : 'Nocturno'
  const dateLabel = formatDate(dream.date)
  const preview = expanded ? dream.narrative : dream.narrative.slice(0, 220)

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 overflow-hidden">
      <div className="px-3 py-2 border-b border-gray-800 flex items-center gap-2 text-xs">
        {cycleIcon}
        <span className="font-medium text-gray-200">{cycleLabel}</span>
        <span className="text-gray-500">·</span>
        <span className="text-gray-400">{dateLabel}</span>
        <span className="text-gray-500">·</span>
        <span className="text-violet-300 font-mono">{dream.agent}</span>
        {dream.model && (
          <span className="ml-auto text-[10px] text-gray-500 font-mono">{dream.model}</span>
        )}
      </div>
      <div className="p-3 text-sm text-gray-300 leading-relaxed">
        <p>{preview}{!expanded && dream.narrative.length > 220 ? '…' : ''}</p>
        {dream.narrative.length > 220 && (
          <button
            onClick={onToggle}
            className="mt-1 text-xs text-violet-400 hover:text-violet-300"
          >
            {expanded ? 'Ver menos' : 'Ver completo'}
          </button>
        )}
      </div>
      {expanded && (
        <div className="border-t border-gray-800 px-3 py-2 grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
          {dream.key_events.length > 0 && (
            <Section title="Eventos clave" items={dream.key_events} tone="text-emerald-300" />
          )}
          {dream.learnings.length > 0 && (
            <Section title="Aprendizajes" items={dream.learnings} tone="text-sky-300" />
          )}
          {dream.pending_threads.length > 0 && (
            <Section title="Pendientes" items={dream.pending_threads} tone="text-amber-300" />
          )}
          {dream.emotional_arc && Object.keys(dream.emotional_arc).length > 0 && (
            <div>
              <div className="text-gray-400 uppercase tracking-widest text-[10px] mb-1">Arco emocional</div>
              <pre className="text-[11px] text-gray-300 bg-gray-950/40 rounded p-2 overflow-x-auto">
                {JSON.stringify(dream.emotional_arc, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Section({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  return (
    <div>
      <div className="text-gray-400 uppercase tracking-widest text-[10px] mb-1">{title}</div>
      <ul className="space-y-0.5">
        {items.map((it, i) => (
          <li key={i} className={`text-[12px] ${tone} leading-snug`}>· {it}</li>
        ))}
      </ul>
    </div>
  )
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('es-PE', { day: '2-digit', month: 'short', year: 'numeric' })
  } catch {
    return iso
  }
}

export default DreamsPanel
