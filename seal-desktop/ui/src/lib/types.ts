export interface SoulMemory {
  id: number
  content: string
  category: string
  importance: number
  created_at: string
  agent: string
  scope?: string
}

export interface AgentStatus {
  name: string
  status: 'ALIVE' | 'STALE' | 'OFFLINE'
  last_seen?: number
}

export interface AgentOcean {
  agent: string
  ocean: { O: number; C: number; E: number; A: number; N: number }
  baseline?: { O: number; C: number; E: number; A: number; N: number }
  updated_at?: string
}

export interface GamGoal {
  id: number
  agent: string
  topic: string
  summary?: string
  event_count?: number
  relevance_score?: number
  first_seen?: string
  last_updated?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  agent?: string
  timestamp: string
}

export type ViewId = 'chat' | 'memory' | 'agents' | 'goals' | 'skills' | 'nerves' | 'governance' | 'subconscious' | 'profile' | 'settings' | 'integrations' | 'screen' | 'bench' | 'codegraph' | 'activity' | 'pulse' | 'search' | 'replay' | 'speccompiler' | 'topology' | 'eval' | 'awareness' | 'nexus_review' | 'privacy' | 'audit_log'

export const AGENT_COLORS: Record<string, string> = {
  ALICE: '#7c3aed',
  JARVIS: '#2563eb',
  NEXUS: '#059669',
  DUM: '#d97706',
  ADA: '#dc2626',
}

export const OCEAN_COLORS: Record<string, string> = {
  O: '#818cf8',
  C: '#34d399',
  E: '#fbbf24',
  A: '#f472b6',
  N: '#f87171',
}

export const OCEAN_LABELS: Record<string, string> = {
  O: 'Openness',
  C: 'Conscientiousness',
  E: 'Extraversion',
  A: 'Agreeableness',
  N: 'Neuroticism',
}
