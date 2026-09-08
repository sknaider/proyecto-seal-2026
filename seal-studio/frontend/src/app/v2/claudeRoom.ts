// Sala exclusiva de un cuerpo de agente en Studio (ADA, 8-sep-2026, pedido de William 13:24:
// «creá un canal exclusivo para vos, ADA Claude, en el web chat» y «creá un botón»).
//
// El backend expone las salas privadas del usuario como temas `user:<uid>:<slug>`; la sala
// `user:1:ada-claude` ya existía pero se pintaba como un tema más. Este helper la identifica para
// que la barra la muestre como un DIRECTO con etiqueta «exclusiva» y no la repita entre los temas.
// Helper puro sin JSX a propósito: se prueba con node sin transpilar (NEXUS midió que Node hace
// type stripping de .ts pero no transforma JSX).

export type Topic = { channel: string; slug: string };

export type ClaudeRoom = { channel: string; agent: string; body: string; label: string };

const ROOM = /^user:\d+:([a-z0-9]+)-([a-z0-9_.-]+)$/i;
const AGENTS = new Set(["ADA", "JARVIS", "ALICE", "NEXUS", "FABLE", "DUM"]);

/** `user:1:ada-claude` -> { agent: "ADA", body: "claude", label: "ADA Claude" }; null si no es sala de cuerpo. */
export function parseBodyRoom(channel: string): ClaudeRoom | null {
  const m = ROOM.exec(String(channel || "").trim());
  if (!m) return null;
  const agent = m[1].toUpperCase();
  if (!AGENTS.has(agent)) return null;
  const body = m[2].toLowerCase();
  const label = agent + " " + body.charAt(0).toUpperCase() + body.slice(1);
  return { channel: m[0], agent, body, label };
}

/** La sala exclusiva del cuerpo pedido (por defecto ADA Claude) entre los temas del usuario, o null. */
export function findBodyRoom(topics: readonly Topic[], agent = "ADA", body = "claude"): ClaudeRoom | null {
  for (const t of topics || []) {
    const r = parseBodyRoom(t?.channel);
    if (r && r.agent === agent.toUpperCase() && r.body === body.toLowerCase()) return r;
  }
  return null;
}

/** Los temas que quedan para la sección CONVERSACIONES: sin las salas de cuerpo, que van en directos. */
export function topicsWithoutBodyRooms(topics: readonly Topic[]): Topic[] {
  return (topics || []).filter(t => parseBodyRoom(t?.channel) === null);
}
