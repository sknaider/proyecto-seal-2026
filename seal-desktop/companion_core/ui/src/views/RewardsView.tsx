import { useEffect, useState, useCallback } from 'react'
import { Gift, Flame, Award, Copy, Check, RefreshCw, Sparkles } from 'lucide-react'

interface Achievement {
  id: string
  title: string
  description: string
  unlocked: boolean
  progressLabel?: string
  reward?: { type: 'messages' | 'days_plus' | string; amount: number }
}

interface InviteCode { code: string; used: boolean; usedBy?: string | null }

interface Snapshot {
  plan: 'FREE' | 'PLUS' | 'PRO' | string
  streak_days: number
  longest_streak: number
  messages_count: number
  features_used: number
  features_tracked: number
  achievements: Achievement[]
  invite_codes: InviteCode[]
}

// Local default until the rewards backend lands.
const DEFAULT_SNAPSHOT: Snapshot = {
  plan: 'FREE',
  streak_days: 1,
  longest_streak: 1,
  messages_count: 0,
  features_used: 0,
  features_tracked: 14,
  achievements: [
    { id: 'first_day',  title: 'Primer día',           description: 'Tu SEAL nació. Bienvenida.', unlocked: true,  progressLabel: '✓',   reward: { type: 'messages', amount: 100 } },
    { id: 'week',       title: 'Semana completa',      description: '7 días seguidos con tu SEAL.', unlocked: false, progressLabel: '1/7', reward: { type: 'messages', amount: 500 } },
    { id: 'explorer',   title: 'Curiosa insaciable',   description: 'Usaste 10 features distintas.', unlocked: false, progressLabel: '0/10', reward: { type: 'messages', amount: 300 } },
    { id: 'mentor',     title: 'Mentor',               description: 'Invitaste 3 amigos.', unlocked: false, progressLabel: '0/3', reward: { type: 'days_plus', amount: 30 } },
    { id: 'month',      title: '30 días seguidos',     description: 'Un mes entero juntas.', unlocked: false, progressLabel: '1/30', reward: { type: 'days_plus', amount: 7 } },
  ],
  invite_codes: [
    { code: 'SEAL-FREE-001', used: false },
    { code: 'SEAL-FREE-002', used: false },
    { code: 'SEAL-FREE-003', used: false },
  ],
}

const TABS = ['achievements', 'streak', 'invites'] as const

export default function RewardsView() {
  const [tab, setTab] = useState<typeof TABS[number]>('achievements')
  const [snap, setSnap] = useState<Snapshot>(DEFAULT_SNAPSHOT)
  const [loading, setLoading] = useState(false)
  const [copiedCode, setCopiedCode] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setSnap(DEFAULT_SNAPSHOT)
    setLoading(false)
  }, [])

  useEffect(() => { void load() }, [load])

  const copyCode = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code)
      setCopiedCode(code)
      setTimeout(() => setCopiedCode(null), 2000)
    } catch {/* noop */}
  }

  const unlockedCount = snap.achievements.filter(a => a.unlocked).length
  const remainingCodes = snap.invite_codes.filter(c => !c.used).length

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Gift className="w-5 h-5 text-violet-500" />
            <h1 className="text-xl font-semibold text-slate-800">Recompensas</h1>
            <span className="text-xs px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">{snap.plan}</span>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded border border-seal-border hover:border-violet-400 text-seal-muted hover:text-violet-500"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
          <StatCard icon={Flame} label="Racha" value={`${snap.streak_days}d`} color="text-orange-500" />
          <StatCard icon={Sparkles} label="Mensajes" value={`${snap.messages_count}`} color="text-violet-500" />
          <StatCard icon={Award} label="Logros" value={`${unlockedCount}/${snap.achievements.length}`} color="text-emerald-500" />
          <StatCard icon={Gift} label="Códigos" value={`${remainingCodes}`} color="text-sky-500" />
        </div>

        {/* Tabs */}
        <div className="flex border-b border-seal-border mb-3 text-xs">
          {TABS.map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 capitalize border-b-2 -mb-px ${tab === t ? 'border-violet-500 text-violet-700' : 'border-transparent text-seal-muted hover:text-slate-700'}`}
            >
              {t === 'achievements' ? 'Logros' : t === 'streak' ? 'Racha' : 'Invitar'}
            </button>
          ))}
        </div>

        {/* Tab: Achievements */}
        {tab === 'achievements' && (
          <div className="space-y-2">
            {snap.achievements.map(a => (
              <div key={a.id} className={`rounded-lg border p-3 ${a.unlocked ? 'bg-emerald-50 border-emerald-200' : 'bg-seal-surface border-seal-border'}`}>
                <div className="flex items-center gap-2 mb-1">
                  <Award className={`w-4 h-4 ${a.unlocked ? 'text-emerald-500' : 'text-stone-400'}`} />
                  <h3 className={`text-sm font-medium ${a.unlocked ? 'text-emerald-800' : 'text-slate-700'}`}>{a.title}</h3>
                  {a.progressLabel && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/60 text-slate-600 border border-stone-200">{a.progressLabel}</span>
                  )}
                  {a.reward && (
                    <span className="ml-auto text-[10px] text-violet-600">+{a.reward.amount} {a.reward.type === 'messages' ? 'msg' : a.reward.type === 'days_plus' ? 'd Plus' : a.reward.type}</span>
                  )}
                </div>
                <p className="text-xs text-slate-600">{a.description}</p>
              </div>
            ))}
          </div>
        )}

        {/* Tab: Streak */}
        {tab === 'streak' && (
          <div className="rounded-xl bg-seal-surface border border-seal-border p-6 text-center">
            <Flame className="w-16 h-16 text-orange-500 mx-auto mb-3" />
            <p className="text-4xl font-light text-slate-800">{snap.streak_days} {snap.streak_days === 1 ? 'día' : 'días'}</p>
            <p className="text-sm text-seal-muted mt-1">Tu racha actual con SEAL.</p>
            <p className="text-xs text-slate-500 mt-4">
              Récord histórico: <strong>{snap.longest_streak} {snap.longest_streak === 1 ? 'día' : 'días'}</strong>.
              Volvé mañana para mantenerla viva.
            </p>
          </div>
        )}

        {/* Tab: Invites */}
        {tab === 'invites' && (
          <div className="space-y-3">
            <div className="rounded-xl bg-violet-50 border border-violet-200 p-4">
              <p className="text-sm text-violet-800">
                Compartí tus códigos. Por cada amiga que se una con tu código:
                <br />
                ✨ <strong>vos ganás 50 mensajes</strong> · ella gana <strong>100 + 7 días Plus</strong>
              </p>
            </div>
            <div className="space-y-2">
              {snap.invite_codes.map(c => (
                <div key={c.code} className="flex items-center gap-2 p-3 rounded-lg border border-seal-border bg-seal-surface">
                  <span className="font-mono text-sm flex-1 text-slate-800">{c.code}</span>
                  {c.used ? (
                    <span className="text-[10px] px-2 py-1 rounded-full bg-stone-100 text-stone-500">Usado{c.usedBy ? ` · @${c.usedBy}` : ''}</span>
                  ) : (
                    <span className="text-[10px] px-2 py-1 rounded-full bg-emerald-100 text-emerald-700">Disponible</span>
                  )}
                  <button
                    onClick={() => copyCode(c.code)}
                    disabled={c.used}
                    className="p-1.5 rounded hover:bg-stone-100 text-seal-muted hover:text-slate-700 disabled:opacity-30"
                    title="Copiar"
                  >
                    {copiedCode === c.code ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-seal-muted text-center pt-2">
              Has invitado {snap.invite_codes.filter(c => c.used).length} · Mensajes ganados: {snap.invite_codes.filter(c => c.used).length * 50}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function StatCard({ icon: Icon, label, value, color }: { icon: React.ComponentType<{ className?: string }>; label: string; value: string; color: string }) {
  return (
    <div className="rounded-lg border border-seal-border bg-seal-surface p-3 flex items-center gap-2">
      <Icon className={`w-5 h-5 ${color}`} />
      <div>
        <p className="text-lg font-semibold text-slate-800 leading-tight">{value}</p>
        <p className="text-[10px] uppercase tracking-widest text-seal-muted">{label}</p>
      </div>
    </div>
  )
}
