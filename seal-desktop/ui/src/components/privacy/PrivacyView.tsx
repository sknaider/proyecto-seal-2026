import { useState } from 'react'
import { Shield, Download, FileDown, Info, Lock, Cloud, Server, KeyRound, BarChart3, AlertTriangle } from 'lucide-react'

type Classification = 'RAW_USER_CONTENT' | 'DERIVED_SIGNALS' | 'CREDENTIALS' | 'METADATA' | 'DIAGNOSTICS'
type Egress = 'stays_local' | 'leaves_device'

interface Operation {
  id: string
  category: 'chat' | 'ingestion' | 'cloud' | 'telemetry' | 'meet' | 'integrations'
  label: string
  description: string
  classification: Classification
  egress: Egress
  destination: string | null
  why_needed: string
  impact_if_off: string
  enabled: boolean
  toggleable: boolean
  last_used: string | null
}

const CLASSIFICATION_META: Record<Classification, { label: string; tone: string }> = {
  RAW_USER_CONTENT:  { label: 'Raw user content', tone: 'bg-violet-900/40 text-violet-300 border-violet-700/60' },
  DERIVED_SIGNALS:   { label: 'Derived signals',  tone: 'bg-amber-900/40 text-amber-300 border-amber-700/60' },
  CREDENTIALS:       { label: 'Credentials',      tone: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/60' },
  METADATA:          { label: 'Metadata',         tone: 'bg-sky-900/40 text-sky-300 border-sky-700/60' },
  DIAGNOSTICS:       { label: 'Diagnostics',      tone: 'bg-stone-800 text-stone-300 border-stone-600/60' },
}

const CATEGORY_META: Record<Operation['category'], { label: string; icon: typeof Shield }> = {
  chat:         { label: 'Operaciones de chat',         icon: BarChart3 },
  ingestion:    { label: 'Ingestión de canales',        icon: Download },
  cloud:        { label: 'Operaciones cloud (opt-in)',  icon: Cloud },
  telemetry:    { label: 'Telemetría',                  icon: BarChart3 },
  meet:         { label: 'Llamadas / Meet',             icon: Server },
  integrations: { label: 'Integraciones externas',      icon: KeyRound },
}

// Seed inicial — el backend `/api/privacy/operations` reemplazará esto cuando esté listo.
// Mantener nombres en línea con docs `/agents/ALICE/docs/openhuman_replication/v2/31_privacy_security_detailed.md`.
const INITIAL_OPERATIONS: Operation[] = [
  {
    id: 'generate_response',
    category: 'chat',
    label: 'Generar respuestas (chat)',
    description: 'Tu mensaje + memorias se procesan en GEMMA 4 local.',
    classification: 'RAW_USER_CONTENT',
    egress: 'stays_local',
    destination: null,
    why_needed: 'Sin esto tu SOUL no puede responderte.',
    impact_if_off: 'El chat queda inhabilitado.',
    enabled: true,
    toggleable: false,
    last_used: null,
  },
  {
    id: 'memory_tree_retrieval',
    category: 'chat',
    label: 'Búsqueda en el árbol de memoria',
    description: 'Recupera memorias h→d→m→y para dar contexto al chat.',
    classification: 'RAW_USER_CONTENT',
    egress: 'stays_local',
    destination: null,
    why_needed: 'Permite responder con contexto histórico.',
    impact_if_off: 'Tu SOUL responde sin recordarte.',
    enabled: true,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'generate_embeddings',
    category: 'chat',
    label: 'Generar embeddings locales',
    description: 'Vectoriza tus textos con BGE-M3 local para búsqueda semántica.',
    classification: 'RAW_USER_CONTENT',
    egress: 'stays_local',
    destination: null,
    why_needed: 'Habilita búsqueda semántica sobre tu memoria.',
    impact_if_off: 'Sólo búsqueda textual exacta.',
    enabled: true,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'prompt_injection_guard',
    category: 'chat',
    label: 'Defensa contra prompt injection',
    description: 'Inspecciona prompts antes de pasarlos al LLM.',
    classification: 'DERIVED_SIGNALS',
    egress: 'stays_local',
    destination: null,
    why_needed: 'Bloquea intentos de hijack del agente.',
    impact_if_off: 'Riesgo de prompt injection — no recomendado apagar.',
    enabled: true,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'ingest_gmail',
    category: 'ingestion',
    label: 'Leer Gmail conectado',
    description: 'Sincroniza correos vía API oficial Google (OAuth tier 1).',
    classification: 'RAW_USER_CONTENT',
    egress: 'stays_local',
    destination: null,
    why_needed: 'Habilita resúmenes y búsqueda sobre tu correo.',
    impact_if_off: 'Tu SOUL no ve nuevos correos.',
    enabled: false,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'ingest_slack',
    category: 'ingestion',
    label: 'Leer Slack conectado',
    description: 'Sincroniza mensajes Slack vía OAuth oficial.',
    classification: 'RAW_USER_CONTENT',
    egress: 'stays_local',
    destination: null,
    why_needed: 'Resumen y respuesta sugerida sobre tu Slack.',
    impact_if_off: 'Tu SOUL ignora Slack.',
    enabled: false,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'screen_intelligence_capture',
    category: 'ingestion',
    label: 'Awareness del escritorio (captura)',
    description: 'Captura screenshots por sesión limitada (denylist de apps sensibles).',
    classification: 'RAW_USER_CONTENT',
    egress: 'stays_local',
    destination: null,
    why_needed: 'Permite que SOUL te ayude con lo que estás haciendo en pantalla.',
    impact_if_off: 'SOUL no ve tu pantalla.',
    enabled: false,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'oauth_token_storage',
    category: 'integrations',
    label: 'Guardar tokens OAuth localmente',
    description: 'Credenciales de Gmail/Slack/etc cifradas en companion.toml.',
    classification: 'CREDENTIALS',
    egress: 'stays_local',
    destination: null,
    why_needed: 'Necesario para mantener conexiones activas.',
    impact_if_off: 'Tendrías que reconectar cada vez.',
    enabled: true,
    toggleable: false,
    last_used: null,
  },
  {
    id: 'wallet_credentials',
    category: 'integrations',
    label: 'Frase BIP39 para encryption local',
    description: 'Mnemónica cifra tu vault SQLite y portabilidad cross-device.',
    classification: 'CREDENTIALS',
    egress: 'stays_local',
    destination: null,
    why_needed: 'Si activás cifrado fuerte, esta frase es la llave.',
    impact_if_off: 'Sin cifrado fuerte del vault.',
    enabled: false,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'byok_anthropic',
    category: 'cloud',
    label: 'Llamadas a Anthropic (BYOK)',
    description: 'Tu mensaje viaja a api.anthropic.com con TU API key.',
    classification: 'RAW_USER_CONTENT',
    egress: 'leaves_device',
    destination: 'api.anthropic.com',
    why_needed: 'Reasoning profundo con Claude Opus/Sonnet.',
    impact_if_off: 'Sólo modelos locales para razonamiento.',
    enabled: false,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'byok_openai',
    category: 'cloud',
    label: 'Llamadas a OpenAI (BYOK)',
    description: 'Tu mensaje viaja a api.openai.com con TU API key.',
    classification: 'RAW_USER_CONTENT',
    egress: 'leaves_device',
    destination: 'api.openai.com',
    why_needed: 'Acceso a GPT-5 / GPT-4o.',
    impact_if_off: 'Sin OpenAI; quedan local + otros BYOK.',
    enabled: false,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'byok_openrouter',
    category: 'cloud',
    label: 'Llamadas a OpenRouter (BYOK)',
    description: 'Aggregator multi-provider. Tu data pasa por OpenRouter antes del modelo final.',
    classification: 'RAW_USER_CONTENT',
    egress: 'leaves_device',
    destination: 'openrouter.ai',
    why_needed: '50+ modelos con UN solo key.',
    impact_if_off: 'Sin OpenRouter.',
    enabled: false,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'meet_join_anonymous_guest',
    category: 'meet',
    label: 'Unirse a Google Meet como guest',
    description: 'SOUL entra en ventana separada y solicita admisión al host.',
    classification: 'METADATA',
    egress: 'leaves_device',
    destination: 'Google Meet',
    why_needed: 'Toma notas y resume la reunión por vos.',
    impact_if_off: 'Sin asistencia automática en meetings.',
    enabled: false,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'meet_live_listen_speak',
    category: 'meet',
    label: 'Escuchar/hablar dentro de Meet',
    description: 'STT/TTS dentro de la llamada. Default local (whisper.cpp); ElevenLabs sólo si lo activás.',
    classification: 'DERIVED_SIGNALS',
    egress: 'leaves_device',
    destination: 'Google Meet (+ ElevenLabs si está activado)',
    why_needed: 'Permite que SOUL participe verbalmente.',
    impact_if_off: 'SOUL escucha pero no habla.',
    enabled: false,
    toggleable: true,
    last_used: null,
  },
  {
    id: 'telemetry_anonymous',
    category: 'telemetry',
    label: 'Reportes de error anónimos',
    description: 'Sólo crash type + file location. Nunca tus mensajes, vault ni API keys.',
    classification: 'DIAGNOSTICS',
    egress: 'leaves_device',
    destination: 'telemetry.seal.local (opt-in)',
    why_needed: 'Nos ayuda a estabilizar SOUL más rápido.',
    impact_if_off: 'No tracking, no telemetry.',
    enabled: true,
    toggleable: true,
    last_used: null,
  },
]

export function PrivacyView() {
  const [operations, setOperations] = useState<Operation[]>(INITIAL_OPERATIONS)
  const [filter, setFilter] = useState<'all' | 'leaves' | 'stays'>('all')

  const filtered = operations.filter(op => {
    if (filter === 'leaves') return op.egress === 'leaves_device'
    if (filter === 'stays') return op.egress === 'stays_local'
    return true
  })

  const grouped = filtered.reduce<Record<Operation['category'], Operation[]>>((acc, op) => {
    if (!acc[op.category]) acc[op.category] = []
    acc[op.category].push(op)
    return acc
  }, {} as Record<Operation['category'], Operation[]>)

  const toggle = (id: string) => {
    setOperations(prev => prev.map(op => op.id === id && op.toggleable ? { ...op, enabled: !op.enabled } : op))
    // TODO(wire): POST /api/privacy/operations/:id/toggle
  }

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(operations, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `soul-privacy-disclosure-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const localCount = operations.filter(o => o.egress === 'stays_local').length
  const leavesCount = operations.filter(o => o.egress === 'leaves_device').length

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 text-gray-200">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-2">
          <Shield className="w-5 h-5 text-violet-400" />
          <h1 className="text-xl font-semibold">Privacidad y datos</h1>
        </div>
        <p className="text-sm text-gray-400">
          Acá ves TODO lo que tu SOUL hace, qué datos toca y qué sale de tu equipo. SOUL corre local por default.
        </p>
      </div>

      {/* Stats + actions */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="px-3 py-2 rounded-lg bg-emerald-900/30 border border-emerald-700/50 text-emerald-300 text-sm flex items-center gap-2">
          <Lock className="w-4 h-4" /> {localCount} operaciones se quedan locales
        </div>
        <div className="px-3 py-2 rounded-lg bg-amber-900/30 border border-amber-700/50 text-amber-300 text-sm flex items-center gap-2">
          <Cloud className="w-4 h-4" /> {leavesCount} operaciones salen del equipo (algunas opt-in)
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setFilter('all')}
            className={`px-3 py-1.5 text-xs rounded-md border ${filter === 'all' ? 'bg-violet-700/40 border-violet-500 text-violet-200' : 'bg-gray-800/60 border-gray-700 text-gray-400 hover:text-gray-200'}`}
          >Todo</button>
          <button
            onClick={() => setFilter('stays')}
            className={`px-3 py-1.5 text-xs rounded-md border ${filter === 'stays' ? 'bg-emerald-700/40 border-emerald-500 text-emerald-200' : 'bg-gray-800/60 border-gray-700 text-gray-400 hover:text-gray-200'}`}
          >Sólo local</button>
          <button
            onClick={() => setFilter('leaves')}
            className={`px-3 py-1.5 text-xs rounded-md border ${filter === 'leaves' ? 'bg-amber-700/40 border-amber-500 text-amber-200' : 'bg-gray-800/60 border-gray-700 text-gray-400 hover:text-gray-200'}`}
          >Sólo sale</button>
          <button
            onClick={exportJson}
            className="px-3 py-1.5 text-xs rounded-md border bg-gray-800/60 border-gray-700 text-gray-400 hover:text-gray-200 flex items-center gap-1.5"
          >
            <FileDown className="w-3.5 h-3.5" /> Export JSON
          </button>
        </div>
      </div>

      {/* Categorías */}
      <div className="space-y-6">
        {(Object.keys(grouped) as Operation['category'][]).map(cat => {
          const meta = CATEGORY_META[cat]
          const Icon = meta.icon
          return (
            <section key={cat}>
              <div className="flex items-center gap-2 mb-2 text-gray-400 text-xs uppercase tracking-widest">
                <Icon className="w-3.5 h-3.5" />
                {meta.label}
              </div>
              <div className="space-y-2">
                {grouped[cat].map(op => (
                  <OperationCard key={op.id} op={op} onToggle={() => toggle(op.id)} />
                ))}
              </div>
            </section>
          )
        })}
      </div>

      {/* Footer disclaimer honesto */}
      <div className="mt-8 p-4 rounded-lg bg-gray-900/60 border border-gray-800 text-xs text-gray-400 leading-relaxed">
        <div className="flex items-start gap-2">
          <Info className="w-4 h-4 mt-0.5 text-gray-500 shrink-0" />
          <div>
            <strong className="text-gray-300">Nuestra promesa:</strong> SOUL no vende tu información, no entrena modelos comerciales con tus mensajes y procesa por default en TU equipo.
            <br />
            <strong className="text-gray-300">Tu responsabilidad:</strong> al activar funciones que salen del equipo (BYOK cloud, Meet Agent, telemetría), aceptás los términos del destino correspondiente.
          </div>
        </div>
      </div>
    </div>
  )
}

function OperationCard({ op, onToggle }: { op: Operation; onToggle: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const classMeta = CLASSIFICATION_META[op.classification]
  const egressLeaves = op.egress === 'leaves_device'

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 overflow-hidden">
      <div className="flex items-start gap-3 p-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-gray-100">{op.label}</span>
            <span className={`px-1.5 py-0.5 text-[10px] rounded border ${classMeta.tone}`}>{classMeta.label}</span>
            {egressLeaves ? (
              <span className="px-1.5 py-0.5 text-[10px] rounded border bg-amber-900/30 text-amber-300 border-amber-700/60 flex items-center gap-1">
                <Cloud className="w-2.5 h-2.5" /> Sale del equipo
              </span>
            ) : (
              <span className="px-1.5 py-0.5 text-[10px] rounded border bg-emerald-900/30 text-emerald-300 border-emerald-700/60 flex items-center gap-1">
                <Lock className="w-2.5 h-2.5" /> Stays local
              </span>
            )}
            {!op.toggleable && (
              <span className="px-1.5 py-0.5 text-[10px] rounded border bg-gray-800 text-gray-400 border-gray-700">Requerido</span>
            )}
          </div>
          <p className="text-xs text-gray-400 mt-1">{op.description}</p>
          {egressLeaves && op.destination && (
            <p className="text-[11px] text-amber-200/80 mt-1 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" /> Va a: <span className="font-mono">{op.destination}</span>
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setExpanded(v => !v)}
            className="text-[11px] text-gray-500 hover:text-gray-300"
            title="Por qué / Qué pierdo"
          >
            {expanded ? 'menos' : 'detalle'}
          </button>
          <Toggle enabled={op.enabled} onClick={onToggle} disabled={!op.toggleable} />
        </div>
      </div>
      {expanded && (
        <div className="border-t border-gray-800 px-3 py-2 text-[11px] text-gray-400 space-y-1 bg-gray-900/30">
          <div><span className="text-gray-300">Por qué:</span> {op.why_needed}</div>
          <div><span className="text-gray-300">Si lo apagás:</span> {op.impact_if_off}</div>
          {op.last_used && <div><span className="text-gray-300">Último uso:</span> {op.last_used}</div>}
        </div>
      )}
    </div>
  )
}

function Toggle({ enabled, onClick, disabled }: { enabled: boolean; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      aria-pressed={enabled}
      className={`relative w-10 h-5 rounded-full transition-colors ${enabled ? 'bg-emerald-600' : 'bg-gray-700'} ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${enabled ? 'translate-x-5' : ''}`}
      />
    </button>
  )
}

export default PrivacyView
