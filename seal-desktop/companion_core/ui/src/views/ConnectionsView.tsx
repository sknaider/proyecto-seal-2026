import { useState } from 'react'
import { Plug, Lock, Cloud, Code, MessageCircle, Calendar, FileText, Briefcase, Image, Music, Search, Check } from 'lucide-react'

interface Integration {
  id: string
  label: string
  category: 'messaging' | 'productivity' | 'dev' | 'storage' | 'media' | 'other'
  tier: 'native' | 'mcp' | 'composio' | 'custom'
  comingSoon?: boolean
  Icon?: React.ComponentType<{ className?: string }>
}

const CATALOG: Integration[] = [
  // Native — top picks
  { id: 'gmail',         label: 'Gmail',          category: 'messaging', tier: 'native', Icon: MessageCircle, comingSoon: true },
  { id: 'gcal',          label: 'Google Calendar', category: 'productivity', tier: 'native', Icon: Calendar, comingSoon: true },
  { id: 'gdrive',        label: 'Google Drive',   category: 'storage', tier: 'native', Icon: FileText, comingSoon: true },
  { id: 'notion',        label: 'Notion',         category: 'productivity', tier: 'native', Icon: FileText, comingSoon: true },
  { id: 'slack',         label: 'Slack',          category: 'messaging', tier: 'native', Icon: MessageCircle, comingSoon: true },
  { id: 'discord',       label: 'Discord',        category: 'messaging', tier: 'native', Icon: MessageCircle, comingSoon: true },
  { id: 'github',        label: 'GitHub',         category: 'dev', tier: 'native', Icon: Code, comingSoon: true },
  { id: 'linear',        label: 'Linear',         category: 'dev', tier: 'native', Icon: Briefcase, comingSoon: true },
  { id: 'telegram',      label: 'Telegram',       category: 'messaging', tier: 'native', Icon: MessageCircle, comingSoon: true },
  { id: 'whatsapp_biz',  label: 'WhatsApp Business', category: 'messaging', tier: 'native', Icon: MessageCircle, comingSoon: true },

  // MCP (open standard)
  { id: 'jira',          label: 'Jira',           category: 'dev', tier: 'mcp', comingSoon: true },
  { id: 'confluence',    label: 'Confluence',     category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'asana',         label: 'Asana',          category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'trello',        label: 'Trello',         category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'gdocs',         label: 'Google Docs',    category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'gsheets',       label: 'Google Sheets',  category: 'productivity', tier: 'mcp', comingSoon: true },
  { id: 'gphotos',       label: 'Google Photos',  category: 'media', tier: 'mcp', comingSoon: true },
  { id: 'spotify',       label: 'Spotify',        category: 'media', tier: 'mcp', Icon: Music, comingSoon: true },

  // Composio fallback (long tail)
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
  native:   { label: '⚡ Native',    tone: 'bg-violet-100 text-violet-700 border-violet-200',     desc: 'OAuth directo · keys cifradas local' },
  mcp:      { label: '🔌 MCP',       tone: 'bg-sky-100 text-sky-700 border-sky-200',             desc: 'Open standard MCP server' },
  composio: { label: '☁ Composio',   tone: 'bg-amber-100 text-amber-700 border-amber-200',       desc: 'Vía Composio.dev — data pasa por terceros' },
  custom:   { label: '🧪 Custom',    tone: 'bg-stone-100 text-stone-700 border-stone-200',       desc: 'Tu propio MCP server' },
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

export default function ConnectionsView() {
  const [filter, setFilter] = useState<typeof FILTERS[number]>('all')
  const [search, setSearch] = useState('')

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

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Plug className="w-5 h-5 text-blue-500" />
            <h1 className="text-xl font-semibold text-slate-800">Conexiones</h1>
          </div>
        </div>

        {/* Tier explainer */}
        <div className="rounded-xl bg-seal-surface border border-seal-border p-4 mb-4">
          <p className="text-xs text-slate-600 leading-relaxed">
            Tu SEAL puede conectarse a otras apps de tres formas — cada una con distinto nivel de privacidad.
            <strong className="text-violet-700"> Native</strong> es la más privada (OAuth directo, tokens locales).
            <strong className="text-sky-700"> MCP</strong> usa estándar abierto.
            <strong className="text-amber-700"> Composio</strong> es fallback para apps long-tail — tu data pasa por terceros con tu consentimiento.
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
              >{f}</button>
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
                  return (
                    <div key={it.id} className="flex items-center gap-3 p-3 rounded-lg border border-seal-border bg-seal-surface hover:bg-stone-50">
                      <div className="w-9 h-9 rounded bg-stone-100 flex items-center justify-center text-slate-600">
                        <Icon className="w-5 h-5" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-800 truncate">{it.label}</p>
                        <p className={`text-[10px] inline-block px-1.5 py-0.5 rounded border ${meta.tone}`} title={meta.desc}>{meta.label}</p>
                      </div>
                      {it.comingSoon ? (
                        <span className="text-[10px] text-seal-muted">soon</span>
                      ) : (
                        <button className="text-xs text-blue-500 hover:text-blue-600 flex items-center gap-1">
                          <Check className="w-3 h-3" /> Conectar
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
          <Lock className="w-3 h-3 inline mr-1" /> Native + MCP guardan tus credenciales cifradas localmente.
          <Cloud className="w-3 h-3 inline mx-1" /> Composio publica claves en sus servidores (opt-in explícito).
        </div>
      </div>
    </div>
  )
}
