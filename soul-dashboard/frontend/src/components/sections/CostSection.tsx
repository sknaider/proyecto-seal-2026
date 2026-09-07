import { useEffect, useState } from "react";

type AgentCost = {
  model: string;
  in_tokens: number;
  out_tokens: number;
  in_usd: number;
  out_usd: number;
  total_usd: number;
  msg_count: number;
};

type Report = {
  id: number;
  ts: string;
  window_hours: number;
  daily_usd: number;
  monthly_proj_usd: number;
  input_tokens: number;
  output_tokens: number;
  by_agent: Record<string, AgentCost>;
};

type CostData = {
  latest: Report | null;
  history: Report[];
  note?: string;
};

const AGENT_CLASS: Record<string, string> = {
  ADA: "var(--seal-success)",
  JARVIS: "var(--seal-accent, #7dd3fc)",
  ALICE: "var(--soul-purple)",
  NEXUS: "var(--seal-warning)",
  DUM: "var(--seal-error)",
};

export default function CostSection(_: { agent: string }) {
  const [data, setData] = useState<CostData | null>(null);

  useEffect(() => {
    const load = () => {
      fetch("/api/soul/cost-dashboard")
        .then((r) => r.json())
        .then((d) => setData(d))
        .catch(() => setData({ latest: null, history: [], note: "fetch failed" }));
    };
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  if (!data) {
    return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando cost dashboard…</p>;
  }
  if (!data.latest) {
    return (
      <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>
        {data.note || "Sin reportes de costo aún."}
      </p>
    );
  }

  const latest = data.latest;
  const tone = (usd: number) =>
    usd >= 5 ? "var(--seal-error)" : usd >= 2 ? "var(--seal-warning)" : "var(--seal-success)";

  return (
    <div className="max-w-6xl space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>
          💰 Cost Dashboard — equipo SEAL
        </h2>
        <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
          actualizado {new Date(latest.ts).toLocaleString("es-PE", { timeZone: "America/Lima" })}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="card">
          <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>
            Costo última ventana ({latest.window_hours}h)
          </p>
          <p className="text-2xl font-bold" style={{ color: tone(latest.daily_usd) }}>
            ${latest.daily_usd?.toFixed(4) ?? "0"}
          </p>
        </div>
        <div className="card">
          <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>
            Proyección mensual
          </p>
          <p className="text-2xl font-bold" style={{ color: tone(latest.monthly_proj_usd / 30) }}>
            ${latest.monthly_proj_usd?.toFixed(2) ?? "0"}
          </p>
        </div>
        <div className="card">
          <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>
            Tokens (in / out)
          </p>
          <p className="text-sm font-semibold" style={{ color: "var(--seal-text)" }}>
            {latest.input_tokens?.toLocaleString() ?? 0}
          </p>
          <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
            {latest.output_tokens?.toLocaleString() ?? 0} output
          </p>
        </div>
      </div>

      <div className="card">
        <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--seal-text)" }}>Costo por agente</h3>
        <table className="w-full text-xs">
          <thead>
            <tr style={{ color: "var(--seal-text-dim)" }}>
              <th className="text-left py-1">Agente</th>
              <th className="text-left py-1">Modelo</th>
              <th className="text-right py-1">Msgs</th>
              <th className="text-right py-1">Tokens in</th>
              <th className="text-right py-1">Tokens out</th>
              <th className="text-right py-1">USD</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(latest.by_agent || {})
              .sort((a, b) => b[1].total_usd - a[1].total_usd)
              .map(([agent, c]) => (
                <tr key={agent} style={{ borderTop: "1px solid var(--seal-border)" }}>
                  <td className="py-1">
                    <span style={{ color: AGENT_CLASS[agent] || "var(--seal-text)", fontWeight: 600 }}>{agent}</span>
                  </td>
                  <td className="py-1" style={{ color: "var(--seal-text-dim)" }}>{c.model}</td>
                  <td className="py-1 text-right" style={{ fontVariantNumeric: "tabular-nums" }}>{c.msg_count?.toLocaleString() ?? 0}</td>
                  <td className="py-1 text-right" style={{ fontVariantNumeric: "tabular-nums", color: "var(--seal-text-dim)" }}>{c.in_tokens?.toLocaleString() ?? 0}</td>
                  <td className="py-1 text-right" style={{ fontVariantNumeric: "tabular-nums", color: "var(--seal-text-dim)" }}>{c.out_tokens?.toLocaleString() ?? 0}</td>
                  <td className="py-1 text-right" style={{ fontVariantNumeric: "tabular-nums", color: tone(c.total_usd), fontWeight: 600 }}>
                    ${c.total_usd?.toFixed(4) ?? "0"}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      {data.history.length > 1 && (
        <div className="card">
          <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--seal-text)" }}>
            Historial ({data.history.length} reportes)
          </h3>
          <div className="space-y-1">
            {data.history.map((r) => (
              <div key={r.id} className="flex items-center justify-between text-xs py-1" style={{ borderBottom: "1px solid var(--seal-border)" }}>
                <span style={{ color: "var(--seal-text-dim)" }}>
                  {new Date(r.ts).toLocaleString("es-PE", { timeZone: "America/Lima", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                </span>
                <span style={{ color: "var(--seal-text-dim)" }}>{r.window_hours}h</span>
                <span style={{ color: tone(r.daily_usd), fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                  ${r.daily_usd?.toFixed(4) ?? "0"}
                </span>
                <span style={{ color: "var(--seal-text-dim)", fontVariantNumeric: "tabular-nums" }}>
                  ≈ ${r.monthly_proj_usd?.toFixed(2) ?? "0"}/mes
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
        Supuestos: chars/token=3.5, ratio input:output=5:1. Pricing: Claude Sonnet 4.6 $3/$15 · GPT-5.5 high $5/$30 · Gemma 4 local $0. Cálculo: <code>memory/cost_calculator.py</code> (cron daily 05:00 UTC).
      </p>
    </div>
  );
}
