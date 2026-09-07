import { useCallback, useEffect, useState } from 'react'
import { API } from '../App'
import { CalendarClock, Plus, Trash2, Power, RefreshCw, Clock, X } from 'lucide-react'

interface CronJob {
  id: number
  name: string
  agent?: string | null
  cron_expression: string
  handler: string
  enabled: number | boolean
  last_run_at?: string | null
  next_run_at?: string | null
  created_by?: string | null
  created_at?: string | null
}

const PRESETS: Array<{ label: string; expr: string; hint: string }> = [
  { label: 'Cada hora',        expr: '0 * * * *',   hint: 'Al minuto 0 de cada hora' },
  { label: 'Diario 7am Lima',  expr: '0 12 * * *',  hint: '12:00 UTC = 7:00 am Lima' },
  { label: 'Diario 11pm Lima', expr: '0 4 * * *',   hint: '04:00 UTC = 23:00 Lima' },
  { label: 'Lun-Vie 9am Lima', expr: '0 14 * * 1-5',hint: 'Días hábiles 9am Lima' },
  { label: 'Cada 15 min',      expr: '*/15 * * * *',hint: 'Polling rápido' },
  { label: 'Semanal Lun 9am',  expr: '0 14 * * 1',  hint: 'Inicio de semana' },
]

const HANDLERS_HINT = ['dream_cycle.morning', 'dream_cycle.evening', 'memory_tree.rebuild', 'briefing.daily', 'custom']

export default function CronJobsView() {
  const [jobs, setJobs] = useState<CronJob[]>([])
  const [loading, setLoading] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy] = useState<number | null>(null)

  // Form state
  const [fName, setFName] = useState('')
  const [fExpr, setFExpr] = useState('0 * * * *')
  const [fHandler, setFHandler] = useState('')
  const [fAgent, setFAgent] = useState('')
  const [fEnabled, setFEnabled] = useState(true)
  const [submitErr, setSubmitErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/cron-jobs`)
      const d = await r.json()
      setJobs(d.jobs || [])
    } catch {
      setJobs([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const reset = () => {
    setFName(''); setFExpr('0 * * * *'); setFHandler(''); setFAgent(''); setFEnabled(true); setSubmitErr(null)
  }

  const submit = async () => {
    if (!fName.trim() || !fExpr.trim() || !fHandler.trim()) {
      setSubmitErr('nombre, expresión y handler son obligatorios')
      return
    }
    try {
      const r = await fetch(`${API}/api/cron-jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: fName.trim(),
          cron_expression: fExpr.trim(),
          handler: fHandler.trim(),
          agent: fAgent.trim() || null,
          enabled: fEnabled,
        }),
      })
      const d = await r.json()
      if (!r.ok || d.ok === false) {
        setSubmitErr(d.detail || d.error || 'no se pudo crear')
        return
      }
      reset()
      setShowForm(false)
      void load()
    } catch (e) {
      setSubmitErr(e instanceof Error ? e.message : 'fallo de red')
    }
  }

  const toggle = async (id: number) => {
    setBusy(id)
    try {
      await fetch(`${API}/api/cron-jobs/${id}/toggle`, { method: 'POST' })
      void load()
    } finally { setBusy(null) }
  }

  const remove = async (id: number) => {
    if (!confirm('¿Eliminar este job programado?')) return
    setBusy(id)
    try {
      await fetch(`${API}/api/cron-jobs/${id}`, { method: 'DELETE' })
      void load()
    } finally { setBusy(null) }
  }

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <CalendarClock className="w-5 h-5 text-indigo-500" />
            <h1 className="text-xl font-semibold text-slate-800">Programar</h1>
            <span className="text-xs text-seal-muted">jobs automáticos en tu SEAL</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              disabled={loading}
              className="p-1.5 rounded border border-seal-border hover:border-indigo-400 text-seal-muted hover:text-indigo-500"
              title="Actualizar"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => { reset(); setShowForm(true) }}
              className="flex items-center gap-1 px-3 py-1.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-xs"
            >
              <Plus className="w-3.5 h-3.5" /> Nuevo
            </button>
          </div>
        </div>

        {!loading && jobs.length === 0 && !showForm && (
          <div className="rounded-xl border border-dashed border-seal-border bg-seal-surface p-6 text-center text-sm text-seal-muted">
            Aún no tenés jobs programados. Crea uno para que SEAL haga cosas por vos en horario fijo.
          </div>
        )}

        {/* Form */}
        {showForm && (
          <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50 p-4 shadow">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-indigo-800">Nuevo job programado</h3>
              <button onClick={() => { setShowForm(false); reset() }} className="text-indigo-500 hover:text-indigo-700">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3 text-sm">
              <div>
                <label className="block text-[11px] uppercase tracking-widest text-indigo-700 mb-1">Nombre</label>
                <input
                  value={fName}
                  onChange={e => setFName(e.target.value)}
                  placeholder="Ej: Resumen diario de mi día"
                  className="w-full bg-white border border-indigo-200 rounded px-2 py-1.5 text-slate-800"
                />
              </div>

              <div>
                <label className="block text-[11px] uppercase tracking-widest text-indigo-700 mb-1">Cuándo</label>
                <div className="flex flex-wrap gap-1 mb-2">
                  {PRESETS.map(p => (
                    <button
                      key={p.expr}
                      onClick={() => setFExpr(p.expr)}
                      className={`text-[11px] px-2 py-1 rounded border ${fExpr === p.expr ? 'border-indigo-500 bg-indigo-100 text-indigo-700' : 'border-indigo-200 bg-white text-slate-600 hover:border-indigo-300'}`}
                      title={p.hint}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
                <input
                  value={fExpr}
                  onChange={e => setFExpr(e.target.value)}
                  placeholder="0 * * * *"
                  className="w-full bg-white border border-indigo-200 rounded px-2 py-1.5 text-slate-800 font-mono text-xs"
                />
                <p className="text-[10px] text-indigo-600 mt-1">Formato cron (min hora día mes diasem). Lima = UTC-5.</p>
              </div>

              <div>
                <label className="block text-[11px] uppercase tracking-widest text-indigo-700 mb-1">Qué ejecuta (handler)</label>
                <input
                  value={fHandler}
                  onChange={e => setFHandler(e.target.value)}
                  placeholder="dream_cycle.morning"
                  list="cron-handlers"
                  className="w-full bg-white border border-indigo-200 rounded px-2 py-1.5 text-slate-800 font-mono text-xs"
                />
                <datalist id="cron-handlers">
                  {HANDLERS_HINT.map(h => <option key={h} value={h} />)}
                </datalist>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[11px] uppercase tracking-widest text-indigo-700 mb-1">Agente (opcional)</label>
                  <input
                    value={fAgent}
                    onChange={e => setFAgent(e.target.value)}
                    placeholder="SEAL"
                    className="w-full bg-white border border-indigo-200 rounded px-2 py-1.5 text-slate-800"
                  />
                </div>
                <div className="flex items-end">
                  <label className="flex items-center gap-2 text-xs text-indigo-800">
                    <input type="checkbox" checked={fEnabled} onChange={e => setFEnabled(e.target.checked)} />
                    Empezar activo
                  </label>
                </div>
              </div>

              {submitErr && <p className="text-xs text-red-600">⚠️ {submitErr}</p>}

              <div className="flex justify-end gap-2 pt-1">
                <button onClick={() => { setShowForm(false); reset() }} className="px-3 py-1.5 text-xs rounded border border-indigo-200 text-slate-600 hover:bg-white">
                  Cancelar
                </button>
                <button onClick={submit} className="px-3 py-1.5 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white">
                  Crear job
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Jobs list */}
        <div className="space-y-2">
          {jobs.map(j => {
            const enabled = j.enabled === 1 || j.enabled === true
            return (
              <div key={j.id} className={`rounded-lg border p-3 ${enabled ? 'border-seal-border bg-seal-surface' : 'border-stone-200 bg-stone-50 opacity-70'}`}>
                <div className="flex items-start gap-2">
                  <Clock className={`w-4 h-4 shrink-0 mt-0.5 ${enabled ? 'text-indigo-500' : 'text-stone-400'}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-slate-800">{j.name}</span>
                      {j.agent && <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">{j.agent}</span>}
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-stone-200 text-stone-600'}`}>
                        {enabled ? 'Activo' : 'Pausado'}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-seal-muted">
                      <span className="font-mono text-slate-600">{j.cron_expression}</span>
                      <span>·</span>
                      <span className="font-mono text-slate-500">{j.handler}</span>
                      {j.next_run_at && <><span>·</span><span>próx: {j.next_run_at}</span></>}
                      {j.last_run_at && <><span>·</span><span>últ: {j.last_run_at}</span></>}
                    </div>
                  </div>
                  <button
                    onClick={() => toggle(j.id)}
                    disabled={busy === j.id}
                    className={`p-1.5 rounded hover:bg-stone-100 ${enabled ? 'text-emerald-600' : 'text-stone-400'}`}
                    title={enabled ? 'Pausar' : 'Activar'}
                  >
                    <Power className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => remove(j.id)}
                    disabled={busy === j.id}
                    className="p-1.5 rounded hover:bg-red-50 text-red-500"
                    title="Eliminar"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>

        <div className="mt-6 text-[11px] text-seal-muted text-center pb-4">
          Mirror del módulo cron de OpenHuman. Los jobs corren en companion_core; los handlers se registran del lado del backend.
        </div>
      </div>
    </div>
  )
}
