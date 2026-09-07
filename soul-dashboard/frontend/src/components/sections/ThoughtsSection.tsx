import { useState, useEffect } from "react";

type Thought = {
  id: number;
  thought: string;
  emotion: string;
  uncertainty: string;
  intention: string;
  at: string;
};

export default function ThoughtsSection({ agent }: { agent: string }) {
  const [thoughts, setThoughts] = useState<Thought[]>([]);
  const [loading, setLoading] = useState(true);
  const [newThought, setNewThought] = useState("");
  const [newEmotion, setNewEmotion] = useState("");
  const [posting, setPosting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/soul/thoughts?agent=${agent}&limit=30`);
      if (r.ok) setThoughts(await r.json());
    } catch {}
    setLoading(false);
  };

  useEffect(() => { load(); }, [agent]);

  const submitThought = async () => {
    if (!newThought.trim()) return;
    setPosting(true);
    try {
      await fetch("/api/soul/thoughts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent, thought: newThought.trim(), emotional_state: newEmotion.trim() }),
      });
      setNewThought("");
      setNewEmotion("");
      await load();
    } catch {}
    setPosting(false);
  };

  return (
    <div className="max-w-3xl space-y-4">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Monólogo Interno — {agent}</h2>

      {/* Write thought */}
      <div className="card space-y-2">
        <p className="text-xs font-semibold" style={{ color: "var(--seal-text-dim)" }}>Registrar pensamiento</p>
        <textarea
          className="w-full px-3 py-2 text-sm rounded border outline-none resize-none"
          style={{
            background: "var(--seal-bg)", borderColor: "var(--seal-border)",
            color: "var(--seal-text)", minHeight: "80px",
          }}
          placeholder="Escribe el pensamiento..."
          value={newThought}
          onChange={(e) => setNewThought(e.target.value)}
        />
        <div className="flex gap-2">
          <input
            className="flex-1 px-3 py-1.5 text-xs rounded border outline-none"
            style={{
              background: "var(--seal-bg)", borderColor: "var(--seal-border)",
              color: "var(--seal-text)",
            }}
            placeholder="Estado emocional (ej: curiosidad, calma...)"
            value={newEmotion}
            onChange={(e) => setNewEmotion(e.target.value)}
          />
          <button
            onClick={submitThought}
            disabled={posting || !newThought.trim()}
            className="px-4 py-1.5 text-xs rounded font-medium transition-colors disabled:opacity-40"
            style={{ background: "var(--soul-purple)", color: "white" }}
          >
            {posting ? "Guardando..." : "Guardar"}
          </button>
        </div>
      </div>

      {loading ? (
        <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando pensamientos...</p>
      ) : (
        <div className="space-y-3">
          {thoughts.map((t) => (
            <div key={t.id} className="card" style={{ padding: "10px 12px", borderLeft: "3px solid var(--soul-purple)" }}>
              <p className="text-sm leading-relaxed" style={{ color: "var(--seal-text)" }}>{t.thought}</p>
              <div className="flex flex-wrap gap-2 mt-2">
                {t.emotion && (
                  <span className="pill" style={{ background: "var(--soul-purple-dim)", color: "var(--soul-purple)" }}>
                    💜 {t.emotion}
                  </span>
                )}
                {t.uncertainty && (
                  <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-warning)" }}>
                    ❓ {t.uncertainty}
                  </span>
                )}
                {t.intention && (
                  <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-accent)" }}>
                    → {t.intention}
                  </span>
                )}
                <span className="text-[10px] ml-auto" style={{ color: "var(--seal-text-dim)" }}>
                  {t.at ? new Date(t.at).toLocaleString("es-PE", { timeZone: "America/Lima" }) : ""}
                </span>
              </div>
            </div>
          ))}
          {thoughts.length === 0 && (
            <p className="text-sm text-center py-8" style={{ color: "var(--seal-text-dim)" }}>Sin pensamientos registrados.</p>
          )}
        </div>
      )}
    </div>
  );
}
