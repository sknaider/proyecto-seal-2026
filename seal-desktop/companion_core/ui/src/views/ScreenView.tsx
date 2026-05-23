import { useCallback, useEffect, useState } from 'react'
import { API } from '../App'
import { Monitor, RefreshCw, Shield, Eye, AlertTriangle, Camera, Brain, Image as ImageIcon } from 'lucide-react'

interface ScreenStatus {
  ok: boolean
  capture_available: boolean
  analyzer_available: boolean
  recommended_model?: string | null
  permission_note?: string | null
  setup_hint?: string | null
}

interface ScreenCapture {
  image_id: string
  captured_at: string
  has_thumbnail: boolean
  size_bytes: number
}

interface ScreenHistory {
  ok: boolean
  captures: ScreenCapture[]
  count: number
  local_only?: boolean
}

export default function ScreenView() {
  const [status, setStatus] = useState<ScreenStatus | null>(null)
  const [captures, setCaptures] = useState<ScreenCapture[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<string>('')
  const [message, setMessage] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [captureBusy, setCaptureBusy] = useState(false)
  const [analyzeBusy, setAnalyzeBusy] = useState(false)

  const readFailure = async (r: Response) => {
    try {
      const d = await r.json()
      return d.detail || d.message || `HTTP ${r.status}`
    } catch {
      return `HTTP ${r.status}`
    }
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [statusRes, historyRes] = await Promise.all([
        fetch(`${API}/api/screen/status`),
        fetch(`${API}/api/screen/history?limit=12`),
      ])
      setStatus(await statusRes.json())
      if (historyRes.ok) {
        const history = await historyRes.json() as ScreenHistory
        setCaptures(history.captures || [])
        setSelectedId(current => current || history.captures?.[0]?.image_id || null)
      }
    } catch {
      setStatus(null)
      setCaptures([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const ready = status?.capture_available && status?.analyzer_available
  const canCapture = Boolean(status?.capture_available) && !captureBusy
  const canAnalyze = Boolean(status?.analyzer_available && selectedId) && !analyzeBusy

  const captureScreen = async () => {
    setCaptureBusy(true)
    setMessage('')
    setAnalysis('')
    try {
      const r = await fetch(`${API}/api/screen/capture`, { method: 'POST' })
      if (!r.ok) throw new Error(await readFailure(r))
      const d = await r.json()
      setSelectedId(d.image_id)
      setMessage('Captura guardada')
      await load()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'No se pudo capturar')
    } finally {
      setCaptureBusy(false)
    }
  }

  const analyzeCapture = async () => {
    if (!selectedId) return
    setAnalyzeBusy(true)
    setMessage('')
    setAnalysis('')
    try {
      const r = await fetch(`${API}/api/screen/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_id: selectedId,
          model: status?.recommended_model || 'gemma3:4b',
          prompt: 'Describe la pantalla en español. Resalta acciones pendientes y riesgos visibles.',
        }),
      })
      if (!r.ok) throw new Error(await readFailure(r))
      const d = await r.json()
      setAnalysis(d.description || '')
      setMessage('Analisis listo')
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'No se pudo analizar')
    } finally {
      setAnalyzeBusy(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-5xl mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Monitor className="w-5 h-5 text-blue-500" />
            <h1 className="text-xl font-semibold text-slate-800">Pantalla</h1>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded border border-seal-border hover:border-blue-400 text-seal-muted hover:text-blue-500"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        <section className="rounded-lg border border-seal-border bg-seal-surface p-4">
          <div className="flex items-start gap-3">
            <Shield className={`w-5 h-5 mt-0.5 ${ready ? 'text-emerald-600' : 'text-amber-500'}`} />
            <div className="flex-1">
              <h2 className="text-sm font-semibold text-slate-800">{ready ? 'Lista para revisar pantalla' : 'Falta preparar esta función'}</h2>
              <p className="text-xs text-seal-muted mt-1 leading-relaxed">
                {status?.permission_note || 'SEAL solo revisa la pantalla cuando tú lo pides.'}
              </p>
            </div>
          </div>
        </section>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <StatusCard title="Captura local" ok={status?.capture_available} />
          <StatusCard title="Análisis local" ok={status?.analyzer_available} />
        </div>

        <section className="rounded-lg border border-seal-border bg-seal-surface p-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <button
              onClick={captureScreen}
              disabled={!canCapture}
              className="flex min-h-10 items-center justify-center gap-2 rounded-lg bg-blue-500 px-4 py-2 text-sm text-white disabled:bg-stone-300 disabled:text-stone-500"
            >
              <Camera className="w-4 h-4" />
              {captureBusy ? 'Capturando...' : 'Capturar'}
            </button>
            <button
              onClick={analyzeCapture}
              disabled={!canAnalyze}
              className="flex min-h-10 items-center justify-center gap-2 rounded-lg bg-slate-800 px-4 py-2 text-sm text-white disabled:bg-stone-300 disabled:text-stone-500"
            >
              <Brain className="w-4 h-4" />
              {analyzeBusy ? 'Analizando...' : 'Analizar'}
            </button>
          </div>
          {message && (
            <p className={`text-xs leading-relaxed ${analysis ? 'text-emerald-700' : 'text-amber-700'}`}>
              {message}
            </p>
          )}
          {!ready && (
            <p className="mt-3 flex items-start gap-2 text-xs text-amber-700 leading-relaxed">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{status?.setup_hint || 'Todavía falta preparar captura o análisis local.'}</span>
            </p>
          )}
        </section>

        {analysis && (
          <section className="rounded-lg border border-seal-border bg-seal-surface p-4">
            <div className="flex items-center gap-2 mb-2">
              <Eye className="w-4 h-4 text-blue-500" />
              <h2 className="text-sm font-semibold text-slate-800">Resultado</h2>
            </div>
            <p className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">{analysis}</p>
          </section>
        )}

        <section className="space-y-2">
          <div className="flex items-center gap-2">
            <ImageIcon className="w-4 h-4 text-seal-muted" />
            <h2 className="text-sm font-semibold text-slate-800">Historial</h2>
          </div>
          {captures.length === 0 ? (
            <div className="rounded-lg border border-dashed border-seal-border bg-seal-surface p-4 text-sm text-seal-muted">
              Sin capturas locales.
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {captures.map(capture => (
                <button
                  key={capture.image_id}
                  onClick={() => setSelectedId(capture.image_id)}
                  className={`overflow-hidden rounded-lg border bg-seal-surface text-left transition ${
                    selectedId === capture.image_id ? 'border-blue-500 ring-2 ring-blue-100' : 'border-seal-border hover:border-blue-300'
                  }`}
                >
                  <div className="aspect-video bg-stone-100">
                    {capture.has_thumbnail ? (
                      <img
                        src={`${API}/api/screen/thumbnail/${capture.image_id}`}
                        alt=""
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center">
                        <ImageIcon className="w-6 h-6 text-seal-muted" />
                      </div>
                    )}
                  </div>
                  <div className="p-2">
                    <p className="truncate text-xs font-medium text-slate-700">{capture.image_id}</p>
                    <p className="text-[11px] text-seal-muted">{Math.round(capture.size_bytes / 1024)} KB</p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function StatusCard({ title, ok }: { title: string; ok?: boolean }) {
  return (
    <div className="rounded-lg border border-seal-border bg-seal-surface p-3">
      <p className="text-xs text-seal-muted">{title}</p>
      <p className={`mt-1 text-sm font-medium ${ok ? 'text-emerald-600' : 'text-amber-600'}`}>
        {ok ? 'Disponible' : 'No lista'}
      </p>
    </div>
  )
}
