import { useEffect, useState } from 'react'

export type MascotState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'happy' | 'sad'

interface Props {
  state?: MascotState
  size?: number
  name?: string
}

/**
 * SoulMascot — SVG blob mascot for user-product mode.
 *
 * Inspired by OpenHuman's yellow blob but with SEAL palette (warm violet
 * with rosy cheeks). Pure SVG + CSS animations — no external deps, no
 * lottie. Reactive to state: breathing idle, pulsing when listening,
 * thinking dots above, mouth animation when speaking.
 */
export function SoulMascot({ state = 'idle', size = 240, name }: Props) {
  // Thinking dot cycle index
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (state !== 'thinking') return
    const id = setInterval(() => setTick(t => (t + 1) % 3), 350)
    return () => clearInterval(id)
  }, [state])

  const animationClass = (() => {
    switch (state) {
      case 'listening':  return 'mascot-pulse'
      case 'speaking':   return 'mascot-talk'
      case 'happy':      return 'mascot-bounce'
      case 'sad':        return 'mascot-droop'
      case 'thinking':   return 'mascot-think'
      case 'idle':
      default:           return 'mascot-breathe'
    }
  })()

  const eyeY = state === 'sad' ? 178 : 175
  const mouthPath = (() => {
    switch (state) {
      case 'happy':    return 'M 130 195 Q 150 215 170 195'
      case 'sad':      return 'M 130 210 Q 150 195 170 210'
      case 'speaking': return 'M 130 200 Q 150 220 170 200'
      case 'thinking': return 'M 130 200 L 170 200'
      default:         return 'M 130 200 Q 150 212 170 200'
    }
  })()

  return (
    <div className="flex flex-col items-center justify-center select-none">
      <style>{`
        @keyframes mascot-breathe {
          0%, 100% { transform: scale(1) translateY(0); }
          50%      { transform: scale(1.02) translateY(-2px); }
        }
        @keyframes mascot-pulse {
          0%, 100% { transform: scale(1);    filter: drop-shadow(0 0 0 rgba(167,139,250,0)); }
          50%      { transform: scale(1.04); filter: drop-shadow(0 0 14px rgba(167,139,250,0.55)); }
        }
        @keyframes mascot-talk {
          0%, 100% { transform: scale(1)    translateY(0); }
          25%      { transform: scale(1.01) translateY(-1px); }
          75%      { transform: scale(0.99) translateY(1px); }
        }
        @keyframes mascot-bounce {
          0%, 100% { transform: scale(1)    translateY(0); }
          40%      { transform: scale(1.05) translateY(-6px); }
        }
        @keyframes mascot-droop {
          0%, 100% { transform: scale(1)    translateY(3px); }
        }
        @keyframes mascot-think {
          0%, 100% { transform: scale(1)    translateY(0); }
          50%      { transform: scale(1.005) translateY(-1px); }
        }
        .mascot-breathe { animation: mascot-breathe 4s ease-in-out infinite; }
        .mascot-pulse   { animation: mascot-pulse   1.4s ease-in-out infinite; }
        .mascot-talk    { animation: mascot-talk    0.45s ease-in-out infinite; }
        .mascot-bounce  { animation: mascot-bounce  0.9s ease-in-out infinite; }
        .mascot-droop   { animation: mascot-droop   3s ease-in-out infinite; }
        .mascot-think   { animation: mascot-think   3s ease-in-out infinite; }
        .thought-dot { opacity: 0.25; transition: opacity 0.2s ease; }
        .thought-dot.on { opacity: 1; }
      `}</style>

      <svg
        viewBox="0 0 300 320"
        width={size}
        height={size}
        className={animationClass}
        style={{ transformOrigin: '50% 60%' }}
      >
        <defs>
          <radialGradient id="bodyGradient" cx="50%" cy="40%" r="60%">
            <stop offset="0%"  stopColor="#c4b5fd" />
            <stop offset="55%" stopColor="#a78bfa" />
            <stop offset="100%" stopColor="#7c3aed" />
          </radialGradient>
          <radialGradient id="cheek" cx="50%" cy="50%" r="50%">
            <stop offset="0%"  stopColor="rgba(251,113,133,0.85)" />
            <stop offset="100%" stopColor="rgba(251,113,133,0)" />
          </radialGradient>
          <filter id="softShadow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="8" result="blur" />
            <feOffset dy="6" />
            <feComponentTransfer result="shadow">
              <feFuncA type="linear" slope="0.4" />
            </feComponentTransfer>
            <feMerge>
              <feMergeNode in="shadow" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Head orb (small floating bubble) */}
        <circle cx="150" cy="58" r="22" fill="url(#bodyGradient)" filter="url(#softShadow)" />

        {/* Body blob — pear-ish silhouette */}
        <path
          d="M 60 180
             Q 60 110 150 110
             Q 240 110 240 180
             Q 245 240 200 270
             Q 175 290 150 290
             Q 125 290 100 270
             Q 55 240 60 180 Z"
          fill="url(#bodyGradient)"
          filter="url(#softShadow)"
        />

        {/* Cheeks */}
        <circle cx="105" cy="200" r="16" fill="url(#cheek)" />
        <circle cx="195" cy="200" r="16" fill="url(#cheek)" />

        {/* Eyes */}
        <circle cx="125" cy={eyeY} r="6" fill="#1f1235" />
        <circle cx="175" cy={eyeY} r="6" fill="#1f1235" />
        <circle cx="127" cy={eyeY - 2} r="2" fill="#fff" />
        <circle cx="177" cy={eyeY - 2} r="2" fill="#fff" />

        {/* Mouth */}
        <path d={mouthPath} stroke="#1f1235" strokeWidth="3" fill="none" strokeLinecap="round" />

        {/* Tiny arms hint (subtle curves at sides) */}
        <path d="M 62 215 Q 50 230 60 245" stroke="#7c3aed" strokeWidth="3" fill="none" strokeLinecap="round" opacity="0.6" />
        <path d="M 238 215 Q 250 230 240 245" stroke="#7c3aed" strokeWidth="3" fill="none" strokeLinecap="round" opacity="0.6" />

        {/* Thinking dots above head */}
        {state === 'thinking' && (
          <g>
            <circle cx="180" cy="40" r="4" fill="#a78bfa" className={`thought-dot ${tick >= 0 ? 'on' : ''}`} />
            <circle cx="195" cy="30" r="3" fill="#a78bfa" className={`thought-dot ${tick >= 1 ? 'on' : ''}`} />
            <circle cx="208" cy="22" r="2" fill="#a78bfa" className={`thought-dot ${tick >= 2 ? 'on' : ''}`} />
          </g>
        )}
      </svg>

      {name && (
        <div className="mt-4 text-center">
          <p className="text-sm text-gray-500 uppercase tracking-widest">SOUL</p>
          <p className="text-2xl font-light text-gray-100">{name}</p>
          <p className="text-xs text-gray-500 mt-1 capitalize">{state}</p>
        </div>
      )}
    </div>
  )
}

export default SoulMascot
