import { useEffect, useState } from 'react'
import { SoulMascot, type MascotAccessory, type MascotMotion, type MascotState, type MascotVariant } from './SoulMascot'
import { API } from '../App'

interface HomeViewProps {
  agentName?: string
  userName?: string
  emotion?: string
  onStartChat?: () => void
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

/**
 * HomeView — SEAL App entry screen.
 *
 * OpenHuman-inspired split layout:
 *   • Left: SoulMascot (animated)
 *   • Right: status card + quick CTAs
 *
 * Mirrors doc 13 (Home post-onboarding) with SEAL local-first positioning.
 */
export function HomeView({ agentName = 'SEAL', userName = '', emotion = 'calm', onStartChat }: HomeViewProps) {
  const [model] = useState<string>('GEMMA 4 local')
  const [unread, setUnread] = useState<number>(0)
  const [localityHint, setLocalityHint] = useState<string>('Local · sin costo')
  const [avatar, setAvatar] = useState<AvatarProfile>(DEFAULT_AVATAR)

  useEffect(() => {
    fetch(`${API}/api/health`).then(r => r.json()).then(d => {
      if (typeof d?.stats?.messages === 'number') setUnread(d.stats.messages)
      if (d?.status === 'ok') setLocalityHint('Local · sin costo')
    }).catch(() => {})
    fetch(`${API}/api/avatar/profile`)
      .then(r => r.json())
      .then(d => { if (d?.avatar) setAvatar(d.avatar) })
      .catch(() => {})
  }, [])

  const mascotState: MascotState = mapEmotion(emotion)

  return (
    <div className="h-full flex flex-col bg-gradient-to-b from-[#0a0814] to-[#13102b] text-gray-100">
      {/* Local-first status banner. */}
      <div className="px-4 py-2 text-xs text-violet-300/90 bg-violet-950/40 border-b border-violet-900/60 flex items-center justify-center gap-2">
        <span>🟣</span>
        <span>{localityHint}</span>
        <span className="text-violet-300/50">·</span>
        <span className="text-violet-300/70">{model}</span>
      </div>

      <div className="flex-1 flex flex-col md:flex-row items-center justify-center px-6 py-8 gap-6 md:gap-12 overflow-hidden">
        {/* Left: mascot */}
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

        {/* Right: status card + CTA */}
        <div className="w-full max-w-md flex flex-col gap-4">
          <div className="rounded-2xl bg-white/5 backdrop-blur border border-white/10 shadow-2xl p-6 text-center">
            <p className="text-3xl font-light tracking-tight">
              {userName ? <>Hola, <span className="text-violet-300">{userName}</span></> : <>Hola.</>}
            </p>
            <p className="mt-2 text-sm text-gray-400">
              {agentName} está despierta y lista para conversar.
            </p>

            <div className="mt-5 flex items-center justify-center gap-2 text-xs">
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-900/40 border border-emerald-700/60 text-emerald-300">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" /> Activa
              </span>
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-violet-900/40 border border-violet-700/60 text-violet-300">
                {model}
              </span>
              {unread > 0 && (
                <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-amber-900/40 border border-amber-700/60 text-amber-300">
                  {unread} sin leer
                </span>
              )}
            </div>

            <button
              onClick={onStartChat}
              className="mt-6 w-full py-3 px-4 rounded-xl bg-violet-600 hover:bg-violet-500 transition-colors text-white font-medium shadow-lg shadow-violet-900/50"
            >
              💬 Empezar a conversar
            </button>

            <p className="mt-3 text-xs text-gray-500">
              Pruebá pidiendo: <span className="text-gray-400">"Resumime lo de hoy"</span>, <span className="text-gray-400">"Mi agenda mañana"</span>, <span className="text-gray-400">"Recordame X"</span>
            </p>
          </div>

          {/* Community / Soporte card — equivalente al Discord card de OpenHuman pero más sobrio */}
          <div className="rounded-2xl bg-white/5 backdrop-blur border border-white/10 p-4 flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-violet-900/60 flex items-center justify-center text-violet-300">💜</div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-200">Únete a la comunidad SEAL</p>
              <p className="text-xs text-gray-500">Updates, ideas y bugs en nuestro canal.</p>
            </div>
            <a
              href="https://github.com/sknaider"
              target="_blank"
              rel="noreferrer noopener"
              className="text-xs text-violet-300 hover:text-violet-200"
            >
              Abrir →
            </a>
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
