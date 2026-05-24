import { useEffect, useState } from 'react'
import { SoulMascot, type MascotAccessory, type MascotMotion, type MascotState, type MascotVariant } from './SoulMascot'
import { API } from '../App'
import { Lock, Sparkles, Calendar, Sun, MessageCircle } from 'lucide-react'

interface HomeViewProps {
  agentName?: string
  userName?: string
  emotion?: string
  onStartChat?: (prefill?: string) => void
}

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

const QUICK_PROMPTS = [
  { icon: Sun,           label: 'Resumime lo de hoy', prompt: 'Resumime lo de hoy en pocas frases.' },
  { icon: Calendar,      label: 'Mi agenda mañana',   prompt: '¿Qué tengo mañana? Dame un resumen claro.' },
  { icon: MessageCircle, label: 'Charlamos un rato',  prompt: 'Hola, ¿cómo estuviste hoy?' },
] as const

/**
 * HomeView — SEAL App entry screen.
 *
 * Light theme coherente con el resto del producto.
 * Layout split: mascot grande izquierda + tarjeta de bienvenida + quick prompts derecha.
 */
export function HomeView({ agentName = 'SEAL', userName = '', emotion = 'calm', onStartChat }: HomeViewProps) {
  const [model] = useState<string>('GEMMA 4 local')
  const [avatar, setAvatar] = useState<AvatarProfile>(DEFAULT_AVATAR)

  useEffect(() => {
    fetch(`${API}/api/avatar/profile`)
      .then(r => r.json())
      .then(d => { if (d?.avatar) setAvatar(d.avatar) })
      .catch(() => {})
  }, [])

  const mascotState: MascotState = mapEmotion(emotion)

  return (
    <div className="h-full flex flex-col bg-gradient-to-br from-stone-50 via-white to-violet-50 text-slate-800">
      {/* Local-first status banner — pill flotante con sello */}
      <div className="px-4 py-2 flex items-center justify-center gap-2 text-xs">
        <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700 shadow-sm">
          <Lock className="w-3 h-3" />
          Tus datos se quedan en tu equipo
        </span>
        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-violet-50 border border-violet-200 text-violet-700">
          <Sparkles className="w-3 h-3" />
          {model}
        </span>
      </div>

      <div className="flex-1 flex flex-col md:flex-row items-center justify-center px-6 py-6 gap-8 md:gap-12 overflow-hidden">
        {/* Mascot */}
        <div className="flex-shrink-0">
          <SoulMascot
            state={mascotState}
            size={260}
            name={agentName}
            variant={avatar.variant}
            primaryColor={avatar.primary_color}
            secondaryColor={avatar.secondary_color}
            accentColor={avatar.accent_color}
            accessory={avatar.accessory}
            motion={avatar.motion}
          />
        </div>

        {/* Welcome card + quick prompts */}
        <div className="w-full max-w-md flex flex-col gap-4">
          <div className="rounded-2xl bg-white border border-stone-200 shadow-lg shadow-violet-900/5 p-6 text-center">
            <p className="text-3xl font-light tracking-tight text-slate-900">
              {userName ? <>Hola, <span className="text-violet-600 font-medium">{userName}</span></> : <>Hola.</>}
            </p>
            <p className="mt-1.5 text-sm text-slate-500">
              {agentName} está despierta y lista para conversar.
            </p>

            <div className="mt-4 flex items-center justify-center gap-2 text-xs">
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" /> Activa
              </span>
            </div>

            <button
              onClick={() => onStartChat?.()}
              className="mt-5 w-full py-3 px-4 rounded-xl bg-violet-600 hover:bg-violet-500 active:bg-violet-700 transition-colors text-white font-medium shadow-md shadow-violet-200"
            >
              💬 Empezar a conversar
            </button>
          </div>

          {/* Quick prompts — chips clickeables que arrancan chat con prefill */}
          <div>
            <p className="text-[11px] uppercase tracking-widest text-slate-400 mb-2 text-center">
              Pedile algo rápido
            </p>
            <div className="flex flex-col gap-1.5">
              {QUICK_PROMPTS.map(({ icon: Icon, label, prompt }) => (
                <button
                  key={label}
                  onClick={() => onStartChat?.(prompt)}
                  className="flex items-center gap-2 px-3 py-2 rounded-xl bg-white border border-stone-200 hover:border-violet-300 hover:bg-violet-50 transition-colors text-sm text-slate-700 hover:text-violet-700 shadow-sm text-left group"
                >
                  <Icon className="w-4 h-4 text-violet-500 shrink-0 group-hover:scale-110 transition-transform" />
                  <span className="flex-1 truncate">{label}</span>
                  <span className="text-stone-300 group-hover:text-violet-400">→</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function mapEmotion(e: string): MascotState {
  const k = (e || '').toLowerCase()
  if (k === 'happy' || k === 'satisfied' || k === 'energetic') return 'happy'
  if (k === 'sad' || k === 'tired') return 'sad'
  if (k === 'focused' || k === 'reflective' || k === 'thinking') return 'thinking'
  if (k === 'listening') return 'listening'
  if (k === 'speaking') return 'speaking'
  return 'idle'
}

export default HomeView
