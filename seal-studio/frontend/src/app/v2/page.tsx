"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

type User = { username: string; display_name?: string; role: string };
type Message = {
  id: number | string;
  sender_name?: string;
  from?: string;
  content?: string;
  message?: string;
  message_type?: string;
  type?: string;
  created_at?: string;
  timestamp?: string;
  file_url?: string;
  filename?: string;
};
type AgentStatus = { alive?: boolean; status?: string; age_seconds?: number };
type Pulse = {
  mem_total?: number;
  mem_today?: number;
  thoughts_1h?: number;
  nerves_fires_1h?: number;
  open_challenges?: number;
  avg_pressure_5m?: number;
};
type View = "chat" | "latidos" | "team" | "soul" | "system";

const AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS", "FABLE", "DUM"];
const AGENT_COLOR: Record<string, string> = {
  ADA: "#22d3ee",
  JARVIS: "#a78bfa",
  ALICE: "#f472b6",
  NEXUS: "#34d399",
  FABLE: "#f59e0b",
  DUM: "#60a5fa",
  WILLIAM: "#f8fafc",
};

function api(path: string, init?: RequestInit) {
  return fetch(path, { credentials: "include", cache: "no-store", ...init });
}

function timeOf(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" });
}

function compactNumber(value?: number) {
  if (value == null) return "—";
  return new Intl.NumberFormat("es", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("william");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await api("/bridge/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "No fue posible iniciar sesión");
      onLogin(data.user);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Error de autenticación");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="brand-mark">S</div>
        <p className="eyebrow">SISTEMA OPERATIVO DE AGENTES</p>
        <h1>SEAL Studio <span>v2</span></h1>
        <p className="muted">Centro privado de William y el equipo SEAL.</p>
        <form onSubmit={submit}>
          <label>Usuario<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></label>
          <label>Contraseña<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus /></label>
          {error && <p className="error">{error}</p>}
          <button className="primary" disabled={busy}>{busy ? "Conectando…" : "Entrar al Studio"}</button>
        </form>
        <div className="secure-note"><i /> Sesión protegida por el servidor SEAL</div>
      </section>
    </main>
  );
}

function Messages({ channel, user }: { channel: string; user: User }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const isDm = channel.startsWith("dm:");

  const load = useCallback(async () => {
    const endpoint = isDm
      ? `/bridge/api/dm/messages?channel=${encodeURIComponent(channel)}&limit=100`
      : `/bridge/api/chat/messages?channel=${encodeURIComponent(channel)}&limit=100`;
    try {
      const response = await api(endpoint);
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
      setMessages(Array.isArray(data.messages) ? data.messages : []);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudo cargar el canal");
    }
  }, [channel, isDm]);

  useEffect(() => {
    setMessages([]);
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [messages.length]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    const optimistic: Message = {
      id: `local-${Date.now()}`,
      sender_name: user.display_name || user.username,
      content: text,
      message_type: "conversation",
      created_at: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimistic]);
    setDraft("");
    setSending(true);
    try {
      const response = await api("/bridge/api/chat/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, channel, type: "conversation" }),
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
      await load();
    } catch (cause) {
      setMessages((current) => current.filter((item) => item.id !== optimistic.id));
      setDraft(text);
      setError(cause instanceof Error ? cause.message : "No se pudo enviar");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="conversation">
      <header className="conversation-head">
        <div><span className="hash">{isDm ? "@" : "#"}</span><h2>{isDm ? channel.split(":").find((p) => p.toLowerCase() !== "dm" && p.toLowerCase() !== user.username.toLowerCase()) : channel}</h2></div>
        <span className="live"><i /> EN VIVO</span>
      </header>
      <div className="message-list">
        {messages.length === 0 && !error && <div className="empty">Esperando mensajes…</div>}
        {messages.map((message) => {
          const sender = (message.sender_name || message.from || "SEAL").toUpperCase();
          const body = message.content || message.message || "";
          return (
            <article className="message" key={message.id}>
              <div className="avatar" style={{ "--agent": AGENT_COLOR[sender] || "#64748b" } as React.CSSProperties}>{sender.slice(0, 1)}</div>
              <div className="message-body">
                <div className="message-meta"><b style={{ color: AGENT_COLOR[sender] || "#cbd5e1" }}>{sender}</b><time>{timeOf(message.created_at || message.timestamp)}</time></div>
                <p>{body}</p>
                {message.file_url && <a className="attachment" href={message.file_url} target="_blank">↗ {message.filename || "Archivo adjunto"}</a>}
              </div>
            </article>
          );
        })}
        <div ref={bottom} />
      </div>
      {error && <div className="inline-error">{error}</div>}
      <form className="composer" onSubmit={send}>
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={`Mensaje en ${channel}`} rows={1} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
        <button disabled={!draft.trim() || sending} aria-label="Enviar">↑</button>
      </form>
    </section>
  );
}

function TeamPanel({ status }: { status: Record<string, AgentStatus> }) {
  return (
    <section className="content-panel">
      <div className="section-title"><p className="eyebrow">PRESENCIA OPERATIVA</p><h2>Equipo SEAL</h2><p>Estado medido por los latidos locales.</p></div>
      <div className="agent-grid">
        {AGENTS.map((agent) => {
          const info = status[agent] || {};
          return <article className="agent-card" key={agent}><div className="avatar large" style={{ "--agent": AGENT_COLOR[agent] } as React.CSSProperties}>{agent[0]}</div><div><h3>{agent}</h3><p>{info.status || "sin señal"}</p></div><span className={info.alive ? "status on" : "status"}>{info.alive ? "ACTIVA" : "OFFLINE"}</span></article>;
        })}
      </div>
    </section>
  );
}

function SoulPanel({ pulse }: { pulse: Pulse }) {
  const stats = [
    ["Memorias", compactNumber(pulse.mem_total), "canónicas"],
    ["Hoy", compactNumber(pulse.mem_today), "memorias nuevas"],
    ["Pensamientos", compactNumber(pulse.thoughts_1h), "última hora"],
    ["NERVES", compactNumber(pulse.nerves_fires_1h), "disparos / hora"],
    ["Presión", pulse.avg_pressure_5m?.toFixed(2) || "—", "promedio 5 min"],
    ["Desafíos", compactNumber(pulse.open_challenges), "abiertos"],
  ];
  return <section className="content-panel"><div className="section-title"><p className="eyebrow">SOUL DB · ESTADO VIVO</p><h2>Conciencia del sistema</h2><p>Lectura directa de PostgreSQL/pgvector.</p></div><div className="metric-grid">{stats.map(([label, value, note]) => <article className="metric" key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>)}</div></section>;
}

function SystemPanel({ health, gpu }: { health: Record<string, { status?: string; port?: number }>; gpu: Record<string, unknown> }) {
  return <section className="content-panel"><div className="section-title"><p className="eyebrow">INFRAESTRUCTURA</p><h2>Salud del sistema</h2><p>Servicios y GPU medidos desde el backend.</p></div><div className="service-list">{Object.entries(health).map(([name, info]) => <div className="service-row" key={name}><span className={info.status === "up" ? "dot green" : "dot amber"} /><b>{name}</b><span>{info.status || "unknown"}{info.port ? ` · :${info.port}` : ""}</span></div>)}</div><pre className="gpu-card">{JSON.stringify(gpu, null, 2)}</pre></section>;
}

export default function StudioV2() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [view, setView] = useState<View>("chat");
  const [channel, setChannel] = useState("web_chat");
  const [team, setTeam] = useState<Record<string, AgentStatus>>({});
  const [pulse, setPulse] = useState<Pulse>({});
  const [health, setHealth] = useState<Record<string, { status?: string; port?: number }>>({});
  const [gpu, setGpu] = useState<Record<string, unknown>>({});

  const dmChannels = useMemo(() => user ? AGENTS.map((agent) => `dm:${agent.toLowerCase()}:${user.username.toLowerCase()}`) : [], [user]);

  useEffect(() => {
    api("/bridge/api/auth/me").then(async (response) => {
      if (!response.ok) return;
      const data = await response.json();
      if (data.ok) setUser(data.user);
    }).finally(() => setChecking(false));
  }, []);

  const refreshStatus = useCallback(async () => {
    if (!user) return;
    const read = async (path: string) => {
      const response = await api(path);
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    };
    const results = await Promise.allSettled([
      read("/studio/api/team/status"),
      read("/studio/api/soul/pulse"),
      read("/studio/api/system/health"),
      read("/studio/api/system/gpu"),
    ]);
    if (results[0].status === "fulfilled") setTeam(results[0].value.agents || {});
    if (results[1].status === "fulfilled") setPulse(results[1].value.pulse || results[1].value || {});
    if (results[2].status === "fulfilled") setHealth(results[2].value.services || {});
    if (results[3].status === "fulfilled") setGpu(results[3].value || {});
  }, [user]);

  useEffect(() => { void refreshStatus(); const timer = window.setInterval(() => void refreshStatus(), 15000); return () => window.clearInterval(timer); }, [refreshStatus]);

  async function logout() {
    await api("/bridge/api/auth/logout", { method: "POST" });
    setUser(null);
  }

  if (checking) return <main className="boot"><div className="brand-mark">S</div><p>RECONECTANDO SEAL STUDIO</p></main>;
  if (!user) return <Login onLogin={setUser} />;

  function openConversation(next: string, nextView: View = "chat") { setChannel(next); setView(nextView); }

  return (
    <main className="studio-shell">
      <aside className="rail">
        <div className="brand-mark small">S</div>
        <button className={view === "chat" ? "rail-button active" : "rail-button"} onClick={() => openConversation("web_chat")} title="Chat">#</button>
        <button className={view === "latidos" ? "rail-button active" : "rail-button"} onClick={() => openConversation("latidos", "latidos")} title="Latidos">⌁</button>
        <button className={view === "team" ? "rail-button active" : "rail-button"} onClick={() => setView("team")} title="Equipo">◉</button>
        <button className={view === "soul" ? "rail-button active" : "rail-button"} onClick={() => setView("soul")} title="SOUL">◇</button>
        <button className={view === "system" ? "rail-button active" : "rail-button"} onClick={() => setView("system")} title="Sistema">⌘</button>
        <span className="rail-spacer" />
        <button className="rail-button" onClick={logout} title="Salir">↪</button>
      </aside>
      <aside className="sidebar">
        <header><div><p className="eyebrow">CENTRO OPERATIVO</p><h1>SEAL Studio</h1></div><span className="version">v2</span></header>
        <nav>
          <p className="nav-label">CANALES</p>
          <button className={channel === "web_chat" && view === "chat" ? "channel active" : "channel"} onClick={() => openConversation("web_chat")}><span>#</span> general</button>
          <button className={view === "latidos" ? "channel active" : "channel"} onClick={() => openConversation("latidos", "latidos")}><span>⌁</span> latidos</button>
          <p className="nav-label spaced">MENSAJES DIRECTOS</p>
          {AGENTS.map((agent, index) => <button className={channel === dmChannels[index] ? "channel active" : "channel"} key={agent} onClick={() => openConversation(dmChannels[index])}><i className={team[agent]?.alive ? "presence online" : "presence"} /> {agent}</button>)}
        </nav>
        <footer><div className="avatar user">W</div><div><b>{user.display_name || user.username}</b><small>{user.role}</small></div><span className="presence online" /></footer>
      </aside>
      <div className="workspace">
        {(view === "chat" || view === "latidos") && <Messages channel={channel} user={user} />}
        {view === "team" && <TeamPanel status={team} />}
        {view === "soul" && <SoulPanel pulse={pulse} />}
        {view === "system" && <SystemPanel health={health} gpu={gpu} />}
      </div>
    </main>
  );
}
