import { useState, useEffect, useRef } from 'react'
import { Cpu, RefreshCw, AlertTriangle, CheckCircle, Clock, Brain, Zap, Radio, GitFork, MessageSquare, Layers, Lightbulb, Database, HeartPulse, ListTodo, BookText, Sparkles, Network, BookHeart, ChevronDown, ScrollText as TimelineIcon, ArrowRight, Plus, X, Trash2, MinusCircle, Pencil, Activity, NotebookPen, Search, Moon } from 'lucide-react'
import { AGENT_COLORS } from '../../lib/types'
import { DreamsPanel } from './DreamsPanel'

const HOST = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
const SOUL = `http://${HOST}:8800`

interface Diagnosis {
  id: number
  agent: string
  diagnosis: string
  status: string
  created_at: string
}

interface InnerThought {
  id: number
  agent: string
  content: string
  state: string
  created_at: string
}

interface AgentTask {
  id: number
  agent: string
  title: string
  status: string
  deadline?: string
  created_at: string
}

interface SubconsciousData {
  diagnoses: Diagnosis[]
  thoughts: InnerThought[]
  tasks: AgentTask[]
}

const STATUS_COLORS: Record<string, string> = {
  pending: '#f59e0b',
  applied: '#10b981',
  failed: '#ef4444',
  skipped: '#555',
  active: '#7c3aed',
  completed: '#10b981',
  cancelled: '#555',
}

function fmtRelTime(iso: string) {
  try {
    const diff = Date.now() - new Date(iso).getTime()
    const m = Math.floor(diff / 60000)
    if (m < 1) return 'just now'
    if (m < 60) return `${m}m ago`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h}h ago`
    return `${Math.floor(h / 24)}d ago`
  } catch { return '' }
}

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? '#444'
  return (
    <span className="text-[9px] px-1.5 py-0.5 rounded font-medium"
      style={{ color, backgroundColor: color + '20' }}>
      {status}
    </span>
  )
}

interface NerveDrive {
  tank: string
  pressure: number
  threshold: number
  fired: boolean
  ocean_param: string | null
}

interface NervesAgent {
  agent: string
  drives: NerveDrive[]
}

const TANK_LABELS: Record<string, string> = {
  alert_drive: 'Alert', boredom: 'Boredom', context_pressure: 'Context',
  curiosity: 'Curiosity', energy_drive: 'Energy', learning_drive: 'Learning',
  social_drive: 'Social', task_drive: 'Task',
}

function NervesPanel() {
  const [agents, setAgents] = useState<NervesAgent[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedAgent, setSelectedAgent] = useState('all')
  const [nvSort, setNvSort] = useState<'pressure' | 'tank'>('tank')
  const [agSort, setAgSort] = useState<'fires' | 'name'>('name')

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/nerves`)
        const d = await r.json()
        setAgents(d.agents ?? [])
      } catch { /* ignore */ }
      finally { setLoading(false) }
    }
    load()
    const iv = setInterval(load, 15_000)
    return () => clearInterval(iv)
  }, [])

  const visible = selectedAgent === 'all' ? agents : agents.filter(a => a.agent === selectedAgent)
  const allAgentNames = ['all', ...agents.map(a => a.agent)]

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 mb-1">
        <div className="flex items-center gap-0.5">
          {allAgentNames.map(a => {
            const isActive = selectedAgent === a
            const color = a === 'all' ? '#555' : (AGENT_COLORS[a] ?? '#555')
            return (
              <button key={a} onClick={() => setSelectedAgent(a)}
                className="px-1.5 py-0.5 rounded text-[9px] font-mono transition-colors"
                style={isActive
                  ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' }
                  : { color: '#333', border: '1px solid transparent' }}>
                {a === 'all' ? 'all' : a.slice(0, 2)}
              </button>
            )
          })}
        </div>
        <button onClick={() => setNvSort(s => s === 'pressure' ? 'tank' : 'pressure')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={nvSort === 'pressure' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ pressure
        </button>
        <button onClick={() => setAgSort(s => s === 'fires' ? 'name' : 'fires')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={agSort === 'fires' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ agents
        </button>
        <span className="text-[10px] text-[#333]">15s auto-refresh</span>
      </div>

      {loading && <div className="text-xs text-[#333] py-4 text-center">Loading drives…</div>}

      {(agSort === 'fires'
        ? [...visible].sort((a, b) => b.drives.filter(d => d.fired).length - a.drives.filter(d => d.fired).length)
        : visible
      ).map(ag => {
        const color = AGENT_COLORS[ag.agent] ?? '#666'
        const firedCount = ag.drives.filter(d => d.fired).length
        return (
          <div key={ag.agent} className="rounded-lg border border-[#0f0f0f] bg-[#050505] overflow-hidden"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}>
            <div className="flex items-center gap-2 px-3 py-2 border-b border-[#0d0d0d]">
              <span className="text-xs font-semibold" style={{ color }}>{ag.agent}</span>
              {firedCount > 0 && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-[#ef444420] text-[#ef4444] font-medium">
                  {firedCount} fired
                </span>
              )}
            </div>
            <div className="px-3 py-2 space-y-2">
              {(nvSort === 'pressure' ? [...ag.drives].sort((a, b) => b.pressure - a.pressure) : ag.drives).map(drv => {
                const pct = Math.min((drv.pressure / drv.threshold) * 100, 100)
                const barColor = drv.fired ? '#ef4444' : pct >= 70 ? '#f59e0b' : '#7c3aed'
                return (
                  <div key={drv.tank}>
                    <div className="flex items-center justify-between mb-0.5">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-[#888]">{TANK_LABELS[drv.tank] ?? drv.tank}</span>
                        {drv.ocean_param && (
                          <span className="text-[9px] text-[#444]">{drv.ocean_param[0].toUpperCase()}</span>
                        )}
                        {drv.fired && <Zap size={9} className="text-[#ef4444]" />}
                      </div>
                      <span className="text-[9px] font-mono text-[#444]">
                        {drv.pressure.toFixed(1)} / {drv.threshold}
                      </span>
                    </div>
                    <div className="w-full h-1 bg-[#111] rounded-full overflow-hidden">
                      <div className="h-full rounded-full transition-all"
                        style={{ width: `${pct}%`, backgroundColor: barColor + (drv.fired ? '' : '88') }} />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}

      {!loading && !visible.length && (
        <div className="text-xs text-[#333] py-6 text-center">No NERVES data</div>
      )}
    </div>
  )
}

interface StreamThought {
  id: number
  agent: string
  content: string
  state: string
  created_at: string
}

function emotionColor(state: string) {
  const s = state.toLowerCase()
  if (s.includes('alerta') || s.includes('preocup') || s.includes('concern')) return '#ef4444'
  if (s.includes('joyful') || s.includes('positiv') || s.includes('excited') || s.includes('entusias')) return '#10b981'
  if (s.includes('reflexiv') || s.includes('observ') || s.includes('analiz')) return '#7c3aed'
  if (s.includes('neutral') || s.includes('calm')) return '#555'
  return '#f59e0b'
}

const STREAM_AGENTS = ['ALICE', 'JARVIS', 'NEXUS', 'DUM', 'ADA']

function InjectThoughtModal({ onClose, onInjected }: { onClose: () => void; onInjected: () => void }) {
  const [ag, setAg] = useState('ALICE')
  const [thought, setThought] = useState('')
  const [emo, setEmo] = useState('')
  const [intention, setIntention] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!thought.trim()) { setErr('Thought required'); return }
    setSaving(true); setErr('')
    try {
      const r = await fetch(`${SOUL}/api/soul/inner-thoughts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: ag, thought: thought.trim(), emotional_state: emo.trim() || undefined, intention: intention.trim() || undefined }),
      })
      const d = await r.json()
      if (!r.ok || !d.ok) { setErr(d.error ?? 'failed'); return }
      onInjected(); onClose()
    } catch (ex) { setErr(String(ex)) }
    finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50" onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={submit}
        className="bg-[#080808] border border-[#1a1a1a] rounded-lg w-80 p-4 space-y-3 shadow-2xl">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-[#e5e5e5]">Inject Thought</span>
          <button type="button" onClick={onClose} className="text-[#333] hover:text-[#777]"><X size={14} /></button>
        </div>
        <div>
          <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Agent</label>
          <div className="flex flex-wrap gap-1">
            {STREAM_AGENTS.map(a => {
              const isActive = ag === a
              const color = AGENT_COLORS[a] ?? '#555'
              return (
                <button key={a} type="button" onClick={() => setAg(a)}
                  className="px-2 py-1 rounded text-[10px] font-mono transition-colors"
                  style={isActive ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                  {a}
                </button>
              )
            })}
          </div>
        </div>
        <div>
          <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Thought *</label>
          <textarea value={thought} onChange={e => setThought(e.target.value)} rows={3}
            placeholder="what are you thinking…"
            className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#ccc] outline-none resize-none placeholder:text-[#333]" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Emotional State</label>
            <input value={emo} onChange={e => setEmo(e.target.value)} placeholder="serene"
              className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#aaa] outline-none placeholder:text-[#333]" />
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Intention</label>
            <input value={intention} onChange={e => setIntention(e.target.value)} placeholder="optional"
              className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#aaa] outline-none placeholder:text-[#333]" />
          </div>
        </div>
        {err && <p className="text-[9px] text-red-400">{err}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="px-3 py-1 text-xs text-[#444] hover:text-[#666]">Cancel</button>
          <button type="submit" disabled={saving}
            className="px-3 py-1 rounded bg-[#7c3aed] text-white text-xs font-medium hover:bg-[#6d28d9] disabled:opacity-50">
            {saving ? 'Injecting…' : 'Inject'}
          </button>
        </div>
      </form>
    </div>
  )
}

function InnerMonologueStream({ agentFilter }: { agentFilter: string }) {
  const [thoughts, setThoughts] = useState<StreamThought[]>([])
  const [newIds, setNewIds] = useState<Set<number>>(new Set())
  const latestIdRef = useRef(0)
  const [connected, setConnected] = useState(false)
  const glowTimers = useRef<ReturnType<typeof setTimeout>[]>([])
  const [showInject, setShowInject] = useState(false)
  const [tick, setTick] = useState(0)
  const [deleting, setDeleting] = useState<Set<number>>(new Set())
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [streamSort, setStreamSort] = useState<'state' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function poll() {
      try {
        const q = agentFilter === 'all' ? '' : `&agent=${agentFilter}`
        const r = await fetch(`${SOUL}/api/soul/inner-thoughts?limit=20${q}`)
        const d = await r.json()
        const incoming: StreamThought[] = d.thoughts ?? []
        if (!active) return
        setConnected(true)
        setThoughts(prev => {
          const existingIds = new Set(prev.map(t => t.id))
          const fresh = incoming.filter(t => !existingIds.has(t.id))
          if (fresh.length > 0) {
            setNewIds(ids => {
              const next = new Set(ids)
              fresh.forEach(t => next.add(t.id))
              return next
            })
            const tid = setTimeout(() => {
              if (!active) return
              setNewIds(ids => {
                const next = new Set(ids)
                fresh.forEach(t => next.delete(t.id))
                return next
              })
            }, 2000)
            glowTimers.current.push(tid)
            const maxId = Math.max(...incoming.map(t => t.id))
            if (maxId > latestIdRef.current) latestIdRef.current = maxId
          }
          return incoming
        })
      } catch {
        setConnected(false)
      }
    }
    poll()
    const iv = setInterval(poll, 5_000)
    return () => {
      active = false
      clearInterval(iv)
      glowTimers.current.forEach(clearTimeout)
      glowTimers.current = []
    }
  }, [agentFilter, tick])

  async function handleDeleteThought(id: number, e: React.MouseEvent) {
    e.stopPropagation()
    if (confirmDel !== id) { setConfirmDel(id); return }
    setConfirmDel(null)
    setDeleting(prev => new Set(prev).add(id))
    try {
      const r = await fetch(`${SOUL}/api/soul/inner-thoughts/${id}`, { method: 'DELETE' })
      if (r.ok) setThoughts(prev => prev.filter(t => t.id !== id))
    } finally {
      setDeleting(prev => { const n = new Set(prev); n.delete(id); return n })
    }
  }

  // Compute emotion frequency from loaded thoughts
  const emoFreq = thoughts.reduce<Record<string, number>>((acc, t) => {
    if (t.state) acc[t.state] = (acc[t.state] ?? 0) + 1
    return acc
  }, {})
  const topEmos = Object.entries(emoFreq).sort((a, b) => b[1] - a[1]).slice(0, 6)

  return (
    <div className="space-y-1.5">
      {showInject && <InjectThoughtModal onClose={() => setShowInject(false)} onInjected={() => setTick(t => t + 1)} />}
      {/* Live indicator */}
      <div className="flex items-center gap-2 px-1 pb-1">
        <div className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-[#10b981] animate-pulse' : 'bg-[#333]'}`} />
        <span className="text-[10px] text-[#333]">{connected ? 'live · 5s polling' : 'disconnected'}</span>
        <button onClick={() => setShowInject(true)}
          className="flex items-center gap-1 px-2 py-0.5 rounded border border-[#1a1a1a] text-[9px] text-[#555] hover:text-[#888] hover:border-[#333] transition-colors">
          <Plus size={9} /> Inject
        </button>
        <button onClick={() => setStreamSort(s => s === 'state' ? 'date' : 'state')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={streamSort === 'state' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ state
        </button>
        <span className="text-[10px] text-[#222] ml-auto">{thoughts.length} thoughts</span>
      </div>

      {/* Emotion frequency chips */}
      {topEmos.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap px-1 pb-1">
          {topEmos.map(([emo, cnt]) => {
            const c = emotionColor(emo)
            return (
              <span key={emo} className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[8px] font-medium"
                style={{ backgroundColor: c + '18', color: c, border: `1px solid ${c}30` }}>
                {emo}
                <span className="font-mono opacity-60">{cnt}</span>
              </span>
            )
          })}
        </div>
      )}

      {(streamSort === 'state'
        ? [...thoughts].sort((a, b) => (a.state ?? '').localeCompare(b.state ?? ''))
        : thoughts
      ).map(t => {
        const color = AGENT_COLORS[t.agent] ?? '#666'
        const ec = emotionColor(t.state)
        const isNew = newIds.has(t.id)
        return (
          <div key={t.id}
            className={`group rounded border border-[#0f0f0f] bg-[#050505] px-3 py-2.5 transition-all duration-700 ${
              isNew ? 'border-l-2 opacity-100' : 'opacity-90'
            }`}
            style={{
              borderLeftColor: isNew ? color : undefined,
              borderLeftWidth: isNew ? 2 : undefined,
              boxShadow: isNew ? `0 0 8px ${color}22` : undefined,
            }}>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-semibold" style={{ color }}>{t.agent}</span>
              <span className="text-[9px] px-1.5 py-0.5 rounded font-medium"
                style={{ color: ec, backgroundColor: ec + '18' }}>
                {t.state}
              </span>
              <span className="text-[10px] text-[#2a2a2a] ml-auto">{fmtRelTime(t.created_at)}</span>
              <button onClick={e => handleDeleteThought(t.id, e)}
                disabled={deleting.has(t.id)}
                className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 px-1 py-0.5 rounded text-[9px] transition-all disabled:opacity-30"
                style={{ color: confirmDel === t.id ? '#ef4444' : '#555' }}
                title={confirmDel === t.id ? 'Click again to confirm delete' : 'Delete thought'}>
                {confirmDel === t.id ? <span className="text-[8px]">confirm?</span> : <Trash2 size={9} />}
              </button>
            </div>
            <p className="text-xs text-[#888] leading-relaxed">{t.content}</p>
          </div>
        )
      })}

      {thoughts.length === 0 && (
        <div className="text-xs text-[#333] py-8 text-center">
          <Brain size={24} className="mx-auto mb-2 text-[#1a1a1a]" />
          No thoughts yet
        </div>
      )}
    </div>
  )
}

const DIAG_STATUS_COLORS: Record<string, string> = {
  pending_review: '#f59e0b',
  accepted: '#7c3aed',
  applied: '#10b981',
  rejected: '#ef4444',
  superseded: '#555',
  pending: '#f59e0b',
}

// ── Reasoning Traces ────────────────────────────────────────────────────
interface ReasoningTrace {
  id: number
  agent: string
  task: string
  premises: string
  reasoning: string
  conclusion: string
  outcome: string | null
  outcome_success: boolean | null
  created_at: string
}

function parseLatency(conclusion: string): string | null {
  const m = conclusion?.match(/latency_ms=(\d+)/)
  if (!m) return null
  const ms = parseInt(m[1])
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function TracesPanel({ agentFilter }: { agentFilter: string }) {
  const [traces, setTraces] = useState<ReasoningTrace[]>([])
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [limit, setLimit] = useState(20)
  const [traceSort, setTraceSort] = useState<'failures' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const q = agentFilter === 'all' ? '' : `&agent=${agentFilter}`
        const r = await fetch(`${SOUL}/api/soul/reasoning-traces?limit=${limit}${q}`)
        const d = await r.json()
        if (active) setTraces(d.traces ?? [])
      } catch { /* keep previous */ } finally { if (active) setLoading(false) }
    }
    load()
    const iv = setInterval(load, 30_000)
    return () => { active = false; clearInterval(iv) }
  }, [agentFilter, limit])

  if (loading && !traces.length) {
    return <div className="text-xs text-[#444] py-8 text-center">Loading traces…</div>
  }

  const successCount = traces.filter(t => t.outcome_success === true).length
  const failCount = traces.filter(t => t.outcome_success === false).length
  const resolvedCount = successCount + failCount
  const successRate = resolvedCount > 0 ? Math.round((successCount / resolvedCount) * 100) : null
  const latenciesMs = traces.flatMap(t => {
    const m = t.conclusion?.match(/latency_ms=(\d+)/)
    return m ? [parseInt(m[1])] : []
  })
  const avgMs = latenciesMs.length > 0 ? Math.round(latenciesMs.reduce((a, b) => a + b, 0) / latenciesMs.length) : null
  const topAgent = traces.reduce((acc, t) => {
    acc[t.agent] = (acc[t.agent] ?? 0) + 1
    return acc
  }, {} as Record<string, number>)
  const topAgentName = Object.entries(topAgent).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null

  return (
    <div className="space-y-1.5">
      {traces.length > 0 && (
        <div className="flex items-center gap-3 px-3 py-2 rounded bg-[#050505] border border-[#0d0d0d] mb-2">
          <span className="text-[9px] text-[#333] font-mono">{traces.length} shown</span>
          {successRate !== null && (
            <span className="text-[9px] font-mono" style={{ color: successRate >= 70 ? '#10b981' : successRate >= 40 ? '#f59e0b' : '#ef4444' }}>
              {successRate}% success
            </span>
          )}
          {avgMs !== null && (
            <span className="text-[9px] text-[#444] font-mono">
              avg {avgMs >= 1000 ? `${(avgMs / 1000).toFixed(1)}s` : `${avgMs}ms`}
            </span>
          )}
          {topAgentName && (
            <span className="text-[9px] font-semibold" style={{ color: AGENT_COLORS[topAgentName] ?? '#888' }}>
              top: {topAgentName} ({topAgent[topAgentName]})
            </span>
          )}
          <button onClick={() => setTraceSort(s => s === 'failures' ? 'date' : 'failures')}
            className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
            style={traceSort === 'failures' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : { borderColor: '#1a1a1a', color: '#333' }}>
            ↓ failures
          </button>
        </div>
      )}
      {(traceSort === 'failures' ? [...traces].sort((a, b) => (a.outcome_success === false ? 0 : 1) - (b.outcome_success === false ? 0 : 1)) : traces).map(t => {
        const color = AGENT_COLORS[t.agent] ?? '#666'
        const isExp = expanded === t.id
        const latency = parseLatency(t.conclusion ?? '')
        const success = t.outcome_success
        return (
          <div key={t.id}
            className="rounded border border-[#0f0f0f] bg-[#050505] overflow-hidden cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}
            onClick={() => setExpanded(x => x === t.id ? null : t.id)}
          >
            <div className="px-3 py-2">
              <div className="flex items-start gap-2">
                <span className="text-[10px] font-semibold flex-shrink-0" style={{ color }}>{t.agent}</span>
                <span className="text-xs text-[#aaa] flex-1 truncate">{t.task}</span>
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  {latency && <span className="text-[9px] text-[#444] font-mono">{latency}</span>}
                  {success === true  && <CheckCircle size={10} className="text-[#10b981]" />}
                  {success === false && <AlertTriangle size={10} className="text-[#ef4444]" />}
                  {success === null  && <Clock size={10} className="text-[#555]" />}
                </div>
              </div>
              {!isExp && t.reasoning && (
                <p className="text-[10px] text-[#444] mt-1 truncate">{t.reasoning}</p>
              )}
            </div>
            {isExp && (
              <div className="px-3 pb-3 border-t border-[#111] space-y-2 pt-2">
                {t.premises && (() => {
                  try {
                    const ps: string[] = JSON.parse(t.premises)
                    return (
                      <div>
                        <span className="text-[9px] text-[#333] uppercase tracking-wider">premises</span>
                        <div className="flex flex-wrap gap-1 mt-0.5">
                          {ps.map((p, i) => (
                            <span key={i} className="text-[9px] px-1.5 py-0.5 rounded bg-[#111] text-[#555]">{p}</span>
                          ))}
                        </div>
                      </div>
                    )
                  } catch { return null }
                })()}
                {t.reasoning && (
                  <div>
                    <span className="text-[9px] text-[#333] uppercase tracking-wider">reasoning</span>
                    <p className="text-xs text-[#666] mt-0.5 leading-relaxed">{t.reasoning}</p>
                  </div>
                )}
                {t.conclusion && (
                  <div>
                    <span className="text-[9px] text-[#333] uppercase tracking-wider">conclusion</span>
                    <p className="text-xs text-[#888] mt-0.5">{t.conclusion}</p>
                  </div>
                )}
                {t.outcome && (
                  <div>
                    <span className="text-[9px] text-[#333] uppercase tracking-wider">outcome</span>
                    <p className="text-xs mt-0.5" style={{ color: success === true ? '#10b981' : success === false ? '#ef4444' : '#555' }}>
                      {t.outcome}
                    </p>
                  </div>
                )}
                <p className="text-[9px] text-[#222]">
                  {new Date(t.created_at).toLocaleString('es-PE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                </p>
              </div>
            )}
          </div>
        )
      })}
      {!loading && !traces.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <GitFork size={24} className="mx-auto mb-2 text-[#1a1a1a]" />
          No reasoning traces
        </div>
      )}
      {!loading && traces.length >= limit && (
        <button onClick={() => setLimit(l => l + 20)}
          className="w-full py-2 text-[10px] text-[#444] hover:text-[#888] border border-[#111] hover:border-[#333] rounded transition-colors">
          Load more ({limit} shown)
        </button>
      )}
    </div>
  )
}

interface Opinion {
  id: number
  agent: string
  topic: string
  content: string
  confidence: number
  category: string
  active: boolean
  importance: number | null
  status: string | null
}

const CAT_COLORS: Record<string, string> = {
  general: '#555',
  decision: '#7c3aed',
  insight: '#38bdf8',
  milestone: '#10b981',
  correction: '#f59e0b',
  pattern: '#ef4444',
}

const OPINION_AGENTS = ['ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM'] as const
const OPINION_CATS = ['general', 'decision', 'insight', 'milestone', 'correction', 'pattern'] as const

function NewOpinionModal({ onClose, onCreated, defaultAgent }: { onClose: () => void; onCreated: () => void; defaultAgent: string }) {
  const [agent, setAgent] = useState(OPINION_AGENTS.includes(defaultAgent as typeof OPINION_AGENTS[number]) ? defaultAgent : 'ALICE')
  const [topic, setTopic] = useState('')
  const [content, setContent] = useState('')
  const [category, setCategory] = useState<string>('general')
  const [confidence, setConfidence] = useState(0.7)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!topic.trim() || !content.trim()) { setErr('Topic and content are required'); return }
    setSaving(true); setErr('')
    try {
      const r = await fetch(`${SOUL}/api/soul/opinions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent, topic: topic.trim(), content: content.trim(), confidence, category })
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
          <span className="text-sm font-medium text-[#e5e5e5]">New Opinion</span>
          <button onClick={onClose}><X size={14} className="text-[#555]" /></button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[9px] text-[#444] uppercase tracking-wider">Agent</label>
              <div className="flex flex-wrap gap-1 mt-1">
                {OPINION_AGENTS.map(a => {
                  const isActive = agent === a
                  const color = AGENT_COLORS[a] ?? '#555'
                  return (
                    <button key={a} type="button" onClick={() => setAgent(a)}
                      className="px-2 py-0.5 rounded text-[10px] font-mono transition-colors"
                      style={isActive ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                      {a}
                    </button>
                  )
                })}
              </div>
            </div>
            <div>
              <label className="text-[9px] text-[#444] uppercase tracking-wider">Category</label>
              <div className="flex flex-wrap gap-1 mt-1">
                {OPINION_CATS.map(c => (
                  <button key={c} type="button" onClick={() => setCategory(c)}
                    className="px-2 py-0.5 rounded text-[10px] font-mono transition-colors"
                    style={category === c ? { color: '#7c3aed', border: '1px solid #7c3aed44', backgroundColor: '#7c3aed18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                    {c}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider">Topic</label>
            <input value={topic} onChange={e => setTopic(e.target.value)} placeholder="Opinion topic…"
              className="w-full mt-1 bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1.5 text-xs text-[#ccc] focus:outline-none focus:border-[#333]" />
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider">Content</label>
            <textarea value={content} onChange={e => setContent(e.target.value)} rows={3}
              placeholder="Describe the opinion…"
              className="w-full mt-1 bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1.5 text-xs text-[#ccc] focus:outline-none focus:border-[#333] resize-none" />
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider">Confidence — {Math.round(confidence * 100)}%</label>
            <input type="range" min={0} max={1} step={0.05} value={confidence} onChange={e => setConfidence(parseFloat(e.target.value))}
              className="w-full mt-1 accent-[#7c3aed]" />
          </div>
          {err && <p className="text-[10px] text-[#ef4444]">{err}</p>}
          <div className="flex gap-2 pt-1">
            <button type="button" onClick={onClose}
              className="flex-1 py-1.5 rounded border border-[#1a1a1a] text-xs text-[#555] hover:text-[#888] transition-colors">
              Cancel
            </button>
            <button type="submit" disabled={saving}
              className="flex-1 py-1.5 rounded bg-[#111] border border-[#333] text-xs text-[#ccc] hover:bg-[#1a1a1a] transition-colors disabled:opacity-50">
              {saving ? 'Saving…' : 'Add Opinion'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function OpinionsPanel({ agentFilter }: { agentFilter: string }) {
  const [opinions, setOpinions] = useState<Opinion[]>([])
  const [catFilter, setCatFilter] = useState('all')
  const [opSearch, setOpSearch] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [showNew, setShowNew] = useState(false)
  const [tick, setTick] = useState(0)
  const [superseding, setSuperseding] = useState<Set<number>>(new Set())
  const [limit, setLimit] = useState(60)
  const [opSort, setOpSort] = useState<'confidence' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const q = new URLSearchParams({ agent: agentFilter, limit: String(limit) })
        const r = await fetch(`${SOUL}/api/soul/opinions?${q}`)
        const d = await r.json()
        if (active) setOpinions(d.opinions ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [agentFilter, tick, limit])

  async function handleSupersede(id: number, e: React.MouseEvent) {
    e.stopPropagation()
    if (superseding.has(id)) return
    setSuperseding(prev => new Set(prev).add(id))
    try {
      const r = await fetch(`${SOUL}/api/soul/opinions/${id}/supersede`, { method: 'PATCH' })
      if (r.ok) setOpinions(prev => prev.map(o => o.id === id ? { ...o, active: false, status: 'superseded' } : o))
    } finally {
      setSuperseding(prev => { const n = new Set(prev); n.delete(id); return n })
    }
  }

  const cats = ['all', ...Array.from(new Set(opinions.map(o => o.category))).sort()]
  const byCat = catFilter === 'all' ? opinions : opinions.filter(o => o.category === catFilter)
  const visible = opSearch.trim()
    ? byCat.filter(o => o.topic.toLowerCase().includes(opSearch.toLowerCase()) || o.content.toLowerCase().includes(opSearch.toLowerCase()))
    : byCat
  const sortedVisible = opSort === 'confidence' ? [...visible].sort((a, b) => b.confidence - a.confidence) : visible

  return (
    <div className="space-y-2">
      {showNew && <NewOpinionModal onClose={() => setShowNew(false)} onCreated={() => setTick(t => t + 1)} defaultAgent={agentFilter !== 'all' ? agentFilter : 'ALICE'} />}
      <div className="flex items-center gap-2 bg-[#0a0a0a] border border-[#1a1a1a] rounded px-2 py-1.5 mb-1">
        <Search size={11} className="text-[#333] flex-shrink-0" />
        <input value={opSearch} onChange={e => setOpSearch(e.target.value)}
          placeholder="search opinions…"
          className="flex-1 bg-transparent text-xs text-[#aaa] outline-none placeholder-[#333]" />
        {opSearch && <button onClick={() => setOpSearch('')} className="text-[#333] hover:text-[#666]"><X size={9} /></button>}
      </div>
      <div className="flex items-center gap-2 flex-wrap pb-1">
        <div className="flex items-center gap-1 flex-wrap">
          {cats.map(c => (
            <button key={c} onClick={() => setCatFilter(c)}
              className={`px-2 py-0.5 rounded text-[9px] border transition-colors ${
                catFilter === c
                  ? 'bg-[#111] border-[#333] text-[#ccc]'
                  : 'border-[#1a1a1a] text-[#333] hover:text-[#555]'
              }`}
              style={catFilter === c && c !== 'all' ? { borderColor: CAT_COLORS[c] ?? '#333', color: CAT_COLORS[c] ?? '#ccc' } : {}}>
              {c}
            </button>
          ))}
        </div>
        <button onClick={() => setShowNew(true)}
          className="ml-auto flex items-center gap-1 px-2 py-0.5 rounded border border-[#1a1a1a] text-[9px] text-[#555] hover:text-[#888] hover:border-[#333] transition-colors">
          <Plus size={9} /> New
        </button>
        <button onClick={() => setOpSort(s => s === 'confidence' ? 'date' : 'confidence')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={opSort === 'confidence' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ confidence
        </button>
        <span className="text-[10px] text-[#333]">
          {visible.length}{loading && <span className="ml-1">…</span>}
        </span>
      </div>

      {sortedVisible.map(op => {
        const color = AGENT_COLORS[op.agent] ?? '#666'
        const catColor = CAT_COLORS[op.category] ?? '#555'
        const confPct = Math.round(op.confidence * 100)
        const confColor = op.confidence >= 0.8 ? '#10b981' : op.confidence >= 0.5 ? '#f59e0b' : '#555'
        const isExp = expanded === op.id

        return (
          <div key={op.id}
            className="group rounded-lg border border-[#0f0f0f] bg-[#050505] overflow-hidden cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}
            onClick={() => setExpanded(x => x === op.id ? null : op.id)}>
            <div className="px-3 py-2.5">
              <div className="flex items-start gap-2 mb-1.5">
                <p className="text-xs text-[#ccc] font-medium flex-1 leading-snug">{op.topic}</p>
                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                  <span className="text-[9px] font-semibold" style={{ color }}>{op.agent}</span>
                  <span className="text-[9px] px-1 rounded"
                    style={{ color: catColor, backgroundColor: catColor + '18' }}>
                    {op.category}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <div className="w-14 h-1 bg-[#111] rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${confPct}%`, backgroundColor: confColor }} />
                  </div>
                  <span className="text-[9px] font-mono" style={{ color: confColor }}>{confPct}%</span>
                </div>
                {op.importance !== null && (
                  <span className="text-[9px] text-[#444]">imp {op.importance?.toFixed(0)}</span>
                )}
                {!op.active
                  ? <span className="text-[9px] text-[#2a2a2a] ml-auto">superseded</span>
                  : (
                    <button onClick={e => handleSupersede(op.id, e)}
                      className="ml-auto opacity-0 group-hover:opacity-100 flex items-center gap-0.5 px-1 py-0.5 rounded text-[9px] text-[#555] hover:text-[#f59e0b] transition-all"
                      title="Mark as superseded" disabled={superseding.has(op.id)}>
                      <MinusCircle size={9} />
                    </button>
                  )
                }
              </div>

              {isExp && (
                <div className="mt-2 pt-2 border-t border-[#111]">
                  <p className="text-xs text-[#777] leading-relaxed">{op.content}</p>
                </div>
              )}
            </div>
          </div>
        )
      })}

      {!loading && !visible.length && (
        <div className="text-xs text-[#333] py-6 text-center">
          <MessageSquare size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No opinions in this category
        </div>
      )}

      {opinions.length >= limit && (
        <button onClick={() => setLimit(l => l + 60)}
          className="w-full py-1.5 text-[10px] text-[#333] hover:text-[#555] border border-[#0f0f0f] rounded transition-colors">
          Load more ({opinions.length} shown)
        </button>
      )}
    </div>
  )
}

interface DistilledExchange {
  id: number
  agent: string
  session_date: string
  key_insights: string[]
  decisions_made: string[]
  summary: string | null
  created_at: string
}

function DistilledExchangesPanel({ agentFilter }: { agentFilter: string }) {
  const [exchanges, setExchanges] = useState<DistilledExchange[]>([])
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [limit, setLimit] = useState(30)
  const [exSort, setExSort] = useState<'richness' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const q = new URLSearchParams({ agent: agentFilter, limit: String(limit) })
        const r = await fetch(`${SOUL}/api/soul/distilled-exchanges?${q}`)
        const d = await r.json()
        if (active) setExchanges(d.exchanges ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [agentFilter, limit])

  function fmtDate(iso: string) {
    try {
      return new Date(iso).toLocaleString('es-PE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    } catch { return iso }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 pb-1">
        <span className="text-[10px] text-[#333]">
          {exchanges.length} sessions{loading && <span className="ml-1">…</span>}
        </span>
        <button onClick={() => setExSort(s => s === 'richness' ? 'date' : 'richness')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
          style={exSort === 'richness' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ richness
        </button>
      </div>

      {(exSort === 'richness' ? [...exchanges].sort((a, b) =>
        ((b.key_insights?.length ?? 0) + (b.decisions_made?.length ?? 0)) -
        ((a.key_insights?.length ?? 0) + (a.decisions_made?.length ?? 0))
      ) : exchanges).map(ex => {
        const color = AGENT_COLORS[ex.agent] ?? '#666'
        const isExp = expanded === ex.id
        const totalItems = (ex.key_insights?.length ?? 0) + (ex.decisions_made?.length ?? 0)

        return (
          <div key={ex.id}
            className="rounded-lg border border-[#0f0f0f] bg-[#050505] overflow-hidden cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}
            onClick={() => setExpanded(x => x === ex.id ? null : ex.id)}>
            <div className="px-3 py-2.5">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-[9px] font-semibold flex-shrink-0" style={{ color }}>{ex.agent}</span>
                <span className="text-[10px] text-[#555] flex-1">{fmtDate(ex.created_at)}</span>
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  {(ex.key_insights?.length ?? 0) > 0 && (
                    <span className="text-[9px] px-1 rounded bg-[#38bdf818] text-[#38bdf8]">
                      {ex.key_insights.length}↑ insights
                    </span>
                  )}
                  {(ex.decisions_made?.length ?? 0) > 0 && (
                    <span className="text-[9px] px-1 rounded bg-[#10b98118] text-[#10b981]">
                      {ex.decisions_made.length} decisions
                    </span>
                  )}
                </div>
              </div>

              {!isExp && ex.summary && (
                <p className="text-xs text-[#555] leading-snug line-clamp-1">{ex.summary}</p>
              )}

              {isExp && (
                <div className="space-y-2 mt-1">
                  {ex.key_insights?.length > 0 && (
                    <div>
                      <p className="text-[9px] text-[#38bdf8] uppercase tracking-wider mb-1">insights</p>
                      {ex.key_insights.map((ins, i) => (
                        <p key={i} className="text-xs text-[#888] leading-relaxed pl-2 border-l border-[#38bdf820] mb-0.5">{ins}</p>
                      ))}
                    </div>
                  )}
                  {ex.decisions_made?.length > 0 && (
                    <div>
                      <p className="text-[9px] text-[#10b981] uppercase tracking-wider mb-1">decisions</p>
                      {ex.decisions_made.map((dec, i) => (
                        <p key={i} className="text-xs text-[#888] leading-relaxed pl-2 border-l border-[#10b98120] mb-0.5">{dec}</p>
                      ))}
                    </div>
                  )}
                  {ex.summary && (
                    <div className="pt-1 border-t border-[#111]">
                      <p className="text-[9px] text-[#444] uppercase tracking-wider mb-1">summary</p>
                      <p className="text-xs text-[#666] leading-relaxed">{ex.summary}</p>
                    </div>
                  )}
                  {totalItems === 0 && !ex.summary && (
                    <p className="text-xs text-[#2a2a2a]">no content</p>
                  )}
                </div>
              )}
            </div>
          </div>
        )
      })}

      {!loading && !exchanges.length && (
        <div className="text-xs text-[#333] py-6 text-center">
          <Layers size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No distilled exchanges
        </div>
      )}

      {exchanges.length >= limit && (
        <button onClick={() => setLimit(l => l + 30)}
          className="w-full py-1.5 text-[10px] text-[#333] hover:text-[#555] border border-[#0f0f0f] rounded transition-colors">
          Load more ({exchanges.length} shown)
        </button>
      )}
    </div>
  )
}

interface CuriosityEntry {
  id: number
  agent: string
  question: string
  output_type: string
  seed_content: string | null
  created_at: string
}

const CURIOSITY_AGENTS_LIST = ['ALICE', 'JARVIS', 'NEXUS', 'DUM', 'ADA']
const CURIOSITY_TYPES = ['inner_monologue', 'web_chat'] as const

function NewCuriosityModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [ag, setAg] = useState('ALICE')
  const [question, setQuestion] = useState('')
  const [outType, setOutType] = useState<string>('inner_monologue')
  const [seed, setSeed] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!question.trim()) { setErr('Question required'); return }
    setSaving(true); setErr('')
    try {
      const r = await fetch(`${SOUL}/api/soul/curiosity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: ag, question: question.trim(), output_type: outType, seed_content: seed.trim() || undefined }),
      })
      const d = await r.json()
      if (!r.ok || !d.ok) { setErr(d.error ?? 'failed'); return }
      onCreated(); onClose()
    } catch (ex) { setErr(String(ex)) }
    finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50" onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={submit}
        className="bg-[#080808] border border-[#1a1a1a] rounded-lg w-80 p-4 space-y-3 shadow-2xl">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-[#e5e5e5]">Log Curiosity</span>
          <button type="button" onClick={onClose} className="text-[#333] hover:text-[#777]"><X size={14} /></button>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Agent</label>
            <div className="flex flex-wrap gap-1">
              {CURIOSITY_AGENTS_LIST.map(a => {
                const isActive = ag === a
                const color = AGENT_COLORS[a] ?? '#555'
                return (
                  <button key={a} type="button" onClick={() => setAg(a)}
                    className="px-2 py-1 rounded text-[10px] font-mono transition-colors"
                    style={isActive ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                    {a}
                  </button>
                )
              })}
            </div>
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Type</label>
            <div className="flex flex-wrap gap-1">
              {CURIOSITY_TYPES.map(t => (
                <button key={t} type="button" onClick={() => setOutType(t)}
                  className="px-2 py-1 rounded text-[10px] font-mono transition-colors"
                  style={outType === t ? { color: '#7c3aed', border: '1px solid #7c3aed44', backgroundColor: '#7c3aed18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                  {t}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div>
          <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Question / Thought *</label>
          <textarea value={question} onChange={e => setQuestion(e.target.value)} rows={3}
            placeholder="what are you curious about…"
            className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#ccc] outline-none resize-none placeholder:text-[#333]" />
        </div>
        <div>
          <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Seed Content (optional)</label>
          <input value={seed} onChange={e => setSeed(e.target.value)} placeholder="related memory or context"
            className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#aaa] outline-none placeholder:text-[#333]" />
        </div>
        {err && <p className="text-[9px] text-red-400">{err}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="px-3 py-1 text-xs text-[#444] hover:text-[#666]">Cancel</button>
          <button type="submit" disabled={saving}
            className="px-3 py-1 rounded bg-[#f59e0b] text-black text-xs font-medium hover:bg-[#d97706] disabled:opacity-50">
            {saving ? 'Saving…' : 'Log'}
          </button>
        </div>
      </form>
    </div>
  )
}

function CuriosityPanel({ agentFilter }: { agentFilter: string }) {
  const [items, setItems] = useState<CuriosityEntry[]>([])
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [showNew, setShowNew] = useState(false)
  const [tick, setTick] = useState(0)
  const [outTypeFilter, setOutTypeFilter] = useState('all')
  const [limit, setLimit] = useState(40)
  const [curSort, setCurSort] = useState<'agent' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const q = new URLSearchParams({ agent: agentFilter, limit: String(limit) })
        const r = await fetch(`${SOUL}/api/soul/curiosity-log?${q}`)
        const d = await r.json()
        if (active) setItems(d.curiosities ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [agentFilter, tick, limit])

  const outTypes = ['all', ...Array.from(new Set(items.map(c => c.output_type).filter(Boolean))).sort()]
  const visibleCuriosities = outTypeFilter === 'all' ? items : items.filter(c => c.output_type === outTypeFilter)

  return (
    <div className="space-y-1.5">
      {showNew && <NewCuriosityModal onClose={() => setShowNew(false)} onCreated={() => setTick(t => t + 1)} />}
      <div className="flex items-center gap-2 mb-1 flex-wrap">
        <div className="flex items-center gap-1 flex-wrap">
          {outTypes.map(t => (
            <button key={t} onClick={() => setOutTypeFilter(t)}
              className={`px-2 py-0.5 rounded text-[9px] border transition-colors ${outTypeFilter === t ? 'bg-[#111] border-[#333] text-[#ccc]' : 'border-[#1a1a1a] text-[#333] hover:text-[#555]'}`}>
              {t}
            </button>
          ))}
        </div>
        <button onClick={() => setCurSort(s => s === 'agent' ? 'date' : 'agent')}
          className="flex items-center gap-1 px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={curSort === 'agent' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ agent
        </button>
        <button onClick={() => setShowNew(true)}
          className="ml-auto flex items-center gap-1 px-2 py-0.5 rounded border border-[#1a1a1a] text-[9px] text-[#555] hover:text-[#f59e0b] hover:border-[#444] transition-colors">
          <Plus size={9} /> Log Curiosity
        </button>
      </div>
      {loading && !items.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading…</div>
      )}
      {(curSort === 'agent'
        ? [...visibleCuriosities].sort((a, b) => a.agent.localeCompare(b.agent))
        : visibleCuriosities
      ).map(c => {
        const color = AGENT_COLORS[c.agent] ?? '#666'
        const isExp = expanded === c.id
        return (
          <div key={c.id}
            className="rounded border border-[#0f0f0f] bg-[#050505] overflow-hidden cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}
            onClick={() => setExpanded(x => x === c.id ? null : c.id)}>
            <div className="px-3 py-2.5">
              <div className="flex items-start gap-2 mb-1">
                <span className="text-[9px] font-semibold flex-shrink-0 mt-0.5" style={{ color }}>{c.agent}</span>
                <p className={`text-xs text-[#ccc] leading-snug flex-1 ${isExp ? '' : 'line-clamp-2'}`}>
                  {c.question}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-[#111] text-[#444]">{c.output_type}</span>
                <span className="text-[10px] text-[#222] ml-auto">{fmtRelTime(c.created_at)}</span>
              </div>
              {isExp && c.seed_content && (
                <div className="mt-1.5 pt-1.5 border-t border-[#111]">
                  <p className="text-[9px] text-[#444] uppercase tracking-wider mb-0.5">seed memory</p>
                  <p className="text-xs text-[#555] leading-relaxed">{c.seed_content}</p>
                </div>
              )}
            </div>
          </div>
        )
      })}
      {!loading && !visibleCuriosities.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <Lightbulb size={24} className="mx-auto mb-2 text-[#1a1a1a]" />
          {items.length ? 'No results for this type' : 'No curiosity log'}
        </div>
      )}
      {!loading && items.length >= limit && (
        <button onClick={() => setLimit(l => l + 40)}
          className="w-full py-1.5 text-[10px] text-[#444] hover:text-[#777] border border-[#111] hover:border-[#222] rounded transition-colors">
          Load more ({limit} shown)
        </button>
      )}
      <p className="text-[9px] text-[#222] text-right px-1">organic questions from idle state</p>
    </div>
  )
}

const EVENT_TYPES = ['all', 'milestone', 'response', 'command', 'error'] as const
type EvtType = typeof EVENT_TYPES[number]

interface EventEntry {
  id: number
  agent: string
  event_type: string
  content: string
  created_at: string
}

function EventLogPanel({ agentFilter }: { agentFilter: string }) {
  const [items, setItems] = useState<EventEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [evtType, setEvtType] = useState<EvtType>('all')
  const [expandedEvts, setExpandedEvts] = useState<Set<number>>(new Set())
  const [tick, setTick] = useState(0)
  const [evtSort, setEvtSort] = useState<'type' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const ag = agentFilter !== 'all' ? `&agent=${encodeURIComponent(agentFilter)}` : ''
        const r = await fetch(`${SOUL}/api/soul/event-log?event_type=${evtType}&limit=80${ag}`)
        const d = await r.json()
        if (active) setItems(d.events ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    const iv = setInterval(() => setTick(t => t + 1), 30_000)
    return () => { active = false; clearInterval(iv) }
  }, [agentFilter, evtType, tick])

  const EVENT_TYPE_COLORS: Record<string, string> = {
    milestone: '#10b981',
    response: '#7c3aed',
    command: '#f59e0b',
    error: '#ef4444',
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1 mb-2">
        {EVENT_TYPES.map(t => (
          <button key={t} onClick={() => setEvtType(t)}
            className={`px-2 py-0.5 rounded text-[9px] transition-colors ${
              evtType === t ? 'bg-[#7c3aed] text-white' : 'text-[#444] hover:text-[#888] border border-[#111] hover:border-[#222]'
            }`}>
            {t}
          </button>
        ))}
        <button onClick={() => setEvtSort(s => s === 'type' ? 'date' : 'type')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={evtSort === 'type' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ type
        </button>
        <span className="text-[9px] text-[#222] ml-auto">30s auto-refresh</span>
      </div>
      {loading && !items.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading events…</div>
      )}
      {(evtSort === 'type'
        ? [...items].sort((a, b) => a.event_type.localeCompare(b.event_type))
        : items
      ).map(e => {
        const ac = AGENT_COLORS[e.agent] ?? '#666'
        const tc = EVENT_TYPE_COLORS[e.event_type] ?? '#444'
        const isEvtExp = expandedEvts.has(e.id)
        return (
          <div key={e.id}
            className="rounded border border-[#0f0f0f] bg-[#050505] px-3 py-2 cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: ac, borderLeftWidth: 2 }}
            onClick={() => setExpandedEvts(prev => { const n = new Set(prev); n.has(e.id) ? n.delete(e.id) : n.add(e.id); return n })}>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[9px] font-semibold" style={{ color: ac }}>{e.agent}</span>
              <span className="text-[9px] px-1.5 py-0.5 rounded font-medium"
                style={{ color: tc, backgroundColor: tc + '20' }}>
                {e.event_type}
              </span>
              <span className="text-[9px] text-[#222] ml-auto">{fmtRelTime(e.created_at)}</span>
            </div>
            <p className={`text-xs text-[#777] leading-snug ${isEvtExp ? '' : 'line-clamp-3'}`}>{e.content}</p>
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">No events</div>
      )}
      <p className="text-[9px] text-[#222] text-right px-1">{items.length} events</p>
    </div>
  )
}

interface SessionMem {
  id: number
  session_id: string
  agent: string
  turn_number: number
  summary: string | null
  key_decisions: string | null
  active_tasks: string | null
  pending_items: string | null
  created_at: string
}

function SessionMemoryPanel({ agentFilter }: { agentFilter: string }) {
  const [items, setItems] = useState<SessionMem[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [editSummary, setEditSummary] = useState('')
  const [saving, setSaving] = useState(false)
  const [limit, setLimit] = useState(60)
  const [smSort, setSmSort] = useState<'turn' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const ag = agentFilter !== 'all' ? `&agent=${encodeURIComponent(agentFilter)}` : ''
        const r = await fetch(`${SOUL}/api/soul/session-memory?limit=${limit}${ag}`)
        const d = await r.json()
        if (active) setItems(d.sessions ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [agentFilter, limit])

  function startEdit(s: SessionMem, e: React.MouseEvent) {
    e.stopPropagation()
    setEditing(s.id)
    setEditSummary(s.summary ?? '')
  }

  async function handleSave(id: number, e: React.MouseEvent) {
    e.stopPropagation()
    setSaving(true)
    try {
      const r = await fetch(`${SOUL}/api/soul/session-memory/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ summary: editSummary }),
      })
      if (r.ok) {
        setItems(prev => prev.map(s => s.id === id ? { ...s, summary: editSummary } : s))
        setEditing(null)
      }
    } finally { setSaving(false) }
  }

  const visibleSm = smSort === 'turn' ? [...items].sort((a, b) => b.turn_number - a.turn_number) : items

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2 pb-1">
        <span className="text-[10px] text-[#333]">{items.length} sessions</span>
        <button onClick={() => setSmSort(s => s === 'turn' ? 'date' : 'turn')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
          style={smSort === 'turn' ? { borderColor: '#2563eb', color: '#2563eb', backgroundColor: '#2563eb18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ turn#
        </button>
      </div>
      {loading && !items.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading session memory…</div>
      )}
      {visibleSm.map(s => {
        const color = AGENT_COLORS[s.agent] ?? '#666'
        const isExp = expanded === s.id
        const isEditing = editing === s.id
        let decisions: string[] = []
        let tasks: Array<{ desc?: string; status?: string }> = []
        let pending: string[] = []
        try { decisions = JSON.parse(s.key_decisions ?? '[]') } catch { /* */ }
        try { tasks = JSON.parse(s.active_tasks ?? '[]') } catch { /* */ }
        try { pending = JSON.parse(s.pending_items ?? '[]') } catch { /* */ }
        return (
          <div key={s.id}
            className="group rounded border border-[#0f0f0f] bg-[#050505] overflow-hidden hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}>
            <div className="px-3 py-2.5 cursor-pointer" onClick={() => !isEditing && setExpanded(x => x === s.id ? null : s.id)}>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[9px] font-semibold" style={{ color }}>{s.agent}</span>
                <span className="text-[9px] px-1 py-0.5 rounded bg-[#111] text-[#444]">t{s.turn_number}</span>
                <span className="text-[9px] text-[#222] ml-auto">{fmtRelTime(s.created_at)}</span>
                {!isEditing && (
                  <button onClick={e => startEdit(s, e)}
                    className="opacity-0 group-hover:opacity-100 p-0.5 text-[#333] hover:text-[#888] transition-all"
                    title="Edit summary">
                    <Pencil size={9} />
                  </button>
                )}
              </div>
              {isEditing ? (
                <div className="space-y-1.5" onClick={e => e.stopPropagation()}>
                  <textarea value={editSummary} onChange={e => setEditSummary(e.target.value)} rows={3}
                    className="w-full bg-[#111] border border-[#333] rounded px-2 py-1 text-xs text-[#ccc] outline-none resize-none" />
                  <div className="flex justify-end gap-1.5">
                    <button onClick={e => { e.stopPropagation(); setEditing(null) }}
                      className="px-2 py-0.5 text-[9px] text-[#444] hover:text-[#666]">Cancel</button>
                    <button onClick={e => handleSave(s.id, e)} disabled={saving}
                      className="px-2 py-0.5 rounded bg-[#7c3aed] text-white text-[9px] hover:bg-[#6d28d9] disabled:opacity-50">
                      {saving ? '…' : 'Save'}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  {s.summary && (
                    <p className={`text-xs text-[#666] leading-snug ${isExp ? '' : 'line-clamp-2'}`}>{s.summary}</p>
                  )}
                  {isExp && (
                    <div className="mt-2 space-y-1.5">
                      {decisions.length > 0 && (
                        <div>
                          <p className="text-[9px] text-[#333] uppercase tracking-wider mb-0.5">decisions</p>
                          <ul className="space-y-0.5">
                            {decisions.map((d, i) => (
                              <li key={i} className="text-xs text-[#7c3aed] leading-snug">• {d}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {tasks.length > 0 && (
                        <div>
                          <p className="text-[9px] text-[#333] uppercase tracking-wider mb-0.5">tasks</p>
                          <ul className="space-y-0.5">
                            {tasks.map((t, i) => (
                              <li key={i} className="text-xs text-[#10b981] leading-snug">
                                • {t.desc ?? JSON.stringify(t)}
                                {t.status && <span className="text-[#555] ml-1">({t.status})</span>}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {pending.length > 0 && (
                        <div>
                          <p className="text-[9px] text-[#333] uppercase tracking-wider mb-0.5">pending</p>
                          <ul className="space-y-0.5">
                            {pending.map((p, i) => (
                              <li key={i} className="text-xs text-[#f59e0b] leading-snug">• {p}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <Database size={24} className="mx-auto mb-2 text-[#1a1a1a]" />
          No session memory
        </div>
      )}

      {items.length >= limit && (
        <button onClick={() => setLimit(l => l + 60)}
          className="w-full py-1.5 text-[10px] text-[#333] hover:text-[#555] border border-[#0f0f0f] rounded transition-colors">
          Load more ({items.length} shown)
        </button>
      )}
      <p className="text-[9px] text-[#222] text-right px-1">{items.length} sessions</p>
    </div>
  )
}

interface EmoDiaryEntry {
  id: number
  agent: string
  valence: number
  arousal: number
  key_moment: string | null
  pending_thread: string | null
  relationship_note: string | null
  importance: number
  created_at: string
}

const SOUL_EMO = typeof window !== 'undefined' ? `http://${window.location.hostname}:8800` : 'http://localhost:8800'

interface ProceduralMemory {
  id: number
  agent: string
  task_type: string
  query: string
  workflow: string | null
  facts: string | null
  hit_count: number
  success_count: number
  fail_count: number
  success_rate: number
  active: boolean
  pending_revision: boolean
  version: number
  reflection: string | null
  created_at: string
  last_improved_at: string | null
}

const SOUL_PROC = typeof window !== 'undefined' ? `http://${window.location.hostname}:8800` : 'http://localhost:8800'

function ProceduresPanel({ agentFilter }: { agentFilter: string }) {
  const [items, setItems] = useState<ProceduralMemory[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [procSort, setProcSort] = useState<'success' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const q = new URLSearchParams()
        if (agentFilter && agentFilter !== 'all') q.set('agent', agentFilter)
        const r = await fetch(`${SOUL_PROC}/api/soul/procedural-memories?${q}`)
        const d = await r.json()
        if (active) setItems(d.procedures ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [agentFilter])

  const visibleProcs = procSort === 'success' ? [...items].sort((a, b) => b.success_rate - a.success_rate) : items

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 pb-1">
        <span className="text-[10px] text-[#333]">{items.length} procedures</span>
        <button onClick={() => setProcSort(s => s === 'success' ? 'date' : 'success')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
          style={procSort === 'success' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ success %
        </button>
      </div>
      {loading && !items.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading procedures…</div>
      )}
      {visibleProcs.map(p => {
        const color = AGENT_COLORS[p.agent] ?? '#666'
        const isExp = expanded === p.id
        const successPct = Math.round(p.success_rate * 100)
        const srColor = successPct >= 80 ? '#10b981' : successPct >= 50 ? '#f59e0b' : '#ef4444'
        let factsObj: Record<string, unknown> = {}
        try { factsObj = p.facts ? JSON.parse(p.facts) : {} } catch { /* */ }
        return (
          <div key={p.id}
            className="rounded border border-[#0f0f0f] bg-[#050505] overflow-hidden cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}
            onClick={() => setExpanded(x => x === p.id ? null : p.id)}>
            <div className="px-3 py-2.5">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-[9px] font-semibold" style={{ color }}>{p.agent}</span>
                <span className="text-[9px] px-1 rounded bg-[#0a0a0a] text-[#444]">{p.task_type}</span>
                {!p.active && <span className="text-[9px] text-[#555]">inactive</span>}
                {p.pending_revision && <span className="text-[9px] text-[#f59e0b]">⟳ revision</span>}
                <span className="text-[9px] text-[#333] ml-auto">v{p.version}</span>
              </div>
              <p className={`text-xs text-[#ccc] leading-snug mb-2 ${isExp ? '' : 'line-clamp-1'}`}>{p.query}</p>
              {/* Stats row */}
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <div className="w-16 h-1.5 bg-[#111] rounded overflow-hidden">
                    <div className="h-full rounded" style={{ width: `${successPct}%`, backgroundColor: srColor }} />
                  </div>
                  <span className="text-[9px] font-mono" style={{ color: srColor }}>{successPct}%</span>
                </div>
                <span className="text-[9px] text-[#333]">{p.hit_count}× hit · {p.success_count}✓ {p.fail_count}✗</span>
              </div>
              {isExp && (
                <div className="mt-2 pt-2 border-t border-[#0d0d0d] space-y-2">
                  {p.workflow && (
                    <div>
                      <p className="text-[9px] text-[#333] uppercase tracking-wider mb-0.5">workflow</p>
                      <p className="text-xs text-[#555] leading-snug">{p.workflow}</p>
                    </div>
                  )}
                  {Object.keys(factsObj).length > 0 && (
                    <div>
                      <p className="text-[9px] text-[#333] uppercase tracking-wider mb-0.5">facts</p>
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(factsObj).slice(0, 6).map(([k, v]) => (
                          <span key={k} className="text-[8px] px-1 rounded bg-[#0f0f0f] text-[#555]">
                            {k}: {String(v).slice(0, 30)}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {p.reflection && (
                    <p className="text-xs text-[#7c3aed] italic leading-snug">{p.reflection}</p>
                  )}
                </div>
              )}
            </div>
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <BookText size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No procedural memories
        </div>
      )}
    </div>
  )
}

interface BacklogTask {
  id: number
  agent: string
  title: string
  description: string | null
  status: string
  priority: number
  deadline: string | null
  created_at: string
}

const TASK_STATUS_COLORS: Record<string, string> = {
  pending: '#f59e0b',
  in_progress: '#7c3aed',
  completed: '#10b981',
  cancelled: '#555',
  blocked: '#ef4444',
}

const SOUL_TASKS = typeof window !== 'undefined' ? `http://${window.location.hostname}:8800` : 'http://localhost:8800'

const TASK_STATUS_CYCLE: Record<string, string> = {
  pending: 'in_progress',
  in_progress: 'completed',
}

const TASK_AGENTS_LIST = ['ALICE', 'JARVIS', 'NEXUS', 'DUM', 'ADA']

function NewTaskModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [ag, setAg] = useState('ALICE')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState(5)
  const [deadline, setDeadline] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim()) { setError('Title required'); return }
    setSaving(true); setError('')
    try {
      const body: Record<string, unknown> = { agent: ag, title: title.trim(), priority }
      if (description.trim()) body.description = description.trim()
      if (deadline) body.deadline = new Date(deadline).toISOString()
      const r = await fetch(`${SOUL_TASKS}/api/soul/agent-tasks`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
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
          <span className="text-sm font-medium text-[#e5e5e5]">New Task</span>
          <button type="button" onClick={onClose} className="text-[#333] hover:text-[#888]"><X size={14} /></button>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[9px] text-[#444] mb-1 block">Agent</label>
            <div className="flex flex-wrap gap-1">
              {TASK_AGENTS_LIST.map(a => {
                const isActive = ag === a
                const color = AGENT_COLORS[a] ?? '#555'
                return (
                  <button key={a} type="button" onClick={() => setAg(a)}
                    className="px-2 py-1 rounded text-[10px] font-mono transition-colors"
                    style={isActive ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                    {a}
                  </button>
                )
              })}
            </div>
          </div>
          <div>
            <label className="text-[9px] text-[#444] mb-1 block">Priority (1-10)</label>
            <input type="range" min={1} max={10} value={priority} onChange={e => setPriority(+e.target.value)}
              className="w-full mt-1 accent-[#7c3aed]" />
            <span className="text-[9px] text-[#7c3aed]">{priority}</span>
          </div>
        </div>
        <div>
          <label className="text-[9px] text-[#444] mb-1 block">Title *</label>
          <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Task title"
            className="w-full bg-[#0a0a0a] border border-[#1a1a1a] rounded px-2 py-1.5 text-xs text-[#ccc] focus:outline-none focus:border-[#7c3aed]" />
        </div>
        <div>
          <label className="text-[9px] text-[#444] mb-1 block">Description</label>
          <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} placeholder="Optional details…"
            className="w-full bg-[#0a0a0a] border border-[#1a1a1a] rounded px-2 py-1.5 text-xs text-[#ccc] focus:outline-none focus:border-[#7c3aed] resize-none" />
        </div>
        <div>
          <label className="text-[9px] text-[#444] mb-1 block">Deadline (optional)</label>
          <input type="date" value={deadline} onChange={e => setDeadline(e.target.value)}
            className="w-full bg-[#0a0a0a] border border-[#1a1a1a] rounded px-2 py-1.5 text-xs text-[#ccc] focus:outline-none focus:border-[#7c3aed]" />
        </div>
        {error && <p className="text-[10px] text-red-500">{error}</p>}
        <button type="submit" disabled={saving}
          className="w-full py-2 rounded bg-[#7c3aed] text-white text-xs font-medium hover:bg-[#6d28d9] disabled:opacity-50">
          {saving ? 'Creating…' : 'Create Task'}
        </button>
      </form>
    </div>
  )
}

function BacklogPanel({ agentFilter }: { agentFilter: string }) {
  const [tasks, setTasks] = useState<BacklogTask[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [updatingTask, setUpdatingTask] = useState<Set<number>>(new Set())
  const [confirmDeleteTask, setConfirmDeleteTask] = useState<number | null>(null)
  const [showNewTask, setShowNewTask] = useState(false)
  const [tick, setTick] = useState(0)
  const [backSort, setBackSort] = useState<'priority' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const q = new URLSearchParams()
        if (agentFilter && agentFilter !== 'all') q.set('agent', agentFilter)
        const r = await fetch(`${SOUL_TASKS}/api/soul/agent-tasks?${q}`)
        const d = await r.json()
        if (active) setTasks(d.tasks ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [agentFilter, tick])

  async function handleDeleteTask(id: number, e: React.MouseEvent) {
    e.stopPropagation()
    if (confirmDeleteTask === id) {
      try {
        await fetch(`${SOUL_TASKS}/api/soul/agent-tasks/${id}`, { method: 'DELETE' })
        setTasks(prev => prev.filter(t => t.id !== id))
      } finally { setConfirmDeleteTask(null) }
    } else {
      setConfirmDeleteTask(id)
      setTimeout(() => setConfirmDeleteTask(c => c === id ? null : c), 3000)
    }
  }

  async function handleAdvanceStatus(id: number, currentStatus: string, e: React.MouseEvent) {
    e.stopPropagation()
    const nextStatus = TASK_STATUS_CYCLE[currentStatus]
    if (!nextStatus) return
    setUpdatingTask(prev => new Set(prev).add(id))
    try {
      const r = await fetch(`${SOUL_TASKS}/api/soul/agent-tasks/${id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: nextStatus }),
      })
      const d = await r.json()
      if (r.ok && d.ok) {
        setTasks(prev => prev.map(t => t.id === id ? { ...t, status: nextStatus } : t))
      }
    } catch { } finally {
      setUpdatingTask(prev => { const n = new Set(prev); n.delete(id); return n })
    }
  }

  return (
    <div className="space-y-2">
      {showNewTask && <NewTaskModal onClose={() => setShowNewTask(false)} onCreated={() => setTick(t => t + 1)} />}
      <div className="flex items-center gap-2 mb-1">
        <button onClick={() => setShowNewTask(true)}
          className="flex items-center gap-1 px-2 py-0.5 text-[9px] bg-[#7c3aed18] text-[#7c3aed] border border-[#7c3aed30] rounded hover:bg-[#7c3aed30] transition-colors">
          <Plus size={8} />New
        </button>
        <button onClick={() => setBackSort(s => s === 'priority' ? 'date' : 'priority')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
          style={backSort === 'priority' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ priority
        </button>
      </div>
      {loading && !tasks.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading backlog…</div>
      )}
      {(backSort === 'priority' ? [...tasks].sort((a, b) => b.priority - a.priority) : tasks).map(t => {
        const color = AGENT_COLORS[t.agent] ?? '#666'
        const sc = TASK_STATUS_COLORS[t.status] ?? '#555'
        const isExp = expanded === t.id
        const canAdvance = !!TASK_STATUS_CYCLE[t.status]
        const isUpdating = updatingTask.has(t.id)
        const isConfirmingDelete = confirmDeleteTask === t.id
        return (
          <div key={t.id}
            className="group rounded border border-[#0f0f0f] bg-[#050505] overflow-hidden cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}
            onClick={() => setExpanded(x => x === t.id ? null : t.id)}>
            <div className="px-3 py-2.5">
              <div className="flex items-start gap-2 mb-1">
                <p className={`text-xs text-[#ccc] leading-snug flex-1 ${isExp ? '' : 'line-clamp-2'}`}>{t.title}</p>
                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                  <span className="text-[9px] font-semibold" style={{ color }}>{t.agent}</span>
                  <div className="flex items-center gap-1">
                    <span className="text-[9px] px-1 rounded font-medium"
                      style={{ color: sc, backgroundColor: sc + '18' }}>{t.status}</span>
                    {canAdvance && (
                      <button onClick={e => handleAdvanceStatus(t.id, t.status, e)}
                        disabled={isUpdating}
                        className="p-0.5 text-[#333] hover:text-[#7c3aed] transition-colors disabled:opacity-50"
                        title={`Advance to ${TASK_STATUS_CYCLE[t.status]}`}>
                        <ArrowRight size={9} />
                      </button>
                    )}
                    <button onClick={e => handleDeleteTask(t.id, e)}
                      className={`opacity-0 group-hover:opacity-100 p-0.5 transition-all ${isConfirmingDelete ? 'text-red-500' : 'text-[#333] hover:text-red-400'}`}
                      title={isConfirmingDelete ? 'Click again to delete' : 'Delete task'}>
                      {isConfirmingDelete ? <span className="text-[8px] font-bold">DEL?</span> : <Trash2 size={9} />}
                    </button>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[9px] text-[#333]">p{t.priority}</span>
                <div className="w-8 h-1 bg-[#111] rounded overflow-hidden">
                  <div className="h-full rounded bg-[#7c3aed]" style={{ width: `${(t.priority / 10) * 100}%` }} />
                </div>
                {t.deadline && (
                  <span className="text-[9px] text-[#f59e0b] ml-1">
                    due {new Date(t.deadline).toLocaleDateString('es-PE', { month: 'short', day: 'numeric' })}
                  </span>
                )}
              </div>
              {isExp && t.description && (
                <p className="text-xs text-[#555] leading-snug mt-2 pt-2 border-t border-[#0d0d0d]">{t.description}</p>
              )}
            </div>
          </div>
        )
      })}
      {!loading && !tasks.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <ListTodo size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No tasks in backlog
        </div>
      )}
      <p className="text-[9px] text-[#222] text-right px-1">{tasks.length} tasks</p>
    </div>
  )
}

const DIARY_AGENTS_LIST = ['ALICE', 'JARVIS', 'NEXUS', 'DUM', 'ADA']

function NewDiaryEntryModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [ag, setAg] = useState('ALICE')
  const [valence, setValence] = useState(0.5)
  const [arousal, setArousal] = useState(0.5)
  const [keyMoment, setKeyMoment] = useState('')
  const [pendingThread, setPendingThread] = useState('')
  const [relNote, setRelNote] = useState('')
  const [imp, setImp] = useState(7)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true); setErr('')
    try {
      const r = await fetch(`${SOUL_EMO}/api/soul/emotional-diary`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent: ag, valence, arousal, importance: imp,
          key_moment: keyMoment.trim() || undefined,
          pending_thread: pendingThread.trim() || undefined,
          relationship_note: relNote.trim() || undefined,
        }),
      })
      const d = await r.json()
      if (!r.ok || !d.ok) { setErr(d.error ?? 'failed'); return }
      onCreated(); onClose()
    } catch (ex) { setErr(String(ex)) }
    finally { setSaving(false) }
  }

  const valColor = valence >= 0.5 ? '#10b981' : valence >= 0.3 ? '#f59e0b' : '#ef4444'

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50" onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={submit}
        className="bg-[#080808] border border-[#1a1a1a] rounded-lg w-80 p-4 space-y-3 shadow-2xl">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-[#e5e5e5]">New Diary Entry</span>
          <button type="button" onClick={onClose} className="text-[#333] hover:text-[#777]"><X size={14} /></button>
        </div>
        <div>
          <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Agent</label>
          <div className="flex flex-wrap gap-1">
            {DIARY_AGENTS_LIST.map(a => {
              const isActive = ag === a
              const color = AGENT_COLORS[a] ?? '#555'
              return (
                <button key={a} type="button" onClick={() => setAg(a)}
                  className="px-2 py-1 rounded text-[10px] font-mono transition-colors"
                  style={isActive ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#444', border: '1px solid #1a1a1a' }}>
                  {a}
                </button>
              )
            })}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">
              Valence <span style={{ color: valColor }}>{valence.toFixed(2)}</span>
            </label>
            <input type="range" min={-1} max={1} step={0.05} value={valence} onChange={e => setValence(Number(e.target.value))}
              className="w-full accent-green-500" />
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">
              Arousal <span className="text-[#7c3aed]">{arousal.toFixed(2)}</span>
            </label>
            <input type="range" min={0} max={1} step={0.05} value={arousal} onChange={e => setArousal(Number(e.target.value))}
              className="w-full accent-violet-500" />
          </div>
        </div>
        <div>
          <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Key Moment</label>
          <textarea value={keyMoment} onChange={e => setKeyMoment(e.target.value)} rows={2}
            placeholder="what happened…"
            className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#ccc] outline-none resize-none placeholder:text-[#333]" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Pending Thread</label>
            <input value={pendingThread} onChange={e => setPendingThread(e.target.value)} placeholder="optional"
              className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#aaa] outline-none placeholder:text-[#333]" />
          </div>
          <div>
            <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Rel. Note</label>
            <input value={relNote} onChange={e => setRelNote(e.target.value)} placeholder="optional"
              className="w-full bg-[#111] border border-[#222] rounded px-2 py-1.5 text-xs text-[#aaa] outline-none placeholder:text-[#333]" />
          </div>
        </div>
        <div>
          <label className="text-[9px] text-[#444] uppercase tracking-wider block mb-1">Importance {imp}</label>
          <input type="range" min={1} max={10} step={1} value={imp} onChange={e => setImp(Number(e.target.value))}
            className="w-full accent-amber-500" />
        </div>
        {err && <p className="text-[9px] text-red-400">{err}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="px-3 py-1 text-xs text-[#444] hover:text-[#666]">Cancel</button>
          <button type="submit" disabled={saving}
            className="px-3 py-1 rounded bg-[#7c3aed] text-white text-xs font-medium hover:bg-[#6d28d9] disabled:opacity-50">
            {saving ? 'Saving…' : 'Log Entry'}
          </button>
        </div>
      </form>
    </div>
  )
}

function EmotionalDiaryPanel({ agentFilter }: { agentFilter: string }) {
  const [items, setItems] = useState<EmoDiaryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [tick, setTick] = useState(0)
  const [deleting, setDeleting] = useState<Set<number>>(new Set())
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [diarySort, setDiarySort] = useState<'valence' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const q = new URLSearchParams({ limit: '30' })
        if (agentFilter && agentFilter !== 'all') q.set('agent', agentFilter)
        const r = await fetch(`${SOUL_EMO}/api/soul/emotional-diary?${q}`)
        const d = await r.json()
        if (active) setItems(d.entries ?? [])
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [agentFilter, tick])

  async function handleDelete(id: number, e: React.MouseEvent) {
    e.stopPropagation()
    if (confirmDel !== id) { setConfirmDel(id); return }
    setDeleting(prev => new Set(prev).add(id))
    setConfirmDel(null)
    try {
      await fetch(`${SOUL_EMO}/api/soul/emotional-diary/${id}`, { method: 'DELETE' })
      setItems(prev => prev.filter(x => x.id !== id))
    } finally {
      setDeleting(prev => { const n = new Set(prev); n.delete(id); return n })
    }
  }

  return (
    <div className="space-y-2">
      {showNew && <NewDiaryEntryModal onClose={() => setShowNew(false)} onCreated={() => setTick(t => t + 1)} />}
      <div className="flex items-center gap-2 mb-1">
        <button onClick={() => setShowNew(true)}
          className="flex items-center gap-1 px-2 py-0.5 rounded border border-[#1a1a1a] text-[9px] text-[#555] hover:text-[#888] hover:border-[#333] transition-colors">
          <Plus size={9} /> New Entry
        </button>
        <button onClick={() => setDiarySort(s => s === 'valence' ? 'date' : 'valence')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
          style={diarySort === 'valence' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ lows
        </button>
      </div>
      {loading && !items.length && (
        <div className="text-xs text-[#444] py-6 text-center">Loading emotional diary…</div>
      )}
      {(diarySort === 'valence' ? [...items].sort((a, b) => Number(a.valence) - Number(b.valence)) : items).map(e => {
        const color = AGENT_COLORS[e.agent] ?? '#666'
        const isExp = expanded === e.id
        const valence = Number(e.valence)
        const arousal = Number(e.arousal)
        const valenceColor = valence >= 0.6 ? '#10b981' : valence >= 0.4 ? '#f59e0b' : '#ef4444'
        const isDel = deleting.has(e.id)
        const isConfirm = confirmDel === e.id
        return (
          <div key={e.id}
            className="group rounded border border-[#0f0f0f] bg-[#050505] overflow-hidden cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: color, borderLeftWidth: 2 }}
            onClick={() => { setConfirmDel(null); setExpanded(x => x === e.id ? null : e.id) }}>
            <div className="px-3 py-2.5">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-[9px] font-semibold" style={{ color }}>{e.agent}</span>
                <span className="text-[9px] px-1 py-0.5 rounded bg-[#0a0a0a] text-[#333]">imp {e.importance}</span>
                <span className="text-[9px] text-[#333] ml-auto">{new Date(e.created_at).toLocaleDateString('es-PE', { month: 'short', day: 'numeric' })}</span>
                <button onClick={ev => handleDelete(e.id, ev)} disabled={isDel}
                  className={`opacity-0 group-hover:opacity-100 transition-opacity px-1 py-0.5 rounded text-[9px] ${
                    isConfirm ? 'text-red-400 border border-red-800' : 'text-[#333] hover:text-red-400'
                  }`}>
                  {isDel ? '…' : isConfirm ? 'confirm?' : <Trash2 size={9} />}
                </button>
              </div>
              {/* Valence + Arousal gauges */}
              <div className="grid grid-cols-2 gap-2 mb-2">
                <div>
                  <p className="text-[9px] text-[#333] mb-0.5">valence</p>
                  <div className="h-1.5 bg-[#111] rounded overflow-hidden">
                    <div className="h-full rounded" style={{ width: `${valence * 100}%`, backgroundColor: valenceColor }} />
                  </div>
                  <p className="text-[9px] font-mono mt-0.5" style={{ color: valenceColor }}>{valence.toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-[9px] text-[#333] mb-0.5">arousal</p>
                  <div className="h-1.5 bg-[#111] rounded overflow-hidden">
                    <div className="h-full rounded bg-[#7c3aed]" style={{ width: `${arousal * 100}%` }} />
                  </div>
                  <p className="text-[9px] font-mono text-[#7c3aed] mt-0.5">{arousal.toFixed(2)}</p>
                </div>
              </div>
              {e.key_moment && (
                <p className={`text-xs text-[#666] leading-snug ${isExp ? '' : 'line-clamp-2'}`}>{e.key_moment}</p>
              )}
              {isExp && (
                <div className="mt-2 space-y-2 pt-2 border-t border-[#0d0d0d]">
                  {e.pending_thread && (
                    <div>
                      <p className="text-[9px] text-[#333] uppercase tracking-wider mb-0.5">pending thread</p>
                      <p className="text-xs text-[#f59e0b] leading-snug">{e.pending_thread}</p>
                    </div>
                  )}
                  {e.relationship_note && (
                    <div>
                      <p className="text-[9px] text-[#333] uppercase tracking-wider mb-0.5">relationship note</p>
                      <p className="text-xs text-[#60a5fa] leading-snug">{e.relationship_note}</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )
      })}
      {!loading && !items.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          <HeartPulse size={22} className="mx-auto mb-2 text-[#1a1a1a]" />
          No emotional diary entries
        </div>
      )}
      <p className="text-[9px] text-[#222] text-right px-1">{items.length} entries</p>
    </div>
  )
}

interface GamTopic {
  id: number
  agent: string
  topic: string
  summary: string
  event_count: number
  relevance_score: number
  first_seen: string
  last_updated: string
}

interface GamEvent {
  id: number
  agent: string
  topic_id: number
  event: string
  causal_direction: string
  related_event_ids: number[]
  metadata: string | null
  event_timestamp: string
}

const GAM_AGENTS = ['all', 'ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM'] as const

function GAMPanel() {
  const [topics, setTopics] = useState<GamTopic[]>([])
  const [events, setEvents] = useState<GamEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedTopic, setSelectedTopic] = useState<number | null>(null)
  const [gamAgent, setGamAgent] = useState<string>('all')
  const [gamSort, setGamSort] = useState<'events' | 'date'>('date')
  const [topicEvSort, setTopicEvSort] = useState<'agent' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/gam-topics`)
        const d = await r.json()
        if (active) {
          setTopics(d.topics ?? [])
          setEvents(d.events ?? [])
        }
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  const baseTopics = gamAgent === 'all' ? topics : topics.filter(t => t.agent === gamAgent)
  const filteredTopics = gamSort === 'events' ? [...baseTopics].sort((a, b) => b.event_count - a.event_count) : baseTopics
  const topicEvents = selectedTopic != null
    ? events.filter(e => e.topic_id === selectedTopic)
    : events

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1 flex-wrap">
        {GAM_AGENTS.map(a => {
          const cnt = a === 'all' ? topics.length : topics.filter(t => t.agent === a).length
          const color = a === 'all' ? '#555' : (AGENT_COLORS[a] ?? '#555')
          return (
            <button key={a} onClick={() => { setGamAgent(a); setSelectedTopic(null) }}
              className="text-[10px] px-2 py-0.5 rounded transition-colors"
              style={gamAgent === a ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' } : { color: '#333', border: '1px solid transparent' }}>
              {a} ({cnt})
            </button>
          )
        })}
        <button onClick={() => setGamSort(s => s === 'events' ? 'date' : 'events')}
          className="ml-auto px-2 py-0.5 rounded text-[10px] border transition-colors"
          style={gamSort === 'events' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#111', color: '#333' }}>
          ↓ events
        </button>
      </div>
      {loading && <div className="text-xs text-[#444] py-6 text-center">Loading GAM…</div>}

      {/* Topics */}
      {filteredTopics.map(t => {
        const agColor = AGENT_COLORS[t.agent] ?? '#666'
        const rel = Math.round(Number(t.relevance_score) * 100)
        const isSelected = selectedTopic === t.id
        return (
          <div key={t.id}
            className={`rounded-lg border bg-[#050505] p-3 cursor-pointer transition-colors ${
              isSelected ? 'border-[#7c3aed]' : 'border-[#0f0f0f] hover:border-[#1a1a1a]'
            }`}
            onClick={() => setSelectedTopic(isSelected ? null : t.id)}>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium text-[#e5e5e5] flex-1 min-w-0 truncate">{t.topic}</span>
              <span className="text-[9px] px-1 rounded bg-[#111] text-[#555]">{t.event_count} events</span>
              <span className="text-[10px] font-medium" style={{ color: agColor }}>{t.agent}</span>
            </div>
            <div className="flex items-center gap-2 mb-1.5">
              <div className="flex items-center gap-1">
                <div className="w-12 h-1 bg-[#111] rounded-full overflow-hidden">
                  <div className="h-full rounded-full bg-[#7c3aed]" style={{ width: `${rel}%` }} />
                </div>
                <span className="text-[9px] text-[#555]">{rel}% relevance</span>
              </div>
              <span className="text-[9px] text-[#2a2a2a] ml-auto">{new Date(t.last_updated).toLocaleDateString('es-PE')}</span>
            </div>
            <p className="text-[10px] text-[#444] italic">{t.summary}</p>

            {/* Events for this topic */}
            {isSelected && topicEvents.length > 0 && (
              <div className="mt-2 pt-2 border-t border-[#111] space-y-1.5">
                <div className="flex items-center justify-end mb-1">
                  <button onClick={e => { e.stopPropagation(); setTopicEvSort(s => s === 'agent' ? 'date' : 'agent') }}
                    className="px-1.5 py-0.5 rounded border text-[8px] transition-colors"
                    style={topicEvSort === 'agent' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
                    ↓ agent
                  </button>
                </div>
                {(topicEvSort === 'agent'
                  ? [...topicEvents].sort((a, b) => (a.agent ?? '').localeCompare(b.agent ?? ''))
                  : topicEvents
                ).map(ev => {
                  const meta = ev.metadata ? (() => { try { return JSON.parse(ev.metadata!); } catch { return null } })() : null
                  const dirColor = ev.causal_direction === 'cause' ? '#f59e0b' : '#38bdf8'
                  return (
                    <div key={ev.id} className="flex items-start gap-2">
                      <span className="text-[8px] px-1 rounded shrink-0 mt-0.5" style={{ backgroundColor: dirColor + '20', color: dirColor }}>
                        {ev.causal_direction}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-[10px] text-[#888]">{ev.event}</p>
                        {meta?.status && <span className="text-[8px] text-[#333]">status: {meta.status}</span>}
                        {ev.related_event_ids?.length > 0 && (
                          <span className="text-[8px] text-[#222] ml-2">→ [{ev.related_event_ids.join(', ')}]</span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
      {!loading && !topics.length && (
        <div className="text-xs text-[#333] py-8 text-center">No GAM topics</div>
      )}
    </div>
  )
}

interface Belief {
  id: number
  agent: string
  topic: string
  content: string
  confidence: number
  evidence_count: number
  valid_from: string
  invalid_at: string | null
  created_at: string
}

const BELIEF_AGENTS = ['all', 'ALICE', 'JARVIS', 'NEXUS', 'DUM', 'ADA']

function BeliefPanel({ agentFilter }: { agentFilter: string }) {
  const [items, setItems] = useState<Belief[]>([])
  const [loading, setLoading] = useState(true)
  const [agent, setAgent] = useState(agentFilter === 'all' ? 'all' : agentFilter)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [beliefSearch, setBeliefSearch] = useState('')
  const [tick, setTick] = useState(0)
  const [limit, setLimit] = useState(100)

  useEffect(() => {
    setAgent(agentFilter === 'all' ? 'all' : agentFilter)
  }, [agentFilter])

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const q = new URLSearchParams({ limit: String(limit) })
        if (agent !== 'all') q.set('agent', agent)
        const r = await fetch(`${SOUL}/api/soul/beliefs?${q}`)
        const d = await r.json()
        if (active) setItems(d.beliefs ?? [])
      } catch { if (active) setItems([]) }
      finally { if (active) setLoading(false) }
    }
    load()
    const iv = setInterval(() => setTick(t => t + 1), 60_000)
    return () => { active = false; clearInterval(iv) }
  }, [agent, tick, limit])

  const [beliefSort, setBeliefSort] = useState<'confidence' | 'evidence' | 'date'>('date')
  const avgConf = items.length ? Math.round(items.reduce((s, b) => s + b.confidence, 0) / items.length * 100) : 0
  const highConf = items.filter(b => b.confidence >= 0.75).length
  const lowConf = items.filter(b => b.confidence < 0.5).length
  const filteredBeliefs = beliefSearch.trim()
    ? items.filter(b => b.topic.toLowerCase().includes(beliefSearch.toLowerCase()) || b.content.toLowerCase().includes(beliefSearch.toLowerCase()))
    : items
  const visibleBeliefs = beliefSort === 'confidence' ? [...filteredBeliefs].sort((a, b) => b.confidence - a.confidence)
    : beliefSort === 'evidence' ? [...filteredBeliefs].sort((a, b) => b.evidence_count - a.evidence_count)
    : filteredBeliefs

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 bg-[#0a0a0a] border border-[#1a1a1a] rounded px-2 py-1.5">
        <Search size={11} className="text-[#333] flex-shrink-0" />
        <input value={beliefSearch} onChange={e => setBeliefSearch(e.target.value)}
          placeholder="search beliefs…"
          className="flex-1 bg-transparent text-xs text-[#aaa] outline-none placeholder-[#333]" />
        {beliefSearch && <button onClick={() => setBeliefSearch('')} className="text-[#333] hover:text-[#666]"><X size={9} /></button>}
      </div>
      <div className="flex items-center gap-1 flex-wrap">
        {BELIEF_AGENTS.map(a => {
          const color = AGENT_COLORS[a]
          const cnt = a === 'all' ? items.length : items.filter(i => i.agent === a).length
          return (
            <button key={a} onClick={() => setAgent(a)}
              className={`text-[10px] px-2 py-0.5 rounded transition-colors ${
                agent === a ? 'bg-[#1a1a1a]' : 'text-[#333] hover:text-[#666]'
              }`}
              style={agent === a ? { color: color ?? '#e5e5e5' } : {}}>
              {a} ({cnt})
            </button>
          )
        })}
        {loading && <span className="text-[9px] text-[#333] animate-pulse ml-2">loading…</span>}
        <span className="text-[9px] text-[#222]">60s auto-refresh</span>
        <button onClick={() => setBeliefSort(s => s === 'confidence' ? 'evidence' : s === 'evidence' ? 'date' : 'confidence')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors ml-auto"
          style={beliefSort !== 'date' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          {beliefSort === 'confidence' ? '↓ confidence' : beliefSort === 'evidence' ? '↓ evidence' : '↓ sort'}
        </button>
      </div>
      {items.length > 0 && (
        <div className="flex items-center gap-4 rounded bg-[#050505] border border-[#0a0a0a] px-3 py-2">
          <div className="flex flex-col items-center">
            <span className="text-[9px] text-[#333]">avg confidence</span>
            <span className="text-xs font-mono"
              style={{ color: avgConf >= 75 ? '#10b981' : avgConf >= 50 ? '#f59e0b' : '#ef4444' }}>
              {avgConf}%
            </span>
          </div>
          <div className="flex flex-col items-center">
            <span className="text-[9px] text-[#333]">high ≥75%</span>
            <span className="text-xs font-mono text-[#10b981]">{highConf}</span>
          </div>
          <div className="flex flex-col items-center">
            <span className="text-[9px] text-[#333]">low &lt;50%</span>
            <span className="text-xs font-mono text-[#ef4444]">{lowConf}</span>
          </div>
        </div>
      )}
      {visibleBeliefs.map(b => {
        const conf = Math.round(b.confidence * 100)
        const confColor = conf >= 75 ? '#10b981' : conf >= 50 ? '#f59e0b' : '#ef4444'
        const agColor = AGENT_COLORS[b.agent] ?? '#666'
        const isExp = expanded === b.id
        return (
          <div key={b.id}
            className="rounded-lg border border-[#0f0f0f] bg-[#050505] p-3 cursor-pointer hover:border-[#1a1a1a]"
            style={{ borderLeftColor: agColor, borderLeftWidth: 2 }}
            onClick={() => setExpanded(e => e === b.id ? null : b.id)}>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium text-[#e5e5e5] flex-1 min-w-0 truncate">{b.topic}</span>
              <span className="text-[10px] font-medium" style={{ color: agColor }}>{b.agent}</span>
            </div>
            <div className="flex items-center gap-3 mb-1.5">
              <div className="flex items-center gap-1.5">
                <div className="w-16 h-1 bg-[#111] rounded-full overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${conf}%`, backgroundColor: confColor }} />
                </div>
                <span className="text-[10px]" style={{ color: confColor }}>{conf}%</span>
              </div>
              {b.evidence_count > 0 && (
                <span className="text-[9px] text-[#444]">{b.evidence_count} evidence</span>
              )}
              <span className="text-[9px] text-[#2a2a2a] ml-auto">{new Date(b.created_at).toLocaleDateString('es-PE')}</span>
            </div>
            <p className={`text-xs text-[#666] leading-relaxed ${isExp ? '' : 'line-clamp-2'}`}>{b.content}</p>
          </div>
        )
      })}
      {!loading && !visibleBeliefs.length && (
        <div className="text-xs text-[#333] py-8 text-center">
          {items.length ? 'No beliefs match search' : 'No beliefs recorded'}
        </div>
      )}
      {!loading && !beliefSearch && items.length >= limit && (
        <button onClick={() => setLimit(l => l + 50)}
          className="w-full py-1.5 text-[10px] text-[#444] hover:text-[#777] border border-[#111] hover:border-[#222] rounded transition-colors">
          Load more ({limit} shown)
        </button>
      )}
    </div>
  )
}

interface JournalEntry {
  id: number
  agent: string
  session_date: string
  entry: string
  mood: string
  key_moments: string
  style_anchor: string
  created_at: string
}

function JournalPanel({ agentFilter }: { agentFilter: string }) {
  const [entries, setEntries] = useState<JournalEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [moodFilter, setMoodFilter] = useState('all')
  const [jrnSort, setJrnSort] = useState<'moments' | 'date'>('date')
  const [tick, setTick] = useState(0)
  const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost'

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const params = agentFilter !== 'all' ? `?agent=${agentFilter}&limit=50` : '?limit=50'
        const r = await fetch(`http://${host}:8800/api/soul/agent-journal${params}`)
        const d = await r.json()
        if (active) setEntries(d.entries || [])
      } catch {} finally { if (active) setLoading(false) }
    }
    load()
    const iv = setInterval(() => setTick(t => t + 1), 60_000)
    return () => { active = false; clearInterval(iv) }
  }, [host, agentFilter, tick])

  const toggleExpand = (id: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const fmtDate = (d: string) => {
    try { return new Date(d).toLocaleDateString('es-PE', { year: 'numeric', month: 'short', day: 'numeric' }) }
    catch { return d }
  }

  const moods = ['all', ...Array.from(new Set(entries.map(e => e.mood).filter(Boolean))).sort()]
  const filteredEntries = moodFilter === 'all' ? entries : entries.filter(e => e.mood === moodFilter)
  const visibleEntries = jrnSort === 'moments'
    ? [...filteredEntries].sort((a, b) => {
        let ma: string[] = []; let mb: string[] = []
        try { ma = JSON.parse(a.key_moments || '[]') } catch {}
        try { mb = JSON.parse(b.key_moments || '[]') } catch {}
        return mb.length - ma.length
      })
    : filteredEntries

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1 flex-wrap">
        {moods.length > 1 && moods.map(m => (
          <button key={m} onClick={() => setMoodFilter(m)}
            className={`px-2 py-0.5 rounded text-[9px] border transition-colors ${moodFilter === m ? 'bg-[#111] border-[#333] text-[#ccc]' : 'border-[#1a1a1a] text-[#333] hover:text-[#555]'}`}>
            {m}
          </button>
        ))}
        <button onClick={() => setJrnSort(s => s === 'moments' ? 'date' : 'moments')}
          className="ml-auto px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={jrnSort === 'moments' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ moments
        </button>
      </div>
      {loading && <div className="text-xs text-[#444] text-center py-6">Loading journal…</div>}
      {visibleEntries.map(e => {
        const ac = AGENT_COLORS[e.agent] ?? '#666'
        const isExp = expanded.has(e.id)
        let moments: string[] = []
        try { moments = JSON.parse(e.key_moments || '[]') } catch {}

        return (
          <div key={e.id}
            className="rounded border border-[#111] bg-[#030303] p-3 cursor-pointer hover:border-[#1a1a1a] transition-colors"
            style={{ borderLeftColor: ac, borderLeftWidth: 2 }}
            onClick={() => toggleExpand(e.id)}>
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-[9px] font-semibold" style={{ color: ac }}>{e.agent}</span>
              <span className="text-[9px] text-[#444]">{fmtDate(e.session_date)}</span>
              {e.mood && (
                <span className="text-[9px] px-1.5 py-0.5 rounded"
                  style={{ backgroundColor: ac + '18', color: ac }}>{e.mood}</span>
              )}
              <ChevronDown size={10} className={`ml-auto text-[#333] transition-transform ${isExp ? 'rotate-180' : ''}`} />
            </div>
            <p className={`text-[10px] text-[#888] leading-relaxed italic ${isExp ? '' : 'line-clamp-3'}`}>{e.entry}</p>
            {isExp && moments.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {moments.map((m, i) => (
                  <span key={i} className="text-[9px] px-1.5 py-0.5 rounded bg-[#0a0a0a] text-[#555] border border-[#111]">{m}</span>
                ))}
              </div>
            )}
          </div>
        )
      })}
      {!loading && !entries.length && (
        <div className="text-xs text-[#333] text-center py-6">No journal entries</div>
      )}
    </div>
  )
}

interface SoulEvent {
  event_type: string
  agent: string
  created_at: string
  body: string
  sub: string
}

const TIMELINE_TYPE_COLORS: Record<string, string> = {
  thought: '#10b981',
  drift: '#818cf8',
  lifecycle: '#f59e0b',
  nerve_fire: '#ef4444',
}

const TIMELINE_AGENTS = ['all', 'ALICE', 'JARVIS', 'NEXUS', 'DUM', 'ADA'] as const

function SoulTimelinePanel() {
  const [events, setEvents] = useState<SoulEvent[]>([])
  const [agentF, setAgentF] = useState('all')
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const [tlSort, setTlSort] = useState<'agent' | 'date'>('date')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const r = await fetch(`${SOUL}/api/soul/soul-timeline?agent=${agentF}&limit=80`)
        const d = await r.json()
        if (active) setEvents(d.events ?? [])
      } catch { if (active) setEvents([]) }
      finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [agentF, tick])

  useEffect(() => {
    const iv = setInterval(() => setTick(t => t + 1), 15_000)
    return () => clearInterval(iv)
  }, [])

  function fmtRel(iso: string) {
    try {
      const diff = Date.now() - new Date(iso).getTime()
      const m = Math.floor(diff / 60000)
      if (m < 1) return 'just now'
      if (m < 60) return `${m}m ago`
      const h = Math.floor(m / 60)
      if (h < 24) return `${h}h ago`
      return `${Math.floor(h / 24)}d ago`
    } catch { return '' }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-0.5">
          {TIMELINE_AGENTS.map(a => {
            const isActive = agentF === a
            const color = a === 'all' ? '#555' : (AGENT_COLORS[a] ?? '#555')
            return (
              <button key={a} onClick={() => setAgentF(a)}
                className="px-1.5 py-0.5 rounded text-[9px] font-mono transition-colors"
                style={isActive
                  ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' }
                  : { color: '#333', border: '1px solid transparent' }}>
                {a === 'all' ? 'all' : a.slice(0, 2)}
              </button>
            )
          })}
        </div>
        <button onClick={() => setTlSort(s => s === 'agent' ? 'date' : 'agent')}
          className="px-2 py-0.5 rounded border text-[9px] transition-colors"
          style={tlSort === 'agent' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ agent
        </button>
        <button onClick={() => setTick(t => t + 1)}
          className="ml-auto px-2 py-1 rounded text-[10px] border border-[#111] text-[#333] hover:text-[#555] transition-colors">
          refresh
        </button>
        {loading && <span className="text-[9px] text-[#333] animate-pulse">loading…</span>}
      </div>
      <div className="space-y-1.5">
        {(tlSort === 'agent'
          ? [...events].sort((a, b) => a.agent.localeCompare(b.agent))
          : events
        ).map((ev, i) => {
          const tc = TIMELINE_TYPE_COLORS[ev.event_type] ?? '#555'
          const ac = AGENT_COLORS[ev.agent] ?? '#555'
          return (
            <div key={i} className="flex items-start gap-2 rounded bg-[#030303] border border-[#0a0a0a] px-2.5 py-2"
              style={{ borderLeftColor: tc, borderLeftWidth: 2 }}>
              <div className="flex flex-col gap-0.5 flex-shrink-0 w-16">
                <span className="text-[8px] px-1 py-0.5 rounded text-center" style={{ backgroundColor: tc + '20', color: tc }}>
                  {ev.event_type.replace('_', ' ')}
                </span>
                <span className="text-[8px] font-semibold text-center" style={{ color: ac }}>{ev.agent}</span>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-[9px] text-[#ccc] leading-relaxed line-clamp-2">{ev.body}</p>
                {ev.sub && <p className="text-[8px] text-[#444] mt-0.5">{ev.sub}</p>}
              </div>
              <span className="text-[8px] text-[#222] flex-shrink-0 whitespace-nowrap">{fmtRel(ev.created_at)}</span>
            </div>
          )
        })}
      </div>
      {!loading && !events.length && (
        <div className="text-xs text-[#333] text-center py-8">No timeline events</div>
      )}
    </div>
  )
}

interface EmoDay { day: string; emotions: Record<string, number> }
const EMO_LABELS = ['calm', 'focused', 'curious', 'reflective', 'alert', 'stressed', 'other']
const EMO_COLORS: Record<string, string> = {
  calm: '#10b981', focused: '#2563eb', curious: '#7c3aed',
  reflective: '#f59e0b', alert: '#f97316', stressed: '#ef4444', other: '#555',
}

function EmotionalTimelinePanel({ agentFilter }: { agentFilter: string }) {
  const [data, setData] = useState<Record<string, EmoDay[]>>({})
  const [labels, setLabels] = useState<string[]>([])
  const [days, setDays] = useState(14)
  const [loading, setLoading] = useState(true)
  const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
  const SOUL_EMO = `http://${host}:8800`
  const AGENTS_EMO = ['ALICE', 'JARVIS', 'NEXUS', 'ADA', 'DUM']
  const AGENT_COLORS_EMO: Record<string, string> = { ALICE: '#7c3aed', JARVIS: '#2563eb', NEXUS: '#059669', DUM: '#d97706', ADA: '#dc2626' }

  useEffect(() => {
    let active = true
    setLoading(true)
    fetch(`${SOUL_EMO}/api/soul/emotional-timeline?days=${days}`)
      .then(r => r.json())
      .then(d => {
        if (active) {
          setData(d.agents ?? {})
          setLabels(d.labels ?? EMO_LABELS)
        }
      })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [days])

  const agentsToShow = agentFilter === 'all' ? AGENTS_EMO : [agentFilter]

  if (loading) return <div className="text-xs text-[#444] py-8 text-center">Loading emotional timeline…</div>

  return (
    <div className="space-y-4 p-1">
      <div className="flex items-center gap-2">
        <p className="text-[10px] text-[#555] uppercase tracking-wider flex-1">Emotional State Timeline</p>
        <div className="flex items-center gap-0.5">
          {([7, 14, 30] as const).map(d => (
            <button key={d} onClick={() => setDays(d)}
              className="px-2 py-0.5 rounded text-[9px] font-mono transition-colors"
              style={days === d
                ? { color: '#7c3aed', border: '1px solid #7c3aed44', backgroundColor: '#7c3aed18' }
                : { color: '#333', border: '1px solid transparent' }}>
              {d}d
            </button>
          ))}
        </div>
      </div>
      {agentsToShow.map(ag => {
        const rows = (data[ag] ?? []).slice().sort((a, b) => a.day.localeCompare(b.day))
        if (!rows.length) return (
          <div key={ag} className="rounded border border-[#111] bg-[#050505] p-3">
            <p className="text-xs font-medium mb-1" style={{ color: AGENT_COLORS_EMO[ag] ?? '#888' }}>{ag}</p>
            <p className="text-[10px] text-[#333]">No data for this period</p>
          </div>
        )
        const allCounts = rows.flatMap(r => Object.values(r.emotions))
        const maxCount = Math.max(...allCounts, 1)
        return (
          <div key={ag} className="rounded border border-[#111] bg-[#050505] p-3">
            <p className="text-xs font-medium mb-2" style={{ color: AGENT_COLORS_EMO[ag] ?? '#888' }}>{ag}</p>
            <div className="overflow-x-auto">
              <div className="flex gap-0.5 min-w-0">
                {rows.map(row => {
                  const total = Object.values(row.emotions).reduce((a, b) => a + b, 0)
                  const dominant = Object.entries(row.emotions).sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'other'
                  return (
                    <div key={row.day} className="flex flex-col items-center gap-0.5 flex-1 min-w-[28px]"
                      title={`${row.day}\n${Object.entries(row.emotions).map(([k, v]) => `${k}: ${v}`).join('\n')}`}>
                      <div className="w-full flex flex-col-reverse gap-px" style={{ height: 48 }}>
                        {labels.map(lbl => {
                          const cnt = row.emotions[lbl] ?? 0
                          if (cnt === 0) return null
                          const h = Math.max((cnt / maxCount) * 48, 2)
                          return (
                            <div key={lbl} className="w-full rounded-sm"
                              style={{ height: h, backgroundColor: EMO_COLORS[lbl] ?? '#555', opacity: dominant === lbl ? 1 : 0.6 }} />
                          )
                        })}
                      </div>
                      <span className="text-[7px] text-[#333] rotate-[-60deg] origin-center mt-1 whitespace-nowrap">
                        {row.day.slice(5)}
                      </span>
                      <span className="text-[8px] text-[#555]">{total}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        )
      })}
      <div className="flex gap-3 flex-wrap mt-2">
        {(labels.length ? labels : EMO_LABELS).map(lbl => (
          <div key={lbl} className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-sm" style={{ backgroundColor: EMO_COLORS[lbl] ?? '#555' }} />
            <span className="text-[9px] text-[#444]">{lbl}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

interface DiaryEntry { id: number; agent: string; session_date: string; entry: string; mood: string; key_moments: string[]; style_anchor: unknown }

function AgentDiaryPanel({ agentFilter }: { agentFilter: string }) {
  const [entries, setEntries] = useState<DiaryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [diarySrt, setDiarySrt] = useState<'moments' | 'date'>('date')
  const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
  const SOUL_D = `http://${host}:8800`
  const AGENT_COLORS_D: Record<string, string> = { ALICE: '#7c3aed', JARVIS: '#2563eb', NEXUS: '#059669', DUM: '#d97706', ADA: '#dc2626' }

  useEffect(() => {
    let active = true
    setLoading(true)
    const param = agentFilter !== 'all' ? `agent=${agentFilter}&` : ''
    fetch(`${SOUL_D}/api/soul/agent-diary?${param}limit=20`)
      .then(r => r.json())
      .then(d => { if (active) setEntries(d.entries ?? []) })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [agentFilter])

  if (loading) return <div className="text-xs text-[#444] py-8 text-center">Loading diary…</div>
  if (!entries.length) return <div className="text-xs text-[#333] py-8 text-center">No diary entries</div>

  function moodColor(mood: string) {
    const m = mood.toLowerCase()
    if (m.includes('grateful') || m.includes('happy') || m.includes('satisf')) return '#10b981'
    if (m.includes('curious') || m.includes('inspired')) return '#7c3aed'
    if (m.includes('calm') || m.includes('steady')) return '#2563eb'
    if (m.includes('stress') || m.includes('anxious')) return '#ef4444'
    return '#f59e0b'
  }

  const visibleDiary = diarySrt === 'moments'
    ? [...entries].sort((a, b) => (b.key_moments?.length ?? 0) - (a.key_moments?.length ?? 0))
    : entries

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[9px] text-[#222]">{entries.length} entries</span>
        <button onClick={() => setDiarySrt(s => s === 'moments' ? 'date' : 'moments')}
          className="ml-auto px-2 py-0.5 rounded text-[9px] border transition-colors"
          style={diarySrt === 'moments' ? { borderColor: '#10b981', color: '#10b981', backgroundColor: '#10b98118' } : { borderColor: '#1a1a1a', color: '#333' }}>
          ↓ moments
        </button>
      </div>
      {visibleDiary.map(e => {
        const isExp = expanded === e.id
        return (
          <div key={e.id} className="rounded border border-[#111] bg-[#050505] overflow-hidden"
            style={{ borderLeftColor: AGENT_COLORS_D[e.agent] ?? '#333', borderLeftWidth: 2 }}>
            <button className="w-full px-3 py-2.5 text-left flex items-start gap-2"
              onClick={() => setExpanded(isExp ? null : e.id)}>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-xs font-medium" style={{ color: AGENT_COLORS_D[e.agent] ?? '#888' }}>{e.agent}</span>
                  <span className="text-[10px] text-[#555]">{e.session_date}</span>
                  <span className="text-[9px] px-1.5 py-0.5 rounded-full ml-auto"
                    style={{ backgroundColor: moodColor(e.mood) + '22', color: moodColor(e.mood) }}>
                    {e.mood}
                  </span>
                </div>
                <p className="text-[11px] text-[#888] line-clamp-2 leading-relaxed">{e.entry}</p>
              </div>
              <ChevronDown size={10} className={`flex-shrink-0 mt-1 text-[#444] transition-transform ${isExp ? 'rotate-180' : ''}`} />
            </button>
            {isExp && (
              <div className="px-3 pb-3 space-y-2 border-t border-[#0f0f0f]">
                <p className="text-xs text-[#ccc] leading-relaxed pt-2">{e.entry}</p>
                {e.key_moments?.length > 0 && (
                  <div>
                    <p className="text-[9px] text-[#444] uppercase tracking-wider mb-1">Key Moments</p>
                    <ul className="space-y-0.5">
                      {e.key_moments.map((m, i) => (
                        <li key={i} className="text-[10px] text-[#888] flex items-start gap-1.5">
                          <span className="text-[#444] flex-shrink-0">•</span>{m}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function SubconsciousView() {
  const [data, setData] = useState<SubconsciousData>({ diagnoses: [], thoughts: [], tasks: [] })
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<'thoughts' | 'diagnoses' | 'tasks' | 'nerves' | 'traces' | 'opinions' | 'exchanges' | 'curiosity' | 'events' | 'smemory' | 'emodiary' | 'backlog' | 'procedures' | 'beliefs' | 'gam' | 'journal' | 'timeline' | 'emotimeline' | 'diary' | 'dreams'>('thoughts')
  const [agent, setAgent] = useState('all')
  const [diagUpdating, setDiagUpdating] = useState<Record<number, boolean>>({})
  const [bulkDiag, setBulkDiag] = useState(false)
  const [diagStatusFilter, setDiagStatusFilter] = useState('all')
  const [diagSort, setDiagSort] = useState<'agent' | 'date'>('date')
  const [taskSort, setTaskSort] = useState<'agent' | 'deadline' | 'date'>('date')

  async function bulkAcceptAll(ids: number[]) {
    if (!ids.length || bulkDiag) return
    setBulkDiag(true)
    await Promise.allSettled(ids.map(id => updateDiagStatus(id, 'accepted')))
    setBulkDiag(false)
  }

  async function bulkRejectAll(ids: number[]) {
    if (!ids.length || bulkDiag) return
    setBulkDiag(true)
    await Promise.allSettled(ids.map(id => updateDiagStatus(id, 'rejected')))
    setBulkDiag(false)
  }

  async function updateDiagStatus(id: number, status: string) {
    setDiagUpdating(prev => ({ ...prev, [id]: true }))
    try {
      await fetch(`${SOUL}/api/soul/diagnoses/${id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      setData(prev => ({
        ...prev,
        diagnoses: prev.diagnoses.map(d => d.id === id ? { ...d, status } : d),
      }))
    } catch { /* ignore */ }
    finally { setDiagUpdating(prev => ({ ...prev, [id]: false })) }
  }

  async function load() {
    setLoading(true)
    try {
      const r = await fetch(`${SOUL}/api/soul/subconscious?agent=${agent}&limit=40`)
      const d = await r.json()
      setData(d)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }

  useEffect(() => {
    load()
    const iv = setInterval(load, 30_000)
    return () => clearInterval(iv)
  }, [agent])

  const pendingDiagnoses = data.diagnoses.filter(d => d.status === 'pending').length
  const activeTasks = data.tasks.filter(t => t.status === 'active').length

  const AGENTS = ['all', 'ALICE', 'JARVIS', 'NEXUS', 'DUM']

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="h-12 flex items-center gap-2 px-4 border-b border-[#0f0f0f] flex-shrink-0">
        <Cpu size={13} className="text-[#7c3aed]" />
        <span className="text-sm font-medium text-[#e5e5e5]">Subconscious</span>
        <span className="text-[10px] text-[#333] ml-1">background mind</span>
        <div className="flex items-center gap-1.5 ml-2">
          {pendingDiagnoses > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#f59e0b18] text-[#f59e0b]">
              {pendingDiagnoses} pending
            </span>
          )}
          {activeTasks > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#7c3aed18] text-[#7c3aed]">
              {activeTasks} active
            </span>
          )}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <div className="flex items-center gap-0.5">
            {AGENTS.map(a => {
              const isActive = agent === a
              const color = a === 'all' ? '#555' : (AGENT_COLORS[a] ?? '#555')
              return (
                <button key={a} onClick={() => setAgent(a)}
                  className="px-1.5 py-0.5 rounded text-[9px] font-mono transition-colors"
                  style={isActive
                    ? { color, border: `1px solid ${color}44`, backgroundColor: color + '18' }
                    : { color: '#333', border: '1px solid transparent' }}>
                  {a === 'all' ? 'all' : a.slice(0, 2)}
                </button>
              )
            })}
          </div>
          <button onClick={load}
            className="text-[10px] text-[#333] hover:text-[#666] transition-colors p-1 rounded border border-[#111] hover:border-[#222]">
            <RefreshCw size={10} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[#111] px-4 flex-shrink-0">
        {([
          { id: 'thoughts' as const, label: 'Stream', Icon: Radio },
          { id: 'diagnoses' as const, label: `Diagnoses (${data.diagnoses.length})`, Icon: AlertTriangle },
          { id: 'tasks' as const, label: `Tasks (${data.tasks.length})`, Icon: CheckCircle },
          { id: 'nerves' as const, label: 'Nerves', Icon: Zap },
          { id: 'traces' as const, label: 'Traces', Icon: GitFork },
          { id: 'opinions' as const, label: 'Opinions', Icon: MessageSquare },
          { id: 'exchanges' as const, label: 'Sessions', Icon: Layers },
          { id: 'curiosity' as const, label: 'Curiosity', Icon: Lightbulb },
          { id: 'events' as const, label: 'Events', Icon: Layers },
          { id: 'smemory' as const, label: 'S.Memory', Icon: Database },
          { id: 'emodiary' as const, label: 'Emo Diary', Icon: HeartPulse },
          { id: 'backlog' as const, label: 'Backlog', Icon: ListTodo },
          { id: 'procedures' as const, label: 'Procedures', Icon: BookText },
          { id: 'beliefs' as const, label: 'Beliefs', Icon: Sparkles },
          { id: 'gam' as const, label: 'GAM', Icon: Network },
          { id: 'journal' as const, label: 'Journal', Icon: BookHeart },
          { id: 'timeline' as const, label: 'Timeline', Icon: TimelineIcon },
          { id: 'emotimeline' as const, label: 'Emo Timeline', Icon: Activity },
          { id: 'diary' as const, label: 'Diary', Icon: NotebookPen },
          { id: 'dreams' as const, label: 'Dreams', Icon: Moon },
        ]).map(({ id, label, Icon }) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs transition-colors ${
              tab === id ? 'text-[#e5e5e5] border-b border-[#7c3aed]' : 'text-[#555] hover:text-[#888]'
            }`}>
            <Icon size={10} />
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
        {loading && (
          <div className="text-xs text-[#444] py-4 text-center">Loading subconscious…</div>
        )}

        {tab === 'thoughts' && <InnerMonologueStream agentFilter={agent} />}

        {tab === 'diagnoses' && (() => {
          const pending = data.diagnoses.filter(d => ['pending_review', 'pending'].includes(d.status))
          const accepted = data.diagnoses.filter(d => d.status === 'accepted')
          const applied = data.diagnoses.filter(d => d.status === 'applied')
          const rejected = data.diagnoses.filter(d => d.status === 'rejected')
          const diagStatusOpts = [
            { key: 'all', label: 'all', color: '#555', count: data.diagnoses.length },
            { key: 'pending', label: 'pending', color: '#f59e0b', count: pending.length },
            { key: 'accepted', label: 'accepted', color: '#7c3aed', count: accepted.length },
            { key: 'applied', label: 'applied', color: '#10b981', count: applied.length },
            { key: 'rejected', label: 'rejected', color: '#ef4444', count: rejected.length },
          ]
          const visibleDiagnoses = diagStatusFilter === 'all'
            ? data.diagnoses
            : diagStatusFilter === 'pending'
              ? pending
              : data.diagnoses.filter(d => d.status === diagStatusFilter)
          return (
            <>
              {/* Stats pipeline */}
              {data.diagnoses.length > 0 && (
                <div className="flex items-center gap-3 px-1 mb-2 pb-2 border-b border-[#0d0d0d]">
                  {[
                    { label: 'pending', count: pending.length, color: '#f59e0b' },
                    { label: 'accepted', count: accepted.length, color: '#7c3aed' },
                    { label: 'applied', count: applied.length, color: '#10b981' },
                  ].map(({ label, count, color }) => (
                    <span key={label} className="text-[10px]" style={{ color: count > 0 ? color : '#2a2a2a' }}>
                      {count} {label}
                    </span>
                  ))}
                  <span className="text-[10px] text-[#1a1a1a] ml-auto">self-repair loop</span>
                </div>
              )}
              {/* Status filter */}
              <div className="flex items-center gap-1 flex-wrap mb-2">
                {diagStatusOpts.map(({ key, label, color, count }) => (
                  <button key={key} onClick={() => setDiagStatusFilter(key)}
                    className={`text-[9px] px-2 py-0.5 rounded transition-colors ${
                      diagStatusFilter === key ? 'bg-[#1a1a1a]' : 'text-[#333] hover:text-[#666]'
                    }`}
                    style={diagStatusFilter === key ? { color } : {}}>
                    {label} ({count})
                  </button>
                ))}
                <button onClick={() => setDiagSort(s => s === 'agent' ? 'date' : 'agent')}
                  className="ml-auto px-2 py-0.5 rounded border text-[9px] transition-colors"
                  style={diagSort === 'agent' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
                  ↓ agent
                </button>
              </div>
              {pending.length > 0 && diagStatusFilter !== 'rejected' && (
                <div className="flex items-center gap-2 mb-2">
                  <button disabled={bulkDiag}
                    onClick={() => bulkAcceptAll(pending.map(d => d.id))}
                    className="text-[9px] px-2 py-0.5 rounded bg-[#7c3aed18] text-[#7c3aed] hover:bg-[#7c3aed30] disabled:opacity-40 transition-colors">
                    Accept All ({pending.length} pending)
                  </button>
                  <button disabled={bulkDiag}
                    onClick={() => bulkRejectAll(pending.map(d => d.id))}
                    className="text-[9px] px-2 py-0.5 rounded bg-[#ef444418] text-[#ef4444] hover:bg-[#ef444430] disabled:opacity-40 transition-colors">
                    Reject All ({pending.length} pending)
                  </button>
                </div>
              )}
              {(diagSort === 'agent'
                ? [...visibleDiagnoses].sort((a, b) => a.agent.localeCompare(b.agent))
                : visibleDiagnoses
              ).map(d => {
                const color = AGENT_COLORS[d.agent] ?? '#666'
                const sv = DIAG_STATUS_COLORS[d.status] ?? '#444'
                const busy = diagUpdating[d.id]
                const canAccept = ['pending_review', 'pending'].includes(d.status)
                const canApply = d.status === 'accepted'
                const canReject = ['pending_review', 'pending', 'accepted'].includes(d.status)
                return (
                  <div key={d.id} className="rounded border border-[#0f0f0f] bg-[#050505] px-3 py-2.5"
                    style={{ borderLeftColor: sv, borderLeftWidth: 2 }}>
                    <div className="flex items-center gap-2 mb-1.5">
                      {d.status === 'applied'
                        ? <CheckCircle size={10} className="text-[#10b981]" />
                        : <AlertTriangle size={10} style={{ color: sv }} />
                      }
                      <span className="text-[10px] font-semibold" style={{ color }}>{d.agent}</span>
                      <StatusBadge status={d.status} />
                      <span className="text-[10px] text-[#2a2a2a] ml-auto">{fmtRelTime(d.created_at)}</span>
                    </div>
                    <p className="text-xs text-[#888] leading-relaxed mb-2">{d.diagnosis}</p>
                    {(canAccept || canApply || canReject) && (
                      <div className="flex items-center gap-1.5 pt-1.5 border-t border-[#0d0d0d]">
                        {canAccept && (
                          <button disabled={busy}
                            onClick={() => updateDiagStatus(d.id, 'accepted')}
                            className="text-[9px] px-2 py-0.5 rounded bg-[#7c3aed18] text-[#7c3aed] hover:bg-[#7c3aed30] disabled:opacity-40 transition-colors">
                            Accept
                          </button>
                        )}
                        {canApply && (
                          <button disabled={busy}
                            onClick={() => updateDiagStatus(d.id, 'applied')}
                            className="text-[9px] px-2 py-0.5 rounded bg-[#10b98118] text-[#10b981] hover:bg-[#10b98130] disabled:opacity-40 transition-colors">
                            Apply
                          </button>
                        )}
                        {canReject && (
                          <button disabled={busy}
                            onClick={() => updateDiagStatus(d.id, 'rejected')}
                            className="text-[9px] px-2 py-0.5 rounded bg-[#ef444418] text-[#ef4444] hover:bg-[#ef444430] disabled:opacity-40 transition-colors ml-auto">
                            Reject
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </>
          )
        })()}

        {tab === 'tasks' && data.tasks.length > 0 && (
          <div className="flex items-center gap-1 mb-1">
            <button onClick={() => setTaskSort(s => s === 'agent' ? 'date' : 'agent')}
              className="px-2 py-0.5 rounded border text-[9px] transition-colors"
              style={taskSort === 'agent' ? { borderColor: '#7c3aed', color: '#7c3aed', backgroundColor: '#7c3aed18' } : { borderColor: '#1a1a1a', color: '#333' }}>
              ↓ agent
            </button>
            <button onClick={() => setTaskSort(s => s === 'deadline' ? 'date' : 'deadline')}
              className="px-2 py-0.5 rounded border text-[9px] transition-colors"
              style={taskSort === 'deadline' ? { borderColor: '#ef4444', color: '#ef4444', backgroundColor: '#ef444418' } : { borderColor: '#1a1a1a', color: '#333' }}>
              ↓ deadline
            </button>
          </div>
        )}
        {tab === 'tasks' && (taskSort === 'agent'
          ? [...data.tasks].sort((a, b) => a.agent.localeCompare(b.agent))
          : taskSort === 'deadline'
            ? [...data.tasks].sort((a, b) => {
                if (!a.deadline && !b.deadline) return 0
                if (!a.deadline) return 1
                if (!b.deadline) return -1
                return new Date(a.deadline).getTime() - new Date(b.deadline).getTime()
              })
            : data.tasks
        ).map(t => {
          const color = AGENT_COLORS[t.agent] ?? '#666'
          const isActive = t.status === 'active'
          const deadline = t.deadline ? new Date(t.deadline) : null
          const isOverdue = deadline ? deadline < new Date() : false
          return (
            <div key={t.id} className="rounded border border-[#0f0f0f] bg-[#050505] px-3 py-2.5"
              style={{ borderLeftColor: isActive ? color : '#1a1a1a', borderLeftWidth: 2 }}>
              <div className="flex items-center gap-2">
                {isActive
                  ? <div className="w-1.5 h-1.5 rounded-full bg-[#7c3aed] animate-pulse flex-shrink-0" />
                  : <div className="w-1.5 h-1.5 rounded-full bg-[#222] flex-shrink-0" />
                }
                <span className="text-xs text-[#ccc] flex-1">{t.title}</span>
                <StatusBadge status={t.status} />
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-[10px] font-semibold" style={{ color }}>{t.agent}</span>
                {deadline && (
                  <span className={`text-[10px] flex items-center gap-1 ${isOverdue ? 'text-[#ef4444]' : 'text-[#333]'}`}>
                    <Clock size={8} />
                    {deadline.toLocaleDateString('es-PE', { month: 'short', day: 'numeric' })}
                  </span>
                )}
                <span className="text-[10px] text-[#2a2a2a] ml-auto">{fmtRelTime(t.created_at)}</span>
              </div>
            </div>
          )
        })}

        {/* Stream handles its own empty state */}
        {!loading && tab === 'diagnoses' && !data.diagnoses.length && (
          <div className="text-xs text-[#333] py-8 text-center">No diagnoses</div>
        )}
        {!loading && tab === 'tasks' && !data.tasks.length && (
          <div className="text-xs text-[#333] py-8 text-center">No tasks</div>
        )}

        {tab === 'nerves' && <NervesPanel />}
        {tab === 'traces' && <TracesPanel agentFilter={agent} />}
        {tab === 'opinions' && <OpinionsPanel agentFilter={agent} />}
        {tab === 'exchanges' && <DistilledExchangesPanel agentFilter={agent} />}
        {tab === 'curiosity' && <CuriosityPanel agentFilter={agent} />}
        {tab === 'events' && <EventLogPanel agentFilter={agent} />}
        {tab === 'smemory' && <SessionMemoryPanel agentFilter={agent} />}
        {tab === 'emodiary' && <EmotionalDiaryPanel agentFilter={agent} />}
        {tab === 'backlog' && <BacklogPanel agentFilter={agent} />}
        {tab === 'procedures' && <ProceduresPanel agentFilter={agent} />}
        {tab === 'beliefs' && <BeliefPanel agentFilter={agent} />}
        {tab === 'gam' && <GAMPanel />}
        {tab === 'journal' && <JournalPanel agentFilter={agent} />}
        {tab === 'timeline' && <SoulTimelinePanel />}
        {tab === 'emotimeline' && <EmotionalTimelinePanel agentFilter={agent} />}
        {tab === 'diary' && <AgentDiaryPanel agentFilter={agent} />}
        {tab === 'dreams' && <DreamsPanel />}
      </div>

      {/* Footer */}
      <div className="px-4 py-1.5 border-t border-[#111] flex-shrink-0 flex items-center">
        <p className="text-[10px] text-[#333]">
          {data.diagnoses.length} diagnoses · {data.tasks.length} tasks
        </p>
        <span className="text-[10px] text-[#222] ml-auto">30s auto-refresh</span>
      </div>
    </div>
  )
}
