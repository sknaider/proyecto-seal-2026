import { useState, useEffect } from "react";

type SnapshotData = {
  agent: string;
  ocean: Record<string, number>;
  agent_state: string | null;
  emotional_state: string | null;
  last_intention: string | null;
  task: { name: string; step: number; total: number } | null;
  last_thought: { text: string; emotion: string; at: string } | null;
  last_diary: { entry: string; mood: string; date: string } | null;
  memory_count: number;
  motivation: { tank: string; value: number; last_fired: string | null }[];
  updated_at: string | null;
};

const OCEAN_COLORS: Record<string, string> = {
  O: "#8b5cf6", C: "#3b82f6", E: "#f59e0b", A: "#22c55e", N: "#ef4444",
};
const OCEAN_LABELS: Record<string, string> = {
  O: "Apertura", C: "Responsabilidad", E: "Extroversión", A: "Amabilidad", N: "Neuroticismo",
};

export default function Snapshot({ agent }: { agent: string }) {
  const [data, setData] = useState<SnapshotData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/soul/snapshot?agent=${agent}`)
      .then((r) => r.json())
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [agent]);

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando snapshot...</p>;
  if (!data) return <p className="text-sm" style={{ color: "var(--seal-error)" }}>Error al cargar datos.</p>;

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-lg font-bold" style={{ color: "var(--soul-purple)" }}>{data.agent}</h1>
        {data.agent_state && (
          <span className="pill" style={{ background: "#1e1e35", color: "var(--soul-purple)" }}>
            {data.agent_state}
          </span>
        )}
        {data.emotional_state && (
          <span className="pill" style={{ background: "#1c1c2a", color: "var(--seal-text-dim)" }}>
            {data.emotional_state}
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* OCEAN mini */}
        <div className="card">
          <p className="text-xs font-semibold mb-3" style={{ color: "var(--seal-text-dim)" }}>OCEAN</p>
          {["O", "C", "E", "A", "N"].map((dim) => {
            const v = data.ocean?.[dim] ?? 0;
            const pct = Math.round(v * 100);
            return (
              <div key={dim} className="mb-2">
                <div className="flex justify-between text-[10px] mb-0.5">
                  <span style={{ color: "var(--seal-text-dim)" }}>{dim} — {OCEAN_LABELS[dim]}</span>
                  <span style={{ color: "var(--seal-text)" }}>{pct}%</span>
                </div>
                <div className="confidence-bar">
                  <div
                    className="confidence-bar-fill"
                    style={{ width: `${pct}%`, background: OCEAN_COLORS[dim] }}
                  />
                </div>
              </div>
            );
          })}
        </div>

        {/* Stats */}
        <div className="card">
          <p className="text-xs font-semibold mb-3" style={{ color: "var(--seal-text-dim)" }}>Estado general</p>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span style={{ color: "var(--seal-text-dim)" }}>Memorias activas</span>
              <span className="font-bold" style={{ color: "var(--soul-purple)" }}>{data.memory_count.toLocaleString()}</span>
            </div>
            {data.task && (
              <div className="flex justify-between">
                <span style={{ color: "var(--seal-text-dim)" }}>Tarea activa</span>
                <span className="text-xs truncate max-w-36" style={{ color: "var(--seal-text)" }}>{data.task.name || "—"}</span>
              </div>
            )}
            {data.task && data.task.total > 0 && (
              <div>
                <div className="flex justify-between text-[10px] mb-0.5">
                  <span style={{ color: "var(--seal-text-dim)" }}>Progreso</span>
                  <span style={{ color: "var(--seal-text)" }}>{data.task.step}/{data.task.total}</span>
                </div>
                <div className="confidence-bar">
                  <div
                    className="confidence-bar-fill"
                    style={{ width: `${(data.task.step / data.task.total) * 100}%`, background: "var(--soul-purple)" }}
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Last thought */}
        {data.last_thought && (
          <div className="card">
            <p className="text-xs font-semibold mb-2" style={{ color: "var(--seal-text-dim)" }}>Último pensamiento</p>
            <p className="text-sm leading-relaxed" style={{ color: "var(--seal-text)" }}>{data.last_thought.text}</p>
            <div className="flex items-center gap-2 mt-2">
              {data.last_thought.emotion && (
                <span className="text-[10px] px-2 py-0.5 rounded" style={{ background: "var(--seal-bg)", color: "var(--soul-purple)" }}>
                  {data.last_thought.emotion}
                </span>
              )}
              <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
                {data.last_thought.at ? new Date(data.last_thought.at).toLocaleString("es-PE", { timeZone: "America/Lima" }) : ""}
              </span>
            </div>
          </div>
        )}

        {/* Last diary */}
        {data.last_diary?.entry && (
          <div className="card">
            <p className="text-xs font-semibold mb-2" style={{ color: "var(--seal-text-dim)" }}>
              Diario — {data.last_diary.date}
              {data.last_diary.mood && <span className="ml-2 text-[10px] opacity-70">{data.last_diary.mood}</span>}
            </p>
            <p className="text-sm leading-relaxed" style={{ color: "var(--seal-text)" }}>
              {data.last_diary.entry}
            </p>
          </div>
        )}
      </div>

      {/* Motivation tanks */}
      {data.motivation.length > 0 && (
        <div className="card">
          <p className="text-xs font-semibold mb-3" style={{ color: "var(--seal-text-dim)" }}>Tanques de motivación</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {data.motivation.map((m) => (
              <div key={m.tank}>
                <div className="flex justify-between text-[10px] mb-0.5">
                  <span style={{ color: "var(--seal-text-dim)" }}>{m.tank}</span>
                  <span style={{ color: "var(--seal-text)" }}>{Math.round(m.value * 100)}%</span>
                </div>
                <div className="confidence-bar">
                  <div
                    className="confidence-bar-fill"
                    style={{ width: `${Math.round(m.value * 100)}%`, background: "var(--seal-accent)" }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.last_intention && (
        <div className="card">
          <p className="text-xs font-semibold mb-1" style={{ color: "var(--seal-text-dim)" }}>Última intención</p>
          <p className="text-sm" style={{ color: "var(--seal-text)" }}>{data.last_intention}</p>
        </div>
      )}
    </div>
  );
}
