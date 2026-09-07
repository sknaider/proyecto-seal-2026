import { Bot, User as UserIcon, Reply, Download } from 'lucide-react';
import type { Message } from '../types';

const AGENT_STYLES: Record<string, { bg: string; accent: string; icon: string }> = {
  ADA:    { bg: 'bg-seal-accent/5 border-seal-accent/20', accent: 'text-seal-accent', icon: 'A' },
  JARVIS: { bg: 'bg-seal-yellow/5 border-seal-yellow/20', accent: 'text-seal-yellow', icon: 'J' },
  DUM:    { bg: 'bg-seal-green/5 border-seal-green/20', accent: 'text-seal-green', icon: 'D' },
  ALICE:  { bg: 'bg-purple-400/5 border-purple-400/20', accent: 'text-purple-400', icon: 'AL' },
};

function timeAgo(ts: string): string {
  const d = new Date(ts);
  const now = Date.now();
  const diff = Math.floor((now - d.getTime()) / 1000);
  if (diff < 60) return 'ahora';
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return d.toLocaleDateString('es-PE', { day: '2-digit', month: 'short' });
}

function formatTimestamp(ts: string): string {
  return new Date(ts).toLocaleString('es-PE', {
    hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short',
  });
}

function renderContent(msg: Message) {
  if (msg.type === 'image' && msg.file_url) {
    return (
      <div>
        {msg.message && <p className="mb-2 text-sm">{msg.message}</p>}
        <img
          src={msg.file_url}
          alt="uploaded"
          className="max-w-sm rounded-lg cursor-pointer hover:opacity-90 transition-opacity"
          onClick={() => window.open(msg.file_url!, '_blank')}
        />
      </div>
    );
  }
  if (msg.type === 'audio' && msg.file_url) {
    return (
      <div>
        <audio controls src={msg.file_url} className="max-w-xs" />
        {msg.message && <p className="mt-1 text-xs text-seal-400">{msg.message}</p>}
      </div>
    );
  }
  if (msg.type === 'file' && msg.file_url) {
    return (
      <a
        href={msg.file_url}
        download={msg.file_name}
        className="flex items-center gap-2 bg-seal-700 rounded-lg px-3 py-2 hover:bg-seal-600 transition-colors"
      >
        <Download size={16} className="text-seal-accent" />
        <span className="text-sm">{msg.file_name || 'Archivo'}</span>
      </a>
    );
  }

  // Text with basic markdown-like formatting
  const text = msg.message || '';
  return (
    <p className="text-sm whitespace-pre-wrap break-words leading-relaxed">
      {text.split(/(`[^`]+`)/g).map((part, i) =>
        part.startsWith('`') && part.endsWith('`')
          ? <code key={i} className="bg-seal-700 px-1 py-0.5 rounded text-seal-accent text-xs font-mono">{part.slice(1, -1)}</code>
          : part.split(/(\*\*[^*]+\*\*)/g).map((p2, j) =>
              p2.startsWith('**') && p2.endsWith('**')
                ? <strong key={`${i}-${j}`} className="font-semibold text-white">{p2.slice(2, -2)}</strong>
                : <span key={`${i}-${j}`}>{p2}</span>
            )
      )}
    </p>
  );
}

interface Props {
  msg: Message;
  isOwn: boolean;
  onReply?: (msg: Message) => void;
}

export default function MessageBubble({ msg, isOwn, onReply }: Props) {
  const sender = (msg.from || '').toUpperCase();
  const agentStyle = AGENT_STYLES[sender];
  const isAgent = !!agentStyle;

  return (
    <div className={`msg-enter group flex gap-3 px-4 py-1.5 hover:bg-white/[0.02] transition-colors ${isOwn ? 'flex-row-reverse' : ''}`}>
      {/* Avatar */}
      <div className={`w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold ${
        isAgent
          ? `${agentStyle.bg} border ${agentStyle.accent}`
          : 'bg-seal-600 text-seal-400'
      }`}>
        {isAgent ? <Bot size={14} /> : <UserIcon size={14} />}
      </div>

      {/* Content */}
      <div className={`flex-1 min-w-0 ${isOwn ? 'text-right' : ''}`}>
        <div className={`flex items-baseline gap-2 mb-0.5 ${isOwn ? 'justify-end' : ''}`}>
          <span className={`text-xs font-semibold ${agentStyle?.accent || 'text-seal-400'}`}>
            {msg.from || 'unknown'}
          </span>
          <span className="text-[10px] text-seal-500" title={formatTimestamp(msg.timestamp)}>
            {timeAgo(msg.timestamp)}
          </span>
        </div>

        <div className={`inline-block max-w-[85%] rounded-xl px-3 py-2 ${
          isOwn
            ? 'bg-seal-accent/10 text-white border border-seal-accent/20'
            : isAgent
              ? `${agentStyle.bg} border text-gray-200`
              : 'bg-seal-700 text-gray-200'
        } ${isOwn ? 'rounded-tr-sm' : 'rounded-tl-sm'}`}>
          {renderContent(msg)}
        </div>

        {/* Reply button (on hover) */}
        {onReply && (
          <button
            onClick={() => onReply(msg)}
            className="invisible group-hover:visible ml-2 text-seal-500 hover:text-seal-accent transition-colors"
          >
            <Reply size={14} />
          </button>
        )}
      </div>
    </div>
  );
}
