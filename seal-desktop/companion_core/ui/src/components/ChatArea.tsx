import { useState, useEffect, useRef, useCallback } from 'react'
import { API } from '../App'
import { Send } from 'lucide-react'

interface Msg { role: string; content: string; ts?: string }

interface Props {
  threadId: string
  agentName: string
  lang: 'en' | 'es'
  theme: 'light' | 'dark'
  onMessageSent?: () => void
}

const ONBOARDING = {
  en: `Right now I can't pull in your calendar, emails, or tasks because no integrations are connected yet. So I can't tell you what's on your plate today or what meetings are coming up.

To get a fuller briefing, head to **Settings → Skills** and link up your calendar, email, or task apps. Takes a minute, and then I can surface what actually matters to you each morning.

In the meantime, what's your main focus for today? I'm happy to help you plan it out, set priorities, or just chat through whatever is on your mind.`,
  es: `Por ahora no puedo ver tu calendario, correos o tareas porque aún no hay integraciones conectadas. Por eso no puedo decirte qué tienes pendiente hoy ni qué reuniones se aproximan.

Para recibir un briefing completo, ve a **Ajustes → Habilidades** y conecta tu calendario, correo o aplicación de tareas. Tarda un minuto, y luego puedo mostrarte lo que realmente importa cada mañana.

Mientras tanto, ¿en qué quieres enfocarte hoy? Puedo ayudarte a organizarte, fijar prioridades o simplemente conversar sobre lo que tengas en mente.`,
}

const PLACEHOLDER = { en: 'Type a message…', es: 'Escribe un mensaje…' }
const SHIFT_HINT  = { en: 'Shift+Enter for newline', es: 'Mayús+Enter para nueva línea' }
const CONN_ERR    = {
  en: '⚠️ I could not connect. Close and reopen SEAL App; if it keeps failing, open Settings.',
  es: '⚠️ No pude conectarme. Cierra y abre SEAL App; si sigue fallando, abre Ajustes.',
}

export default function ChatArea({ threadId, agentName, lang, theme, onMessageSent }: Props) {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef  = useRef<HTMLTextAreaElement>(null)
  const prevThreadRef = useRef<string>('')

  const loadMessages = useCallback(async (tid: string) => {
    try {
      const r = await fetch(`${API}/api/chat/threads/${tid}/messages`)
      const d = await r.json()
      setMessages(d.messages || [])
    } catch { setMessages([]) }
  }, [])

  useEffect(() => {
    if (threadId !== prevThreadRef.current) {
      prevThreadRef.current = threadId
      loadMessages(threadId)
    }
  }, [threadId, loadMessages])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  useEffect(() => {
    const handler = (e: Event) => { setInput((e as CustomEvent).detail); inputRef.current?.focus() }
    window.addEventListener('inject-prompt', handler)
    return () => window.removeEventListener('inject-prompt', handler)
  }, [])

  async function send() {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    setMessages(m => [...m, { role: 'user', content: text }])
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, content: text }),
      })
      const d = await r.json()
      setMessages(m => [...m, { role: 'assistant', content: d.reply || d.error || '…' }])
      onMessageSent?.()
    } catch {
      setMessages(m => [...m, { role: 'assistant', content: CONN_ERR[lang] }])
    }
    setLoading(false)
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  const isDark = theme === 'dark'
  const agentBubble = isDark
    ? 'bg-[#151820] border border-[#1e2330] text-slate-200'
    : 'bg-stone-50 border border-stone-200 text-stone-700'
  const userBubble = 'bg-blue-600 text-white'
  const agentLabel = isDark ? 'text-slate-500' : 'text-stone-500'
  const inputBg    = isDark ? 'bg-[#151820] border-[#1e2330] text-slate-200 placeholder-slate-500 focus-within:border-blue-500' : 'bg-stone-50 border-stone-200 text-stone-800 placeholder-stone-400 focus-within:border-blue-300'
  const hintColor  = isDark ? 'text-slate-600' : 'text-stone-400'
  const borderTop  = isDark ? 'border-[#1e2330]' : 'border-stone-100'

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">

        {/* Onboarding message when empty */}
        {messages.length === 0 && !loading && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center text-white text-xs font-bold shrink-0 mt-0.5">
              {agentName[0]?.toUpperCase() ?? 'C'}
            </div>
            <div className="flex-1 max-w-[85%]">
              <p className={`text-xs font-medium mb-1 ${agentLabel}`}>{agentName}</p>
              <div className={`rounded-2xl rounded-tl-sm px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${agentBubble}`}>
                {ONBOARDING[lang]}
              </div>
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0 mt-0.5 ${
              m.role === 'assistant'
                ? 'bg-gradient-to-br from-blue-500 to-violet-600 text-white'
                : isDark ? 'bg-slate-700 text-slate-300' : 'bg-stone-200 text-stone-600'
            }`}>
              {m.role === 'assistant' ? agentName[0]?.toUpperCase() ?? 'C' : 'U'}
            </div>
            <div className={`max-w-[80%] flex flex-col ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
              {m.role === 'assistant' && (
                <p className={`text-xs font-medium mb-1 ${agentLabel}`}>{agentName}</p>
              )}
              <div className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
                m.role === 'user'
                  ? `${userBubble} rounded-tr-sm`
                  : `${agentBubble} rounded-tl-sm`
              }`}>
                {m.content}
              </div>
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center text-white text-xs font-bold shrink-0">
              {agentName[0]?.toUpperCase() ?? 'C'}
            </div>
            <div className={`rounded-2xl rounded-tl-sm px-4 py-3 ${agentBubble}`}>
              <div className="flex gap-1.5 items-center">
                <span className={`w-1.5 h-1.5 rounded-full animate-bounce ${isDark ? 'bg-slate-500' : 'bg-stone-400'}`} style={{ animationDelay: '0ms' }} />
                <span className={`w-1.5 h-1.5 rounded-full animate-bounce ${isDark ? 'bg-slate-500' : 'bg-stone-400'}`} style={{ animationDelay: '150ms' }} />
                <span className={`w-1.5 h-1.5 rounded-full animate-bounce ${isDark ? 'bg-slate-500' : 'bg-stone-400'}`} style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className={`px-4 pb-4 pt-2 border-t shrink-0 ${borderTop}`}>
        <div className={`flex items-end gap-2 border rounded-2xl px-4 py-2.5 transition-colors ${inputBg}`}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder={PLACEHOLDER[lang]}
            rows={1}
            className="flex-1 bg-transparent text-sm resize-none outline-none max-h-32 leading-relaxed"
            style={{ fieldSizing: 'content' } as React.CSSProperties}
          />
          <button onClick={send} disabled={!input.trim() || loading}
            className="p-1.5 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors shrink-0">
            <Send size={14} className="text-white" />
          </button>
        </div>
        <p className={`text-[10px] text-center mt-1 ${hintColor}`}>{SHIFT_HINT[lang]}</p>
      </div>
    </div>
  )
}
