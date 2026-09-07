/**
 * Código SEAL — Resize Handles Module
 * Draggable resize for sidebar and bottom panel.
 */

import { state } from './state.js';

export function initResizeHandles() {
  const sidebarHandle = document.getElementById('resize-sidebar');
  const sidebar = document.getElementById('sidebar');
  if (sidebarHandle && sidebar) {
    let startX, startWidth;
    sidebarHandle.addEventListener('mousedown', (e) => {
      startX = e.clientX;
      startWidth = sidebar.offsetWidth;
      sidebarHandle.classList.add('active');
      const onMove = (ev) => {
        const newWidth = Math.max(160, Math.min(500, startWidth + ev.clientX - startX));
        sidebar.style.width = `${newWidth}px`;
      };
      const onUp = () => {
        sidebarHandle.classList.remove('active');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (state.editor) state.editor.layout();
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  const bottomHandle = document.getElementById('resize-bottom');
  const chatWrapper = document.getElementById('chat-wrapper');
  if (bottomHandle && chatWrapper) {
    let startY, startHeight;
    bottomHandle.addEventListener('mousedown', (e) => {
      if (chatWrapper.classList.contains('collapsed')) return;
      startY = e.clientY;
      startHeight = chatWrapper.offsetHeight;
      bottomHandle.classList.add('active');
      const onMove = (ev) => {
        const newHeight = Math.max(100, Math.min(600, startHeight - (ev.clientY - startY)));
        chatWrapper.style.height = `${newHeight}px`;
      };
      const onUp = () => {
        bottomHandle.classList.remove('active');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (state.editor) state.editor.layout();
        if (state.terminalFit) setTimeout(() => state.terminalFit.fit(), 50);
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }
}
