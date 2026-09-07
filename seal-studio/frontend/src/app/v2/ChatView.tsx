"use client";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Virtuoso, VirtuosoHandle } from "react-virtuoso";
import { ArrowDown, ArrowUp, Hash, LockKeyhole, Paperclip, Search, X, LoaderCircle } from "lucide-react";
import { api, jsonResponse } from "@/lib/api";
import { COLORS, isHeartbeat, mergeMessages, messageId, time, type Message, type User } from "@/lib/studio";
import MessageContent from "./MessageContent";
import CaptureControls from "./CaptureControls";

export default function ChatView({ channel, user, heartbeatOnly = false }: { channel: string; user: User; heartbeatOnly?: boolean }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [sending, setSending] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [olderBusy, setOlderBusy] = useState(false);
  const [hasOlder, setHasOlder] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const [query, setQuery] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [firstIndex, setFirstIndex] = useState(1000000);
  const rows = useRef<Message[]>([]);
  const latest = useRef(0);
  const pollBusy = useRef(false);
  const mounted = useRef(true);
  const request = useRef<AbortController | null>(null);
  const list = useRef<VirtuosoHandle>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const isDm = channel.startsWith("dm:");
  const title = isDm ? channel.split(":").slice(1).find(p => p.toLowerCase() !== user.username.toLowerCase())?.toUpperCase() : heartbeatOnly ? "Latidos" : channel === "web_chat" ? "General" : channel;
  const include = useCallback((m: Message) => isDm || heartbeatOnly === isHeartbeat(m), [isDm, heartbeatOnly]);
  const filtered = useMemo(() => messages.filter(m => include(m) && (!query || `${m.sender_name || m.from} ${m.content || m.message}`.toLowerCase().includes(query.toLowerCase()))), [messages, include, query]);

  const load = useCallback(async () => {
    if (pollBusy.current || document.hidden) return;
    pollBusy.current = true;
    const abort = new AbortController();
    request.current = abort;
    try {
      const params = new URLSearchParams({ channel, limit: "100" });
      if (latest.current) params.set("after", String(latest.current));
      const data = await jsonResponse(await api(`/bridge/api/chat/messages?${params}`, { signal: abort.signal }));
      if (!mounted.current || abort.signal.aborted) return;
      const incoming: Message[] = Array.isArray(data.messages) ? data.messages : [];
      if (!latest.current) setHasOlder(incoming.length === 100);
      for (const m of incoming) latest.current = Math.max(latest.current, messageId(m));
      rows.current = mergeMessages(rows.current, incoming);
      setMessages(rows.current);
      setLoaded(true); setError("");
    } catch (cause) {
      if (!abort.signal.aborted && mounted.current) setError(cause instanceof Error ? cause.message : "No se pudo cargar el canal.");
    } finally { pollBusy.current = false; }
  }, [channel]);

  useEffect(() => {
    mounted.current = true;
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    const resume = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", resume);
    const focus = (e: KeyboardEvent) => { if (e.key === "Escape") input.current?.focus(); };
    document.addEventListener("keydown", focus);
    return () => { mounted.current = false; request.current?.abort(); if ("speechSynthesis" in window) speechSynthesis.cancel(); window.clearInterval(timer); document.removeEventListener("visibilitychange", resume); document.removeEventListener("keydown", focus); };
  }, [load]);

  async function older() {
    if (olderBusy || !rows.current.length) return;
    setOlderBusy(true);
    try {
      const before = Math.min(...rows.current.map(messageId).filter(Boolean));
      const params = new URLSearchParams({ channel, limit: "100", before: String(before) });
      const data = await jsonResponse(await api(`/bridge/api/chat/messages?${params}`));
      if (!mounted.current) return;
      const incoming: Message[] = data.messages || [];
      const known = new Set(rows.current.map(messageId));
      const added = incoming.filter(m => !known.has(messageId(m)) && include(m));
      setFirstIndex(i => i - added.length);
      rows.current = mergeMessages(rows.current, incoming);
      setMessages(rows.current); setHasOlder(incoming.length === 100);
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "No se pudo cargar el historial."); }
    finally { if (mounted.current) setOlderBusy(false); }
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    const content = draft.trim();
    if ((!content && !file) || sending || capturing) return;
    setSending(true); setError("");
    try {
      if (file) {
        const body = new FormData(); body.append("file", file); body.append("caption", content); body.append("channel", channel);
        await jsonResponse(await api("/bridge/api/upload", { method: "POST", body }, 120000));
      } else {
        await jsonResponse(await api("/bridge/api/chat/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: content, channel, type: "conversation" }) }));
      }
      if (!mounted.current) return;
      setDraft(""); setFile(null); await load();
      list.current?.scrollToIndex({ index: "LAST", behavior: "smooth" });
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "No se pudo enviar; conservamos tu mensaje."); }
    finally { if (mounted.current) { setSending(false); input.current?.focus(); } }
  }

  function selectFile(next?: File) {
    if (!next) return;
    if (next.size > 25 * 1024 * 1024) { setError("Elegí un archivo de hasta 25 MB."); return; }
    setFile(next); setError("");
  }

  return <section className={`conversation${dragging ? " dragging" : ""}`} aria-label={`Conversación ${title}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); if (!sending) selectFile(e.dataTransfer.files[0]); }}>
    <header className="conversation-head"><div>{isDm ? <LockKeyhole size={18}/> : <Hash size={20}/>}<div><h2>{title}</h2><small>{isDm ? "Conversación privada" : heartbeatOnly ? "Actividad automática del equipo" : "Un lugar para pensar y construir juntos"}</small></div></div><span className={`connection ${error ? "bad" : ""}`}><i/>{error ? "Sin conexión" : loaded ? "Conectado" : "Conectando"}</span></header>
    <div className="chat-toolbar"><Search size={14}/><input aria-label="Buscar en los mensajes cargados" placeholder="Buscar en esta conversación…" value={query} onChange={e => setQuery(e.target.value)}/>{query && <button className="icon-button" aria-label="Limpiar búsqueda" onClick={() => setQuery("")}><X size={14}/></button>}<small>{filtered.length} mensajes cargados</small></div>
    {hasOlder && <button className="history-button" disabled={olderBusy || !!query} onClick={older}>{olderBusy ? "Cargando…" : "Cargar mensajes anteriores"}</button>}
    <div className="virtual-chat">
      {!filtered.length ? <div className="empty-state"><div className="empty-icon">{loaded ? <Hash size={26}/> : <LoaderCircle className="spin" size={26}/>}</div><h3>{query ? "Sin coincidencias" : loaded ? "Todo empieza con una conversación" : "Cargando tu conversación"}</h3><p>{error || (query ? "Probá otra palabra." : loaded ? "Escribí cuando quieras. Este espacio es tuyo." : "Conectando con el equipo SEAL…")}</p></div> : <Virtuoso key={query ? "search" : "timeline"} ref={list} data={filtered} firstItemIndex={query ? 0 : firstIndex} initialTopMostItemIndex={filtered.length - 1} followOutput={bottom => bottom ? "smooth" : false} atBottomStateChange={setAtBottom} computeItemKey={(_,m) => m.id} increaseViewportBy={250} itemContent={(_,m) => {
        const sender = (m.sender_name || m.from || "SEAL").toUpperCase();
        return <article className="message"><div className="avatar" style={{ "--agent": COLORS[sender] || "#94a3b8" } as React.CSSProperties}>{sender[0]}</div><div className="message-body"><div className="message-meta"><b style={{ color: COLORS[sender] || "#cbd5e1" }}>{sender}</b><time dateTime={m.created_at || m.timestamp}>{time(m.created_at || m.timestamp)}</time></div><MessageContent message={m}/></div></article>;
      }}/>}
      {!atBottom && <button className="jump-bottom" onClick={() => list.current?.scrollToIndex({ index: "LAST", behavior: "smooth" })}><ArrowDown size={14}/> Ir al último mensaje</button>}
    </div>
    {error && <div className="inline-error" role="alert">{error}<button onClick={() => void load()}>Reintentar</button></div>}
    {!heartbeatOnly && <div className="composer-wrap"><form className="composer" onSubmit={send}>
      {file && <div className="selected-file"><Paperclip size={14}/><span>{file.name} · {(file.size / 1024).toFixed(0)} KB</span><button type="button" className="icon-button" aria-label="Quitar adjunto" disabled={sending} onClick={() => setFile(null)}><X size={14}/></button></div>}
      <div className="composer-input"><textarea ref={input} disabled={sending} value={draft} onChange={e => { setDraft(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }} placeholder={`Escribí a ${title}…`} aria-label="Mensaje" rows={2} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }}/></div>
      <div className="composer-actions"><input ref={fileInput} type="file" hidden onChange={e => { selectFile(e.target.files?.[0]); e.target.value = ""; }}/><button className="icon-button attach-button" type="button" title="Adjuntar archivo" aria-label="Adjuntar archivo" disabled={sending || capturing} onClick={() => fileInput.current?.click()}><Paperclip size={18}/></button><CaptureControls disabled={sending} onFile={selectFile} onError={setError} onBusyChange={setCapturing}/><small>{capturing ? "Terminá la grabación antes de enviar" : "Enter para enviar · Shift + Enter para nueva línea"}</small><button className="send-button" disabled={(!draft.trim() && !file) || sending || capturing} aria-label="Enviar mensaje">{sending ? <LoaderCircle className="spin" size={17}/> : <ArrowUp size={18}/>}</button></div>
    </form><p className="composer-note">{isDm ? "Solo los participantes tienen acceso a esta conversación." : "Las respuestas del equipo aparecerán aquí."}</p></div>}
    {dragging && <div className="drop-hint"><Paperclip size={30}/>Soltá tu archivo aquí</div>}
  </section>;
}
