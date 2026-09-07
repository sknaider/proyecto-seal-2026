import { useState, useEffect } from "react";
import Snapshot from "./components/sections/Snapshot";
import OceanSection from "./components/sections/OceanSection";
import MemoriesSection from "./components/sections/MemoriesSection";
import ThoughtsSection from "./components/sections/ThoughtsSection";
import DiarySection from "./components/sections/DiarySection";
import EmotionsSection from "./components/sections/EmotionsSection";
import OpinionsSection from "./components/sections/OpinionsSection";
import BeliefsSection from "./components/sections/BeliefsSection";
import InstinctsSection from "./components/sections/InstinctsSection";
import GoalsSection from "./components/sections/GoalsSection";
import EventsSection from "./components/sections/EventsSection";
import RelationshipsSection from "./components/sections/RelationshipsSection";
import WorkingStateSection from "./components/sections/WorkingStateSection";
import CognitiveLifecycleSection from "./components/sections/CognitiveLifecycleSection";
import EvidenceDashboardSection from "./components/sections/EvidenceDashboardSection";
import AwarenessDashboardSection from "./components/sections/AwarenessDashboardSection";
import NexusReviewQueueSection from "./components/sections/NexusReviewQueueSection";
import AutonomyDashboardSection from "./components/sections/AutonomyDashboardSection";
import WilliamReviewSection from "./components/sections/WilliamReviewSection";
import ContextMeterSection from "./components/sections/ContextMeterSection";
import NervesSection from "./components/sections/NervesSection";
import AgentControlsSection from "./components/sections/AgentControlsSection";
import CostSection from "./components/sections/CostSection";

const AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS", "DUM"];

const OTHER_PANELS: { url: string; icon: string; label: string }[] = [
  { url: "http://100.75.201.110:8773/", icon: "🛟", label: "Agent Control" },
  { url: "http://100.75.201.110:5173/", icon: "🖥️", label: "Soul App 2" },
  { url: "http://100.75.201.110:5174/", icon: "👤", label: "Soul App" },
  { url: "http://100.75.201.110:3001/", icon: "🎨", label: "SEAL Studio" },
  { url: "http://100.75.201.110:8765/", icon: "💬", label: "SEAL Chat" },
  { url: "http://100.75.201.110:8768/", icon: "🌙", label: "v3 Studio" },
];

// NAV ordenado: identidad → estado interno → operaciones
const NAV: { id: string; icon: string; label: string; group?: string }[] = [
  // Estado general
  { id: "snapshot",      icon: "⚡", label: "Snapshot"       },
  { id: "context_meter", icon: "📐", label: "Context Meter"  },
  { id: "nerves",        icon: "🔥", label: "NERVES"         },
  // Identidad / personalidad
  { id: "ocean",         icon: "🧠", label: "OCEAN"          },
  { id: "emotions",      icon: "💜", label: "Emociones"      },
  // Memoria y subconsciente
  { id: "memories",      icon: "🗃️",  label: "Memorias"       },
  { id: "thoughts",      icon: "💭", label: "Pensamientos"   },
  { id: "diary",         icon: "📓", label: "Diario"         },
  { id: "opinions",      icon: "💬", label: "Opiniones"      },
  { id: "beliefs",       icon: "🔮", label: "Creencias"      },
  // Motivación y conducta
  { id: "instincts",     icon: "⚡", label: "Instintos"      },
  { id: "goals",         icon: "🎯", label: "Objetivos"      },
  { id: "relationships", icon: "🤝", label: "Relaciones"     },
  // Operaciones
  { id: "working_state", icon: "🔧", label: "Estado Activo"  },
  { id: "agent_controls", icon: "🎮", label: "Controls"       },
  { id: "cost",          icon: "💰", label: "Cost"           },
  // Evidencia y eventos
  { id: "evidence",      icon: "▣", label: "Evidence"       },
  { id: "nexus_review",  icon: "⚖", label: "NEXUS Review"   },
  { id: "autonomy",      icon: "◇", label: "Autonomy"       },
  { id: "william_review", icon: "◈", label: "William Review" },
  { id: "awareness",     icon: "◉", label: "Awareness"      },
  { id: "cognitive_lifecycle", icon: "📡", label: "Lifecycle" },
  { id: "events",        icon: "📋", label: "Eventos"        },
];

const validAgent = (value: string | null) =>
  value && AGENTS.includes(value.toUpperCase()) ? value.toUpperCase() : "ADA";

const validSection = (value: string | null) =>
  value && NAV.some((item) => item.id === value) ? value : "snapshot";

export default function App() {
  const initialParams = new URLSearchParams(window.location.search);
  const [agent, setAgent] = useState(() => validAgent(initialParams.get("agent")));
  const [section, setSection] = useState(() => validSection(initialParams.get("view")));
  const [agentStatus, setAgentStatus] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const r = await fetch("/api/soul/agents");
        if (r.ok) {
          const data = await r.json();
          const map: Record<string, boolean> = {};
          for (const a of data) map[a.name] = a.active;
          setAgentStatus(map);
        }
      } catch {}
    };
    fetchStatus();
    const t = setInterval(fetchStatus, 30000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    params.set("agent", agent);
    params.set("view", section);
    const next = `${window.location.pathname}?${params.toString()}`;
    if (`${window.location.pathname}${window.location.search}` !== next) {
      window.history.replaceState(null, "", next);
    }
  }, [agent, section]);

  const renderSection = () => {
    const props = { agent };
    switch (section) {
      case "snapshot":      return <Snapshot {...props} />;
      case "ocean":         return <OceanSection {...props} />;
      case "emotions":      return <EmotionsSection {...props} />;
      case "memories":      return <MemoriesSection {...props} />;
      case "thoughts":      return <ThoughtsSection {...props} />;
      case "diary":         return <DiarySection {...props} />;
      case "opinions":      return <OpinionsSection {...props} />;
      case "beliefs":       return <BeliefsSection {...props} />;
      case "instincts":     return <InstinctsSection {...props} />;
      case "goals":         return <GoalsSection {...props} />;
      case "relationships": return <RelationshipsSection {...props} />;
      case "working_state": return <WorkingStateSection {...props} />;
      case "context_meter": return <ContextMeterSection {...props} />;
      case "nerves":        return <NervesSection {...props} />;
      case "agent_controls": return <AgentControlsSection {...props} />;
      case "cost":          return <CostSection {...props} />;
      case "evidence":      return <EvidenceDashboardSection {...props} />;
      case "nexus_review":  return <NexusReviewQueueSection {...props} />;
      case "autonomy":      return <AutonomyDashboardSection {...props} />;
      case "william_review": return <WilliamReviewSection {...props} />;
      case "awareness":     return <AwarenessDashboardSection {...props} />;
      case "cognitive_lifecycle": return <CognitiveLifecycleSection {...props} />;
      case "events":        return <EventsSection {...props} />;
      default:              return <Snapshot {...props} />;
    }
  };

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <header
        className="flex items-center justify-between h-10 px-4 border-b flex-shrink-0"
        style={{ borderColor: "var(--seal-border)", background: "var(--seal-surface)" }}
      >
        <div className="flex items-center gap-2">
          <span className="font-bold text-sm tracking-wider" style={{ color: "var(--soul-purple)" }}>
            SOUL DASHBOARD
          </span>
          <span className="text-xs" style={{ color: "var(--seal-text-dim)" }}>v0.1</span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {OTHER_PANELS.map((p) => (
            <a
              key={p.url}
              href={p.url}
              target="_blank"
              rel="noreferrer"
              className="text-[10px] px-2 py-0.5 rounded border transition-colors hover:bg-[var(--soul-purple)] hover:text-white"
              style={{
                borderColor: "var(--seal-border)",
                background: "var(--seal-bg)",
                color: "var(--seal-text-dim)",
                textDecoration: "none",
                whiteSpace: "nowrap",
              }}
              title={p.url}
            >
              <span style={{ marginRight: "3px" }}>{p.icon}</span>{p.label}
            </a>
          ))}
          <span className="text-[10px] ml-2" style={{ color: "var(--seal-text-dim)" }}>·</span>
          {AGENTS.map((a) => (
            <div key={a} className="flex items-center gap-1">
              <div
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: agentStatus[a] !== false ? "var(--seal-success)" : "var(--seal-error)" }}
              />
              <span className="text-xs" style={{ color: "var(--seal-text-dim)" }}>{a}</span>
            </div>
          ))}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside
          className="w-48 flex-shrink-0 flex flex-col border-r overflow-y-auto"
          style={{ borderColor: "var(--seal-border)", background: "var(--seal-surface)" }}
        >
          {/* Agent selector */}
          <div className="p-3 border-b" style={{ borderColor: "var(--seal-border)" }}>
            <p className="section-header" style={{ padding: "0 0 6px" }}>Agente</p>
            <div className="flex flex-wrap gap-1">
              {AGENTS.map((a) => (
                <button
                  key={a}
                  onClick={() => setAgent(a)}
                  className="text-[11px] px-2 py-0.5 rounded font-medium transition-colors"
                  style={{
                    background: agent === a ? "var(--soul-purple)" : "var(--seal-bg)",
                    color: agent === a ? "white" : "var(--seal-text-dim)",
                  }}
                >
                  {a}
                </button>
              ))}
            </div>
          </div>

          {/* Navigation */}
          <nav className="flex-1 py-2">
            {NAV.map((item) => (
              <button
                key={item.id}
                onClick={() => setSection(item.id)}
                className={`nav-btn ${section === item.id ? "active" : ""}`}
              >
                <span className="icon">{item.icon}</span>
                <span>{item.label}</span>
              </button>
            ))}
          </nav>
        </aside>

        {/* Main */}
        <main className="flex-1 overflow-auto p-5">
          {renderSection()}
        </main>
      </div>

      {/* Footer */}
      <footer
        className="flex items-center justify-between h-6 px-4 border-t flex-shrink-0 text-xs"
        style={{
          borderColor: "var(--seal-border)",
          background: "var(--seal-surface)",
          color: "var(--seal-text-dim)",
        }}
      >
        <span>SOUL Dashboard — agente activo: <strong style={{ color: "var(--soul-purple)" }}>{agent}</strong></span>
        <span>{new Date().toLocaleTimeString("es-PE", { timeZone: "America/Lima", hour: "2-digit", minute: "2-digit" })}</span>
      </footer>
    </div>
  );
}
