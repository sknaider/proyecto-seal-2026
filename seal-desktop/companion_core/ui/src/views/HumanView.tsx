import { useEffect, useRef, useState } from 'react'
import { API } from '../App'
import { SoulMascot, type MascotAccessory, type MascotMotion, type MascotState, type MascotVariant } from '../components/SoulMascot'
import { Mic, MicOff, Volume2, VolumeX, Palette } from 'lucide-react'

interface Msg { role: 'user' | 'assistant' | string; content: string; ts?: string }
interface AvatarProfile {
  variant: MascotVariant
  primary_color: string
  secondary_color: string
  accent_color: string
  accessory: MascotAccessory
  motion: MascotMotion
}

const DEFAULT_AVATAR: AvatarProfile = {
  variant: 'orb',
  primary_color: '#a78bfa',
  secondary_color: '#7c3aed',
  accent_color: '#fb7185',
  accessory: 'none',
  motion: 'normal',
}
const PALETTES = [
  { name: 'Violeta', primary_color: '#a78bfa', secondary_color: '#7c3aed', accent_color: '#fb7185' },
  { name: 'Menta', primary_color: '#14b8a6', secondary_color: '#0f766e', accent_color: '#f59e0b' },
  { name: 'Azul', primary_color: '#60a5fa', secondary_color: '#2563eb', accent_color: '#f472b6' },
  { name: 'Ambar', primary_color: '#f59e0b', secondary_color: '#b45309', accent_color: '#38bdf8' },
]

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
  const [avatarMessage, setAvatarMessage] = useState('')
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

  const saveAvatar = async (patch: Partial<AvatarProfile>) => {
    const next = { ...avatar, ...patch }
    setAvatar(next)
    setAvatarMessage('')
    try {
      const r = await fetch(`${API}/api/avatar/profile`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setAvatar(d.avatar || next)
      setAvatarMessage('Guardado')
    } catch {
      setAvatarMessage('No se pudo guardar')
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

        <div className="mt-5 w-full max-w-md rounded-lg border border-stone-200 bg-white/80 p-3 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Palette className="w-4 h-4 text-violet-500" />
              <h2 className="text-sm font-semibold text-slate-800">Avatar</h2>
            </div>
            {avatarMessage && <span className="text-[11px] text-slate-500">{avatarMessage}</span>}
          </div>
          <div className="grid grid-cols-3 gap-2">
            {(['orb', 'leaf', 'spark'] as MascotVariant[]).map(v => (
              <button
                key={v}
                onClick={() => void saveAvatar({ variant: v })}
                className={`rounded border px-2 py-1.5 text-xs capitalize ${avatar.variant === v ? 'border-violet-500 bg-violet-50 text-violet-700' : 'border-stone-200 text-slate-600'}`}
              >
                {v}
              </button>
            ))}
          </div>
          <div className="mt-2 flex gap-2">
            {PALETTES.map(palette => (
              <button
                key={palette.name}
                onClick={() => void saveAvatar(palette)}
                className="h-8 flex-1 rounded border border-stone-200"
                title={palette.name}
                style={{ background: `linear-gradient(90deg, ${palette.primary_color}, ${palette.secondary_color} 60%, ${palette.accent_color})` }}
              />
            ))}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <select
              value={avatar.accessory}
              onChange={e => void saveAvatar({ accessory: e.target.value as MascotAccessory })}
              className="rounded border border-stone-200 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none"
            >
              <option value="none">Sin accesorio</option>
              <option value="halo">Halo</option>
              <option value="headset">Headset</option>
              <option value="badge">Badge</option>
            </select>
            <select
              value={avatar.motion}
              onChange={e => void saveAvatar({ motion: e.target.value as MascotMotion })}
              className="rounded border border-stone-200 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none"
            >
              <option value="calm">Calma</option>
              <option value="normal">Normal</option>
              <option value="expressive">Expresiva</option>
            </select>
          </div>
        </div>
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
