import { useEffect, useRef, useState } from 'react'
import { API } from '../App'
import { SoulMascot, type MascotState } from '../components/SoulMascot'
import { DEFAULT_AVATAR, type AvatarProfile } from '../lib/avatar'
import { Mic, MicOff, Volume2, VolumeX } from 'lucide-react'

interface Msg { role: 'user' | 'assistant' | string; content: string; ts?: string }

/**
 * HumanView — voice-first chat con el SoulMascot grande.
 *
 * Mirror del Human view de OpenHuman (doc 14): mascota gigante izq +
 * conversación simple derecha + mic abajo. Versión SEAL App: STT vía
 * Web Speech API local cuando disponible, TTS opcional.
 */
export default function HumanView() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [listening, setListening] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [speakReplies, setSpeakReplies] = useState(true)
  const [transcript, setTranscript] = useState('')
  const [supported, setSupported] = useState<boolean>(false)
  const [avatar, setAvatar] = useState<AvatarProfile>(DEFAULT_AVATAR)
  const recognitionRef = useRef<any>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (!SR) { setSupported(false); return }
    setSupported(true)
    const rec = new SR()
    rec.lang = 'es-PE'
    rec.continuous = false
    rec.interimResults = true
    rec.onresult = (e: any) => {
      let text = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        text += e.results[i][0].transcript
      }
      setTranscript(text)
      if (e.results[e.results.length - 1].isFinal) {
        void send(text)
      }
    }
    rec.onend = () => setListening(false)
    rec.onerror = () => setListening(false)
    recognitionRef.current = rec
    return () => { try { rec.stop() } catch {} }
  }, [])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  useEffect(() => {
    fetch(`${API}/api/avatar/profile`)
      .then(r => r.json())
      .then(d => { if (d?.avatar) setAvatar(d.avatar) })
      .catch(() => {})
  }, [])

  const startListening = () => {
    if (!recognitionRef.current || listening) return
    setTranscript('')
    setListening(true)
    try { recognitionRef.current.start() } catch { setListening(false) }
  }

  const stopListening = () => {
    if (!recognitionRef.current) return
    try { recognitionRef.current.stop() } catch {}
    setListening(false)
  }

  const speak = (text: string) => {
    if (!speakReplies || !('speechSynthesis' in window)) return
    try {
      const u = new SpeechSynthesisUtterance(text)
      u.lang = 'es-PE'
      u.rate = 1.0
      window.speechSynthesis.speak(u)
    } catch {/* noop */}
  }

  const send = async (text: string) => {
    const clean = text.trim()
    if (!clean) return
    setMessages(prev => [...prev, { role: 'user', content: clean }])
    setTranscript('')
    setThinking(true)
    try {
      const r = await fetch(`${API}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: 'human-voice', content: clean }),
      })
      const d = await r.json()
      const reply = d?.reply ?? d?.message ?? d?.content ?? '(sin respuesta)'
      setMessages(prev => [...prev, { role: 'assistant', content: reply }])
      speak(reply)
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${e instanceof Error ? e.message : 'unknown'}` }])
    } finally {
      setThinking(false)
    }
  }

  const mascotState: MascotState = listening ? 'listening' : thinking ? 'thinking' : 'idle'

  return (
    <div className="h-full flex flex-col md:flex-row bg-gradient-to-br from-stone-50 to-violet-50">
      {/* Mascota grande */}
      <div className="flex-1 flex flex-col items-center justify-center p-6 select-none">
        <SoulMascot
          state={mascotState}
          size={300}
          variant={avatar.variant}
          primaryColor={avatar.primary_color}
          secondaryColor={avatar.secondary_color}
          accentColor={avatar.accent_color}
          accessory={avatar.accessory}
          motion={avatar.motion}
        />
        <p className="mt-6 text-sm text-slate-500 max-w-xs text-center">
          {listening
            ? '🎙 Escuchando…'
            : thinking
            ? '💭 Pensando…'
            : 'Tocá el mic y hablale a tu SEAL.'}
        </p>
        {transcript && (
          <p className="mt-3 text-xs text-violet-600 italic max-w-md text-center px-4">"{transcript}"</p>
        )}
      </div>

      {/* Chat lateral + controles */}
      <div className="flex flex-col w-full md:w-[420px] border-l border-stone-200 bg-white shadow-inner">
        <div className="px-4 py-3 border-b border-stone-200 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-800">Voz</h2>
          <button
            onClick={() => setSpeakReplies(v => !v)}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700"
            title="Speak replies"
          >
            {speakReplies ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
            {speakReplies ? 'TTS on' : 'TTS off'}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {messages.length === 0 && (
            <p className="text-xs text-slate-400 text-center py-6">
              Aún no charlaron. Pulsá el mic para empezar.
            </p>
          )}
          {messages.map((m, i) => (
            <div
              key={i}
              className={`max-w-[85%] px-3 py-2 rounded-2xl text-sm ${m.role === 'user' ? 'ml-auto bg-violet-500 text-white' : 'mr-auto bg-stone-100 text-slate-800'}`}
            >
              {m.content}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        <div className="p-4 border-t border-stone-200 flex flex-col items-center gap-2">
          {!supported && (
            <p className="text-[11px] text-amber-600 text-center">
              Tu navegador no soporta Web Speech API. Probá Brave o Chrome.
            </p>
          )}
          <button
            disabled={!supported || thinking}
            onClick={listening ? stopListening : startListening}
            className={`w-16 h-16 rounded-full shadow-lg flex items-center justify-center text-white transition-all ${listening ? 'bg-red-500 hover:bg-red-600 animate-pulse' : 'bg-violet-500 hover:bg-violet-600'} disabled:opacity-40`}
            aria-label={listening ? 'Detener' : 'Hablar'}
          >
            {listening ? <MicOff className="w-7 h-7" /> : <Mic className="w-7 h-7" />}
          </button>
          <p className="text-[11px] text-slate-500">
            {listening ? 'Tocá para detener' : 'Tocá y hablá'}
          </p>
        </div>
      </div>
    </div>
  )
}
