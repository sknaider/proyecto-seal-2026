type MessageHandler = (data: any) => void;

class SealWebSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<MessageHandler>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private _connected = false;

  get connected() { return this._connected; }

  connect(token: string) {
    if (this.ws?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws?token=${token}`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this._connected = true;
      this.notify({ type: 'connection', status: 'connected' });
    };

    this.ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        this.notify(data);
      } catch { /* ignore non-JSON */ }
    };

    this.ws.onclose = () => {
      this._connected = false;
      this.notify({ type: 'connection', status: 'disconnected' });
      this.scheduleReconnect(token);
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
    this._connected = false;
  }

  send(data: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  subscribe(handler: MessageHandler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  private notify(data: any) {
    this.handlers.forEach(h => h(data));
  }

  private scheduleReconnect(token: string) {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(token), 3000);
  }
}

export const sealWS = new SealWebSocket();
