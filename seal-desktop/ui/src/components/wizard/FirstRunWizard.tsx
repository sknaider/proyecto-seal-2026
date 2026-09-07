import { useState } from 'react'
import { Sparkles, User, Brain, CheckCircle, ChevronRight } from 'lucide-react'

const HOST = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
const SOUL = `http://${HOST}:8800`

const OCEAN_PRESETS = [
  {
    id: 'balanced',
    label: 'Balanced',
    desc: 'Thoughtful, curious, grounded — good all-around companion',
    icon: '⚖️',
  },
  {
    id: 'creative',
    label: 'Creative',
    desc: 'High openness, spontaneous, loves ideas and brainstorming',
    icon: '🎨',
  },
  {
    id: 'analytical',
    label: 'Analytical',
    desc: 'Precise, methodical, detail-oriented — great for technical work',
    icon: '🔬',
  },
  {
    id: 'empathetic',
    label: 'Empathetic',
    desc: 'Warm, agreeable, supportive — prioritises your wellbeing',
    icon: '💙',
  },
]

interface Props {
  onComplete: () => void
}

export function FirstRunWizard({ onComplete }: Props) {
  const [step, setStep] = useState(0)
  const [agentName, setAgentName] = useState('')
  const [preset, setPreset] = useState('balanced')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleFinish() {
    if (!agentName.trim()) { setError('Give your assistant a name'); return }
    setLoading(true)
    setError('')
    try {
      const userId = crypto.randomUUID()
      const r = await fetch(`${SOUL}/api/companion/first-run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_name: agentName.trim(), user_id: userId, ocean_preset: preset }),
      })
      if (!r.ok) throw new Error(`Server error ${r.status}`)
      onComplete()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Setup failed — check backend')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm">
      <div className="w-full max-w-lg mx-4 bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="bg-gradient-to-r from-violet-900/60 to-blue-900/60 px-8 py-6 border-b border-gray-700">
          <div className="flex items-center gap-3 mb-1">
            <Sparkles className="w-6 h-6 text-violet-400" />
            <span className="text-xs font-mono text-violet-400 uppercase tracking-widest">SOUL by SEAL</span>
          </div>
          <h1 className="text-2xl font-bold text-white">Welcome</h1>
          <p className="text-gray-400 text-sm mt-1">Let's set up your personal AI assistant</p>
        </div>

        {/* Steps */}
        <div className="px-8 pt-6 pb-8">
          {step === 0 && <StepWelcome onNext={() => setStep(1)} />}
          {step === 1 && (
            <StepName
              value={agentName}
              onChange={setAgentName}
              error={error}
              onNext={() => { if (!agentName.trim()) { setError('Required'); return } setError(''); setStep(2) }}
              onBack={() => setStep(0)}
            />
          )}
          {step === 2 && (
            <StepPersonality
              selected={preset}
              onSelect={setPreset}
              onNext={() => setStep(3)}
              onBack={() => setStep(1)}
            />
          )}
          {step === 3 && (
            <StepConfirm
              agentName={agentName}
              preset={preset}
              loading={loading}
              error={error}
              onBack={() => setStep(2)}
              onFinish={handleFinish}
            />
          )}
        </div>

        {/* Progress dots */}
        <div className="flex justify-center gap-2 pb-6">
          {[0, 1, 2, 3].map(i => (
            <div
              key={i}
              className={`w-2 h-2 rounded-full transition-colors ${i === step ? 'bg-violet-400' : i < step ? 'bg-violet-700' : 'bg-gray-700'}`}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function StepWelcome({ onNext }: { onNext: () => void }) {
  return (
    <div className="space-y-6">
      <div className="text-center py-4">
        <div className="w-16 h-16 rounded-full bg-violet-900/40 border border-violet-700 flex items-center justify-center mx-auto mb-4">
          <Sparkles className="w-8 h-8 text-violet-400" />
        </div>
        <p className="text-gray-300 leading-relaxed">
          SOUL is your private AI assistant — it learns your preferences, remembers context across conversations, and adapts to you over time.
        </p>
        <p className="text-gray-500 text-sm mt-3">Setup takes 30 seconds.</p>
      </div>
      <button
        onClick={onNext}
        className="w-full flex items-center justify-center gap-2 py-3 px-6 bg-violet-600 hover:bg-violet-500 text-white rounded-lg font-medium transition-colors"
      >
        Get Started <ChevronRight className="w-4 h-4" />
      </button>
    </div>
  )
}

function StepName({
  value, onChange, error, onNext, onBack,
}: {
  value: string; onChange: (v: string) => void; error: string;
  onNext: () => void; onBack: () => void
}) {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-violet-400 text-sm font-mono mb-2">
        <User className="w-4 h-4" /> Step 1 — Name your assistant
      </div>
      <div>
        <label className="block text-gray-300 text-sm mb-2">What should your assistant be called?</label>
        <input
          autoFocus
          type="text"
          value={value}
          onChange={e => onChange(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && onNext()}
          placeholder="e.g. Nova, Atlas, Aria…"
          className="w-full bg-gray-800 border border-gray-600 focus:border-violet-500 rounded-lg px-4 py-3 text-white placeholder-gray-500 outline-none transition-colors"
        />
        {error && <p className="text-red-400 text-xs mt-1">{error}</p>}
      </div>
      <div className="flex gap-3">
        <button onClick={onBack} className="flex-1 py-3 px-4 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg transition-colors">Back</button>
        <button onClick={onNext} className="flex-1 flex items-center justify-center gap-2 py-3 px-4 bg-violet-600 hover:bg-violet-500 text-white rounded-lg font-medium transition-colors">
          Next <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

function StepPersonality({
  selected, onSelect, onNext, onBack,
}: {
  selected: string; onSelect: (v: string) => void; onNext: () => void; onBack: () => void
}) {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-violet-400 text-sm font-mono mb-2">
        <Brain className="w-4 h-4" /> Step 2 — Personality preset
      </div>
      <div className="grid grid-cols-2 gap-3">
        {OCEAN_PRESETS.map(p => (
          <button
            key={p.id}
            onClick={() => onSelect(p.id)}
            className={`p-3 rounded-lg border text-left transition-all ${
              selected === p.id
                ? 'border-violet-500 bg-violet-900/30 text-white'
                : 'border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-500'
            }`}
          >
            <div className="text-lg mb-1">{p.icon}</div>
            <div className="font-medium text-sm">{p.label}</div>
            <div className="text-xs text-gray-500 mt-1 leading-tight">{p.desc}</div>
          </button>
        ))}
      </div>
      <div className="flex gap-3">
        <button onClick={onBack} className="flex-1 py-3 px-4 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg transition-colors">Back</button>
        <button onClick={onNext} className="flex-1 flex items-center justify-center gap-2 py-3 px-4 bg-violet-600 hover:bg-violet-500 text-white rounded-lg font-medium transition-colors">
          Next <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

function StepConfirm({
  agentName, preset, loading, error, onBack, onFinish,
}: {
  agentName: string; preset: string; loading: boolean; error: string;
  onBack: () => void; onFinish: () => void
}) {
  const presetLabel = OCEAN_PRESETS.find(p => p.id === preset)?.label ?? preset
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-violet-400 text-sm font-mono mb-2">
        <CheckCircle className="w-4 h-4" /> Step 3 — Confirm
      </div>
      <div className="bg-gray-800 rounded-lg p-4 space-y-3">
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">Assistant name</span>
          <span className="text-white font-medium">{agentName}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">Personality</span>
          <span className="text-white font-medium">{presetLabel}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">Config stored at</span>
          <span className="text-gray-500 font-mono text-xs">~/.seal/companion.toml</span>
        </div>
      </div>
      {error && <p className="text-red-400 text-sm">{error}</p>}
      <div className="flex gap-3">
        <button onClick={onBack} disabled={loading} className="flex-1 py-3 px-4 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg transition-colors disabled:opacity-50">Back</button>
        <button
          onClick={onFinish}
          disabled={loading}
          className="flex-1 flex items-center justify-center gap-2 py-3 px-4 bg-violet-600 hover:bg-violet-500 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
        >
          {loading ? 'Setting up…' : 'Launch Assistant'}
          {!loading && <Sparkles className="w-4 h-4" />}
        </button>
      </div>
    </div>
  )
}
