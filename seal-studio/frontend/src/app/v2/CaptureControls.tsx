"use client";
import { useEffect, useRef, useState } from "react";
import { Mic, Video, Square, X } from "lucide-react";

// Capture is local until the user explicitly submits the resulting attachment.
export default function CaptureControls({ disabled, onFile, onError, onBusyChange }: { disabled: boolean; onFile: (file: File) => void; onError: (error: string) => void; onBusyChange: (busy: boolean) => void }) {
  const [mode, setMode] = useState<"audio" | "video" | null>(null);
  const [busy, setBusy] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const preview = useRef<HTMLVideoElement>(null);
  const alive = useRef(true);
  const discard = useRef(false);
  const clock = useRef<ReturnType<typeof setInterval> | null>(null);
  const limit = useRef<ReturnType<typeof setTimeout> | null>(null);
  function cleanup() {
    stream.current?.getTracks().forEach(t => t.stop()); stream.current = null;
    if (clock.current) clearInterval(clock.current);
    if (limit.current) clearTimeout(limit.current);
  }
  useEffect(() => { alive.current = true; return () => { alive.current = false; discard.current = true; if (recorder.current?.state === "recording") recorder.current.stop(); cleanup(); }; }, []);
  useEffect(() => { if (preview.current && stream.current) preview.current.srcObject = stream.current; }, [mode]);
  useEffect(() => { onBusyChange(busy || mode !== null); }, [busy, mode, onBusyChange]);
  async function start(kind: "audio" | "video") {
    if (busy || mode) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) { onError("Para grabar desde otro equipo necesitás abrir Studio mediante HTTPS. Mientras tanto, podés adjuntar audio o video guardado."); return; }
    if (typeof MediaRecorder === "undefined") { onError("Este navegador no permite grabar. Podés adjuntar un archivo de audio o video."); return; }
    setBusy(true); discard.current = false;
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true, video: kind === "video" ? { width: { ideal: 640 }, height: { ideal: 360 } } : false });
      if (!alive.current) { media.getTracks().forEach(t => t.stop()); return; }
      stream.current = media;
      const types = kind === "video" ? ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"] : ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
      const mimeType = types.find(type => MediaRecorder.isTypeSupported(type));
      const rec = new MediaRecorder(media, { ...(mimeType ? { mimeType } : {}), videoBitsPerSecond: 800000, audioBitsPerSecond: 64000 });
      recorder.current = rec;
      const parts: Blob[] = []; let bytes = 0;
      rec.ondataavailable = e => { if (e.data.size) { parts.push(e.data); bytes += e.data.size; if (bytes > 24 * 1024 * 1024 && rec.state === "recording") rec.stop(); } };
      rec.onerror = () => { discard.current = true; cleanup(); if (alive.current) { setMode(null); onError("La grabación falló. No se envió ningún archivo."); } };
      rec.onstop = () => {
        cleanup();
        if (!alive.current) return;
        setMode(null);
        if (discard.current || !parts.length) return;
        const blob = new Blob(parts, { type: rec.mimeType || mimeType || (kind === "video" ? "video/webm" : "audio/webm") });
        const ext = blob.type.includes("mp4") ? "mp4" : "webm";
        onFile(new File([blob], `${kind}-${Date.now()}.${ext}`, { type: blob.type }));
      };
      rec.start(1000); setMode(kind); setSeconds(0);
      clock.current = setInterval(() => setSeconds(s => s + 1), 1000);
      limit.current = setTimeout(() => { if (rec.state === "recording") rec.stop(); }, 180000);
    } catch { cleanup(); onError("No se pudo acceder a cámara o micrófono. Revisá los permisos del navegador."); }
    finally { if (alive.current) setBusy(false); }
  }
  function stop(cancel: boolean) { discard.current = cancel; if (recorder.current?.state === "recording") recorder.current.stop(); }
  return <>
    {!mode ? <><button type="button" className="icon-button" aria-label="Grabar audio" title="Grabar audio" disabled={disabled || busy} onClick={() => void start("audio")}><Mic size={18}/></button><button type="button" className="icon-button" aria-label="Grabar video" title="Grabar video" disabled={disabled || busy} onClick={() => void start("video")}><Video size={18}/></button></> : <div className="recording-controls"><span className="recording-dot"/><span>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2,"0")}</span><button type="button" className="icon-button" aria-label="Terminar grabación" title="Terminar y adjuntar; no envía" onClick={() => stop(false)}><Square size={15}/></button><button type="button" className="icon-button" aria-label="Descartar grabación" onClick={() => stop(true)}><X size={15}/></button><small>Máx. 3 min · todavía no se envía</small></div>}
    {mode === "video" && <video className="capture-preview" ref={preview} autoPlay muted playsInline aria-label="Vista previa local de cámara"/>}
  </>;
}
