import { useState } from 'react'
import { Sparkles, ArrowRight, ArrowLeft, Check, Lock, Cloud } from 'lucide-react'
import { API } from '../App'
import { SoulMascot } from './SoulMascot'

const OCEAN_PRESETS = [
  { id: 'balanced',   label: 'Equilibrado/a',  emoji: '⚖️', desc: 'Tono neutral, decisiones medidas' },
  { id: 'warm',       label: 'Cálido/a',       emoji: '💜', desc: 'Cercanía afectiva, escucha activa' },
  { id: 'sharp',      label: 'Analítico/a',    emoji: '🔬', desc: 'Precisión, datos, lógica' },
  { id: 'creative',   label: 'Creativo/a',     emoji: '🎨', desc: 'Lluvia de ideas, asociaciones' },
  { id: 'pareja',     label: 'Pareja',         emoji: '💕', desc: 'Compañía cariñosa diaria. No reemplaza a una persona — acompaña.' },
] as const

interface Props {
  onFinish: (name: string) => void
}

/**
 * FirstRunWizard — minimal 4-step onboarding for SEAL App.
 *
 * Mirrors OpenHuman docs 01-02 (Choose Core + Sign In) but SEAL-flavored:
 * Local-default messaging, OCEAN preset selection, agent naming, privacy
 * promise. Calls POST /api/companion/first-run on finish.
 */
export function FirstRunWizard({ onFinish }: Props) {
  const [step, setStep] = useState(0)
  const [name, setName] = useState('')
  const [agentName, setAgentName] = useState('SEAL')
  const [preset, setPreset] = useState<typeof OCEAN_PRESETS[number]['id']>('balanced')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const finish = async () => {
    if (!name.trim()) {
      setErr('Tu nombre es necesario.')
      setStep(1)
      return
    }
    setLoading(true)
    setErr(null)
    try {
      const r = await fetch(`${API}/api/companion/first-run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          primary_agent: agentName.trim() || 'SEAL',
          ocean: { preset },
        }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      onFinish(name.trim())
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-gradient-to-br from-violet-950/80 to-stone-950/90 backdrop-blur p-4">
      <div className="w-full max-w-2xl bg-white rounded-2xl shadow-2xl overflow-hidden border border-stone-200">
        {/* Header */}
        <div className="px-6 py-4 border-b border-stone-200 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-violet-500" />
            <span className="text-sm font-mono uppercase tracking-widest text-violet-600">SEAL App</span>
          </div>
          <div className="flex gap-1.5">
            {[0, 1, 2, 3].map(i => (
              <div
                key={i}
                className={`w-2 h-2 rounded-full ${i === step ? 'bg-violet-500' : i < step ? 'bg-violet-300' : 'bg-stone-200'}`}
              />
            ))}
          </div>
        </div>

        {/* Step content */}
        <div className="p-8 min-h-[360px]">
          {step === 0 && <StepWelcome onNext={() => setStep(1)} />}
          {step === 1 && <StepName name={name} setName={setName} err={err} onBack={() => setStep(0)} onNext={() => { setErr(null); setStep(2) }} />}
          {step === 2 && <StepPersonality preset={preset} setPreset={setPreset} agentName={agentName} setAgentName={setAgentName} onBack={() => setStep(1)} onNext={() => setStep(3)} />}
          {step === 3 && <StepConfirm name={name} agentName={agentName} preset={preset} loading={loading} err={err} onBack={() => setStep(2)} onFinish={finish} />}
        </div>
      </div>
    </div>
  )
}

function StepWelcome({ onNext }: { onNext: () => void }) {
  return (
    <div className="flex flex-col items-center text-center gap-5">
      <SoulMascot state="happy" size={170} />
      <h1 className="text-3xl font-light text-slate-800">¡Hola!</h1>
      <p className="text-slate-600 max-w-md leading-relaxed">
        Soy SEAL App — tu compañera personal de IA.
        Corro <strong>local</strong> en tu computadora con GEMMA 4. Tus mensajes, memorias y datos
        no salen de tu equipo a menos que vos lo decidas.
      </p>
      <div className="flex items-center gap-3 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1"><Lock className="w-3 h-3 text-emerald-500" /> Local-first</span>
        <span className="inline-flex items-center gap-1"><Cloud className="w-3 h-3 text-amber-500" /> Cloud opt-in</span>
      </div>
      <button
        onClick={onNext}
        className="mt-2 px-6 py-3 rounded-xl bg-violet-500 hover:bg-violet-600 text-white font-medium flex items-center gap-2 shadow-md"
      >
        Empezar <ArrowRight className="w-4 h-4" />
      </button>
    </div>
  )
}

function StepName({ name, setName, err, onBack, onNext }: { name: string; setName: (n: string) => void; err: string | null; onBack: () => void; onNext: () => void }) {
  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-2xl font-light text-slate-800">¿Cómo te llamás?</h2>
      <p className="text-sm text-slate-500">Voy a usar tu nombre para personalizar nuestras conversaciones.</p>
      <input
        type="text"
        value={name}
        onChange={e => setName(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && name.trim() && onNext()}
        placeholder="Tu nombre"
        autoFocus
        className="w-full px-4 py-3 text-lg font-medium text-slate-900 placeholder:text-slate-400 bg-white border border-stone-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300"
      />
      {err && <p className="text-xs text-red-500">{err}</p>}
      <div className="flex justify-between mt-6">
        <button onClick={onBack} className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700">
          <ArrowLeft className="w-4 h-4" /> Atrás
        </button>
        <button
          onClick={onNext}
          disabled={!name.trim()}
          className="px-5 py-2 rounded-lg bg-violet-500 hover:bg-violet-600 text-white text-sm font-medium flex items-center gap-1 disabled:opacity-50"
        >
          Siguiente <ArrowRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

function StepPersonality({ preset, setPreset, agentName, setAgentName, onBack, onNext }: { preset: string; setPreset: (p: typeof OCEAN_PRESETS[number]['id']) => void; agentName: string; setAgentName: (n: string) => void; onBack: () => void; onNext: () => void }) {
  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-2xl font-light text-slate-800">Diseñá tu SEAL</h2>
      <p className="text-sm text-slate-500">Elegí cómo te quiere acompañar tu IA. Esto define su personalidad inicial — podés ajustarla después.</p>

      <div>
        <label className="text-xs uppercase tracking-widest text-slate-500 mb-1 block">Nombre de tu IA</label>
        <input
          type="text"
          value={agentName}
          onChange={e => setAgentName(e.target.value)}
          placeholder="SEAL"
          className="w-full px-3 py-2 font-medium text-slate-900 placeholder:text-slate-400 bg-white border border-stone-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-violet-300"
        />
      </div>

      <div>
        <label className="text-xs uppercase tracking-widest text-slate-500 mb-2 block">Personalidad</label>
        <div className="grid grid-cols-2 gap-2">
          {OCEAN_PRESETS.map(p => (
            <button
              key={p.id}
              onClick={() => setPreset(p.id)}
              className={`text-left p-3 rounded-xl border transition ${preset === p.id ? 'bg-violet-50 border-violet-400 ring-2 ring-violet-200' : 'bg-white border-stone-200 hover:border-stone-300'}`}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xl">{p.emoji}</span>
                <span className="text-sm font-medium text-slate-800">{p.label}</span>
              </div>
              <p className="text-xs text-slate-500">{p.desc}</p>
            </button>
          ))}
        </div>
      </div>

      <div className="flex justify-between mt-4">
        <button onClick={onBack} className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700">
          <ArrowLeft className="w-4 h-4" /> Atrás
        </button>
        <button onClick={onNext} className="px-5 py-2 rounded-lg bg-violet-500 hover:bg-violet-600 text-white text-sm font-medium flex items-center gap-1">
          Siguiente <ArrowRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

function StepConfirm({ name, agentName, preset, loading, err, onBack, onFinish }: { name: string; agentName: string; preset: string; loading: boolean; err: string | null; onBack: () => void; onFinish: () => void }) {
  const presetMeta = OCEAN_PRESETS.find(p => p.id === preset)
  return (
    <div className="flex flex-col items-center text-center gap-5">
      <SoulMascot state="listening" size={140} />
      <h2 className="text-2xl font-light text-slate-800">Listo para empezar</h2>
      <div className="space-y-2 text-sm text-slate-600 bg-stone-50 px-5 py-4 rounded-xl border border-stone-200 w-full max-w-md text-left">
        <p><span className="text-slate-500">Tu nombre:</span> <strong className="text-slate-800">{name || '(sin nombre)'}</strong></p>
        <p><span className="text-slate-500">Tu IA se llama:</span> <strong className="text-slate-800">{agentName}</strong></p>
        <p><span className="text-slate-500">Personalidad:</span> <strong className="text-slate-800">{presetMeta?.emoji} {presetMeta?.label}</strong></p>
      </div>
      <p className="text-xs text-slate-500 max-w-md">
        Tu SEAL guarda todo en este equipo. Puedes cambiar estas decisiones desde Ajustes en cualquier momento.
      </p>
      {err && <p className="text-xs text-red-500">{err}</p>}
      <div className="flex justify-between w-full max-w-md">
        <button onClick={onBack} disabled={loading} className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700 disabled:opacity-50">
          <ArrowLeft className="w-4 h-4" /> Atrás
        </button>
        <button
          onClick={onFinish}
          disabled={loading}
          className="px-5 py-2 rounded-lg bg-violet-500 hover:bg-violet-600 text-white text-sm font-medium flex items-center gap-1 disabled:opacity-50"
        >
          {loading ? 'Guardando…' : (<>Empezar <Check className="w-4 h-4" /></>)}
        </button>
      </div>
    </div>
  )
}

export default FirstRunWizard
