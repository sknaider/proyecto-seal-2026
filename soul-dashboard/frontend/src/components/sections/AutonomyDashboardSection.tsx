import { useEffect, useState } from "react";

type AutonomyPayload = {
  agent: string;
  reviewer: string;
  generated_at: string | null;
  summary: {
    pending_review: number;
    recent_decisions: number;
    worker_runs: number;
    learning_green: boolean;
    stages_ok: number;
    stages_total: number;
    william_required: number;
    debt_alerts: number;
    william_review: number;
  };
  review_queue: Record<string, unknown>;
  policy_gates: Record<string, unknown>;
  debt_alerts: {
    summary: Record<string, number>;
    alerts: Record<string, unknown>[];
  };
  william_review: {
    summary: Record<string, number>;
    items: Record<string, unknown>[];
  };
  learning_loop: {
    summary: Record<string, unknown>;
    stages: { step: number; name: string; ok: boolean; count: number }[];
    boundary: string;
  };
  recent_decisions: {
    id: number;
    item_id: string;
    decision: string;
    actor: string;
    applied: boolean;
    created_at: string | null;
  }[];
  latest_worker_run: Record<string, unknown>;
  boundary: string;
};

const fmtDate = (value: string | null) => {
  if (!value) return "-";
  return new Date(value).toLocaleString("es-PE", {
    timeZone: "America/Lima",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

function Metric({ label, value, tone }: { label: string; value: string | number; tone?: "ok" | "warn" | "dim" }) {
  const color = tone === "ok" ? "var(--seal-success)" : tone === "warn" ? "var(--seal-warning)" : "var(--seal-text)";
  return (
    <div className="card">
      <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>{label}</p>
      <p className="text-xl font-semibold" style={{ color }}>{value}</p>
    </div>
  );
}

export default function AutonomyDashboardSection({ agent }: { agent: string }) {
  const [data, setData] = useState<AutonomyPayload | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    fetch(`/api/soul/autonomy_dashboard?agent=${agent}&reviewer=NEXUS`)
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

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando Autonomy...</p>;
  if (!data) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin datos de autonomía.</p>;

  const s = data.summary;

  return (
    <div className="max-w-6xl space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Autonomy Control</h2>
          <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
            {data.agent} · reviewer {data.reviewer} · actualizado {fmtDate(data.generated_at)}
          </p>
        </div>
        <span className="pill" style={{ background: "var(--seal-bg)", color: s.learning_green ? "var(--seal-success)" : "var(--seal-warning)" }}>
          {s.stages_ok}/{s.stages_total} stages
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-9 gap-3">
        <Metric label="Pending" value={s.pending_review} tone={s.pending_review ? "warn" : "ok"} />
        <Metric label="Decisions" value={s.recent_decisions} tone="ok" />
        <Metric label="Worker Runs" value={s.worker_runs} tone={s.worker_runs ? "ok" : "warn"} />
        <Metric label="Learning" value={s.learning_green ? "green" : "watch"} tone={s.learning_green ? "ok" : "warn"} />
        <Metric label="Stages" value={`${s.stages_ok}/${s.stages_total}`} tone={s.stages_ok === s.stages_total ? "ok" : "warn"} />
        <Metric label="William Gate" value={s.william_required} tone={s.william_required ? "warn" : "ok"} />
        <Metric label="Debt Alerts" value={s.debt_alerts} tone={s.debt_alerts ? "warn" : "ok"} />
        <Metric label="William Review" value={s.william_review} tone={s.william_review ? "warn" : "ok"} />
        <Metric label="Boundary" value="audit" tone="dim" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-3">
        <div className="card">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Learning Loop</h3>
            <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>{data.learning_loop.boundary}</span>
          </div>
          <div className="space-y-2">
            {data.learning_loop.stages.map((stage) => (
              <div key={stage.step} className="grid grid-cols-[32px_1fr_80px_72px] gap-2 text-xs items-center border-b pb-2" style={{ borderColor: "var(--seal-border)" }}>
                <span style={{ color: "var(--seal-text-dim)" }}>{stage.step}</span>
                <span style={{ color: "var(--seal-text)" }}>{stage.name}</span>
                <span style={{ color: "var(--seal-text-dim)" }}>{stage.count}</span>
                <span style={{ color: stage.ok ? "var(--seal-success)" : "var(--seal-warning)" }}>{stage.ok ? "OK" : "WAIT"}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Decision Trail</h3>
          <div className="space-y-2">
            {data.recent_decisions.length ? data.recent_decisions.map((decision) => (
              <div key={decision.id} className="text-xs border-b pb-2" style={{ borderColor: "var(--seal-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold" style={{ color: decision.decision === "rejected" ? "var(--seal-error)" : "var(--seal-success)" }}>
                    {decision.decision}
                  </span>
                  <span style={{ color: "var(--seal-text-dim)" }}>{fmtDate(decision.created_at)}</span>
                </div>
                <p className="break-words" style={{ color: "var(--seal-text)" }}>{decision.item_id}</p>
                <p style={{ color: "var(--seal-text-dim)" }}>{decision.actor} · applied={String(decision.applied)}</p>
              </div>
            )) : (
              <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Sin decisiones recientes.</p>
            )}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="flex items-center justify-between gap-3 mb-2">
          <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Operational Snapshot</h3>
          <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>{data.boundary}</span>
        </div>
        <pre className="max-h-72 overflow-auto rounded border p-2 text-[10px]" style={{ borderColor: "var(--seal-border)", color: "var(--seal-text-dim)" }}>
          {JSON.stringify({
            review_queue: data.review_queue,
            policy_gates: data.policy_gates,
            debt_alerts: data.debt_alerts?.summary,
            william_review: data.william_review?.summary,
            learning_summary: data.learning_loop.summary,
            latest_worker_run: data.latest_worker_run,
          }, null, 2)}
        </pre>
      </div>
    </div>
  );
}
