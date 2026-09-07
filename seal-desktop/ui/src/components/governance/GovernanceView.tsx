import { useState, useEffect } from 'react'
import { Scale, CheckCircle, XCircle, Clock, Network, BookLock, Users, Activity, Heart, ShieldCheck, BadgeCheck, Sliders, Ban, ScrollText, Coins, Wrench, Star, Plus, X, Trash2, GitMerge, Pencil, UserCheck, History, Search } from 'lucide-react'
import { AGENT_COLORS } from '../../lib/types'

const HOST = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
const SOUL = `http://${HOST}:8800`

interface Debate {
  id: number
  topic: string
  agents_involved: string[]
  trigger_type: string
  rounds_completed: number
  consensus_reached: boolean
  outcome?: string
  synthesis?: string
  created_at: string
  completed_at?: string
}

interface Challenge {
  id: number
  challenger_agent: string
  target_agent?: string
  topic: string
  challenge: string
  response?: string
  resolved: boolean
  resolution?: string
  created_at: string
  resolved_at?: string
}

function AgentChip({ name }: { name: string }) {
  const color = AGENT_COLORS[name] ?? '#555'
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded font-medium"
      style={{ color, backgroundColor: color + '20' }}>
      {name}
    </span>
  )
}

function DebateCard({ d, onCompleted }: { d: Debate; onCompleted?: () => void }) {
  const [open, setOpen] = useState(false)
  const [completing, setCompleting] = useState(false)
  const [consensus, setConsensus] = useState(true)
  const [outcome, setOutcome] = useState('')
  const [saving, setSaving] = useState(false)
  const duration = d.completed_at
    ? Math.round((new Date(d.completed_at).getTime() - new Date(d.created_at).getTime()) / 1000)
    : null
  const isOpen = !d.completed_at

  async function handleComplete(e: React.MouseEvent) {
    e.stopPropagation()
    setSaving(true)
    try {
      const r = await fetch(`${SOUL}/api/soul/debate-log/${d.id}/complete`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ consensus_reached: consensus, outcome: outcome.trim() || undefined })
      })
      if (r.ok) { setCompleting(false); onCompleted?.() }
    } finally { setSaving(false) }
  }

  return (
    <div className="rounded-lg border border-[#0f0f0f] bg-[#050505] overflow-hidden hover:border-[#222] transition-colors"
      style={{ borderLeftColor: d.consensus_reached ? '#10b981' : isOpen ? '#f59e0b' : '#333', borderLeftWidth: 2 }}
    >
      <div className="px-3 py-2.5 cursor-pointer" onClick={() => { setOpen(x => !x); setCompleting(false) }}>
        <div className="flex items-start gap-2">
          {d.consensus_reached
            ? <CheckCircle size={12} className="text-[#10b981] flex-shrink-0 mt-0.5" />
            : isOpen
              ? <Clock size={12} className="text-[#f59e0b] flex-shrink-0 mt-0.5" />
              : <XCircle size={12} className="text-[#444] flex-shrink-0 mt-0.5" />
          }
          <div className="flex-1 min-w-0">
            <p className="text-xs text-[#ccc] font-medium truncate">{d.topic}</p>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              {(d.agents_involved ?? []).map(a => <AgentChip key={a} name={a} />)}
              <span className="text-[10px] text-[#333] ml-auto">
                {new Date(d.created_at).toLocaleDateString('es-PE')}
              </span>
            </div>
          </div>
          {isOpen && (
            <button onClick={e => { e.stopPropagation(); setCompleting(x => !x); setOpen(false) }}
              className="flex-shrink-0 flex items-center gap-0.5 px-1.5 py-0.5 rounded border border-[#1a1a1a] text-[9px] text-[#555] hover:text-[#10b981] hover:border-[#10b98130] transition-colors">
              <GitMerge size={9} /> Close
            </button>
          )}
        </div>

        {open && (
          <div className="mt-2 pt-2 border-t border-[#111] space-y-2">
            <div className="flex items-center gap-3 text-[10px] text-[#444]">
              <span>trigger: <span className="text-[#555]">{d.trigger_type}</span></span>
              <span>rounds: <span className="text-[#555]">{d.rounds_completed}</span></span>
              {duration !== null && <span>duration: <span className="text-[#555]">{duration}s</span></span>}
            </div>
            {d.outcome && (
              <div>
                <span className="text-[9px] text-[#444] uppercase tracking-wider">Outcome</span>
                <p className="text-xs text-[#888] mt-0.5 leading-relaxed">{d.outcome}</p>
              </div>
            )}
            {d.synthesis && (
              <div>
                <span className="text-[9px] text-[#444] uppercase tracking-wider">Synthesis</span>
                <p className="text-xs text-[#666] mt-0.5 leading-relaxed">{d.synthesis}</p>
              </div>
            )}
          </div>
        )}
      </div>

      {completing && (
        <div className="px-3 pb-3 pt-2 border-t border-[#0d0d0d] space-y-2" onClick={e => e.stopPropagation()}>
          <div className="flex items-center gap-3">
            <span className="text-[9px] text-[#444] uppercase tracking-wider">Consensus</span>
            <button onClick={() => setConsensus(true)}
              className={`px-2 py-0.5 rounded text-[9px] border transition-colors ${consensus ? 'bg-[#10b98115] border-[#10b98140] text-[#10b981]' : 'border-[#111] text-[#333]'}`}>
              Yes
            </button>
            <button onClick={() => setConsensus(false)}
              className={`px-2 py-0.5 rounded text-[9px] border transition-colors ${!consensus ? 'bg-[#ef444415] border-[#ef444440] text-[#ef4444]' : 'border-[#111] text-[#333]'}`}>
              No
            </button>
          </div>
          <textarea value={outcome} onChange={e => setOutcome(e.target.value)} rows={2}
            placeholder="Outcome (optional)…"
            className="w-full bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1.5 text-[10px] text-[#888] focus:outline-none focus:border-[#333] resize-none" />
          <div className="flex gap-2">
            <button onClick={() => setCompleting(false)}
              className="flex-1 py-1 rounded border border-[#111] text-[9px] text-[#444] hover:text-[#666] transition-colors">
              Cancel
            </button>
            <button onClick={handleComplete} disabled={saving}
              className="flex-1 py-1 rounded bg-[#10b98115] border border-[#10b98130] text-[9px] text-[#10b981] hover:bg-[#10b98125] transition-colors disabled:opacity-50">
              {saving ? 'Saving…' : 'Mark Complete'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

const CHALLENGE_AGENTS = ['ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM'] as const

function NewChallengeModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [challenger, setChallenger] = useState<string>('ALICE')
  const [target, setTarget] = useState<string>('none')
  const [topic, setTopic] = useState('')
  const [challenge, setChallenge] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!topic.trim() || !challenge.trim()) { setErr('Topic and challenge are required'); return }
    setSaving(true); setErr('')
    try {
      const body: Record<string, unknown> = { challenger_agent: challenger, topic: topic.trim(), challenge: challenge.trim() }
      if (target !== 'none') body.target_agent = target
      const r = await fetch(`${SOUL}/api/soul/governance/challenge`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      })
      const d = await r.json()
      if (!r.ok) { setErr(d.error ?? 'Failed'); return }
      onCreated(); onClose()
    } catch (ex: unknown) { setErr(String(ex)) } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-[#080808] border border-[#1a1a1a] rounded-xl p-5 w-80 space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-[#e5e5e5]">New Challenge</span>
          <button onClick={onClose}><X size={14} className="text-[#555]" /></button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[9px] text-[#444] uppercase tracking-wider">Challenger</label>
              <div className="flex flex-wrap gap-1 mt-1">
                {CHALLENGE_AGENTS.map(a => {
                  const isActive = challenger === a
                  const color = AGENT_COLORS[a] ?? '#555'
                  return (
                    <button key={a} type="button" onClick={() => setChallenger(a)}
                      className="px-2 py-0.5 rounded text-[10px] font-mono transition-colors"
                      style={isActive ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                      {a}
                    </button>
                  )
                })}
              </div>
            </div>
            <div>
              <label className="text-[9px] text-[#444] uppercase tracking-wider">Target (opt)</label>
              <div className="flex flex-wrap gap-1 mt-1">
                {(['none', ...CHALLENGE_AGENTS] as const).map(a => {
                  const isActive = target === a
                  const color = a === 'none' ? '#555' : (AGENT_COLORS[a] ?? '#555')
                  return (
                    <button key={a} type="button" onClick={() => setTarget(a)}
                      className="px-2 py-0.5 rounded text-[10px] font-mono transition-colors"
                      style={isActive ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                      {a === 'none' ? 'any' : a}
                    </button>
                  )
                })}
              </div>
            </div>
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider">Topic</label>
            <input value={topic} onChange={e => setTopic(e.target.value)} placeholder="Challenge topic…"
              className="w-full mt-1 bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1.5 text-xs text-[#ccc] focus:outline-none focus:border-[#333]" />
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider">Challenge</label>
            <textarea value={challenge} onChange={e => setChallenge(e.target.value)} rows={3}
              placeholder="Describe the challenge…"
              className="w-full mt-1 bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1.5 text-xs text-[#ccc] focus:outline-none focus:border-[#333] resize-none" />
          </div>
          {err && <p className="text-[10px] text-[#ef4444]">{err}</p>}
          <div className="flex gap-2 pt-1">
            <button type="button" onClick={onClose}
              className="flex-1 py-1.5 rounded border border-[#1a1a1a] text-xs text-[#555] hover:text-[#888] transition-colors">
              Cancel
            </button>
            <button type="submit" disabled={saving}
              className="flex-1 py-1.5 rounded bg-[#111] border border-[#333] text-xs text-[#ccc] hover:bg-[#1a1a1a] transition-colors disabled:opacity-50">
              {saving ? 'Creating…' : 'Challenge'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function ChallengeCard({ c, onResolved }: { c: Challenge; onResolved?: () => void }) {
  const [open, setOpen] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [resolution, setResolution] = useState('')
  const [saving, setSaving] = useState(false)
  const challengerColor = AGENT_COLORS[c.challenger_agent] ?? '#666'
  const targetColor = c.target_agent ? (AGENT_COLORS[c.target_agent] ?? '#666') : null

  async function handleResolve(e: React.MouseEvent) {
    e.stopPropagation()
    if (!resolution.trim()) return
    setSaving(true)
    try {
      const r = await fetch(`${SOUL}/api/soul/agent-challenges/${c.id}/resolve`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resolution: resolution.trim() })
      })
      if (r.ok) { setResolving(false); onResolved?.() }
    } finally { setSaving(false) }
  }

  return (
    <div className="rounded-lg border border-[#0f0f0f] bg-[#050505] overflow-hidden hover:border-[#222] transition-colors"
      style={{ borderLeftColor: challengerColor, borderLeftWidth: 2 }}
    >
      <div className="px-3 py-2.5 cursor-pointer" onClick={() => { setOpen(x => !x); setResolving(false) }}>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] font-semibold" style={{ color: challengerColor }}>
            {c.challenger_agent}
          </span>
          {c.target_agent && (
            <>
              <span className="text-[10px] text-[#333]">→</span>
              <span className="text-[10px] font-semibold" style={{ color: targetColor ?? '#666' }}>
                {c.target_agent}
              </span>
            </>
          )}
          <span className="text-[10px] text-[#555] truncate ml-1 flex-1">{c.topic}</span>
          {c.resolved
            ? <CheckCircle size={10} className="text-[#10b981] flex-shrink-0" />
            : (
              <button onClick={e => { e.stopPropagation(); setResolving(x => !x); setOpen(false) }}
                className="flex-shrink-0 flex items-center gap-0.5 px-1.5 py-0.5 rounded border border-[#1a1a1a] text-[9px] text-[#555] hover:text-[#10b981] hover:border-[#10b98130] transition-colors">
                <CheckCircle size={9} /> Resolve
              </button>
            )
          }
        </div>
        <p className={`text-xs text-[#888] leading-relaxed ${open ? '' : 'truncate'}`}>
          {c.challenge}
        </p>
        {open && (
          <div className="mt-2 pt-2 border-t border-[#111] space-y-1.5">
            {c.response && (
              <div>
                <span className="text-[9px] text-[#444] uppercase tracking-wider">Response</span>
                <p className="text-xs text-[#666] mt-0.5 leading-relaxed">{c.response}</p>
              </div>
            )}
            {c.resolution && (
              <div>
                <span className="text-[9px] text-[#444] uppercase tracking-wider">Resolution</span>
                <p className="text-xs text-[#10b981] mt-0.5 text-[10px]">{c.resolution}</p>
              </div>
            )}
            <span className="text-[10px] text-[#2a2a2a]">
              {new Date(c.created_at).toLocaleString('es-PE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        )}
      </div>

      {resolving && (
        <div className="px-3 pb-3 pt-2 border-t border-[#0d0d0d] space-y-2" onClick={e => e.stopPropagation()}>
          <textarea value={resolution} onChange={e => setResolution(e.target.value)} rows={2}
            placeholder="Resolution text…"
            className="w-full bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1.5 text-[10px] text-[#888] focus:outline-none focus:border-[#333] resize-none" />
          <div className="flex gap-2">
            <button onClick={() => setResolving(false)}
              className="flex-1 py-1 rounded border border-[#111] text-[9px] text-[#444] hover:text-[#666] transition-colors">
              Cancel
            </button>
            <button onClick={handleResolve} disabled={saving || !resolution.trim()}
              className="flex-1 py-1 rounded bg-[#10b98115] border border-[#10b98130] text-[9px] text-[#10b981] hover:bg-[#10b98125] transition-colors disabled:opacity-50">
              {saving ? 'Saving…' : 'Mark Resolved'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

interface TrustRelationship {
  agent_a: string
  agent_b: string
  trust_level: number
  collaboration_count: number
  debate_count: number
  notes: string | null
  last_updated: string
}

function trustColor(t: number) {
  if (t >= 0.8) return '#10b981'
  if (t >= 0.6) return '#f59e0b'
  if (t >= 0.4) return '#6b7280'
  return '#ef4444'
}

function TrustMatrixPanel() {
  const [rels, setRels] = useState<TrustRelationship[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tmSort, setTmSort] = useState<'trust' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/trust-matrix`)
        const d = await r.json()
        if (active) setRels(d.relationships ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const visibleRels = tmSort === 'trust' ? [...rels].sort((a, b) => b.trust_level - a.trust_level) : rels

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[9px] text-[#222]">{rels.length} relationships</span>
        <button onClick={() => setTmSort(s => s === 'trust' ? 'date' : 'trust')}
          className="ml-auto px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={tmSort === 'trust' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ trust
        </button>
      </div>
      {loading && !rels.length && (
        <div className="text-xs text-[#444] py-4 text-center">Loading…</div>
      )}
      {visibleRels.map(r => {
        const key = `${r.agent_a}-${r.agent_b}`
        const colorA = AGENT_COLORS[r.agent_a] ?? '#666'
        const colorB = AGENT_COLORS[r.agent_b] ?? '#aaa'
        const tc = trustColor(r.trust_level)
        const pct = Math.round(r.trust_level * 100)
        const isExp = expanded === key

        return (
          <div key={key}
            className="rounded-lg border border-[#0f0f0f] bg-[#050505] px-3 py-2 cursor-pointer hover:border-[#1a1a1a] transition-colors"
            onClick={() => setExpanded(x => x === key ? null : key)}>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold" style={{ color: colorA }}>{r.agent_a}</span>
              <span className="text-[9px] text-[#333]">→</span>
              <span className="text-xs font-semibold" style={{ color: colorB }}>{r.agent_b}</span>
              <div className="flex items-center gap-1.5 ml-auto">
                <div className="w-16 h-1 bg-[#111] rounded-full overflow-hidden">
                  <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: tc }} />
                </div>
                <span className="text-[10px] font-mono w-8 text-right" style={{ color: tc }}>{pct}%</span>
              </div>
            </div>
            {(r.collaboration_count > 0 || r.debate_count > 0) && (
              <div className="flex items-center gap-3 mt-1">
                {r.collaboration_count > 0 && (
                  <span className="text-[9px] text-[#444]">{r.collaboration_count} collabs</span>
                )}
                {r.debate_count > 0 && (
                  <span className="text-[9px] text-[#555]">{r.debate_count} debates</span>
                )}
              </div>
            )}
            {isExp && r.notes && (
              <div className="mt-1.5 pt-1.5 border-t border-[#111]">
                <p className="text-[10px] text-[#555] leading-relaxed">{
                  typeof r.notes === 'string'
                    ? (() => { try { const p = JSON.parse(r.notes); return p.dynamic_from_ADA_LOCAL ?? r.notes } catch { return r.notes } })()
                    : String(r.notes)
                }</p>
              </div>
            )}
          </div>
        )
      })}
      {!loading && !rels.length && (
        <div className="text-xs text-[#333] py-6 text-center">
          <Network size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No trust data
        </div>
      )}
    </div>
  )
}

interface Rule {
  id: number
  agent: string | null
  rule_key: string
  content: string
  priority: number
  tier: number
  active: boolean
  set_by: string | null
  created_at: string
}

const TIER_LABELS: Record<number, string> = { 1: 'Critical', 2: 'High', 3: 'Normal', 4: 'Low' }
const TIER_COLORS: Record<number, string> = { 1: '#ef4444', 2: '#f59e0b', 3: '#10b981', 4: '#555' }
const RULE_AGENTS = ['ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM', 'TEAM']

function NewRuleModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [ruleKey, setRuleKey] = useState('')
  const [content, setContent] = useState('')
  const [agent, setAgent] = useState('')
  const [priority, setPriority] = useState(5)
  const [tier, setTier] = useState(3)
  const [setBy, setSetBy] = useState('ALICE')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!ruleKey.trim() || !content.trim()) { setError('rule_key and content required'); return }
    setSaving(true); setError('')
    try {
      const r = await fetch(`${SOUL}/api/soul/rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rule_key: ruleKey.trim(), content: content.trim(), agent: agent || null, priority, tier, set_by: setBy || null }),
      })
      const d = await r.json()
      if (!r.ok || !d.ok) { setError(d.error ?? 'failed'); return }
      onCreated(); onClose()
    } catch (ex) { setError(String(ex)) }
    finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50" onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={submit}
        className="bg-[#080808] border border-[#1a1a1a] rounded-lg w-80 p-4 space-y-3 shadow-2xl">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-[#e5e5e5]">New Rule</span>
          <button type="button" onClick={onClose} className="text-[#333] hover:text-[#777]"><X size={14} /></button>
        </div>
        <div className="space-y-2">
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Rule Key *</label>
            <input value={ruleKey} onChange={e => setRuleKey(e.target.value)} autoFocus
              className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#ccc] outline-none focus:border-[#444] placeholder-[#333] font-mono"
              placeholder="snake_case_key" />
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Content *</label>
            <textarea value={content} onChange={e => setContent(e.target.value)} rows={3}
              className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#ccc] outline-none focus:border-[#444] placeholder-[#333] resize-none"
              placeholder="Rule description…" />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Agent</label>
              <div className="flex flex-wrap gap-1">
                {(['', ...RULE_AGENTS]).map(a => {
                  const isActive = agent === a
                  const color = a === '' ? '#555' : (AGENT_COLORS[a] ?? '#555')
                  return (
                    <button key={a || 'GLOBAL'} type="button" onClick={() => setAgent(a)}
                      className="px-2 py-0.5 rounded text-[10px] font-mono transition-colors"
                      style={isActive ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                      {a === '' ? 'GLOBAL' : a}
                    </button>
                  )
                })}
              </div>
            </div>
            <div>
              <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Tier</label>
              <div className="flex flex-wrap gap-1">
                {[1,2,3,4].map(t => (
                  <button key={t} type="button" onClick={() => setTier(t)}
                    className="px-2 py-0.5 rounded text-[10px] font-mono transition-colors"
                    style={tier === t ? { color: TIER_COLORS[t], border: `1px solid ${TIER_COLORS[t]}44`, backgroundColor: TIER_COLORS[t] + '18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                    {TIER_LABELS[t]}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Priority {priority}</label>
            <input type="range" min={1} max={10} step={1} value={priority} onChange={e => setPriority(parseInt(e.target.value))}
              className="w-full accent-[#7c3aed]" />
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Set By</label>
            <input value={setBy} onChange={e => setSetBy(e.target.value)}
              className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#ccc] outline-none focus:border-[#444] placeholder-[#333]"
              placeholder="ALICE" />
          </div>
        </div>
        {error && <p className="text-[10px] text-[#ef4444]">{error}</p>}
        <div className="flex gap-2 pt-1">
          <button type="button" onClick={onClose}
            className="flex-1 px-3 py-1.5 text-xs text-[#555] border border-[#1a1a1a] rounded hover:border-[#333] transition-colors">
            Cancel
          </button>
          <button type="submit" disabled={saving}
            className="flex-1 px-3 py-1.5 text-xs bg-[#7c3aed] text-white rounded hover:bg-[#6d28d9] disabled:opacity-50 transition-colors">
            {saving ? 'Creating…' : 'Create'}
          </button>
        </div>
      </form>
    </div>
  )
}

function RulesBrowserPanel() {
  const [rules, setRules] = useState<Rule[]>([])
  const [agentFilter, setAgentFilter] = useState('all')
  const [activeOnly, setActiveOnly] = useState(true)
  const [tierFilter, setTierFilter] = useState<number | 0>(0)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [showNewRule, setShowNewRule] = useState(false)
  const [tick, setTick] = useState(0)
  const [confirmDeleteRule, setConfirmDeleteRule] = useState<number | null>(null)
  const [ruleSort, setRuleSort] = useState<'priority' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const q = new URLSearchParams({ agent: agentFilter, active_only: String(activeOnly), limit: '80' })
        const r = await fetch(`${SOUL}/api/soul/rules?${q}`)
        const d = await r.json()
        if (active) setRules(d.rules ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [agentFilter, activeOnly, tick])

  async function handleDeleteRule(id: number, e: React.MouseEvent) {
    e.stopPropagation()
    if (confirmDeleteRule === id) {
      try {
        await fetch(`${SOUL}/api/soul/rules/${id}`, { method: 'DELETE' })
        setRules(prev => prev.filter(r => r.id !== id))
      } finally { setConfirmDeleteRule(null) }
    } else {
      setConfirmDeleteRule(id)
      setTimeout(() => setConfirmDeleteRule(c => c === id ? null : c), 3000)
    }
  }

  const agents = ['all', 'ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM', 'TEAM']
  const filteredRules = tierFilter === 0 ? rules : rules.filter(r => r.tier === tierFilter)
  const visibleRules = ruleSort === 'priority' ? [...filteredRules].sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0)) : filteredRules

  return (
    <div className="space-y-2">
      {showNewRule && <NewRuleModal onClose={() => setShowNewRule(false)} onCreated={() => setTick(t => t + 1)} />}
      <div className="flex items-center gap-2 flex-wrap pb-1">
        <button onClick={() => setShowNewRule(true)}
          className="flex items-center gap-1 px-2 py-0.5 text-[9px] bg-[#7c3aed18] text-[#7c3aed] border border-[#7c3aed30] rounded hover:bg-[#7c3aed30] transition-colors">
          <Plus size={8} />New
        </button>
        <div className="flex items-center gap-0.5">
          {agents.map(a => {
            const isActive = agentFilter === a
            const color = a === 'all' ? '#555' : (AGENT_COLORS[a] ?? '#555')
            return (
              <button key={a} onClick={() => setAgentFilter(a)}
                className="px-1.5 py-0.5 rounded text-[9px] font-mono transition-colors"
                style={isActive ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#333', border: '1px solid transparent' }}>
                {a === 'all' ? 'all' : a.slice(0, 2)}
              </button>
            )
          })}
        </div>
        <button onClick={() => setActiveOnly(v => !v)}
          className={`px-2 py-0.5 rounded text-[9px] border transition-colors ${
            activeOnly ? 'bg-[#111] border-[#10b981] text-[#10b981]' : 'border-[#1a1a1a] text-[#444] hover:text-[#666]'
          }`}>
          active only
        </button>
        <button onClick={() => setRuleSort(s => s === 'priority' ? 'date' : 'priority')}
          className="ml-auto px-2 py-0.5 rounded text-[10px] border transition-colors"
          style={ruleSort === 'priority' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#111', color: '#333' }}>
          ↓ priority
        </button>
        <span className="text-[10px] text-[#333]">
          {visibleRules.length} rules{loading && <span className="ml-1">…</span>}
        </span>
      </div>
      {/* tier filter */}
      <div className="flex items-center gap-1">
        <button onClick={() => setTierFilter(0)}
          className={`text-[9px] px-1.5 py-0.5 rounded transition-colors ${
            tierFilter === 0 ? 'bg-[#1a1a1a] text-[#e5e5e5]' : 'text-[#333] hover:text-[#666]'
          }`}>
          all tiers
        </button>
        {[1,2,3,4].map(t => (
          <button key={t} onClick={() => setTierFilter(tierFilter === t ? 0 : t)}
            className="text-[9px] px-1.5 py-0.5 rounded transition-colors"
            style={tierFilter === t
              ? { color: TIER_COLORS[t], backgroundColor: TIER_COLORS[t] + '20', border: `1px solid ${TIER_COLORS[t]}44` }
              : { color: '#333', border: '1px solid transparent' }}>
            {TIER_LABELS[t]}
          </button>
        ))}
      </div>

      {visibleRules.map(rule => {
        const color = rule.agent ? (AGENT_COLORS[rule.agent] ?? '#666') : '#7c3aed'
        const tc = TIER_COLORS[rule.tier] ?? '#555'
        const isExp = expanded === rule.id
        const isConfirmingDelete = confirmDeleteRule === rule.id

        return (
          <div key={rule.id}
            className="group rounded-lg border border-[#0f0f0f] bg-[#050505] overflow-hidden cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: tc, borderLeftWidth: 2 }}
            onClick={() => setExpanded(x => x === rule.id ? null : rule.id)}>
            <div className="px-3 py-2.5">
              <div className="flex items-start gap-2 mb-1">
                <p className="text-[10px] font-mono text-[#555] flex-1 truncate">{rule.rule_key}</p>
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <span className="text-[9px]" style={{ color: tc }}>{TIER_LABELS[rule.tier] ?? `T${rule.tier}`}</span>
                  <span className="text-[9px] font-semibold" style={{ color }}>
                    {rule.agent ?? 'GLOBAL'}
                  </span>
                  <button
                    onClick={e => handleDeleteRule(rule.id, e)}
                    className={`flex items-center gap-0.5 px-1 py-0.5 rounded text-[9px] transition-all ${
                      isConfirmingDelete
                        ? 'opacity-100 bg-[#ef444418] text-[#ef4444] border border-[#ef444430]'
                        : 'opacity-0 group-hover:opacity-100 text-[#333] hover:text-[#ef4444]'
                    }`}
                    title={isConfirmingDelete ? 'Click again to delete' : 'Delete rule'}>
                    {isConfirmingDelete ? <span className="text-[8px] font-medium">DEL?</span> : <Trash2 size={8} />}
                  </button>
                </div>
              </div>
              <p className={`text-xs text-[#888] leading-snug ${isExp ? '' : 'line-clamp-2'}`}>{rule.content}</p>
              {isExp && (
                <div className="flex items-center gap-3 mt-1.5 pt-1.5 border-t border-[#111]">
                  <span className="text-[9px] text-[#333]">p{rule.priority}</span>
                  {rule.set_by && (
                    <span className="text-[9px] text-[#333]">set by <span style={{ color: AGENT_COLORS[rule.set_by] ?? '#aaa' }}>{rule.set_by}</span></span>
                  )}
                  <span className="text-[9px] text-[#222] ml-auto">
                    {new Date(rule.created_at).toLocaleDateString('es-PE', { month: 'short', day: 'numeric' })}
                  </span>
                </div>
              )}
            </div>
          </div>
        )
      })}

      {!loading && !rules.length && (
        <div className="text-xs text-[#333] py-6 text-center">
          <BookLock size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No rules found
        </div>
      )}
    </div>
  )
}

interface PeerModel {
  id: number
  observer: string
  subject: string
  observed_patterns: string[]
  blind_spots: string[]
  strengths: string[]
  updated_at: string
}

function PeerModelsPanel() {
  const [models, setModels] = useState<PeerModel[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [editField, setEditField] = useState<'strengths' | 'blind_spots' | 'observed_patterns'>('strengths')
  const [editText, setEditText] = useState('')
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [pmSort, setPmSort] = useState<'patterns' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/peer-models`)
        const d = await r.json()
        if (active) setModels(d.models ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  function startEdit(m: PeerModel, field: typeof editField, e: React.MouseEvent) {
    e.stopPropagation()
    setEditing(m.id)
    setEditField(field)
    setEditText((m[field] ?? []).join('\n'))
    setExpanded(`${m.observer}-${m.subject}`)
  }

  async function handleSave(e: React.MouseEvent) {
    e.stopPropagation()
    if (editing === null) return
    setSaving(true)
    try {
      const arr = editText.split('\n').map(s => s.trim()).filter(Boolean)
      const r = await fetch(`${SOUL}/api/soul/peer-models/${editing}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [editField]: arr })
      })
      if (r.ok) {
        setModels(prev => prev.map(m => m.id === editing ? { ...m, [editField]: arr } : m))
        setEditing(null)
      }
    } finally { setSaving(false) }
  }

  const visibleModels = pmSort === 'patterns' ? [...models].sort((a, b) => (b.observed_patterns?.length ?? 0) - (a.observed_patterns?.length ?? 0)) : models

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[9px] text-[#222]">{models.length} peer models</span>
        <button onClick={() => setPmSort(s => s === 'patterns' ? 'date' : 'patterns')}
          className="ml-auto px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={pmSort === 'patterns' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ patterns
        </button>
      </div>
      {loading && !models.length && (
        <div className="text-xs text-[#444] py-4 text-center">Loading…</div>
      )}
      {visibleModels.map(m => {
        const key = `${m.observer}-${m.subject}`
        const obsColor = AGENT_COLORS[m.observer] ?? '#666'
        const subColor = AGENT_COLORS[m.subject] ?? '#aaa'
        const isExp = expanded === key
        const isEditing = editing === m.id

        return (
          <div key={key}
            className="rounded-lg border border-[#0f0f0f] bg-[#050505] overflow-hidden hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: obsColor, borderLeftWidth: 2 }}>
            <div className="px-3 py-2.5 cursor-pointer" onClick={() => { setExpanded(x => x === key ? null : key); setEditing(null) }}>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-xs font-semibold" style={{ color: obsColor }}>{m.observer}</span>
                <span className="text-[10px] text-[#333]">models</span>
                <span className="text-xs font-semibold" style={{ color: subColor }}>{m.subject}</span>
                <span className="text-[10px] text-[#222] ml-auto">
                  {new Date(m.updated_at).toLocaleDateString('es-PE', { month: 'short', day: 'numeric' })}
                </span>
              </div>
              <div className="flex items-center gap-3 text-[9px] text-[#444]">
                {m.strengths?.length > 0 && <span className="text-[#10b981]">{m.strengths.length} strengths</span>}
                {m.blind_spots?.length > 0 && <span className="text-[#f59e0b]">{m.blind_spots.length} blind spots</span>}
                {m.observed_patterns?.length > 0 && <span className="text-[#7c3aed]">{m.observed_patterns.length} patterns</span>}
              </div>
            </div>

            {isExp && (
              <div className="px-3 pb-3 border-t border-[#0d0d0d] space-y-2 pt-2">
                {(['strengths', 'blind_spots', 'observed_patterns'] as const).map(field => {
                  const fieldColor = field === 'strengths' ? '#10b981' : field === 'blind_spots' ? '#f59e0b' : '#7c3aed'
                  const items = m[field] ?? []
                  const isEditingField = isEditing && editField === field
                  return (
                    <div key={field}>
                      <div className="flex items-center gap-2 mb-1">
                        <p className="text-[9px] uppercase tracking-wider" style={{ color: fieldColor }}>{field.replace('_', ' ')}</p>
                        {!isEditing && (
                          <button onClick={e => startEdit(m, field, e)}
                            className="text-[8px] text-[#333] hover:text-[#666] px-1 rounded border border-[#111] transition-colors">
                            edit
                          </button>
                        )}
                      </div>
                      {isEditingField ? (
                        <div className="space-y-1.5" onClick={e => e.stopPropagation()}>
                          <textarea value={editText} onChange={e => setEditText(e.target.value)} rows={4}
                            className="w-full bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1.5 text-[10px] text-[#888] focus:outline-none focus:border-[#333] resize-none"
                            placeholder="One item per line…" />
                          <div className="flex gap-1.5">
                            <button onClick={() => setEditing(null)}
                              className="flex-1 py-0.5 rounded border border-[#111] text-[9px] text-[#444] hover:text-[#666] transition-colors">Cancel</button>
                            <button onClick={handleSave} disabled={saving}
                              className="flex-1 py-0.5 rounded border text-[9px] transition-colors disabled:opacity-50"
                              style={{ borderColor: fieldColor + '40', color: fieldColor, backgroundColor: fieldColor + '10' }}>
                              {saving ? '…' : 'Save'}
                            </button>
                          </div>
                        </div>
                      ) : (
                        items.map((s, i) => (
                          <p key={i} className="text-xs text-[#777] pl-2 mb-0.5 leading-snug"
                            style={{ borderLeft: `2px solid ${fieldColor}20` }}>{s}</p>
                        ))
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
      {!loading && !models.length && (
        <div className="text-xs text-[#333] py-6 text-center">
          <Users size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No peer models
        </div>
      )}
    </div>
  )
}

interface AuditEntry {
  id: number
  ts: string
  agent: string
  method: string
  path: string
  status: number
  latency_ms: number
  backend: string | null
}

function statusColor(s: number) {
  if (s < 300) return '#10b981'
  if (s < 400) return '#f59e0b'
  return '#ef4444'
}

function AuditLogPanel() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [auditSort, setAuditSort] = useState<'latency' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/audit-log?limit=60`)
        const d = await r.json()
        if (active) setEntries(d.entries ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const visibleAudit = auditSort === 'latency' ? [...entries].sort((a, b) => (b.latency_ms ?? 0) - (a.latency_ms ?? 0)) : entries

  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-2 pb-1.5">
        <span className="text-[10px] text-[#333]">{entries.length} entries</span>
        <button onClick={() => setAuditSort(s => s === 'latency' ? 'date' : 'latency')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
          style={auditSort === 'latency' ? { borderColor: '#f59e0b', color: '#f59e0b', backgroundColor: '#f59e0b18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ latency
        </button>
      </div>
      {loading && !entries.length && (
        <div className="text-xs text-[#444] py-4 text-center">Loading…</div>
      )}
      {visibleAudit.map(e => {
        const ac = AGENT_COLORS[e.agent] ?? '#666'
        const sc = statusColor(e.status)
        return (
          <div key={e.id}
            className="flex items-center gap-2 px-2 py-1.5 rounded border border-[#0a0a0a] hover:border-[#1a1a1a] transition-colors">
            <span className="text-[9px] font-semibold w-12 flex-shrink-0" style={{ color: ac }}>{e.agent}</span>
            <span className="text-[9px] text-[#444] font-mono w-8 flex-shrink-0">{e.method}</span>
            <span className="text-[10px] text-[#666] font-mono flex-1 truncate min-w-0">{e.path}</span>
            <span className="text-[9px] font-mono flex-shrink-0" style={{ color: sc }}>{e.status}</span>
            <span className="text-[9px] text-[#333] font-mono w-12 text-right flex-shrink-0">{e.latency_ms}ms</span>
            <span className="text-[9px] text-[#222] w-16 text-right flex-shrink-0">
              {new Date(e.ts).toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          </div>
        )
      })}
      {!loading && !entries.length && (
        <div className="text-xs text-[#333] py-6 text-center">
          <Activity size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No audit entries
        </div>
      )}
    </div>
  )
}

interface Relationship {
  id: number
  agent: string
  person: string
  trust_level: number
  communication_style: string | null
  dynamic: string | null
  interaction_count: number
  updated_at: string
}

function RelationshipsPanel() {
  const [items, setItems] = useState<Relationship[]>([])
  const [loading, setLoading] = useState(true)
  const [relSort, setRelSort] = useState<'interactions' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/relationships`)
        const d = await r.json()
        if (active) setItems(d.relationships ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const visibleRels = relSort === 'interactions' ? [...items].sort((a, b) => b.interaction_count - a.interaction_count) : items

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[9px] text-[#222]">{items.length} relationships</span>
        <button onClick={() => setRelSort(s => s === 'interactions' ? 'date' : 'interactions')}
          className="ml-auto px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={relSort === 'interactions' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ interactions
        </button>
      </div>
      {loading && !items.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading relationships…</div>
      )}
      {visibleRels.map(rel => {
        const color = AGENT_COLORS[rel.agent] ?? '#666'
        const trust = Number(rel.trust_level)
        const trustColor = trust >= 0.8 ? '#10b981' : trust >= 0.6 ? '#f59e0b' : '#ef4444'
        return (
          <div key={rel.id}
            className="rounded-lg border border-[#0f0f0f] bg-[#050505] px-4 py-3"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold" style={{ color }}>{rel.agent}</span>
              <span className="text-[10px] text-[#333]">↔</span>
              <span className="text-sm font-semibold text-[#e5e5e5]">{rel.person}</span>
              <Heart size={10} className="text-[#ef4444] ml-1" />
            </div>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[9px] text-[#444]">trust</span>
              <div className="flex-1 h-1.5 bg-[#111] rounded overflow-hidden">
                <div className="h-full rounded" style={{ width: `${trust * 100}%`, backgroundColor: trustColor }} />
              </div>
              <span className="text-[9px] font-mono" style={{ color: trustColor }}>{trust.toFixed(2)}</span>
            </div>
            {rel.dynamic && (
              <p className="text-xs text-[#666] mb-1">{rel.dynamic}</p>
            )}
            {rel.communication_style && (
              <p className="text-[10px] text-[#444]">style: {rel.communication_style}</p>
            )}
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <Heart size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No relationships
        </div>
      )}
    </div>
  )
}

interface AlmaRecord {
  agent_name: string
  ocean_baseline: string | null
  core_values_hash: string
  identity_pubkey: string
  boot_hash: string
  created_at: string
  last_validated_at: string | null
}

function AlmaPanel() {
  const [items, setItems] = useState<AlmaRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [almaSort, setAlmaSort] = useState<'openness' | 'default'>('default')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/agent-alma`)
        const d = await r.json()
        if (active) setItems(d.alma ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const visibleAlma = almaSort === 'openness'
    ? [...items].sort((a, b) => {
        let bA: Record<string, number> = {}; try { bA = a.ocean_baseline ? JSON.parse(a.ocean_baseline) : {} } catch {}
        let bB: Record<string, number> = {}; try { bB = b.ocean_baseline ? JSON.parse(b.ocean_baseline) : {} } catch {}
        return (bB['O'] ?? 0) - (bA['O'] ?? 0)
      })
    : items

  return (
    <div className="space-y-2">
      {loading && !items.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading alma…</div>
      )}
      <div className="flex items-center justify-end">
        <button onClick={() => setAlmaSort(s => s === 'openness' ? 'default' : 'openness')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={almaSort === 'openness' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ O
        </button>
      </div>
      {visibleAlma.map(a => {
        const color = AGENT_COLORS[a.agent_name] ?? '#666'
        const isExp = expanded === a.agent_name
        let baseline: Record<string, number> = {}
        try { baseline = a.ocean_baseline ? JSON.parse(a.ocean_baseline) : {} } catch { /* */ }
        return (
          <div key={a.agent_name}
            className="rounded-lg border border-[#0f0f0f] bg-[#050505] overflow-hidden cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}
            onClick={() => setExpanded(x => x === a.agent_name ? null : a.agent_name)}>
            <div className="px-3 py-2.5">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-semibold" style={{ color }}>{a.agent_name}</span>
                <ShieldCheck size={11} className="text-[#10b981]" />
                {a.last_validated_at && (
                  <span className="text-[9px] text-[#333] ml-auto">
                    validated {new Date(a.last_validated_at).toLocaleDateString('es-PE', { month: 'short', day: 'numeric', year: 'numeric' })}
                  </span>
                )}
              </div>
              {Object.keys(baseline).length > 0 && (
                <div className="space-y-1 mb-2">
                  {(['O','C','E','A','N'] as const).map(trait => {
                    const val = baseline[trait] ?? 0
                    return (
                      <div key={trait} className="flex items-center gap-2">
                        <span className="text-[9px] text-[#444] w-3">{trait}</span>
                        <div className="flex-1 h-1 bg-[#111] rounded overflow-hidden">
                          <div className="h-full rounded" style={{ width: `${val * 100}%`, backgroundColor: color + '80' }} />
                        </div>
                        <span className="text-[9px] text-[#444] w-8 text-right font-mono">{val.toFixed(2)}</span>
                      </div>
                    )
                  })}
                </div>
              )}
              {isExp && (
                <div className="space-y-1 mt-2 pt-2 border-t border-[#0d0d0d]">
                  {[
                    { label: 'identity', val: a.identity_pubkey },
                    { label: 'values', val: a.core_values_hash },
                    { label: 'boot', val: a.boot_hash },
                  ].map(({ label, val }) => (
                    <div key={label}>
                      <p className="text-[9px] text-[#333] uppercase tracking-wider">{label} hash</p>
                      <p className="text-[9px] font-mono text-[#2a2a2a] break-all">{val.slice(0, 40)}…</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <ShieldCheck size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No alma records
        </div>
      )}
    </div>
  )
}

interface AgentConfigRecord {
  agent: string
  dar_threshold: number
  skill_synthesize_daily_limit: number
  persona_axes: string | null
  preferences: string | null
  updated_at: string
}

const SOUL_CFG = typeof window !== 'undefined' ? `http://${window.location.hostname}:8800` : 'http://localhost:8800'

function AgentConfigPanel() {
  const [items, setItems] = useState<AgentConfigRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [editDar, setEditDar] = useState(0.4)
  const [editLimit, setEditLimit] = useState(5)
  const [editAxes, setEditAxes] = useState<Record<string, number>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [cfgSort, setCfgSort] = useState<'dar' | 'agent'>('agent')
  const [cfgAxesSort, setCfgAxesSort] = useState<'value' | 'default'>('default')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL_CFG}/api/soul/agent-config`)
        const d = await r.json()
        if (active) setItems(d.configs ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  function startEdit(cfg: AgentConfigRecord, e: React.MouseEvent) {
    e.stopPropagation()
    setEditDar(Number(cfg.dar_threshold))
    setEditLimit(cfg.skill_synthesize_daily_limit)
    let axes: Record<string, number> = {}
    try { axes = cfg.persona_axes ? JSON.parse(cfg.persona_axes) : {} } catch { /* */ }
    const numAxes: Record<string, number> = {}
    for (const [k, v] of Object.entries(axes)) {
      if (typeof v === 'number') numAxes[k] = v
    }
    setEditAxes(numAxes)
    setEditing(cfg.agent)
  }

  async function handleSave(agent: string, e: React.MouseEvent) {
    e.stopPropagation()
    setSaving(agent)
    try {
      const r = await fetch(`${SOUL_CFG}/api/soul/agent-config/${agent}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dar_threshold: editDar, skill_synthesize_daily_limit: editLimit, persona_axes: editAxes }),
      })
      const d = await r.json()
      if (d.ok) {
        setItems(prev => prev.map(c => c.agent === agent
          ? { ...c, dar_threshold: editDar, skill_synthesize_daily_limit: editLimit, persona_axes: JSON.stringify(editAxes) }
          : c
        ))
        setEditing(null)
      }
    } finally { setSaving(null) }
  }

  return (
    <div className="space-y-2">
      {loading && !items.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading agent configs…</div>
      )}
      <div className="flex items-center mb-1">
        <button onClick={() => setCfgSort(s => s === 'dar' ? 'agent' : 'dar')}
          className="ml-auto px-2 py-0.5 rounded text-[10px] border transition-colors"
          style={cfgSort === 'dar' ? { borderColor: '#f59e0b', color: '#f59e0b', backgroundColor: '#f59e0b18' } : { borderColor: '#111', color: '#333' }}>
          ↓ DAR
        </button>
      </div>
      {(cfgSort === 'dar'
        ? [...items].sort((a, b) => Number(b.dar_threshold) - Number(a.dar_threshold))
        : items
      ).map(cfg => {
        const color = AGENT_COLORS[cfg.agent] ?? '#666'
        const isExp = expanded === cfg.agent
        const isEdit = editing === cfg.agent
        let axes: Record<string, unknown> = {}
        try { axes = cfg.persona_axes ? JSON.parse(cfg.persona_axes) : {} } catch { /* */ }
        const darPct = Math.round(Number(isEdit ? editDar : cfg.dar_threshold) * 100)
        return (
          <div key={cfg.agent}
            className="rounded border border-[#0f0f0f] bg-[#050505] overflow-hidden hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}>
            <div className="px-3 py-2.5 cursor-pointer" onClick={() => !isEdit && setExpanded(x => x === cfg.agent ? null : cfg.agent)}>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-semibold" style={{ color }}>{cfg.agent}</span>
                <Sliders size={10} className="text-[#333]" />
                {!isEdit && (
                  <button onClick={e => startEdit(cfg, e)}
                    className="ml-1 text-[#333] hover:text-[#7c3aed] transition-colors">
                    <Pencil size={9} />
                  </button>
                )}
                <span className="text-[9px] text-[#333] ml-auto">
                  updated {new Date(cfg.updated_at).toLocaleDateString('es-PE', { month: 'short', day: 'numeric', year: 'numeric' })}
                </span>
              </div>
              {isEdit ? (
                <div className="space-y-2" onClick={e => e.stopPropagation()}>
                  <div>
                    <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">
                      DAR threshold {editDar.toFixed(2)}
                    </label>
                    <input type="range" min={0} max={1} step={0.01} value={editDar} onChange={e => setEditDar(Number(e.target.value))}
                      className="w-full accent-violet-500" />
                  </div>
                  <div>
                    <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">
                      Skill synth / day: {editLimit}
                    </label>
                    <input type="range" min={0} max={20} step={1} value={editLimit} onChange={e => setEditLimit(Number(e.target.value))}
                      className="w-full accent-violet-500" />
                  </div>
                  {Object.keys(editAxes).length > 0 && (
                    <div className="space-y-1.5 pt-1 border-t border-[#0d0d0d]">
                      <p className="text-[9px] text-[#333] uppercase tracking-wider">Persona Axes</p>
                      {Object.entries(editAxes).map(([k, v]) => (
                        <div key={k}>
                          <label className="text-[9px] text-[#444] block mb-0.5">
                            {k.replace(/_/g, ' ')}: {v.toFixed(2)}
                          </label>
                          <input type="range" min={0} max={1} step={0.01} value={v}
                            onChange={e => setEditAxes(prev => ({ ...prev, [k]: Number(e.target.value) }))}
                            className="w-full accent-violet-500" />
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2 pt-1">
                    <button onClick={e => { e.stopPropagation(); setEditing(null) }}
                      className="px-2 py-0.5 text-xs text-[#444] hover:text-[#666]">Cancel</button>
                    <button onClick={e => handleSave(cfg.agent, e)} disabled={saving === cfg.agent}
                      className="px-2 py-0.5 rounded bg-[#7c3aed] text-white text-[10px] font-medium hover:bg-[#6d28d9] disabled:opacity-50">
                      {saving === cfg.agent ? '…' : 'Save'}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-3 mb-2">
                  <div>
                    <p className="text-[9px] text-[#333] mb-0.5">DAR threshold</p>
                    <div className="flex items-center gap-1.5">
                      <div className="flex-1 h-1.5 bg-[#111] rounded overflow-hidden">
                        <div className="h-full rounded bg-[#7c3aed]" style={{ width: `${darPct}%` }} />
                      </div>
                      <span className="text-[9px] font-mono text-[#7c3aed]">{Number(cfg.dar_threshold).toFixed(2)}</span>
                    </div>
                  </div>
                  <div>
                    <p className="text-[9px] text-[#333] mb-0.5">skill synth / day</p>
                    <span className="text-[10px] font-mono text-[#aaa]">{cfg.skill_synthesize_daily_limit}</span>
                  </div>
                </div>
              )}
              {isExp && Object.keys(axes).length > 0 && (
                <div className="mt-2 pt-2 border-t border-[#0d0d0d]">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-[9px] text-[#333] uppercase tracking-wider flex-1">persona axes</p>
                    <button onClick={e => { e.stopPropagation(); setCfgAxesSort(s => s === 'value' ? 'default' : 'value') }}
                      className="px-1.5 py-0.5 rounded border text-[9px] transition-colors"
                      style={cfgAxesSort === 'value' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
                      ↓ val
                    </button>
                  </div>
                  <div className="space-y-1">
                    {(cfgAxesSort === 'value'
                      ? Object.entries(axes).sort(([, a], [, b]) => (typeof b === 'number' && typeof a === 'number') ? b - a : 0)
                      : Object.entries(axes)
                    ).map(([k, v]) => {
                      const numV = typeof v === 'number' ? v : null
                      return (
                        <div key={k} className="flex items-center gap-2">
                          <span className="text-[9px] text-[#444] w-32 flex-shrink-0">{k.replace(/_/g, ' ')}</span>
                          {numV !== null ? (
                            <>
                              <div className="flex-1 h-1 bg-[#111] rounded overflow-hidden">
                                <div className="h-full rounded bg-[#7c3aed80]" style={{ width: `${Math.min(numV, 1) * 100}%` }} />
                              </div>
                              <span className="text-[9px] font-mono text-[#444] w-8 text-right">{numV.toFixed(2)}</span>
                            </>
                          ) : (
                            <span className="text-[9px] text-[#555]">{String(v)}</span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <Sliders size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No agent configs
        </div>
      )}
    </div>
  )
}

interface SycophancyEval {
  agent: string
  eval_date: string
  pi_score: number
  tests_run: number
  sycophantic_count: number
  independent_count: number
  dissent_count: number
  eval_method: string
  notes: string | null
  created_at: string
}

function SycophancyPanel() {
  const [items, setItems] = useState<SycophancyEval[]>([])
  const [loading, setLoading] = useState(true)
  const [spSort, setSpSort] = useState<'integrity' | 'default'>('default')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/sycophancy-eval`)
        const d = await r.json()
        if (active) setItems(d.evals ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const sortedEvals = spSort === 'integrity'
    ? [...items].sort((a, b) => Number(a.pi_score) - Number(b.pi_score))
    : items

  return (
    <div className="space-y-3">
      {loading && !items.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading integrity scores…</div>
      )}
      <div className="flex items-center justify-end mb-1">
        <button onClick={() => setSpSort(s => s === 'integrity' ? 'default' : 'integrity')}
          className="flex items-center gap-1 px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={spSort === 'integrity' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ integrity
        </button>
      </div>
      {sortedEvals.map(ev => {
        const color = AGENT_COLORS[ev.agent] ?? '#666'
        const integrity = 1 - Number(ev.pi_score)
        const intColor = integrity >= 0.95 ? '#10b981' : integrity >= 0.80 ? '#f59e0b' : '#ef4444'
        const total = ev.tests_run || 1
        return (
          <div key={ev.agent + ev.eval_date}
            className="rounded-lg border border-[#0f0f0f] bg-[#050505] px-3 py-2.5"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold" style={{ color }}>{ev.agent}</span>
              <BadgeCheck size={11} style={{ color: intColor }} />
              <span className="text-[9px] font-mono ml-auto" style={{ color: intColor }}>
                {(integrity * 100).toFixed(1)}% integrity
              </span>
              <span className="text-[9px] text-[#333]">{ev.eval_date}</span>
            </div>
            {/* Integrity bar */}
            <div className="mb-2">
              <div className="h-2 bg-[#111] rounded overflow-hidden">
                <div className="h-full rounded transition-all" style={{ width: `${integrity * 100}%`, backgroundColor: intColor }} />
              </div>
            </div>
            {/* Breakdown stacked bar */}
            <div className="flex gap-px h-1.5 rounded overflow-hidden mb-2">
              <div title={`Independent: ${ev.independent_count}`} style={{ width: `${ev.independent_count / total * 100}%`, backgroundColor: '#10b981' }} />
              <div title={`Dissent: ${ev.dissent_count}`} style={{ width: `${ev.dissent_count / total * 100}%`, backgroundColor: '#f59e0b' }} />
              <div title={`Sycophantic: ${ev.sycophantic_count}`} style={{ width: `${ev.sycophantic_count / total * 100}%`, backgroundColor: '#ef4444' }} />
            </div>
            <div className="flex gap-4 text-[9px]">
              <span><span className="text-[#10b981]">■</span> independent {ev.independent_count}</span>
              <span><span className="text-[#f59e0b]">■</span> dissent {ev.dissent_count}</span>
              <span><span className="text-[#ef4444]">■</span> sycophantic {ev.sycophantic_count}</span>
              <span className="ml-auto text-[#333]">{ev.tests_run} tests · {ev.eval_method}</span>
            </div>
            {ev.notes && (
              <p className="text-[9px] text-[#2a2a2a] mt-1.5 italic">{ev.notes}</p>
            )}
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <BadgeCheck size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No integrity evaluations
        </div>
      )}

      {/* Sycophancy raw log */}
      <SycophancyLogSection />
    </div>
  )
}

interface SycophancyLogEntry {
  id: number
  agent: string
  evaluation: string
  risk_score: number
  flags: string | string[]
  evidence_demanded: boolean
  user_was_wrong: boolean | null
  agent_corrected: boolean | null
  session_id: string
  created_at: string
}

const _SYCO_AGENTS = ['all', 'ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM'] as const

function SycophancyLogSection() {
  const [items, setItems] = useState<SycophancyLogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [agentFilter, setAgentFilter] = useState('all')
  const [sycoSort, setSycoSort] = useState<'risk' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      try {
        const r = await fetch(`${SOUL}/api/soul/sycophancy-log?limit=100`)
        const d = await r.json()
        if (active) setItems(d.entries ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  if (loading || !items.length) return null

  const filtered = agentFilter === 'all' ? items : items.filter(e => e.agent === agentFilter)
  const avgRisk = filtered.length ? filtered.reduce((s, e) => s + Number(e.risk_score), 0) / filtered.length : 0
  const highRisk = filtered.filter(e => Number(e.risk_score) >= 0.7).length
  const corrected = filtered.filter(e => e.agent_corrected === true).length
  const corrRate = filtered.length ? corrected / filtered.length : 0

  return (
    <div className="mt-4 space-y-2">
      <p className="flex items-center gap-1.5 text-[9px] text-[#333] uppercase tracking-wider">
        <ScrollText size={9} /> Raw Log ({items.length})
      </p>
      {/* agent filter */}
      <div className="flex items-center gap-1 flex-wrap">
        {_SYCO_AGENTS.map(a => (
          <button key={a} onClick={() => setAgentFilter(a)}
            className={`text-[9px] px-1.5 py-0.5 rounded transition-colors ${
              agentFilter === a ? 'bg-[#1a1a1a] text-[#e5e5e5]' : 'text-[#333] hover:text-[#666]'
            }`}
            style={agentFilter === a && a !== 'all' ? { color: AGENT_COLORS[a] ?? '#e5e5e5' } : {}}>
            {a}
          </button>
        ))}
      </div>
      {/* risk stats strip */}
      <div className="flex items-center gap-4 rounded bg-[#050505] border border-[#0a0a0a] px-3 py-2">
        <div className="flex flex-col items-center">
          <span className="text-[9px] text-[#333]">avg risk</span>
          <span className="text-xs font-mono"
            style={{ color: avgRisk >= 0.7 ? '#ef4444' : avgRisk >= 0.4 ? '#f59e0b' : '#10b981' }}>
            {Math.round(avgRisk * 100)}%
          </span>
        </div>
        <div className="flex flex-col items-center">
          <span className="text-[9px] text-[#333]">high risk</span>
          <span className="text-xs font-mono text-[#ef4444]">{highRisk}</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="text-[9px] text-[#333]">correction rate</span>
          <span className="text-xs font-mono"
            style={{ color: corrRate >= 0.7 ? '#10b981' : corrRate >= 0.4 ? '#f59e0b' : '#ef4444' }}>
            {Math.round(corrRate * 100)}%
          </span>
        </div>
        <span className="text-[9px] text-[#222]">{filtered.length} entries</span>
        <button onClick={() => setSycoSort(s => s === 'risk' ? 'date' : 'risk')}
          className="ml-auto px-2 py-0.5 rounded text-[9px] border transition-colors"
          style={sycoSort === 'risk' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ risk
        </button>
      </div>
      {(sycoSort === 'risk' ? [...filtered].sort((a, b) => Number(b.risk_score) - Number(a.risk_score)) : filtered).map(e => {
        const riskColor = Number(e.risk_score) >= 0.7 ? '#ef4444' : Number(e.risk_score) >= 0.4 ? '#f59e0b' : '#10b981'
        const agColor = AGENT_COLORS[e.agent] ?? '#666'
        const flags: string[] = typeof e.flags === 'string' ? JSON.parse(e.flags || '[]') : (e.flags ?? [])
        return (
          <div key={e.id} className="rounded border border-[#0a0a0a] bg-[#030303] px-3 py-2">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-medium" style={{ color: agColor }}>{e.agent}</span>
              <span className="text-[9px] px-1 rounded" style={{ backgroundColor: riskColor + '20', color: riskColor }}>{e.evaluation}</span>
              <span className="text-[9px] font-mono ml-auto" style={{ color: riskColor }}>{Math.round(Number(e.risk_score) * 100)}%</span>
            </div>
            {flags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {flags.map((f, i) => (
                  <span key={i} className="text-[8px] px-1 rounded bg-[#111] text-[#333]">{f}</span>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

interface TokenBudgetRecord {
  agent: string
  daily_budget_input: number
  daily_budget_output: number
  consumed_today_input: number
  consumed_today_output: number
  last_reset_at: string
}

function TokenBudgetPanel() {
  const [items, setItems] = useState<TokenBudgetRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [budSort, setBudSort] = useState<'usage' | 'output' | 'agent'>('agent')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/token-budget`)
        const d = await r.json()
        if (active) setItems(d.budgets ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const fmtM = (n: number) => n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(0)}k` : `${n}`

  return (
    <div className="space-y-3">
      {loading && !items.length && <div className="text-xs text-[#444] py-6 text-center">Loading…</div>}
      <div className="flex items-center gap-1 mb-1">
        <button onClick={() => setBudSort(s => s === 'usage' ? 'output' : s === 'output' ? 'agent' : 'usage')}
          className="ml-auto px-2 py-0.5 rounded text-[10px] border transition-colors"
          style={budSort === 'usage' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : budSort === 'output' ? { borderColor: '#f59e0b', color: '#f59e0b', backgroundColor: '#f59e0b18' } : { borderColor: '#111', color: '#333' }}>
          {budSort === 'usage' ? '↓ input%' : budSort === 'output' ? '↓ output%' : '↓ sort'}
        </button>
      </div>
      {(budSort === 'usage'
        ? [...items].sort((a, b) => {
            const pctA = a.daily_budget_input > 0 ? a.consumed_today_input / a.daily_budget_input : 0
            const pctB = b.daily_budget_input > 0 ? b.consumed_today_input / b.daily_budget_input : 0
            return pctB - pctA
          })
        : budSort === 'output'
        ? [...items].sort((a, b) => {
            const pctA = a.daily_budget_output > 0 ? a.consumed_today_output / a.daily_budget_output : 0
            const pctB = b.daily_budget_output > 0 ? b.consumed_today_output / b.daily_budget_output : 0
            return pctB - pctA
          })
        : items
      ).map(b => {
        const agColor = AGENT_COLORS[b.agent] ?? '#666'
        const inPct = b.daily_budget_input > 0 ? b.consumed_today_input / b.daily_budget_input : 0
        const outPct = b.daily_budget_output > 0 ? b.consumed_today_output / b.daily_budget_output : 0
        const inColor = inPct >= 0.8 ? '#ef4444' : inPct >= 0.5 ? '#f59e0b' : '#10b981'
        const outColor = outPct >= 0.8 ? '#ef4444' : outPct >= 0.5 ? '#f59e0b' : '#10b981'
        return (
          <div key={b.agent} className="rounded-lg border border-[#0f0f0f] bg-[#050505] px-3 py-2.5"
            style={{ borderLeftColor: agColor, borderLeftWidth: 2 }}>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold" style={{ color: agColor }}>{b.agent}</span>
              <Coins size={10} className="text-[#f59e0b]" />
              <span className="text-[9px] text-[#333] ml-auto">reset {new Date(b.last_reset_at).toLocaleDateString('es-PE')}</span>
            </div>
            <div className="space-y-1.5">
              {[
                { label: 'Input tokens', consumed: b.consumed_today_input, budget: b.daily_budget_input, pct: inPct, color: inColor },
                { label: 'Output tokens', consumed: b.consumed_today_output, budget: b.daily_budget_output, pct: outPct, color: outColor },
              ].map(({ label, consumed, budget, pct, color }) => (
                <div key={label}>
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="text-[9px] text-[#444]">{label}</span>
                    <span className="text-[9px] font-mono" style={{ color }}>
                      {fmtM(consumed)} / {fmtM(budget)}
                    </span>
                  </div>
                  <div className="h-1.5 bg-[#111] rounded-full overflow-hidden">
                    <div className="h-full rounded-full transition-all" style={{ width: `${pct * 100}%`, backgroundColor: color }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">No budget data</div>
      )}
    </div>
  )
}

interface ToolUsage {
  id: number
  agent: string
  session_id: string
  tool_name: string
  call_count: number
  estimated_tokens: number
  budget_warn: boolean
  budget_block: boolean
  created_at: string
}

interface ToolLimit {
  id: number
  agent: string | null
  tool_pattern: string
  max_calls: number
  max_tokens: number
  warn_at_pct: number
  active: boolean
}

function ToolBudgetPanel() {
  const [usage, setUsage] = useState<ToolUsage[]>([])
  const [limits, setLimits] = useState<ToolLimit[]>([])
  const [loading, setLoading] = useState(true)
  const [toolUsageSort, setToolUsageSort] = useState<'calls' | 'tokens' | 'agent'>('agent')
  const [limitSort, setLimitSort] = useState<'max' | 'agent'>('agent')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/tool-budget`)
        const d = await r.json()
        if (active) { setUsage(d.usage ?? []); setLimits(d.limits ?? []) }
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  return (
    <div className="space-y-4">
      {loading && <div className="text-xs text-[#444] py-6 text-center">Loading…</div>}

      {/* Limits */}
      {limits.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1">
            <p className="text-[9px] text-[#333] uppercase tracking-wider flex items-center gap-1 flex-1">
              <Wrench size={9} /> Tool Limits ({limits.length})
            </p>
            <button onClick={() => setLimitSort(s => s === 'max' ? 'agent' : 'max')}
              className="px-2 py-0.5 rounded border text-[9px] transition-colors"
              style={limitSort === 'max' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
              ↓ max
            </button>
          </div>
          {(limitSort === 'max'
            ? [...limits].sort((a, b) => b.max_calls - a.max_calls)
            : limits
          ).map(l => (
            <div key={l.id} className="flex items-center gap-2 rounded bg-[#050505] border border-[#0a0a0a] px-3 py-2">
              <span className="text-xs font-mono text-[#e5e5e5]">{l.tool_pattern}</span>
              <span className={`text-[9px] px-1 rounded ml-1 ${l.active ? 'bg-[#10b98118] text-[#10b981]' : 'bg-[#111] text-[#333]'}`}>
                {l.active ? 'active' : 'inactive'}
              </span>
              <span className="text-[9px] text-[#444] ml-auto">max {l.max_calls} calls</span>
              <span className="text-[9px] text-[#444]">warn @{l.warn_at_pct}%</span>
              {l.agent && <span className="text-[9px]" style={{ color: AGENT_COLORS[l.agent] ?? '#555' }}>{l.agent}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Usage */}
      {usage.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1">
            <p className="text-[9px] text-[#333] uppercase tracking-wider flex items-center gap-1">
              <Activity size={9} /> Session Usage ({usage.length})
            </p>
            <div className="ml-auto flex items-center gap-1">
              <button onClick={() => setToolUsageSort(s => s === 'calls' ? 'tokens' : s === 'tokens' ? 'agent' : 'calls')}
                className="px-2 py-0.5 rounded text-[10px] border transition-colors"
                style={toolUsageSort === 'calls' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : toolUsageSort === 'tokens' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#111', color: '#333' }}>
                {toolUsageSort === 'calls' ? '↓ calls' : toolUsageSort === 'tokens' ? '↓ tokens' : '↓ sort'}
              </button>
            </div>
          </div>
          {(toolUsageSort === 'calls'
            ? [...usage].sort((a, b) => b.call_count - a.call_count)
            : toolUsageSort === 'tokens'
            ? [...usage].sort((a, b) => b.estimated_tokens - a.estimated_tokens)
            : usage
          ).map(u => {
            const agColor = AGENT_COLORS[u.agent] ?? '#666'
            return (
              <div key={u.id} className="rounded bg-[#050505] border border-[#0a0a0a] px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-[#e5e5e5]">{u.tool_name}</span>
                  {u.budget_warn && <span className="text-[9px] text-[#f59e0b]">⚠ warn</span>}
                  {u.budget_block && <span className="text-[9px] text-[#ef4444]">⛔ blocked</span>}
                  <span className="text-[10px] font-medium ml-auto" style={{ color: agColor }}>{u.agent}</span>
                </div>
                <div className="flex items-center gap-3 mt-0.5 text-[9px] text-[#444]">
                  <span>{u.call_count} calls</span>
                  <span>~{u.estimated_tokens.toLocaleString()} tokens</span>
                  <span className="ml-auto text-[#2a2a2a]">{u.session_id}</span>
                </div>
              </div>
            )
          })}
        </div>
      )}
      {!loading && !usage.length && !limits.length && (
        <div className="text-xs text-[#333] py-8 text-center">No tool budget data</div>
      )}
    </div>
  )
}

interface RawTrustScore {
  id: number
  agent: string
  peer: string
  trust_score: number
  interaction_count: number
  positive_count: number
  negative_count: number
  trust_events: string | null
  updated_at: string
}

function RawTrustPanel() {
  const [items, setItems] = useState<RawTrustScore[]>([])
  const [loading, setLoading] = useState(true)
  const [trustSort, setTrustSort] = useState<'score' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/raw-trust-scores`)
        const d = await r.json()
        if (active) setItems(d.scores ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const visibleTrust = trustSort === 'score' ? [...items].sort((a, b) => b.trust_score - a.trust_score) : items

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 pb-1">
        <span className="text-[10px] text-[#333]">{items.length} scores</span>
        <button onClick={() => setTrustSort(s => s === 'score' ? 'date' : 'score')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
          style={trustSort === 'score' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ trust score
        </button>
      </div>
      {loading && <div className="text-xs text-[#444] py-6 text-center">Loading…</div>}
      {visibleTrust.map(s => {
        const agColor = AGENT_COLORS[s.agent] ?? '#666'
        const peerColor = AGENT_COLORS[s.peer] ?? '#888'
        const pct = Math.round(s.trust_score * 100)
        const tColor = pct >= 80 ? '#10b981' : pct >= 60 ? '#f59e0b' : '#ef4444'
        const events: { ts: string; delta: number; reason: string }[] = s.trust_events
          ? (() => { try { return JSON.parse(s.trust_events!); } catch { return [] } })()
          : []
        return (
          <div key={s.id} className="rounded-lg border border-[#0f0f0f] bg-[#050505] px-3 py-2.5"
            style={{ borderLeftColor: agColor, borderLeftWidth: 2 }}>
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-sm font-semibold" style={{ color: agColor }}>{s.agent}</span>
              <span className="text-[9px] text-[#444]">→</span>
              <span className="text-sm font-medium" style={{ color: peerColor }}>{s.peer}</span>
              <Star size={9} className="ml-auto" style={{ color: tColor }} />
              <span className="text-[10px] font-mono" style={{ color: tColor }}>{pct}%</span>
            </div>
            <div className="h-1.5 bg-[#111] rounded-full overflow-hidden mb-1.5">
              <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: tColor }} />
            </div>
            <div className="flex gap-4 text-[9px] text-[#444]">
              <span>{s.interaction_count} interactions</span>
              <span className="text-[#10b981]">+{s.positive_count}</span>
              <span className="text-[#ef4444]">-{s.negative_count}</span>
              <span className="ml-auto text-[#2a2a2a]">{new Date(s.updated_at).toLocaleDateString('es-PE')}</span>
            </div>
            {events.length > 0 && (
              <div className="mt-1.5 space-y-0.5">
                {events.slice(-3).map((e, i) => (
                  <div key={i} className="flex items-center gap-1.5 text-[8px]">
                    <span className={e.delta >= 0 ? 'text-[#10b981]' : 'text-[#ef4444]'}>
                      {e.delta >= 0 ? '+' : ''}{e.delta}
                    </span>
                    <span className="text-[#333] flex-1 truncate">{e.reason}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">No raw trust scores</div>
      )}
    </div>
  )
}

interface DenialRecord {
  id: number
  agent: string
  session_id: string
  consecutive_denials: number
  total_denials: number
  last_denial: string
}

function DenialTrackingPanel() {
  const [items, setItems] = useState<DenialRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [denialSort, setDenialSort] = useState<'total' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/denial-tracking`)
        const d = await r.json()
        if (active) setItems(d.denials ?? [])
      } catch { if (active) setItems([]) }
      finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const maxTotal = Math.max(1, ...items.map(i => i.total_denials))
  const visibleDenials = denialSort === 'total' ? [...items].sort((a, b) => b.total_denials - a.total_denials) : items

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 pb-1">
        <span className="text-[10px] text-[#333]">{items.length} records</span>
        <button onClick={() => setDenialSort(s => s === 'total' ? 'date' : 'total')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
          style={denialSort === 'total' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ most denials
        </button>
      </div>
      {loading && <div className="text-xs text-[#444] py-4 text-center animate-pulse">Loading…</div>}
      {visibleDenials.map(rec => {
        const color = AGENT_COLORS[rec.agent] ?? '#666'
        const pct = (rec.total_denials / maxTotal) * 100
        return (
          <div key={rec.id} className="rounded-lg border border-[#0f0f0f] bg-[#050505] p-3"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs font-medium" style={{ color }}>{rec.agent}</span>
              <div className="flex items-center gap-2">
                {rec.consecutive_denials > 0 && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-[#ef444420] text-[#ef4444]">
                    {rec.consecutive_denials} consecutive
                  </span>
                )}
                <span className="text-[9px] text-[#333] font-mono">
                  last {new Date(rec.last_denial).toLocaleDateString('es-PE')}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[9px] text-[#444] w-12">total</span>
              <div className="flex-1 h-1.5 bg-[#111] rounded">
                <div className="h-full rounded transition-all"
                  style={{ width: `${pct}%`, backgroundColor: color + '88' }} />
              </div>
              <span className="text-[9px] font-mono text-[#555] w-6 text-right">{rec.total_denials}</span>
            </div>
            <div className="text-[9px] text-[#2a2a2a] font-mono truncate">{rec.session_id}</div>
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <Ban size={24} className="mx-auto mb-2 text-[#1a1a1a]" />
          No denial records
        </div>
      )}
    </div>
  )
}

// ── Comms Matrix Panel ───────────────────────────────────────────────────────
interface CommsRow { from: string; to: string; cnt: number }

const COMMS_NODE_COLORS: Record<string, string> = {
  ...AGENT_COLORS,
  William: '#f59e0b',
  Henry: '#84cc16',
  equipo: '#64748b',
  SEAL_CRON: '#94a3b8',
}

function CommsMatrixPanel() {
  const [matrix, setMatrix] = useState<CommsRow[]>([])
  const [agents, setAgents] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [hovered, setHovered] = useState<CommsRow | null>(null)
  const [commsSort, setCommsSort] = useState<'msgs' | 'alpha'>('msgs')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/comms-matrix`)
        const d = await r.json()
        if (active) { setMatrix(d.matrix ?? []); setAgents(d.agents ?? []) }
      } catch { if (active) { setMatrix([]); setAgents([]) } }
      finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const maxCnt = Math.max(1, ...matrix.map(r => r.cnt))

  const sortedAgents = commsSort === 'msgs'
    ? [...agents].sort((a, b) => {
        const sumA = matrix.filter(r => r.from === a).reduce((n, r) => n + r.cnt, 0)
        const sumB = matrix.filter(r => r.from === b).reduce((n, r) => n + r.cnt, 0)
        return sumB - sumA
      })
    : [...agents].sort((a, b) => a.localeCompare(b))

  function getCell(from: string, to: string): CommsRow | undefined {
    return matrix.find(r => r.from === from && r.to === to)
  }

  if (loading) return <div className="text-xs text-[#444] py-8 text-center animate-pulse">Loading matrix…</div>
  if (!matrix.length) return <div className="text-xs text-[#333] py-6 text-center">No comms data</div>

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] text-[#333] flex-1">{matrix.length} communication pairs · {agents.length} nodes</span>
        <button onClick={() => setCommsSort(s => s === 'msgs' ? 'alpha' : 'msgs')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={commsSort === 'msgs' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ msgs sent
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="text-[9px] border-collapse">
          <thead>
            <tr>
              <th className="text-[#222] font-normal text-right pr-2 py-1 w-20">from↓ to→</th>
              {sortedAgents.map(to => (
                <th key={to} className="text-center px-1 py-1 w-14 font-medium"
                  style={{ color: COMMS_NODE_COLORS[to] ?? '#666' }}>
                  <div className="truncate max-w-[50px]">{to}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedAgents.map(from => (
              <tr key={from} className="hover:bg-[#050505]">
                <td className="text-right pr-2 py-0.5 font-medium"
                  style={{ color: COMMS_NODE_COLORS[from] ?? '#666' }}>{from}</td>
                {sortedAgents.map(to => {
                  if (from === to) return (
                    <td key={to} className="px-1 py-0.5 text-center">
                      <div className="w-10 h-5 bg-[#0a0a0a] rounded-sm mx-auto" />
                    </td>
                  )
                  const cell = getCell(from, to)
                  if (!cell) return (
                    <td key={to} className="px-1 py-0.5 text-center text-[#111]">—</td>
                  )
                  const intensity = Math.log1p(cell.cnt) / Math.log1p(maxCnt)
                  const fromColor = COMMS_NODE_COLORS[from] ?? '#888'
                  return (
                    <td key={to} className="px-1 py-0.5 text-center cursor-pointer"
                      onMouseEnter={() => setHovered(cell)}
                      onMouseLeave={() => setHovered(null)}>
                      <div className="w-10 h-5 rounded-sm mx-auto flex items-center justify-center text-[8px] font-mono transition-opacity hover:opacity-80"
                        style={{ backgroundColor: fromColor + Math.round(intensity * 200).toString(16).padStart(2, '0'), color: intensity > 0.5 ? '#fff' : '#888' }}>
                        {cell.cnt >= 1000 ? `${(cell.cnt / 1000).toFixed(1)}k` : cell.cnt}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hovered && (
        <div className="bg-[#050505] border border-[#1a1a1a] rounded p-2 text-[10px] flex items-center gap-2">
          <span style={{ color: COMMS_NODE_COLORS[hovered.from] ?? '#888' }}>{hovered.from}</span>
          <span className="text-[#333]">→</span>
          <span style={{ color: COMMS_NODE_COLORS[hovered.to] ?? '#888' }}>{hovered.to}</span>
          <span className="text-[#555] ml-2">{hovered.cnt.toLocaleString()} messages</span>
        </div>
      )}
    </div>
  )
}

const DEBATE_AGENTS_LIST = ['ALICE', 'JARVIS', 'NEXUS', 'DUM', 'ADA']

function NewDebateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [topic, setTopic] = useState('')
  const [agents, setAgents] = useState<string[]>(['ALICE', 'JARVIS'])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  function toggleAgent(a: string) {
    setAgents(prev => prev.includes(a) ? prev.filter(x => x !== a) : [...prev, a])
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!topic.trim()) { setError('Topic required'); return }
    setSaving(true); setError('')
    try {
      const r = await fetch(`${SOUL}/api/soul/governance/debate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic: topic.trim(), agents_involved: agents }),
      })
      const d = await r.json()
      if (d.ok) { onCreated(); onClose() }
      else setError(d.error ?? 'Failed')
    } catch { setError('Network error') } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={handleSubmit}
        className="bg-[#050505] border border-[#1a1a1a] rounded-xl p-5 w-full max-w-sm space-y-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-sm font-medium text-[#e5e5e5]">New Debate</span>
          <button type="button" onClick={onClose} className="text-[#333] hover:text-[#888]"><X size={14} /></button>
        </div>
        <div>
          <label className="text-[9px] text-[#444] mb-1 block">Topic *</label>
          <input value={topic} onChange={e => setTopic(e.target.value)} placeholder="Debate topic…"
            className="w-full bg-[#0a0a0a] border border-[#1a1a1a] rounded px-2 py-1.5 text-xs text-[#ccc] focus:outline-none focus:border-[#7c3aed]" />
        </div>
        <div>
          <label className="text-[9px] text-[#444] mb-1.5 block">Agents Involved</label>
          <div className="flex flex-wrap gap-1.5">
            {DEBATE_AGENTS_LIST.map(a => {
              const sel = agents.includes(a)
              const color = AGENT_COLORS[a] ?? '#666'
              return (
                <button key={a} type="button" onClick={() => toggleAgent(a)}
                  className="px-2 py-0.5 rounded text-[9px] border transition-colors"
                  style={sel ? { borderColor: color, color, backgroundColor: color + '18' } : { borderColor: '#1a1a1a', color: '#444' }}>
                  {a}
                </button>
              )
            })}
          </div>
        </div>
        {error && <p className="text-[10px] text-red-500">{error}</p>}
        <button type="submit" disabled={saving}
          className="w-full py-2 rounded bg-[#7c3aed] text-white text-xs font-medium hover:bg-[#6d28d9] disabled:opacity-50">
          {saving ? 'Creating…' : 'Create Debate'}
        </button>
      </form>
    </div>
  )
}

interface DebateLogEntry {
  id: number
  topic: string
  agents_involved: string[]
  trigger_type: string
  rounds_completed: number
  consensus_reached: boolean
  outcome: string | null
  synthesis: string | null
  cost_estimate: number | null
  created_at: string
  completed_at: string | null
}

const _DEBATE_LOG_AGENTS = ['all', 'ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM'] as const

function DebateLogPanel() {
  const [items, setItems] = useState<DebateLogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [agentFilter, setAgentFilter] = useState('all')
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [debSort, setDebSort] = useState<'rounds' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/debate-log?limit=100`)
        const d = await r.json()
        if (active) setItems(d.debates ?? [])
      } catch { if (active) setItems([]) }
      finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  function toggle(id: number) {
    setExpanded(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  const filtered = agentFilter === 'all'
    ? items
    : items.filter(e => e.agents_involved.includes(agentFilter))

  const consensusCount = filtered.filter(e => e.consensus_reached).length
  const visibleDebates = debSort === 'rounds' ? [...filtered].sort((a, b) => (b.rounds_completed ?? 0) - (a.rounds_completed ?? 0)) : filtered

  return (
    <div className="space-y-3">
      {/* stats strip */}
      {items.length > 0 && (
        <div className="flex items-center gap-4 rounded bg-[#050505] border border-[#0a0a0a] px-3 py-2">
          <div className="flex flex-col items-center">
            <span className="text-[9px] text-[#333]">total</span>
            <span className="text-xs font-mono text-[#e5e5e5]">{filtered.length}</span>
          </div>
          <div className="flex flex-col items-center">
            <span className="text-[9px] text-[#333]">consensus</span>
            <span className="text-xs font-mono text-[#10b981]">{consensusCount}</span>
          </div>
          <div className="flex flex-col items-center">
            <span className="text-[9px] text-[#333]">no consensus</span>
            <span className="text-xs font-mono text-[#ef4444]">{filtered.length - consensusCount}</span>
          </div>
        </div>
      )}
      {/* agent filter */}
      <div className="flex items-center gap-1 flex-wrap">
        {_DEBATE_LOG_AGENTS.map(a => (
          <button key={a} onClick={() => setAgentFilter(a)}
            className={`text-[9px] px-1.5 py-0.5 rounded transition-colors ${
              agentFilter === a ? 'bg-[#1a1a1a] text-[#e5e5e5]' : 'text-[#333] hover:text-[#666]'
            }`}
            style={agentFilter === a && a !== 'all' ? { color: AGENT_COLORS[a] ?? '#e5e5e5' } : {}}>
            {a}
          </button>
        ))}
        {loading && <span className="text-[9px] text-[#333] animate-pulse ml-1">loading…</span>}
        <button onClick={() => setDebSort(s => s === 'rounds' ? 'date' : 'rounds')}
          className="ml-auto px-2 py-0.5 rounded text-[9px] border transition-colors"
          style={debSort === 'rounds' ? { borderColor: '#f59e0b', color: '#f59e0b', backgroundColor: '#f59e0b18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ rounds
        </button>
      </div>
      {/* debate cards */}
      {visibleDebates.map(e => {
        const isExp = expanded.has(e.id)
        const consensusColor = e.consensus_reached ? '#10b981' : '#ef4444'
        return (
          <div key={e.id} className="rounded border border-[#0f0f0f] bg-[#050505] px-3 py-2.5 cursor-pointer hover:border-[#1a1a1a]"
            style={{ borderLeftColor: consensusColor, borderLeftWidth: 2 }}
            onClick={() => toggle(e.id)}>
            <div className="flex items-center gap-2 mb-1.5 flex-wrap">
              <span className="text-xs text-[#e5e5e5] font-medium flex-1 min-w-0 truncate">{e.topic}</span>
              <span className="text-[9px] px-1 py-0.5 rounded"
                style={{ backgroundColor: consensusColor + '20', color: consensusColor }}>
                {e.consensus_reached ? '✓ consensus' : '✗ no consensus'}
              </span>
            </div>
            <div className="flex items-center gap-2 flex-wrap mb-1">
              {e.agents_involved.map(a => (
                <span key={a} className="text-[9px] px-1 py-0.5 rounded"
                  style={{ backgroundColor: (AGENT_COLORS[a] ?? '#666') + '20', color: AGENT_COLORS[a] ?? '#666' }}>
                  {a}
                </span>
              ))}
              <span className="text-[9px] text-[#333] ml-auto">{e.rounds_completed} rounds</span>
              <span className="text-[9px] text-[#222]">{e.trigger_type}</span>
            </div>
            {isExp && (
              <div className="mt-2 space-y-1.5 border-t border-[#0d0d0d] pt-2">
                {e.outcome && (
                  <p className="text-xs text-[#666] leading-snug"><span className="text-[9px] text-[#333] uppercase mr-1">outcome</span>{e.outcome}</p>
                )}
                {e.synthesis && (
                  <p className="text-xs text-[#555] leading-snug italic"><span className="text-[9px] text-[#333] uppercase mr-1 not-italic">synthesis</span>{e.synthesis}</p>
                )}
                {e.cost_estimate != null && (
                  <p className="text-[9px] text-[#333]">cost est. {e.cost_estimate}</p>
                )}
                <p className="text-[9px] text-[#222]">{new Date(e.created_at).toLocaleString('es-PE')}</p>
              </div>
            )}
          </div>
        )
      })}
      {!loading && !filtered.length && (
        <div className="text-xs text-[#333] py-8 text-center">No debates found</div>
      )}
    </div>
  )
}

interface PersonRelationship { agent: string; person: string; trust_level: number; communication_style: string; dynamic: string; interaction_count: number; updated_at: string | null }

function RelationshipsExtPanel() {
  const [rels, setRels] = useState<PersonRelationship[]>([])
  const [loading, setLoading] = useState(true)
  const [relExtSort, setRelExtSort] = useState<'trust' | 'date'>('date')

  useEffect(() => {
    let active = true
    fetch(`${SOUL}/api/soul/agent-relationships-ext`)
      .then(r => r.json())
      .then(d => { if (active) setRels(d.relationships ?? []) })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  if (loading) return <div className="text-xs text-[#444] py-8 text-center">Loading…</div>
  if (!rels.length) return <div className="text-xs text-[#333] py-8 text-center">No person relationships</div>

  function trustColor(t: number) {
    if (t >= 0.85) return '#10b981'
    if (t >= 0.7) return '#f59e0b'
    return '#ef4444'
  }

  const sortedRels = relExtSort === 'trust' ? [...rels].sort((a, b) => b.trust_level - a.trust_level) : rels
  const byAgent: Record<string, PersonRelationship[]> = {}
  sortedRels.forEach(r => { if (!byAgent[r.agent]) byAgent[r.agent] = []; byAgent[r.agent].push(r) })

  return (
    <div className="space-y-3">
      <div className="flex items-center mb-1">
        <button onClick={() => setRelExtSort(s => s === 'trust' ? 'date' : 'trust')}
          className="ml-auto px-2 py-0.5 rounded text-[10px] border transition-colors"
          style={relExtSort === 'trust' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#111', color: '#333' }}>
          ↓ trust
        </button>
      </div>
      {Object.entries(byAgent).map(([ag, agRels]) => (
        <div key={ag} className="rounded border border-[#111] bg-[#050505] p-3"
          style={{ borderLeftColor: AGENT_COLORS[ag] ?? '#333', borderLeftWidth: 2 }}>
          <p className="text-xs font-medium mb-2" style={{ color: AGENT_COLORS[ag] ?? '#888' }}>{ag}</p>
          <div className="space-y-2.5">
            {agRels.map(rel => (
              <div key={rel.person} className="pl-2 border-l border-[#1a1a1a]">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs text-[#ccc] font-medium">{rel.person}</span>
                  <span className="text-[9px] font-mono ml-auto" style={{ color: trustColor(rel.trust_level) }}>
                    trust {(rel.trust_level * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="h-1.5 bg-[#0d0d0d] rounded-full overflow-hidden mb-1.5">
                  <div className="h-full rounded-full" style={{ width: `${rel.trust_level * 100}%`, backgroundColor: trustColor(rel.trust_level) }} />
                </div>
                <p className="text-[10px] text-[#888] italic mb-1">"{rel.dynamic}"</p>
                <div className="flex items-center gap-2">
                  <span className="text-[9px] text-[#444]">style: {rel.communication_style}</span>
                  <span className="text-[9px] text-[#333] ml-auto">{rel.interaction_count} interactions</span>
                </div>
                {rel.updated_at && (
                  <p className="text-[9px] text-[#333] mt-0.5">updated {rel.updated_at.slice(0, 10)}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Agent Capabilities Panel (spec §3 — 13 toggles, risk tiers) ─────────────

const CAP_CATEGORIES: { label: string; caps: { key: string; label: string; risk: 'safe' | 'mod' | 'high' }[] }[] = [
  { label: 'System', caps: [
    { key: 'cap_shell_commands', label: 'Shell commands', risk: 'high' },
    { key: 'cap_git',            label: 'Git operations', risk: 'mod'  },
  ]},
  { label: 'Files', caps: [
    { key: 'cap_read_files',  label: 'Read files',  risk: 'safe' },
    { key: 'cap_write_files', label: 'Write files', risk: 'mod'  },
  ]},
  { label: 'Vision', caps: [
    { key: 'cap_screen_capture', label: 'Screen capture', risk: 'mod'  },
    { key: 'cap_camera',         label: 'Camera',         risk: 'high' },
  ]},
  { label: 'Web', caps: [
    { key: 'cap_web_search',     label: 'Web search',     risk: 'safe' },
    { key: 'cap_browser_control', label: 'Browser control', risk: 'high' },
  ]},
  { label: 'Memory', caps: [
    { key: 'cap_memory_read',  label: 'Memory read',  risk: 'safe' },
    { key: 'cap_memory_write', label: 'Memory write', risk: 'mod'  },
  ]},
  { label: 'Automation', caps: [
    { key: 'cap_cron_jobs',    label: 'Cron jobs',    risk: 'mod'  },
    { key: 'cap_notifications', label: 'Notifications', risk: 'safe' },
  ]},
  { label: 'Integrations', caps: [
    { key: 'cap_channel_read', label: 'Channel read', risk: 'mod' },
  ]},
]

const RISK_COLORS = { safe: '#10b981', mod: '#f59e0b', high: '#ef4444' }
const RISK_LABELS = { safe: 'Safe', mod: 'Moderate', high: 'High risk' }
const CAP_AGENTS = ['JARVIS', 'ADA', 'ALICE', 'NEXUS', 'DUM'] as const

function CapabilitiesPanel() {
  const [agent, setAgent] = useState<string>('JARVIS')
  const [caps, setCaps] = useState<Record<string, boolean>>({})
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    fetch(`${SOUL}/api/soul/capabilities?agent=${agent}`)
      .then(r => r.json())
      .then(d => {
        if (!active) return
        setCaps(d.capabilities ?? {})
        setUpdatedAt(d.updated_at ?? null)
      })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [agent])

  function toggle(key: string) {
    setCaps(c => ({ ...c, [key]: !c[key] }))
  }

  async function handleSave() {
    setSaving(true)
    try {
      const r = await fetch(`${SOUL}/api/soul/capabilities`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent, capabilities: caps }),
      })
      if (r.ok) { setSaved(true); setTimeout(() => setSaved(false), 2000) }
    } catch { /* ignore */ }
    setSaving(false)
  }

  const enabledCount = Object.values(caps).filter(Boolean).length
  const totalCount = Object.values(caps).length

  return (
    <div className="space-y-3">
      {/* Agent selector + summary */}
      <div className="flex items-center gap-3">
        <select value={agent} onChange={e => setAgent(e.target.value)}
          className="bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1.5 text-xs text-[#e5e5e5] outline-none">
          {CAP_AGENTS.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <span className="text-[10px] text-[#555]">{enabledCount}/{totalCount} enabled</span>
        {updatedAt && <span className="text-[10px] text-[#333] ml-auto">{updatedAt.slice(0, 16)}</span>}
      </div>

      {/* Risk legend */}
      <div className="flex items-center gap-3">
        {(['safe', 'mod', 'high'] as const).map(r => (
          <div key={r} className="flex items-center gap-1">
            <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: RISK_COLORS[r] }} />
            <span className="text-[9px] text-[#444]">{RISK_LABELS[r]}</span>
          </div>
        ))}
      </div>

      {loading ? (
        <p className="text-xs text-[#444]">Loading…</p>
      ) : (
        <div className="space-y-3">
          {CAP_CATEGORIES.map(cat => (
            <div key={cat.label} className="bg-[#080808] border border-[#111] rounded-xl overflow-hidden">
              <div className="px-3 py-2 border-b border-[#111]">
                <span className="text-[10px] font-medium text-[#555] uppercase tracking-widest">{cat.label}</span>
              </div>
              <div className="divide-y divide-[#0d0d0d]">
                {cat.caps.map(c => {
                  const on = !!caps[c.key]
                  return (
                    <div key={c.key} className="flex items-center justify-between px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <div className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                          style={{ backgroundColor: RISK_COLORS[c.risk] }} />
                        <span className="text-xs text-[#888]">{c.label}</span>
                      </div>
                      <button onClick={() => toggle(c.key)}
                        className={`relative w-8 h-4 rounded-full transition-colors ${on ? 'bg-[#7c3aed]' : 'bg-[#1a1a1a]'}`}>
                        <div className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${on ? 'translate-x-4' : 'translate-x-0.5'}`} />
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-end pt-1">
        <button onClick={handleSave} disabled={saving || loading}
          className="px-3 py-1.5 bg-[#7c3aed] hover:bg-[#6d28d9] disabled:opacity-40 text-white text-xs rounded-lg transition-colors">
          {saved ? '✓ Saved' : saving ? 'Saving…' : 'Save capabilities'}
        </button>
      </div>
    </div>
  )
}

export function GovernanceView() {
  const [debates, setDebates] = useState<Debate[]>([])
  const [challenges, setChallenges] = useState<Challenge[]>([])
  const [tab, setTab] = useState<'debates' | 'challenges' | 'trust' | 'rules' | 'peers' | 'audit' | 'relations' | 'alma' | 'syco' | 'config' | 'denials' | 'budget' | 'tools' | 'rawtrust' | 'matrix' | 'relext' | 'debatelog' | 'caps'>('debates')
  const [loading, setLoading] = useState(false)
  const [showNewDebate, setShowNewDebate] = useState(false)
  const [showNewChallenge, setShowNewChallenge] = useState(false)
  const [tick, setTick] = useState(0)
  const [debateSearch, setDebateSearch] = useState('')
  const [govDebSort, setGovDebSort] = useState<'rounds' | 'date'>('date')
  const [govChalSort, setGovChalSort] = useState<'open' | 'date'>('date')

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/governance?limit=30`)
        const d = await r.json()
        setDebates(d.debates ?? [])
        setChallenges(d.challenges ?? [])
      } catch { /* ignore */ }
      finally { setLoading(false) }
    }
    load()
  }, [tick])

  const consensusCount = debates.filter(d => d.consensus_reached).length
  const openChallenges = challenges.filter(c => !c.resolved).length

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="h-12 flex items-center gap-2 px-4 border-b border-[#0f0f0f] flex-shrink-0">
        <Scale size={13} className="text-[#7c3aed]" />
        <span className="text-sm font-medium text-[#e5e5e5]">Governance</span>
        {openChallenges > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#f59e0b18] text-[#f59e0b]">
            {openChallenges} open
          </span>
        )}
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[#111] px-4 flex-shrink-0">
        {([
          { id: 'debates' as const, label: `Debates (${debates.length} · ${consensusCount}✓)` },
          { id: 'challenges' as const, label: `Challenges (${challenges.length})` },
          { id: 'trust' as const, label: 'Trust' },
          { id: 'rules' as const, label: 'Rules' },
          { id: 'peers' as const, label: 'Peers' },
          { id: 'audit' as const, label: 'Audit' },
          { id: 'relations' as const, label: 'Relations' },
          { id: 'alma' as const, label: 'Alma' },
          { id: 'syco' as const, label: 'Integrity' },
          { id: 'config' as const, label: 'Config' },
          { id: 'denials' as const, label: 'Denials' },
          { id: 'budget' as const, label: 'Budget' },
          { id: 'tools' as const, label: 'Tool Limits' },
          { id: 'rawtrust' as const, label: 'Raw Trust' },
          { id: 'matrix' as const, label: 'Comms' },
          { id: 'relext' as const, label: 'People', Icon: UserCheck },
          { id: 'debatelog' as const, label: 'Debate Log', Icon: History },
          { id: 'caps' as const, label: 'Capabilities' },
        ]).map(({ id, label }) => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-3 py-2 text-xs transition-colors ${
              tab === id ? 'text-[#e5e5e5] border-b border-[#7c3aed]' : 'text-[#555] hover:text-[#888]'
            }`}>
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {showNewDebate && <NewDebateModal onClose={() => setShowNewDebate(false)} onCreated={() => setTick(t => t + 1)} />}
        {showNewChallenge && <NewChallengeModal onClose={() => setShowNewChallenge(false)} onCreated={() => setTick(t => t + 1)} />}
        {loading && (
          <div className="text-xs text-[#444] py-4 text-center">Loading...</div>
        )}
        {tab === 'debates' && (
          <div className="flex items-center gap-2 mb-2">
            <div className="flex-1 flex items-center gap-2 bg-[#0a0a0a] border border-[#1a1a1a] rounded px-2 py-1">
              <Search size={10} className="text-[#333]" />
              <input value={debateSearch} onChange={e => setDebateSearch(e.target.value)}
                placeholder="filter by topic…"
                className="flex-1 bg-transparent text-xs text-[#aaa] outline-none placeholder-[#333]" />
            </div>
            <button onClick={() => setGovDebSort(s => s === 'rounds' ? 'date' : 'rounds')}
              className="px-2 py-0.5 rounded border text-[9px] transition-colors shrink-0"
              style={govDebSort === 'rounds' ? { borderColor: '#f59e0b', color: '#f59e0b', backgroundColor: '#f59e0b18' } : { borderColor: '#1a1a1a', color: '#333' }}>
              ↓ rounds
            </button>
            <button onClick={() => setShowNewDebate(true)}
              className="flex items-center gap-1 px-2 py-1 text-[9px] bg-[#7c3aed18] text-[#7c3aed] border border-[#7c3aed30] rounded hover:bg-[#7c3aed30] transition-colors shrink-0">
              <Plus size={8} />New
            </button>
          </div>
        )}
        {tab === 'debates' && (govDebSort === 'rounds'
          ? [...debates].sort((a, b) => (b.rounds_completed ?? 0) - (a.rounds_completed ?? 0))
          : debates
        ).filter(d => !debateSearch.trim() || d.topic.toLowerCase().includes(debateSearch.toLowerCase()))
          .map(d => <DebateCard key={d.id} d={d} onCompleted={() => setTick(t => t + 1)} />)}
        {tab === 'challenges' && (
          <div className="flex items-center gap-2 justify-end mb-1">
            <button onClick={() => setGovChalSort(s => s === 'open' ? 'date' : 'open')}
              className="px-2 py-0.5 rounded border text-[9px] transition-colors"
              style={govChalSort === 'open' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : { borderColor: '#1a1a1a', color: '#333' }}>
              ↓ open first
            </button>
            <button onClick={() => setShowNewChallenge(true)}
              className="flex items-center gap-1 px-2 py-0.5 text-[9px] bg-[#f59e0b18] text-[#f59e0b] border border-[#f59e0b30] rounded hover:bg-[#f59e0b30] transition-colors">
              <Plus size={8} />New
            </button>
          </div>
        )}
        {tab === 'challenges' && (govChalSort === 'open'
          ? [...challenges].sort((a, b) => (a.resolved ? 1 : 0) - (b.resolved ? 1 : 0))
          : challenges
        ).map(c => <ChallengeCard key={c.id} c={c} onResolved={() => setTick(t => t + 1)} />)}
        {tab === 'trust' && <TrustMatrixPanel />}
        {tab === 'rules' && <RulesBrowserPanel />}
        {tab === 'peers' && <PeerModelsPanel />}
        {tab === 'audit' && <AuditLogPanel />}
        {tab === 'relations' && <RelationshipsPanel />}
        {tab === 'alma' && <AlmaPanel />}
        {tab === 'syco' && <SycophancyPanel />}
        {tab === 'config' && <AgentConfigPanel />}
        {tab === 'denials' && <DenialTrackingPanel />}
        {tab === 'budget' && <TokenBudgetPanel />}
        {tab === 'tools' && <ToolBudgetPanel />}
        {tab === 'rawtrust' && <RawTrustPanel />}
        {tab === 'matrix' && <CommsMatrixPanel />}
        {tab === 'relext' && <RelationshipsExtPanel />}
        {tab === 'debatelog' && <DebateLogPanel />}
        {tab === 'caps' && <CapabilitiesPanel />}
        {!loading && tab === 'debates' && !debates.length && (
          <div className="text-xs text-[#333] py-8 text-center">No debate history</div>
        )}
        {!loading && tab === 'challenges' && !challenges.length && (
          <div className="text-xs text-[#333] py-8 text-center">No challenges recorded</div>
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-1.5 border-t border-[#111] flex-shrink-0">
        <p className="text-[10px] text-[#333]">
          Multi-agent consensus engine — {debates.length} debates · {challenges.length} challenges
        </p>
      </div>
    </div>
  )
}
