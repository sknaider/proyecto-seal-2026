import { useEffect, useState, useCallback } from 'react'
import { API } from '../App'
import { TreePine, Calendar, Clock, RefreshCw, Folder, FolderOpen, ChevronRight, Search, GitBranch, DatabaseZap } from 'lucide-react'

interface Bucket {
  id: number | string
  bucket_key?: string
  bucket_start?: string
  bucket_end?: string
  summary?: string | null
  importance?: number
  memory_count?: number
  child_count?: number
  child_ids?: Array<number | string>
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

interface MemoryItem {
  id: number
  category?: string | null
  content: string
  importance?: number
  created_at?: string
}

interface DetailResponse {
  ok: boolean
  bucket: Bucket
  parent?: Bucket | null
  children: Bucket[]
  memories: MemoryItem[]
  child_count: number
}

const LEVELS = ['hour', 'day', 'month', 'year'] as const
const TREE_AGENT = 'USER'

export default function MemoryTreeView() {
  const [level, setLevel] = useState<typeof LEVELS[number]>('day')
  const [data, setData] = useState<Bucket[]>([])
  const [detail, setDetail] = useState<DetailResponse | null>(null)
  const [query, setQuery] = useState('')
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(false)
  const [rebuilding, setRebuilding] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setMessage('')
    try {
      const r = await fetch(`${API}/api/memory-tree?agent=${TREE_AGENT}&level=${level}&limit=60`)
      const d: TreeResponse = await r.json()
      setData(d.buckets || [])
      setDetail(null)
    } catch {
      setData([])
      setMessage('No se pudo cargar Memory Tree')
    } finally {
      setLoading(false)
    }
  }, [level])

  useEffect(() => { void load() }, [load])

  const search = async () => {
    const q = query.trim()
    if (!q) {
      await load()
      return
    }
    setLoading(true)
    setMessage('')
    try {
      const r = await fetch(`${API}/api/memory-tree/search?agent=${TREE_AGENT}&level=${level}&q=${encodeURIComponent(q)}&limit=60`)
      const d: TreeResponse = await r.json()
      setData(d.buckets || [])
      setDetail(null)
      setMessage(`${d.count} resultados`)
    } catch {
      setData([])
      setMessage('No se pudo buscar')
    } finally {
      setLoading(false)
    }
  }

  const openBucket = async (bucket: Bucket) => {
    setMessage('')
    try {
      const r = await fetch(`${API}/api/memory-tree/buckets/${bucket.id}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setDetail(await r.json())
    } catch {
      setMessage('No se pudo abrir el detalle')
      setDetail(null)
    }
  }

  const rebuild = async () => {
    setRebuilding(true)
    setMessage('')
    try {
      const r = await fetch(`${API}/api/memory-tree/rebuild`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: TREE_AGENT, level: 'all', dry_run: false }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setMessage('Memory Tree actualizado')
      await load()
    } catch {
      setMessage('No se pudo reconstruir')
    } finally {
      setRebuilding(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <TreePine className="w-5 h-5 text-emerald-600" />
            <h1 className="text-xl font-semibold text-slate-800">Resumen de recuerdos</h1>
            <span className="text-xs text-seal-muted">por hora, día, mes y año</span>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded border border-seal-border hover:border-emerald-400 text-seal-muted hover:text-emerald-600"
            title="Refrescar"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Level switch */}
        <div className="flex items-center gap-2 mb-4 text-xs">
          <span className="text-seal-muted">Ver por:</span>
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
          <span className="ml-auto text-[11px] text-seal-muted">{data.length} grupos</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-2 mb-4">
          <div className="flex min-w-0 rounded-lg border border-seal-border bg-seal-surface">
            <Search className="w-4 h-4 text-seal-muted ml-3 mt-2.5 shrink-0" />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') void search() }}
              className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm outline-none"
              placeholder="Buscar en resúmenes"
            />
            <button
              onClick={search}
              className="px-3 text-sm text-emerald-700 hover:text-emerald-800"
            >
              Buscar
            </button>
          </div>
          <button
            onClick={rebuild}
            disabled={rebuilding}
            className="flex min-h-10 items-center justify-center gap-2 rounded-lg border border-seal-border bg-seal-surface px-3 text-sm text-slate-700 hover:border-emerald-400 disabled:text-stone-400"
          >
            <DatabaseZap className={`w-4 h-4 ${rebuilding ? 'animate-pulse' : ''}`} />
            {rebuilding ? 'Reconstruyendo...' : 'Reconstruir'}
          </button>
        </div>

        {message && (
          <p className="mb-3 text-xs text-seal-muted">{message}</p>
        )}

        {!loading && data.length === 0 && (
          <div className="rounded-lg border border-dashed border-seal-border bg-seal-surface p-6 text-center text-sm text-seal-muted">
            Aún no hay resumen para este periodo. Cuando SEAL tenga más conversaciones, aparecerán aquí.
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px] gap-4">
          <div className="space-y-2">
            {data.map(b => {
              const isOpen = detail?.bucket?.id === b.id
              const Icon = isOpen ? FolderOpen : Folder
              return (
                <button
                  key={b.id}
                  onClick={() => void openBucket(b)}
                  className={`w-full text-left rounded-lg border bg-seal-surface hover:bg-stone-50 transition ${
                    isOpen ? 'border-emerald-500 ring-2 ring-emerald-100' : 'border-seal-border'
                  }`}
                >
                  <div className="flex items-center gap-2 p-3">
                    <ChevronRight className={`w-3.5 h-3.5 text-seal-muted transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                    <Icon className="w-4 h-4 text-emerald-600 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-slate-800">{formatBucketKey(b)}</span>
                        {b.child_count != null && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-stone-100 text-slate-500">{b.child_count} hijos</span>
                        )}
                        {b.memory_count != null && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-stone-100 text-slate-500">{b.memory_count} recuerdos</span>
                        )}
                      </div>
                      {b.summary && (
                        <p className="text-xs text-seal-muted mt-1 line-clamp-2">{b.summary}</p>
                      )}
                    </div>
                    <div className="text-right text-[10px] text-seal-muted font-mono shrink-0 hidden md:block">
                      {level === 'hour' ? <Clock className="w-3 h-3 inline mr-1" /> : <Calendar className="w-3 h-3 inline mr-1" />}
                      {b.created_at ? formatTime(b.created_at) : '—'}
                    </div>
                  </div>
                </button>
              )
            })}
          </div>

          <aside className="rounded-lg border border-seal-border bg-seal-surface p-3 lg:sticky lg:top-4 self-start">
            {!detail ? (
              <div className="py-8 text-center text-sm text-seal-muted">
                <GitBranch className="w-6 h-6 mx-auto mb-2 text-stone-400" />
                Selecciona un grupo para ver hijos y recuerdos.
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <p className="text-xs text-seal-muted">Detalle</p>
                  <h2 className="text-sm font-semibold text-slate-800">{formatBucketKey(detail.bucket)}</h2>
                </div>
                {detail.bucket.summary && (
                  <p className="text-xs leading-relaxed text-slate-700">{detail.bucket.summary}</p>
                )}
                {detail.parent && (
                  <button
                    onClick={() => void openBucket(detail.parent!)}
                    className="w-full rounded border border-seal-border px-2 py-1.5 text-left text-xs text-slate-700 hover:border-emerald-400"
                  >
                    Padre: {formatBucketKey(detail.parent)}
                  </button>
                )}
                {detail.children.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-slate-700">Hijos</p>
                    <div className="space-y-1">
                      {detail.children.map(child => (
                        <button
                          key={child.id}
                          onClick={() => void openBucket(child)}
                          className="w-full rounded border border-seal-border px-2 py-1.5 text-left text-xs text-seal-muted hover:border-emerald-400 hover:text-slate-700"
                        >
                          {formatBucketKey(child)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {detail.memories.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-slate-700">Recuerdos</p>
                    <div className="space-y-2">
                      {detail.memories.map(memory => (
                        <div key={memory.id} className="rounded border border-seal-border bg-stone-50 p-2">
                          <p className="text-xs leading-relaxed text-slate-700">{memory.content}</p>
                          <p className="mt-1 text-[10px] text-seal-muted">imp {memory.importance ?? '—'} · {memory.category || 'sin categoría'}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </aside>
        </div>

        <div className="mt-6 text-[11px] text-seal-muted text-center pb-4">
          SEAL agrupa lo importante para que puedas volver a encontrarlo sin leer todo de nuevo.
        </div>
      </div>
    </div>
  )
}

function formatBucketKey(bucket: Bucket): string {
  const value = bucket.bucket_key || bucket.bucket_start || String(bucket.id)
  try {
    return new Date(value).toLocaleString('es-PE', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return value
  }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('es-PE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}
