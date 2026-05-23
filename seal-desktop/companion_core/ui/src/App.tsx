import { useState, useEffect } from 'react'
import ChatView from './views/ChatView'
import MemoryView from './views/MemoryView'
import SkillsView from './views/SkillsView'
import GoalsView from './views/GoalsView'
import SettingsView from './views/SettingsView'
import DreamsView from './views/DreamsView'
import PrivacyView from './views/PrivacyView'
import NotificationsView from './views/NotificationsView'
import AIBackendView from './views/AIBackendView'
import AuditLogView from './views/AuditLogView'
import HumanView from './views/HumanView'
import ConnectionsView from './views/ConnectionsView'
import MemoryTreeView from './views/MemoryTreeView'
import RewardsView from './views/RewardsView'
import { HomeView } from './components/HomeView'
import { FirstRunWizard } from './components/FirstRunWizard'
import { Home, MessageSquare, Brain, Zap, Target, Settings, Moon, Shield, Bell, Cpu, ClipboardList, Mic, Plug, TreePine, Gift } from 'lucide-react'

export const API = 'http://localhost:8769'

type View = 'home' | 'human' | 'chat' | 'memory' | 'tree' | 'dreams' | 'skills' | 'goals' | 'connections' | 'rewards' | 'notifs' | 'privacy' | 'ai' | 'audit' | 'settings'

const NAV = [
  { id: 'home',        icon: Home,          label: 'Home' },
  { id: 'human',       icon: Mic,           label: 'Voz' },
  { id: 'chat',        icon: MessageSquare, label: 'Chat' },
  { id: 'memory',      icon: Brain,         label: 'Memory' },
  { id: 'tree',        icon: TreePine,      label: 'Árbol' },
  { id: 'dreams',      icon: Moon,          label: 'Dreams' },
  { id: 'skills',      icon: Zap,           label: 'Skills' },
  { id: 'goals',       icon: Target,        label: 'Goals' },
  { id: 'connections', icon: Plug,          label: 'Conn' },
  { id: 'rewards',     icon: Gift,          label: 'Recomp.' },
  { id: 'notifs',      icon: Bell,          label: 'Alerts' },
  { id: 'privacy',     icon: Shield,        label: 'Privacidad' },
  { id: 'ai',          icon: Cpu,           label: 'AI' },
  { id: 'audit',       icon: ClipboardList, label: 'Actividad' },
  { id: 'settings',    icon: Settings,      label: 'Config' },
] as const

const EMOTION_EMOJI: Record<string, string> = {
  calm: '😌', energetic: '⚡', focused: '🎯', reflective: '💭', satisfied: '✨',
}

export default function App() {
  const [view, setView] = useState<View>('home')
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
          <span className="text-seal-muted text-xs">companion</span>
        </div>
        <div className="flex items-center gap-3">
          {/* Agent name + emotion */}
          <div className="flex items-center gap-1.5 bg-seal-surface border border-seal-border rounded-full px-2.5 py-0.5">
            <span className="text-xs">{EMOTION_EMOJI[emotion] || '✦'}</span>
            <span className="text-xs text-slate-300">{agentName}</span>
            <span className="text-xs text-seal-muted capitalize">{emotion}</span>
          </div>
          {userName && <span className="text-xs text-seal-muted">Hi, {userName}</span>}
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        {view === 'home'        && <HomeView agentName={agentName} userName={userName} emotion={emotion} onStartChat={() => setView('chat')} />}
        {view === 'human'       && <HumanView />}
        {view === 'chat'        && <ChatView onMessageSent={refreshEmotion} />}
        {view === 'connections' && <ConnectionsView />}
        {view === 'rewards'     && <RewardsView />}
        {view === 'memory'      && <MemoryView />}
        {view === 'tree'        && <MemoryTreeView />}
        {view === 'dreams'   && <DreamsView />}
        {view === 'skills'   && <SkillsView onUseSkill={(prompt) => { setView('chat'); window.dispatchEvent(new CustomEvent('inject-prompt', { detail: prompt })) }} />}
        {view === 'goals'    && <GoalsView />}
        {view === 'notifs'   && <NotificationsView />}
        {view === 'privacy'  && <PrivacyView />}
        {view === 'ai'       && <AIBackendView />}
        {view === 'audit'    && <AuditLogView />}
        {view === 'settings' && <SettingsView onSaved={(n) => setUserName(n)} onAgentSaved={(n) => setAgentName(n)} />}
      </main>

      {/* Bottom nav */}
      <nav className="flex border-t border-seal-border shrink-0">
        {NAV.map(({ id, icon: Icon, label }) => (
          <button
            key={id}
            onClick={() => setView(id as View)}
            className={`flex-1 flex flex-col items-center gap-1 py-2 text-xs transition-colors ${
              view === id ? 'text-blue-400' : 'text-seal-muted hover:text-slate-300'
            }`}
          >
            <Icon size={18} />
            {label}
          </button>
        ))}
      </nav>
    </div>
  )
}
