import { useEffect, useState, useCallback } from 'react'
import { API } from '../App'
import { Moon, Sun, RefreshCw, Sparkles } from 'lucide-react'

interface Dream {
  id: number
  agent: string
  date: string
  cycle: 'morning' | 'evening' | string
  narrative: string
  key_events?: string[]
  emotional_arc?: Record<string, unknown>
  learnings?: string[]
  pending_threads?: string[]
  model?: string | null
  created_at?: string | null
}

interface Response { dreams: Dream[]; count: number }

export default function DreamsView() {
  const [dreams, setDreams] = useState<Dream[]>([])
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/dreams`)
      const d: Response = await r.json()
      setDreams(d.dreams || [])
    } catch {
      setDreams([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-blue-500" />
            <h1 className="text-xl font-semibold text-slate-800">Ideas</h1>
            <span className="text-xs text-seal-muted">resúmenes útiles de SEAL</span>
          </div>
          <button
            onClick={load}
            className="p-1.5 rounded border border-seal-border hover:border-blue-400 text-seal-muted hover:text-blue-500"
            title="Actualizar"
            disabled={loading}
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {!loading && dreams.length === 0 && (
          <div className="text-center py-12 text-seal-muted text-sm">
            Aún no hay ideas guardadas. SEAL las creará cuando tenga suficiente actividad para resumir.
          </div>
        )}

        <div className="space-y-3">
          {dreams.map(d => {
            const Icon = d.cycle === 'morning' ? Sun : Moon
            const tone = d.cycle === 'morning' ? 'text-amber-500' : 'text-indigo-500'
            const isOpen = expanded === d.id
            const preview = isOpen ? d.narrative : d.narrative.slice(0, 280)
            return (
              <div key={d.id} className="rounded-xl border border-seal-border bg-seal-surface shadow-sm">
                <div className="px-4 py-2 border-b border-seal-border flex items-center gap-2 text-xs">
                  <Icon className={`w-4 h-4 ${tone}`} />
                  <span className="font-medium text-slate-700 capitalize">{d.cycle}</span>
                  <span className="text-seal-muted">·</span>
                  <span className="text-seal-muted">{d.date}</span>
                  {d.model && (
                    <span className="ml-auto text-[10px] text-seal-muted font-mono">{d.model}</span>
                  )}
                </div>
                <div className="p-4 text-sm leading-relaxed text-slate-700">
                  <p>{preview}{!isOpen && d.narrative.length > 280 ? '…' : ''}</p>
                  {d.narrative.length > 280 && (
                    <button
                      onClick={() => setExpanded(isOpen ? null : d.id)}
                      className="mt-1 text-xs text-blue-500 hover:text-blue-600"
                    >
                      {isOpen ? 'Ver menos' : 'Ver completo'}
                    </button>
                  )}
                </div>
                {isOpen && (
                  <div className="border-t border-seal-border px-4 py-3 grid grid-cols-1 md:grid-cols-2 gap-3 text-xs bg-stone-50">
                    {!!d.key_events?.length && (
                      <DreamSection title="Eventos clave" items={d.key_events} tone="text-emerald-600" />
                    )}
                    {!!d.learnings?.length && (
                      <DreamSection title="Aprendizajes" items={d.learnings} tone="text-sky-600" />
                    )}
                    {!!d.pending_threads?.length && (
                      <DreamSection title="Pendientes" items={d.pending_threads} tone="text-amber-600" />
                    )}
                    {d.emotional_arc && Object.keys(d.emotional_arc).length > 0 && (
                      <div>
                        <div className="text-seal-muted uppercase tracking-widest text-[10px] mb-1">Arco emocional</div>
                        <pre className="text-[11px] text-slate-700 bg-white rounded p-2 overflow-x-auto border border-seal-border">
                          {JSON.stringify(d.emotional_arc, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function DreamSection({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  return (
    <div>
      <div className="text-seal-muted uppercase tracking-widest text-[10px] mb-1">{title}</div>
      <ul className="space-y-0.5">
        {items.map((it, i) => (
          <li key={i} className={`text-[12px] ${tone} leading-snug`}>· {it}</li>
        ))}
      </ul>
    </div>
  )
}
