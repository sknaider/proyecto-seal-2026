import { useState, useEffect } from "react";

type EmotionData = {
  agent: string;
  current_emotion: string | null;
  agent_state: string | null;
  last_mood: string | null;
  mood_date: string | null;
  motivation: { tank: string; value: number; last_fired: string | null; fire_count: number }[];
  updated_at: string | null;
};

const TANK_COLORS: Record<string, string> = {
  curiosity: "#8b5cf6",
  care: "#22c55e",
  pride: "#f59e0b",
  security: "#3b82f6",
  meaning: "#ec4899",
  rest: "#6366f1",
  autonomy: "#14b8a6",
  recognition: "#f97316",
};

const tankColor = (tank: string) => TANK_COLORS[tank.toLowerCase()] || "var(--seal-accent)";

export default function EmotionsSection({ agent }: { agent: string }) {
  const [data, setData] = useState<EmotionData | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/soul/emotions?agent=${agent}`);
      if (r.ok) setData(await r.json());
    } catch {}
    setLoading(false);
  };

  useEffect(() => { load(); }, [agent]);

  if (loading) return <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando estado emocional...</p>;
  if (!data) return <p className="text-sm" style={{ color: "var(--seal-error)" }}>Sin datos.</p>;

  return (
    <div className="max-w-2xl space-y-5">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Emociones — {agent}</h2>

      {/* Current state */}
      <div className="card">
        <div className="flex flex-wrap gap-3 items-center">
          {data.agent_state && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>Estado</p>
              <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--soul-purple)", fontSize: "0.8rem" }}>
                {data.agent_state}
              </span>
            </div>
          )}
          {data.current_emotion && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>Emoción</p>
              <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-warning)", fontSize: "0.8rem" }}>
                {data.current_emotion}
              </span>
            </div>
          )}
          {data.last_mood && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--seal-text-dim)" }}>Mood diario</p>
              <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-success)", fontSize: "0.8rem" }}>
                {data.last_mood}
              </span>
              {data.mood_date && (
                <p className="text-[9px] mt-0.5" style={{ color: "var(--seal-text-dim)" }}>{data.mood_date}</p>
              )}
            </div>
          )}
        </div>
        {data.updated_at && (
          <p className="text-[10px] mt-3" style={{ color: "var(--seal-text-dim)" }}>
            Actualizado: {new Date(data.updated_at).toLocaleString("es-PE", { timeZone: "America/Lima" })}
          </p>
        )}
      </div>

      {/* Motivation tanks */}
      {data.motivation.length > 0 && (
        <div className="card">
          <p className="text-xs font-semibold mb-4" style={{ color: "var(--seal-text-dim)" }}>Tanques de motivación</p>
          <div className="space-y-3">
            {data.motivation.map((m) => {
              const pct = Math.round(Math.max(0, Math.min(1, m.value)) * 100);
              const color = tankColor(m.tank);
              return (
                <div key={m.tank}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium capitalize" style={{ color: "var(--seal-text)" }}>{m.tank}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--seal-bg)", color: "var(--seal-text-dim)" }}>
                        {m.fire_count} disparos
                      </span>
                    </div>
                    <span className="text-sm font-bold" style={{ color }}>{pct}%</span>
                  </div>
                  <div className="relative h-2 rounded-full overflow-hidden" style={{ background: "var(--seal-bg)" }}>
                    <div
                      className="absolute inset-y-0 left-0 rounded-full transition-all duration-500"
                      style={{ width: `${pct}%`, background: color }}
                    />
                  </div>
                  {m.last_fired && (
                    <p className="text-[9px] mt-0.5" style={{ color: "var(--seal-text-dim)" }}>
                      Último disparo: {new Date(m.last_fired).toLocaleString("es-PE", { timeZone: "America/Lima" })}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {data.motivation.length === 0 && (
        <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Sin datos de motivación para {agent}.</p>
      )}
    </div>
  );
}
