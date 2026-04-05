import React, { useState, useEffect } from 'react';
import TitleBar from './components/TitleBar.jsx';
import Sidebar from './components/Sidebar.jsx';
import EditorPanel from './components/EditorPanel.jsx';
import TerminalPanel from './components/TerminalPanel.jsx';
import ChatPanel from './components/ChatPanel.jsx';
import RightPanel from './components/RightPanel.jsx';
import StatusBar from './components/StatusBar.jsx';

const SEAL_BRIDGE = 'http://localhost:8766';
const SEAL_CHAT = 'http://localhost:8765';

export default function App() {
  const [activeBottomTab, setActiveBottomTab] = useState('chat');
  const [showBottom, setShowBottom] = useState(true);
  const [showRight, setShowRight] = useState(true);
  const [openFiles, setOpenFiles] = useState([]);
  const [activeFile, setActiveFile] = useState(null);
  const [teamStatus, setTeamStatus] = useState(null);

  // Team status polling
  useEffect(() => {
    const fetch_ = async () => {
      try {
        const r = await fetch(`${SEAL_BRIDGE}/api/health`);
        if (r.ok) {
          const d = await r.json();
          setTeamStatus(d);
        }
      } catch {}
    };
    fetch_();
    const iv = setInterval(fetch_, 10000);
    return () => clearInterval(iv);
  }, []);

  // Menu events from main process
  useEffect(() => {
    if (!window.seal) return;
    window.seal.on('toggle-terminal', () => { setActiveBottomTab('terminal'); setShowBottom(true); });
    window.seal.on('toggle-chat', () => { setActiveBottomTab('chat'); setShowBottom(true); });
    window.seal.on('toggle-soul', () => setShowRight(v => !v));
    window.seal.on('toggle-explorer', () => {}); // TODO
  }, []);

  const openFile = async (path) => {
    if (openFiles.find(f => f.path === path)) {
      setActiveFile(path);
      return;
    }
    try {
      const content = await window.seal.fs.readFile(path);
      const name = path.split('/').pop();
      setOpenFiles(prev => [...prev, { path, name, content, modified: false }]);
      setActiveFile(path);
    } catch (e) {
      console.error('Failed to open file:', e);
    }
  };

  const closeFile = (path) => {
    setOpenFiles(prev => prev.filter(f => f.path !== path));
    if (activeFile === path) {
      setActiveFile(openFiles.length > 1 ? openFiles[openFiles.length - 2]?.path : null);
    }
  };

  const updateFileContent = (path, content) => {
    setOpenFiles(prev => prev.map(f => f.path === path ? { ...f, content, modified: true } : f));
  };

  const saveFile = async (path) => {
    const file = openFiles.find(f => f.path === path);
    if (!file) return;
    try {
      await window.seal.fs.writeFile(path, file.content);
      setOpenFiles(prev => prev.map(f => f.path === path ? { ...f, modified: false } : f));
    } catch (e) {
      console.error('Failed to save:', e);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <TitleBar teamStatus={teamStatus} />

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left sidebar */}
        <Sidebar onOpenFile={openFile} />

        {/* Center */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Editor */}
          <div style={{ flex: showBottom ? '1 1 60%' : '1 1 100%', overflow: 'hidden' }}>
            <EditorPanel
              files={openFiles}
              activeFile={activeFile}
              onSelectFile={setActiveFile}
              onCloseFile={closeFile}
              onChangeContent={updateFileContent}
              onSave={saveFile}
            />
          </div>

          {/* Bottom panel (terminal / chat) */}
          {showBottom && (
            <div style={{ flex: '0 0 40%', borderTop: '1px solid var(--seal-border)', display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', height: 28, background: 'var(--seal-bg)', borderBottom: '1px solid var(--seal-border)' }}>
                {['chat', 'terminal'].map(tab => (
                  <button
                    key={tab}
                    onClick={() => setActiveBottomTab(tab)}
                    style={{
                      padding: '0 12px', border: 'none', cursor: 'pointer',
                      fontSize: 11, textTransform: 'uppercase', letterSpacing: 1,
                      background: 'transparent',
                      color: activeBottomTab === tab ? 'var(--seal-accent)' : 'var(--seal-text-dim)',
                      borderBottom: activeBottomTab === tab ? '2px solid var(--seal-accent)' : '2px solid transparent',
                    }}
                  >
                    {tab}
                  </button>
                ))}
                <div style={{ flex: 1 }} />
                <button
                  onClick={() => setShowBottom(false)}
                  style={{ padding: '0 8px', border: 'none', background: 'transparent', color: 'var(--seal-text-dim)', cursor: 'pointer', fontSize: 14 }}
                >
                  ×
                </button>
              </div>
              <div style={{ flex: 1, overflow: 'hidden' }}>
                {activeBottomTab === 'chat' && <ChatPanel />}
                {activeBottomTab === 'terminal' && <TerminalPanel />}
              </div>
            </div>
          )}
        </div>

        {/* Right sidebar */}
        {showRight && <RightPanel teamStatus={teamStatus} />}
      </div>

      <StatusBar activeFile={activeFile} />
    </div>
  );
}
