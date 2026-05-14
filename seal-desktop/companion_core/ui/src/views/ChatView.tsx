import { useState, useEffect, useRef, useCallback } from 'react'
import { API } from '../App'
import { Send, Plus, ChevronDown } from 'lucide-react'

interface Msg { role: string; content: string; ts?: string }
interface Thread { thread_id: string; msg_count: number; last_ts: string }

function genThread() {
  return 'thread-' + Date.now()
}

interface Props { onMessageSent?: () => void }

export default function ChatView({ onMessageSent }: Props) {
  const [threads, setThreads] = useState<Thread[]>([])
  const [threadId, setThreadId] = useState(genThread)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [showThreads, setShowThreads] = useState(false)
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

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Thread selector bar */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-seal-border shrink-0">
        <button
          onClick={() => setShowThreads(!showThreads)}
          className="flex-1 flex items-center gap-1 text-xs text-seal-muted hover:text-slate-300 truncate text-left"
        >
          <ChevronDown size={12} />
          <span className="truncate">{threadId}</span>
        </button>
        <button onClick={newThread} className="p-1 rounded hover:bg-seal-border text-seal-muted hover:text-slate-300">
          <Plus size={14} />
        </button>
      </div>

      {/* Thread list dropdown */}
      {showThreads && (
        <div className="absolute z-10 mt-8 left-0 right-0 bg-seal-surface border border-seal-border shadow-xl max-h-52 overflow-y-auto">
          {threads.map(t => (
            <button
              key={t.thread_id}
              onClick={() => selectThread(t.thread_id)}
              className={`w-full text-left px-3 py-2 text-xs hover:bg-seal-border flex justify-between items-center ${t.thread_id === threadId ? 'text-blue-400' : 'text-slate-300'}`}
            >
              <span className="truncate">{t.thread_id}</span>
              <span className="text-seal-muted ml-2 shrink-0">{t.msg_count} msgs</span>
            </button>
          ))}
          {threads.length === 0 && <div className="px-3 py-2 text-xs text-seal-muted">No threads yet</div>}
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4" onClick={() => setShowThreads(false)}>
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center text-seal-muted">
              <div className="text-3xl mb-2">✦</div>
              <div className="text-sm">Start a conversation</div>
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs shrink-0 ${
              m.role === 'user' ? 'bg-blue-600' : 'bg-slate-700'
            }`}>
              {m.role === 'user' ? 'U' : '✦'}
            </div>
            <div className={`max-w-[80%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap leading-relaxed ${
              m.role === 'user'
                ? 'bg-blue-600/20 text-slate-100'
                : 'bg-seal-surface text-slate-200 border border-seal-border'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex gap-3">
            <div className="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center text-xs shrink-0">✦</div>
            <div className="bg-seal-surface border border-seal-border rounded-xl px-3 py-2">
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

      {/* Input */}
      <div className="p-3 border-t border-seal-border shrink-0">
        <div className="flex items-end gap-2 bg-seal-surface border border-seal-border rounded-xl px-3 py-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Message your companion… (Enter to send)"
            rows={1}
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-seal-muted resize-none outline-none max-h-32"
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
        <div className="text-xs text-seal-muted mt-1 text-center">Shift+Enter for newline</div>
      </div>
    </div>
  )
}
