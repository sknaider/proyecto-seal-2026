import { useEffect, useState } from "react";

type WilliamReviewPayload = {
  agent: string;
  reviewer: string;
  generated_at: string | null;
  summary: {
    total: number;
    queue_items: number;
    alerts: number;
  };
  items: {
    type: string;
    item: Record<string, unknown>;
    policy: Record<string, unknown>;
  }[];
  boundary: string;
};

const fmtDate = (value: unknown) => {
  if (!value || typeof value !== "string") return "-";
  return new Date(value).toLocaleString("es-PE", {
    timeZone: "America/Lima",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

function Metric({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="card">
      <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>{label}</p>
      <p className="text-xl font-semibold" style={{ color: color || "var(--seal-text)" }}>{value}</p>
    </div>
  );
}

export default function WilliamReviewSection({ agent }: { agent: string }) {
  const [data, setData] = useState<WilliamReviewPayload | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    fetch(`/api/soul/nexus_review_queue/william_review?agent=${agent}&reviewer=NEXUS`)
      .then((r) => r.json())
      .then((payload) => {
        setData(payload);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [agent]);

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando William Review...</p>;
  if (!data) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin datos de William Review.</p>;

  const s = data.summary;

  return (
    <div className="max-w-6xl space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>William Review</h2>
          <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
            Solo decisiones con gate humano · {data.agent} · {fmtDate(data.generated_at)}
          </p>
        </div>
        <span className="pill" style={{ background: "var(--seal-bg)", color: s.total ? "var(--seal-warning)" : "var(--seal-success)" }}>
          {s.total} items
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Metric label="Total" value={s.total} color={s.total ? "var(--seal-warning)" : "var(--seal-success)"} />
        <Metric label="Queue" value={s.queue_items} color="var(--seal-text)" />
        <Metric label="Alerts" value={s.alerts} color={s.alerts ? "var(--seal-warning)" : "var(--seal-success)"} />
        <Metric label="Boundary" value="human" color="var(--seal-text-dim)" />
      </div>

      <div className="card">
        <div className="flex items-center justify-between gap-3 mb-3">
          <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Human Gate Queue</h3>
          <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>{data.boundary}</span>
        </div>
        <div className="space-y-3">
          {data.items.length ? data.items.map((row, idx) => (
            <div key={`${row.type}-${idx}`} className="grid grid-cols-1 lg:grid-cols-[120px_1fr_280px] gap-3 text-xs border-b pb-3" style={{ borderColor: "var(--seal-border)" }}>
              <div>
                <p className="font-semibold" style={{ color: "var(--seal-warning)" }}>{row.type}</p>
                <p style={{ color: "var(--seal-text-dim)" }}>{String(row.item.kind || row.item.severity || "")}</p>
              </div>
              <div className="min-w-0">
                <p className="font-medium break-words" style={{ color: "var(--seal-text)" }}>
                  {String(row.item.title || row.item.item_id || "review item")}
                </p>
                <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>
                  {String(row.item.item_id || row.item.id || "")} · {fmtDate(row.item.created_at)}
                </p>
              </div>
              <pre className="max-h-36 overflow-auto rounded border p-2 text-[10px]" style={{ borderColor: "var(--seal-border)", color: "var(--seal-text-dim)" }}>
                {JSON.stringify({ item: row.item, policy: row.policy }, null, 2)}
              </pre>
            </div>
          )) : (
            <p className="text-xs" style={{ color: "var(--seal-success)" }}>No hay nada que requiera autorizacion de William.</p>
          )}
        </div>
      </div>
    </div>
  );
}
