import { useEffect, useState } from "react";

type Summary = {
  suite_count: number;
  passing_suites: number;
  failing_suites: number;
  latest_run_at: string | null;
  recent_ticks: number;
  queue_pending: number;
  closed_loop_outcomes: number;
  promote_pending: number;
  rollback_pending: number;
  guardrail_candidates: number;
  regression_tests: number;
};

type AwarenessState = {
  state: string;
  current_focus: string | null;
  budget_class: string | null;
  local_runtime_status: string | null;
  big_cortex_status: string | null;
  confidence: number;
  updated_at: string | null;
};

type SuiteRun = {
  id: number;
  suite: string;
  score: number;
  passed: boolean;
  evidence: string;
  run_at: string | null;
};

type Tick = {
  id: number;
  event_id: string;
  attention_action: string;
  local_model_used: boolean;
  escalated_runtime: string | null;
  summary: string;
  score: number;
  created_at: string | null;
};

type QueueItem = {
  id: number;
  status: string;
  canary_mode: boolean;
  nexus_review_required: boolean;
  candidate_reason: string;
  created_at: string | null;
};

type Outcome = {
  id: number;
  outcome_type: string;
  metric_delta: number;
  benchmark_score: number;
  regression_count: number;
  promotion_decision: string;
  guardrail_candidate: boolean;
  regression_test_candidate: boolean;
  status: string;
  created_at: string | null;
};

type Schedule = {
  cadence: string;
  next_run_at: string | null;
  benchmark_suite: string;
  status: string;
  nexus_review_required: boolean;
};

type AwarenessDashboard = {
  agent: string;
  generated_at: string | null;
  summary: Summary;
  state: AwarenessState | null;
  latest_suites: SuiteRun[];
  ticks: Tick[];
  adapter_queue: QueueItem[];
  closed_loop: {
    outcomes: Outcome[];
    schedule: Schedule | null;
  };
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

const tone = (ok: boolean) => ok ? "var(--seal-success)" : "var(--seal-error)";

function Metric({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="card">
      <p className="text-[10px] uppercase mb-1" style={{ color: "var(--seal-text-dim)" }}>{label}</p>
      <p className="text-xl font-semibold" style={{ color: color || "var(--seal-text)" }}>{value}</p>
    </div>
  );
}

export default function AwarenessDashboardSection({ agent }: { agent: string }) {
  const [data, setData] = useState<AwarenessDashboard | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch(`/api/soul/awareness_dashboard?agent=${agent}`)
        .then((r) => r.json())
        .then((payload) => {
          if (!cancelled) {
            setData(payload);
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

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando awareness...</p>;
  if (!data) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin datos de awareness.</p>;

  const s = data.summary;
  const suitesGreen = s.failing_suites === 0 && s.suite_count > 0;

  return (
    <div className="max-w-6xl space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Awareness — {agent}</h2>
          <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Actualizado {fmtDate(data.generated_at)}</p>
        </div>
        <span className="pill" style={{ background: "var(--seal-bg)", color: tone(suitesGreen) }}>
          {suitesGreen ? "contrato verde" : "revisar"}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        <Metric label="Suites" value={`${s.passing_suites}/${s.suite_count}`} color={tone(suitesGreen)} />
        <Metric label="Ticks" value={s.recent_ticks} color="var(--seal-accent)" />
        <Metric label="Queue" value={s.queue_pending} color={s.queue_pending ? "var(--seal-warning)" : "var(--seal-success)"} />
        <Metric label="Outcomes" value={s.closed_loop_outcomes} color="var(--soul-purple)" />
        <Metric label="Promote" value={s.promote_pending} color="var(--seal-success)" />
        <Metric label="Rollback" value={s.rollback_pending} color={s.rollback_pending ? "var(--seal-error)" : "var(--seal-success)"} />
        <Metric label="Guardrails" value={s.guardrail_candidates} color="var(--seal-warning)" />
        <Metric label="Tests" value={s.regression_tests} color="var(--seal-accent)" />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_2fr] gap-4">
        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Runtime State</h3>
          {data.state ? (
            <div className="space-y-2 text-xs">
              <div className="grid grid-cols-[110px_1fr] gap-2">
                <span style={{ color: "var(--seal-text-dim)" }}>Estado</span>
                <span style={{ color: "var(--seal-text)" }}>{data.state.state}</span>
              </div>
              <div className="grid grid-cols-[110px_1fr] gap-2">
                <span style={{ color: "var(--seal-text-dim)" }}>Budget</span>
                <span style={{ color: "var(--seal-text)" }}>{data.state.budget_class || "—"}</span>
              </div>
              <div className="grid grid-cols-[110px_1fr] gap-2">
                <span style={{ color: "var(--seal-text-dim)" }}>Local</span>
                <span style={{ color: "var(--seal-text)" }}>{data.state.local_runtime_status || "—"}</span>
              </div>
              <div className="grid grid-cols-[110px_1fr] gap-2">
                <span style={{ color: "var(--seal-text-dim)" }}>Cortex</span>
                <span style={{ color: "var(--seal-text)" }}>{data.state.big_cortex_status || "—"}</span>
              </div>
              <p className="pt-2 break-words" style={{ color: "var(--seal-text-dim)" }}>{data.state.current_focus || "sin foco persistido"}</p>
            </div>
          ) : (
            <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Sin loop productivo persistiendo estado para {agent}.</p>
          )}
        </div>

        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Awareness Suites</h3>
            <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>{fmtDate(s.latest_run_at)}</span>
          </div>
          <div className="space-y-2">
            {data.latest_suites.map((run) => (
              <div key={run.id} className="grid grid-cols-[72px_1fr_48px] gap-2 text-xs items-start">
                <span style={{ color: tone(run.passed) }}>{run.passed ? "passed" : "failed"}</span>
                <div>
                  <p className="font-medium" style={{ color: "var(--seal-text)" }}>{run.suite}</p>
                  <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>{run.evidence}</p>
                </div>
                <span className="text-right" style={{ color: run.score >= 90 ? "var(--seal-success)" : "var(--seal-warning)" }}>{run.score}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Recent Ticks</h3>
          {data.ticks.length ? data.ticks.slice(0, 8).map((tick) => (
            <div key={tick.id} className="mb-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span style={{ color: "var(--seal-accent)" }}>{tick.attention_action}</span>
                <span style={{ color: "var(--seal-text-dim)" }}>{fmtDate(tick.created_at)}</span>
              </div>
              <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>{tick.summary}</p>
            </div>
          )) : (
            <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Sin ticks productivos para este agente.</p>
          )}
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Adapter Queue</h3>
          {data.adapter_queue.length ? data.adapter_queue.slice(0, 8).map((item) => (
            <div key={item.id} className="mb-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span style={{ color: "var(--seal-warning)" }}>{item.status}</span>
                <span style={{ color: item.canary_mode ? "var(--seal-success)" : "var(--seal-error)" }}>
                  {item.canary_mode ? "canary" : "direct"}
                </span>
              </div>
              <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>{item.candidate_reason}</p>
            </div>
          )) : (
            <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Sin adapters pendientes para {agent}.</p>
          )}
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--seal-text)" }}>Closed Loop</h3>
          {data.closed_loop.schedule && (
            <div className="mb-3 text-xs">
              <p style={{ color: "var(--seal-text)" }}>{data.closed_loop.schedule.benchmark_suite}</p>
              <p style={{ color: "var(--seal-text-dim)" }}>{data.closed_loop.schedule.cadence} · {fmtDate(data.closed_loop.schedule.next_run_at)}</p>
            </div>
          )}
          {data.closed_loop.outcomes.length ? data.closed_loop.outcomes.slice(0, 6).map((outcome) => (
            <div key={outcome.id} className="mb-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span style={{ color: outcome.promotion_decision.includes("rollback") ? "var(--seal-error)" : "var(--seal-success)" }}>
                  {outcome.promotion_decision}
                </span>
                <span style={{ color: "var(--seal-text-dim)" }}>{outcome.benchmark_score.toFixed(2)}</span>
              </div>
              <p style={{ color: "var(--seal-text-dim)" }}>{outcome.outcome_type} · regressions {outcome.regression_count}</p>
            </div>
          )) : (
            <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Sin outcomes productivos para {agent}.</p>
          )}
        </div>
      </div>
    </div>
  );
}
