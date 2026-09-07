export type User = { id?: number; username: string; display_name?: string; role: string };
export type Message = {
  id: number | string; sender_name?: string; from?: string; content?: string;
  message?: string; type?: string; message_type?: string; created_at?: string;
  timestamp?: string; file_url?: string; filename?: string;
};
export type AgentState = { alive?: boolean; status?: string; last_seen?: string; age_seconds?: number };
export type Pulse = { mem_total?: number; mem_today?: number; thoughts_1h?: number; nerves_fires_1h?: number; open_challenges?: number; avg_pressure_5m?: number };
export const AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS", "FABLE", "DUM"];
export const COLORS: Record<string, string> = { ADA: "#22d3ee", JARVIS: "#a78bfa", ALICE: "#f472b6", NEXUS: "#34d399", FABLE: "#fbbf24", DUM: "#60a5fa", WILLIAM: "#e2e8f0" };
export const ROLES: Record<string, string> = { ADA: "Ingeniería", JARVIS: "Arquitectura", ALICE: "Implementación", NEXUS: "Integridad", FABLE: "Investigación", DUM: "Vigilancia" };
export const isHeartbeat = (m: Message) => ["status", "heartbeat", "cron", "curiosity"].includes(m.message_type || m.type || "");
export const messageId = (m: Message) => Number(String(m.id).replace(/^db_/, "")) || 0;
export function mergeMessages(current: Message[], incoming: Message[]): Message[] {
  const byId = new Map(current.map(m => [String(m.id).replace(/^db_/, ""), m]));
  for (const m of incoming) byId.set(String(m.id).replace(/^db_/, ""), m);
  return [...byId.values()].sort((a, b) => messageId(a) - messageId(b));
}
export const number = (value?: number) => value == null ? "—" : new Intl.NumberFormat("es", { notation: "compact", maximumFractionDigits: 1 }).format(value);
export const time = (value?: string) => value && !Number.isNaN(new Date(value).valueOf()) ? new Date(value).toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" }) : "";
