"use client";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy, FileDown, Volume2 } from "lucide-react";
import type { Message } from "@/lib/studio";

function uploadPath(path?: string) { return path && /^\/uploads\/[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)+$/.test(path) ? `/bridge${path}` : null; }
export default function MessageContent({ message }: { message: Message }) {
  const text = message.content || message.message || "";
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const attachment = uploadPath(message.file_url);
  const kind = message.message_type || message.type;
  async function copy() {
    try { await navigator.clipboard.writeText(text); setCopied(true); setCopyError(false); window.setTimeout(() => setCopied(false), 1800); }
    catch { setCopyError(true); }
  }
  return <>
    <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
      img: ({ src, alt }) => typeof src === "string" && uploadPath(src) ? <img src={uploadPath(src)!} alt={alt || "Imagen"} loading="lazy" /> : <span>{alt || "Imagen externa"}</span>,
    }}>{text}</ReactMarkdown></div>
    {attachment && <a className="attachment" href={attachment} target="_blank" rel="noopener noreferrer"><FileDown size={16}/>{message.filename || "Archivo adjunto"}</a>}
    {attachment && /\.(png|jpe?g|webp|gif)$/i.test(attachment) && <a href={attachment} target="_blank" rel="noopener noreferrer"><img className="attachment-preview" src={attachment} alt={message.filename || "Adjunto"} loading="lazy" /></a>}
    {attachment && (kind === "audio" || /\.(mp3|wav|ogg|m4a|flac)$/i.test(attachment)) && <audio className="media-player" controls preload="none" src={attachment}/>}
    {attachment && kind !== "audio" && (kind === "video" || /\.(mp4|webm|mov)$/i.test(attachment)) && <video className="media-player" controls playsInline preload="none" src={attachment}/>}
    <button className="copy-message" onClick={copy} aria-label="Copiar mensaje">{copied ? <Check size={12}/> : <Copy size={12}/>} {copied ? "Copiado" : copyError ? "Seleccioná el texto para copiar" : "Copiar"}</button>
    <button className="copy-message" aria-label="Leer o detener lectura en voz alta" title="Volvé a pulsar para detener" onClick={() => { if ("speechSynthesis" in window) { if (speechSynthesis.speaking) { speechSynthesis.cancel(); return; } const utterance = new SpeechSynthesisUtterance(text); utterance.lang = "es-PE"; speechSynthesis.speak(utterance); } }}><Volume2 size={12}/>Leer / detener</button>
  </>;
}
