import { Hash, MessageSquare, Users, LogOut, Wifi, WifiOff, Plus, Search, Bot, User as UserIcon } from 'lucide-react';
import type { Channel, User } from '../types';
import { useState } from 'react';

interface Props {
  user: User;
  channels: Channel[];
  activeChannel: string;
  connected: boolean;
  onSwitchChannel: (ch: string) => void;
  onCreateDM: (target: string) => void;
  onLogout: () => void;
}

const AGENTS = ['ADA', 'JARVIS', 'ALICE'];
const AGENT_COLORS: Record<string, string> = {
  ADA: 'text-seal-accent',
  JARVIS: 'text-seal-yellow',
  DUM: 'text-seal-green',
  ALICE: 'text-purple-400',
};

function channelIcon(ch: Channel) {
  if (ch.type === 'dm') return <MessageSquare size={16} />;
  if (ch.type === 'private') return <Users size={16} />;
  return <Hash size={16} />;
}

export default function Sidebar({ user, channels, activeChannel, connected, onSwitchChannel, onCreateDM, onLogout }: Props) {
  const [search, setSearch] = useState('');
  const [showNewDM, setShowNewDM] = useState(false);

  const defaultChannels = [
    { id: 0, name: 'general', type: 'general' as const },
    { id: -1, name: 'web_chat', type: 'general' as const },
    { id: -2, name: 'alice', type: 'general' as const },
  ];

  const allChannels = [
    ...defaultChannels,
    ...channels.filter(c => !defaultChannels.find(d => d.name === c.name)),
  ];

  const filtered = search
    ? allChannels.filter(c => c.name.toLowerCase().includes(search.toLowerCase()))
    : allChannels;

  return (
    <div className="w-64 bg-seal-800 border-r border-seal-700 flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-seal-700">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-seal-accent/20 flex items-center justify-center">
            <span className="text-seal-accent font-bold text-sm">S</span>
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="font-semibold text-white text-sm truncate">SEAL Chat</h2>
            <div className="flex items-center gap-1 text-xs">
              {connected
                ? <><Wifi size={10} className="text-seal-green" /><span className="text-seal-green">Online</span></>
                : <><WifiOff size={10} className="text-seal-red" /><span className="text-seal-red">Offline</span></>
              }
            </div>
          </div>
        </div>
      </div>

      {/* Search */}
      <div className="px-3 py-2">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-seal-500" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Buscar canal..."
            className="w-full bg-seal-700 border border-seal-600 rounded-md pl-8 pr-3 py-1.5 text-xs text-white placeholder-seal-500 focus:outline-none focus:border-seal-accent"
          />
        </div>
      </div>

      {/* Channels */}
      <div className="flex-1 overflow-y-auto px-2 py-1">
        <div className="flex items-center justify-between px-2 py-1.5">
          <span className="text-xs font-semibold text-seal-400 uppercase tracking-wider">Canales</span>
        </div>
        {filtered.filter(c => c.type !== 'dm').map(ch => (
          <button
            key={ch.name}
            onClick={() => onSwitchChannel(ch.name)}
            className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-sm transition-colors ${
              activeChannel === ch.name
                ? 'bg-seal-accent/10 text-seal-accent'
                : 'text-seal-400 hover:bg-seal-700 hover:text-white'
            }`}
          >
            {channelIcon(ch)}
            <span className="truncate">{ch.name}</span>
          </button>
        ))}

        {/* DMs */}
        <div className="flex items-center justify-between px-2 py-1.5 mt-3">
          <span className="text-xs font-semibold text-seal-400 uppercase tracking-wider">Mensajes Directos</span>
          <button
            onClick={() => setShowNewDM(!showNewDM)}
            className="text-seal-500 hover:text-seal-accent transition-colors"
          >
            <Plus size={14} />
          </button>
        </div>

        {showNewDM && (
          <div className="px-2 py-1 space-y-1">
            {AGENTS.map(agent => (
              <button
                key={agent}
                onClick={() => { onCreateDM(agent); setShowNewDM(false); }}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-sm text-seal-400 hover:bg-seal-700 hover:text-white transition-colors"
              >
                <Bot size={14} className={AGENT_COLORS[agent]} />
                <span>Chat con {agent}</span>
              </button>
            ))}
          </div>
        )}

        {filtered.filter(c => c.type === 'dm').map(ch => (
          <button
            key={ch.name}
            onClick={() => onSwitchChannel(ch.name)}
            className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-sm transition-colors ${
              activeChannel === ch.name
                ? 'bg-seal-accent/10 text-seal-accent'
                : 'text-seal-400 hover:bg-seal-700 hover:text-white'
            }`}
          >
            <MessageSquare size={16} />
            <span className="truncate">{ch.name.replace('dm:', '').replace(/:.*/, '')}</span>
          </button>
        ))}
      </div>

      {/* User footer */}
      <div className="p-3 border-t border-seal-700">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-seal-600 flex items-center justify-center">
            <UserIcon size={14} className="text-seal-400" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-white truncate">{user.display_name || user.username}</p>
            <p className="text-xs text-seal-500">{user.role}</p>
          </div>
          <button onClick={onLogout} className="text-seal-500 hover:text-seal-red transition-colors">
            <LogOut size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
