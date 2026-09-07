import { useState } from "react";

const AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS"];

type Result = { agent: string; ok: boolean; message: string; ts: number };

export default function AgentControlsSection(_: { agent: string }) {
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [results, setResults] = useState<Result[]>([]);

  const doAction = async (agent: string, action: string) => {
    const ok = confirm(`${action} ${agent}?`);
    if (!ok) return;
    setBusy({ ...busy, [`${agent}:${action}`]: true });
    try {
      let res: Response;
      if (action === "relaunch" || action === "sleep") {
        res = await fetch(`/api/agents/${action}/${agent}`, { method: "POST" });
      } else {
        res = await fetch(`/api/soul/agent-action`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agent, action }),
        });
      }
      const body = await res.json();
      const msg = body.message || (body.ok ? `${action} ok` : (body.error || body.detail || "error"));
      setResults((r) => [{ agent, ok: !!body.ok || res.ok, message: msg, ts: Date.now() }, ...r].slice(0, 12));
    } catch (e: any) {
      setResults((r) => [{ agent, ok: false, message: e?.message || "fetch failed", ts: Date.now() }, ...r].slice(0, 12));
    } finally {
      setBusy({ ...busy, [`${agent}:${action}`]: false });
    }
  };

  const btn = (agent: string, action: string, label: string, danger?: boolean) => {
    const key = `${agent}:${action}`;
    const isBusy = busy[key];
    return (
      <button
        key={key}
        disabled={isBusy}
        onClick={() => doAction(agent, action)}
        className="text-[11px] px-2 py-1 rounded border transition-colors"
        style={{
          borderColor: "var(--seal-border)",
          background: "var(--seal-bg)",
          color: danger ? "var(--seal-error)" : "var(--seal-text)",
          opacity: isBusy ? 0.5 : 1,
          cursor: isBusy ? "wait" : "pointer",
        }}
      >
        {isBusy ? "…" : label}
      </button>
    );
  };

  return (
    <div className="max-w-5xl space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Agent Controls</h2>
        <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>Rescatado de :8768</span>
      </div>
      <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
        Acciones operacionales sobre los agentes principales. Cada acción pide confirmación.
        <strong style={{ color: "var(--seal-warning)" }}> Cuidado:</strong> sleep + relaunch reinician sesión.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {AGENTS.map((agent) => (
          <div key={agent} className="card">
            <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--soul-purple)" }}>{agent}</h3>
            <div className="flex flex-wrap gap-1.5">
              {btn(agent, "pause", "⏸ pause")}
              {btn(agent, "resurrect", "▶ resurrect")}
              {btn(agent, "sleep", "💤 sleep+checkpoint", true)}
              {btn(agent, "relaunch", "🔄 relaunch", true)}
              {btn(agent, "reset_crashes", "↺ reset crashes")}
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--seal-text)" }}>Historial reciente</h3>
        {results.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Sin acciones ejecutadas en esta sesión.</p>
        ) : (
          <div className="space-y-1">
            {results.map((r, i) => (
              <div key={i} className="text-xs flex items-center gap-2" style={{ fontVariantNumeric: "tabular-nums" }}>
                <span style={{ color: "var(--seal-text-dim)", fontSize: "10px" }}>
                  {new Date(r.ts).toLocaleTimeString("es-PE", { timeZone: "America/Lima", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
                <span className="pill" style={{ background: r.ok ? "#064e3b" : "#450a0a", color: r.ok ? "var(--seal-success)" : "var(--seal-error)", fontSize: "10px" }}>
                  {r.agent}
                </span>
                <span style={{ color: r.ok ? "var(--seal-text)" : "var(--seal-error)" }}>{r.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
