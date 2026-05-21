import { useState, useEffect } from "react";

const OCEAN_COLORS: Record<string, string> = {
  O: "#8b5cf6", C: "#3b82f6", E: "#f59e0b", A: "#22c55e", N: "#ef4444",
};
const OCEAN_LABELS: Record<string, [string, string]> = {
  O: ["Apertura", "Curiosidad, creatividad, apertura a ideas nuevas"],
  C: ["Responsabilidad", "Organización, disciplina, fiabilidad"],
  E: ["Extroversión", "Energía social, asertividad, positividad"],
  A: ["Amabilidad", "Cooperación, empatía, cuidado de otros"],
  N: ["Neuroticismo", "Tendencia a emociones negativas, ansiedad"],
};

type AllOcean = { agent: string; ocean: Record<string, number>; baseline: Record<string, number>; updated_at: string | null }[];

export default function OceanSection({ agent }: { agent: string }) {
  const [all, setAll] = useState<AllOcean>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch("/api/soul/ocean/all")
      .then((r) => r.json())
      .then((d) => { setAll(d.agents || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const current = all.find((a) => a.agent === agent);

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando OCEAN...</p>;

  return (
    <div className="max-w-2xl space-y-6">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>OCEAN — {agent}</h2>

      {current ? (
        <div className="card space-y-4">
          {["O", "C", "E", "A", "N"].map((dim) => {
            const v = current.ocean[dim] ?? 0;
            const b = current.baseline[dim] ?? 0;
            const pct = Math.round(v * 100);
            const bPct = Math.round(b * 100);
            const delta = v - b;
            return (
              <div key={dim}>
                <div className="flex items-start justify-between mb-1">
                  <div>
                    <span className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>
                      {dim} — {OCEAN_LABELS[dim][0]}
                    </span>
                    <p className="text-[10px] mt-0.5" style={{ color: "var(--seal-text-dim)" }}>{OCEAN_LABELS[dim][1]}</p>
                  </div>
                  <div className="text-right">
                    <span className="text-lg font-bold" style={{ color: OCEAN_COLORS[dim] }}>{pct}%</span>
                    {Math.abs(delta) > 0.005 && (
                      <span
                        className="ml-2 text-xs"
                        style={{ color: delta > 0 ? "var(--seal-success)" : "var(--seal-error)" }}
                      >
                        {delta > 0 ? "+" : ""}{(delta * 100).toFixed(1)}
                      </span>
                    )}
                  </div>
                </div>
                <div className="relative h-2 rounded-full overflow-hidden" style={{ background: "var(--seal-bg)" }}>
                  <div
                    className="absolute inset-y-0 left-0 rounded-full transition-all duration-500"
                    style={{ width: `${pct}%`, background: OCEAN_COLORS[dim] }}
                  />
                  {bPct > 0 && (
                    <div
                      className="absolute top-0 bottom-0 w-0.5"
                      style={{ left: `${bPct}%`, background: "rgba(255,255,255,0.3)" }}
                      title={`Baseline: ${bPct}%`}
                    />
                  )}
                </div>
                {bPct > 0 && (
                  <p className="text-[10px] mt-0.5" style={{ color: "var(--seal-text-dim)" }}>Baseline: {bPct}%</p>
                )}
              </div>
            );
          })}
          {current.updated_at && (
            <p className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
              Actualizado: {new Date(current.updated_at).toLocaleString("es-PE", { timeZone: "America/Lima" })}
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin datos OCEAN para {agent}.</p>
      )}

      {/* All agents comparison */}
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "var(--seal-text-dim)" }}>Comparación equipo</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {all.map((a) => (
            <div key={a.agent} className="card" style={{ borderColor: a.agent === agent ? "var(--soul-purple)" : "var(--seal-border)" }}>
              <p className="text-xs font-bold mb-2" style={{ color: a.agent === agent ? "var(--soul-purple)" : "var(--seal-text-dim)" }}>{a.agent}</p>
              {["O", "C", "E", "A", "N"].map((dim) => {
                const pct = Math.round((a.ocean[dim] ?? 0) * 100);
                return (
                  <div key={dim} className="flex items-center gap-1 mb-1">
                    <span className="text-[10px] w-3" style={{ color: OCEAN_COLORS[dim] }}>{dim}</span>
                    <div className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: "var(--seal-bg)" }}>
                      <div style={{ width: `${pct}%`, height: "100%", background: OCEAN_COLORS[dim], borderRadius: "9999px" }} />
                    </div>
                    <span className="text-[9px] w-6 text-right" style={{ color: "var(--seal-text-dim)" }}>{pct}</span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
