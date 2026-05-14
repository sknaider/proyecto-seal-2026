import { useState, useEffect, useCallback } from 'react'
import { API } from '../App'
import { Save, Plus, Trash2, RefreshCw, Eye, EyeOff, ExternalLink } from 'lucide-react'

interface McpServer {
  id: string; name: string; command: string; args: string[]; enabled: boolean; tools_cache?: string
}

interface OceanProfile {
  name: string; persona_description?: string
  ocean_o: number; ocean_c: number; ocean_e: number; ocean_a: number; ocean_n: number
  emotional_state: string; personality_traits: string
}

interface Props { onSaved: (name: string) => void; onAgentSaved?: (name: string) => void }

const OCEAN_LABELS = [
  { key: 'ocean_o', label: 'O — Openness',          low: 'Conventional', high: 'Curious' },
  { key: 'ocean_c', label: 'C — Conscientiousness',  low: 'Flexible',     high: 'Organized' },
  { key: 'ocean_e', label: 'E — Extraversion',       low: 'Reflective',   high: 'Energetic' },
  { key: 'ocean_a', label: 'A — Agreeableness',      low: 'Direct',       high: 'Empathetic' },
  { key: 'ocean_n', label: 'N — Neuroticism',        low: 'Stable',       high: 'Reactive' },
] as const

const EMOTION_EMOJI: Record<string, string> = {
  calm: '😌', energetic: '⚡', focused: '🎯', reflective: '💭', satisfied: '✨',
}

export default function SettingsView({ onSaved, onAgentSaved }: Props) {
  const [name, setName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('claude-haiku-4-5-20251001')
  const [showKey, setShowKey] = useState(false)
  const [systemPrompt, setSystemPrompt] = useState('')
  const [servers, setServers] = useState<McpServer[]>([])
  const [saved, setSaved] = useState(false)
  const [newSrv, setNewSrv] = useState({ id: '', name: '', command: '', args: '' })
  const [showAddSrv, setShowAddSrv] = useState(false)
  const [discoveringId, setDiscoveringId] = useState<string | null>(null)
  const [tab, setTab] = useState<'general' | 'agent' | 'mcp' | 'prompt'>('general')

  // Agent profile state
  const [ocean, setOcean] = useState<OceanProfile>({
    name: 'Companion', ocean_o: 0.7, ocean_c: 0.6, ocean_e: 0.5, ocean_a: 0.8, ocean_n: 0.2,
    emotional_state: 'calm', personality_traits: '', persona_description: '',
  })
  const [agentSaved, setAgentSaved] = useState(false)

  const MODELS = ['claude-haiku-4-5-20251001', 'claude-sonnet-4-6', 'claude-opus-4-7']

  const load = useCallback(async () => {
    const [cfg, sp, srvsR, prof] = await Promise.all([
      fetch(`${API}/api/companion/config`).then(r => r.json()).catch(() => ({})),
      fetch(`${API}/api/system-prompt`).then(r => r.json()).catch(() => ({})),
      fetch(`${API}/api/mcp/servers`).then(r => r.json()).catch(() => ({})),
      fetch(`${API}/api/agent/profile`).then(r => r.json()).catch(() => ({})),
    ])
    if (cfg.name) setName(cfg.name)
    if (cfg.api_key) setApiKey(cfg.api_key)
    if (cfg.default_model) setModel(cfg.default_model)
    if (sp.system_prompt) setSystemPrompt(sp.system_prompt)
    setServers(srvsR.servers || [])
    if (prof.name) setOcean(prev => ({ ...prev, ...prof }))
  }, [])

  useEffect(() => { load() }, [load])

  async function saveConfig() {
    await fetch(`${API}/api/companion/config`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, api_key: apiKey, default_model: model }),
    })
    await fetch(`${API}/api/system-prompt`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ system_prompt: systemPrompt }),
    })
    onSaved(name)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  async function saveAgent() {
    await fetch(`${API}/api/agent/profile`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: ocean.name,
        persona_description: ocean.persona_description,
        ocean_o: ocean.ocean_o, ocean_c: ocean.ocean_c, ocean_e: ocean.ocean_e,
        ocean_a: ocean.ocean_a, ocean_n: ocean.ocean_n,
      }),
    })
    // Refresh to get updated personality_traits
    const prof = await fetch(`${API}/api/agent/profile`).then(r => r.json()).catch(() => ({}))
    setOcean(prev => ({ ...prev, ...prof }))
    onAgentSaved?.(ocean.name)
    setAgentSaved(true)
    setTimeout(() => setAgentSaved(false), 2000)
  }

  async function addServer() {
    if (!newSrv.id || !newSrv.name || !newSrv.command) return
    let args: string[] = []
    try { args = JSON.parse(newSrv.args || '[]') } catch { args = newSrv.args.split(',').map(s => s.trim()).filter(Boolean) }
    await fetch(`${API}/api/mcp/servers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: newSrv.id, name: newSrv.name, command: newSrv.command, args }),
    })
    setNewSrv({ id: '', name: '', command: '', args: '' })
    setShowAddSrv(false)
    load()
  }

  async function toggleServer(srv: McpServer) {
    await fetch(`${API}/api/mcp/servers/${srv.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !srv.enabled }),
    })
    load()
  }

  async function deleteServer(id: string) {
    await fetch(`${API}/api/mcp/servers/${id}`, { method: 'DELETE' })
    setServers(s => s.filter(x => x.id !== id))
  }

  async function discoverTools(srv: McpServer) {
    setDiscoveringId(srv.id)
    await fetch(`${API}/api/mcp/servers/${srv.id}/tools`)
    await load()
    setDiscoveringId(null)
  }

  function toolCount(srv: McpServer) {
    if (!srv.tools_cache) return 0
    try { return JSON.parse(srv.tools_cache).length } catch { return 0 }
  }

  const TABS = [
    { id: 'general', label: 'General' },
    { id: 'agent',   label: 'Agent' },
    { id: 'mcp',     label: 'MCP Tools' },
    { id: 'prompt',  label: 'System Prompt' },
  ] as const

  return (
    <div className="flex flex-col h-full">
      {/* Tabs */}
      <div className="flex border-b border-seal-border shrink-0 overflow-x-auto">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-xs whitespace-nowrap transition-colors shrink-0 ${tab === t.id ? 'text-blue-400 border-b-2 border-blue-400' : 'text-seal-muted hover:text-slate-300'}`}
          >{t.label}</button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4">

        {/* General tab */}
        {tab === 'general' && (
          <div className="space-y-4">
            <div>
              <label className="text-xs text-seal-muted block mb-1">Your name</label>
              <input value={name} onChange={e => setName(e.target.value)} placeholder="William"
                className="w-full bg-seal-surface border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-seal-muted outline-none focus:border-blue-500 transition-colors" />
            </div>
            <div>
              <label className="text-xs text-seal-muted block mb-1">Anthropic API Key</label>
              <div className="flex gap-2">
                <input type={showKey ? 'text' : 'password'} value={apiKey} onChange={e => setApiKey(e.target.value)}
                  placeholder="sk-ant-…"
                  className="flex-1 bg-seal-surface border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-seal-muted outline-none focus:border-blue-500 transition-colors font-mono" />
                <button onClick={() => setShowKey(!showKey)} className="p-2 text-seal-muted hover:text-slate-300">
                  {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              {!apiKey && <p className="text-xs text-amber-400/70 mt-1">Without an API key, companion uses local Ollama as fallback</p>}
            </div>
            <div>
              <label className="text-xs text-seal-muted block mb-1">Model</label>
              <select value={model} onChange={e => setModel(e.target.value)}
                className="w-full bg-seal-surface border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-blue-500 transition-colors">
                {MODELS.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          </div>
        )}

        {/* Agent tab — OCEAN personality */}
        {tab === 'agent' && (
          <div className="space-y-4">
            {/* Emotional state display */}
            <div className="flex items-center gap-3 bg-seal-surface border border-seal-border rounded-lg p-3">
              <span className="text-2xl">{EMOTION_EMOJI[ocean.emotional_state] || '✦'}</span>
              <div>
                <p className="text-sm text-slate-200 capitalize font-medium">{ocean.emotional_state}</p>
                <p className="text-xs text-seal-muted">Current emotional state — updates automatically</p>
              </div>
            </div>

            <div>
              <label className="text-xs text-seal-muted block mb-1">Agent name</label>
              <input value={ocean.name} onChange={e => setOcean(p => ({ ...p, name: e.target.value }))}
                placeholder="Companion"
                className="w-full bg-seal-surface border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-seal-muted outline-none focus:border-blue-500 transition-colors" />
            </div>

            <div>
              <label className="text-xs text-seal-muted block mb-1">Persona description <span className="text-seal-muted">(optional)</span></label>
              <textarea value={ocean.persona_description || ''} onChange={e => setOcean(p => ({ ...p, persona_description: e.target.value }))}
                placeholder="e.g. You are a calm, insightful advisor who asks clarifying questions…"
                rows={2}
                className="w-full bg-seal-surface border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-seal-muted outline-none resize-none focus:border-blue-500 transition-colors" />
            </div>

            <div className="space-y-3">
              <label className="text-xs text-seal-muted block">Personality (OCEAN)</label>
              {OCEAN_LABELS.map(({ key, label, low, high }) => {
                const val = ocean[key as keyof OceanProfile] as number
                return (
                  <div key={key}>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-slate-300">{label}</span>
                      <span className="text-blue-400">{Math.round(val * 100)}%</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-seal-muted w-20 text-right shrink-0">{low}</span>
                      <input type="range" min={0} max={100} value={Math.round(val * 100)}
                        onChange={e => setOcean(p => ({ ...p, [key]: +e.target.value / 100 }))}
                        className="flex-1 accent-blue-500" />
                      <span className="text-xs text-seal-muted w-20 shrink-0">{high}</span>
                    </div>
                  </div>
                )
              })}
            </div>

            {ocean.personality_traits && (
              <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3">
                <p className="text-xs text-seal-muted mb-1">Generated personality</p>
                <p className="text-xs text-blue-300 leading-relaxed">{ocean.personality_traits}</p>
              </div>
            )}

            <button onClick={saveAgent}
              className={`w-full flex items-center justify-center gap-2 py-2 text-sm rounded-lg transition-all ${agentSaved ? 'bg-green-600 text-white' : 'bg-blue-600 hover:bg-blue-500 text-white'}`}>
              <Save size={14} />
              {agentSaved ? 'Agent saved!' : 'Save agent profile'}
            </button>
          </div>
        )}

        {/* MCP Tools tab */}
        {tab === 'mcp' && (
          <div className="space-y-3">
            <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3 text-xs text-blue-300 leading-relaxed">
              MCP (Model Context Protocol) servers give your companion tools like file access, web search, GitHub, and more.
            </div>
            {servers.map(srv => (
              <div key={srv.id} className="bg-seal-surface border border-seal-border rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-200">{srv.name}</span>
                      {toolCount(srv) > 0 && <span className="text-xs bg-green-500/20 text-green-400 px-1.5 py-0.5 rounded">{toolCount(srv)} tools</span>}
                      {!srv.enabled && <span className="text-xs text-seal-muted">(disabled)</span>}
                    </div>
                    <p className="text-xs text-seal-muted font-mono mt-0.5 truncate">{srv.command} {(srv.args || []).join(' ')}</p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0 ml-2">
                    <button onClick={() => discoverTools(srv)} disabled={!!discoveringId} className="p-1.5 text-seal-muted hover:text-blue-400 disabled:opacity-50" title="Discover tools">
                      <RefreshCw size={13} className={discoveringId === srv.id ? 'animate-spin' : ''} />
                    </button>
                    <button onClick={() => toggleServer(srv)} className={`text-xs px-2 py-0.5 rounded border transition-colors ${srv.enabled ? 'border-seal-border text-seal-muted' : 'border-blue-500 text-blue-400'}`}>
                      {srv.enabled ? 'Disable' : 'Enable'}
                    </button>
                    <button onClick={() => deleteServer(srv.id)} className="p-1 text-seal-muted hover:text-red-400"><Trash2 size={13} /></button>
                  </div>
                </div>
              </div>
            ))}
            {servers.length === 0 && !showAddSrv && <div className="text-center text-seal-muted text-sm py-6">No MCP servers configured</div>}
            {showAddSrv && (
              <div className="bg-seal-surface border border-seal-border rounded-lg p-3 space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <input value={newSrv.id} onChange={e => setNewSrv(s => ({ ...s, id: e.target.value }))} placeholder="ID (e.g. filesystem)" className="bg-seal-bg border border-seal-border rounded px-2 py-1.5 text-xs text-slate-200 placeholder-seal-muted outline-none" />
                  <input value={newSrv.name} onChange={e => setNewSrv(s => ({ ...s, name: e.target.value }))} placeholder="Display name" className="bg-seal-bg border border-seal-border rounded px-2 py-1.5 text-xs text-slate-200 placeholder-seal-muted outline-none" />
                </div>
                <input value={newSrv.command} onChange={e => setNewSrv(s => ({ ...s, command: e.target.value }))} placeholder="Command (e.g. npx)" className="w-full bg-seal-bg border border-seal-border rounded px-2 py-1.5 text-xs text-slate-200 placeholder-seal-muted outline-none font-mono" />
                <input value={newSrv.args} onChange={e => setNewSrv(s => ({ ...s, args: e.target.value }))} placeholder="Args (e.g. @modelcontextprotocol/server-filesystem /path)" className="w-full bg-seal-bg border border-seal-border rounded px-2 py-1.5 text-xs text-slate-200 placeholder-seal-muted outline-none font-mono" />
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setShowAddSrv(false)} className="px-2 py-1 text-xs text-seal-muted hover:text-slate-300">Cancel</button>
                  <button onClick={addServer} disabled={!newSrv.id || !newSrv.name || !newSrv.command} className="px-2 py-1 text-xs bg-blue-600 hover:bg-blue-500 rounded disabled:opacity-30 transition-colors">Add</button>
                </div>
              </div>
            )}
            {!showAddSrv && (
              <button onClick={() => setShowAddSrv(true)} className="w-full flex items-center justify-center gap-2 py-2 text-xs text-seal-muted hover:text-slate-300 border border-dashed border-seal-border rounded-lg hover:border-slate-500 transition-colors">
                <Plus size={13} /> Add MCP server
              </button>
            )}
            <a href="https://github.com/modelcontextprotocol/servers" target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-blue-400/60 hover:text-blue-400 transition-colors">
              <ExternalLink size={11} /> Browse MCP server registry
            </a>
          </div>
        )}

        {/* System Prompt tab */}
        {tab === 'prompt' && (
          <div className="space-y-3">
            <p className="text-xs text-seal-muted leading-relaxed">
              Customize how your companion responds. Variables: <code className="text-blue-400">{'{user_name}'}</code> <code className="text-blue-400">{'{today_date}'}</code> <code className="text-blue-400">{'{user_goal}'}</code>
              <br />Note: OCEAN personality is automatically appended to this prompt.
            </p>
            <textarea value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)} rows={12}
              className="w-full bg-seal-surface border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 outline-none resize-none font-mono leading-relaxed focus:border-blue-500 transition-colors" />
          </div>
        )}
      </div>

      {/* Save bar — only for General and Prompt tabs */}
      {(tab === 'general' || tab === 'prompt') && (
        <div className="p-3 border-t border-seal-border shrink-0">
          <button onClick={saveConfig}
            className={`w-full flex items-center justify-center gap-2 py-2 text-sm rounded-lg transition-all ${saved ? 'bg-green-600 text-white' : 'bg-blue-600 hover:bg-blue-500 text-white'}`}>
            <Save size={14} />
            {saved ? 'Saved!' : 'Save settings'}
          </button>
        </div>
      )}
    </div>
  )
}
