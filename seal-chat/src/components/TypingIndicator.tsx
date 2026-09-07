import { Bot } from 'lucide-react';

interface Props {
  agents: string[];
}

const AGENT_COLORS: Record<string, string> = {
  ADA: 'text-seal-accent',
  JARVIS: 'text-seal-yellow',
  DUM: 'text-seal-green',
  ALICE: 'text-purple-400',
};

export default function TypingIndicator({ agents }: Props) {
  if (agents.length === 0) return null;

  const names = agents.join(' y ');
  const color = agents.length === 1 ? (AGENT_COLORS[agents[0]] || 'text-seal-400') : 'text-seal-400';

  return (
    <div className="flex items-center gap-2 px-6 py-1.5 msg-enter">
      <Bot size={14} className={color} />
      <span className={`text-xs ${color}`}>{names}</span>
      <span className="text-xs text-seal-500">
        {agents.length === 1 ? 'esta escribiendo' : 'estan escribiendo'}
      </span>
      <div className="flex gap-0.5">
        <div className="w-1.5 h-1.5 bg-seal-400 rounded-full typing-dot" />
        <div className="w-1.5 h-1.5 bg-seal-400 rounded-full typing-dot" />
        <div className="w-1.5 h-1.5 bg-seal-400 rounded-full typing-dot" />
      </div>
    </div>
  );
}
