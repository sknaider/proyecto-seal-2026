import { useEffect, useState, useCallback } from 'react'
import { API } from '../App'
import { Cpu, Cloud, Lock, Save, AlertCircle, CheckCircle, RefreshCw } from 'lucide-react'

interface RoutingRow {
  role: string
  provider: string
  model: string
  fallback_provider?: string | null
  fallback_model?: string | null
  max_tokens?: number | null
  temperature?: number | null
  enabled: boolean
  updated_at?: string | null
}

interface BYOKStatus {
  ok: boolean
  vault_exists: boolean
  providers_configured: string[]
  vault_path?: string
  keyring_available?: boolean
  master_key_source?: string
  allowed_providers?: string[]
}

const PROVIDERS = ['ollama', 'anthropic', 'openai', 'openrouter', 'custom'] as const
const ROLE_LABELS: Record<string, string> = {
  reasoning: '🧠 Razonamiento',
  agentic: '🤖 Agentic / tools',
  coding: '💻 Coding',
  summary: '📝 Summarization',
}

export default function AIBackendView() {
  const [rows, setRows] = useState<RoutingRow[]>([])
  const [byok, setByok] = useState<BYOKStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [savingRow, setSavingRow] = useState<string | null>(null)
  const [savedHint, setSavedHint] = useState<string | null>(null)

  // BYOK key entry
  const [keyProvider, setKeyProvider] = useState<string>('anthropic')
  const [keyValue, setKeyValue] = useState<string>('')
  const [keyMsg, setKeyMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [r1, r2] = await Promise.all([
        fetch(`${API}/api/llm-routing?agent=DEFAULT`).then(r => r.json()),
        fetch(`${API}/api/byok/status`).then(r => r.json()),
      ])
      setRows(r1.rows || [])
      setByok(r2)
    } catch {
      setRows([])
      setByok(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const saveRow = async (row: RoutingRow) => {
    setSavingRow(row.role)
    try {
      const r = await fetch(`${API}/api/llm-routing`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: 'DEFAULT', rows: [row] }),
      })
      const d = await r.json()
      if (d.ok !== false) {
        setSavedHint(`Guardado ${row.role}`)
        setTimeout(() => setSavedHint(null), 2500)
      }
    } finally {
      setSavingRow(null)
    }
  }

  const saveKey = async () => {
    if (!keyValue.trim()) {
      setKeyMsg('Ingresá un API key.')
      return
    }
    setKeyMsg('Guardando…')
    try {
      const r = await fetch(`${API}/api/byok/key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: keyProvider, api_key: keyValue }),
      })
      const d = await r.json()
      if (d.ok) {
        setKeyMsg(`Guardado en vault (${keyProvider}).`)
        setKeyValue('')
        load()
      } else {
        setKeyMsg(`Error: ${d.error || 'desconocido'}`)
      }
    } catch (e) {
      setKeyMsg(`Error: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const updateRow = (role: string, patch: Partial<RoutingRow>) => {
    setRows(prev => prev.map(r => r.role === role ? { ...r, ...patch } : r))
  }

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-3xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Cpu className="w-5 h-5 text-blue-500" />
            <h1 className="text-xl font-semibold text-slate-800">Modelos de IA</h1>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded border border-seal-border hover:border-blue-400 text-seal-muted hover:text-blue-500"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Routing por rol */}
        <section className="rounded-xl bg-seal-surface border border-seal-border p-4">
          <h2 className="text-sm font-semibold text-slate-800 mb-1">Routing por rol</h2>
          <p className="text-xs text-seal-muted mb-3">
            Tu SEAL App rutea cada tarea al modelo correcto. Default: local con Ollama. Cambiá si querés probar cloud (necesitás BYOK abajo).
          </p>
          <div className="space-y-2">
            {rows.map(row => (
              <div key={row.role} className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center p-2 rounded border border-seal-border bg-stone-50/50">
                <div className="md:col-span-3 text-xs font-medium text-slate-700">
                  {ROLE_LABELS[row.role] || row.role}
                </div>
                <select
                  value={row.provider}
                  onChange={e => updateRow(row.role, { provider: e.target.value })}
                  className="md:col-span-3 px-2 py-1 text-xs bg-white border border-seal-border rounded"
                >
                  {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
                <input
                  type="text"
                  value={row.model}
                  onChange={e => updateRow(row.role, { model: e.target.value })}
                  placeholder="model name (ej: claude-opus-4-7)"
                  className="md:col-span-4 px-2 py-1 text-xs bg-white border border-seal-border rounded font-mono"
                />
                <button
                  onClick={() => saveRow(row)}
                  disabled={savingRow === row.role}
                  className="md:col-span-2 px-2 py-1 rounded bg-blue-500 hover:bg-blue-600 text-white text-xs disabled:opacity-50 flex items-center justify-center gap-1"
                >
                  <Save className="w-3 h-3" /> Guardar
                </button>
              </div>
            ))}
            {rows.length === 0 && !loading && (
              <p className="text-xs text-seal-muted text-center py-4">No hay rows. (¿Backend :8769 disponible?)</p>
            )}
          </div>
          {savedHint && (
            <p className="text-xs text-emerald-600 mt-2 flex items-center gap-1">
              <CheckCircle className="w-3 h-3" /> {savedHint}
            </p>
          )}
        </section>

        {/* BYOK Vault */}
        <section className="rounded-xl bg-seal-surface border border-seal-border p-4">
          <div className="flex items-center gap-2 mb-1">
            <Lock className="w-4 h-4 text-blue-500" />
            <h2 className="text-sm font-semibold text-slate-800">API keys (BYOK)</h2>
          </div>
          <p className="text-xs text-seal-muted mb-3">
            Tus claves se cifran AES-GCM 256 con master key en {byok?.master_key_source ?? '—'} (vault local).
          </p>

          {byok && (
            <div className="mb-3 text-xs text-seal-muted flex flex-wrap items-center gap-2">
              <span>Vault:</span>
              <code className="font-mono px-1.5 py-0.5 bg-stone-100 rounded">{byok.vault_path}</code>
              <span>·</span>
              <span>{byok.providers_configured.length === 0 ? 'sin keys' : byok.providers_configured.join(', ')}</span>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <select
              value={keyProvider}
              onChange={e => setKeyProvider(e.target.value)}
              className="px-2 py-1.5 text-xs bg-white border border-seal-border rounded"
            >
              {(byok?.allowed_providers ?? ['anthropic', 'openai', 'openrouter']).map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <input
              type="password"
              value={keyValue}
              onChange={e => setKeyValue(e.target.value)}
              placeholder="sk-..."
              className="flex-1 min-w-[260px] px-2 py-1.5 text-xs bg-white border border-seal-border rounded font-mono"
            />
            <button
              onClick={saveKey}
              disabled={!keyValue.trim()}
              className="px-3 py-1.5 rounded bg-blue-500 hover:bg-blue-600 text-white text-xs disabled:opacity-50 flex items-center gap-1"
            >
              <Cloud className="w-3 h-3" /> Guardar en vault
            </button>
          </div>
          {keyMsg && (
            <p className="text-xs mt-2 flex items-center gap-1 text-seal-muted">
              <AlertCircle className="w-3 h-3" /> {keyMsg}
            </p>
          )}
        </section>

        <div className="text-[11px] text-seal-muted text-center pb-4">
          Local-first. Cloud opt-in con tu propia API key — pagás directo al proveedor, sin markup.
        </div>
      </div>
    </div>
  )
}
