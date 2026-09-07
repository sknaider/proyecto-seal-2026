import React from 'react';

export default function TitleBar({ teamStatus }) {
  const agents = [
    { name: 'ADA', color: '#a855f7' },
    { name: 'JARVIS', color: '#3b82f6' },
    { name: 'DUM', color: '#f59e0b' },
  ];

  return (
    <div style={{
      height: 32, background: 'var(--seal-surface)', borderBottom: '1px solid var(--seal-border)',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 12px',
      WebkitAppRegion: 'drag', userSelect: 'none',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: 'var(--seal-accent)', fontWeight: 700, fontSize: 12, letterSpacing: 1 }}>
          CÓDIGO SEAL
        </span>
        <span style={{ color: 'var(--seal-text-dim)', fontSize: 10 }}>v0.1</span>
      </div>
      <div style={{ display: 'flex', gap: 10, WebkitAppRegion: 'no-drag' }}>
        {agents.map(a => (
          <div key={a.name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <div style={{
              width: 6, height: 6, borderRadius: '50%',
              background: 'var(--seal-success)',
              opacity: 0.8,
            }} />
            <span style={{ fontSize: 10, color: a.color }}>{a.name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
