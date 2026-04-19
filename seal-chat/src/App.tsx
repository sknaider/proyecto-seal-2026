import { useState } from 'react';
import { useAuth } from './hooks/useAuth';
import { useChat } from './hooks/useChat';
import Login from './pages/Login';
import Sidebar from './components/Sidebar';
import ChatWindow from './components/ChatWindow';
import { Menu, X } from 'lucide-react';

export default function App() {
  const { user, loading: authLoading, error, login, logout } = useAuth();
  const chat = useChat();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Auth loading
  if (authLoading) {
    return (
      <div className="h-full flex items-center justify-center bg-seal-900">
        <div className="text-center">
          <div className="w-12 h-12 border-2 border-seal-accent border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-seal-400 text-sm">Cargando SEAL Chat...</p>
        </div>
      </div>
    );
  }

  // Not logged in
  if (!user) {
    return <Login onLogin={login} error={error} />;
  }

  // Main chat
  return (
    <div className="h-full flex relative">
      {/* Mobile menu button */}
      <button
        onClick={() => setSidebarOpen(!sidebarOpen)}
        className="md:hidden fixed top-3 left-3 z-30 p-2 bg-seal-800 border border-seal-700 rounded-lg text-seal-400"
      >
        {sidebarOpen ? <X size={18} /> : <Menu size={18} />}
      </button>

      {/* Sidebar — hidden on mobile unless toggled */}
      <div className={`
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        md:translate-x-0
        fixed md:relative z-20
        transition-transform duration-200 ease-out
        h-full
      `}>
        <Sidebar
          user={user}
          channels={chat.channels}
          activeChannel={chat.activeChannel}
          connected={chat.connected}
          onSwitchChannel={(ch) => {
            chat.switchChannel(ch);
            setSidebarOpen(false);
          }}
          onCreateDM={chat.createDM}
          onLogout={logout}
        />
      </div>

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-10 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Chat window */}
      <ChatWindow
        user={user}
        messages={chat.messages}
        activeChannel={chat.activeChannel}
        connected={chat.connected}
        loading={chat.loading}
        typingAgents={chat.typingAgents}
        onSend={chat.sendMessage}
      />
    </div>
  );
}
