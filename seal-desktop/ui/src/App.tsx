import React, { Suspense, lazy, useState, useEffect, useRef } from 'react'
import { AppShell } from './components/layout/AppShell'
import { FirstRunWizard } from './components/wizard/FirstRunWizard'
import { CommandPalette } from './components/palette/CommandPalette'
import { ShortcutsModal } from './components/shortcuts/ShortcutsModal'
import { useCompanionConfig } from './contexts/CompanionConfigContext'
import { ErrorBoundary } from './components/layout/ErrorBoundary'
import type { ViewId } from './lib/types'

const ChatView = lazy(() => import('./components/chat/ChatView').then(m => ({ default: m.ChatView })))
const AgentsView = lazy(() => import('./components/agents/AgentsView').then(m => ({ default: m.AgentsView })))
const MemoryView = lazy(() => import('./components/memory/MemoryView').then(m => ({ default: m.MemoryView })))
const GoalsView = lazy(() => import('./components/goals/GoalsView').then(m => ({ default: m.GoalsView })))
const SkillsView = lazy(() => import('./components/skills/SkillsView').then(m => ({ default: m.SkillsView })))
const NervesView = lazy(() => import('./components/nerves/NervesView').then(m => ({ default: m.NervesView })))
const GovernanceView = lazy(() => import('./components/governance/GovernanceView').then(m => ({ default: m.GovernanceView })))
const SubconsciousView = lazy(() => import('./components/subconscious/SubconsciousView').then(m => ({ default: m.SubconsciousView })))
const ProfileView = lazy(() => import('./components/profile/ProfileView').then(m => ({ default: m.ProfileView })))
const SettingsView = lazy(() => import('./components/settings/SettingsView').then(m => ({ default: m.SettingsView })))
const IntegrationsView = lazy(() => import('./components/integrations/IntegrationsView').then(m => ({ default: m.IntegrationsView })))
const ScreenView = lazy(() => import('./components/screen/ScreenView').then(m => ({ default: m.ScreenView })))
const BenchView = lazy(() => import('./components/bench/BenchView').then(m => ({ default: m.BenchView })))
const CodeGraphView = lazy(() => import('./components/codegraph/CodeGraphView').then(m => ({ default: m.CodeGraphView })))
const ActivityFeedView = lazy(() => import('./components/activity/ActivityFeedView').then(m => ({ default: m.ActivityFeedView })))
const PulseView = lazy(() => import('./components/pulse/PulseView').then(m => ({ default: m.PulseView })))
const SearchView = lazy(() => import('./components/search/SearchView').then(m => ({ default: m.SearchView })))
const ReplayView = lazy(() => import('./components/replay/ReplayView').then(m => ({ default: m.ReplayView })))
const SpecCompilerView = lazy(() => import('./components/speccompiler/SpecCompilerView').then(m => ({ default: m.SpecCompilerView })))
const TopologyView = lazy(() => import('./components/topology/TopologyView').then(m => ({ default: m.TopologyView })))
const EvalView = lazy(() => import('./components/eval/EvalView').then(m => ({ default: m.EvalView })))
const AwarenessView = lazy(() => import('./components/awareness/AwarenessView').then(m => ({ default: m.AwarenessView })))
const NexusReviewView = lazy(() => import('./components/nexus/NexusReviewView').then(m => ({ default: m.NexusReviewView })))
const PrivacyView = lazy(() => import('./components/privacy/PrivacyView').then(m => ({ default: m.PrivacyView })))
const AuditLogView = lazy(() => import('./components/privacy/AuditLogView').then(m => ({ default: m.AuditLogView })))

const CHORD_G_HINTS = [
  { key: 'c', label: 'chat' },
  { key: 'm', label: 'memory' },
  { key: 'a', label: 'agents' },
  { key: 'n', label: 'nerves' },
  { key: 'p', label: 'pulse' },
  { key: 's', label: 'search' },
  { key: 'w', label: 'awareness' },
  { key: 'x', label: 'nexus' },
]

function ViewLoading() {
  return (
    <div className="flex h-full items-center justify-center bg-[#050505]">
      <div className="h-2 w-2 rounded-full bg-[#7c3aed] opacity-70 animate-pulse" />
    </div>
  )
}

export default function App() {
  const [view, setView] = useState<ViewId>(() => {
    try {
      const requested = new URLSearchParams(window.location.search).get('view') as ViewId | null
      return requested ?? (localStorage.getItem('seal_last_view') as ViewId) ?? 'chat'
    } catch { return 'chat' }
  })
  const [wizardDone, setWizardDone] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [chord, setChord] = useState('')
  const config = useCompanionConfig()
  const pendingKeyRef = useRef<string>('')
  const pendingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const showWizard = config.mode === 'user-product' && !config.first_run_complete && !wizardDone

  useEffect(() => {
    try { localStorage.setItem('seal_last_view', view) } catch {}
  }, [view])

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName
      const editable = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable
      if (editable) return

      // ⌘K — command palette
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setPaletteOpen(p => !p)
        return
      }
      // Ctrl+/ — jump to search
      if ((e.ctrlKey || e.metaKey) && e.key === '/') {
        e.preventDefault()
        setView('search')
        return
      }
      // ? — shortcuts modal
      if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
        setShortcutsOpen(o => !o)
        return
      }

      const k = e.key.toLowerCase()

      // Two-key sequences: g+nav, n+capture
      if (pendingKeyRef.current === 'g') {
        pendingKeyRef.current = ''
        setChord('')
        if (pendingTimerRef.current) clearTimeout(pendingTimerRef.current)
        const navMap: Record<string, ViewId> = {
          c: 'chat', m: 'memory', a: 'agents', n: 'nerves', p: 'pulse', s: 'search', w: 'awareness', x: 'nexus_review',
        }
        if (navMap[k]) { setView(navMap[k]); return }
      }

      if (k === 'q') {
        window.dispatchEvent(new CustomEvent('seal:quickcapture'))
        return
      }

      if (k === 'g' || k === 'n') {
        pendingKeyRef.current = k
        setChord(k)
        if (pendingTimerRef.current) clearTimeout(pendingTimerRef.current)
        pendingTimerRef.current = setTimeout(() => { pendingKeyRef.current = ''; setChord('') }, 800)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  function renderView() {
    const wrap = (v: ViewId, el: React.ReactNode) => (
      <ErrorBoundary key={v} view={v}>
        <Suspense fallback={<ViewLoading />}>{el}</Suspense>
      </ErrorBoundary>
    )
    switch (view) {
      case 'chat':        return wrap(view, <ChatView onNav={setView} />)
      case 'agents':      return wrap(view, <AgentsView />)
      case 'memory':      return wrap(view, <MemoryView />)
      case 'goals':       return wrap(view, <GoalsView />)
      case 'skills':      return wrap(view, <SkillsView />)
      case 'nerves':      return wrap(view, <NervesView />)
      case 'governance':    return wrap(view, config.show_governance ? <GovernanceView /> : null)
      case 'subconscious':  return wrap(view, <SubconsciousView />)
      case 'profile':       return wrap(view, <ProfileView />)
      case 'settings':      return wrap(view, <SettingsView />)
      case 'integrations':  return wrap(view, <IntegrationsView />)
      case 'screen':        return wrap(view, <ScreenView />)
      case 'bench':         return wrap(view, <BenchView />)
      case 'codegraph':     return wrap(view, <CodeGraphView />)
      case 'activity':      return wrap(view, <ActivityFeedView />)
      case 'pulse':         return wrap(view, <PulseView />)
      case 'search':        return wrap(view, <SearchView />)
      case 'replay':        return wrap(view, <ReplayView />)
      case 'speccompiler':  return wrap(view, <SpecCompilerView />)
      case 'topology':      return wrap(view, <TopologyView />)
      case 'eval':          return wrap(view, <EvalView />)
      case 'awareness':     return wrap(view, <AwarenessView />)
      case 'nexus_review':  return wrap(view, <NexusReviewView />)
      case 'privacy':       return wrap(view, <PrivacyView />)
      case 'audit_log':     return wrap(view, <AuditLogView />)
    }
  }

  return (
    <>
      {showWizard && <FirstRunWizard onComplete={() => setWizardDone(true)} />}
      <ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
      {chord === 'g' && (
        <div className="fixed bottom-[76px] left-4 z-50 bg-[#0a0a0a] border border-[#1a1a1a] rounded-lg px-3 py-2 shadow-2xl pointer-events-none flex items-center gap-0.5">
          <span className="text-[9px] font-mono text-[#7c3aed] font-bold mr-2">G+</span>
          {CHORD_G_HINTS.map(({ key, label }) => (
            <span key={key} className="inline-flex items-center gap-1 mr-2">
              <kbd className="px-1 py-0.5 bg-[#1a1a1a] border border-[#333] rounded text-[8px] text-[#aaa] font-mono">{key}</kbd>
              <span className="text-[9px] text-[#444]">{label}</span>
            </span>
          ))}
        </div>
      )}
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onNavigate={v => setView(v as ViewId)}
      />
      <AppShell activeView={view} onNav={setView}>
        {renderView()}
      </AppShell>
    </>
  )
}
