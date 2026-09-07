import type { MascotAccessory, MascotMotion, MascotVariant } from '../components/SoulMascot'

export interface AvatarProfile {
  variant: MascotVariant
  primary_color: string
  secondary_color: string
  accent_color: string
  accessory: MascotAccessory
  motion: MascotMotion
}

export const DEFAULT_AVATAR: AvatarProfile = {
  variant: 'orb',
  primary_color: '#a78bfa',
  secondary_color: '#7c3aed',
  accent_color: '#fb7185',
  accessory: 'none',
  motion: 'normal',
}

export const AVATAR_PALETTES = [
  { name: 'Violeta',  primary_color: '#a78bfa', secondary_color: '#7c3aed', accent_color: '#fb7185' },
  { name: 'Menta',    primary_color: '#14b8a6', secondary_color: '#0f766e', accent_color: '#f59e0b' },
  { name: 'Azul',     primary_color: '#60a5fa', secondary_color: '#2563eb', accent_color: '#f472b6' },
  { name: 'Ámbar',    primary_color: '#f59e0b', secondary_color: '#b45309', accent_color: '#38bdf8' },
  { name: 'Rosa',     primary_color: '#f472b6', secondary_color: '#be185d', accent_color: '#fde68a' },
  { name: 'Lima',     primary_color: '#a3e635', secondary_color: '#4d7c0f', accent_color: '#f0abfc' },
  { name: 'Dorado',   primary_color: '#fcd34d', secondary_color: '#a16207', accent_color: '#c4b5fd' },
  { name: 'Monocromo',primary_color: '#94a3b8', secondary_color: '#334155', accent_color: '#e2e8f0' },
] as const

