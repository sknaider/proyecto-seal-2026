import { useEffect, useState } from "react";

type Summary = {
  suite_count: number;
  passing_suites: number;
  failing_suites: number;
  failed_runs_24h: number;
  resolved_failures_24h: number;
  latest_run_at: string | null;
  pending_validation_agents: number;
  stale_agents: number;
  open_tasks: number;
  natural_bridge_events: number;
  pending_skill_reviews: number;
  factory_records: number;
};

type EvalRun = {
  id: number;
  suite: string;
  score: number;
  passed: boolean;
  evidence: string;
  run_at: string | null;
};

type RecentFailure = EvalRun & {
  resolved_by_latest: boolean;
  current_passed: boolean | null;
};

type PendingValidation = {
  agent: string;
  task: string | null;
  risk_level: string | null;
  validations: string[];
  updated_at: string | null;
};

type AgentFreshness = {
  agent: string;
  task: string | null;
  state: string | null;
  risk_level: string | null;
  technical_state: string | null;
  age_minutes: number;
  stale: boolean;
  updated_at: string | null;
};

type BridgeEvent = {
  id: number;
  stage: string;
  sender: string | null;
  chat_id: string | null;
  action: string;
  status: string;
  created_at: string | null;
};

type PendingSkillReview = {
  id: number;
  name: string;
  type: string | null;
  metric_score: number;
  skill_path: string | null;
  created_at: string | null;
};

type OpenTask = {
  id: number;
  agent: string;
  title: string;
  status: string;
  priority: number;
  created_at: string | null;
};

type EvidenceDashboard = {
  agent: string;
  generated_at: string | null;
  summary: Summary;
  latest_runs: EvalRun[];
  latest_by_suite: EvalRun[];
  recent_failures_24h: RecentFailure[];
  pending_validations: PendingValidation[];
  agent_freshness: AgentFreshness[];
  bridge: {
    natural_event_count: number;
    latest_events: BridgeEvent[];
  };
  pending_skill_reviews: PendingSkillReview[];
  open_tasks: OpenTask[];
};

const fmtDate = (value: string | null) => {
  if (!value) return "—";
  return new Date(value).toLocaleString("es-PE", {
    timeZone: "America/Lima",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const statusColor = (ok: boolean) => ok ? "var(--seal-success)" : "var(--seal-error)";

function Metric({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="card">
      <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>{label}</p>
      <p className="text-xl font-semibold" style={{ color: tone || "var(--seal-text)" }}>{value}</p>
    </div>
  );
}

export default function EvidenceDashboardSection({ agent }: { agent: string }) {
  const [data, setData] = useState<EvidenceDashboard | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch(`/api/soul/evidence_dashboard?agent=${agent}`)
        .then((r) => r.json())
        .then((d) => {
          if (!cancelled) {
            setData(d);
            setLoading(false);
          }
        })
        .catch(() => {
          if (!cancelled) setLoading(false);
        });
    };
    setLoading(true);
    load();
    const t = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [agent]);

  if (loading) {
    return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando evidencia...</p>;
  }
  if (!data) {
    return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin evidencia disponible.</p>;
  }

  const s = data.summary;
  const allSuitesGreen = s.failing_suites === 0;

  return (
    <div className="max-w-6xl space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Evidence — {agent}</h2>
          <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Actualizado {fmtDate(data.generated_at)}</p>
        </div>
        <span className="pill" style={{ background: "var(--seal-bg)", color: statusColor(allSuitesGreen) }}>
          {allSuitesGreen ? "verde" : "requiere revisión"}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        <Metric label="Suites" value={`${s.passing_suites}/${s.suite_count}`} tone={statusColor(allSuitesGreen)} />
        <Metric label="Actual Fail" value={s.failing_suites} tone={s.failing_suites ? "var(--seal-error)" : "var(--seal-success)"} />
        <Metric label="Hist. 24h" value={s.failed_runs_24h} tone={s.failing_suites ? "var(--seal-error)" : s.failed_runs_24h ? "var(--seal-warning)" : "var(--seal-success)"} />
        <Metric label="Validaciones" value={s.pending_validation_agents} tone={s.pending_validation_agents ? "var(--seal-warning)" : "var(--seal-success)"} />
        <Metric label="Skill Reviews" value={s.pending_skill_reviews} tone={s.pending_skill_reviews ? "var(--seal-warning)" : "var(--seal-success)"} />
        <Metric label="Stale" value={s.stale_agents} tone={s.stale_agents ? "var(--seal-warning)" : "var(--seal-success)"} />
        <Metric label="Bridge" value={s.natural_bridge_events} tone="var(--seal-accent)" />
        <Metric label="Factory" value={s.factory_records} tone="var(--soul-purple)" />
      </div>

      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Suite Matrix</h3>
          <span className="text-[10px]" style={{ color: statusColor(allSuitesGreen) }}>
            {s.passing_suites}/{s.suite_count} current
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2">
          {data.latest_by_suite.map((run) => (
            <div
              key={run.suite}
              className="rounded border px-2 py-1.5 min-w-0"
              title={`${run.suite} · ${run.evidence}`}
              style={{
                borderColor: run.passed ? "rgba(57, 217, 138, 0.32)" : "rgba(255, 95, 109, 0.45)",
                background: run.passed ? "rgba(57, 217, 138, 0.05)" : "rgba(255, 95, 109, 0.08)",
              }}
            >
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate" style={{ color: "var(--seal-text)" }}>{run.suite}</span>
                <span style={{ color: run.passed ? "var(--seal-success)" : "var(--seal-error)" }}>{run.score}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Evaluation Runs</h3>
            <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>{fmtDate(s.latest_run_at)}</span>
          </div>
          <div className="space-y-2">
            {data.latest_runs.slice(0, 10).map((run) => (
              <div key={run.id} className="grid grid-cols-[72px_1fr_48px] gap-2 text-xs items-start">
                <span style={{ color: statusColor(run.passed) }}>{run.passed ? "passed" : "failed"}</span>
                <div>
                  <p className="font-medium" style={{ color: "var(--seal-text)" }}>{run.suite}</p>
                  <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>{run.evidence}</p>
                </div>
                <span className="text-right" style={{ color: run.score >= 90 ? "var(--seal-success)" : "var(--seal-warning)" }}>{run.score}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Recent Failures</h3>
            <span className="text-[10px]" style={{ color: s.failing_suites ? "var(--seal-error)" : "var(--seal-warning)" }}>
              {s.resolved_failures_24h}/{s.failed_runs_24h} resolved
            </span>
          </div>
          {data.recent_failures_24h.length ? (
            <div className="space-y-2">
              {data.recent_failures_24h.slice(0, 6).map((run) => (
                <div key={run.id} className="grid grid-cols-[72px_1fr_48px] gap-2 text-xs items-start">
                  <span style={{ color: run.resolved_by_latest ? "var(--seal-success)" : "var(--seal-error)" }}>
                    {run.resolved_by_latest ? "resolved" : "current"}
                  </span>
                  <div>
                    <p className="font-medium" style={{ color: "var(--seal-text)" }}>{run.suite}</p>
                    <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>{run.evidence}</p>
                  </div>
                  <span className="text-right" style={{ color: "var(--seal-warning)" }}>{run.score}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs" style={{ color: "var(--seal-success)" }}>Sin fallos recientes.</p>
          )}
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Agent Freshness</h3>
          <div className="space-y-2">
            {data.agent_freshness.map((row) => (
              <div key={row.agent} className="grid grid-cols-[72px_84px_1fr] gap-2 text-xs items-start">
                <span className="font-semibold" style={{ color: row.stale ? "var(--seal-warning)" : "var(--seal-success)" }}>{row.agent}</span>
                <span style={{ color: "var(--seal-text-dim)" }}>{Math.round(row.age_minutes)} min</span>
                <div>
                  <p style={{ color: "var(--seal-text)" }}>{row.state || "—"}</p>
                  <p className="truncate" style={{ color: "var(--seal-text-dim)" }}>{row.task || row.technical_state || "sin tarea"}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Pending Validations</h3>
          {data.pending_validations.length ? (
            <div className="space-y-3">
              {data.pending_validations.map((row) => (
                <div key={row.agent} className="text-xs">
                  <p className="font-semibold mb-1" style={{ color: "var(--seal-warning)" }}>{row.agent}</p>
                  {row.validations.map((item, i) => (
                    <p key={i} style={{ color: "var(--seal-text-dim)" }}>{item}</p>
                  ))}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs" style={{ color: "var(--seal-success)" }}>Sin validaciones pendientes.</p>
          )}
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Skill Review Queue</h3>
          {data.pending_skill_reviews.length ? (
            <div className="space-y-2">
              {data.pending_skill_reviews.slice(0, 8).map((skill) => (
                <div key={skill.id} className="text-xs">
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-semibold break-words" style={{ color: "var(--seal-text)" }}>{skill.name}</span>
                    <span style={{ color: skill.metric_score >= 0.8 ? "var(--seal-success)" : "var(--seal-warning)" }}>
                      {skill.metric_score.toFixed(2)}
                    </span>
                  </div>
                  <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>
                    {skill.type || "skill"} · {fmtDate(skill.created_at)}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs" style={{ color: "var(--seal-success)" }}>Sin skill reviews pendientes.</p>
          )}
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Bridge Watch</h3>
          <div className="space-y-2">
            {data.bridge.latest_events.slice(0, 6).map((event) => (
              <div key={event.id} className="grid grid-cols-[92px_1fr] gap-2 text-xs">
                <span style={{ color: "var(--seal-accent)" }}>{event.stage}</span>
                <span className="truncate" style={{ color: "var(--seal-text-dim)" }}>
                  {event.sender || "system"} #{event.chat_id || "—"} · {fmtDate(event.created_at)}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Open Tasks</h3>
          {data.open_tasks.length ? (
            <div className="space-y-2">
              {data.open_tasks.slice(0, 8).map((task) => (
                <div key={task.id} className="grid grid-cols-[44px_52px_1fr] gap-2 text-xs">
                  <span style={{ color: "var(--seal-text-dim)" }}>#{task.id}</span>
                  <span style={{ color: "var(--soul-purple)" }}>{task.agent}</span>
                  <span className="break-words" style={{ color: "var(--seal-text)" }}>{task.title}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs" style={{ color: "var(--seal-success)" }}>Sin tareas abiertas.</p>
          )}
        </div>
      </div>
    </div>
  );
}
