import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { API } from '../App'
import { Send, Plus, ChevronDown, Sparkles, Trash2 } from 'lucide-react'

interface Msg { role: string; content: string; ts?: string }
interface Thread { thread_id: string; msg_count: number; last_ts: string; first_ts?: string }

function genThread() {
  return 'thread-' + Date.now()
}

const ES_MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']

function fmtThreadDate(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const day = d.getDate()
  const mon = ES_MONTHS[d.getMonth()] || ''
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${day} ${mon} ${hh}:${mm}`
}

function threadLabel(thread: Thread, index: number, total: number): string {
  // Conversación 1 es la MÁS ANTIGUA (asume threads ordenados desc por last_ts)
  const n = total - index
  const date = fmtThreadDate(thread.first_ts || thread.last_ts)
  return date ? `Conversación ${n} · ${date}` : `Conversación ${n}`
}

interface Props { onMessageSent?: () => void }

export default function ChatView({ onMessageSent }: Props) {
  const [threads, setThreads] = useState<Thread[]>([])
  const [threadId, setThreadId] = useState(genThread)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [showThreads, setShowThreads] = useState(false)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const loadThreads = useCallback(async () => {
    const r = await fetch(`${API}/api/chat/threads`)
    const d = await r.json()
    setThreads(d.threads || [])
  }, [])

  const loadMessages = useCallback(async (tid: string) => {
    const r = await fetch(`${API}/api/chat/threads/${tid}/messages`)
    const d = await r.json()
    setMessages(d.messages || [])
  }, [])

  useEffect(() => {
    loadThreads()
  }, [loadThreads])

  useEffect(() => {
    loadMessages(threadId)
  }, [threadId, loadMessages])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Autocomplete — debounced ghost suggestions while typing
  useEffect(() => {
    const text = input
    if (!text.trim()) { setSuggestions([]); return }
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/api/autocomplete`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, thread_id: threadId, max_suggestions: 3 }),
        })
        const d = await r.json()
        setSuggestions(Array.isArray(d.suggestions) ? d.suggestions : [])
      } catch { setSuggestions([]) }
    }, 200)
    return () => clearTimeout(t)
  }, [input, threadId])

  // Listen for skill injection from SkillsView
  useEffect(() => {
    const handler = (e: Event) => {
      setInput((e as CustomEvent).detail)
      inputRef.current?.focus()
    }
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
      loadThreads()
      onMessageSent?.()
    } catch {
      setMessages(m => [...m, { role: 'assistant', content: '⚠️ Connection error' }])
    }
    setLoading(false)
  }

  function newThread() {
    setThreadId(genThread())
    setMessages([])
    setShowThreads(false)
  }

  function selectThread(tid: string) {
    setThreadId(tid)
    setShowThreads(false)
  }

  async function deleteThread(tid: string, label: string) {
    if (!confirm(`¿Eliminar "${label}"? No se puede deshacer.`)) return
    try {
      await fetch(`${API}/api/chat/threads/${tid}`, { method: 'DELETE' })
    } catch {/* noop */}
    await loadThreads()
    if (tid === threadId) {
      newThread()
    }
  }

  // Compute display label by thread_id for the top bar
  const currentLabel = useMemo(() => {
    const idx = threads.findIndex(t => t.thread_id === threadId)
    if (idx >= 0) return threadLabel(threads[idx], idx, threads.length)
    return `Conversación ${threads.length + 1}` // brand new thread, not yet saved
  }, [threads, threadId])

  function acceptSuggestion(s: string) {
    setInput(s)
    setSuggestions([])
    inputRef.current?.focus()
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
    else if (e.key === 'Tab' && suggestions.length > 0) {
      e.preventDefault()
      acceptSuggestion(suggestions[0])
    } else if (e.key === 'Escape' && suggestions.length > 0) {
      e.preventDefault()
      setSuggestions([])
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Thread selector bar */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-stone-200 shrink-0 bg-white">
        <button
          onClick={() => setShowThreads(!showThreads)}
          className="flex-1 flex items-center gap-1 text-xs text-slate-700 hover:text-slate-900 truncate text-left"
        >
          <ChevronDown size={12} className={`transition-transform ${showThreads ? 'rotate-180' : ''}`} />
          <span className="truncate font-medium">{currentLabel}</span>
        </button>
        <button onClick={newThread} title="Nueva conversación" className="p-1 rounded hover:bg-stone-100 text-slate-500 hover:text-slate-800">
          <Plus size={14} />
        </button>
      </div>

      {/* Thread list dropdown */}
      {showThreads && (
        <div className="absolute z-10 mt-8 left-0 right-0 bg-white border border-stone-200 shadow-xl max-h-72 overflow-y-auto">
          {threads.map((t, i) => {
            const label = threadLabel(t, i, threads.length)
            const isActive = t.thread_id === threadId
            return (
              <div
                key={t.thread_id}
                className={`group flex items-center gap-2 px-3 py-2 text-xs hover:bg-stone-50 ${isActive ? 'bg-blue-50' : ''}`}
              >
                <button
                  onClick={() => selectThread(t.thread_id)}
                  className={`flex-1 flex justify-between items-center text-left truncate ${isActive ? 'text-blue-700 font-medium' : 'text-slate-700'}`}
                >
                  <span className="truncate">{label}</span>
                  <span className="text-stone-400 ml-2 shrink-0">{t.msg_count} msgs</span>
                </button>
                <button
                  onClick={() => deleteThread(t.thread_id, label)}
                  title="Eliminar conversación"
                  className="p-1 rounded text-stone-400 hover:bg-red-50 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            )
          })}
          {threads.length === 0 && <div className="px-3 py-2 text-xs text-stone-400">Aún no hay conversaciones</div>}
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4" onClick={() => setShowThreads(false)}>
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center text-stone-400">
              <div className="text-3xl mb-2">✦</div>
              <div className="text-sm">Empezá a conversar</div>
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs shrink-0 ${
              m.role === 'user' ? 'bg-blue-600' : 'bg-violet-600'
            }`}>
              {m.role === 'user' ? 'U' : '✦'}
            </div>
            <div className={`max-w-[80%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap leading-relaxed ${
              m.role === 'user'
                ? 'bg-blue-600 text-white'
                : 'bg-stone-100 text-slate-800 border border-stone-200'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex gap-3">
            <div className="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center text-xs shrink-0">✦</div>
            <div className="bg-stone-100 border border-stone-200 rounded-xl px-3 py-2">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Autocomplete suggestions */}
      {suggestions.length > 0 && (
        <div className="px-3 pt-1.5 pb-1 border-t border-seal-border shrink-0 bg-seal-bg/60">
          <div className="flex items-center gap-1.5 flex-wrap">
            <Sparkles size={11} className="text-blue-400/70 shrink-0" />
            <span className="text-[10px] uppercase tracking-widest text-seal-muted">sugerencias</span>
            {suggestions.map((s, i) => (
              <button
                key={`${i}-${s}`}
                onClick={() => acceptSuggestion(s)}
                className="text-xs px-2 py-0.5 rounded-full bg-blue-600/15 hover:bg-blue-600/30 text-blue-300 border border-blue-500/20 truncate max-w-[260px]"
                title={i === 0 ? 'Tab para aceptar' : 'Click para usar'}
              >
                {s}
              </button>
            ))}
            <span className="ml-auto text-[10px] text-seal-muted">Tab ↹ acepta · Esc cierra</span>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="p-3 border-t border-seal-border shrink-0">
        <div className="flex items-end gap-2 bg-seal-surface border border-seal-border rounded-xl px-3 py-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Escribí un mensaje… (Enter para enviar)"
            rows={1}
            className="flex-1 bg-transparent text-sm text-slate-800 placeholder:text-stone-400 resize-none outline-none max-h-32"
            style={{ fieldSizing: 'content' } as React.CSSProperties}
          />
          <button
            onClick={send}
            disabled={!input.trim() || loading}
            className="p-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <Send size={14} />
          </button>
        </div>
        <div className="text-xs text-stone-400 mt-1 text-center">Shift+Enter para nueva línea</div>
      </div>
    </div>
  )
}
