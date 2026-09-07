"use client";
import { FormEvent, useEffect, useState } from "react";
import { ArrowUpRight, Check, Download, Mail, CalendarDays, FileText, Plus, Timer, Play, Pause, RotateCcw, Trash2 } from "lucide-react";
import type { User } from "@/lib/studio";
import GmailPanel from "./GmailPanel";
type Task = { id: string; title: string; done: boolean };
type Workspace = { tasks: Task[]; notes: string };

export default function Productivity({ user }: { user: User }) {
  const key = `seal-workspace-v1:${user.id ?? user.username}`;
  const [data, setData] = useState<Workspace>({ tasks: [], notes: "" });
  const [damaged, setDamaged] = useState<string | null>(null), [removed, setRemoved] = useState<Task[]>([]);
  const [ready, setReady] = useState(false), [storageError, setStorageError] = useState("");
  const [title, setTitle] = useState(""), [running, setRunning] = useState(false), [remaining, setRemaining] = useState(25 * 60);
  const [deadline, setDeadline] = useState(0);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw) {
        setDamaged(raw);
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed.tasks) || typeof parsed.notes !== "string" || !parsed.tasks.every((t: Task) => typeof t?.id === "string" && typeof t.title === "string" && typeof t.done === "boolean")) throw new Error("Formato de respaldo inválido");
        setData(parsed);
      }
      setDamaged(null);
      setReady(true);
    } catch { setStorageError("No se pudo leer el espacio local. No se sobrescribirá; conservá una copia antes de recuperar los datos."); }
  }, [key]);
  useEffect(() => { if (ready) { try { localStorage.setItem(key, JSON.stringify(data)); setStorageError(""); } catch { setStorageError("El navegador no pudo guardar los cambios. Exportá tu respaldo antes de salir."); } } }, [data, key, ready]);
  useEffect(() => {
    if (!running) return;
    const tick = () => { const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000)); setRemaining(seconds); if (!seconds) setRunning(false); };
    tick(); const interval = window.setInterval(tick, 250); return () => clearInterval(interval);
  }, [deadline, running]);
  function add(e: FormEvent) { e.preventDefault(); if (!title.trim() || !ready) return; const id = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`; setData(d => ({ ...d, tasks: [...d.tasks, { id, title: title.trim(), done: false }] })); setTitle(""); }
  function exportData() { const blob = new Blob([damaged ?? JSON.stringify({ version: 1, user: user.username, exported: new Date().toISOString(), ...data }, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `seal-espacio-${damaged !== null ? "recuperar-" : ""}${new Date().toISOString().slice(0,10)}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  function removeTask(task: Task) { setRemoved(r => [...r, task]); setData(d => ({ ...d, tasks: d.tasks.filter(t => t.id !== task.id) })); }
  return <section className="dashboard productivity"><div className="section-title"><p className="eyebrow">MENOS RUIDO. MÁS INTENCIÓN.</p><h2>Hacé espacio para lo importante.</h2><p>Tu tablero personal para organizar el día y encontrar el foco.</p></div>
    <div className="local-notice"><span>Tareas y notas se guardan <b>solo en este navegador</b>, por cuenta. No son un respaldo del servidor.</span><button className="secondary" onClick={exportData} disabled={!ready && damaged === null}><Download size={14}/>Exportar respaldo</button></div>
    {storageError && <p className="inline-error" role="alert">{storageError}</p>}
    {damaged !== null && <button className="secondary" onClick={() => { if (window.confirm("Se reiniciarán únicamente las tareas y notas de esta cuenta en este navegador. ¿Ya exportaste los datos dañados y querés continuar?")) { setData({ tasks: [], notes: "" }); setDamaged(null); setReady(true); } }}>Reiniciar espacio local después de respaldar</button>}
    {!!removed.length && <div className="local-notice" role="status"><span>{removed.length} tareas eliminadas en esta vista.</span><button className="secondary" onClick={() => { const task = removed[removed.length - 1]; setData(d => ({ ...d, tasks: [...d.tasks, task] })); setRemoved(r => r.slice(0,-1)); }}>Deshacer última eliminación</button></div>}
    <div className="productivity-grid"><article className="tool-card task-card"><div className="tool-heading"><h3><Check size={18}/>Tu siguiente paso</h3><small>{data.tasks.filter(t => t.done).length}/{data.tasks.length}</small></div><form className="task-add" onSubmit={add}><input aria-label="Nueva tarea" placeholder="¿Qué querés lograr hoy?" value={title} maxLength={500} disabled={!ready} onChange={e => setTitle(e.target.value)}/><button className="icon-button" disabled={!title.trim() || !ready} aria-label="Agregar tarea"><Plus size={18}/></button></form><div className="task-list">{!data.tasks.length && <p className="empty">Una tarea pequeña también es un comienzo.</p>}{data.tasks.map(task => <div className={"task-row " + (task.done ? "done" : "")} key={task.id}><input type="checkbox" aria-label={`Completar ${task.title}`} checked={task.done} onChange={() => setData(d => ({ ...d, tasks: d.tasks.map(t => t.id === task.id ? { ...t, done: !t.done } : t) }))}/><span>{task.title}</span><button className="icon-button" aria-label={`Eliminar ${task.title}`} onClick={() => removeTask(task)}><Trash2 size={14}/></button></div>)}</div></article>
      <article className="tool-card focus-card"><h3><Timer size={18}/>Un momento de foco</h3><p>Una cosa a la vez. Todo lo demás puede esperar.</p><div className="focus-time" role="timer">{Math.floor(remaining / 60)}<span>:</span>{String(remaining % 60).padStart(2,"0")}</div><div className="focus-actions"><button className="primary" onClick={() => { if (running) setRunning(false); else { const seconds = remaining || 25*60; setRemaining(seconds); setDeadline(Date.now() + seconds*1000); setRunning(true); } }}>{running ? <Pause size={16}/> : <Play size={16}/>} {running ? "Pausar" : "Comenzar"}</button><button className="icon-button" aria-label="Reiniciar temporizador" onClick={() => { setRunning(false); setRemaining(25*60); }}><RotateCcw size={17}/></button></div>{remaining === 0 && <p role="status">Bloque completado. Tomate un descanso.</p>}<small>25 minutos · se reinicia al salir de esta vista</small></article>
      <article className="tool-card notes-card"><h3><FileText size={18}/>Ideas que no querés perder</h3><textarea aria-label="Notas personales" placeholder="Soltá una idea, anotá una decisión, guardá una pregunta…" value={data.notes} maxLength={100000} disabled={!ready} onChange={e => setData(d => ({ ...d, notes: e.target.value }))}/><small>{storageError ? "Guardado pendiente" : "Guardado local automático"}</small></article>
      <article className="tool-card"><h3><Mail size={18}/>Tus herramientas, a mano</h3><div className="integration-links"><a href="https://mail.google.com/" target="_blank" rel="noopener noreferrer"><Mail size={20}/><span><b>Gmail</b><small>Abrir tu correo en Google</small></span><ArrowUpRight size={17}/></a><a href="https://calendar.google.com/" target="_blank" rel="noopener noreferrer"><CalendarDays size={20}/><span><b>Calendario</b><small>Agenda y reuniones</small></span><ArrowUpRight size={17}/></a><a href="https://drive.google.com/" target="_blank" rel="noopener noreferrer"><FileText size={20}/><span><b>Google Drive</b><small>Documentos y archivos</small></span><ArrowUpRight size={17}/></a></div><p className="integration-note"><b>Integración Gmail pendiente.</b> Estos accesos abren Google; Studio todavía no lee tu correo. La conexión requiere configurar OAuth web y que autorices tu cuenta.</p></article></div>
    <GmailPanel/>
  </section>;
}
