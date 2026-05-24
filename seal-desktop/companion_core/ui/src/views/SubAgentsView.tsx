import { useCallback, useEffect, useState } from 'react'
import { API } from '../App'
import {
  Brain,
  Send,
  Sparkles,
  ShieldQuestion,
  FlaskConical,
  Code2,
  ListChecks,
  RefreshCw,
  Database,
  Monitor,
  Gauge,
  LockKeyhole,
  PlugZap,
  Mic,
  TrendingUp,
  FileText,
  TestTubeDiagonal,
  PackageCheck,
  Wrench,
  Globe,
  Archive,
  Scissors,
  Hammer,
  Filter as FilterIcon,
  Zap,
  Sunrise,
  HelpCircle,
  Handshake,
} from 'lucide-react'

interface SubAgent {
  name: string
  role: string
  specialty: string
  triggers: string[]
}

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  orchestrator: Brain,
  planner: ListChecks,
  researcher: FlaskConical,
  critic: ShieldQuestion,
  code_executor: Code2,
  memory_curator: Database,
  screen_analyst: Monitor,
  token_optimizer: Gauge,
  privacy_guard: LockKeyhole,
  connector_operator: PlugZap,
  voice_companion: Mic,
  product_strategist: TrendingUp,
  documentation_writer: FileText,
  test_runner: TestTubeDiagonal,
  release_manager: PackageCheck,
  // 10 canonical OpenHuman additions (JARVIS)
  tools_agent: Wrench,
  integrations_agent: Globe,
  archivist: Archive,
  summarizer: Scissors,
  tool_maker: Hammer,
  trigger_triage: FilterIcon,
  trigger_reactor: Zap,
  morning_briefing: Sunrise,
  help: HelpCircle,
  welcome: Handshake,
}

const COLORS: Record<string, string> = {
  orchestrator: 'text-violet-500 border-violet-400',
  planner:      'text-sky-500 border-sky-400',
  researcher:   'text-emerald-500 border-emerald-400',
  critic:       'text-amber-500 border-amber-400',
  code_executor:'text-pink-500 border-pink-400',
  memory_curator: 'text-indigo-500 border-indigo-400',
  screen_analyst: 'text-cyan-500 border-cyan-400',
  token_optimizer: 'text-lime-600 border-lime-500',
  privacy_guard: 'text-red-500 border-red-400',
  connector_operator: 'text-blue-500 border-blue-400',
  voice_companion: 'text-fuchsia-500 border-fuchsia-400',
  product_strategist: 'text-orange-500 border-orange-400',
  documentation_writer: 'text-stone-600 border-stone-400',
  test_runner: 'text-teal-600 border-teal-500',
  release_manager: 'text-green-600 border-green-500',
  // 10 canonical OpenHuman
  tools_agent: 'text-yellow-600 border-yellow-500',
  integrations_agent: 'text-sky-700 border-sky-600',
  archivist: 'text-amber-700 border-amber-600',
  summarizer: 'text-rose-500 border-rose-400',
  tool_maker: 'text-orange-600 border-orange-500',
  trigger_triage: 'text-purple-500 border-purple-400',
  trigger_reactor: 'text-fuchsia-600 border-fuchsia-500',
  morning_briefing: 'text-yellow-500 border-yellow-400',
  help: 'text-blue-400 border-blue-300',
  welcome: 'text-emerald-400 border-emerald-300',
}

const PLACEHOLDERS: Record<string, string> = {
  orchestrator: '¿Qué hago primero hoy?',
  planner: 'Planeá cómo migrar 200 emails',
  researcher: '¿Cuáles son las APIs públicas de Gmail?',
  critic: 'Revisa este plan antes de enviarlo',
  code_executor: 'Escribí un script que cuente líneas de un archivo',
  memory_curator: 'Ordená estas notas para guardarlas como memoria',
  screen_analyst: 'Analizá esta captura y decime el bloqueo visible',
  token_optimizer: 'Compactá este contexto sin perder IDs ni comandos',
  privacy_guard: 'Revisa si este cambio filtra secretos o rompe privacidad',
  connector_operator: 'Diagnostica por qué GitHub no conecta',
  voice_companion: 'Convertí esta respuesta en versión hablada breve',
  product_strategist: 'Compará tres opciones de pricing para SEAL App',
  documentation_writer: 'Redactá un changelog con evidencia de pruebas',
  test_runner: 'Diseñá el smoke test mínimo para este cambio',
  release_manager: 'Prepará build deb, install y healthcheck de release',
  // 10 canonical OpenHuman
  tools_agent: 'Ejecutá esta tarea usando shell + HTTP + memoria',
  integrations_agent: 'Pedí a Gmail los últimos 5 emails de "factura"',
  archivist: 'Preservá lo importante de esta sesión',
  summarizer: 'Comprimí este output gigante de tool en 3 líneas',
  tool_maker: 'Escribí un polyfill para "convert heic to jpg"',
  trigger_triage: 'Clasificá este webhook que llegó recién',
  trigger_reactor: 'Guardá nota y avisame mañana sobre esto',
  morning_briefing: 'Dame un resumen accionable de hoy',
  help: '¿Cómo activo el watchdog del sidecar?',
  welcome: 'Ayudame a configurar mi SEAL por primera vez',
}

interface InvokeResult { agent: string; role: string; reply: string; suggested_route: string }

export default function SubAgentsView() {
  const [agents, setAgents] = useState<SubAgent[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<string>('orchestrator')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<InvokeResult | null>(null)
  const [invoking, setInvoking] = useState(false)
  const [hint, setHint] = useState<string>('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/sub-agents`)
      const d = await r.json()
      setAgents(d.agents || [])
    } catch {
      setAgents([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // Live route suggestion as user types
  useEffect(() => {
    const q = query.trim()
    if (!q) { setHint(''); return }
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/api/sub-agents/route?query=${encodeURIComponent(q)}`)
        const d = await r.json()
        if (d?.agent && d.agent !== selected) setHint(d.agent)
        else setHint('')
      } catch { setHint('') }
    }, 300)
    return () => clearTimeout(t)
  }, [query, selected])

  const invoke = async () => {
    if (!query.trim() || invoking) return
    setInvoking(true)
    setResult(null)
    try {
      const r = await fetch(`${API}/api/sub-agents/invoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: selected, query: query.trim() }),
      })
      const d = await r.json()
      setResult({
        agent: d.agent || selected,
        role: d.role || '',
        reply: d.reply || '(sin respuesta)',
        suggested_route: d.suggested_route || selected,
      })
    } catch (e) {
      setResult({ agent: selected, role: '', reply: `Error: ${e instanceof Error ? e.message : 'unknown'}`, suggested_route: selected })
    } finally {
      setInvoking(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-violet-500" />
            <h1 className="text-xl font-semibold text-slate-800">Sub-agentes</h1>
            <span className="text-xs text-seal-muted">{agents.length || 25} especialistas - equipo interno de SEAL</span>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded border border-seal-border hover:border-violet-400 text-seal-muted hover:text-violet-500"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Catalogo cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-5">
          {agents.map(a => {
            const Icon = ICONS[a.name] || Brain
            const tone = COLORS[a.name] || 'text-slate-500 border-slate-300'
            const isSel = selected === a.name
            return (
              <button
                key={a.name}
                onClick={() => setSelected(a.name)}
                className={`text-left rounded-xl border-2 bg-seal-surface p-3 transition shadow-sm hover:shadow ${isSel ? `${tone} ring-2 ring-offset-1 ring-violet-200` : 'border-seal-border hover:border-stone-300'}`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Icon className={`w-4 h-4 ${tone.split(' ')[0]}`} />
                  <span className="text-sm font-semibold text-slate-800 capitalize">{a.name.split('_').join(' ')}</span>
                </div>
                <p className="text-[11px] text-seal-muted leading-tight">{a.role}</p>
                <p className="text-[12px] text-slate-600 mt-1.5 leading-snug">{a.specialty}</p>
                {a.triggers?.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {a.triggers.slice(0, 4).map(t => (
                      <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-stone-100 text-slate-500">{t}</span>
                    ))}
                  </div>
                )}
              </button>
            )
          })}
        </div>

        {/* Invocador */}
        <div className="rounded-xl border border-seal-border bg-seal-surface p-4 shadow-sm">
          <div className="text-xs uppercase tracking-widest text-seal-muted mb-2">Pedile algo a <span className="text-violet-600 font-medium">{selected.split('_').join(' ')}</span></div>
          <textarea
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={`Ej.: ${PLACEHOLDERS[selected] || 'Ayudame con esta tarea'}`}
            rows={3}
            className="w-full bg-white border border-seal-border rounded-lg p-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-violet-200"
          />
          {hint && (
            <p className="text-[11px] text-amber-600 mt-1.5">
              Tip: parece más una tarea para <button onClick={() => setSelected(hint)} className="underline font-medium">{hint.split('_').join(' ')}</button>.
            </p>
          )}
          <div className="flex items-center justify-end mt-2">
            <button
              onClick={invoke}
              disabled={!query.trim() || invoking}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Send className="w-3.5 h-3.5" />
              {invoking ? 'Pensando…' : 'Invocar'}
            </button>
          </div>
        </div>

        {/* Resultado */}
        {result && (
          <div className="mt-4 rounded-xl border border-violet-200 bg-violet-50 p-4">
            <div className="flex items-center gap-2 text-xs text-violet-700 mb-2 uppercase tracking-widest">
              <Sparkles className="w-3.5 h-3.5" />
              <span>{result.agent}</span>
              {result.role && <span className="text-violet-500/70 normal-case">· {result.role}</span>}
            </div>
            <pre className="whitespace-pre-wrap text-sm text-slate-800 leading-relaxed font-sans">{result.reply}</pre>
          </div>
        )}

        <div className="mt-6 text-[11px] text-seal-muted text-center pb-4">
          Cada sub-agente recibe tu OCEAN para adaptar el tono. Mirror del sistema 15-agentes de OpenHuman para SEAL App.
        </div>
      </div>
    </div>
  )
}
