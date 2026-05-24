import { useCallback, useEffect, useMemo, useState } from 'react'
import { API } from '../App'
import { Box, RefreshCw, Plug, Cpu, Wrench, Tag, ShieldAlert, ShieldCheck, ShieldQuestion, Eye, CheckCircle2, XCircle, Play, Square, Activity, Download, Package } from 'lucide-react'

interface Plugin {
  ok: boolean
  name: string
  manifest_id?: string
  channels?: string[]
  contracts?: string[]
  providers_auth_envs?: string[]
  categories: string[]
  risk_tier: 'critical' | 'high' | 'normal' | string
  enabled_by_default?: boolean
  activation_on_startup?: boolean
  config_schema_keys?: string[]
  pkg_name?: string
  pkg_version?: string
  pkg_description?: string
  path?: string
  error?: string
}

interface CatalogResponse {
  ok: boolean
  root?: string
  counts?: Record<string, number>
  plugins: Plugin[]
  error?: string
}

const RISK_BADGE: Record<string, { color: string; icon: typeof ShieldAlert; label: string }> = {
  critical: { color: 'bg-red-100 text-red-700 border-red-200',     icon: ShieldAlert,    label: 'crítico' },
  high:     { color: 'bg-amber-100 text-amber-700 border-amber-200', icon: ShieldQuestion, label: 'alto' },
  normal:   { color: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: ShieldCheck, label: 'normal' },
}

const CAT_ICON: Record<string, typeof Plug> = {
  channel: Plug,
  provider: Cpu,
  tool: Wrench,
  misc: Tag,
}

const CAT_LABEL: Record<string, string> = {
  channel: 'Canal de mensajería',
  provider: 'Proveedor de IA',
  tool: 'Herramienta',
  misc: 'Otro',
}

const ALL_CATS = ['channel', 'provider', 'tool', 'misc'] as const
const ALL_RISKS = ['all', 'critical', 'high', 'normal'] as const

interface SmokeCheck { name: string; ok: boolean; path?: string; manifest_id?: string; categories?: string[]; risk_tier?: string; channels?: string[]; error?: string }
interface SmokeResponse { ok: boolean; baseline: string[]; passed: number; total: number; checks: SmokeCheck[] }

interface SidecarState {
  status: 'stopped' | 'starting' | 'running' | 'error' | string
  pid?: number | null
  session_id?: string | null
  server?: string | null
  server_version?: string | null
  last_handshake_at?: string | null
  last_error?: string | null
  crash_count?: number
  restart_count?: number
  watchdog_enabled?: boolean
  binary_resolved?: string | null
}
interface AuditEntry {
  id?: number
  ts?: string
  timestamp?: string
  actor?: string
  action: string
  channel?: string
  payload?: string | Record<string, unknown>
  level?: string
}
interface Capability {
  name: string
  enabled: boolean
  granted_at?: string | null
  granted_by?: string | null
  last_used_at?: string | null
  notes?: string | null
}
interface OpenClawTool {
  name: string
  description: string
  capability_required: string
  input_keys: string[]
  max_payload_bytes: number
  sidecar_method: string
}
interface FsAllowEntry {
  path: string
  mode: string
  granted_at?: string | null
  granted_by?: string | null
}
interface SealPlugin {
  id: string
  name: string
  version?: string
  source?: string
  kind?: string
  description?: string
  risk_tier?: string
  installed: boolean
  capabilities_required?: string[]
  channels?: string[]
}

export default function OpenClawCatalogView() {
  const [data, setData] = useState<CatalogResponse | null>(null)
  const [smoke, setSmoke] = useState<SmokeResponse | null>(null)
  const [sidecar, setSidecar] = useState<SidecarState | null>(null)
  const [caps, setCaps] = useState<Capability[]>([])
  const [tools, setTools] = useState<OpenClawTool[]>([])
  const [toolBusy, setToolBusy] = useState<string | null>(null)
  const [toolResult, setToolResult] = useState<{ tool: string; result: string } | null>(null)
  const [fsAllow, setFsAllow] = useState<FsAllowEntry[]>([])
  const [fsAddPath, setFsAddPath] = useState('')
  const [fsAddMode, setFsAddMode] = useState<'r' | 'rw'>('r')
  const [fsBusy, setFsBusy] = useState(false)
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([])
  const [auditOpen, setAuditOpen] = useState(false)
  const [sealPlugins, setSealPlugins] = useState<SealPlugin[]>([])
  const [importing, setImporting] = useState(false)
  const [installBusy, setInstallBusy] = useState<string | null>(null)
  const [sidecarBusy, setSidecarBusy] = useState(false)
  const [capBusy, setCapBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filterCat, setFilterCat] = useState<string>('all')
  const [filterRisk, setFilterRisk] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  const loadSidecar = useCallback(async () => {
    try {
      const [rSt, rCaps, rTools, rFs, rAudit] = await Promise.all([
        fetch(`${API}/api/openclaw/sidecar/status`),
        fetch(`${API}/api/openclaw/capabilities`),
        fetch(`${API}/api/openclaw/tools`),
        fetch(`${API}/api/openclaw/fs-allowlist`),
        fetch(`${API}/api/openclaw/audit-log?limit=20`),
      ])
      const dSt = await rSt.json()
      const dCaps = await rCaps.json()
      const dTools = await rTools.json()
      const dFs = await rFs.json()
      const dAudit = await rAudit.json()
      if (dSt?.ok && dSt.state) setSidecar(dSt.state)
      if (dCaps?.ok && Array.isArray(dCaps.capabilities)) setCaps(dCaps.capabilities)
      if (dTools?.ok && Array.isArray(dTools.tools)) setTools(dTools.tools)
      if (dFs?.ok && Array.isArray(dFs.entries)) setFsAllow(dFs.entries)
      if (dAudit?.ok && Array.isArray(dAudit.entries)) setAuditEntries(dAudit.entries)
    } catch {/* noop */}
    // Load seal_plugins registry (Phase 2.1)
    try {
      const r = await fetch(`${API}/api/seal/plugins`)
      const d = await r.json()
      if (d?.ok && Array.isArray(d.plugins)) setSealPlugins(d.plugins)
    } catch {/* noop */}
  }, [])

  const importFromOpenclaw = async () => {
    setImporting(true)
    try {
      const r = await fetch(`${API}/api/seal/plugins/import-from-openclaw`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      const d = await r.json()
      if (d?.ok) alert(`✅ Importados ${d.imported || 0} plugins al registro SEAL (failed: ${d.failed || 0})`)
      else alert(`⚠️ Error: ${d?.error || 'desconocido'}`)
      await loadSidecar()
    } finally { setImporting(false) }
  }

  const toggleInstall = async (pluginId: string, installed: boolean) => {
    setInstallBusy(pluginId)
    try {
      await fetch(`${API}/api/seal/plugins/${encodeURIComponent(pluginId)}/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ installed }),
      })
      await loadSidecar()
    } finally { setInstallBusy(null) }
  }

  const toggleWatchdog = async () => {
    const enabled = sidecar?.watchdog_enabled
    const url = enabled ? `${API}/api/openclaw/sidecar/watchdog/disable` : `${API}/api/openclaw/sidecar/watchdog/enable`
    await fetch(url, { method: 'POST' })
    await loadSidecar()
  }

  const fsAdd = async () => {
    if (!fsAddPath.trim()) return
    setFsBusy(true)
    try {
      await fetch(`${API}/api/openclaw/fs-allowlist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: fsAddPath.trim(), mode: fsAddMode }),
      })
      setFsAddPath('')
      await loadSidecar()
    } finally { setFsBusy(false) }
  }
  const fsRemove = async (path: string) => {
    if (!confirm(`¿Quitar "${path}" del FS allowlist?`)) return
    setFsBusy(true)
    try {
      await fetch(`${API}/api/openclaw/fs-allowlist?path=${encodeURIComponent(path)}`, { method: 'DELETE' })
      await loadSidecar()
    } finally { setFsBusy(false) }
  }

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [rCat, rSmoke] = await Promise.all([
        fetch(`${API}/api/openclaw/catalog`),
        fetch(`${API}/api/openclaw/catalog/smoke`),
      ])
      const dCat: CatalogResponse = await rCat.json()
      const dSmoke: SmokeResponse = await rSmoke.json()
      if (!dCat.ok) {
        setError(dCat.error || 'Repo no encontrado. Verificá que /home/dadito/IA/openclaw exista.')
        setData(null)
      } else {
        setData(dCat)
      }
      setSmoke(dSmoke)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'fallo de red')
      setData(null); setSmoke(null)
    } finally {
      setLoading(false)
    }
    await loadSidecar()
  }, [loadSidecar])

  useEffect(() => { void load() }, [load])

  // Auto-refresh sidecar status while it's not stopped
  useEffect(() => {
    if (!sidecar || sidecar.status === 'stopped') return
    const t = setInterval(() => { void loadSidecar() }, 5000)
    return () => clearInterval(t)
  }, [sidecar, loadSidecar])

  const startSidecar = async () => {
    setSidecarBusy(true)
    try {
      await fetch(`${API}/api/openclaw/sidecar/start`, { method: 'POST' })
      await loadSidecar()
    } finally { setSidecarBusy(false) }
  }
  const stopSidecar = async () => {
    setSidecarBusy(true)
    try {
      await fetch(`${API}/api/openclaw/sidecar/stop`, { method: 'POST' })
      await loadSidecar()
    } finally { setSidecarBusy(false) }
  }
  const invokeTool = async (toolName: string) => {
    setToolBusy(toolName); setToolResult(null)
    try {
      const tool = tools.find(t => t.name === toolName)
      const input: Record<string, unknown> = {}
      for (const k of tool?.input_keys || []) {
        const v = prompt(`${toolName} — valor para '${k}':`, k === 'root' ? '/home/dadito/IA/openclaw/extensions' : '')
        if (v === null) { setToolBusy(null); return }
        input[k] = v
      }
      const r = await fetch(`${API}/api/openclaw/tool-call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: toolName, input }),
      })
      const d = await r.json()
      setToolResult({ tool: toolName, result: JSON.stringify(d, null, 2) })
    } catch (e) {
      setToolResult({ tool: toolName, result: `Error: ${e instanceof Error ? e.message : 'unknown'}` })
    } finally { setToolBusy(null) }
  }

  const toggleCap = async (name: string, enabled: boolean) => {
    setCapBusy(name)
    try {
      const isCritical = name.includes('shell_exec') || name.includes('fs_write') || name.includes('network_egress')
      // Si encendemos una critical, pedimos confirmación explícita antes
      if (enabled && isCritical) {
        const ok = confirm(`⚠️ Capability CRÍTICA: ${name}\n\nPodés exponerte a daño real (escritura de archivos, ejecución de shell, o tráfico de red). Solo activá esto si confiás 100% en el plugin.\n\n¿Confirmás?`)
        if (!ok) return
      }
      const r = await fetch(`${API}/api/openclaw/capabilities/${encodeURIComponent(name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled, user_confirmed: enabled && isCritical }),
      })
      const d = await r.json().catch(() => ({}))
      if (d?.requires_confirmation) {
        // Server pidió confirmación que no enviamos (shouldn't happen pero defensivo)
        alert(`Backend rechazó el cambio: requiere user_confirmed=true para ${name}`)
      }
      await loadSidecar()
    } finally { setCapBusy(null) }
  }

  const filtered = useMemo(() => {
    if (!data?.plugins) return []
    const q = search.trim().toLowerCase()
    return data.plugins.filter(p => {
      if (!p.ok) return false
      if (filterCat !== 'all' && !(p.categories || []).includes(filterCat)) return false
      if (filterRisk !== 'all' && p.risk_tier !== filterRisk) return false
      if (q && !p.name.toLowerCase().includes(q) && !(p.pkg_description || '').toLowerCase().includes(q)) return false
      return true
    })
  }, [data, filterCat, filterRisk, search])

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Box className="w-5 h-5 text-violet-500" />
            <h1 className="text-xl font-semibold text-slate-800">OpenClaw — Catálogo</h1>
            <span className="text-xs text-seal-muted">read-only · sin ejecución</span>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded border border-seal-border hover:border-violet-400 text-seal-muted hover:text-violet-500"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Disclaimer Fase 0 */}
        <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700 flex items-center gap-2">
          <Eye className="w-4 h-4 shrink-0" />
          <span>
            <strong>Fase 0 — solo lectura.</strong> SEAL escanea los <code className="bg-white/60 px-1 rounded">openclaw.plugin.json</code> sin ejecutar código de plugins. Ningún canal está conectado todavía.
          </span>
        </div>

        {/* Sidecar + Capabilities (Phase 0.5 / 1 — JARVIS backend) */}
        {sidecar && (
          <div className="mb-4 rounded-xl border border-violet-200 bg-violet-50 p-3 shadow-sm">
            <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
              <div className="flex items-center gap-2">
                <Activity className={`w-4 h-4 ${sidecar.status === 'running' ? 'text-emerald-500 animate-pulse' : sidecar.status === 'error' ? 'text-red-500' : 'text-stone-400'}`} />
                <span className="text-sm font-semibold text-slate-800">Sidecar OpenClaw</span>
                <span className={`text-[11px] px-1.5 py-0.5 rounded-full border ${
                  sidecar.status === 'running' ? 'bg-emerald-100 text-emerald-700 border-emerald-200' :
                  sidecar.status === 'starting' ? 'bg-amber-100 text-amber-700 border-amber-200' :
                  sidecar.status === 'error' ? 'bg-red-100 text-red-700 border-red-200' :
                  'bg-stone-100 text-stone-600 border-stone-200'
                }`}>
                  {sidecar.status === 'running' ? '● UP' : sidecar.status === 'starting' ? '○ INICIANDO' : sidecar.status === 'error' ? '✗ ERROR' : '○ DETENIDO'}
                </span>
                {sidecar.pid && <span className="text-[10px] text-stone-500 font-mono">pid {sidecar.pid}</span>}
                {sidecar.server && <span className="text-[10px] text-stone-500">{sidecar.server} {sidecar.server_version}</span>}
                {(sidecar.restart_count ?? 0) > 0 && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 border border-amber-200" title={`Crashes ${sidecar.crash_count ?? 0} · Restarts ${sidecar.restart_count}`}>
                    ↻ {sidecar.restart_count} restart{sidecar.restart_count === 1 ? '' : 's'}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={toggleWatchdog}
                  className={`flex items-center gap-1 px-2 py-1 rounded text-xs border ${sidecar.watchdog_enabled ? 'bg-emerald-50 border-emerald-300 text-emerald-700' : 'bg-white border-stone-300 text-stone-500 hover:border-emerald-300'}`}
                  title={sidecar.watchdog_enabled ? 'Watchdog ON — auto-restart si crashea (click para apagar)' : 'Watchdog OFF — sidecar no se auto-recupera (click para encender)'}
                >
                  <Activity className="w-3 h-3" />
                  watchdog {sidecar.watchdog_enabled ? 'ON' : 'OFF'}
                </button>
                {sidecar.status === 'running' ? (
                  <button
                    onClick={stopSidecar}
                    disabled={sidecarBusy}
                    className="flex items-center gap-1 px-2 py-1 rounded bg-red-500 hover:bg-red-600 text-white text-xs disabled:opacity-50"
                  >
                    <Square className="w-3 h-3" /> Detener
                  </button>
                ) : (
                  <button
                    onClick={startSidecar}
                    disabled={sidecarBusy}
                    className="flex items-center gap-1 px-2 py-1 rounded bg-violet-500 hover:bg-violet-600 text-white text-xs disabled:opacity-50"
                  >
                    <Play className="w-3 h-3" /> Iniciar
                  </button>
                )}
                <button
                  onClick={loadSidecar}
                  disabled={sidecarBusy}
                  className="p-1 rounded hover:bg-white text-stone-500"
                  title="Actualizar"
                >
                  <RefreshCw className={`w-3 h-3 ${sidecarBusy ? 'animate-spin' : ''}`} />
                </button>
              </div>
            </div>
            {sidecar.last_handshake_at && (
              <p className="text-[11px] text-slate-600">Último handshake: {sidecar.last_handshake_at} {sidecar.session_id && <code className="bg-white/60 px-1 rounded ml-1">{sidecar.session_id.slice(0,12)}…</code>}</p>
            )}
            {sidecar.last_error && (
              <p className="text-[11px] text-red-600 mt-1">⚠️ {sidecar.last_error}</p>
            )}

            {/* Tools (Phase 1.3) */}
            {tools.length > 0 && (
              <div className="mt-3 pt-3 border-t border-violet-200">
                <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">
                  Tools disponibles (sidecar mock — Phase 1.3)
                </p>
                <div className="space-y-1">
                  {tools.map(t => {
                    const capEnabled = caps.find(c => c.name === t.capability_required)?.enabled || false
                    const sidecarUp = sidecar?.status === 'running'
                    const canRun = sidecarUp && capEnabled
                    return (
                      <div key={t.name} className="flex items-center gap-2 px-2 py-1.5 rounded bg-white border border-stone-200 text-xs">
                        <Wrench className="w-3.5 h-3.5 text-violet-500 shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <code className="text-slate-800 font-semibold">{t.name}</code>
                            <span className="text-[10px] text-stone-500">cap: <code className="bg-stone-100 px-1 rounded">{t.capability_required}</code></span>
                            {t.input_keys.length > 0 && <span className="text-[10px] text-stone-500">→ pide: {t.input_keys.join(', ')}</span>}
                          </div>
                          <p className="text-[11px] text-slate-600 truncate">{t.description}</p>
                        </div>
                        <button
                          onClick={() => void invokeTool(t.name)}
                          disabled={!canRun || toolBusy === t.name}
                          className={`px-2 py-1 rounded text-[11px] ${canRun ? 'bg-violet-500 hover:bg-violet-600 text-white' : 'bg-stone-200 text-stone-400 cursor-not-allowed'}`}
                          title={!sidecarUp ? 'Sidecar detenido' : !capEnabled ? `Requiere capability ${t.capability_required}` : 'Invocar'}
                        >
                          {toolBusy === t.name ? '...' : 'Probar'}
                        </button>
                      </div>
                    )
                  })}
                </div>
                {toolResult && (
                  <div className="mt-2 rounded border border-violet-200 bg-violet-50/60 p-2">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[10px] uppercase tracking-widest text-violet-600">Resultado · {toolResult.tool}</span>
                      <button onClick={() => setToolResult(null)} className="text-stone-400 hover:text-stone-600 text-xs">×</button>
                    </div>
                    <pre className="text-[10px] text-slate-700 whitespace-pre-wrap font-mono max-h-48 overflow-y-auto">{toolResult.result}</pre>
                  </div>
                )}
              </div>
            )}

            {/* FS Allowlist (Phase 1.4 — defense-in-depth) */}
            <div className="mt-3 pt-3 border-t border-violet-200">
              <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">
                Filesystem allowlist (rutas que el sidecar puede leer/escribir)
              </p>
              {fsAllow.length === 0 ? (
                <p className="text-[11px] text-stone-500 italic mb-2">Ninguna ruta permitida — el sidecar no puede tocar el disco.</p>
              ) : (
                <ul className="space-y-1 mb-2">
                  {fsAllow.map(e => (
                    <li key={e.path} className="flex items-center gap-2 px-2 py-1 rounded bg-white border border-stone-200 text-xs">
                      <code className="flex-1 truncate text-slate-700">{e.path}</code>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${e.mode === 'rw' ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>
                        {e.mode === 'rw' ? 'lectura+escritura' : 'solo lectura'}
                      </span>
                      <button
                        onClick={() => void fsRemove(e.path)}
                        disabled={fsBusy}
                        className="p-1 rounded text-stone-400 hover:bg-red-50 hover:text-red-500"
                        title="Quitar del allowlist"
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex items-center gap-1.5">
                <input
                  value={fsAddPath}
                  onChange={e => setFsAddPath(e.target.value)}
                  placeholder="/home/dadito/Documentos o /tmp/seal-work"
                  className="flex-1 px-2 py-1 rounded border border-stone-200 text-xs text-slate-700 bg-white focus:outline-none focus:border-violet-400"
                />
                <select
                  value={fsAddMode}
                  onChange={e => setFsAddMode(e.target.value as 'r' | 'rw')}
                  className="px-2 py-1 rounded border border-stone-200 text-xs bg-white text-slate-700"
                >
                  <option value="r">solo lectura</option>
                  <option value="rw">lectura+escritura</option>
                </select>
                <button
                  onClick={fsAdd}
                  disabled={!fsAddPath.trim() || fsBusy}
                  className="px-2 py-1 rounded bg-violet-500 hover:bg-violet-600 text-white text-xs disabled:opacity-40"
                >
                  Agregar
                </button>
              </div>
              <p className="text-[10px] text-stone-400 mt-1">
                Defense-in-depth: aún con capability fs_read/fs_write activa, el sidecar solo puede acceder a estas rutas exactas.
              </p>
            </div>

            {/* Capabilities */}
            {caps.length > 0 && (
              <div className="mt-3 pt-3 border-t border-violet-200">
                <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">
                  Capabilities (default OFF, opt-in granular)
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
                  {caps.map(c => {
                    const isCritical = c.name.includes('shell_exec') || c.name.includes('fs_write') || c.name.includes('network_egress')
                    return (
                      <label
                        key={c.name}
                        className={`flex items-center gap-2 px-2 py-1.5 rounded border cursor-pointer ${
                          c.enabled ? (isCritical ? 'border-red-300 bg-red-50' : 'border-emerald-300 bg-emerald-50') : 'border-stone-200 bg-white hover:border-stone-300'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={c.enabled}
                          disabled={capBusy === c.name}
                          onChange={e => void toggleCap(c.name, e.target.checked)}
                          className="rounded"
                        />
                        <code className="text-[11px] flex-1 truncate">{c.name}</code>
                        {isCritical && <span title="capability crítica"><ShieldAlert className="w-3 h-3 text-red-500" /></span>}
                      </label>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Audit log (Phase 1.5) */}
            <div className="mt-3 pt-3 border-t border-violet-200">
              <button
                onClick={() => setAuditOpen(v => !v)}
                className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-slate-500 hover:text-slate-700"
              >
                <span>{auditOpen ? '▼' : '▶'}</span>
                Audit log OpenClaw {auditEntries.length > 0 && `(${auditEntries.length})`}
              </button>
              {auditOpen && (
                <div className="mt-2 max-h-64 overflow-y-auto">
                  {auditEntries.length === 0 ? (
                    <p className="text-[11px] text-stone-500 italic">Aún no hay actividad registrada. Las invocaciones de tools, cambios de capabilities y start/stop del sidecar aparecerán acá.</p>
                  ) : (
                    <ul className="space-y-0.5 text-[11px]">
                      {auditEntries.map((e, i) => {
                        const ts = e.ts || e.timestamp || ''
                        const isError = (e.level || '').toLowerCase() === 'error' || (e.action || '').includes('denied') || (e.action || '').includes('failed')
                        return (
                          <li key={e.id ?? i} className={`px-2 py-1 rounded font-mono ${isError ? 'bg-red-50 text-red-700' : 'bg-white text-slate-700'}`}>
                            <span className="text-stone-400">{ts.slice(11, 19)}</span>
                            {' '}
                            <span className="font-semibold">{e.action}</span>
                            {e.actor && <span className="text-stone-500"> · {e.actor}</span>}
                            {typeof e.payload === 'string' && e.payload && (
                              <span className="text-stone-500"> · {e.payload.slice(0, 80)}</span>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Smoke baseline (ADA spec) */}
        {smoke && (
          <div className={`mb-4 rounded-lg border px-3 py-2 ${smoke.ok ? 'border-emerald-200 bg-emerald-50' : 'border-amber-200 bg-amber-50'}`}>
            <div className="flex items-center gap-2 text-xs mb-1.5">
              {smoke.ok ? <CheckCircle2 className="w-4 h-4 text-emerald-600" /> : <XCircle className="w-4 h-4 text-amber-600" />}
              <span className={`font-semibold ${smoke.ok ? 'text-emerald-700' : 'text-amber-700'}`}>
                Smoke baseline ADA: {smoke.passed}/{smoke.total} ✓
              </span>
              <span className="text-stone-500">(telegram · discord · matrix · ollama · memory-lancedb)</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {smoke.checks.map(c => (
                <span
                  key={c.name}
                  className={`flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border ${c.ok ? 'border-emerald-300 bg-white text-emerald-700' : 'border-red-300 bg-red-50 text-red-700'}`}
                  title={c.ok ? `${c.manifest_id} · ${c.categories?.join(',')} · risk ${c.risk_tier}` : c.error}
                >
                  {c.ok ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                  {c.name}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* SEAL plugin registry (Phase 2.1) */}
        <div className="mb-4 rounded-xl border border-fuchsia-200 bg-fuchsia-50 p-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              <Package className="w-4 h-4 text-fuchsia-600" />
              <span className="text-sm font-semibold text-slate-800">Registro SEAL nativo</span>
              <span className="text-[11px] text-stone-600">
                {sealPlugins.length === 0
                  ? 'sin plugins importados aún'
                  : `${sealPlugins.length} importados · ${sealPlugins.filter(p => p.installed).length} instalados`}
              </span>
            </div>
            <button
              onClick={importFromOpenclaw}
              disabled={importing}
              className="flex items-center gap-1 px-3 py-1 rounded bg-fuchsia-500 hover:bg-fuchsia-600 text-white text-xs disabled:opacity-50"
              title="Importa manifests OpenClaw al registro SEAL canónico (no ejecuta código)"
            >
              <Download className="w-3 h-3" />
              {importing ? 'Importando…' : sealPlugins.length === 0 ? 'Importar de OpenClaw' : 'Re-importar'}
            </button>
          </div>
          <p className="text-[10px] text-fuchsia-700/80 mt-1">
            seal.plugin.json schema canónico — registro propio compatible OpenClaw. No ejecuta código.
            Marca install/uninstall per plugin sin tocar disco del repo OpenClaw.
          </p>
          {sealPlugins.filter(p => p.installed).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {sealPlugins.filter(p => p.installed).slice(0, 10).map(p => (
                <span key={p.id} className="text-[10px] px-1.5 py-0.5 rounded-full bg-white border border-fuchsia-200 text-fuchsia-700">
                  {p.name || p.id}
                </span>
              ))}
              {sealPlugins.filter(p => p.installed).length > 10 && (
                <span className="text-[10px] text-stone-500">+{sealPlugins.filter(p => p.installed).length - 10} más</span>
              )}
            </div>
          )}
        </div>

        {/* Counts row */}
        {data?.counts && (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2 mb-4 text-xs">
            <Stat label="Total" value={data.counts.total || 0} tone="text-slate-700" />
            <Stat label="Canales" value={data.counts.channel || 0} tone="text-violet-600" />
            <Stat label="Providers" value={data.counts.provider || 0} tone="text-sky-600" />
            <Stat label="Tools" value={data.counts.tool || 0} tone="text-emerald-600" />
            <Stat label="🔴 Crítico" value={data.counts.risk_critical || 0} tone="text-red-600" />
            <Stat label="🟡 Alto" value={data.counts.risk_high || 0} tone="text-amber-600" />
            <Stat label="🟢 Normal" value={data.counts.risk_normal || 0} tone="text-emerald-600" />
          </div>
        )}

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2 mb-3 text-xs">
          <span className="text-seal-muted">Categoría:</span>
          {['all', ...ALL_CATS].map(c => (
            <button
              key={c}
              onClick={() => setFilterCat(c)}
              className={`px-2 py-0.5 rounded border ${filterCat === c ? 'border-violet-500 bg-violet-50 text-violet-700' : 'border-seal-border text-seal-muted hover:text-slate-700'}`}
            >
              {c === 'all' ? 'Todas' : CAT_LABEL[c] || c}
            </button>
          ))}
          <span className="ml-3 text-seal-muted">Riesgo:</span>
          {ALL_RISKS.map(r => (
            <button
              key={r}
              onClick={() => setFilterRisk(r)}
              className={`px-2 py-0.5 rounded border ${filterRisk === r ? 'border-violet-500 bg-violet-50 text-violet-700' : 'border-seal-border text-seal-muted hover:text-slate-700'}`}
            >
              {r === 'all' ? 'Todos' : RISK_BADGE[r]?.label || r}
            </button>
          ))}
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Buscar por nombre o descripción..."
            className="ml-auto px-2 py-1 rounded border border-seal-border text-xs text-slate-700 bg-white focus:outline-none focus:border-violet-400 w-56"
          />
        </div>

        {/* Plugin list */}
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            ⚠️ {error}
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="rounded-lg border border-dashed border-seal-border bg-seal-surface p-6 text-center text-sm text-seal-muted">
            Sin resultados con los filtros actuales.
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {filtered.map(p => {
            const isOpen = expanded === p.name
            const riskMeta = RISK_BADGE[p.risk_tier] || RISK_BADGE.normal
            const RiskIcon = riskMeta.icon
            return (
              <div key={p.name} className="rounded-xl border border-seal-border bg-seal-surface shadow-sm">
                <button
                  onClick={() => setExpanded(isOpen ? null : p.name)}
                  className="w-full text-left p-3 flex items-start gap-3 hover:bg-stone-50"
                >
                  <div className="flex flex-col gap-1 mt-0.5">
                    {(p.categories || []).map(c => {
                      const Icon = CAT_ICON[c] || Tag
                      return <Icon key={c} className="w-4 h-4 text-violet-500" />
                    })}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold text-slate-800">{p.name}</span>
                      <span className={`flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border ${riskMeta.color}`}>
                        <RiskIcon className="w-3 h-3" /> {riskMeta.label}
                      </span>
                      {(p.categories || []).map(c => (
                        <span key={c} className="text-[10px] px-1.5 py-0.5 rounded bg-stone-100 text-slate-500">
                          {CAT_LABEL[c] || c}
                        </span>
                      ))}
                      {p.pkg_version && (
                        <span className="text-[10px] text-stone-400 font-mono">v{p.pkg_version}</span>
                      )}
                    </div>
                    {p.pkg_description && (
                      <p className="text-xs text-slate-600 mt-1 line-clamp-1">{p.pkg_description}</p>
                    )}
                  </div>
                </button>
                {isOpen && (
                  <div className="border-t border-seal-border px-3 py-2.5 bg-stone-50 text-xs space-y-1.5">
                    {!!p.channels?.length && (
                      <Row label="Canales">{p.channels.join(', ')}</Row>
                    )}
                    {!!p.contracts?.length && (
                      <Row label="Contratos">{p.contracts.join(', ')}</Row>
                    )}
                    {!!p.providers_auth_envs?.length && (
                      <Row label="ENV providers">
                        <code className="bg-white px-1 rounded">{p.providers_auth_envs.join(', ')}</code>
                      </Row>
                    )}
                    {!!p.config_schema_keys?.length && (
                      <Row label="Config schema">{p.config_schema_keys.join(', ')}</Row>
                    )}
                    <Row label="Activación">
                      {p.activation_on_startup ? 'al arrancar' : 'on-demand'} ·
                      {p.enabled_by_default ? ' habilitado' : ' deshabilitado'} por default
                    </Row>
                    {p.pkg_name && (
                      <Row label="npm">
                        <code className="bg-white px-1 rounded">{p.pkg_name}</code>
                      </Row>
                    )}
                    <Row label="Path">
                      <code className="bg-white px-1 rounded text-[10px]">{p.path}</code>
                    </Row>
                    {(() => {
                      const sp = sealPlugins.find(s => s.id === p.name)
                      if (!sp) return (
                        <Row label="Registro SEAL">
                          <span className="text-stone-500">no importado · click "Importar de OpenClaw" arriba</span>
                        </Row>
                      )
                      return (
                        <Row label="Registro SEAL">
                          <button
                            onClick={() => void toggleInstall(sp.id, !sp.installed)}
                            disabled={installBusy === sp.id}
                            className={`text-[11px] px-2 py-0.5 rounded border ${sp.installed ? 'border-emerald-300 bg-emerald-50 text-emerald-700' : 'border-stone-300 bg-white text-stone-600 hover:border-emerald-300'} disabled:opacity-50`}
                          >
                            {installBusy === sp.id ? '...' : sp.installed ? '✓ Instalado · click para desinstalar' : 'Instalar en SEAL'}
                          </button>
                          <span className="ml-2 text-stone-400 text-[10px]">v{sp.version || '0.0.0'} · kind={sp.kind}</span>
                        </Row>
                      )
                    })()}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <p className="mt-6 text-[11px] text-seal-muted text-center pb-4">
          Catálogo OpenClaw {data?.plugins?.length ? `(${data.plugins.length} plugins)` : ''} — Fase 0 read-only, ADA spec 23-may-2026.
        </p>
      </div>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="rounded-lg border border-seal-border bg-seal-surface p-2 text-center">
      <div className={`text-lg font-semibold ${tone}`}>{value}</div>
      <div className="text-[10px] uppercase tracking-widest text-seal-muted">{label}</div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2">
      <span className="w-28 text-stone-400 uppercase tracking-widest text-[10px] shrink-0 pt-0.5">{label}</span>
      <span className="flex-1 text-slate-700">{children}</span>
    </div>
  )
}
