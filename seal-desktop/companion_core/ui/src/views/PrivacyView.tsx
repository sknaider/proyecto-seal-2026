import { useEffect, useState, useCallback } from 'react'
import { API } from '../App'
import { Shield, Lock, Cloud, RefreshCw } from 'lucide-react'

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

interface Capabilities {
  agent: string
  capabilities: Record<string, boolean>
}

const RISK_META: Record<string, { label: string; tone: string }> = {
  cap_shell_commands:  { label: '🔴 alto', tone: 'text-red-500' },
  cap_write_files:     { label: '🔴 alto', tone: 'text-red-500' },
  cap_git:             { label: '🟡 medio', tone: 'text-amber-500' },
  cap_browser_control: { label: '🟡 medio', tone: 'text-amber-500' },
  cap_screen_capture:  { label: '🟡 medio', tone: 'text-amber-500' },
  cap_camera:          { label: '🟡 medio', tone: 'text-amber-500' },
  cap_read_files:      { label: '🟢 bajo', tone: 'text-emerald-600' },
  cap_web_search:      { label: '🟢 bajo', tone: 'text-emerald-600' },
  cap_memory_read:     { label: '🟢 bajo', tone: 'text-emerald-600' },
  cap_memory_write:    { label: '🟢 bajo', tone: 'text-emerald-600' },
  cap_cron_jobs:       { label: '🟢 bajo', tone: 'text-emerald-600' },
  cap_notifications:   { label: '🟢 bajo', tone: 'text-emerald-600' },
  cap_channel_read:    { label: '🟢 bajo', tone: 'text-emerald-600' },
}

const CAP_META: Record<string, { label: string; desc: string }> = {
  cap_shell_commands:  { label: 'Ejecutar comandos',      desc: 'Permite acciones avanzadas en tu equipo.' },
  cap_write_files:     { label: 'Editar archivos',        desc: 'Permite crear o cambiar archivos.' },
  cap_git:             { label: 'Usar Git',               desc: 'Permite revisar cambios y trabajar con commits.' },
  cap_browser_control: { label: 'Controlar navegador',    desc: 'Permite abrir paginas y probar pantallas.' },
  cap_screen_capture:  { label: 'Ver pantalla',           desc: 'Permite entender lo que esta abierto.' },
  cap_camera:          { label: 'Usar camara',            desc: 'Permite usar la camara si tu lo autorizas.' },
  cap_read_files:      { label: 'Leer archivos',          desc: 'Permite leer archivos necesarios para una tarea.' },
  cap_web_search:      { label: 'Buscar en internet',     desc: 'Permite consultar informacion actual.' },
  cap_memory_read:     { label: 'Leer recuerdos',         desc: 'Permite usar lo que SEAL ya recuerda.' },
  cap_memory_write:    { label: 'Guardar recuerdos',      desc: 'Permite guardar datos importantes para despues.' },
  cap_cron_jobs:       { label: 'Tareas programadas',     desc: 'Permite ejecutar acciones en horarios definidos.' },
  cap_notifications:   { label: 'Enviar avisos',          desc: 'Permite mostrar recordatorios y alertas.' },
  cap_channel_read:    { label: 'Leer canales conectados', desc: 'Permite leer mensajes de canales autorizados.' },
}

export default function PrivacyView() {
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [auditStats, setAuditStats] = useState<{ local: number; egress: number }>({ local: 0, egress: 0 })
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [loading, setLoading] = useState(false)
  const [savingCap, setSavingCap] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [a, c] = await Promise.all([
        fetch(`${API}/api/audit-log?limit=200`).then(r => r.json()),
        fetch(`${API}/api/capabilities?agent=SOUL`).then(r => r.json()),
      ])
      const entries: AuditEntry[] = a.entries || []
      setAudit(entries)
      const local = entries.filter(e => e.processed_locally).length
      setAuditStats({ local, egress: entries.length - local })
      setCaps(c)
    } catch {
      setAudit([])
      setCaps(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const toggleCap = async (key: string) => {
    if (!caps) return
    setSavingCap(key)
    const next = { ...caps.capabilities, [key]: !caps.capabilities[key] }
    try {
      await fetch(`${API}/api/capabilities`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: 'SOUL', capabilities: next }),
      })
      setCaps({ ...caps, capabilities: next })
    } catch {
      // noop — keep optimistic
    } finally {
      setSavingCap(null)
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-3xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield className="w-5 h-5 text-blue-500" />
            <h1 className="text-xl font-semibold text-slate-800">Privacidad</h1>
          </div>
          <button
            onClick={load}
            className="p-1.5 rounded border border-seal-border hover:border-blue-400 text-seal-muted hover:text-blue-500"
            title="Refresh"
            disabled={loading}
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Capabilities */}
        <section className="rounded-xl bg-seal-surface border border-seal-border p-4">
          <div className="flex items-center gap-2 mb-3">
            <h2 className="text-sm font-semibold text-slate-800">Capacidades de tu SEAL</h2>
          </div>
          {!caps && <p className="text-xs text-seal-muted">Cargando…</p>}
          {caps && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {Object.entries(caps.capabilities).map(([key, enabled]) => {
                const risk = RISK_META[key] || { label: '⚪ —', tone: 'text-stone-500' }
                const meta = CAP_META[key] || { label: key.replace('cap_', '').replaceAll('_', ' '), desc: 'Permiso configurable.' }
                return (
                  <div key={key} className="flex items-center gap-2 px-2 py-1.5 rounded border border-seal-border hover:bg-stone-50">
                    <span className={`text-[10px] ${risk.tone} w-14 shrink-0`}>{risk.label}</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-slate-700">{meta.label}</p>
                      <p className="text-[10px] text-seal-muted leading-snug">{meta.desc}</p>
                    </div>
                    <button
                      onClick={() => toggleCap(key)}
                      disabled={savingCap === key}
                      className={`relative w-9 h-5 rounded-full transition-colors ${enabled ? 'bg-emerald-500' : 'bg-stone-300'} ${savingCap === key ? 'opacity-60' : ''}`}
                    >
                      <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${enabled ? 'translate-x-4' : ''}`} />
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </section>

        {/* Audit Log */}
        <section className="rounded-xl bg-seal-surface border border-seal-border p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-slate-800">Historial de privacidad</h2>
              <span className="text-[10px] text-seal-muted">últimas 200</span>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 border border-emerald-200">
                <Lock className="w-3 h-3" /> {auditStats.local} en este equipo
              </span>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-200">
                <Cloud className="w-3 h-3" /> {auditStats.egress} salieron del equipo
              </span>
            </div>
          </div>
          {audit.length === 0 && (
            <p className="text-xs text-seal-muted text-center py-6">
              Aún no hay acciones. Cuando SEAL lea, guarde o conecte algo, aparecerá aquí.
            </p>
          )}
          {audit.length > 0 && (
            <>
            <div className="md:hidden space-y-2">
              {audit.map(e => (
                <div key={e.id} className="rounded border border-seal-border bg-stone-50 p-2">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-[11px] text-seal-muted">{formatTime(e.created_at)}</span>
                    {e.processed_locally ? (
                      <span className="text-[11px] text-emerald-600 inline-flex items-center gap-1"><Lock className="w-3 h-3" /> en este equipo</span>
                    ) : (
                      <span className="text-[11px] text-amber-600 inline-flex items-center gap-1"><Cloud className="w-3 h-3" /> salió del equipo</span>
                    )}
                  </div>
                  <p className="text-xs text-slate-700">{formatAction(e.action)}</p>
                  <p className="text-[11px] text-seal-muted mt-0.5">{e.provider_used ? `Servicio: ${e.provider_used}` : 'Sin servicio externo'}</p>
                </div>
              ))}
            </div>
            <div className="hidden md:block overflow-hidden border border-seal-border rounded">
              <table className="w-full text-xs">
                <thead className="bg-stone-50 text-seal-muted uppercase tracking-widest">
                  <tr>
                    <th className="text-left px-2 py-1.5 font-medium">Cuándo</th>
                    <th className="text-left px-2 py-1.5 font-medium">Agente</th>
                    <th className="text-left px-2 py-1.5 font-medium">Acción</th>
                    <th className="text-left px-2 py-1.5 font-medium">Dónde pasó</th>
                    <th className="text-left px-2 py-1.5 font-medium">Servicio</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map(e => (
                    <tr key={e.id} className="border-t border-seal-border">
                      <td className="px-2 py-1.5 text-seal-muted font-mono text-[11px] whitespace-nowrap">
                        {formatTime(e.created_at)}
                      </td>
                      <td className="px-2 py-1.5 text-blue-600 font-mono">{e.agent}</td>
                      <td className="px-2 py-1.5 text-slate-700 text-[11px]">{formatAction(e.action)}</td>
                      <td className="px-2 py-1.5">
                        {e.processed_locally ? (
                          <span className="text-emerald-600 inline-flex items-center gap-1"><Lock className="w-3 h-3" /> en este equipo</span>
                        ) : (
                          <span className="text-amber-600 inline-flex items-center gap-1"><Cloud className="w-3 h-3" /> salió del equipo</span>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-seal-muted font-mono text-[11px]">{e.provider_used ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            </>
          )}
        </section>

        <div className="text-[11px] text-seal-muted text-center pb-4">
          Nuestra promesa: SEAL App no vende tu información, no entrena modelos con tus mensajes y procesa local por default.
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

function formatAction(action: string): string {
  if (action.startsWith('capability_toggle:')) return 'Cambió un permiso'
  if (action === 'connection_add') return 'Agregó una conexión'
  if (action === 'connection_remove') return 'Quitó una conexión'
  if (action === 'byok_save') return 'Guardó una clave externa'
  if (action === 'byok_delete') return 'Quitó una clave externa'
  return action.replaceAll('_', ' ')
}
