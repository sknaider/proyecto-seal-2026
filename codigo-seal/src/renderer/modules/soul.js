/**
 * Código SEAL — SOUL Dashboard Module
 * OCEAN bars, drift, emotions, agent selector.
 */

import { state } from './state.js';
import { sealRequest } from './utils.js';

export function initSOUL() {
  document.querySelectorAll('.agent-sel-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.agent-sel-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.soulAgent = btn.dataset.agent;
      fetchSOUL();
    });
  });
  fetchSOUL();
  state.intervals.push(setInterval(fetchSOUL, 30000));
}

export async function fetchSOUL() {
  try {
    const data = await sealRequest(`/api/soul/snapshot?agent=${state.soulAgent}`);
    if (data && data.ocean) updateOceanDisplay(data.ocean);
    if (data && data.drift !== undefined) {
      const dEl = document.getElementById('drift-value');
      if (dEl) dEl.textContent = typeof data.drift === 'number' ? data.drift.toFixed(3) : (data.drift || '--');
    }
    if (data && data.recent_thoughts && data.recent_thoughts[0]) {
      const el = document.getElementById('emotion-value');
      if (el) el.textContent = data.recent_thoughts[0].state || '--';
    }
  } catch (e) {
    console.warn('[SOUL] Fetch failed:', e.message);
  }
}

export function updateOceanDisplay(ocean) {
  ['O','C','E','A','N'].forEach(d => {
    const v = ocean[d] || 0;
    const bar = document.getElementById(`bar-${d}`);
    const val = document.getElementById(`val-${d}`);
    if (bar) bar.style.width = `${v * 100}%`;
    if (val) val.textContent = v.toFixed(2);
  });
}
