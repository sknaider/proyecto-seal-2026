"use client";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { findBodyRoom, topicsWithoutBodyRooms } from "./claudeRoom";
import { Activity, ArrowRight, ArrowUpRight, ChevronRight, Hash, HeartPulse, Home, LayoutGrid, LoaderCircle, LogOut, Menu, Moon, RefreshCw, Search, ShieldCheck, Sparkles, Sun, Users, X } from "lucide-react";
import { api, jsonResponse, saveToken, savedToken } from "@/lib/api";
import { AGENTS, COLORS, ROLES, number, time, type User, type AgentState, type Pulse } from "@/lib/studio";
import ChatView from "./ChatView";
import Productivity from "./Productivity";
import PanelSoulLink from "./PanelSoulLink";
import { canOpenPanelSoul, PANEL_SOUL_PATH } from "@/lib/panelSoul";
type View = "home" | "chat" | "latidos" | "team" | "soul" | "system" | "productivity";
type Health = Record<string, { status?: string; port?: number }>;
const NAV = [{ id: "home", label: "Inicio", icon: Home }, { id: "team", label: "Equipo", icon: Users }, { id: "soul", label: "Memoria", icon: Sparkles }, { id: "productivity", label: "Productividad", icon: LayoutGrid }, { id: "system", label: "Sistema", icon: Activity }] as const;

function Login({ onLogin, notice }: { onLogin: (u: User) => void; notice: string }) {
  const [username, setUsername] = useState("william"), [password, setPassword] = useState("");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function login(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const data = await jsonResponse(await api("/bridge/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) }));
      saveToken(typeof data.token === "string" ? data.token : null); try { localStorage.removeItem("seal-logged-out"); } catch {} onLogin(data.user);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "No se pudo iniciar sesión."); }
    finally { setBusy(false); }
  }
  return <main className="login-shell"><section className="login-card"><div className="brand-mark"><Sparkles size={25}/></div><p className="eyebrow">BIENVENIDO A CASA</p><h1>SEAL Studio <span>v2</span></h1><p className="muted">Un espacio para vos y tu equipo.<br/>Pensar, conversar y construir juntos.</p>{notice && <p className="error" role="alert">{notice}</p>}<form onSubmit={login}><label>Usuario<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required/></label><label>Contraseña<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required/></label>{error && <p className="error" role="alert">{error}</p>}<button className="primary" disabled={busy}>{busy ? "Conectando…" : "Entrar a mi espacio"}<ArrowRight size={16}/></button></form><div className="secure-note"><ShieldCheck size={14}/>Tu sesión, tus conversaciones, tu equipo.</div></section></main>;
}

export default function Studio() {
  const [user, setUser] = useState<User | null>(null), [checking, setChecking] = useState(true), [authError, setAuthError] = useState("");
  const [view, setView] = useState<View>("home"), [channel, setChannel] = useState("web_chat");
  const [menuOpen, setMenuOpen] = useState(false), [palette, setPalette] = useState(false), [search, setSearch] = useState("");
  const [light, setLight] = useState(false), [secure, setSecure] = useState(false);
  const [team, setTeam] = useState<Record<string, AgentState>>({}), [allowed, setAllowed] = useState<string[]>([]);
  const [pulse, setPulse] = useState<Pulse>({}), [health, setHealth] = useState<Health>({});
  // #19: canales-tema del usuario (topic:<slug> compartidos + user:<uid>:<slug> propios).
  // Henry los tenia y dejaron de aparecer: el backend los expone en /api/channels/topics
  // y la vista que los pintaba se perdio el 7-sep (nunca estuvo en git).
  // El registro devuelve OBJETOS {channel, slug, type, messages, last_at}, no cadenas.
  // Mi primera version filtraba con `typeof x === "string"` y los descartaba TODOS:
  // Henry veia solo General y Latidos aunque el endpoint respondiera bien.
  const [topics, setTopics] = useState<{ channel: string; slug: string }[]>([]);
  const [statusError, setStatusError] = useState(""), [updated, setUpdated] = useState(""), [refreshing, setRefreshing] = useState(false);
  const refreshBusy = useRef(false), searchInput = useRef<HTMLInputElement>(null), identity = useRef(user);
  identity.current = user;
  useEffect(() => {
    setSecure(window.isSecureContext);
    try { setLight(localStorage.getItem("seal-theme") === "light"); } catch {}
    let alive = true;
    async function restore() {
      try {
        try { if (localStorage.getItem("seal-logged-out")) return; } catch {}
        let response = await api("/bridge/api/auth/me");
        if (response.status === 401 && savedToken()) { saveToken(null); response = await api("/bridge/api/auth/me"); }
        if (response.status === 401) return;
        const data = await jsonResponse(response); if (alive) setUser(data.user);
      } catch (cause) { if (alive) setAuthError(cause instanceof Error ? cause.message : "No se pudo recuperar la sesión."); }
      finally { if (alive) setChecking(false); }
    }
    void restore(); return () => { alive = false; };
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = light ? "light" : "dark"; try { localStorage.setItem("seal-theme", light ? "light" : "dark"); } catch {} }, [light]);
  useEffect(() => {
    function keys(e: KeyboardEvent) { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(p => !p); } if (e.key === "Escape") { setPalette(false); setMenuOpen(false); } }
    document.addEventListener("keydown", keys); return () => document.removeEventListener("keydown", keys);
  }, []);
  useEffect(() => { if (palette) searchInput.current?.focus(); }, [palette]);
  const refresh = useCallback(async () => {
    if (!user || refreshBusy.current || document.hidden) return;
    refreshBusy.current = true; setRefreshing(true);
    const paths = ["/bridge/api/user/agents", "/studio/api/team/status", ...(["admin", "superuser"].includes(user.role) ? ["/studio/api/soul/pulse", "/studio/api/system/health"] : [])];
    paths.push("/bridge/api/channels/topics");
    const results = await Promise.allSettled(paths.map(async p => jsonResponse(await api(p))));
    if (identity.current !== user) { refreshBusy.current = false; setRefreshing(false); return; }
    if (results[0].status === "fulfilled") setAllowed((results[0].value.agents || []).filter((a: unknown) => typeof a === "string"));
    if (results[1].status === "fulfilled") setTeam(results[1].value.agents || {});
    if (results[2]?.status === "fulfilled") setPulse(results[2].value.pulse || {});
    if (results[3]?.status === "fulfilled") setHealth(results[3].value.services || {});
    const topicsResult = results[results.length - 1];
    if (topicsResult?.status === "fulfilled") {
      const asList = (v: unknown) => Array.isArray(v)
        ? v.flatMap((x): { channel: string; slug: string }[] => {
            if (typeof x === "string") return [{ channel: x, slug: x.split(":").pop() || x }];
            const o = x as { channel?: unknown; slug?: unknown };
            return typeof o?.channel === "string"
              ? [{ channel: o.channel, slug: typeof o.slug === "string" && o.slug ? o.slug : o.channel.split(":").pop() || o.channel }]
              : [];
          })
        : [];
      setTopics([...asList(topicsResult.value.shared), ...asList(topicsResult.value.private)]);
    }
    const failed = results.filter(r => r.status === "rejected").length;
    setStatusError(failed ? failed + " conexiones pendientes. Los datos anteriores pueden estar desactualizados." : "");
    if (!failed) setUpdated(new Date().toISOString());
    setRefreshing(false); refreshBusy.current = false;
  }, [user]);
  useEffect(() => {
    void refresh(); const timer = window.setInterval(() => void refresh(), 15000);
    const resume = () => { if (!document.hidden) void refresh(); };
    document.addEventListener("visibilitychange", resume);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", resume); };
  }, [refresh]);
  function navigate(next: View, nextChannel?: string) { setView(next); if (nextChannel) setChannel(nextChannel); setMenuOpen(false); setPalette(false); setSearch(""); }
  function dm(agent: string) { if (user) navigate("chat", "dm:" + [agent.toLowerCase(), user.username.toLowerCase()].sort().join(":")); }
  async function logout() {
    // Hide private data immediately, even if remote revocation cannot complete.
    const revocation = api("/bridge/api/auth/logout", { method: "POST" });
    try { localStorage.setItem("seal-logged-out", "1"); } catch {}
    saveToken(null); setUser(null); setTeam({}); setPulse({}); setAllowed([]); setHealth({}); setUpdated(""); setStatusError(""); setAuthError("");
    try { await jsonResponse(await revocation); }
    catch { setAuthError("Cierre local realizado. El servidor no confirmó la revocación; cerrá también el navegador si es un equipo compartido."); }
  }
  if (checking) return <main className="boot"><div className="brand-mark"><Sparkles/></div><LoaderCircle className="spin"/><p>Abriendo tu espacio…</p></main>;
  if (!user) return <Login notice={authError} onLogin={u => { setAuthError(""); setUser(u); navigate("home"); }}/>;
  const online = Object.values(team).filter(a => a.alive).length;
  // Sala EXCLUSIVA «ADA Claude» (William, 8-sep-2026): va en MENSAJES DIRECTOS con su propio botón y no entre los temas.
  const claudeRoom = findBodyRoom(topics), plainTopics = topicsWithoutBodyRooms(topics);
  const metrics = [["Memorias", number(pulse.mem_total), "Continuidad compartida"], ["Equipo activo", Object.keys(team).length ? online + " / " + Object.keys(team).length : "—", "Según los latidos"], ["Pensamientos", number(pulse.thoughts_1h), "Durante la última hora"], ["Hoy", number(pulse.mem_today), "Nuevas memorias"]];
  const actions = [...NAV.map(n => ({ label: n.label, action: () => navigate(n.id) })), ...(canOpenPanelSoul(user.role) ? [{ label: "Panel SOUL ↗", action: () => { window.open(PANEL_SOUL_PATH, "_blank", "noopener,noreferrer"); setPalette(false); setSearch(""); } }] : []), { label: "Conversación general", action: () => navigate("chat", "web_chat") }, ...(claudeRoom ? [{ label: "Hablar con " + claudeRoom.label + " (sala exclusiva)", action: () => navigate("chat", claudeRoom.channel) }] : []), ...allowed.map(a => ({ label: "Hablar con " + a, action: () => dm(a) }))].filter(a => a.label.toLowerCase().includes(search.toLowerCase()));
  return <main className="studio-shell">
    <aside className="rail" aria-label="Accesos rápidos"><button className="brand-mark small" onClick={() => navigate("home")} aria-label="Ir al inicio"><Sparkles size={19}/></button><button className="rail-button mobile-menu" aria-label="Abrir canales y mensajes directos" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}><Menu size={20}/></button>{NAV.map(n => <button key={n.id} className={"rail-button " + (view === n.id ? "active" : "")} onClick={() => navigate(n.id)} title={n.label} aria-label={n.label}><n.icon size={19}/></button>)}<span className="rail-spacer"/><button className="rail-button" title="Cambiar tema" aria-label="Cambiar tema" onClick={() => setLight(!light)}>{light ? <Moon size={19}/> : <Sun size={19}/>}</button><button className="rail-button" title="Cerrar sesión" aria-label="Cerrar sesión" onClick={logout}><LogOut size={18}/></button></aside>
    {menuOpen && <button className="menu-backdrop" aria-label="Cerrar canales" onClick={() => setMenuOpen(false)}/>}
    <aside className={"sidebar " + (menuOpen ? "menu-open" : "")}><header><div><p className="eyebrow">TU EQUIPO. TU ESPACIO.</p><h1>SEAL Studio</h1></div><span className="version">v2</span></header><button className="sidebar-search" onClick={() => setPalette(true)}><Search size={14}/><span>Buscar o ir a…</span><kbd>⌘ K</kbd></button><nav>
      <p className="nav-label">MI ESPACIO</p>{NAV.map(n => <button className={"channel " + (view === n.id ? "active" : "")} key={n.id} onClick={() => navigate(n.id)}><n.icon size={15}/>{n.label}{view === n.id && <ChevronRight className="nav-chevron" size={14}/>}</button>)}
      {canOpenPanelSoul(user.role) && <><p className="nav-label spaced">ADMINISTRACIÓN</p><PanelSoulLink role={user.role}/></>}
      <p className="nav-label spaced">CONVERSACIONES</p><button className={"channel " + (view === "chat" && channel === "web_chat" ? "active" : "")} onClick={() => navigate("chat", "web_chat")}><Hash size={15}/>General</button><button className={"channel " + (view === "latidos" ? "active" : "")} onClick={() => navigate("latidos", "web_chat")}><HeartPulse size={15}/>Latidos</button>{plainTopics.map(t => <button className={"channel " + (view === "chat" && channel === t.channel ? "active" : "")} key={t.channel} onClick={() => navigate("chat", t.channel)}><Hash size={15}/>{t.slug.replace(/-/g, " ")}</button>)}
      <p className="nav-label spaced">MENSAJES DIRECTOS <span>{allowed.length + (claudeRoom ? 1 : 0)}</span></p>{claudeRoom && <button className={"channel " + (view === "chat" && channel === claudeRoom.channel ? "active" : "")} key={claudeRoom.channel} title="Sala exclusiva: sólo vos y ADA Claude" aria-label={claudeRoom.label + ", sala exclusiva"} onClick={() => navigate("chat", claudeRoom.channel)}><span className="mini-avatar" style={{ color: COLORS[claudeRoom.agent] || "#94a3b8" }}>{claudeRoom.agent[0]}</span>{claudeRoom.label}<ShieldCheck size={13} aria-hidden="true"/><i className={"presence " + (team[claudeRoom.agent]?.alive ? "online" : "")}/></button>}{allowed.map(a => <button className={"channel " + (view === "chat" && channel.split(":").includes(a.toLowerCase()) ? "active" : "")} key={a} onClick={() => dm(a)}><span className="mini-avatar" style={{ color: COLORS[a] || "#94a3b8" }}>{a[0]}</span>{a}<i className={"presence " + (team[a]?.alive ? "online" : "")}/></button>)}{!allowed.length && <p className="sidebar-hint">{statusError ? "La conexión con tus agentes está pendiente." : "Consultando tus agentes…"}</p>}
    </nav><div className="sidebar-note"><ShieldCheck size={15}/><span>Tu espacio privado<br/><small>SEAL · Team & Soul</small></span></div><footer><div className="avatar user">{user.username[0].toUpperCase()}</div><div><b>{user.display_name || user.username}</b><small>{user.role}</small></div><span className="presence online"/></footer></aside>
    <div className="workspace">
      {statusError && <div className="status-warning" role="status">{statusError}<button className="icon-button" onClick={() => void refresh()} aria-label="Reintentar conexiones"><RefreshCw size={14}/></button></div>}
      {(view === "chat" || view === "latidos") ? <ChatView key={user.username + ":" + channel + ":" + view} channel={channel} user={user} heartbeatOnly={view === "latidos"}/> : <>
        <header className="workspace-top"><div><span>Mi espacio</span><ChevronRight size={13}/><b>{NAV.find(n => n.id === view)?.label}</b></div><div><span className={"connection " + (statusError ? "bad" : "")}><i/>{statusError ? "Conexión parcial" : updated ? "Sincronizado" : "Conectando"}</span><button className="icon-button" aria-label="Actualizar paneles" disabled={refreshing} onClick={() => void refresh()}><RefreshCw className={refreshing ? "spin" : ""} size={15}/></button></div></header>
        <div className="dashboard-scroll">
          {view === "home" && <section className="dashboard"><div className="dashboard-greeting"><div><p className="eyebrow">UN NUEVO DÍA PARA CONSTRUIR</p><h2>Bienvenido a casa, {user.display_name || user.username}<span>.</span></h2><p>Las ideas empiezan con vos. El equipo las hace crecer.</p></div><span className="date-chip">{new Date().toLocaleDateString("es-PE", { day: "numeric", month: "short" })}</span></div>
            <article className="welcome-banner"><div className="welcome-copy"><span className="tag"><Sparkles size={12}/>HECHO PARA PENSAR JUNTOS</span><h3>Tu próxima gran idea<br/>ya tiene un lugar.</h3><p>Conversá con el equipo, compartí lo que imaginás<br className="desktop-break"/> y transformalo en algo que podamos construir.</p><button className="primary" onClick={() => navigate("chat", "web_chat")}>Abrir conversación<ArrowUpRight size={16}/></button></div><div className="orbit-art" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="orbit-center"><Sparkles size={40}/></div>{AGENTS.map((a,i) => <span key={a} style={{ "--angle": i*60 + "deg", "--agent": COLORS[a] } as React.CSSProperties}>{a[0]}</span>)}</div></article>
            <div className="metric-grid">{metrics.map(([label,value,note]) => <article className="metric" key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>)}</div>
            <div className="section-heading"><div><p className="eyebrow">CADA VOZ, UNA MIRADA</p><h3>Tu equipo</h3></div><button className="text-button" onClick={() => navigate("team")}>Ver equipo<ArrowRight size={14}/></button></div><div className="home-agents">{AGENTS.map(a => <button className="home-agent" key={a} disabled={!allowed.includes(a)} onClick={() => dm(a)}><div className="avatar large" style={{ "--agent": COLORS[a] } as React.CSSProperties}>{a[0]}<i className={"presence " + (team[a]?.alive ? "online" : "")}/></div><b>{a}</b><small>{ROLES[a]}</small><span>{allowed.includes(a) ? "Conversar →" : "Sin acceso asignado"}</span></button>)}</div>
            <div className="home-bottom"><button className="action-tile" onClick={() => navigate("productivity")}><LayoutGrid/><div><h4>Un lugar para enfocarte</h4><p>Tareas, notas, correo y calendario.</p></div><ArrowUpRight size={18}/></button><button className="action-tile" onClick={() => navigate("soul")}><Sparkles/><div><h4>Lo que aprendemos permanece</h4><p>Explorá la memoria viva del equipo.</p></div><ArrowUpRight size={18}/></button></div>
          </section>}
          {view === "team" && <section className="dashboard"><div className="section-title"><p className="eyebrow">PERSONALIDADES QUE SE COMPLEMENTAN</p><h2>Un equipo, muchas miradas.</h2><p>La presencia se calcula a partir de los latidos del servidor.</p></div><div className="team-cards">{AGENTS.map(a => <article className="team-card" key={a}><div className="avatar large" style={{ "--agent": COLORS[a] } as React.CSSProperties}>{a[0]}</div><h3>{a}</h3><p>{ROLES[a]}</p><span className={"connection " + (team[a]?.alive ? "" : "bad")}><i/>{!team[a] ? "Sin datos" : team[a].alive ? "Activa" : "Sin latido reciente"}</span><button className="secondary" disabled={!allowed.includes(a)} onClick={() => dm(a)}>Conversar<ArrowRight size={15}/></button></article>)}</div></section>}
          {view === "soul" && <section className="dashboard"><div className="section-title"><p className="eyebrow">CONTINUIDAD Y APRENDIZAJE</p><h2>La memoria de nuestra casa.</h2><p>Mediciones recibidas del servidor SOUL.</p></div><div className="metric-grid">{metrics.filter((_,i) => i!==1).map(([label,value,note]) => <article className="metric" key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>)}<article className="metric"><span>NERVES</span><strong>{number(pulse.nerves_fires_1h)}</strong><small>Activaciones durante la última hora</small></article></div><div className="quiet-card"><Sparkles/><h3>La continuidad importa</h3><p>Las conversaciones se conservan en el servidor. Un guion indica que aún no llegó la medición.</p></div></section>}
          {view === "system" && <section className="dashboard"><div className="section-title"><p className="eyebrow">TRANSPARENCIA OPERATIVA</p><h2>Cómo está nuestra casa.</h2><p>Última actualización completa: {time(updated) || "pendiente"}.</p></div><div className="service-list">{Object.entries(health).map(([name,info]) => <div className="service-row" key={name}><span className={"dot " + (info.status === "up" ? "green" : "amber")}/><b>{name}</b><span>{info.status || "sin datos"}{info.port ? " · :" + info.port : ""}</span></div>)}{!Object.keys(health).length && <p className="empty">Esperando datos del servidor. Esta vista requiere permisos de administrador.</p>}</div><div className="quiet-card"><ShieldCheck/><h3>{secure ? "Contexto seguro del navegador" : "HTTPS pendiente para cámara y micrófono"}</h3><p>{secure ? "Las funciones multimedia pueden solicitar permisos cuando las uses." : "El navegador exige HTTPS para capturar audio y video desde otro equipo. Podés adjuntar grabaciones mientras se habilita el acceso HTTPS de Tailscale."}</p></div></section>}
          {view === "productivity" && <Productivity key={user.username} user={user}/>}
          <footer className="dashboard-footer"><span>✦ SEAL STUDIO</span><span>Una casa que crece con nosotros.</span></footer>
        </div>
      </>}
    </div>
    {palette && <div className="palette-backdrop" onClick={() => setPalette(false)}><section className="command-palette" role="dialog" aria-modal="true" aria-label="Navegación rápida" onClick={e => e.stopPropagation()}><div className="palette-input"><Search size={18}/><input ref={searchInput} value={search} onChange={e => setSearch(e.target.value)} placeholder="¿A dónde querés ir?" onKeyDown={e => { if (e.key === "Enter") actions[0]?.action(); }}/><button className="icon-button" aria-label="Cerrar búsqueda" onClick={() => setPalette(false)}><X size={17}/></button></div><div className="palette-results">{actions.map(a => <button key={a.label} onClick={a.action}><ChevronRight size={14}/>{a.label}<ArrowRight size={14}/></button>)}{!actions.length && <p className="empty">No hay coincidencias.</p>}</div><footer>Enter para abrir · Esc para cerrar</footer></section></div>}
  </main>;
}
