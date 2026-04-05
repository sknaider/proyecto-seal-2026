/**
 * Código SEAL — Virtual Scroll for Chat Messages
 * Renders only visible messages for performance with large histories.
 * Based on: Claude Code VirtualMessageList (SPEC_15). Clean-room: ~100 lines.
 */

const ITEM_HEIGHT_ESTIMATE = 60; // px average per message
const OVERSCAN = 10; // Extra items rendered above/below viewport

export class VirtualScroll {
  constructor(container, renderItem) {
    this.container = container;
    this.renderItem = renderItem;
    this.items = [];
    this.scrollTop = 0;
    this.viewportHeight = 0;
    this.totalHeight = 0;

    // Create scroll container
    this.scrollEl = document.createElement('div');
    this.scrollEl.className = 'virtual-scroll';
    this.scrollEl.style.cssText = 'overflow-y:auto;height:100%;position:relative;';

    this.spacer = document.createElement('div');
    this.spacer.style.cssText = 'position:relative;';

    this.viewport = document.createElement('div');
    this.viewport.style.cssText = 'position:absolute;left:0;right:0;';

    this.scrollEl.appendChild(this.spacer);
    this.spacer.appendChild(this.viewport);
    container.appendChild(this.scrollEl);

    this.scrollEl.addEventListener('scroll', () => {
      this.scrollTop = this.scrollEl.scrollTop;
      this._render();
    });

    this._resizeObserver = new ResizeObserver(() => {
      this.viewportHeight = this.scrollEl.clientHeight;
      this._render();
    });
    this._resizeObserver.observe(this.scrollEl);
  }

  /**
   * Set items and re-render.
   */
  setItems(items) {
    this.items = items;
    this.totalHeight = items.length * ITEM_HEIGHT_ESTIMATE;
    this.spacer.style.height = `${this.totalHeight}px`;
    this._render();
  }

  /**
   * Append item and scroll to bottom.
   */
  appendItem(item) {
    this.items.push(item);
    this.totalHeight = this.items.length * ITEM_HEIGHT_ESTIMATE;
    this.spacer.style.height = `${this.totalHeight}px`;
    this._render();
    // Auto-scroll to bottom
    this.scrollEl.scrollTop = this.totalHeight;
  }

  /**
   * Render visible items only.
   */
  _render() {
    const startIdx = Math.max(0, Math.floor(this.scrollTop / ITEM_HEIGHT_ESTIMATE) - OVERSCAN);
    const endIdx = Math.min(
      this.items.length,
      Math.ceil((this.scrollTop + this.viewportHeight) / ITEM_HEIGHT_ESTIMATE) + OVERSCAN
    );

    this.viewport.style.top = `${startIdx * ITEM_HEIGHT_ESTIMATE}px`;
    this.viewport.innerHTML = '';

    for (let i = startIdx; i < endIdx; i++) {
      const el = this.renderItem(this.items[i], i);
      if (el) this.viewport.appendChild(el);
    }
  }

  /**
   * Scroll to bottom.
   */
  scrollToBottom() {
    this.scrollEl.scrollTop = this.totalHeight;
  }

  /**
   * Cleanup.
   */
  destroy() {
    this._resizeObserver.disconnect();
    this.container.innerHTML = '';
  }
}
