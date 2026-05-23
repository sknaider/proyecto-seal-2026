import { useCallback, useEffect, useState } from 'react'
import { API } from '../App'
import { Filter, RefreshCw, Wand2, Plus, Trash2, Upload, Download, BarChart3 } from 'lucide-react'

interface Rule {
  id: string
  label: string
  category: string
  pattern: string
  enabled: boolean
  builtin: boolean
}

interface Stat {
  rule_id: string
  label: string
  category: string
  match_count: number
  chars_saved: number
  last_used_at?: string | null
}

interface CompactResult {
  ok: boolean
  input_chars: number
  output_chars: number
  savings_pct: number
  rules_applied: { rule: string; matches: number }[]
  output: string
}

const SAMPLE = "\u001b[31maviso\u001b[0m: revisar salida larga\nnpm warn deprecated old-package\nlisto"

export default function TokenJuiceView() {
  const [rules, setRules] = useState<Rule[]>([])
  const [stats, setStats] = useState<Stat[]>([])
  const [input, setInput] = useState(SAMPLE)
  const [result, setResult] = useState<CompactResult | null>(null)
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(false)
  const [draft, setDraft] = useState({ id: '', label: '', pattern: '', category: 'custom' })
  const [importText, setImportText] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setMessage('')
    try {
      const [rulesRes, statsRes] = await Promise.all([
        fetch(`${API}/api/tokenjuice/rules?include_disabled=true`),
        fetch(`${API}/api/tokenjuice/stats`),
      ])
      const rulesData = await rulesRes.json()
      const statsData = await statsRes.json()
      setRules(rulesData.rules || [])
      setStats(statsData.stats || [])
    } catch {
      setRules([])
      setStats([])
      setMessage('No se pudo cargar TokenJuice')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const compact = async () => {
    setLoading(true)
    setMessage('')
    try {
      const r = await fetch(`${API}/api/tokenjuice/compact`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: input }),
      })
      setResult(await r.json())
      await load()
    } finally {
      setLoading(false)
    }
  }

  const saveRule = async () => {
    setMessage('')
    const payload = {
      id: draft.id || undefined,
      label: draft.label,
      pattern: draft.pattern,
      category: draft.category || 'custom',
      enabled: true,
    }
    try {
      const r = await fetch(`${API}/api/tokenjuice/rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!r.ok) throw new Error(await readFailure(r))
      setDraft({ id: '', label: '', pattern: '', category: 'custom' })
      setMessage('Regla guardada')
      await load()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'No se pudo guardar')
    }
  }

  const toggleRule = async (rule: Rule) => {
    if (rule.builtin) return
    try {
      const r = await fetch(`${API}/api/tokenjuice/rules/${rule.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !rule.enabled }),
      })
      if (!r.ok) throw new Error(await readFailure(r))
      await load()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'No se pudo actualizar')
    }
  }

  const deleteRule = async (rule: Rule) => {
    if (rule.builtin) return
    try {
      const r = await fetch(`${API}/api/tokenjuice/rules/${rule.id}`, { method: 'DELETE' })
      if (!r.ok) throw new Error(await readFailure(r))
      await load()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'No se pudo borrar')
    }
  }

  const exportRules = async () => {
    try {
      const r = await fetch(`${API}/api/tokenjuice/export`)
      const d = await r.json()
      setImportText(JSON.stringify(d, null, 2))
      setMessage('Export listo')
    } catch {
      setMessage('No se pudo exportar')
    }
  }

  const importRules = async () => {
    try {
      const payload = JSON.parse(importText)
      const r = await fetch(`${API}/api/tokenjuice/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ custom_rules: payload.custom_rules || [] }),
      })
      if (!r.ok) throw new Error(await readFailure(r))
      const d = await r.json()
      setMessage(`${d.count} reglas importadas`)
      await load()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Import inválido')
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-5xl mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Filter className="w-5 h-5 text-blue-500" />
            <h1 className="text-xl font-semibold text-slate-800">Contexto</h1>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded border border-seal-border hover:border-blue-400 text-seal-muted hover:text-blue-500"
            title="Refrescar"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {message && <p className="text-xs text-seal-muted">{message}</p>}

        <section className="rounded-lg border border-seal-border bg-seal-surface p-4">
          <h2 className="text-sm font-semibold text-slate-800 mb-2">Reducir ruido</h2>
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            rows={6}
            className="w-full rounded border border-seal-border bg-white px-3 py-2 text-xs text-slate-700 outline-none resize-none"
          />
          <button
            onClick={compact}
            disabled={!input.trim() || loading}
            className="mt-3 inline-flex items-center gap-2 rounded bg-blue-500 px-3 py-1.5 text-xs text-white disabled:opacity-50"
          >
            <Wand2 className="w-3.5 h-3.5" />
            Limpiar texto
          </button>
        </section>

        <section className="rounded-lg border border-seal-border bg-seal-surface p-4">
          <h2 className="text-sm font-semibold text-slate-800 mb-3">Nueva regla</h2>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
            <input
              value={draft.id}
              onChange={e => setDraft({ ...draft, id: e.target.value })}
              className="rounded border border-seal-border px-2 py-2 text-xs outline-none"
              placeholder="id opcional"
            />
            <input
              value={draft.label}
              onChange={e => setDraft({ ...draft, label: e.target.value })}
              className="rounded border border-seal-border px-2 py-2 text-xs outline-none"
              placeholder="Nombre"
            />
            <input
              value={draft.category}
              onChange={e => setDraft({ ...draft, category: e.target.value })}
              className="rounded border border-seal-border px-2 py-2 text-xs outline-none"
              placeholder="Categoría"
            />
            <button
              onClick={saveRule}
              disabled={!draft.label.trim() || !draft.pattern.trim()}
              className="flex min-h-9 items-center justify-center gap-2 rounded bg-slate-800 px-3 py-1.5 text-xs text-white disabled:bg-stone-300 disabled:text-stone-500"
            >
              <Plus className="w-3.5 h-3.5" />
              Guardar
            </button>
          </div>
          <input
            value={draft.pattern}
            onChange={e => setDraft({ ...draft, pattern: e.target.value })}
            className="mt-2 w-full rounded border border-seal-border px-2 py-2 font-mono text-xs outline-none"
            placeholder="Regex"
          />
        </section>

        {result && (
          <section className="rounded-lg border border-seal-border bg-seal-surface p-4">
            <div className="flex flex-wrap items-center gap-2 text-xs text-seal-muted mb-3">
              <span>{result.input_chars} caracteres</span>
              <span>→</span>
              <span>{result.output_chars} caracteres</span>
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-700">{result.savings_pct}% menos</span>
            </div>
            <pre className="whitespace-pre-wrap rounded bg-stone-50 border border-seal-border p-3 text-xs text-slate-700">{result.output || '(vacío)'}</pre>
          </section>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] gap-4">
          <section className="rounded-lg border border-seal-border bg-seal-surface p-4">
            <h2 className="text-sm font-semibold text-slate-800 mb-2">Reglas</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {rules.map(rule => (
                <div key={rule.id} className={`rounded border border-seal-border bg-stone-50 px-3 py-2 ${rule.enabled ? '' : 'opacity-60'}`}>
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium text-slate-700">{rule.label}</p>
                      <p className="text-[10px] text-seal-muted">{rule.category} · {rule.builtin ? 'base' : 'custom'} · {rule.enabled ? 'activa' : 'apagada'}</p>
                      <p className="mt-1 truncate font-mono text-[10px] text-seal-muted">{rule.pattern}</p>
                    </div>
                    {!rule.builtin && (
                      <div className="flex shrink-0 items-center gap-1">
                        <button
                          onClick={() => void toggleRule(rule)}
                          className="rounded border border-seal-border px-2 py-1 text-[10px] text-slate-600 hover:border-blue-400"
                        >
                          {rule.enabled ? 'Off' : 'On'}
                        </button>
                        <button
                          onClick={() => void deleteRule(rule)}
                          className="rounded border border-seal-border p-1 text-seal-muted hover:border-red-300 hover:text-red-600"
                          title="Borrar"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <aside className="space-y-4">
            <section className="rounded-lg border border-seal-border bg-seal-surface p-4">
              <div className="mb-2 flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-blue-500" />
                <h2 className="text-sm font-semibold text-slate-800">Stats</h2>
              </div>
              {stats.length === 0 ? (
                <p className="text-xs text-seal-muted">Sin uso registrado.</p>
              ) : (
                <div className="space-y-2">
                  {stats.slice(0, 6).map(stat => (
                    <div key={stat.rule_id} className="rounded border border-seal-border bg-stone-50 p-2">
                      <p className="truncate text-xs font-medium text-slate-700">{stat.label}</p>
                      <p className="text-[10px] text-seal-muted">{stat.match_count} matches · {stat.chars_saved} chars</p>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="rounded-lg border border-seal-border bg-seal-surface p-4">
              <h2 className="text-sm font-semibold text-slate-800 mb-2">Import / Export</h2>
              <div className="flex gap-2 mb-2">
                <button onClick={exportRules} className="flex flex-1 items-center justify-center gap-2 rounded border border-seal-border px-2 py-1.5 text-xs hover:border-blue-400">
                  <Download className="w-3.5 h-3.5" />
                  Export
                </button>
                <button onClick={importRules} disabled={!importText.trim()} className="flex flex-1 items-center justify-center gap-2 rounded border border-seal-border px-2 py-1.5 text-xs hover:border-blue-400 disabled:text-stone-400">
                  <Upload className="w-3.5 h-3.5" />
                  Import
                </button>
              </div>
              <textarea
                value={importText}
                onChange={e => setImportText(e.target.value)}
                rows={7}
                className="w-full resize-none rounded border border-seal-border bg-white px-2 py-2 font-mono text-[10px] outline-none"
              />
            </section>
          </aside>
        </div>
      </div>
    </div>
  )
}

async function readFailure(r: Response): Promise<string> {
  try {
    const d = await r.json()
    return d.detail || d.message || `HTTP ${r.status}`
  } catch {
    return `HTTP ${r.status}`
  }
}
