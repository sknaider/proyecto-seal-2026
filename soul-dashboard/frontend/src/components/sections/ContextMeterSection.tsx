import { useEffect, useState } from "react";

type AgentMeter = {
  agent: string;
  pct: number;
  tokens: string;
  limit: string;
  age: string;
};

type ContextMeterData = {
  agents: AgentMeter[];
  error?: string;
};

export default function ContextMeterSection(_: { agent: string }) {
  const [data, setData] = useState<ContextMeterData | null>(null);

  useEffect(() => {
    const load = () => {
      fetch("/api/soul/context-meter")
        .then((r) => r.json())
        .then((d) => setData(d))
        .catch(() => setData({ agents: [], error: "fetch failed" }));
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  if (!data) {
    return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando context meter…</p>;
  }
  if (data.error) {
    return <p className="text-sm" style={{ color: "var(--seal-error)" }}>Error: {data.error}</p>;
  }

  const toneFor = (pct: number) => {
    if (pct >= 90) return "var(--seal-error)";
    if (pct >= 70) return "var(--seal-warning)";
    return "var(--seal-success)";
  };

  return (
    <div className="max-w-5xl space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>
          Context Meter — tokens consumidos por agente
        </h2>
        <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>auto-refresh 15s</span>
      </div>
      <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
        Rescatado de :8768 por ALICE — muestra cuánto contexto consumió cada agente. Cuando llega a 90%+ se acerca la compactación.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {data.agents.map((a) => (
          <div key={a.agent} className="card">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold" style={{ color: "var(--soul-purple)" }}>{a.agent}</h3>
              <span className="pill" style={{ background: "var(--seal-bg)", color: toneFor(a.pct), fontWeight: 600 }}>
                {a.pct.toFixed(0)}%
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs mb-2">
              <div>
                <p style={{ color: "var(--seal-text-dim)", fontSize: "10px", textTransform: "uppercase", letterSpacing: "0.05em" }}>tokens</p>
                <p style={{ color: "var(--seal-text)", fontWeight: 600 }}>{a.tokens}</p>
              </div>
              <div>
                <p style={{ color: "var(--seal-text-dim)", fontSize: "10px", textTransform: "uppercase", letterSpacing: "0.05em" }}>limit</p>
                <p style={{ color: "var(--seal-text-dim)" }}>{a.limit}</p>
              </div>
              <div>
                <p style={{ color: "var(--seal-text-dim)", fontSize: "10px", textTransform: "uppercase", letterSpacing: "0.05em" }}>age</p>
                <p style={{ color: "var(--seal-text-dim)" }}>{a.age}</p>
              </div>
            </div>
            <div style={{ width: "100%", height: "4px", background: "var(--seal-bg)", borderRadius: "2px", overflow: "hidden" }}>
              <div style={{
                width: `${Math.min(100, a.pct)}%`,
                height: "100%",
                background: toneFor(a.pct),
                transition: "width 0.3s",
              }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
