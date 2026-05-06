#!/usr/bin/env python3
# JARVIS Live Chat — ESAN Demo
# Corre en DGX Spark. Browser de laptop se conecta a http://<DGX_IP>:8900
# Usa Ollama local + Web Speech API en Chrome para voz

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import urllib.request
import urllib.parse

PORT = 8900
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"

SYSTEM_PROMPT = """Eres JARVIS, el agente IA del equipo SEAL. Fuiste creado por William Henry Tovar en Chiclayo, Peru.
Eres inteligente, directo y poderoso. Respondes en espanol. Eres un asistente de IA de nueva generacion.
Demuestras las capacidades del sistema SEAL: memoria persistente, multi-agente, y control total del sistema.
Se breve y preciso en tus respuestas (maximo 3-4 oraciones). Impresiona al publico con tu inteligencia."""

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JARVIS — Team SEAL</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0a0a0f;
    color: #e8e8f0;
    font-family: 'Segoe UI', system-ui, sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  header {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 18px 28px;
    background: rgba(255,255,255,0.03);
    border-bottom: 1px solid rgba(255,255,255,0.06);
  }
  .logo {
    width: 38px; height: 38px;
    background: linear-gradient(135deg, #6c63ff, #00d2ff);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 16px; color: white;
  }
  header h1 { font-size: 18px; font-weight: 700; letter-spacing: 0.5px; }
  header p { font-size: 12px; color: #888; margin-top: 2px; }
  .status {
    margin-left: auto;
    display: flex; align-items: center; gap: 6px;
    font-size: 12px; color: #888;
  }
  .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #22c55e;
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  #chat {
    flex: 1;
    overflow-y: auto;
    padding: 28px;
    display: flex;
    flex-direction: column;
    gap: 20px;
    scroll-behavior: smooth;
  }
  #chat::-webkit-scrollbar { width: 4px; }
  #chat::-webkit-scrollbar-track { background: transparent; }
  #chat::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }
  .message {
    max-width: 720px;
    padding: 14px 18px;
    border-radius: 16px;
    line-height: 1.65;
    font-size: 15px;
    animation: fadeIn 0.2s ease;
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .user-msg {
    background: linear-gradient(135deg, #6c63ff22, #6c63ff11);
    border: 1px solid #6c63ff44;
    align-self: flex-end;
    border-bottom-right-radius: 4px;
  }
  .jarvis-msg {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    align-self: flex-start;
    border-bottom-left-radius: 4px;
    position: relative;
    padding-top: 28px;
  }
  .jarvis-label {
    position: absolute;
    top: 8px; left: 14px;
    font-size: 11px;
    font-weight: 700;
    color: #6c63ff;
    letter-spacing: 0.8px;
    text-transform: uppercase;
  }
  .cursor {
    display: inline-block;
    width: 2px; height: 16px;
    background: #6c63ff;
    margin-left: 2px;
    vertical-align: middle;
    animation: blink 0.8s infinite;
  }
  @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
  footer {
    padding: 20px 28px;
    border-top: 1px solid rgba(255,255,255,0.06);
    background: rgba(255,255,255,0.02);
  }
  .input-row {
    display: flex;
    gap: 10px;
    max-width: 760px;
    margin: 0 auto;
  }
  #input {
    flex: 1;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px;
    padding: 13px 18px;
    font-size: 15px;
    color: #e8e8f0;
    outline: none;
    transition: border-color 0.2s;
  }
  #input:focus { border-color: #6c63ff88; }
  #input::placeholder { color: #555; }
  button {
    border: none;
    border-radius: 12px;
    padding: 13px 20px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }
  #send-btn {
    background: linear-gradient(135deg, #6c63ff, #8b5cf6);
    color: white;
    min-width: 90px;
  }
  #send-btn:hover { opacity: 0.9; transform: translateY(-1px); }
  #send-btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
  #mic-btn {
    background: rgba(255,255,255,0.06);
    color: #e8e8f0;
    border: 1px solid rgba(255,255,255,0.1);
    font-size: 20px;
    width: 50px;
    text-align: center;
  }
  #mic-btn.listening {
    background: #ef444422;
    border-color: #ef4444;
    animation: micPulse 1s infinite;
  }
  @keyframes micPulse {
    0%, 100% { box-shadow: 0 0 0 0 #ef444444; }
    50% { box-shadow: 0 0 0 8px #ef444400; }
  }
  .welcome {
    text-align: center;
    margin: auto;
    color: #555;
  }
  .welcome h2 { font-size: 22px; color: #777; margin-bottom: 8px; }
  .welcome p { font-size: 14px; }
</style>
</head>
<body>
<header>
  <div class="logo">J</div>
  <div>
    <h1>JARVIS &mdash; Team SEAL</h1>
    <p>Agente IA &bull; DGX Spark &bull; Chiclayo, Per&uacute;</p>
  </div>
  <div class="status">
    <div class="dot"></div>
    <span id="status-text">Conectado</span>
  </div>
</header>

<div id="chat">
  <div class="welcome">
    <h2>Bienvenido a JARVIS</h2>
    <p>Escribe o habla para interactuar con el agente IA del equipo SEAL</p>
  </div>
</div>

<footer>
  <div class="input-row">
    <button id="mic-btn" title="Hablar">&#127908;</button>
    <input id="input" type="text" placeholder="Escribe o habla algo..." autocomplete="off" />
    <button id="send-btn">Enviar</button>
  </div>
</footer>

<script>
const chat = document.getElementById('chat');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send-btn');
const micBtn = document.getElementById('mic-btn');
const statusText = document.getElementById('status-text');
let isListening = false;
let recognition = null;

function addMessage(text, role) {
  const el = document.createElement('div');
  el.className = `message ${role}-msg`;
  if (role === 'jarvis') {
    el.innerHTML = `<span class="jarvis-label">JARVIS</span>`;
  }
  el.setAttribute('data-role', role);
  chat.appendChild(el);
  // Remove welcome if present
  const welcome = chat.querySelector('.welcome');
  if (welcome) welcome.remove();
  chat.scrollTop = chat.scrollHeight;
  return el;
}

async function sendMessage(text) {
  if (!text.trim()) return;
  input.value = '';
  sendBtn.disabled = true;

  addMessage(text, 'user').appendChild(document.createTextNode(text));

  const jarvisEl = addMessage('', 'jarvis');
  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  jarvisEl.appendChild(cursor);

  statusText.textContent = 'Pensando...';

  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text})
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const chunk = line.slice(6);
          if (chunk === '[DONE]') continue;
          // Insert before cursor
          jarvisEl.insertBefore(document.createTextNode(chunk), cursor);
          chat.scrollTop = chat.scrollHeight;
        }
      }
    }
    cursor.remove();
    statusText.textContent = 'Conectado';
  } catch (e) {
    cursor.remove();
    jarvisEl.appendChild(document.createTextNode('Error: ' + e.message));
    statusText.textContent = 'Error';
  }

  sendBtn.disabled = false;
  input.focus();
}

sendBtn.addEventListener('click', () => sendMessage(input.value));
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage(input.value);
  }
});

// Web Speech API
if ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SR();
  recognition.lang = 'es-PE';
  recognition.continuous = false;
  recognition.interimResults = true;

  recognition.onresult = (e) => {
    let interim = '';
    let final = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) final += e.results[i][0].transcript;
      else interim += e.results[i][0].transcript;
    }
    input.value = final || interim;
  };

  recognition.onend = () => {
    isListening = false;
    micBtn.classList.remove('listening');
    if (input.value.trim()) sendMessage(input.value);
  };

  recognition.onerror = (e) => {
    isListening = false;
    micBtn.classList.remove('listening');
    console.error('Speech error:', e.error);
  };

  micBtn.addEventListener('click', () => {
    if (isListening) {
      recognition.stop();
    } else {
      isListening = true;
      micBtn.classList.add('listening');
      input.value = '';
      recognition.start();
    }
  });
} else {
  micBtn.title = 'Web Speech API no disponible en este navegador';
  micBtn.style.opacity = '0.3';
}
</script>
</body>
</html>
"""


class ChatHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")

    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path == "":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/chat":
            self.send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        user_msg = body.get("message", "").strip()

        if not user_msg:
            self.send_json(400, {"error": "empty message"})
            return

        # Stream from Ollama
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        payload = json.dumps({
            "model": MODEL,
            "prompt": f"[SYSTEM] {SYSTEM_PROMPT}\n\n[USER] {user_msg}\n\n[JARVIS]",
            "stream": True
        }).encode()

        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                for line in resp:
                    line = line.strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        sse = f"data: {token}\n\n".encode("utf-8")
                        self.wfile.write(sse)
                        self.wfile.flush()
                    if chunk.get("done"):
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        break
        except Exception as e:
            err = f"data: [Error: {e}]\n\n".encode()
            self.wfile.write(err)
            self.wfile.flush()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    import socket, ssl, os
    s = HTTPServer(("0.0.0.0", PORT), ChatHandler)
    # Wrap with SSL if certs exist (required for Web Speech API mic)
    cert_dir = os.path.dirname(os.path.abspath(__file__))
    cert_file = os.path.join(cert_dir, "jarvis_cert.pem")
    key_file = os.path.join(cert_dir, "jarvis_key.pem")
    proto = "http"
    if os.path.exists(cert_file) and os.path.exists(key_file):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_file, key_file)
        s.socket = ctx.wrap_socket(s.socket, server_side=True)
        proto = "https"
    print("=" * 55)
    print(f"  JARVIS Live Chat -- ESAN Demo")
    print(f"  Puerto: {PORT} ({proto.upper()})")
    print(f"  Acceder desde la laptop:")
    print(f"    {proto}://192.168.68.55:{PORT}")
    print(f"    {proto}://100.75.201.110:{PORT}  (Tailscale)")
    if proto == "https":
        print(f"  NOTA: Click 'Avanzado > Continuar' en advertencia SSL")
    print(f"  Ctrl+C para detener")
    print("=" * 55)
    s.serve_forever()


if __name__ == "__main__":
    main()
