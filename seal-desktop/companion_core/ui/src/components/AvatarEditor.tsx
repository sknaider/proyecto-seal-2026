import { useEffect, useState } from 'react'
import { API } from '../App'
import { SoulMascot, type MascotAccessory, type MascotMotion, type MascotVariant } from './SoulMascot'
import { AVATAR_PALETTES, DEFAULT_AVATAR, type AvatarProfile } from '../lib/avatar'
import { Palette, Sparkles } from 'lucide-react'

interface Props {
  previewSize?: number
  compact?: boolean
}

export function AvatarEditor({ previewSize = 300, compact = false }: Props) {
  const [avatar, setAvatar] = useState<AvatarProfile>(DEFAULT_AVATAR)
  const [message, setMessage] = useState('')

  useEffect(() => {
    fetch(`${API}/api/avatar/profile`)
      .then(r => r.json())
      .then(d => { if (d?.avatar) setAvatar(d.avatar) })
      .catch(() => setMessage('No se pudo cargar'))
  }, [])

  const saveAvatar = async (patch: Partial<AvatarProfile>) => {
    const next = { ...avatar, ...patch }
    setAvatar(next)
    setMessage('')
    try {
      const r = await fetch(`${API}/api/avatar/profile`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setAvatar(d.avatar || next)
      setMessage('Guardado')
    } catch {
      setMessage('No se pudo guardar')
    }
  }

  return (
    <div className={`grid gap-4 ${compact ? '' : 'lg:grid-cols-[minmax(0,1fr)_380px]'}`}>
      <div className="flex min-h-[320px] items-center justify-center rounded-lg border border-stone-200 bg-white/80 p-6">
        <SoulMascot
          state="idle"
          size={previewSize}
          variant={avatar.variant}
          primaryColor={avatar.primary_color}
          secondaryColor={avatar.secondary_color}
          accentColor={avatar.accent_color}
          accessory={avatar.accessory}
          motion={avatar.motion}
        />
      </div>

      <div className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Palette className="h-4 w-4 text-violet-500" />
            <h2 className="text-sm font-semibold text-slate-800">Avatar</h2>
          </div>
          {message && <span className="text-[11px] text-slate-500">{message}</span>}
        </div>

        <div className="space-y-4">
          <div>
            <p className="mb-2 text-xs font-medium text-slate-600">Forma</p>
            <div className="grid grid-cols-3 gap-2">
              {(['orb', 'leaf', 'spark'] as MascotVariant[]).map(v => (
                <button
                  key={v}
                  onClick={() => void saveAvatar({ variant: v })}
                  className={`rounded border px-2 py-2 text-xs capitalize ${avatar.variant === v ? 'border-violet-500 bg-violet-50 text-violet-700' : 'border-stone-200 text-slate-600 hover:border-violet-300'}`}
                >
                  {v}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="mb-2 text-xs font-medium text-slate-600">Color</p>
            <div className="grid grid-cols-4 gap-2">
              {AVATAR_PALETTES.map(palette => (
                <button
                  key={palette.name}
                  onClick={() => void saveAvatar(palette)}
                  className="h-10 rounded border border-stone-200 hover:border-violet-400"
                  title={palette.name}
                  style={{ background: `linear-gradient(90deg, ${palette.primary_color}, ${palette.secondary_color} 60%, ${palette.accent_color})` }}
                />
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-xs font-medium text-slate-600">
              Accesorio
              <select
                value={avatar.accessory}
                onChange={e => void saveAvatar({ accessory: e.target.value as MascotAccessory })}
                className="mt-1 w-full rounded border border-stone-200 bg-white px-2 py-2 text-xs text-slate-700 outline-none focus:border-violet-400"
              >
                <option value="none">Sin accesorio</option>
                <option value="halo">Halo</option>
                <option value="headset">Headset</option>
                <option value="badge">Badge</option>
              </select>
            </label>

            <label className="text-xs font-medium text-slate-600">
              Movimiento
              <select
                value={avatar.motion}
                onChange={e => void saveAvatar({ motion: e.target.value as MascotMotion })}
                className="mt-1 w-full rounded border border-stone-200 bg-white px-2 py-2 text-xs text-slate-700 outline-none focus:border-violet-400"
              >
                <option value="calm">Calma</option>
                <option value="normal">Normal</option>
                <option value="expressive">Expresiva</option>
              </select>
            </label>
          </div>

          <div className="flex items-center gap-2 rounded border border-violet-100 bg-violet-50 px-3 py-2 text-xs text-violet-700">
            <Sparkles className="h-4 w-4 shrink-0" />
            <span>Este perfil se guarda localmente y se usa en Inicio y Voz.</span>
          </div>
        </div>
      </div>
    </div>
  )
}

