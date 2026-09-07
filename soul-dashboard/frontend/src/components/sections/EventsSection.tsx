import { useState, useEffect } from "react";

type Event = {
  id: number;
  type: string;
  content: string;
  metadata: Record<string, unknown> | null;
  at: string;
};

const TYPE_COLORS: Record<string, string> = {
  memory_stored: "#8b5cf6",
  memory_recalled: "#3b82f6",
  thought_added: "#f59e0b",
  diary_written: "#22c55e",
  goal_updated: "#14b8a6",
  instinct_fired: "#ef4444",
  ocean_updated: "#ec4899",
};

const typeColor = (t: string) => {
  for (const [k, v] of Object.entries(TYPE_COLORS)) {
    if (t?.toLowerCase().includes(k.split("_")[0])) return v;
  }
  return "var(--seal-text-dim)";
};

export default function EventsSection({ agent }: { agent: string }) {
  const [events, setEvents] = useState<Event[]>([]);
  const [types, setTypes] = useState<{ type: string; count: number }[]>([]);
  const [filterType, setFilterType] = useState("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ agent, limit: "50" });
      if (filterType) params.set("event_type", filterType);
      const [er, tr] = await Promise.all([
        fetch(`/api/soul/events?${params}`),
        fetch(`/api/soul/events/types?agent=${agent}`),
      ]);
      if (er.ok) setEvents(await er.json());
      if (tr.ok) setTypes(await tr.json());
    } catch {}
    setLoading(false);
  };

  useEffect(() => { load(); }, [agent, filterType]);

  return (
    <div className="max-w-3xl space-y-4">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Eventos — {agent}</h2>

      {/* Type filter */}
      <div className="flex flex-wrap gap-1">
        <button
          className="pill cursor-pointer"
          style={{
            background: !filterType ? "var(--soul-purple)" : "var(--seal-surface)",
            color: !filterType ? "white" : "var(--seal-text-dim)",
          }}
          onClick={() => setFilterType("")}
        >
          Todos ({types.reduce((a, t) => a + t.count, 0)})
        </button>
        {types.slice(0, 8).map((t) => (
          <button
            key={t.type}
            className="pill cursor-pointer"
            style={{
              background: filterType === t.type ? typeColor(t.type) : "var(--seal-surface)",
              color: filterType === t.type ? "white" : "var(--seal-text-dim)",
            }}
            onClick={() => setFilterType(filterType === t.type ? "" : t.type)}
          >
            {t.type} ({t.count})
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando eventos...</p>
      ) : (
        <div className="space-y-1.5">
          {events.map((e) => (
            <div
              key={e.id}
              className="flex items-start gap-3 px-3 py-2 rounded"
              style={{ background: "var(--seal-surface)", borderLeft: `3px solid ${typeColor(e.type)}` }}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-[10px] font-semibold" style={{ color: typeColor(e.type) }}>{e.type}</span>
                  <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
                    {e.at ? new Date(e.at).toLocaleString("es-PE", { timeZone: "America/Lima" }) : ""}
                  </span>
                </div>
                <p className="text-xs leading-relaxed" style={{ color: "var(--seal-text)" }}>{e.content}</p>
              </div>
            </div>
          ))}
          {events.length === 0 && (
            <p className="text-sm text-center py-8" style={{ color: "var(--seal-text-dim)" }}>Sin eventos.</p>
          )}
        </div>
      )}
    </div>
  );
}
