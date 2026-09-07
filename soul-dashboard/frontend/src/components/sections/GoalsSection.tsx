import { useState, useEffect } from "react";

type Goal = {
  id: number;
  goal: string;
  priority: number;
  status: string;
  horizon: string;
  deadline: string | null;
  progress: number;
  created_at: string;
};

const STATUS_COLORS: Record<string, string> = {
  active: "var(--seal-success)",
  completed: "var(--seal-accent)",
  paused: "var(--seal-warning)",
  cancelled: "var(--seal-error)",
};

export default function GoalsSection({ agent }: { agent: string }) {
  const [goals, setGoals] = useState<Goal[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/soul/goals?agent=${agent}`)
      .then((r) => r.json())
      .then((d) => { setGoals(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [agent]);

  const active = goals.filter((g) => g.status === "active" || !g.status);
  const rest = goals.filter((g) => g.status && g.status !== "active");

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando objetivos...</p>;

  const GoalCard = ({ g }: { g: Goal }) => (
    <div className="card" style={{ padding: "10px 12px" }}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1">
          <p className="text-sm leading-relaxed" style={{ color: "var(--seal-text)" }}>{g.goal}</p>
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            <span className="pill" style={{ background: "var(--seal-bg)", color: STATUS_COLORS[g.status] || "var(--seal-text-dim)" }}>
              {g.status || "activo"}
            </span>
            {g.horizon && (
              <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-accent)" }}>{g.horizon}</span>
            )}
            {g.deadline && (
              <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-warning)" }}>
                📅 {new Date(g.deadline).toLocaleDateString("es-PE")}
              </span>
            )}
          </div>
        </div>
        <span className="text-xs font-bold flex-shrink-0" style={{ color: "var(--soul-purple)" }}>
          P{g.priority}
        </span>
      </div>
      {g.progress > 0 && (
        <div className="mt-2">
          <div className="flex justify-between text-[10px] mb-0.5">
            <span style={{ color: "var(--seal-text-dim)" }}>Progreso</span>
            <span style={{ color: "var(--seal-text)" }}>{Math.round(g.progress * 100)}%</span>
          </div>
          <div className="confidence-bar">
            <div
              className="confidence-bar-fill"
              style={{ width: `${Math.round(g.progress * 100)}%`, background: "var(--seal-success)" }}
            />
          </div>
        </div>
      )}
    </div>
  );

  return (
    <div className="max-w-3xl space-y-5">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Objetivos — {agent}</h2>

      {active.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "var(--seal-text-dim)" }}>
            Activos ({active.length})
          </h3>
          <div className="space-y-2">
            {active.map((g) => <GoalCard key={g.id} g={g} />)}
          </div>
        </div>
      )}

      {rest.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "var(--seal-text-dim)" }}>
            Historial ({rest.length})
          </h3>
          <div className="space-y-2">
            {rest.map((g) => <GoalCard key={g.id} g={g} />)}
          </div>
        </div>
      )}

      {goals.length === 0 && (
        <p className="text-sm text-center py-8" style={{ color: "var(--seal-text-dim)" }}>
          Sin objetivos registrados para {agent}.
        </p>
      )}
    </div>
  );
}
