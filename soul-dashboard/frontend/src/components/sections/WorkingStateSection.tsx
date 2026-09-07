import { useState, useEffect } from "react";

type WorkingState = {
  agent: string;
  task: string | null;
  step: number | null;
  total_steps: number | null;
  description: string | null;
  active_hypotheses: string[];
  current_constraints: string[];
  pending_validations: string[];
  discarded_paths: string[];
  risk_level: string | null;
  agent_state: string | null;
  emotional_state: string | null;
  last_intention: string | null;
  state: Record<string, unknown> | null;
  turn_count: number | null;
  updated_at: string | null;
};

const RISK_COLORS: Record<string, string> = {
  low: "var(--seal-success)",
  medium: "var(--seal-warning)",
  high: "var(--seal-error)",
  critical: "#ef4444",
};

export default function WorkingStateSection({ agent }: { agent: string }) {
  const [ws, setWs] = useState<WorkingState | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/soul/working_state?agent=${agent}`)
      .then((r) => r.json())
      .then((d) => { setWs(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [agent]);

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando estado...</p>;
  if (!ws?.task && !ws?.agent_state && !ws?.state) {
    return (
      <div className="max-w-2xl">
        <h2 className="text-base font-bold mb-4" style={{ color: "var(--soul-purple)" }}>Estado Activo — {agent}</h2>
        <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin estado activo registrado.</p>
      </div>
    );
  }

  const StringList = ({ label, items }: { label: string; items: string[] }) => {
    if (!items?.length) return null;
    return (
      <div>
        <p className="text-xs font-semibold mb-1.5" style={{ color: "var(--seal-text-dim)" }}>{label}</p>
        <div className="space-y-1">
          {items.map((item, i) => (
            <div key={i} className="flex items-start gap-2 text-xs" style={{ color: "var(--seal-text)" }}>
              <span style={{ color: "var(--soul-purple)" }}>•</span>
              <span>{item}</span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Estado Activo — {agent}</h2>
        {ws?.risk_level && (
          <span
            className="pill"
            style={{ background: "var(--seal-bg)", color: RISK_COLORS[ws.risk_level.toLowerCase()] || "var(--seal-text-dim)" }}
          >
            ⚠ {ws.risk_level}
          </span>
        )}
        {ws?.turn_count !== null && (
          <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-text-dim)" }}>
            {ws.turn_count} turnos
          </span>
        )}
      </div>

      {/* Task */}
      {ws?.task && (
        <div className="card">
          <p className="text-xs font-semibold mb-1" style={{ color: "var(--seal-text-dim)" }}>Tarea</p>
          <p className="text-sm font-medium" style={{ color: "var(--seal-text)" }}>{ws.task}</p>
          {ws.description && (
            <p className="text-xs mt-1" style={{ color: "var(--seal-text-dim)" }}>{ws.description}</p>
          )}
          {ws.step !== null && ws.total_steps !== null && ws.total_steps > 0 && (
            <div className="mt-2">
              <div className="flex justify-between text-[10px] mb-0.5">
                <span style={{ color: "var(--seal-text-dim)" }}>Paso {ws.step} de {ws.total_steps}</span>
                <span style={{ color: "var(--seal-text)" }}>{Math.round((ws.step / ws.total_steps) * 100)}%</span>
              </div>
              <div className="confidence-bar">
                <div
                  className="confidence-bar-fill"
                  style={{ width: `${(ws.step / ws.total_steps) * 100}%`, background: "var(--soul-purple)" }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* States */}
      {(ws?.agent_state || ws?.emotional_state || ws?.last_intention) && (
        <div className="card space-y-2">
          {ws.agent_state && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-0.5" style={{ color: "var(--seal-text-dim)" }}>Estado cognitivo</p>
              <p className="text-sm" style={{ color: "var(--seal-text)" }}>{ws.agent_state}</p>
            </div>
          )}
          {ws.emotional_state && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-0.5" style={{ color: "var(--seal-text-dim)" }}>Estado emocional</p>
              <p className="text-sm" style={{ color: "var(--soul-purple)" }}>{ws.emotional_state}</p>
            </div>
          )}
          {ws.last_intention && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-0.5" style={{ color: "var(--seal-text-dim)" }}>Última intención</p>
              <p className="text-sm" style={{ color: "var(--seal-text)" }}>{ws.last_intention}</p>
            </div>
          )}
        </div>
      )}

      {/* Lists */}
      {(ws?.active_hypotheses?.length > 0 || ws?.current_constraints?.length > 0 || ws?.pending_validations?.length > 0) && (
        <div className="card space-y-4">
          <StringList label="Hipótesis activas" items={ws.active_hypotheses} />
          <StringList label="Restricciones actuales" items={ws.current_constraints} />
          <StringList label="Validaciones pendientes" items={ws.pending_validations} />
          <StringList label="Caminos descartados" items={ws.discarded_paths} />
        </div>
      )}

      {ws?.state && Object.keys(ws.state).length > 0 && (
        <div className="card">
          <p className="text-xs font-semibold mb-2" style={{ color: "var(--seal-text-dim)" }}>Estado extendido (JSONB)</p>
          <pre
            className="text-[10px] overflow-auto"
            style={{ color: "var(--seal-text-dim)", maxHeight: "200px" }}
          >
            {JSON.stringify(ws.state, null, 2)}
          </pre>
        </div>
      )}

      {ws?.updated_at && (
        <p className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
          Actualizado: {new Date(ws.updated_at).toLocaleString("es-PE", { timeZone: "America/Lima" })}
        </p>
      )}
    </div>
  );
}
