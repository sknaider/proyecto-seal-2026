/**
 * Código SEAL — Chat Module
 * WebSocket chat with team, bottom panel + sidebar, reconnect with backoff.
 */

import { state, CHAT_WS } from './state.js';
import { escHtml, showToast } from './utils.js';

let chatReconnectDelay = 1000;
const CHAT_MAX_RECONNECT = 60000;

export function initChat() {
  document.getElementById('btn-send').addEventListener('click', () => sendChatFrom('chat-input'));
  document.getElementById('chat-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatFrom('chat-input'); }
  });
  document.getElementById('btn-send-main').addEventListener('click', () => sendChatFrom('chat-input-main'));
  document.getElementById('chat-input-main').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatFrom('chat-input-main'); }
  });
  connectChat();
}

export function connectChat() {
  try {
    state.chatWs = new WebSocket(CHAT_WS);
    state.chatWs.onopen = () => {
      state.chatConnected = true;
      chatReconnectDelay = 1000;
      updateChatDots('connected');
      showToast('Chat del equipo conectado', 'success', 2000);
    };
    state.chatWs.onclose = () => {
      state.chatConnected = false;
      updateChatDots('disconnected');
      setTimeout(connectChat, chatReconnectDelay);
      chatReconnectDelay = Math.min(chatReconnectDelay * 2, CHAT_MAX_RECONNECT);
    };
    state.chatWs.onerror = () => state.chatWs.close();
    state.chatWs.onmessage = e => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.from === 'William' && msg._echo) return;
        addChatMessage(msg);
      } catch (err) {
        console.warn('[Chat] Parse error:', err.message);
      }
    };
  } catch (err) {
    console.warn('[Chat] Connection failed:', err.message);
    updateChatDots('disconnected');
    setTimeout(connectChat, chatReconnectDelay);
    chatReconnectDelay = Math.min(chatReconnectDelay * 2, CHAT_MAX_RECONNECT);
  }
}

function updateChatDots(cls) {
  ['chat-dot', 'chat-dot-bottom'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.className = `chat-dot ${cls}`;
  });
}

export function addChatMessage(msg) {
  const from = (msg.from || 'SYS').toLowerCase();
  const text = escHtml((msg.message || '').slice(0, 2000));
  const time = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }) : '';

  const sidebar = document.getElementById('chat-messages');
  if (sidebar) {
    const d = document.createElement('div');
    d.className = 'chat-msg';
    d.innerHTML = `<span class="chat-from ${from}">${escHtml(msg.from || 'SYS')}</span> <span class="chat-text">${text.slice(0, 500)}</span>`;
    sidebar.appendChild(d);
    sidebar.scrollTop = sidebar.scrollHeight;
  }

  const main = document.getElementById('chat-messages-main');
  if (main) {
    const d = document.createElement('div');
    d.className = `chat-msg-main ${from}`;
    d.innerHTML = `<div class="chat-msg-header"><span class="chat-msg-from ${from}">${escHtml(msg.from || 'SYS')}</span><span class="chat-msg-time">${time}</span></div><div class="chat-msg-text">${text}</div>`;
    main.appendChild(d);
    main.scrollTop = main.scrollHeight;
    while (main.children.length > 500) main.removeChild(main.firstChild);
  }
}

export function sendChatFrom(inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  if (state.chatWs && state.chatWs.readyState === WebSocket.OPEN) {
    state.chatWs.send(JSON.stringify({ action: 'say', message: text }));
  }
  addChatMessage({ from: 'William', message: text, timestamp: new Date().toISOString() });
  input.value = '';
  input.focus();
}
