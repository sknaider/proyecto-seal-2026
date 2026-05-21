import { useEffect, useState } from "react";

type ReviewSummary = {
  total: number;
  high: number;
  medium: number;
  low: number;
  adapter_candidates: number;
  closed_loop_outcomes: number;
  pending_validations: number;
  resolved_by_audit_decision: number;
  review_tasks: number;
  lifecycle_reviews: number;
  recent_decisions: number;
  by_kind: Record<string, number>;
};

type ReviewItem = {
  id: string;
  kind: string;
  source_table: string;
  source_id: number | null;
  agent: string;
  reviewer: string;
  status: string;
  priority: "high" | "medium" | "low";
  title: string;
  summary: string | null;
  risk: string;
  decision: string;
  evidence: Record<string, unknown>;
  created_at: string | null;
};

type ReviewQueue = {
  agent: string;
  reviewer: string;
  generated_at: string | null;
  summary: ReviewSummary;
  items: ReviewItem[];
  recent_decisions: ReviewDecisionRecord[];
  boundary: string;
};

type Decision = "approved" | "rejected" | "needs_evidence";

type EvidencePacket = {
  item_id: string;
  generated_at: string | null;
  packet: {
    source: Record<string, unknown>;
    evidence: Record<string, unknown>;
    related: Record<string, unknown>;
    decisions: ReviewDecisionRecord[];
    diff: {
      decision: string;
      current: Record<string, unknown>;
      proposed: Record<string, unknown>;
      changed_fields: string[];
      source_mutation: boolean;
      requires_william: boolean;
      boundary: string;
    };
    timeline: {
      summary: Record<string, number>;
      events: {
        type: string;
        at: string | null;
        title: string;
        details: Record<string, unknown>;
      }[];
      boundary: string;
    };
    policy: Record<string, unknown>;
    recommended_next_action: string;
  };
  boundary: string;
};

type DebtAlert = {
  severity: "high" | "medium" | "low";
  kind: string;
  item_id: string;
  title: string;
  created_at: string | null;
  details: Record<string, unknown>;
};

type ReviewDecisionRecord = {
  id: number;
  item_id: string;
  source_table: string;
  source_id: number | null;
  agent: string;
  reviewer: string;
  actor: string;
  decision: string;
  rationale: string;
  applied: boolean;
  created_at: string | null;
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

const priorityColor = (priority: string) => {
  if (priority === "high") return "var(--seal-error)";
  if (priority === "medium") return "var(--seal-warning)";
  return "var(--seal-success)";
};

function Metric({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="card">
      <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>{label}</p>
      <p className="text-xl font-semibold" style={{ color: color || "var(--seal-text)" }}>{value}</p>
    </div>
  );
}

export default function NexusReviewQueueSection({ agent }: { agent: string }) {
  const [data, setData] = useState<ReviewQueue | null>(null);
  const [loading, setLoading] = useState(true);
  const [rationales, setRationales] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [packet, setPacket] = useState<EvidencePacket | null>(null);
  const [policy, setPolicy] = useState<Record<string, unknown> | null>(null);
  const [worker, setWorker] = useState<Record<string, unknown> | null>(null);
  const [alerts, setAlerts] = useState<{ summary: Record<string, number>; alerts: DebtAlert[] } | null>(null);
  const [williamReview, setWilliamReview] = useState<Record<string, unknown> | null>(null);
  const [packetLoading, setPacketLoading] = useState<string | null>(null);

  const load = () => {
    fetch(`/api/soul/nexus_review_queue?agent=${agent}&reviewer=NEXUS`)
      .then((r) => r.json())
      .then((payload) => {
        setData(payload);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
    fetch(`/api/soul/nexus_review_queue/policy_gates?agent=${agent}&reviewer=NEXUS`)
      .then((r) => r.json())
      .then((payload) => setPolicy(payload.summary || payload))
      .catch(() => setPolicy(null));
    fetch(`/api/soul/nexus_review_queue/alerts?agent=${agent}&reviewer=NEXUS`)
      .then((r) => r.json())
      .then((payload) => setAlerts(payload))
      .catch(() => setAlerts(null));
    fetch(`/api/soul/nexus_review_queue/william_review?agent=${agent}&reviewer=NEXUS`)
      .then((r) => r.json())
      .then((payload) => setWilliamReview(payload.summary || payload))
      .catch(() => setWilliamReview(null));
  };

  useEffect(() => {
    setLoading(true);
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [agent]);

  const decide = (item: ReviewItem, decision: Decision) => {
    const rationale = (rationales[item.id] || "").trim();
    if (rationale.length < 8) {
      setNotice("Rationale requerido para registrar decision.");
      return;
    }
    setSaving(`${item.id}:${decision}`);
    setNotice(null);
    fetch("/api/soul/nexus_review_queue/decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: item.id,
        decision,
        rationale,
        reviewer: "NEXUS",
        actor: agent,
        evidence: { ui: "nexus_review_queue" },
      }),
    })
      .then(async (r) => {
        const payload = await r.json();
        if (!r.ok) throw new Error(payload.detail || "decision failed");
        setRationales((prev) => ({ ...prev, [item.id]: "" }));
        setNotice(`Decision ${payload.decision} registrada para ${item.id}.`);
        load();
      })
      .catch((err) => setNotice(String(err.message || err)))
      .finally(() => setSaving(null));
  };

  const loadPacket = (itemId: string) => {
    setPacketLoading(itemId);
    setNotice(null);
    fetch(`/api/soul/nexus_review_queue/evidence_packet?agent=${agent}&reviewer=NEXUS&item_id=${encodeURIComponent(itemId)}`)
      .then(async (r) => {
        const payload = await r.json();
        if (!r.ok) throw new Error(payload.detail || "packet failed");
        setPacket(payload);
      })
      .catch((err) => setNotice(String(err.message || err)))
      .finally(() => setPacketLoading(null));
  };

  const runWorker = (dryRun = true) => {
    setSaving(dryRun ? "worker:dry" : "worker:apply");
    setNotice(null);
    fetch("/api/soul/nexus_review_queue/decision_worker", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent, reviewer: "NEXUS", dry_run: dryRun, limit: 60 }),
    })
      .then(async (r) => {
        const payload = await r.json();
        if (!r.ok) throw new Error(payload.detail || "worker failed");
        setWorker(payload);
        setNotice(`${dryRun ? "Dry run" : "Worker"}: applied=${payload.applied_count} skipped=${payload.skipped_count}.`);
        load();
      })
      .catch((err) => setNotice(String(err.message || err)))
      .finally(() => setSaving(null));
  };

  const requestRollback = (itemId: string, dryRun = true) => {
    setSaving(`rollback:${itemId}:${dryRun ? "dry" : "apply"}`);
    setNotice(null);
    fetch("/api/soul/nexus_review_queue/rollback_request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: itemId,
        agent,
        reviewer: "NEXUS",
        actor: agent,
        dry_run: dryRun,
        reason: "Rollback review requested from NEXUS dashboard with packet evidence.",
      }),
    })
      .then(async (r) => {
        const payload = await r.json();
        if (!r.ok) throw new Error(payload.detail || "rollback request failed");
        setNotice(`${dryRun ? "Rollback dry-run" : "Rollback request"} listo para ${itemId}; status=${payload.status}.`);
        load();
      })
      .catch((err) => setNotice(String(err.message || err)))
      .finally(() => setSaving(null));
  };

  const actionStyle = (kind: "approve" | "reject" | "evidence") => {
    if (kind === "approve") return { borderColor: "var(--seal-success)", color: "var(--seal-success)" };
    if (kind === "reject") return { borderColor: "var(--seal-error)", color: "var(--seal-error)" };
    return { borderColor: "var(--seal-warning)", color: "var(--seal-warning)" };
  };

  const ActionButton = ({
    item,
    decision,
    label,
    kind,
  }: {
    item: ReviewItem;
    decision: Decision;
    label: string;
    kind: "approve" | "reject" | "evidence";
  }) => {
    const key = `${item.id}:${decision}`;
    const disabled = saving !== null || (rationales[item.id] || "").trim().length < 8;
    return (
      <button
        type="button"
        className="px-2 py-1 rounded text-[10px] font-semibold border disabled:opacity-40 disabled:cursor-not-allowed"
        style={actionStyle(kind)}
        disabled={disabled}
        onClick={() => decide(item, decision)}
      >
        {saving === key ? "..." : label}
      </button>
    );
  };

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando NEXUS review queue...</p>;
  if (!data) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin cola de revisión.</p>;

  const s = data.summary;

  return (
    <div className="max-w-6xl space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>NEXUS Review Queue</h2>
          <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
            {data.reviewer} revisa {data.agent} · actualizado {fmtDate(data.generated_at)}
          </p>
        </div>
        <span className="pill" style={{ background: "var(--seal-bg)", color: s.high ? "var(--seal-error)" : "var(--seal-warning)" }}>
          {s.total} pendientes
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        <Metric label="Total" value={s.total} color={s.total ? "var(--seal-warning)" : "var(--seal-success)"} />
        <Metric label="High" value={s.high} color={s.high ? "var(--seal-error)" : "var(--seal-success)"} />
        <Metric label="Medium" value={s.medium} color="var(--seal-warning)" />
        <Metric label="Adapters" value={s.adapter_candidates} color="var(--seal-accent)" />
        <Metric label="Outcomes" value={s.closed_loop_outcomes} color="var(--soul-purple)" />
        <Metric label="Validations" value={s.pending_validations} color={s.pending_validations ? "var(--seal-warning)" : "var(--seal-success)"} />
        <Metric label="Resolved" value={s.resolved_by_audit_decision || 0} color="var(--seal-success)" />
        <Metric label="Tasks" value={s.review_tasks} color="var(--seal-text)" />
      </div>

      {notice ? (
        <div className="card text-xs" style={{ color: notice.includes("registrada") ? "var(--seal-success)" : "var(--seal-warning)" }}>
          {notice}
        </div>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-3">
        <div className="card">
          <div className="flex items-center justify-between gap-3 mb-2">
            <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Evidence Packet</h3>
            <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
              {packet ? packet.boundary : "read-only"}
            </span>
          </div>
          {packet ? (
            <div className="space-y-3 text-xs">
              <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
              <div>
                <p className="font-semibold mb-1" style={{ color: "var(--seal-text)" }}>{packet.item_id}</p>
                <p style={{ color: "var(--seal-text-dim)" }}>{packet.packet.recommended_next_action}</p>
                <div className="flex flex-wrap gap-2 mt-2">
                  <button
                    type="button"
                    className="px-2 py-1 rounded border text-[10px] font-semibold"
                    style={{ borderColor: "var(--seal-border)", color: "var(--seal-text)" }}
                    disabled={saving !== null}
                    onClick={() => requestRollback(packet.item_id, true)}
                  >
                    Rollback Dry
                  </button>
                  <button
                    type="button"
                    className="px-2 py-1 rounded border text-[10px] font-semibold"
                    style={{ borderColor: "var(--seal-warning)", color: "var(--seal-warning)" }}
                    disabled={saving !== null}
                    onClick={() => requestRollback(packet.item_id, false)}
                  >
                    Request Rollback
                  </button>
                </div>
                <pre className="mt-2 max-h-48 overflow-auto rounded border p-2 text-[10px]" style={{ borderColor: "var(--seal-border)", color: "var(--seal-text-dim)" }}>
                  {JSON.stringify(packet.packet.evidence, null, 2)}
                </pre>
              </div>
              <pre className="max-h-56 overflow-auto rounded border p-2 text-[10px]" style={{ borderColor: "var(--seal-border)", color: "var(--seal-text-dim)" }}>
                {JSON.stringify(packet.packet.source, null, 2)}
              </pre>
              <pre className="max-h-56 overflow-auto rounded border p-2 text-[10px]" style={{ borderColor: "var(--seal-border)", color: "var(--seal-text-dim)" }}>
                {JSON.stringify({ related: packet.packet.related, policy: packet.packet.policy, decisions: packet.packet.decisions.slice(0, 3) }, null, 2)}
              </pre>
              </div>
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                <div className="rounded border p-2" style={{ borderColor: "var(--seal-border)" }}>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <p className="font-semibold" style={{ color: "var(--seal-text)" }}>Diff Preview</p>
                    <span style={{ color: packet.packet.diff.requires_william ? "var(--seal-warning)" : "var(--seal-success)" }}>
                      {packet.packet.diff.requires_william ? "William gate" : "NEXUS gate"}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <pre className="max-h-44 overflow-auto rounded border p-2 text-[10px]" style={{ borderColor: "var(--seal-border)", color: "var(--seal-text-dim)" }}>
                      {JSON.stringify({ current: packet.packet.diff.current }, null, 2)}
                    </pre>
                    <pre className="max-h-44 overflow-auto rounded border p-2 text-[10px]" style={{ borderColor: "var(--seal-border)", color: "var(--seal-text-dim)" }}>
                      {JSON.stringify({ proposed: packet.packet.diff.proposed }, null, 2)}
                    </pre>
                  </div>
                </div>
                <div className="rounded border p-2" style={{ borderColor: "var(--seal-border)" }}>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <p className="font-semibold" style={{ color: "var(--seal-text)" }}>Timeline</p>
                    <span style={{ color: "var(--seal-text-dim)" }}>{String(packet.packet.timeline.summary.events || 0)} events</span>
                  </div>
                  <div className="max-h-48 overflow-auto space-y-2">
                    {packet.packet.timeline.events.length ? packet.packet.timeline.events.map((event, idx) => (
                      <div key={`${event.type}-${idx}`} className="border-b pb-1" style={{ borderColor: "var(--seal-border)" }}>
                        <div className="flex items-center justify-between gap-2">
                          <span style={{ color: "var(--seal-text)" }}>{event.type}</span>
                          <span style={{ color: "var(--seal-text-dim)" }}>{fmtDate(event.at)}</span>
                        </div>
                        <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>{event.title}</p>
                      </div>
                    )) : (
                      <p style={{ color: "var(--seal-text-dim)" }}>Sin eventos.</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Abre un packet desde pendientes o decisiones recientes.</p>
          )}
        </div>
        <div className="card text-xs">
          <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--seal-text)" }}>Policy Gates</h3>
          <div className="grid grid-cols-2 gap-2 mb-3">
            <div className="rounded border p-2" style={{ borderColor: "var(--seal-border)" }}>
              <p className="text-[10px] uppercase" style={{ color: "var(--seal-text-dim)" }}>NEXUS OK</p>
              <p className="text-lg font-semibold" style={{ color: "var(--seal-success)" }}>{String(policy?.nexus_allowed ?? 0)}</p>
            </div>
            <div className="rounded border p-2" style={{ borderColor: "var(--seal-border)" }}>
              <p className="text-[10px] uppercase" style={{ color: "var(--seal-text-dim)" }}>William</p>
              <p className="text-lg font-semibold" style={{ color: "var(--seal-warning)" }}>{String(policy?.william_required ?? 0)}</p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 mb-3">
            <div className="rounded border p-2" style={{ borderColor: "var(--seal-border)" }}>
              <p className="text-[10px] uppercase" style={{ color: "var(--seal-text-dim)" }}>Debt</p>
              <p className="text-lg font-semibold" style={{ color: (alerts?.summary?.total || 0) ? "var(--seal-warning)" : "var(--seal-success)" }}>{String(alerts?.summary?.total ?? 0)}</p>
            </div>
            <div className="rounded border p-2" style={{ borderColor: "var(--seal-border)" }}>
              <p className="text-[10px] uppercase" style={{ color: "var(--seal-text-dim)" }}>William</p>
              <p className="text-lg font-semibold" style={{ color: (Number(williamReview?.total || 0)) ? "var(--seal-warning)" : "var(--seal-success)" }}>{String(williamReview?.total ?? 0)}</p>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className="px-2 py-1 rounded border text-[10px] font-semibold disabled:opacity-40"
              style={{ borderColor: "var(--seal-border)", color: "var(--seal-text)" }}
              disabled={saving !== null}
              onClick={() => runWorker(true)}
            >
              {saving === "worker:dry" ? "..." : "Dry Worker"}
            </button>
            <button
              type="button"
              className="px-2 py-1 rounded border text-[10px] font-semibold disabled:opacity-40"
              style={{ borderColor: "var(--seal-success)", color: "var(--seal-success)" }}
              disabled={saving !== null}
              onClick={() => runWorker(false)}
            >
              {saving === "worker:apply" ? "..." : "Apply Worker"}
            </button>
          </div>
          {worker ? (
            <pre className="mt-2 max-h-32 overflow-auto rounded border p-2 text-[10px]" style={{ borderColor: "var(--seal-border)", color: "var(--seal-text-dim)" }}>
              {JSON.stringify(worker, null, 2)}
            </pre>
          ) : null}
        </div>
      </div>

      {(data.recent_decisions || []).length ? (
        <div className="card">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Recent Decisions</h3>
            <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>{data.recent_decisions.length}</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {data.recent_decisions.map((decision) => (
              <div key={decision.id} className="border rounded p-2 text-xs" style={{ borderColor: "var(--seal-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold" style={{ color: priorityColor(decision.decision === "rejected" ? "high" : "medium") }}>
                    {decision.decision}
                  </span>
                  <span style={{ color: "var(--seal-text-dim)" }}>{fmtDate(decision.created_at)}</span>
                </div>
                <p className="break-words" style={{ color: "var(--seal-text)" }}>{decision.item_id}</p>
                <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>{decision.rationale}</p>
                <div className="flex items-center justify-between gap-2">
                  <p style={{ color: "var(--seal-text-dim)" }}>{decision.actor} · applied={String(decision.applied)}</p>
                  <button
                    type="button"
                    className="px-2 py-0.5 rounded border text-[10px]"
                    style={{ borderColor: "var(--seal-border)", color: "var(--seal-text)" }}
                    onClick={() => loadPacket(decision.item_id)}
                    disabled={packetLoading === decision.item_id}
                  >
                    {packetLoading === decision.item_id ? "..." : "Packet"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {alerts?.alerts?.length ? (
        <div className="card">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Debt Alerts</h3>
            <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>{alerts.summary.total}</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {alerts.alerts.slice(0, 8).map((alert, idx) => (
              <div key={`${alert.item_id}-${alert.kind}-${idx}`} className="border rounded p-2 text-xs" style={{ borderColor: "var(--seal-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold" style={{ color: priorityColor(alert.severity === "high" ? "high" : alert.severity === "medium" ? "medium" : "low") }}>
                    {alert.severity}
                  </span>
                  <span style={{ color: "var(--seal-text-dim)" }}>{fmtDate(alert.created_at)}</span>
                </div>
                <p style={{ color: "var(--seal-text)" }}>{alert.title}</p>
                <button
                  type="button"
                  className="mt-1 px-2 py-0.5 rounded border text-[10px]"
                  style={{ borderColor: "var(--seal-border)", color: "var(--seal-text)" }}
                  onClick={() => loadPacket(alert.item_id)}
                >
                  Packet
                </button>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="card">
        <div className="flex items-center justify-between gap-3 mb-3">
          <h3 className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>Pending Decisions</h3>
          <span className="text-[10px] text-right" style={{ color: "var(--seal-text-dim)" }}>{data.boundary}</span>
        </div>
        <div className="space-y-3">
          {data.items.length ? data.items.map((item) => (
            <div
              key={item.id}
              className="grid grid-cols-1 lg:grid-cols-[86px_120px_1fr_260px] gap-3 text-xs items-start border-b pb-3"
              style={{ borderColor: "var(--seal-border)" }}
            >
              <span className="font-semibold" style={{ color: priorityColor(item.priority) }}>{item.priority}</span>
              <div>
                <p style={{ color: "var(--seal-text)" }}>{item.kind}</p>
                <p style={{ color: "var(--seal-text-dim)" }}>{item.status}</p>
              </div>
              <div className="min-w-0">
                <p className="font-medium break-words" style={{ color: "var(--seal-text)" }}>{item.title}</p>
                <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>{item.summary || item.source_table}</p>
                <p className="break-words" style={{ color: "var(--seal-text-dim)" }}>
                  {item.source_table}{item.source_id ? ` #${item.source_id}` : ""} · risk={item.risk} · {item.decision} · {fmtDate(item.created_at)}
                </p>
              </div>
              <div className="space-y-2">
                <textarea
                  className="w-full min-h-[58px] rounded border bg-transparent px-2 py-1 text-xs outline-none"
                  style={{ borderColor: "var(--seal-border)", color: "var(--seal-text)" }}
                  placeholder="Rationale"
                  value={rationales[item.id] || ""}
                  onChange={(e) => setRationales((prev) => ({ ...prev, [item.id]: e.target.value }))}
                />
                <div className="flex flex-wrap gap-2 justify-start lg:justify-end">
                  <button
                    type="button"
                    className="px-2 py-1 rounded text-[10px] font-semibold border"
                    style={{ borderColor: "var(--seal-border)", color: "var(--seal-text)" }}
                    onClick={() => loadPacket(item.id)}
                    disabled={packetLoading === item.id}
                  >
                    {packetLoading === item.id ? "..." : "Packet"}
                  </button>
                  <ActionButton item={item} decision="approved" label="Approve" kind="approve" />
                  <ActionButton item={item} decision="needs_evidence" label="Evidence" kind="evidence" />
                  <ActionButton item={item} decision="rejected" label="Reject" kind="reject" />
                </div>
              </div>
            </div>
          )) : (
            <p className="text-xs" style={{ color: "var(--seal-success)" }}>Sin revisiones pendientes.</p>
          )}
        </div>
      </div>
    </div>
  );
}
