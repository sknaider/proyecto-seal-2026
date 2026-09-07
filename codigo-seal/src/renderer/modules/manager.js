/**
 * Código SEAL — Manager View Module
 * Agent status, health monitoring, spawn agents, GPU.
 */

import { state } from './state.js';
import { escHtml, sealRequest, showToast } from './utils.js';

export function initManagerView() {
  document.getElementById('btn-spawn-agent')?.addEventListener('click', spawnAgent);
  updateManagerHealth();
  state.managerInterval = setInterval(updateManagerHealth, 15000);
}

export async function updateManagerHealth() {
  // GPU via system IPC
  try {
    if (window.seal && window.seal.system) {
      const gpu = await window.seal.system.gpu();
      const gpuEl = document.getElementById('health-gpu');
      const statusGpu = document.getElementById('status-gpu');
      if (gpu && !gpu.error) {
        if (gpuEl) gpuEl.textContent = `${gpu.temp}°C / ${gpu.util}%`;
        if (statusGpu) statusGpu.textContent = `GPU: ${gpu.temp}°C`;
        if (gpu.temp > 80) gpuEl?.classList.add('health-err');
        else gpuEl?.classList.remove('health-err');
      }
    }
  } catch (e) {
    console.warn('[Manager] GPU failed:', e.message);
  }

  // SEAL Runtime bridge
  try {
    const health = await sealRequest('/api/health');
    const rtEl = document.getElementById('health-runtime');
    if (rtEl) {
      if (health && !health.error) { rtEl.textContent = '●'; rtEl.className = 'health-ok'; }
      else { rtEl.textContent = '●'; rtEl.className = 'health-err'; }
    }
  } catch {
    const rtEl = document.getElementById('health-runtime');
    if (rtEl) { rtEl.textContent = '○'; rtEl.className = 'health-err'; }
  }

  // Chat server
  try {
    const chatEl = document.getElementById('health-chat');
    if (chatEl) {
      chatEl.textContent = state.chatConnected ? '●' : '○';
      chatEl.className = state.chatConnected ? 'health-ok' : 'health-err';
    }
  } catch {}

  // DB (PostgreSQL via bridge)
  try {
    const db = await sealRequest('/api/soul/snapshot?agent=ADA');
    const dbEl = document.getElementById('health-db');
    if (dbEl) {
      if (db && !db.error) { dbEl.textContent = '●'; dbEl.className = 'health-ok'; }
      else { dbEl.textContent = '○'; dbEl.className = 'health-err'; }
    }
  } catch {
    const dbEl = document.getElementById('health-db');
    if (dbEl) { dbEl.textContent = '○'; dbEl.className = 'health-err'; }
  }

  updateAgentIndicators();
}

function updateAgentIndicators() {
  document.querySelector('.agent-dot.ada')?.classList.add('active');
  if (state.chatConnected) {
    document.querySelector('.agent-dot.jarvis')?.classList.add('active');
    document.querySelector('.agent-dot.dum')?.classList.add('active');
  }
}

async function spawnAgent() {
  const prompt = document.getElementById('spawn-prompt')?.value;
  const type = document.getElementById('spawn-type')?.value;
  if (!prompt) return;
  try {
    const result = await sealRequest('/api/agents/spawn', 'POST', {
      name: `agent_${Date.now()}`, prompt, agent_type: type, description: prompt.slice(0, 50),
    });
    if (result) {
      document.getElementById('spawn-prompt').value = '';
      addManagerTask(prompt, 'in-progress');
    }
  } catch (e) {
    console.warn('[Manager] Spawn failed:', e.message);
    showToast('Error al spawn agente', 'error');
  }
}

export function addManagerTask(name, status) {
  const list = document.getElementById('manager-tasks');
  if (!list) return;
  const d = document.createElement('div');
  d.className = `manager-task ${status}`;
  const icons = { 'completed': '✓', 'in-progress': '⟳', 'pending': '○' };
  d.innerHTML = `<span class="task-status">${icons[status] || '○'}</span><span class="task-name">${escHtml(name)}</span>`;
  list.appendChild(d);
}

export function startHealthMonitor() {
  const updateChatIndicator = () => {
    const el = document.getElementById('status-chat-indicator');
    if (el) el.textContent = state.chatConnected ? '💬 Online' : '💬 Offline';
  };
  state.intervals.push(setInterval(updateChatIndicator, 5000));
  updateChatIndicator();
}
