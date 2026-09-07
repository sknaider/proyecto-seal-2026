import { useState, useEffect } from "react";

type Instinct = {
  id: number;
  trigger: string;
  action: string;
  strength: number;
  wins: number;
  losses: number;
  score: number;
  created_at: string;
};

type Activation = {
  trigger: string;
  context: string;
  outcome: string;
  at: string;
};

export default function InstinctsSection({ agent }: { agent: string }) {
  const [instincts, setInstincts] = useState<Instinct[]>([]);
  const [activations, setActivations] = useState<Activation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/soul/instincts?agent=${agent}`)
      .then((r) => r.json())
      .then((d) => {
        setInstincts(d.instincts || []);
        setActivations(d.recent_activations || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [agent]);

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando instintos...</p>;

  return (
    <div className="max-w-3xl space-y-5">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Instintos — {agent}</h2>

      {/* Instincts list */}
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "var(--seal-text-dim)" }}>
          Instintos activos ({instincts.length})
        </h3>
        <div className="space-y-2">
          {instincts.map((inst) => {
            const total = inst.wins + inst.losses;
            const winRate = total > 0 ? (inst.wins / total) * 100 : 0;
            return (
              <div key={inst.id} className="card" style={{ padding: "10px 12px" }}>
                <div className="flex items-start gap-3">
                  <div className="flex-1">
                    <p className="text-xs font-semibold mb-0.5" style={{ color: "var(--seal-text-dim)" }}>Disparador</p>
                    <p className="text-sm" style={{ color: "var(--seal-text)" }}>{inst.trigger}</p>
                    <p className="text-xs font-semibold mt-1.5 mb-0.5" style={{ color: "var(--seal-text-dim)" }}>Acción</p>
                    <p className="text-sm" style={{ color: "var(--seal-accent)" }}>{inst.action}</p>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <p className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>
                      {Math.round(inst.strength * 100)}%
                    </p>
                    <p className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>fuerza</p>
                    <p className="text-xs mt-1" style={{ color: inst.wins > inst.losses ? "var(--seal-success)" : "var(--seal-text-dim)" }}>
                      {inst.wins}W / {inst.losses}L
                    </p>
                  </div>
                </div>
                <div className="mt-2">
                  <div className="confidence-bar">
                    <div
                      className="confidence-bar-fill"
                      style={{ width: `${Math.round(inst.strength * 100)}%`, background: "var(--soul-purple)" }}
                    />
                  </div>
                  {total > 0 && (
                    <p className="text-[10px] mt-0.5" style={{ color: "var(--seal-text-dim)" }}>
                      Win rate: {winRate.toFixed(0)}% — score: {inst.score.toFixed(2)}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
          {instincts.length === 0 && (
            <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin instintos registrados.</p>
          )}
        </div>
      </div>

      {/* Recent activations */}
      {activations.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "var(--seal-text-dim)" }}>
            Activaciones recientes
          </h3>
          <div className="space-y-2">
            {activations.map((a, i) => (
              <div key={i} className="card" style={{ padding: "8px 12px", borderLeft: "3px solid var(--seal-warning)" }}>
                <p className="text-[10px] font-semibold mb-0.5" style={{ color: "var(--seal-warning)" }}>{a.trigger}</p>
                {a.context && <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>{a.context}</p>}
                {a.outcome && (
                  <p className="text-xs mt-1" style={{ color: a.outcome.includes("success") ? "var(--seal-success)" : "var(--seal-text)" }}>
                    → {a.outcome}
                  </p>
                )}
                <p className="text-[10px] mt-1" style={{ color: "var(--seal-text-dim)" }}>
                  {a.at ? new Date(a.at).toLocaleString("es-PE", { timeZone: "America/Lima" }) : ""}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
