import { useEffect, useState, useCallback } from 'react'
import { API } from '../App'
import { Plug, Lock, Cloud, Code, MessageCircle, Calendar, FileText, Briefcase, Image, Music, Search, Check, X } from 'lucide-react'

interface Integration {
  id: string
  label: string
  category: 'messaging' | 'productivity' | 'dev' | 'storage' | 'media' | 'other'
  tier: 'native' | 'mcp' | 'composio' | 'custom'
  comingSoon?: boolean
  Icon?: React.ComponentType<{ className?: string }>
}

interface Connector {
  id: string
  name: string
  category: string
  connected: boolean
  status: string
  connected_at?: string | null
  oauth_provider?: string | null
  oauth_configured?: boolean
  setup_hint?: string | null
}

const CATALOG: Integration[] = [
  // Direct connections: top picks
  { id: 'gmail',         label: 'Gmail',          category: 'messaging', tier: 'native', Icon: MessageCircle },
  { id: 'gcal',          label: 'Google Calendar', category: 'productivity', tier: 'native', Icon: Calendar },
  { id: 'gdrive',        label: 'Google Drive',   category: 'storage', tier: 'native', Icon: FileText },
  { id: 'notion',        label: 'Notion',         category: 'productivity', tier: 'native', Icon: FileText },
  { id: 'slack',         label: 'Slack',          category: 'messaging', tier: 'native', Icon: MessageCircle, comingSoon: true },
  { id: 'discord',       label: 'Discord',        category: 'messaging', tier: 'native', Icon: MessageCircle, comingSoon: true },
  { id: 'github',        label: 'GitHub',         category: 'dev', tier: 'native', Icon: Code },
  { id: 'linear',        label: 'Linear',         category: 'dev', tier: 'native', Icon: Briefcase, comingSoon: true },
  { id: 'telegram',      label: 'Telegram',       category: 'messaging', tier: 'native', Icon: MessageCircle, comingSoon: true },
  { id: 'whatsapp_biz',  label: 'WhatsApp Business', category: 'messaging', tier: 'native', Icon: MessageCircle, comingSoon: true },

  // Open connectors
  { id: 'jira',          label: 'Jira',           category: 'dev', tier: 'mcp', comingSoon: true },
  { id: 'confluence',    label: 'Confluence',     category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'asana',         label: 'Asana',          category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'trello',        label: 'Trello',         category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'gdocs',         label: 'Google Docs',    category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'gsheets',       label: 'Google Sheets',  category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'gphotos',       label: 'Google Photos',  category: 'media', tier: 'mcp', comingSoon: true },
  { id: 'spotify',       label: 'Spotify',        category: 'media', tier: 'mcp', Icon: Music, comingSoon: true },

  // External connector fallback
  { id: 'hubspot',       label: 'HubSpot',        category: 'productivity', tier: 'composio', comingSoon: true },
  { id: 'salesforce',    label: 'Salesforce',     category: 'productivity', tier: 'composio', comingSoon: true },
  { id: 'stripe',        label: 'Stripe',         category: 'other', tier: 'composio', comingSoon: true },
  { id: 'quickbooks',    label: 'QuickBooks',     category: 'other', tier: 'composio', comingSoon: true },
  { id: 'mailchimp',     label: 'Mailchimp',      category: 'messaging', tier: 'composio', comingSoon: true },
  { id: 'twitter',       label: 'X / Twitter',    category: 'messaging', tier: 'composio', comingSoon: true },
  { id: 'instagram',     label: 'Instagram',      category: 'media', tier: 'composio', Icon: Image, comingSoon: true },
  { id: 'figma',         label: 'Figma',          category: 'dev', tier: 'composio', comingSoon: true },
  { id: 'dropbox',       label: 'Dropbox',        category: 'storage', tier: 'composio', comingSoon: true },
  { id: 'onedrive',      label: 'OneDrive',       category: 'storage', tier: 'composio', comingSoon: true },
  { id: 'box',           label: 'Box',            category: 'storage', tier: 'composio', comingSoon: true },
]

const TIER_META: Record<Integration['tier'], { label: string; tone: string; desc: string }> = {
  native:   { label: 'Directo',      tone: 'bg-violet-100 text-violet-700 border-violet-200',     desc: 'Conexión directa guardada en este equipo' },
  mcp:      { label: 'Abierto',      tone: 'bg-sky-100 text-sky-700 border-sky-200',             desc: 'Conector abierto que puedes revisar o cambiar' },
  composio: { label: 'Externo',      tone: 'bg-amber-100 text-amber-700 border-amber-200',       desc: 'Usa un servicio externo con tu permiso' },
  custom:   { label: 'Personal',     tone: 'bg-stone-100 text-stone-700 border-stone-200',       desc: 'Conector creado por ti' },
}

const CAT_LABELS: Record<Integration['category'], string> = {
  messaging:    'Mensajería',
  productivity: 'Productividad',
  dev:          'Desarrollo',
  storage:      'Almacenamiento',
  media:        'Multimedia',
  other:        'Otros',
}

const FILTERS = ['all', 'native', 'mcp', 'composio'] as const
const FILTER_LABELS: Record<typeof FILTERS[number], string> = {
  all: 'Todas',
  native: 'Directas',
  mcp: 'Abiertas',
  composio: 'Externas',
}

export default function ConnectionsView() {
  const [filter, setFilter] = useState<typeof FILTERS[number]>('all')
  const [search, setSearch] = useState('')
  const [connectors, setConnectors] = useState<Connector[]>([])
  const [selected, setSelected] = useState<Integration | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/connections`)
      const d = await r.json()
      setConnectors(d.connectors || [])
    } catch {
      setConnectors([])
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const visible = CATALOG.filter(it => {
    if (filter !== 'all' && it.tier !== filter) return false
    if (search.trim() && !it.label.toLowerCase().includes(search.trim().toLowerCase())) return false
    return true
  })

  const grouped = visible.reduce<Record<Integration['category'], Integration[]>>((acc, it) => {
    if (!acc[it.category]) acc[it.category] = []
    acc[it.category].push(it)
    return acc
  }, {} as Record<Integration['category'], Integration[]>)

  const connectorMap = new Map(connectors.map(c => [c.id, c]))

  const prepareConnection = async (id: string) => {
    setBusyId(id)
    try {
      const start = await fetch(`${API}/api/connections/oauth/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connector_id: id }),
      })
      if (start.ok) {
        const d = await start.json()
        if (d.auth_url) {
          window.location.href = d.auth_url
          return
        }
      } else if (start.status === 409) {
        await load()
        return
      }
      await fetch(`${API}/api/connections/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connector_id: id }),
      })
      setSelected(null)
      await load()
    } finally {
      setBusyId(null)
    }
  }

  const removeConnection = async (id: string) => {
    setBusyId(id)
    try {
      await fetch(`${API}/api/connections/${id}`, { method: 'DELETE' })
      await load()
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Plug className="w-5 h-5 text-blue-500" />
            <h1 className="text-xl font-semibold text-slate-800">Conexiones</h1>
          </div>
        </div>

        {/* Connection privacy explainer */}
        <div className="rounded-xl bg-seal-surface border border-seal-border p-4 mb-4">
          <p className="text-xs text-slate-600 leading-relaxed">
            SEAL puede conectarse a otras apps de tres formas. <strong className="text-violet-700">Directo</strong> guarda la conexión en este equipo.
            <strong className="text-sky-700"> Abierto</strong> usa conectores revisables. <strong className="text-amber-700">Externo</strong> usa un servicio fuera de tu equipo solo con tu permiso.
          </p>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2 mb-4 text-xs">
          <div className="flex rounded overflow-hidden border border-seal-border">
            {FILTERS.map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1.5 ${filter === f ? 'bg-blue-500 text-white' : 'bg-seal-surface text-seal-muted hover:text-slate-700'}`}
              >{FILTER_LABELS[f]}</button>
            ))}
          </div>
          <div className="flex-1 max-w-xs flex items-center gap-1 bg-seal-surface border border-seal-border rounded px-2 py-1.5">
            <Search className="w-3.5 h-3.5 text-seal-muted" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Buscar app…"
              className="flex-1 outline-none bg-transparent text-xs"
            />
          </div>
          <span className="text-[11px] text-seal-muted ml-auto">{visible.length} de {CATALOG.length}</span>
        </div>

        {/* Catalog grouped by category */}
        {Object.keys(grouped).length === 0 && (
          <p className="text-center text-sm text-seal-muted py-12">Nada coincide con tu búsqueda.</p>
        )}
        <div className="space-y-5">
          {(Object.keys(grouped) as Integration['category'][]).map(cat => (
            <section key={cat}>
              <h2 className="text-xs uppercase tracking-widest text-seal-muted mb-2">{CAT_LABELS[cat]}</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
                {grouped[cat].map(it => {
                  const meta = TIER_META[it.tier]
                  const Icon = it.Icon ?? Plug
                  const state = connectorMap.get(it.id)
                  const prepared = state?.connected === true
                  const canOAuth = Boolean(state?.oauth_provider)
                  return (
                    <div key={it.id} className="flex items-center gap-3 p-3 rounded-lg border border-seal-border bg-seal-surface hover:bg-stone-50">
                      <div className="w-9 h-9 rounded bg-stone-100 flex items-center justify-center text-slate-600">
                        <Icon className="w-5 h-5" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-800 truncate">{it.label}</p>
                        <p className={`text-[10px] inline-block px-1.5 py-0.5 rounded border ${meta.tone}`} title={meta.desc}>{meta.label}</p>
                      </div>
                      {prepared ? (
                        <button
                          onClick={() => removeConnection(it.id)}
                          disabled={busyId === it.id}
                          className="text-[10px] text-emerald-700 hover:text-red-500"
                        >
                          Conectada
                        </button>
                      ) : it.comingSoon && !state ? (
                        <span className="text-[10px] text-seal-muted">pronto</span>
                      ) : (
                        <button
                          onClick={() => setSelected(it)}
                          className="text-xs text-blue-500 hover:text-blue-600 flex items-center gap-1"
                        >
                          <Check className="w-3 h-3" /> {canOAuth ? 'Conectar' : 'Preparar'}
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            </section>
          ))}
        </div>

        <div className="mt-6 text-[11px] text-seal-muted text-center pb-4">
          <Lock className="w-3 h-3 inline mr-1" /> Las conexiones directas y abiertas guardan tus credenciales cifradas en este equipo.
          <Cloud className="w-3 h-3 inline mx-1" /> Las externas solo se activan cuando tú lo decides.
        </div>
      </div>

      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm rounded-lg border border-seal-border bg-white p-4 shadow-xl">
            <div className="flex items-center justify-between gap-2 mb-3">
              <h2 className="text-sm font-semibold text-slate-800">Preparar {selected.label}</h2>
              <button onClick={() => setSelected(null)} className="p-1 text-seal-muted hover:text-slate-700">
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-xs text-slate-600 leading-relaxed mb-4">
              {connectorMap.get(selected.id)?.oauth_provider
                ? (connectorMap.get(selected.id)?.oauth_configured
                  ? 'SEAL abrirá el permiso OAuth del proveedor y guardará el token cifrado en este equipo.'
                  : connectorMap.get(selected.id)?.setup_hint || 'Faltan credenciales OAuth para activar esta conexión.')
                : 'SEAL guardará esta app como lista para conectar en este equipo. No leerá datos ni hará acciones hasta que agregues credenciales o permisos reales.'}
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setSelected(null)} className="px-3 py-1.5 text-xs text-seal-muted hover:text-slate-700">Cancelar</button>
              <button
                onClick={() => prepareConnection(selected.id)}
                disabled={busyId === selected.id}
                className="px-3 py-1.5 rounded bg-blue-500 hover:bg-blue-600 text-white text-xs disabled:opacity-50"
              >
                Preparar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
