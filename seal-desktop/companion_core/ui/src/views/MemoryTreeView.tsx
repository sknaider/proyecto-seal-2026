import { useEffect, useState, useCallback } from 'react'
import { API } from '../App'
import { TreePine, Calendar, Clock, RefreshCw, Folder, FolderOpen, ChevronRight } from 'lucide-react'

interface Bucket {
  id: number | string
  bucket_key: string
  summary?: string | null
  importance?: number
  memory_count?: number
  created_at?: string
  parent_id?: number | string | null
}

interface TreeResponse {
  ok?: boolean
  agent: string
  level: 'hour' | 'day' | 'month' | 'year' | string
  buckets: Bucket[]
  count: number
}

const LEVELS = ['hour', 'day', 'month', 'year'] as const

export default function MemoryTreeView() {
  const [level, setLevel] = useState<typeof LEVELS[number]>('day')
  const [data, setData] = useState<Bucket[]>([])
  const [loading, setLoading] = useState(false)
  const [openId, setOpenId] = useState<string | number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/memory-tree?agent=SOUL&level=${level}&limit=60`)
      const d: TreeResponse = await r.json()
      setData(d.buckets || [])
    } catch {
      setData([])
    } finally {
      setLoading(false)
    }
  }, [level])

  useEffect(() => { void load() }, [load])

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <TreePine className="w-5 h-5 text-emerald-600" />
            <h1 className="text-xl font-semibold text-slate-800">Árbol de memoria</h1>
            <span className="text-xs text-seal-muted">consolidación h→d→m→y</span>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded border border-seal-border hover:border-emerald-400 text-seal-muted hover:text-emerald-600"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Level switch */}
        <div className="flex items-center gap-2 mb-4 text-xs">
          <span className="text-seal-muted">Nivel:</span>
          <div className="flex rounded overflow-hidden border border-seal-border">
            {LEVELS.map(l => (
              <button
                key={l}
                onClick={() => setLevel(l)}
                className={`px-3 py-1.5 capitalize ${level === l ? 'bg-emerald-500 text-white' : 'bg-seal-surface text-seal-muted hover:text-slate-700'}`}
              >
                {l === 'hour' ? 'hora' : l === 'day' ? 'día' : l === 'month' ? 'mes' : 'año'}
              </button>
            ))}
          </div>
          <span className="ml-auto text-[11px] text-seal-muted">{data.length} buckets</span>
        </div>

        {!loading && data.length === 0 && (
          <div className="rounded-xl border border-dashed border-seal-border bg-seal-surface p-6 text-center text-sm text-seal-muted">
            Aún no hay árbol de memoria al nivel {level}. Cuando tu SEAL acumule conversaciones, el cron de consolidación lo va a generar automáticamente.
          </div>
        )}

        <div className="space-y-2">
          {data.map(b => {
            const isOpen = openId === b.id
            const Icon = isOpen ? FolderOpen : Folder
            return (
              <button
                key={b.id}
                onClick={() => setOpenId(isOpen ? null : b.id)}
                className="w-full text-left rounded-lg border border-seal-border bg-seal-surface hover:bg-stone-50 transition"
              >
                <div className="flex items-center gap-2 p-3">
                  <ChevronRight className={`w-3.5 h-3.5 text-seal-muted transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                  <Icon className="w-4 h-4 text-emerald-600 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-slate-800">{b.bucket_key}</span>
                      {b.memory_count != null && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-stone-100 text-slate-500">{b.memory_count} memorias</span>
                      )}
                      {b.importance != null && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700">imp {b.importance}</span>
                      )}
                    </div>
                    {b.summary && !isOpen && (
                      <p className="text-xs text-seal-muted mt-1 line-clamp-2">{b.summary}</p>
                    )}
                  </div>
                  <div className="text-right text-[10px] text-seal-muted font-mono shrink-0 hidden md:block">
                    {level === 'hour' ? <Clock className="w-3 h-3 inline mr-1" /> : <Calendar className="w-3 h-3 inline mr-1" />}
                    {b.created_at ? formatTime(b.created_at) : '—'}
                  </div>
                </div>
                {isOpen && b.summary && (
                  <div className="px-3 pb-3 text-xs text-slate-600 leading-relaxed border-t border-seal-border pt-2 bg-stone-50/50">
                    {b.summary}
                  </div>
                )}
              </button>
            )
          })}
        </div>

        <div className="mt-6 text-[11px] text-seal-muted text-center pb-4">
          El árbol consolida tus interacciones: cada hora se resume al día, cada día al mes, cada mes al año.
        </div>
      </div>
    </div>
  )
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('es-PE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}
