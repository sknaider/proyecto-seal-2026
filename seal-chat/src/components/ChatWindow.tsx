import { useEffect, useRef, useState, useCallback } from 'react';
import { Hash, Loader2, ChevronDown, Search, X, Bell, BellOff } from 'lucide-react';
import MessageBubble from './MessageBubble';
import ChatInput from './ChatInput';
import TypingIndicator from './TypingIndicator';
import type { Message, User } from '../types';
import { api } from '../lib/api';

interface Props {
  user: User;
  messages: Message[];
  activeChannel: string;
  connected: boolean;
  loading: boolean;
  typingAgents: string[];
  onSend: (text: string, type?: string) => void;
}

export default function ChatWindow({ user, messages, activeChannel, connected, loading, typingAgents, onSend }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Message[]>([]);
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);

  // Browser notifications
  const requestNotifications = useCallback(() => {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'granted') {
      setNotificationsEnabled(prev => !prev);
    } else if (Notification.permission !== 'denied') {
      Notification.requestPermission().then(perm => {
        if (perm === 'granted') setNotificationsEnabled(true);
      });
    }
  }, []);

  // Send notification for new messages when tab is not focused
  useEffect(() => {
    if (!notificationsEnabled || messages.length === 0) return;
    const lastMsg = messages[messages.length - 1];
    if (!lastMsg || lastMsg.from?.toLowerCase() === user.username?.toLowerCase()) return;
    if (document.hasFocus()) return;
    const notification = new Notification(`${lastMsg.from} en #${activeChannel}`, {
      body: lastMsg.message?.slice(0, 100) || 'Nuevo mensaje',
      icon: '/favicon.ico',
      tag: `seal-${lastMsg.id}`,
    });
    notification.onclick = () => { window.focus(); notification.close(); };
  }, [messages.length, notificationsEnabled, activeChannel, user.username]);

  // Track scroll position
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShowScrollBtn(distFromBottom > 300);
  }, []);

  // Auto-scroll to bottom on new messages (only if near bottom)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom < 150) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  // Scroll to bottom on channel switch
  useEffect(() => {
    bottomRef.current?.scrollIntoView();
    setShowScrollBtn(false);
  }, [activeChannel]);

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const handleUpload = async (file: File) => {
    try {
      await api.upload(file, activeChannel);
    } catch (e) {
      console.error('Upload failed:', e);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    try {
      const res = await api.search(searchQuery);
      setSearchResults(res.results || []);
    } catch {
      setSearchResults([]);
    }
  };

  // Group messages by date
  const groupedMessages = messages.reduce<{ date: string; msgs: Message[] }[]>((acc, msg) => {
    const d = new Date(msg.timestamp);
    const dateStr = d.toLocaleDateString('es-PE', { weekday: 'long', day: 'numeric', month: 'long' });
    const last = acc[acc.length - 1];
    if (last && last.date === dateStr) {
      last.msgs.push(msg);
    } else {
      acc.push({ date: dateStr, msgs: [msg] });
    }
    return acc;
  }, []);

  return (
    <div className="flex-1 flex flex-col h-full bg-seal-900">
      {/* Channel header */}
      <div className="h-14 border-b border-seal-700 bg-seal-800/50 backdrop-blur-sm flex items-center px-4 gap-3 flex-shrink-0">
        <Hash size={18} className="text-seal-accent" />
        <h3 className="font-semibold text-white text-sm">{activeChannel}</h3>
        <div className="flex-1" />
        {!connected && (
          <div className="flex items-center gap-1.5 text-seal-yellow text-xs">
            <Loader2 size={12} className="animate-spin" />
            Reconectando...
          </div>
        )}
        <button
          onClick={requestNotifications}
          className={`p-1.5 rounded-md transition-colors ${notificationsEnabled ? 'text-seal-accent bg-seal-accent/10' : 'text-seal-500 hover:text-seal-400'}`}
          title={notificationsEnabled ? 'Desactivar notificaciones' : 'Activar notificaciones'}
        >
          {notificationsEnabled ? <Bell size={16} /> : <BellOff size={16} />}
        </button>
        <button
          onClick={() => { setSearchOpen(!searchOpen); setSearchResults([]); setSearchQuery(''); }}
          className={`p-1.5 rounded-md transition-colors ${searchOpen ? 'text-seal-accent bg-seal-accent/10' : 'text-seal-500 hover:text-seal-400'}`}
        >
          {searchOpen ? <X size={16} /> : <Search size={16} />}
        </button>
        <span className="text-xs text-seal-500">{messages.length} msgs</span>
      </div>

      {/* Search bar */}
      {searchOpen && (
        <div className="border-b border-seal-700 bg-seal-800 px-4 py-2 flex gap-2">
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="Buscar mensajes..."
            className="flex-1 bg-seal-700 border border-seal-600 rounded-md px-3 py-1.5 text-sm text-white placeholder-seal-500 focus:outline-none focus:border-seal-accent"
            autoFocus
          />
          <button onClick={handleSearch} className="px-3 py-1.5 bg-seal-accent/10 text-seal-accent rounded-md text-sm hover:bg-seal-accent/20">
            Buscar
          </button>
        </div>
      )}

      {/* Search results overlay */}
      {searchResults.length > 0 && (
        <div className="border-b border-seal-700 bg-seal-800/90 max-h-48 overflow-y-auto">
          <p className="px-4 py-1.5 text-xs text-seal-500">{searchResults.length} resultados</p>
          {searchResults.map(msg => (
            <div key={msg.id} className="px-4 py-2 hover:bg-seal-700/50 cursor-pointer border-t border-seal-700/50">
              <div className="flex items-baseline gap-2">
                <span className="text-xs font-semibold text-seal-accent">{msg.from}</span>
                <span className="text-[10px] text-seal-500">{new Date(msg.timestamp).toLocaleString('es-PE')}</span>
              </div>
              <p className="text-xs text-seal-400 truncate">{msg.message}</p>
            </div>
          ))}
        </div>
      )}

      {/* Messages area */}
      <div ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto py-4 relative">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 size={24} className="animate-spin text-seal-accent" />
          </div>
        ) : messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-seal-500">
            <Hash size={40} className="mb-3 text-seal-600" />
            <p className="text-sm">Sin mensajes en #{activeChannel}</p>
            <p className="text-xs mt-1">Se el primero en escribir</p>
          </div>
        ) : (
          <>
            {groupedMessages.map(group => (
              <div key={group.date}>
                {/* Date separator */}
                <div className="flex items-center gap-3 px-4 py-3">
                  <div className="flex-1 h-px bg-seal-700" />
                  <span className="text-[10px] text-seal-500 font-medium uppercase">{group.date}</span>
                  <div className="flex-1 h-px bg-seal-700" />
                </div>
                {group.msgs.map(msg => (
                  <MessageBubble
                    key={msg.id || msg.db_id}
                    msg={msg}
                    isOwn={msg.from?.toLowerCase() === user.username?.toLowerCase()}
                  />
                ))}
              </div>
            ))}
            <TypingIndicator agents={typingAgents} />
            <div ref={bottomRef} />
          </>
        )}
      </div>

      {/* Scroll to bottom button */}
      {showScrollBtn && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-24 right-6 w-10 h-10 bg-seal-700 border border-seal-600 rounded-full flex items-center justify-center text-seal-400 hover:text-seal-accent hover:border-seal-accent transition-colors shadow-lg"
        >
          <ChevronDown size={20} />
        </button>
      )}

      {/* Input */}
      <ChatInput
        onSend={onSend}
        onUpload={handleUpload}
        disabled={!connected}
      />
    </div>
  );
}
