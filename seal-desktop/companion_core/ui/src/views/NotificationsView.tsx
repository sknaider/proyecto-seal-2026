import { useEffect, useState, useCallback } from 'react'
import { API } from '../App'
import { Bell, BellOff, AlertCircle, CheckCircle, RefreshCw } from 'lucide-react'

interface Notification {
  id: number
  type: string
  title: string
  body?: string | null
  severity?: 'critical' | 'high' | 'normal' | 'low' | string
  read: boolean
  dismissed?: boolean
  source_id?: string | null
  source_table?: string | null
  metadata?: Record<string, unknown> | null
  created_at: string
}

interface NotifsResponse {
  ok?: boolean
  notifications: Notification[]
  count?: number
}

const SEV_TONE: Record<string, string> = {
  critical: 'bg-red-50 border-red-200 text-red-700',
  high:     'bg-amber-50 border-amber-200 text-amber-700',
  normal:   'bg-stone-50 border-stone-200 text-stone-700',
  low:      'bg-stone-50/60 border-stone-200/60 text-stone-500',
}

const FILTERS = ['all', 'unread', 'urgent'] as const

export default function NotificationsView() {
  const [notifs, setNotifs] = useState<Notification[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<typeof FILTERS[number]>('all')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/notifications`)
      const d: NotifsResponse = await r.json()
      setNotifs(d.notifications || [])
    } catch {
      setNotifs([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const markRead = async (id: number) => {
    try {
      await fetch(`${API}/api/notifications/${id}/read`, { method: 'POST' })
      setNotifs(prev => prev.map(n => n.id === id ? { ...n, read: true } : n))
    } catch {/* noop */}
  }

  const visible = notifs.filter(n => {
    if (filter === 'unread') return !n.read
    if (filter === 'urgent') return n.severity === 'critical' || n.severity === 'high'
    return true
  })

  const unreadCount = notifs.filter(n => !n.read).length

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Bell className="w-5 h-5 text-blue-500" />
            <h1 className="text-xl font-semibold text-slate-800">Notificaciones</h1>
            {unreadCount > 0 && (
              <span className="px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-700 text-[10px] font-medium">
                {unreadCount} sin leer
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded border border-seal-border overflow-hidden text-xs">
              {FILTERS.map(f => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={`px-2 py-1 ${filter === f ? 'bg-blue-500 text-white' : 'bg-seal-surface text-seal-muted hover:text-slate-700'}`}
                >
                  {f}
                </button>
              ))}
            </div>
            <button
              onClick={load}
              disabled={loading}
              className="p-1.5 rounded border border-seal-border hover:border-blue-400 text-seal-muted hover:text-blue-500"
              title="Refresh"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {!loading && visible.length === 0 && (
          <div className="text-center py-12 text-seal-muted text-sm">
            <BellOff className="w-8 h-8 mx-auto mb-2 opacity-50" />
            No hay notificaciones {filter !== 'all' ? `(${filter})` : ''}.
          </div>
        )}

        <div className="space-y-2">
          {visible.map(n => {
            const tone = SEV_TONE[n.severity ?? 'normal'] ?? SEV_TONE.normal
            const Icon = (n.severity === 'critical' || n.severity === 'high') ? AlertCircle : CheckCircle
            return (
              <div
                key={n.id}
                className={`rounded-lg border p-3 ${tone} ${n.read ? 'opacity-60' : ''}`}
              >
                <div className="flex items-start gap-2">
                  <Icon className="w-4 h-4 mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="text-sm font-medium">{n.title}</h3>
                      <span className="text-[10px] uppercase tracking-widest opacity-60">{n.type}</span>
                      {n.severity && (
                        <span className="text-[10px] uppercase tracking-widest opacity-70">{n.severity}</span>
                      )}
                    </div>
                    {n.body && <p className="text-xs mt-1 opacity-80">{n.body}</p>}
                    <p className="text-[10px] mt-1 opacity-50 font-mono">{formatTime(n.created_at)}</p>
                  </div>
                  {!n.read && (
                    <button
                      onClick={() => markRead(n.id)}
                      className="text-[10px] px-2 py-1 rounded bg-white/60 border border-current/20 hover:bg-white"
                    >
                      Marcar leída
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString('es-PE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}
