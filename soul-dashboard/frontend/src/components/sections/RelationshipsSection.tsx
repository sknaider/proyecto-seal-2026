import { useState, useEffect } from "react";

type Relationship = {
  person: string;
  trust: number;
  style: string;
  dynamic: string;
  interactions: number;
  updated_at: string;
};

export default function RelationshipsSection({ agent }: { agent: string }) {
  const [rels, setRels] = useState<Relationship[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/soul/relationships?agent=${agent}`)
      .then((r) => r.json())
      .then((d) => { setRels(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [agent]);

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando relaciones...</p>;

  return (
    <div className="max-w-2xl space-y-4">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Relaciones — {agent}</h2>

      {rels.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin relaciones registradas.</p>
      ) : (
        <div className="space-y-3">
          {rels.map((r) => {
            const trustPct = Math.round(Math.max(0, Math.min(1, r.trust)) * 100);
            const trustColor = trustPct >= 80 ? "var(--seal-success)" : trustPct >= 50 ? "var(--seal-accent)" : "var(--seal-warning)";
            return (
              <div key={r.person} className="card">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-semibold text-sm" style={{ color: "var(--seal-text)" }}>{r.person}</p>
                    {r.dynamic && (
                      <p className="text-xs mt-0.5" style={{ color: "var(--soul-purple)" }}>{r.dynamic}</p>
                    )}
                    {r.style && (
                      <p className="text-xs mt-1" style={{ color: "var(--seal-text-dim)" }}>{r.style}</p>
                    )}
                  </div>
                  <div className="text-right flex-shrink-0">
                    <p className="text-lg font-bold" style={{ color: trustColor }}>{trustPct}%</p>
                    <p className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>confianza</p>
                    <p className="text-[10px] mt-0.5" style={{ color: "var(--seal-text-dim)" }}>
                      {r.interactions} interacciones
                    </p>
                  </div>
                </div>
                <div className="mt-2 confidence-bar">
                  <div
                    className="confidence-bar-fill"
                    style={{ width: `${trustPct}%`, background: trustColor }}
                  />
                </div>
                {r.updated_at && (
                  <p className="text-[10px] mt-1" style={{ color: "var(--seal-text-dim)" }}>
                    Actualizado: {new Date(r.updated_at).toLocaleDateString("es-PE")}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
