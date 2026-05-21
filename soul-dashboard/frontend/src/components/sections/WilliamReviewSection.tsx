import { useEffect, useState } from "react";

type WilliamReviewPayload = {
  agent: string;
  reviewer: string;
  generated_at: string | null;
  summary: {
    total: number;
    queue_items: number;
    alerts: number;
    recent_decisions: number;
  };
  items: {
    type: string;
    item: Record<string, unknown>;
    policy: Record<string, unknown>;
  }[];
  recent_decisions: {
    id: number;
    target_type: string;
    target_id: string;
    actor: string;
    decision: string;
    rationale: string;
    created_at: string | null;
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
  const [rationales, setRationales] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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

  const submitDecision = (targetType: string, targetId: string, decision: "approved" | "rejected" | "needs_more_info") => {
    const key = `${targetType}:${targetId}`;
    const rationale = (rationales[key] || "").trim();
    if (rationale.length < 12) {
      setNotice("Rationale minimo de 12 caracteres.");
      return;
    }
    setSaving(`${key}:${decision}`);
    setNotice(null);
    fetch("/api/soul/nexus_review_queue/william_decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_type: targetType,
        target_id: targetId,
        decision,
        rationale,
        agent,
        actor: "William",
        reviewer: "NEXUS",
        dry_run: false,
        evidence: { ui: "william_review" },
      }),
    })
      .then(async (r) => {
        const payload = await r.json();
        if (!r.ok) throw new Error(payload.detail || "william decision failed");
        setRationales((prev) => ({ ...prev, [key]: "" }));
        setNotice(`Decision William ${payload.decision} registrada para ${targetId}.`);
        load();
      })
      .catch((err) => setNotice(String(err.message || err)))
      .finally(() => setSaving(null));
  };

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
        <Metric label="Decisions" value={s.recent_decisions || 0} color="var(--seal-success)" />
      </div>

      {notice ? (
        <div className="card text-xs" style={{ color: notice.includes("registrada") ? "var(--seal-success)" : "var(--seal-warning)" }}>
          {notice}
        </div>
      ) : null}

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
              <div className="lg:col-span-3 grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-2">
                <textarea
                  className="w-full min-h-[48px] rounded border bg-transparent px-2 py-1 text-xs outline-none"
                  style={{ borderColor: "var(--seal-border)", color: "var(--seal-text)" }}
                  placeholder="Rationale William"
                  value={rationales[`${row.type}:${String(row.item.item_id || row.item.id || "")}`] || ""}
                  onChange={(e) => setRationales((prev) => ({
                    ...prev,
                    [`${row.type}:${String(row.item.item_id || row.item.id || "")}`]: e.target.value,
                  }))}
                />
                <div className="flex flex-wrap gap-2 lg:justify-end">
                  {(["approved", "needs_more_info", "rejected"] as const).map((decision) => {
                    const targetId = String(row.item.item_id || row.item.id || "");
                    const key = `${row.type}:${targetId}`;
                    const disabled = saving !== null || (rationales[key] || "").trim().length < 12 || !targetId;
                    return (
                      <button
                        key={decision}
                        type="button"
                        className="px-2 py-1 rounded border text-[10px] font-semibold disabled:opacity-40"
                        style={{
                          borderColor: decision === "approved" ? "var(--seal-success)" : decision === "rejected" ? "var(--seal-error)" : "var(--seal-warning)",
                          color: decision === "approved" ? "var(--seal-success)" : decision === "rejected" ? "var(--seal-error)" : "var(--seal-warning)",
                        }}
                        disabled={disabled}
                        onClick={() => submitDecision(row.type === "alert" ? "alert" : "review_item", targetId, decision)}
                      >
                        {saving === `${key}:${decision}` ? "..." : decision}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          )) : (
            <p className="text-xs" style={{ color: "var(--seal-success)" }}>No hay nada que requiera autorizacion de William.</p>
          )}
        </div>
      </div>

      <div className="card">
        <div className="flex items-center justify-between gap-3 mb-3">
          <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>William Decisions</h3>
          <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>audit trail</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {data.recent_decisions?.length ? data.recent_decisions.map((decision) => (
            <div key={decision.id} className="border rounded p-2 text-xs" style={{ borderColor: "var(--seal-border)" }}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold" style={{ color: decision.decision === "approved" ? "var(--seal-success)" : "var(--seal-warning)" }}>{decision.decision}</span>
                <span style={{ color: "var(--seal-text-dim)" }}>{fmtDate(decision.created_at)}</span>
              </div>
              <p className="break-words" style={{ color: "var(--seal-text)" }}>{decision.target_type}:{decision.target_id}</p>
              <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>{decision.rationale}</p>
            </div>
          )) : (
            <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Sin decisiones William registradas.</p>
          )}
        </div>
      </div>
    </div>
  );
}
