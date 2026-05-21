import { useState, useEffect } from "react";

type Opinion = {
  id: number;
  topic: string;
  content: string;
  confidence: number;
  evidence: number;
  category: string;
  status: string;
  updated_at: string;
};

export default function OpinionsSection({ agent }: { agent: string }) {
  const [opinions, setOpinions] = useState<Opinion[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");

  useEffect(() => {
    setLoading(true);
    fetch(`/api/soul/opinions?agent=${agent}&limit=50`)
      .then((r) => r.json())
      .then((d) => { setOpinions(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [agent]);

  const filtered = q
    ? opinions.filter(
        (o) =>
          o.topic?.toLowerCase().includes(q.toLowerCase()) ||
          o.content?.toLowerCase().includes(q.toLowerCase())
      )
    : opinions;

  return (
    <div className="max-w-3xl space-y-4">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Opiniones — {agent}</h2>

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
        {filtered.length} / {opinions.length} opiniones
      </p>

      {loading ? (
        <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando...</p>
      ) : (
        <div className="space-y-2">
          {filtered.map((o) => (
            <div key={o.id} className="card" style={{ padding: "10px 12px" }}>
              <div className="flex items-start justify-between gap-2 mb-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-semibold" style={{ color: "var(--seal-accent)" }}>{o.topic}</span>
                  {o.category && (
                    <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-text-dim)" }}>{o.category}</span>
                  )}
                  {o.status && o.status !== "active" && (
                    <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-warning)" }}>{o.status}</span>
                  )}
                </div>
                <span className="text-xs font-bold flex-shrink-0" style={{ color: "var(--soul-purple)" }}>
                  {Math.round(o.confidence * 100)}%
                </span>
              </div>
              <p className="text-sm leading-relaxed" style={{ color: "var(--seal-text)" }}>{o.content}</p>
              <div className="flex items-center gap-3 mt-1.5">
                <div className="flex-1 confidence-bar">
                  <div
                    className="confidence-bar-fill"
                    style={{ width: `${Math.round(o.confidence * 100)}%`, background: "var(--soul-purple)" }}
                  />
                </div>
                <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
                  {o.evidence} evidencias
                </span>
                <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
                  {o.updated_at ? new Date(o.updated_at).toLocaleDateString("es-PE") : ""}
                </span>
              </div>
            </div>
          ))}
          {filtered.length === 0 && (
            <p className="text-sm text-center py-8" style={{ color: "var(--seal-text-dim)" }}>Sin opiniones.</p>
          )}
        </div>
      )}
    </div>
  );
}
