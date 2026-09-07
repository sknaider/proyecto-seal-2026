"use client";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy, FileDown } from "lucide-react";
import type { Message } from "@/lib/studio";

export default function MessageContent({ message }: { message: Message }) {
  const text = message.content || message.message || "";
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const attachment = message.file_url?.startsWith("/uploads/") ? `/bridge${message.file_url}` : null;
  async function copy() {
    try { await navigator.clipboard.writeText(text); setCopied(true); setCopyError(false); window.setTimeout(() => setCopied(false), 1800); }
    catch { setCopyError(true); }
  }
  return <>
    <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
      img: ({ src, alt }) => typeof src === "string" && src.startsWith("/uploads/") ? <img src={`/bridge${src}`} alt={alt || "Imagen"} loading="lazy" /> : <span>{alt || "Imagen externa"}</span>,
    }}>{text}</ReactMarkdown></div>
    {attachment && <a className="attachment" href={attachment} target="_blank" rel="noopener noreferrer"><FileDown size={16}/>{message.filename || "Archivo adjunto"}</a>}
    {attachment && /\.(png|jpe?g|webp|gif)$/i.test(attachment) && <a href={attachment} target="_blank" rel="noopener noreferrer"><img className="attachment-preview" src={attachment} alt={message.filename || "Adjunto"} loading="lazy" /></a>}
    <button className="copy-message" onClick={copy} aria-label="Copiar mensaje">{copied ? <Check size={12}/> : <Copy size={12}/>} {copied ? "Copiado" : copyError ? "Seleccioná el texto para copiar" : "Copiar"}</button>
  </>;
}
