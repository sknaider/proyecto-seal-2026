import React, { useState, useEffect } from 'react';

function TreeNode({ node, depth = 0, onOpenFile }) {
  const [expanded, setExpanded] = useState(depth < 1);
  const isDir = node.type === 'directory' || node.children;
  const indent = depth * 14 + 8;

  const ext = (node.name || '').split('.').pop();
  const color = { py: '#f59e0b', ts: '#3b82f6', tsx: '#3b82f6', js: '#f59e0b', jsx: '#f59e0b', json: '#22c55e', md: '#94a3b8', sh: '#ef4444' }[ext] || 'var(--seal-text)';

  return (
    <>
      <div
        onClick={() => isDir ? setExpanded(!expanded) : onOpenFile(node.path)}
        style={{
          paddingLeft: indent, paddingRight: 8, height: 24, display: 'flex', alignItems: 'center',
          fontSize: 12, cursor: 'pointer', color: isDir ? 'var(--seal-text)' : color,
          background: 'transparent',
        }}
        onMouseEnter={e => e.currentTarget.style.background = 'var(--seal-bg)'}
        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
      >
        <span style={{ width: 16, fontSize: 10, color: 'var(--seal-text-dim)' }}>
          {isDir ? (expanded ? 'v' : '>') : ' '}
        </span>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.name}
        </span>
      </div>
      {isDir && expanded && node.children?.map(child => (
        <TreeNode key={child.path} node={child} depth={depth + 1} onOpenFile={onOpenFile} />
      ))}
    </>
  );
}

export default function Sidebar({ onOpenFile }) {
  const [tree, setTree] = useState(null);

  useEffect(() => {
    const load = async () => {
      if (!window.seal) return;
      try {
        const t = await window.seal.fs.readDir('/home/dadito/IA/proyecto-seal');
        setTree(t);
      } catch {}
    };
    load();
  }, []);

  return (
    <div style={{
      width: 220, background: 'var(--seal-surface)', borderRight: '1px solid var(--seal-border)',
      overflow: 'auto', flexShrink: 0,
    }}>
      <div style={{ padding: '8px 12px', fontSize: 10, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--seal-text-dim)' }}>
        Archivos
      </div>
      {tree ? (
        tree.children?.map(node => <TreeNode key={node.path} node={node} onOpenFile={onOpenFile} />)
      ) : (
        <div style={{ padding: '8px 12px', fontSize: 11, color: 'var(--seal-text-dim)' }}>Cargando...</div>
      )}
    </div>
  );
}
