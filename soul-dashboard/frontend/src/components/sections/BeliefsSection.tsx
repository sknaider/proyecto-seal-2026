import { useState, useEffect } from "react";

type Belief = {
  id: number;
  topic: string;
  content: string;
  confidence: number;
  evidence: number;
  valid_from: string | null;
  created_at: string;
};

export default function BeliefsSection({ agent }: { agent: string }) {
  const [beliefs, setBeliefs] = useState<Belief[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");

  useEffect(() => {
    setLoading(true);
    fetch(`/api/soul/beliefs?agent=${agent}&limit=50`)
      .then((r) => r.json())
      .then((d) => { setBeliefs(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [agent]);

  const filtered = q
    ? beliefs.filter(
        (b) =>
          b.topic?.toLowerCase().includes(q.toLowerCase()) ||
          b.content?.toLowerCase().includes(q.toLowerCase())
      )
    : beliefs;

  return (
    <div className="max-w-3xl space-y-4">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Creencias — {agent}</h2>

      <input
        className="w-full px-3 py-1.5 text-sm rounded border outline-none"
        style={{
          background: "var(--seal-surface)", borderColor: "var(--seal-border)",
          color: "var(--seal-text)",
        }}
        placeholder="Filtrar por tema o contenido..."
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />

      <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
        {filtered.length} / {beliefs.length} creencias activas
      </p>

      {loading ? (
        <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando...</p>
      ) : (
        <div className="space-y-2">
          {filtered.map((b) => (
            <div key={b.id} className="card" style={{ padding: "10px 12px", borderLeft: "3px solid #8b5cf6" }}>
              <div className="flex items-start justify-between gap-2 mb-1.5">
                <span className="text-sm font-semibold" style={{ color: "var(--soul-purple)" }}>{b.topic}</span>
                <span className="text-xs font-bold flex-shrink-0" style={{ color: "var(--seal-accent)" }}>
                  {Math.round(b.confidence * 100)}% conf.
                </span>
              </div>
              <p className="text-sm leading-relaxed" style={{ color: "var(--seal-text)" }}>{b.content}</p>
              <div className="flex items-center gap-3 mt-2">
                <div className="flex-1 confidence-bar">
                  <div
                    className="confidence-bar-fill"
                    style={{ width: `${Math.round(b.confidence * 100)}%`, background: "var(--seal-accent)" }}
                  />
                </div>
                <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
                  {b.evidence} evidencias
                </span>
                <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
                  {b.created_at ? new Date(b.created_at).toLocaleDateString("es-PE") : ""}
                </span>
              </div>
            </div>
          ))}
          {filtered.length === 0 && (
            <p className="text-sm text-center py-8" style={{ color: "var(--seal-text-dim)" }}>Sin creencias registradas.</p>
          )}
        </div>
      )}
    </div>
  );
}
