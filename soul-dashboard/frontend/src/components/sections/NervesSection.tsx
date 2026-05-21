import { useEffect, useState } from "react";

type Drive = {
  tank: string;
  pressure: number;
  threshold: number;
  fired: boolean;
  ocean_param: string | null;
};

type AgentNerves = {
  agent: string;
  drives: Drive[];
};

type NervesData = {
  agents: AgentNerves[];
  error?: string;
};

const TANK_LABELS: Record<string, string> = {
  alert_drive: "Alert",
  boredom: "Boredom",
  context_pressure: "Context Pressure",
  curiosity: "Curiosity",
  energy_drive: "Energy",
  learning_drive: "Learning",
  social_drive: "Social",
  task_drive: "Task",
  vigilance: "Vigilance",
};

const TANK_COLORS: Record<string, string> = {
  alert_drive: "#f87171",
  boredom: "#94a3b8",
  context_pressure: "#fb923c",
  curiosity: "#a78bfa",
  energy_drive: "#fbbf24",
  learning_drive: "#7dd3fc",
  social_drive: "#6ee7b7",
  task_drive: "#c084fc",
  vigilance: "#f472b6",
};

export default function NervesSection(_: { agent: string }) {
  const [data, setData] = useState<NervesData | null>(null);

  useEffect(() => {
    const load = () => {
      fetch("/api/soul/nerves")
        .then((r) => r.json())
        .then((d) => setData(d))
        .catch(() => setData({ agents: [], error: "fetch failed" }));
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  if (!data) {
    return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando NERVES…</p>;
  }
  if (data.error) {
    return <p className="text-sm" style={{ color: "var(--seal-error)" }}>Error: {data.error}</p>;
  }
  if (!data.agents || data.agents.length === 0) {
    return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin datos de NERVES</p>;
  }

  return (
    <div className="max-w-6xl space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>NERVES — drives por agente</h2>
        <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>auto-refresh 15s</span>
      </div>
      <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
        Presión interna de cada tanque vs su threshold. Cuando un tanque supera su threshold, se enciende (fired) y dispara conducta.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {data.agents.map((ag) => (
          <div key={ag.agent} className="card">
            <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--soul-purple)" }}>{ag.agent}</h3>
            <div className="space-y-2">
              {ag.drives.map((d) => {
                const pct = (d.pressure / Math.max(d.threshold, 1)) * 100;
                const color = TANK_COLORS[d.tank] || "var(--seal-text-dim)";
                return (
                  <div key={d.tank}>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span style={{ color: d.fired ? "var(--seal-error)" : "var(--seal-text)" }}>
                        {d.fired && "🔥 "}
                        {TANK_LABELS[d.tank] || d.tank}
                        {d.ocean_param && (
                          <span style={{ color: "var(--seal-text-dim)", fontSize: "9px", marginLeft: "4px" }}>
                            · {d.ocean_param}
                          </span>
                        )}
                      </span>
                      <span style={{ color: "var(--seal-text-dim)", fontVariantNumeric: "tabular-nums" }}>
                        {d.pressure.toFixed(1)} / {d.threshold.toFixed(0)}
                      </span>
                    </div>
                    <div style={{ width: "100%", height: "4px", background: "var(--seal-bg)", borderRadius: "2px", overflow: "hidden" }}>
                      <div style={{
                        width: `${Math.min(100, pct)}%`,
                        height: "100%",
                        background: color,
                        opacity: d.fired ? 1 : 0.7,
                      }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
