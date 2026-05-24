import { useCallback, useEffect, useMemo, useState } from 'react'
import { API } from '../App'
import { Box, RefreshCw, Plug, Cpu, Wrench, Tag, ShieldAlert, ShieldCheck, ShieldQuestion, Eye, CheckCircle2, XCircle } from 'lucide-react'

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

export default function OpenClawCatalogView() {
  const [data, setData] = useState<CatalogResponse | null>(null)
  const [smoke, setSmoke] = useState<SmokeResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filterCat, setFilterCat] = useState<string>('all')
  const [filterRisk, setFilterRisk] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

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
  }, [])

  useEffect(() => { void load() }, [load])

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
