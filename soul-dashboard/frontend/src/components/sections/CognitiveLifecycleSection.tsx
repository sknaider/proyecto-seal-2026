import { useState, useEffect } from "react";

type LifecycleState = {
  agent: string;
  state: string;
  runtime: string;
  budget_class: string;
  source_event_id: number | null;
  router_action: string | null;
  confidence: number;
  reason: string;
  since: string | null;
  expires_at: string | null;
  updated_at: string | null;
  feature_flag: string;
};

type LifecycleEvent = {
  agent: string;
  previous_state: string | null;
  new_state: string;
  runtime: string;
  budget_class: string;
  source_event_id: number | null;
  router_action: string | null;
  confidence: number;
  reason: string;
  feature_flag: string;
  created_at: string | null;
};

type LifecycleResponse = {
  mode: string;
  enforcement: boolean;
  legacy_agent_lifecycle_touched: boolean;
  states: LifecycleState[];
  events: LifecycleEvent[];
};

const STATE_COLORS: Record<string, string> = {
  standby: "var(--seal-text-dim)",
  active: "var(--seal-success)",
  deep_work: "var(--seal-accent)",
  emergency: "var(--seal-error)",
};

function fmtDate(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString("es-PE", {
    timeZone: "America/Lima",
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
  });
}

export default function CognitiveLifecycleSection({ agent }: { agent: string }) {
  const [data, setData] = useState<LifecycleResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/soul/cognitive_lifecycle?events_limit=12`)
      .then((r) => r.json())
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [agent]);

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando lifecycle...</p>;
  if (!data) return <p className="text-sm" style={{ color: "var(--seal-error)" }}>No se pudo cargar lifecycle.</p>;

  const selected = data.states.find((s) => s.agent === agent);

  return (
    <div className="max-w-5xl space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Lifecycle Cognitivo</h2>
        <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-warning)" }}>
          {data.mode}
        </span>
        <span className="pill" style={{ background: "var(--seal-bg)", color: data.enforcement ? "var(--seal-error)" : "var(--seal-success)" }}>
          enforcement {data.enforcement ? "on" : "off"}
        </span>
      </div>

      {selected && (
        <div className="card">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>Agente seleccionado</p>
              <p className="text-lg font-semibold" style={{ color: STATE_COLORS[selected.state] || "var(--seal-text)" }}>
                {selected.agent} · {selected.state}
              </p>
            </div>
            <div className="flex gap-2">
              <span className="pill" style={{ background: "var(--seal-bg)", color: selected.runtime === "codex" ? "var(--soul-purple)" : "var(--seal-accent)" }}>
                {selected.runtime}
              </span>
              <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-text-dim)" }}>
                {selected.budget_class}
              </span>
            </div>
          </div>
          <p className="text-xs mt-3" style={{ color: "var(--seal-text-dim)" }}>
            Evento {selected.source_event_id ?? "—"} · {selected.router_action ?? "—"} · confianza {Math.round(selected.confidence * 100)}%
          </p>
          <p className="text-xs mt-2" style={{ color: "var(--seal-text)" }}>{selected.reason}</p>
          <p className="text-[10px] mt-3" style={{ color: "var(--seal-text-dim)" }}>
            expira {fmtDate(selected.expires_at)} · flag {selected.feature_flag}
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
        {data.states.map((s) => (
          <div key={s.agent} className="card">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>{s.agent}</p>
              <span className="w-2 h-2 rounded-full" style={{ background: STATE_COLORS[s.state] || "var(--seal-text-dim)" }} />
            </div>
            <p className="text-xs mt-2" style={{ color: STATE_COLORS[s.state] || "var(--seal-text)" }}>{s.state}</p>
            <p className="text-[10px] mt-1" style={{ color: "var(--seal-text-dim)" }}>{s.runtime} · {s.budget_class}</p>
            <div className="confidence-bar mt-2">
              <div
                className="confidence-bar-fill"
                style={{ width: `${Math.round(s.confidence * 100)}%`, background: STATE_COLORS[s.state] || "var(--soul-purple)" }}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <p className="text-xs font-semibold mb-3" style={{ color: "var(--seal-text-dim)" }}>Eventos recientes</p>
        <div className="space-y-2">
          {data.events.map((e, i) => (
            <div key={`${e.agent}-${e.source_event_id}-${i}`} className="flex items-start justify-between gap-3 text-xs">
              <div>
                <span style={{ color: "var(--seal-text)" }}>{e.agent}</span>
                <span style={{ color: "var(--seal-text-dim)" }}> · {e.previous_state ?? "none"} → </span>
                <span style={{ color: STATE_COLORS[e.new_state] || "var(--seal-text)" }}>{e.new_state}</span>
                <p className="mt-0.5" style={{ color: "var(--seal-text-dim)" }}>{e.router_action ?? "—"} · evento {e.source_event_id ?? "—"}</p>
              </div>
              <span style={{ color: "var(--seal-text-dim)" }}>{fmtDate(e.created_at)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
