import { useState, useEffect } from "react";

type Entry = {
  id: number;
  date: string;
  entry: string;
  mood: string;
  moments: Record<string, unknown> | null;
  created_at: string;
};

export default function DiarySection({ agent }: { agent: string }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Entry | null>(null);
  const [newEntry, setNewEntry] = useState("");
  const [newMood, setNewMood] = useState("");
  const [posting, setPosting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/soul/diary?agent=${agent}&limit=20`);
      if (r.ok) {
        const d = await r.json();
        setEntries(d);
        if (d.length > 0) setSelected(d[0]);
      }
    } catch {}
    setLoading(false);
  };

  useEffect(() => { setSelected(null); load(); }, [agent]);

  const submitEntry = async () => {
    if (!newEntry.trim()) return;
    setPosting(true);
    try {
      await fetch("/api/soul/diary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent, entry: newEntry.trim(), mood: newMood.trim() }),
      });
      setNewEntry("");
      setNewMood("");
      await load();
    } catch {}
    setPosting(false);
  };

  return (
    <div className="max-w-4xl">
      <h2 className="text-base font-bold mb-4" style={{ color: "var(--soul-purple)" }}>Diario — {agent}</h2>

      <div className="flex gap-4 h-[calc(100vh-200px)]">
        {/* Left: entries list */}
        <div className="w-48 flex-shrink-0 overflow-y-auto space-y-1">
          {loading ? (
            <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>Cargando...</p>
          ) : (
            entries.map((e) => (
              <button
                key={e.id}
                onClick={() => setSelected(e)}
                className="w-full text-left px-3 py-2 rounded text-xs transition-colors"
                style={{
                  background: selected?.id === e.id ? "var(--soul-purple-dim)" : "var(--seal-surface)",
                  color: selected?.id === e.id ? "var(--soul-purple)" : "var(--seal-text-dim)",
                  border: `1px solid ${selected?.id === e.id ? "var(--soul-purple)" : "var(--seal-border)"}`,
                }}
              >
                <p className="font-semibold">{e.date}</p>
                {e.mood && <p className="opacity-70 truncate">{e.mood}</p>}
                <p className="truncate opacity-60">{e.entry.slice(0, 40)}...</p>
              </button>
            ))
          )}
        </div>

        {/* Right: entry content + write */}
        <div className="flex-1 overflow-y-auto space-y-4">
          {selected && (
            <div className="card">
              <div className="flex items-center gap-2 mb-3">
                <span className="font-bold text-sm" style={{ color: "var(--soul-purple)" }}>{selected.date}</span>
                {selected.mood && (
                  <span className="pill" style={{ background: "var(--soul-purple-dim)", color: "var(--soul-purple)" }}>
                    {selected.mood}
                  </span>
                )}
              </div>
              <p className="text-sm leading-relaxed whitespace-pre-wrap" style={{ color: "var(--seal-text)" }}>
                {selected.entry}
              </p>
              {selected.moments && Object.keys(selected.moments).length > 0 && (
                <div className="mt-3 pt-3 border-t" style={{ borderColor: "var(--seal-border)" }}>
                  <p className="text-xs font-semibold mb-2" style={{ color: "var(--seal-text-dim)" }}>Momentos clave</p>
                  <pre className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
                    {JSON.stringify(selected.moments, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}

          {/* New entry */}
          <div className="card space-y-2">
            <p className="text-xs font-semibold" style={{ color: "var(--seal-text-dim)" }}>Escribir entrada de hoy</p>
            <textarea
              className="w-full px-3 py-2 text-sm rounded border outline-none resize-none"
              style={{
                background: "var(--seal-bg)", borderColor: "var(--seal-border)",
                color: "var(--seal-text)", minHeight: "120px",
              }}
              placeholder="¿Qué pasó hoy? ¿Cómo me siento?"
              value={newEntry}
              onChange={(e) => setNewEntry(e.target.value)}
            />
            <div className="flex gap-2">
              <input
                className="flex-1 px-3 py-1.5 text-xs rounded border outline-none"
                style={{
                  background: "var(--seal-bg)", borderColor: "var(--seal-border)",
                  color: "var(--seal-text)",
                }}
                placeholder="Estado de ánimo..."
                value={newMood}
                onChange={(e) => setNewMood(e.target.value)}
              />
              <button
                onClick={submitEntry}
                disabled={posting || !newEntry.trim()}
                className="px-4 py-1.5 text-xs rounded font-medium transition-colors disabled:opacity-40"
                style={{ background: "var(--soul-purple)", color: "white" }}
              >
                {posting ? "Guardando..." : "Guardar"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
