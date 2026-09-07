"use client";
import { useEffect, useRef, useState } from "react";
import { Mail, RefreshCw, ShieldCheck, ArrowUpRight } from "lucide-react";
import { api, jsonResponse } from "@/lib/api";
type Connection = { configured: boolean; connected: boolean; email?: string };
type MailHeader = { id: string; from: string; subject: string; date: string };
export default function GmailPanel() {
  const [status, setStatus] = useState<Connection | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<MailHeader[] | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true; const controller = new AbortController();
    void api("/api/integrations/gmail/status", { signal: controller.signal }).then(jsonResponse).then(data => { if (alive.current) setStatus(data); }).catch(() => { if (!controller.signal.aborted && alive.current) setError("No se pudo consultar la conexión Gmail."); });
    return () => { alive.current = false; controller.abort(); };
  }, []);
  async function act(action: "connect" | "inbox" | "disconnect") {
    if (busy) return;
    if (action === "disconnect" && !window.confirm("¿Revocar el acceso de Studio a esta cuenta Gmail? No se eliminarán correos.")) return;
    setBusy(true); setError("");
    try {
      const data = await jsonResponse(await api("/api/integrations/gmail/" + action, { method: action === "inbox" ? "GET" : "POST" }, 30000));
      if (!alive.current) return;
      if (action === "connect") {
        const url = new URL(data.url);
        if (url.origin !== "https://accounts.google.com") throw new Error("Destino OAuth inválido.");
        window.location.assign(url.href);
      } else if (action === "inbox") setMessages(data.messages);
      else { setStatus({ configured: true, connected: false }); setMessages(null); }
    } catch (cause) { if (alive.current) setError(cause instanceof Error ? cause.message : "No se pudo completar la operación."); }
    finally { if (alive.current) setBusy(false); }
  }
  return <article className="tool-card gmail-panel"><div className="tool-heading"><h3><Mail size={18}/>Gmail en tu espacio</h3>{status?.connected && <span className="connection"><i/>Conectado</span>}</div>
    <p className="integration-note"><ShieldCheck size={13}/> Solo remitente, asunto y fecha de los últimos 10 correos. Sin cuerpos, adjuntos, envío ni borrado. Se consulta únicamente cuando pulsás «Ver bandeja».</p>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {!status ? <p className="muted">{error ? "Conexión pendiente de verificar." : "Consultando configuración…"}</p> : !status.configured ? <p className="integration-note"><b>Configuración pendiente.</b> El conector está instalado, pero todavía necesita OAuth web de Google, una URL HTTPS y almacenamiento cifrado configurado en el servidor. Studio no tiene acceso a tu correo.</p> : status.connected ? <><p className="muted">{status.email}</p><div className="gmail-actions"><button className="primary" disabled={busy} onClick={() => void act("inbox")}><RefreshCw size={14}/>{busy ? "Consultando…" : "Ver bandeja"}</button><button className="secondary" disabled={busy} onClick={() => void act("disconnect")}>Desconectar</button></div></> : <button className="primary" disabled={busy} onClick={() => void act("connect")}>Conectar mi Gmail<ArrowUpRight size={14}/></button>}
    {messages && <div className="mail-list">{messages.length ? messages.map(m => <a key={m.id} href="https://mail.google.com/" target="_blank" rel="noopener noreferrer"><b>{m.subject || "Sin asunto"}</b><span>{m.from}</span><small>{m.date}</small></a>) : <p className="muted">No hay mensajes en la bandeja de entrada.</p>}</div>}
  </article>;
}
