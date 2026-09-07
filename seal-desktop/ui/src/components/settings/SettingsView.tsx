import { useState, useEffect, useCallback } from 'react'
import { Settings, Key, User, Brain, RefreshCw, CheckCircle, AlertCircle, Cpu, Server, Download, Thermometer, Zap, Wifi, Scissors, ChevronDown, ChevronRight, Cloud, FlaskConical } from 'lucide-react'
import { useCompanionConfig } from '../../contexts/CompanionConfigContext'
import { OceanRadar } from '../ocean/OceanRadar'

interface OllamaModel { name: string; size_gb: number; modified: string }
interface LocalAI { status: 'up' | 'down'; models: OllamaModel[] }

interface GpuInfo {
  index: number
  name: string
  temperature: number | null
  utilization: number | null
  memory_used_mb: number | null
  memory_total_mb: number | null
  power_draw_w: number | null
}

interface SystemHealth {
  services: Record<string, { status: string; port?: number; error?: string; gpus?: GpuInfo[] }>
  timestamp: string
}

const HOST = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
const SOUL = `http://${HOST}:8800`

const OCEAN_PRESETS = [
  { id: 'balanced',   label: 'Balanced',    icon: '⚖️' },
  { id: 'creative',   label: 'Creative',    icon: '🎨' },
  { id: 'analytical', label: 'Analytical',  icon: '🔬' },
  { id: 'empathetic', label: 'Empathetic',  icon: '💙' },
]

interface TomlConfig {
  agent_name: string
  ocean_preset: string
  api_key: string
}

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="bg-[#080808] border border-[#111] rounded-xl overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[#111]">
        <span className="text-[#555]">{icon}</span>
        <span className="text-xs font-medium text-[#888] uppercase tracking-widest">{title}</span>
      </div>
      <div className="p-4">{children}</div>
    </div>
  )
}

function StatusDot({ status }: { status: string }) {
  const color = status === 'up' ? '#10b981' : status === 'degraded' ? '#f59e0b' : '#ef4444'
  return <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
}

function GpuBar({ value, max, color }: { value: number | null; max: number; color: string }) {
  if (value === null) return <span className="text-[10px] text-[#333]">N/A</span>
  const pct = Math.min(100, (value / max) * 100)
  return (
    <div className="flex items-center gap-1.5 flex-1">
      <div className="flex-1 h-1 bg-[#111] rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-[10px] w-8 text-right flex-shrink-0" style={{ color }}>{Math.round(value)}</span>
    </div>
  )
}

function SystemMonitorSection({ health, loadingHealth, onRefresh }: {
  health: SystemHealth | null
  loadingHealth: boolean
  onRefresh: () => void
}) {
  const gpu = health?.services?.gpu
  const gpuInfo: GpuInfo | null = gpu?.gpus?.[0] ?? null
  const pgStatus = health?.services?.postgresql?.status ?? 'unknown'
  const wcStatus = health?.services?.web_chat?.status ?? 'unknown'

  return (
    <Section title="System Monitor" icon={<Server size={12} />}>
      <div className="space-y-3">
        {/* Services row */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <StatusDot status={pgStatus} />
            <span className="text-[11px] text-[#666]">PostgreSQL</span>
          </div>
          <div className="flex items-center gap-1.5">
            <StatusDot status={wcStatus} />
            <span className="text-[11px] text-[#666]">WebChat</span>
          </div>
          <div className="flex items-center gap-1.5">
            <StatusDot status="up" />
            <span className="text-[11px] text-[#666]">Backend :8800</span>
          </div>
          <button onClick={onRefresh} disabled={loadingHealth}
            className="ml-auto p-1 rounded border border-[#111] hover:border-[#222] text-[#333] hover:text-[#666] transition-colors">
            <RefreshCw size={9} className={loadingHealth ? 'animate-spin' : ''} />
          </button>
        </div>

        {/* GPU panel */}
        {gpuInfo && (
          <div className="rounded-lg bg-[#050505] border border-[#0f0f0f] p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Cpu size={11} className="text-[#7c3aed]" />
              <span className="text-xs font-medium text-[#aaa]">{gpuInfo.name}</span>
              <span className="text-[10px] text-[#444] ml-auto">GPU {gpuInfo.index}</span>
            </div>
            <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 items-center">
              <div className="flex items-center gap-1 text-[10px] text-[#444]">
                <Thermometer size={9} /> Temp
              </div>
              <div className="flex items-center gap-1.5">
                <GpuBar value={gpuInfo.temperature} max={100} color={
                  (gpuInfo.temperature ?? 0) > 80 ? '#ef4444' : (gpuInfo.temperature ?? 0) > 60 ? '#f59e0b' : '#10b981'
                } />
                {gpuInfo.temperature !== null && <span className="text-[10px] text-[#444]">°C</span>}
              </div>

              <div className="flex items-center gap-1 text-[10px] text-[#444]">
                <Zap size={9} /> Util
              </div>
              <div className="flex items-center gap-1.5">
                <GpuBar value={gpuInfo.utilization} max={100} color="#7c3aed" />
                {gpuInfo.utilization !== null && <span className="text-[10px] text-[#444]">%</span>}
              </div>

              {gpuInfo.power_draw_w !== null && (
                <>
                  <span className="text-[10px] text-[#444]">Power</span>
                  <span className="text-[11px] text-[#666]">{gpuInfo.power_draw_w.toFixed(1)} W</span>
                </>
              )}
              {gpuInfo.memory_used_mb !== null && gpuInfo.memory_total_mb !== null && (
                <>
                  <span className="text-[10px] text-[#444]">VRAM</span>
                  <div className="flex items-center gap-1.5">
                    <GpuBar value={gpuInfo.memory_used_mb} max={gpuInfo.memory_total_mb} color="#2563eb" />
                    <span className="text-[10px] text-[#444]">MB</span>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
        {!gpuInfo && !loadingHealth && (
          <p className="text-[10px] text-[#333]">No GPU data available</p>
        )}

        {/* DGX Spark */}
        <DgxSparkStatus />

        {health?.timestamp && (
          <p className="text-[10px] text-[#2a2a2a]">
            Updated {new Date(health.timestamp).toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </p>
        )}
      </div>
    </Section>
  )
}

function DgxSparkStatus() {
  const [status, setStatus] = useState<'checking' | 'up' | 'down'>('checking')

  useEffect(() => {
    fetch(`http://192.168.68.200:11434/api/tags`, { signal: AbortSignal.timeout(3000) })
      .then(r => setStatus(r.ok ? 'up' : 'down'))
      .catch(() => setStatus('down'))
  }, [])

  return (
    <div className="flex items-center gap-2">
      <Wifi size={10} className={status === 'up' ? 'text-[#10b981]' : 'text-[#333]'} />
      <span className="text-[11px] text-[#666]">DGX Spark</span>
      <span className="text-[10px] text-[#444]">192.168.68.200</span>
      <span className={`text-[10px] ml-auto ${
        status === 'up' ? 'text-[#10b981]' : status === 'down' ? 'text-[#444]' : 'text-[#333]'
      }`}>
        {status === 'checking' ? '...' : status}
      </span>
    </div>
  )
}

interface TjStats {
  total_compacts: number
  total_chars_saved: number
  total_tokens_saved_estimate: number
  per_rule: Record<string, { compacts: number; chars_saved: number }>
}

interface TjResult {
  text: string
  rule_applied: string
  savings_pct: number
}

function TokenJuiceSection() {
  const [stats, setStats] = useState<TjStats | null>(null)
  const [rules, setRules] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [input, setInput] = useState('')
  const [toolName, setToolName] = useState('bash')
  const [result, setResult] = useState<TjResult | null>(null)
  const [compacting, setCompacting] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [reloadOk, setReloadOk] = useState(false)
  const [showRules, setShowRules] = useState(false)
  const [tjRulesSort, setTjRulesSort] = useState<'alpha' | 'default'>('default')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      try {
        const [sr, rr] = await Promise.all([
          fetch(`${SOUL}/api/tokenjuice/stats`),
          fetch(`${SOUL}/api/tokenjuice/rules`),
        ])
        const [sd, rd] = await Promise.all([sr.json(), rr.json()])
        if (active) {
          setStats(sd)
          setRules(rd.rules ?? [])
        }
      } catch { } finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false }
  }, [])

  async function handleCompact() {
    if (!input.trim()) return
    setCompacting(true); setResult(null)
    try {
      const r = await fetch(`${SOUL}/api/tokenjuice/compact`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tool_name: toolName, argv: '', stdout: input, stderr: '' }),
      })
      const d = await r.json()
      setResult({ text: d.text ?? '', rule_applied: d.rule_applied ?? 'none', savings_pct: d.savings_pct ?? 0 })
    } catch { } finally { setCompacting(false) }
  }

  async function handleReload() {
    setReloading(true); setReloadOk(false)
    try {
      await fetch(`${SOUL}/api/tokenjuice/reload`, { method: 'POST' })
      setReloadOk(true)
      setTimeout(() => setReloadOk(false), 2000)
    } catch { } finally { setReloading(false) }
  }

  function fmtNum(n: number) {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
    return String(n)
  }

  return (
    <Section title="TokenJuice" icon={<Scissors size={12} />}>
      <div className="space-y-3">
        {loading && !stats && (
          <div className="text-[10px] text-[#333] text-center py-3 animate-pulse">Loading TokenJuice…</div>
        )}

        {stats && (
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: 'Compacts', value: fmtNum(stats.total_compacts) },
              { label: 'Chars saved', value: fmtNum(stats.total_chars_saved) },
              { label: 'Tokens est.', value: fmtNum(stats.total_tokens_saved_estimate) },
            ].map(s => (
              <div key={s.label} className="rounded bg-[#040404] border border-[#111] p-2 text-center">
                <div className="text-base font-bold text-[#10b981]">{s.value}</div>
                <div className="text-[9px] text-[#444] mt-0.5">{s.label}</div>
              </div>
            ))}
          </div>
        )}

        {rules.length > 0 && (
          <div>
            <button onClick={() => setShowRules(v => !v)}
              className="flex items-center gap-1 text-[10px] text-[#444] hover:text-[#888] transition-colors">
              {showRules ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
              {rules.length} rules loaded
            </button>
            {showRules && (
              <div className="mt-1.5">
                <div className="flex items-center gap-1 mb-1">
                  <button onClick={() => setTjRulesSort(s => s === 'alpha' ? 'default' : 'alpha')}
                    className="ml-auto px-2 py-0.5 rounded border text-[9px] transition-colors"
                    style={tjRulesSort === 'alpha' ? { borderColor: '#f59e0b', color: '#f59e0b', backgroundColor: '#f59e0b18' } : { borderColor: '#1a1a1a', color: '#333' }}>
                    ↓ A-Z
                  </button>
                </div>
                <div className="flex flex-wrap gap-1">
                  {(tjRulesSort === 'alpha' ? [...rules].sort((a, b) => a.localeCompare(b)) : rules).map(r => (
                    <span key={r} className="text-[9px] px-1.5 py-0.5 rounded bg-[#0a0a0a] border border-[#111] text-[#555] font-mono">{r}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-[#555]">Playground</span>
            <select value={toolName} onChange={e => setToolName(e.target.value)}
              className="bg-[#0d0d0d] border border-[#1a1a1a] rounded px-1.5 py-0.5 text-[9px] text-[#888] outline-none">
              {['bash', 'npm', 'git', 'cargo', 'docker', 'python'].map(t => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <textarea value={input} onChange={e => setInput(e.target.value)} rows={3}
            placeholder="Paste tool stdout here to compress…"
            className="w-full bg-[#0d0d0d] border border-[#1a1a1a] focus:border-[#333] rounded-lg px-2.5 py-2 text-[10px] text-[#ccc] placeholder-[#2a2a2a] outline-none resize-none font-mono" />
          {result && (
            <div className="rounded bg-[#030303] border border-[#111] p-2.5 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-[#10b98118] text-[#10b981]">{result.rule_applied}</span>
                <span className="text-[9px] font-bold text-[#f59e0b]">−{result.savings_pct.toFixed(0)}% saved</span>
              </div>
              <pre className="text-[9px] text-[#888] font-mono whitespace-pre-wrap leading-relaxed max-h-24 overflow-y-auto">{result.text}</pre>
            </div>
          )}
          <div className="flex gap-2">
            <button onClick={handleCompact} disabled={compacting || !input.trim()}
              className="flex-1 py-1.5 bg-[#059669] hover:bg-[#047857] disabled:opacity-40 text-white text-[10px] font-medium rounded-lg transition-colors flex items-center justify-center gap-1">
              <Scissors size={10} />
              {compacting ? 'Compressing…' : 'Compact'}
            </button>
            <button onClick={handleReload} disabled={reloading}
              className="px-3 py-1.5 border border-[#1a1a1a] hover:border-[#333] disabled:opacity-40 text-[#444] hover:text-[#888] text-[9px] rounded-lg transition-colors flex items-center gap-1">
              {reloadOk
                ? <CheckCircle size={10} className="text-[#10b981]" />
                : <RefreshCw size={10} className={reloading ? 'animate-spin' : ''} />}
              {reloadOk ? 'Reloaded' : 'Reload rules'}
            </button>
          </div>
        </div>
      </div>
    </Section>
  )
}

function SoulExportSection({ soul }: { soul: string }) {
  const [exporting, setExporting] = useState(false)
  const [err, setErr] = useState('')

  async function handleExport() {
    setExporting(true)
    setErr('')
    try {
      const r = await fetch(`${soul}/api/soul/export-snapshot`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      if (data.error) throw new Error(data.error)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `seal_soul_export_${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <Section title="SOUL Export" icon={<Download size={12} />}>
      <div className="space-y-2">
        <p className="text-xs text-[#444]">
          Download a JSON snapshot of the SOUL state: agents, OCEAN, memories (7d), goals, NERVES drives, working states.
        </p>
        {err && <p className="text-[10px] text-[#ef4444]">{err}</p>}
        <button
          onClick={handleExport}
          disabled={exporting}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#0d0d0d] border border-[#1a1a1a] hover:border-[#2a2a2a] text-xs text-[#888] hover:text-[#ccc] disabled:opacity-40 transition-colors"
        >
          <Download size={11} />
          {exporting ? 'Exporting…' : 'Export SOUL snapshot'}
        </button>
      </div>
    </Section>
  )
}

// ── AI Backend Settings (spec §4.4) ─────────────────────────────────────────

interface RoutingRow { role: string; provider: string; model: string; fallback_provider: string | null; fallback_model: string | null; enabled: boolean }

const ROLES = ['reasoning', 'agentic', 'coding', 'summary'] as const
const ROLE_DEFAULTS: Record<string, { local: string; byok: string; fallback: string }> = {
  reasoning: { local: 'gemma3:12b',       byok: 'claude-opus-4-7', fallback: 'ollama' },
  agentic:   { local: 'gemma3:12b',       byok: 'claude-sonnet-4-6', fallback: 'ollama' },
  coding:    { local: 'qwen2.5-coder:7b', byok: 'gpt-4o', fallback: 'ollama' },
  summary:   { local: 'gemma3:4b',        byok: 'gpt-4o-mini', fallback: 'ollama' },
}
const PROVIDERS = ['ollama', 'anthropic', 'openai', 'mistral', 'google', 'openrouter'] as const

function AIBackendSection({ soul }: { soul: string }) {
  const [mode, setMode] = useState<'local' | 'byok'>('local')
  const [rows, setRows] = useState<RoutingRow[]>([])
  const [expanded, setExpanded] = useState(false)
  const [ollamaUp, setOllamaUp] = useState<boolean | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetch(`${soul}/api/soul/llm-routing?agent=DEFAULT`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.rows?.length) {
          setRows(d.rows)
          const allLocal = d.rows.every((r: RoutingRow) => r.provider === 'ollama')
          setMode(allLocal ? 'local' : 'byok')
        } else {
          setRows(ROLES.map(role => ({
            role, provider: 'ollama', model: ROLE_DEFAULTS[role].local,
            fallback_provider: null, fallback_model: null, enabled: true,
          })))
        }
      }).catch(() => {})
    fetch(`${soul}/api/local-ai`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setOllamaUp(d?.status === 'up' ? true : false))
      .catch(() => setOllamaUp(false))
  }, [soul])

  function switchMode(m: 'local' | 'byok') {
    setMode(m)
    setRows(ROLES.map(role => ({
      role,
      provider: m === 'local' ? 'ollama' : (ROLE_DEFAULTS[role].byok.startsWith('claude') ? 'anthropic' : 'openai'),
      model: m === 'local' ? ROLE_DEFAULTS[role].local : ROLE_DEFAULTS[role].byok,
      fallback_provider: m === 'local' ? null : 'ollama',
      fallback_model: m === 'local' ? null : ROLE_DEFAULTS[role].local,
      enabled: true,
    })))
  }

  function updateRow(role: string, field: keyof RoutingRow, value: string) {
    setRows(rs => rs.map(r => r.role === role ? { ...r, [field]: value } : r))
  }

  async function handleSave() {
    setSaving(true)
    try {
      const r = await fetch(`${soul}/api/soul/llm-routing`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: 'DEFAULT', rows }),
      })
      if (r.ok) { setSaved(true); setTimeout(() => setSaved(false), 2500) }
    } catch { /* ignore */ }
    setSaving(false)
  }

  async function handleTest() {
    setTesting(true); setTestResult(null)
    try {
      const r = await fetch(`${soul}/api/local-ai/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: 'ping', agent: 'DEFAULT' }),
      })
      if (r.ok) {
        const d = await r.json()
        setTestResult(d.response ? `Local AI OK: "${d.response.slice(0, 60)}"` : 'Test failed — no response')
      } else {
        setTestResult(`Error ${r.status}`)
      }
    } catch (e) {
      setTestResult(e instanceof Error ? e.message : 'Test failed')
    }
    setTesting(false)
  }

  return (
    <Section title="AI Backend" icon={<Cloud size={12} />}>
      {/* Ollama status strip */}
      <div className="flex items-center gap-1.5 mb-3">
        <div className={`w-1.5 h-1.5 rounded-full ${ollamaUp === true ? 'bg-[#10b981]' : ollamaUp === false ? 'bg-[#ef4444]' : 'bg-[#444]'}`} />
        <span className="text-[10px] text-[#555]">
          {ollamaUp === true ? 'Local AI ready' : ollamaUp === false ? 'Local AI offline — configure BYOK or start Ollama' : 'Checking…'}
        </span>
      </div>

      {/* Mode cards */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        {([['local', 'Local (Gemma 4)', 'RECOMMENDED', '$0/mo · private · offline', Cpu] as const,
           ['byok',  'BYOK Cloud',       'POWER USER',   'Your key. You pay.', Cloud] as const,
        ] as const).map(([id, title, badge, sub, Icon]) => (
          <button key={id} onClick={() => switchMode(id as 'local' | 'byok')}
            className={`p-3 rounded-xl border text-left transition-all ${
              mode === id
                ? id === 'local'
                  ? 'border-[#10b981] bg-[#10b98115]'
                  : 'border-[#7c3aed] bg-[#7c3aed15]'
                : 'border-[#1a1a1a] bg-[#0d0d0d] hover:border-[#2a2a2a]'
            }`}>
            <div className="flex items-center gap-1.5 mb-1">
              <Icon size={11} className={mode === id ? (id === 'local' ? 'text-[#10b981]' : 'text-[#7c3aed]') : 'text-[#444]'} />
              <span className={`text-[10px] font-semibold ${mode === id ? (id === 'local' ? 'text-[#10b981]' : 'text-[#7c3aed]') : 'text-[#555]'}`}>{badge}</span>
            </div>
            <p className="text-xs font-medium text-[#e5e5e5] mb-0.5">{title}</p>
            <p className="text-[10px] text-[#444]">{sub}</p>
          </button>
        ))}
      </div>

      {/* Advanced routing toggle */}
      <button onClick={() => setExpanded(e => !e)}
        className="flex items-center gap-1 text-[10px] text-[#555] hover:text-[#888] transition-colors mb-2">
        {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        Advanced routing (4 roles)
      </button>

      {expanded && (
        <div className="space-y-2 mb-3">
          {rows.map(row => (
            <div key={row.role} className="grid grid-cols-[60px_1fr_1fr] gap-1.5 items-center">
              <span className="text-[10px] font-mono text-[#666] capitalize">{row.role}</span>
              <select value={row.provider}
                onChange={e => updateRow(row.role, 'provider', e.target.value)}
                className="bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1 text-[10px] text-[#888] outline-none">
                {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
              <input value={row.model}
                onChange={e => updateRow(row.role, 'model', e.target.value)}
                className="bg-[#0d0d0d] border border-[#1a1a1a] rounded px-2 py-1 text-[10px] text-[#888] font-mono outline-none"
                placeholder="model name" />
            </div>
          ))}
        </div>
      )}

      {/* Test result */}
      {testResult && (
        <p className={`text-[10px] mb-2 ${testResult.includes('OK') ? 'text-[#10b981]' : 'text-[#ef4444]'}`}>{testResult}</p>
      )}

      {/* Action row */}
      <div className="flex items-center gap-2 pt-1">
        <button onClick={handleTest} disabled={testing}
          className="flex items-center gap-1 px-2.5 py-1.5 bg-[#0d0d0d] border border-[#1a1a1a] hover:border-[#2a2a2a] disabled:opacity-40 rounded-lg text-[10px] text-[#888] transition-colors">
          <FlaskConical size={10} />
          {testing ? 'Testing…' : 'Test routes'}
        </button>
        <button onClick={handleSave} disabled={saving}
          className="flex items-center gap-1 px-2.5 py-1.5 bg-[#7c3aed] hover:bg-[#6d28d9] disabled:opacity-40 rounded-lg text-[10px] text-white transition-colors ml-auto">
          {saved ? <><CheckCircle size={10} /> Saved</> : saving ? 'Saving…' : 'Save routing'}
        </button>
      </div>
    </Section>
  )
}

export function SettingsView() {
  const config = useCompanionConfig()
  const [toml, setToml] = useState<TomlConfig>({ agent_name: '', ocean_preset: 'balanced', api_key: '' })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [localAI, setLocalAI] = useState<LocalAI | null>(null)
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [loadingHealth, setLoadingHealth] = useState(false)
  const [modelSort, setModelSort] = useState<'size' | 'name'>('name')

  const fetchHealth = useCallback(async () => {
    setLoadingHealth(true)
    try {
      const r = await fetch(`${SOUL}/api/system/health`)
      if (r.ok) setHealth(await r.json())
    } catch { /* ignore */ }
    finally { setLoadingHealth(false) }
  }, [])

  useEffect(() => {
    fetch(`${SOUL}/api/companion/config`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setToml(d) })
      .catch(() => {})
    fetch(`${SOUL}/api/local-ai`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setLocalAI(d) })
      .catch(() => {})
    fetchHealth()
  }, [fetchHealth])

  async function handleSave() {
    setSaving(true); setError(''); setSaved(false)
    try {
      const r = await fetch(`${SOUL}/api/companion/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...toml, ...(apiKey ? { api_key: apiKey } : {}) }),
      })
      if (!r.ok) throw new Error(`${r.status}`)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="h-12 flex items-center gap-3 px-4 border-b border-[#0f0f0f] flex-shrink-0">
        <Settings size={13} className="text-[#555]" />
        <span className="text-sm font-medium text-[#e5e5e5]">Settings</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[10px] font-mono text-[#333]">{config.mode}</span>
          <span className="text-[10px] font-mono text-[#555]">v{config.version}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {/* Companion section */}
        <Section title="Companion" icon={<User size={12} />}>
          <div className="space-y-3">
            <div>
              <label className="block text-xs text-[#666] mb-1.5">Assistant name</label>
              <input
                type="text"
                value={toml.agent_name}
                onChange={e => setToml(t => ({ ...t, agent_name: e.target.value }))}
                placeholder="My Assistant"
                className="w-full bg-[#0d0d0d] border border-[#1a1a1a] focus:border-[#333] rounded-lg px-3 py-2 text-sm text-[#e5e5e5] placeholder-[#333] outline-none transition-colors"
              />
            </div>
          </div>
        </Section>

        {/* Personality section */}
        <Section title="Personality" icon={<Brain size={12} />}>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              {OCEAN_PRESETS.map(p => (
                <button
                  key={p.id}
                  onClick={() => setToml(t => ({ ...t, ocean_preset: p.id }))}
                  className={`p-2.5 rounded-lg border text-left transition-all ${
                    toml.ocean_preset === p.id
                      ? 'border-[#7c3aed] bg-[#7c3aed18] text-white'
                      : 'border-[#1a1a1a] bg-[#0d0d0d] text-[#666] hover:border-[#2a2a2a]'
                  }`}
                >
                  <span className="text-base">{p.icon}</span>
                  <span className="block text-xs font-medium mt-0.5">{p.label}</span>
                </button>
              ))}
            </div>
            {/* Live OCEAN radar preview */}
            <div className="flex justify-center pt-2">
              <OceanRadar preset={toml.ocean_preset || 'balanced'} />
            </div>
          </div>
        </Section>

        {/* API Key section */}
        <Section title="API Key (BYOK)" icon={<Key size={12} />}>
          <div className="space-y-3">
            <p className="text-xs text-[#444]">
              Provide your own Claude API key. Stored locally in <code className="font-mono text-[#555]">~/.seal/companion.toml</code> with 600 perms.
            </p>
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="sk-ant-api03-…"
                className="w-full bg-[#0d0d0d] border border-[#1a1a1a] focus:border-[#333] rounded-lg px-3 py-2 text-sm text-[#e5e5e5] placeholder-[#333] outline-none transition-colors pr-16"
              />
              <button
                onClick={() => setShowKey(s => !s)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-[#444] hover:text-[#666] transition-colors"
              >
                {showKey ? 'hide' : 'show'}
              </button>
            </div>
            {toml.api_key && !apiKey && (
              <p className="text-[10px] text-[#10b981]">✓ API key configured</p>
            )}
          </div>
        </Section>

        {/* AI Backend — spec §4.4 */}
        <AIBackendSection soul={SOUL} />

        {/* Local AI models list (complementary detail) */}
        {localAI && localAI.models.length > 0 && (
          <Section title="Local Models" icon={<Cpu size={12} />}>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-[#555]">{localAI.models.length} models available</span>
                <button onClick={() => setModelSort(s => s === 'size' ? 'name' : 'size')}
                  className="px-1.5 py-0.5 rounded border text-[9px] transition-colors"
                  style={modelSort === 'size' ? { borderColor: '#f59e0b', color: '#f59e0b', backgroundColor: '#f59e0b18' } : { borderColor: '#1a1a1a', color: '#333' }}>
                  ↓ {modelSort}
                </button>
              </div>
              {(modelSort === 'size'
                ? [...localAI.models].sort((a, b) => b.size_gb - a.size_gb)
                : localAI.models
              ).slice(0, 8).map(m => (
                <div key={m.name} className="flex items-center justify-between">
                  <span className="text-[11px] font-mono text-[#888] truncate flex-1">{m.name}</span>
                  <span className="text-[10px] text-[#444] ml-2">{m.size_gb}GB</span>
                </div>
              ))}
              {localAI.models.length > 8 && (
                <p className="text-[10px] text-[#333]">+{localAI.models.length - 8} more</p>
              )}
            </div>
          </Section>
        )}

        {/* System Monitor */}
        <SystemMonitorSection health={health} loadingHealth={loadingHealth} onRefresh={fetchHealth} />

        {/* SOUL Export */}
        {config.mode !== 'user-product' && (
          <SoulExportSection soul={SOUL} />
        )}

        {/* TokenJuice */}
        {config.mode !== 'user-product' && <TokenJuiceSection />}

        {/* Companion mode info */}
        <Section title="Mode" icon={<RefreshCw size={12} />}>
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-[#555]">Current mode</span>
              <span className={`font-mono ${config.mode === 'user-product' ? 'text-[#10b981]' : 'text-[#7c3aed]'}`}>
                {config.mode}
              </span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-[#555]">Team agents visible</span>
              <span className={config.show_team_agents ? 'text-[#10b981]' : 'text-[#555]'}>
                {config.show_team_agents ? 'yes' : 'no'}
              </span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-[#555]">Governance</span>
              <span className={config.show_governance ? 'text-[#10b981]' : 'text-[#555]'}>
                {config.show_governance ? 'enabled' : 'disabled'}
              </span>
            </div>
            <p className="text-[10px] text-[#2a2a2a] mt-2">
              Mode is set server-side via COMPANION_MODE env var. Restart server to change.
            </p>
          </div>
        </Section>
      </div>

      {/* Save bar */}
      <div className="flex-shrink-0 px-4 py-3 border-t border-[#111] flex items-center gap-3">
        {saved && (
          <div className="flex items-center gap-1.5 text-[#10b981] text-xs">
            <CheckCircle size={12} /> Saved
          </div>
        )}
        {error && (
          <div className="flex items-center gap-1.5 text-[#ef4444] text-xs">
            <AlertCircle size={12} /> {error}
          </div>
        )}
        <button
          onClick={handleSave}
          disabled={saving}
          className="ml-auto px-5 py-2 bg-[#7c3aed] hover:bg-[#6d28d9] disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors"
        >
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
      </div>
    </div>
  )
}
