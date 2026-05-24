import { useState } from 'react'
import { API } from '../App'
import { X, MessageCircle, Send, Hash, Bot, Mail, Video, Briefcase, ShieldAlert, ArrowLeft, CheckCircle2, ExternalLink } from 'lucide-react'

export interface ChannelOption {
  id: string
  label: string
  icon: React.ComponentType<{ className?: string }>
  color: string
  bg: string
  auth_kind: 'oauth' | 'qr_pair' | 'bot_token' | 'webhook_url' | 'webview_session'
  description: string
  fields?: { name: string; label: string; placeholder?: string; type?: string }[]
  risk?: 'critical' | 'high' | 'normal'
}

export const CHANNEL_CATALOG: ChannelOption[] = [
  {
    id: 'whatsapp', label: 'WhatsApp', icon: MessageCircle, color: 'text-emerald-600', bg: 'bg-emerald-50 border-emerald-200',
    auth_kind: 'webview_session',
    description: 'Abre web.whatsapp.com en ventana nativa. Escaneás el QR como siempre. Riesgo TOS — Meta puede banear.',
    risk: 'critical',
  },
  {
    id: 'telegram', label: 'Telegram', icon: Send, color: 'text-sky-600', bg: 'bg-sky-50 border-sky-200',
    auth_kind: 'webview_session',
    description: 'Abre web.telegram.org en ventana nativa. Login normal.',
    risk: 'normal',
  },
  {
    id: 'slack', label: 'Slack', icon: Hash, color: 'text-fuchsia-600', bg: 'bg-fuchsia-50 border-fuchsia-200',
    auth_kind: 'webview_session',
    description: 'Abre app.slack.com en ventana nativa. Iniciá sesión con tu workspace.',
    risk: 'high',
  },
  {
    id: 'discord', label: 'Discord', icon: Bot, color: 'text-indigo-600', bg: 'bg-indigo-50 border-indigo-200',
    auth_kind: 'webview_session',
    description: 'Abre discord.com en ventana nativa. Login normal.',
    risk: 'high',
  },
  {
    id: 'gmail', label: 'Gmail', icon: Mail, color: 'text-red-600', bg: 'bg-red-50 border-red-200',
    auth_kind: 'webview_session',
    description: 'Abre mail.google.com en ventana nativa. Login con tu Google account.',
    risk: 'high',
  },
  {
    id: 'google_meet', label: 'Google Meet', icon: Video, color: 'text-emerald-600', bg: 'bg-emerald-50 border-emerald-200',
    auth_kind: 'webview_session',
    description: 'Abre meet.google.com en ventana nativa.',
    risk: 'high',
  },
  {
    id: 'zoom', label: 'Zoom', icon: Video, color: 'text-blue-600', bg: 'bg-blue-50 border-blue-200',
    auth_kind: 'webview_session',
    description: 'Abre zoom.us en ventana nativa.',
    risk: 'high',
  },
  {
    id: 'linkedin', label: 'LinkedIn', icon: Briefcase, color: 'text-blue-700', bg: 'bg-blue-50 border-blue-200',
    auth_kind: 'webview_session',
    description: 'Abre linkedin.com en ventana nativa.',
    risk: 'high',
  },
]

interface Props { onClose: () => void; onConnected?: () => void }

interface TauriGlobal { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> }
function getTauri(): TauriGlobal | null {
  const w = window as unknown as { __TAURI__?: { core?: TauriGlobal } | TauriGlobal }
  const t = w.__TAURI__
  if (!t) return null
  if ('invoke' in t) return t as TauriGlobal
  if ('core' in t && t.core && 'invoke' in t.core) return t.core
  return null
}

export function AddAccountModal({ onClose, onConnected }: Props) {
  const [selected, setSelected] = useState<ChannelOption | null>(null)
  const [label, setLabel] = useState('')
  const [creds, setCreds] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const submit = async () => {
    if (!selected) return
    setBusy(true); setError(null)
    try {
      // Register the account first (server side) so audit log captures intent
      const r = await fetch(`${API}/api/seal/channel-accounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channel: selected.id,
          label: label.trim() || selected.label,
          auth_kind: selected.auth_kind,
          credentials: creds,
          status: selected.auth_kind === 'oauth' || selected.auth_kind === 'qr_pair' || selected.auth_kind === 'webview_session' ? 'pending_auth' : 'connected',
        }),
      })
      const d = await r.json()
      if (!d.ok && !d.id) {
        setError(d.error || `HTTP ${r.status}`)
        return
      }

      // If webview_session, open the native window with the original web app
      if (selected.auth_kind === 'webview_session') {
        const tauri = getTauri()
        if (tauri) {
          try {
            await tauri.invoke('open_channel_webview', { channel: selected.id })
          } catch (e) {
            setError(`No se pudo abrir ventana nativa: ${e}`)
            return
          }
        } else {
          // Dev browser fallback — open in popup
          const url = ({
            whatsapp: 'https://web.whatsapp.com',
            telegram: 'https://web.telegram.org',
            slack: 'https://app.slack.com',
            discord: 'https://discord.com/app',
            gmail: 'https://mail.google.com',
            google_meet: 'https://meet.google.com',
            zoom: 'https://zoom.us',
            linkedin: 'https://www.linkedin.com/messaging/',
          } as Record<string, string>)[selected.id]
          if (url) window.open(url, `seal-${selected.id}`, 'width=1100,height=800')
        }
      }

      setSuccess(true)
      setTimeout(() => { onConnected?.(); onClose() }, 1200)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'fallo de red')
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="w-full max-w-lg bg-white rounded-2xl shadow-2xl overflow-hidden" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="px-5 py-3 border-b border-stone-200 flex items-center justify-between">
          <div className="flex items-center gap-2">
            {selected && (
              <button onClick={() => { setSelected(null); setError(null); setCreds({}); setLabel('') }} className="p-1 rounded hover:bg-stone-100 text-stone-500">
                <ArrowLeft className="w-4 h-4" />
              </button>
            )}
            <h2 className="text-sm font-semibold text-slate-800">
              {selected ? `Conectar ${selected.label}` : 'Agregar cuenta'}
            </h2>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-stone-100 text-stone-500">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-5">
          {!selected && (
            <>
              <p className="text-xs text-slate-500 mb-3">
                Cada canal se abre <strong>en su sitio original</strong> (mismo dominio, mismo login que ya usás). SEAL agrega su capa de lectura/automatización por encima — nunca te pide tu contraseña.
              </p>
              <div className="grid grid-cols-2 gap-2">
                {CHANNEL_CATALOG.map(c => {
                  const Icon = c.icon
                  return (
                    <button
                      key={c.id}
                      onClick={() => setSelected(c)}
                      className={`text-left p-3 rounded-xl border-2 transition hover:shadow ${c.bg} hover:border-violet-300`}
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <Icon className={`w-4 h-4 ${c.color}`} />
                        <span className="text-sm font-semibold text-slate-800">{c.label}</span>
                        {c.risk === 'critical' && (
                          <span title="riesgo crítico" className="ml-auto"><ShieldAlert className="w-3 h-3 text-red-500" /></span>
                        )}
                      </div>
                      <p className="text-[11px] text-slate-600 leading-snug">{c.description}</p>
                    </button>
                  )
                })}
              </div>
            </>
          )}

          {selected && !success && (
            <>
              <p className="text-xs text-slate-500 mb-3">{selected.description}</p>
              {selected.risk === 'critical' && (
                <div className="mb-3 px-3 py-2 rounded-lg border border-red-200 bg-red-50 text-xs text-red-700 flex items-start gap-2">
                  <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5" />
                  <div>
                    <strong>Atención — riesgo crítico.</strong> {selected.id === 'whatsapp' ? 'WhatsApp prohíbe clients no oficiales en sus TOS. Tu cuenta puede ser baneada.' : 'Acceso sensible.'}
                  </div>
                </div>
              )}

              <label className="block text-[10px] uppercase tracking-widest text-slate-500 mb-1">Etiqueta (opcional)</label>
              <input
                value={label}
                onChange={e => setLabel(e.target.value)}
                placeholder={`Mi ${selected.label}`}
                className="w-full mb-3 px-3 py-2 text-sm bg-white border border-stone-300 rounded-lg text-slate-800 focus:outline-none focus:border-violet-400"
              />

              {selected.auth_kind === 'webview_session' && (
                <div className="mb-3 px-3 py-2.5 rounded-lg border border-violet-200 bg-violet-50 text-xs text-violet-800 flex items-start gap-2">
                  <ExternalLink className="w-4 h-4 shrink-0 mt-0.5" />
                  <div>
                    Al confirmar se abre una <strong>ventana nativa</strong> con el sitio original. SEAL no ve tu password — solo inyecta lectura cuando hayas iniciado sesión.
                  </div>
                </div>
              )}

              {selected.auth_kind === 'bot_token' && selected.fields?.map(f => (
                <div key={f.name} className="mb-3">
                  <label className="block text-[10px] uppercase tracking-widest text-slate-500 mb-1">{f.label}</label>
                  <input
                    type={f.type || 'text'}
                    value={creds[f.name] || ''}
                    onChange={e => setCreds({ ...creds, [f.name]: e.target.value })}
                    placeholder={f.placeholder}
                    className="w-full px-3 py-2 text-sm font-mono bg-white border border-stone-300 rounded-lg text-slate-800 focus:outline-none focus:border-violet-400"
                  />
                </div>
              ))}

              {error && (
                <p className="text-xs text-red-600 mb-2">⚠️ {error}</p>
              )}

              <button
                onClick={submit}
                disabled={busy}
                className="w-full py-2.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium disabled:opacity-50"
              >
                {busy ? 'Abriendo…' : selected.auth_kind === 'webview_session' ? `Abrir ${selected.label}` : `Conectar ${selected.label}`}
              </button>
            </>
          )}

          {selected && success && (
            <div className="text-center py-6">
              <CheckCircle2 className="w-12 h-12 text-emerald-500 mx-auto mb-2" />
              <p className="text-sm text-slate-800 font-medium">{selected.label} listo</p>
              <p className="text-xs text-slate-500 mt-1">
                {selected.auth_kind === 'webview_session'
                  ? 'Ventana nativa abierta. Iniciá sesión y SEAL conectará automáticamente.'
                  : 'Cuenta registrada en el vault local cifrado.'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default AddAccountModal
