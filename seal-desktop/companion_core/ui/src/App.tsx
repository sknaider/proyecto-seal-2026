import { lazy, Suspense, useState, useEffect } from 'react'
import { HomeView } from './components/HomeView'
import { FirstRunWizard } from './components/FirstRunWizard'
import { Home, MessageSquare, Brain, Zap, Target, Settings, Moon, Shield, Bell, Cpu, ClipboardList, Mic, Plug, TreePine, Gift, Monitor, Filter, Users, Palette } from 'lucide-react'

export const API = 'http://localhost:8769'

const ChatView = lazy(() => import('./views/ChatView'))
const MemoryView = lazy(() => import('./views/MemoryView'))
const SkillsView = lazy(() => import('./views/SkillsView'))
const GoalsView = lazy(() => import('./views/GoalsView'))
const SettingsView = lazy(() => import('./views/SettingsView'))
const DreamsView = lazy(() => import('./views/DreamsView'))
const PrivacyView = lazy(() => import('./views/PrivacyView'))
const NotificationsView = lazy(() => import('./views/NotificationsView'))
const AIBackendView = lazy(() => import('./views/AIBackendView'))
const AuditLogView = lazy(() => import('./views/AuditLogView'))
const HumanView = lazy(() => import('./views/HumanView'))
const AvatarView = lazy(() => import('./views/AvatarView'))
const ConnectionsView = lazy(() => import('./views/ConnectionsView'))
const MemoryTreeView = lazy(() => import('./views/MemoryTreeView'))
const RewardsView = lazy(() => import('./views/RewardsView'))
const ScreenView = lazy(() => import('./views/ScreenView'))
const TokenJuiceView = lazy(() => import('./views/TokenJuiceView'))
const SubAgentsView = lazy(() => import('./views/SubAgentsView'))

type View = 'home' | 'human' | 'avatar' | 'chat' | 'memory' | 'tree' | 'dreams' | 'skills' | 'goals' | 'connections' | 'screen' | 'tokenjuice' | 'subagents' | 'rewards' | 'notifs' | 'privacy' | 'ai' | 'audit' | 'settings'

const NAV = [
  { id: 'home',        icon: Home,          label: 'Inicio' },
  { id: 'human',       icon: Mic,           label: 'Voz' },
  { id: 'avatar',      icon: Palette,       label: 'Avatar' },
  { id: 'chat',        icon: MessageSquare, label: 'Chat' },
  { id: 'memory',      icon: Brain,         label: 'Recuerdos' },
  { id: 'tree',        icon: TreePine,      label: 'Resumen' },
  { id: 'dreams',      icon: Moon,          label: 'Ideas' },
  { id: 'skills',      icon: Zap,           label: 'Acciones' },
  { id: 'subagents',   icon: Users,         label: 'Equipo' },
  { id: 'goals',       icon: Target,        label: 'Metas' },
  { id: 'connections', icon: Plug,          label: 'Conectar' },
  { id: 'screen',      icon: Monitor,       label: 'Pantalla' },
  { id: 'tokenjuice',  icon: Filter,        label: 'Contexto' },
  { id: 'rewards',     icon: Gift,          label: 'Recomp.' },
  { id: 'notifs',      icon: Bell,          label: 'Avisos' },
  { id: 'privacy',     icon: Shield,        label: 'Privacidad' },
  { id: 'ai',          icon: Cpu,           label: 'Cerebro' },
  { id: 'audit',       icon: ClipboardList, label: 'Historial' },
  { id: 'settings',    icon: Settings,      label: 'Ajustes' },
] as const

const VIEW_IDS = new Set<View>(NAV.map(item => item.id as View))

function initialView(): View {
  try {
    const requested = new URLSearchParams(window.location.search).get('view') as View | null
    if (requested && VIEW_IDS.has(requested)) return requested
  } catch {}
  return 'home'
}

const EMOTION_EMOJI: Record<string, string> = {
  calm: '😌', energetic: '⚡', focused: '🎯', reflective: '💭', satisfied: '✨',
}

export default function App() {
  const [view, setView] = useState<View>(initialView)
  const [userName, setUserName] = useState('')
  const [agentName, setAgentName] = useState('SEAL')
  const [emotion, setEmotion] = useState('calm')
  const [showWizard, setShowWizard] = useState(false)
  const [bootstrapped, setBootstrapped] = useState(false)

  useEffect(() => {
    // Bootstrap: read config + decide whether first-run wizard is needed.
    fetch(`${API}/api/config`)
      .then(r => r.json())
      .then(d => {
        if (d?.name) setUserName(d.name)
        if (d?.primary_agent) setAgentName(d.primary_agent)
        if (d && d.first_run_complete === false) setShowWizard(true)
      })
      .catch(() => {})
      .finally(() => setBootstrapped(true))

    // Secondary fetch for emotional state (best-effort).
    fetch(`${API}/api/companion/context`)
      .then(r => r.json())
      .then(d => {
        if (d?.user_name) setUserName(d.user_name)
        if (d?.agent?.name) setAgentName(d.agent.name)
        if (d?.agent?.emotional_state) setEmotion(d.agent.emotional_state)
      })
      .catch(() => {})
  }, [])

  // Refresh emotional state after chat updates it
  function refreshEmotion() {
    fetch(`${API}/api/agent/emotional-state`)
      .then(r => r.json())
      .then(d => { if (d.emotional_state) setEmotion(d.emotional_state) })
      .catch(() => {})
  }

  if (!bootstrapped) {
    return <div className="flex items-center justify-center h-screen bg-seal-bg text-slate-500 text-sm">Cargando tu SEAL App…</div>
  }

  return (
    <div className="flex flex-col h-screen bg-seal-bg text-slate-200">
      {showWizard && (
        <FirstRunWizard
          onFinish={(name) => {
            setUserName(name)
            setShowWizard(false)
            setView('home')
          }}
        />
      )}
      {/* Top bar */}
      <header className="flex items-center justify-between px-4 py-2 border-b border-seal-border shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-blue-400 font-bold text-sm tracking-widest">SEAL</span>
          <span className="text-seal-muted text-xs">asistente personal</span>
        </div>
        <div className="flex items-center gap-3">
          {/* Agent name + emotion */}
          <div className="flex items-center gap-1.5 bg-seal-surface border border-seal-border rounded-full px-2.5 py-0.5">
            <span className="text-xs">{EMOTION_EMOJI[emotion] || '✦'}</span>
            <span className="text-xs text-slate-300">{agentName}</span>
            <span className="text-xs text-seal-muted capitalize">{emotion}</span>
          </div>
          {userName && <span className="text-xs text-seal-muted">Hola, {userName}</span>}
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        <Suspense fallback={<div className="flex h-full items-center justify-center bg-seal-bg text-sm text-seal-muted">Cargando vista...</div>}>
          {view === 'home'        && <HomeView agentName={agentName} userName={userName} emotion={emotion} onStartChat={() => setView('chat')} />}
          {view === 'human'       && <HumanView />}
          {view === 'avatar'      && <AvatarView />}
          {view === 'chat'        && <ChatView onMessageSent={refreshEmotion} />}
          {view === 'connections' && <ConnectionsView />}
          {view === 'screen'      && <ScreenView />}
          {view === 'tokenjuice'  && <TokenJuiceView />}
          {view === 'subagents'   && <SubAgentsView />}
          {view === 'rewards'     && <RewardsView />}
          {view === 'memory'      && <MemoryView />}
          {view === 'tree'        && <MemoryTreeView />}
          {view === 'dreams'      && <DreamsView />}
          {view === 'skills'      && <SkillsView onUseSkill={(prompt) => { setView('chat'); window.dispatchEvent(new CustomEvent('inject-prompt', { detail: prompt })) }} />}
          {view === 'goals'       && <GoalsView />}
          {view === 'notifs'      && <NotificationsView />}
          {view === 'privacy'     && <PrivacyView />}
          {view === 'ai'          && <AIBackendView />}
          {view === 'audit'       && <AuditLogView />}
          {view === 'settings'    && <SettingsView onSaved={(n) => setUserName(n)} onAgentSaved={(n) => setAgentName(n)} />}
        </Suspense>
      </main>

      {/* Bottom nav */}
      <nav className="flex border-t border-seal-border shrink-0 overflow-x-auto">
        {NAV.map(({ id, icon: Icon, label }) => (
          <button
            key={id}
            onClick={() => setView(id as View)}
            className={`min-w-[72px] sm:min-w-0 sm:flex-1 shrink-0 flex flex-col items-center gap-1 py-2 text-[11px] sm:text-xs transition-colors ${
              view === id ? 'text-blue-400' : 'text-seal-muted hover:text-slate-300'
            }`}
          >
            <Icon size={18} />
            <span className="whitespace-nowrap">{label}</span>
          </button>
        ))}
      </nav>
    </div>
  )
}
