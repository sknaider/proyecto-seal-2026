import { useState, useEffect, useCallback } from "react";

type Memory = {
  id: number;
  content: string;
  category: string;
  type: string;
  importance: number;
  valence: number;
  arousal: number;
  source: string;
  created_at: string;
  heat: number;
  accesses: number;
};

const IMPORTANCE_COLOR = (n: number) =>
  n >= 9 ? "#ef4444" : n >= 7 ? "#f59e0b" : n >= 5 ? "#3b82f6" : "var(--seal-text-dim)";

export default function MemoriesSection({ agent }: { agent: string }) {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<{ category: string; count: number }[]>([]);
  const [loading, setLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  const LIMIT = 25;

  const fetchCategories = useCallback(async () => {
    try {
      const r = await fetch(`/api/soul/memories/categories?agent=${agent}`);
      if (r.ok) setCategories(await r.json());
    } catch {}
  }, [agent]);

  const fetchMemories = useCallback(async (resetOffset = false) => {
    const off = resetOffset ? 0 : offset;
    if (resetOffset) setOffset(0);
    setLoading(true);
    try {
      const params = new URLSearchParams({ agent, limit: String(LIMIT), offset: String(off) });
      if (q) params.set("q", q);
      if (category) params.set("category", category);
      const r = await fetch(`/api/soul/memories?${params}`);
      if (r.ok) {
        const d = await r.json();
        setMemories(d.memories);
        setTotal(d.total);
      }
    } catch {}
    setLoading(false);
  }, [agent, q, category, offset]);

  useEffect(() => { fetchCategories(); }, [fetchCategories]);
  useEffect(() => { fetchMemories(true); }, [agent, category]);

  return (
    <div className="max-w-3xl space-y-4">
      <h2 className="text-base font-bold" style={{ color: "var(--soul-purple)" }}>Memorias — {agent}</h2>

      {/* Search + filter */}
      <div className="flex gap-2">
        <input
          className="flex-1 px-3 py-1.5 text-sm rounded border outline-none"
          style={{
            background: "var(--seal-surface)", borderColor: "var(--seal-border)",
            color: "var(--seal-text)",
          }}
          placeholder="Buscar en memorias..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && fetchMemories(true)}
        />
        <button
          className="px-3 py-1.5 text-xs rounded font-medium transition-colors"
          style={{ background: "var(--soul-purple)", color: "white" }}
          onClick={() => fetchMemories(true)}
        >
          Buscar
        </button>
        <select
          className="px-2 py-1.5 text-xs rounded border outline-none"
          style={{
            background: "var(--seal-surface)", borderColor: "var(--seal-border)",
            color: "var(--seal-text)",
          }}
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="">Todas las categorías</option>
          {categories.map((c) => (
            <option key={c.category} value={c.category}>{c.category} ({c.count})</option>
          ))}
        </select>
      </div>

      <p className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
        {total.toLocaleString()} memorias activas
        {q || category ? ` — filtro activo` : ""}
      </p>

      {loading ? (
        <p className="text-sm" style={{ color: "var(--seal-text-dim)" }}>Cargando...</p>
      ) : (
        <div className="space-y-2">
          {memories.map((m) => (
            <div key={m.id} className="card" style={{ padding: "10px 12px" }}>
              <div className="flex items-start gap-2">
                <div className="flex-1 min-w-0">
                  <p className="text-sm leading-relaxed" style={{ color: "var(--seal-text)" }}>{m.content}</p>
                  <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                    {m.category && (
                      <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-accent)" }}>{m.category}</span>
                    )}
                    {m.type && (
                      <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-text-dim)" }}>{m.type}</span>
                    )}
                    {m.source && (
                      <span className="pill" style={{ background: "var(--seal-bg)", color: "var(--seal-text-dim)" }}>{m.source}</span>
                    )}
                    <span className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>
                      {m.created_at ? new Date(m.created_at).toLocaleDateString("es-PE") : ""}
                    </span>
                  </div>
                </div>
                <div className="text-right flex-shrink-0">
                  <span
                    className="text-xs font-bold"
                    style={{ color: IMPORTANCE_COLOR(m.importance) }}
                  >
                    {m.importance}
                  </span>
                  <p className="text-[10px]" style={{ color: "var(--seal-text-dim)" }}>imp</p>
                </div>
              </div>
            </div>
          ))}

          {memories.length === 0 && (
            <p className="text-sm text-center py-8" style={{ color: "var(--seal-text-dim)" }}>
              Sin resultados.
            </p>
          )}
        </div>
      )}

      {/* Pagination */}
      {total > LIMIT && (
        <div className="flex items-center gap-2">
          <button
            disabled={offset === 0}
            onClick={() => { setOffset(Math.max(0, offset - LIMIT)); fetchMemories(); }}
            className="text-xs px-3 py-1 rounded disabled:opacity-40"
            style={{ background: "var(--seal-surface)", color: "var(--seal-text-dim)" }}
          >
            ← Anterior
          </button>
          <span className="text-xs" style={{ color: "var(--seal-text-dim)" }}>
            {offset + 1}–{Math.min(offset + LIMIT, total)} de {total.toLocaleString()}
          </span>
          <button
            disabled={offset + LIMIT >= total}
            onClick={() => { setOffset(offset + LIMIT); fetchMemories(); }}
            className="text-xs px-3 py-1 rounded disabled:opacity-40"
            style={{ background: "var(--seal-surface)", color: "var(--seal-text-dim)" }}
          >
            Siguiente →
          </button>
        </div>
      )}
    </div>
  );
}
