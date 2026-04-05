/**
 * Código SEAL — Multi-Agent Live View (Phase 4.2)
 * Real-time visualization of all agents: ADA, JARVIS, DUM.
 * Shows OCEAN, emotional state, current task, messages, drift.
 */

import { state } from './state.js';
import { escHtml, sealRequest } from './utils.js';

let agentViewInterval = null;

export function initMultiAgentView() {
  // Update agent cards every 15s
  agentViewInterval = setInterval(updateAgentCards, 15000);
  state.intervals.push(agentViewInterval);
  updateAgentCards();
}

async function updateAgentCards() {
  const container = document.getElementById('manager-agents');
  if (!container) return;

  const agents = ['ADA', 'JARVIS', 'DUM'];
  const cards = [];

  for (const agent of agents) {
    try {
      const snapshot = await sealRequest(`/api/soul/snapshot?agent=${agent}`);
      if (snapshot && !snapshot.error) {
        cards.push(buildAgentCard(agent, snapshot));
      } else {
        cards.push(buildOfflineCard(agent));
      }
    } catch {
      cards.push(buildOfflineCard(agent));
    }
  }

  container.innerHTML = cards.join('');
}

function buildAgentCard(agent, snapshot) {
  const ocean = snapshot.ocean || {};
  const thought = snapshot.recent_thoughts?.[0];
  const drift = typeof snapshot.drift === 'number' ? snapshot.drift.toFixed(3) : '--';
  const emotionalState = thought?.state || 'unknown';
  const agentClass = agent.toLowerCase();

  const oceanBars = ['O','C','E','A','N'].map(d => {
    const v = ocean[d] || 0;
    return `<div class="mini-ocean-bar"><span>${d}</span><div class="mini-bar"><div class="mini-fill" style="width:${v*100}%"></div></div><span>${v.toFixed(1)}</span></div>`;
  }).join('');

  return `
    <div class="manager-agent live">
      <div class="manager-agent-header">
        <span class="agent-badge ${agentClass}">${agent}</span>
        <span class="agent-role">${agent === 'ADA' ? 'Engineer' : agent === 'JARVIS' ? 'Architect' : 'Guardian'}</span>
        <span class="agent-status-dot running"></span>
      </div>
      <div class="agent-emotion">${escHtml(emotionalState)}</div>
      <div class="agent-ocean-live">${oceanBars}</div>
      <div class="agent-drift">Drift: ${drift}</div>
      ${thought ? `<div class="agent-thought">"${escHtml(thought.thought?.slice(0, 80) || '')}..."</div>` : ''}
    </div>
  `;
}

function buildOfflineCard(agent) {
  return `
    <div class="manager-agent offline">
      <div class="manager-agent-header">
        <span class="agent-badge ${agent.toLowerCase()}">${agent}</span>
        <span class="agent-role">Offline</span>
        <span class="agent-status-dot idle"></span>
      </div>
      <div class="agent-emotion">Sin conexión a SOUL</div>
    </div>
  `;
}
