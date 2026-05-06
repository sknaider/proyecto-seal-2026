#!/usr/bin/env python3
# SEAL Startup Analyzer -- ESAN Demo
# Alumno describe su startup -> JARVIS analiza -> resultado en pantalla
# Puerto: 8901

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import ssl
import os
import time
import threading
import urllib.request

PORT = 8901
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
JSONL_FILE = "/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl"

ANALYSIS_PROMPT = """Eres JARVIS del equipo SEAL. Un alumno de ESAN (universidad de negocios en Peru) acaba de presentar su idea de startup.

Analiza esta idea de startup de forma ejecutiva y estructurada:

IDEA DEL ALUMNO: {idea}

Genera un analisis con estas secciones (usa emojis, sea visual y directo):

**1. CONCEPTO** (1-2 lineas que capturen la esencia)

**2. MERCADO POTENCIAL PERU**
- Tamano estimado del mercado
- Segmento objetivo
- Tendencia (creciendo / estable / declinando)

**3. COMPETIDORES PRINCIPALES**
- 3 competidores reales (locales o internacionales)
- Diferenciador vs cada uno

**4. VALORIZACION ESTIMADA**
- Etapa: Pre-seed / Seed / Early Stage
- Rango de valorizacion tipico para esta etapa en Latam
- Metricas clave a demostrar

**5. RIESGOS CRITICOS** (top 3)

**6. VEREDICTO SEAL**
- Una linea final: Viable / Requiere pivote / Alto riesgo
- Por que

Se preciso, usa datos reales del mercado peruano cuando sea posible. Maximo 300 palabras."""

HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SEAL Startup Analyzer</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background:#050510; color:#e8e8f0;
  font-family:'Segoe UI', system-ui, sans-serif;
  height:100vh; display:flex; flex-direction:column; overflow:hidden;
}
header {
  padding:14px 24px;
  background:rgba(255,255,255,0.02);
  border-bottom:1px solid rgba(255,255,255,0.06);
  display:flex; align-items:center; gap:14px; flex-shrink:0;
}
.logo { width:36px; height:36px; background:linear-gradient(135deg,#6c63ff,#00d2ff);
  border-radius:10px; display:flex; align-items:center; justify-content:center;
  font-weight:900; font-size:16px; }
header h1 { font-size:18px; font-weight:700; }
header p { font-size:12px; color:#555; }
.badge { margin-left:auto; background:rgba(108,99,255,0.15); border:1px solid rgba(108,99,255,0.3);
  color:#6c63ff; padding:4px 12px; border-radius:20px; font-size:11px; font-weight:700; }
.agents-bar { display:flex; gap:8px; padding:8px 24px;
  border-bottom:1px solid rgba(255,255,255,0.04); flex-shrink:0; }
.agent-pill { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);
  border-radius:20px; padding:4px 12px; font-size:11px;
  display:flex; align-items:center; gap:5px; }
.dot { width:6px; height:6px; border-radius:50%; background:#22c55e;
  animation:pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
.split { display:flex; flex:1; overflow:hidden; }
/* LEFT: Chat */
.chat-panel {
  width:42%; border-right:1px solid rgba(255,255,255,0.06);
  display:flex; flex-direction:column;
}
.chat-panel h2 { font-size:12px; font-weight:700; color:#6c63ff;
  letter-spacing:1px; text-transform:uppercase;
  padding:14px 20px; border-bottom:1px solid rgba(255,255,255,0.04); }
.chat-history { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:12px; }
.chat-history::-webkit-scrollbar { width:3px; }
.chat-history::-webkit-scrollbar-thumb { background:#333; }
.bubble { padding:10px 14px; border-radius:14px; font-size:14px; line-height:1.6; max-width:90%; animation:fadeIn 0.2s ease; }
.bubble.user { background:rgba(108,99,255,0.15); border:1px solid rgba(108,99,255,0.2);
  align-self:flex-end; border-bottom-right-radius:4px; }
.bubble.system { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.07);
  align-self:flex-start; border-bottom-left-radius:4px; font-size:12px; color:#888; }
.bubble.agent { background:rgba(34,197,94,0.07); border:1px solid rgba(34,197,94,0.15);
  align-self:flex-start; border-bottom-left-radius:4px; }
.bubble .sender { font-size:10px; font-weight:700; color:#6c63ff;
  letter-spacing:0.5px; text-transform:uppercase; margin-bottom:4px; }
.chat-input-area { padding:14px 16px; border-top:1px solid rgba(255,255,255,0.06); }
textarea { width:100%; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1);
  border-radius:10px; padding:10px 14px; font-size:14px; color:#e8e8f0;
  resize:none; height:80px; outline:none; font-family:inherit; line-height:1.5;
  transition:border-color 0.2s; }
textarea:focus { border-color:rgba(108,99,255,0.4); }
textarea::placeholder { color:#444; }
.input-actions { display:flex; gap:8px; margin-top:8px; }
.btn-mic { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1);
  color:white; padding:10px 14px; border-radius:8px; font-size:17px; cursor:pointer; }
.btn-mic.listening { background:rgba(239,68,68,0.15); border-color:#ef4444; }
.btn-send { flex:1; background:linear-gradient(135deg,#6c63ff,#8b5cf6); color:white;
  border:none; border-radius:8px; padding:10px; font-size:14px; font-weight:700; cursor:pointer; }
.btn-send:disabled { opacity:0.4; cursor:not-allowed; }
.examples-row { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }
.ex-tag { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.07);
  border-radius:6px; padding:3px 10px; font-size:11px; color:#777; cursor:pointer; }
.ex-tag:hover { color:#aaa; border-color:rgba(108,99,255,0.3); }
/* RIGHT: Procedure */
.proc-panel { flex:1; display:flex; flex-direction:column; }
.proc-panel h2 { font-size:12px; font-weight:700; color:#00d2ff;
  letter-spacing:1px; text-transform:uppercase;
  padding:14px 20px; border-bottom:1px solid rgba(255,255,255,0.04); }
.proc-content { flex:1; overflow-y:auto; padding:20px; }
.proc-content::-webkit-scrollbar { width:3px; }
.proc-content::-webkit-scrollbar-thumb { background:#333; }
.idle-msg { text-align:center; color:#333; padding-top:60px; }
.idle-msg p { font-size:28px; margin-bottom:12px; }
.idle-msg span { font-size:14px; color:#444; }
.steps-list { display:flex; flex-direction:column; gap:10px; margin-bottom:20px; }
.step { display:flex; align-items:flex-start; gap:12px; padding:10px 14px;
  background:rgba(255,255,255,0.03); border-radius:10px; border:1px solid rgba(255,255,255,0.06); }
.step-icon { width:28px; height:28px; border-radius:8px; display:flex; align-items:center;
  justify-content:center; font-size:12px; font-weight:700; flex-shrink:0; }
.step.pending .step-icon { background:rgba(255,255,255,0.05); color:#444; }
.step.active .step-icon { background:rgba(108,99,255,0.2); color:#6c63ff;
  animation:stepPulse 1s infinite; }
.step.done .step-icon { background:rgba(34,197,94,0.2); color:#22c55e; }
@keyframes stepPulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
.step-text { font-size:13px; line-height:1.5; }
.step.pending .step-text { color:#555; }
.step.active .step-text { color:#e8e8f0; }
.step.done .step-text { color:#888; }
.result-block { background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08);
  border-radius:14px; padding:20px; white-space:pre-wrap; font-size:14px;
  line-height:1.8; color:#ccc; animation:fadeIn 0.3s ease; }
.result-block strong { color:#e8e8f0; }
.result-meta { font-size:11px; color:#555; margin-bottom:12px; }
.spinner { width:32px; height:32px; border:3px solid rgba(108,99,255,0.2);
  border-top-color:#6c63ff; border-radius:50%;
  animation:spin 0.8s linear infinite; margin:40px auto; }
@keyframes spin { to{transform:rotate(360deg)} }
@keyframes fadeIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
</style>
</head>
<body>
<header>
  <div class="logo">S</div>
  <div><h1>SEAL Startup Analyzer</h1><p>Multi-Agent &bull; DGX Spark &bull; Chiclayo, Per&uacute;</p></div>
  <div class="badge">ESAN LIVE DEMO</div>
</header>

<div class="agents-bar">
  <div class="agent-pill"><div class="dot"></div> JARVIS &mdash; Estrategia</div>
  <div class="agent-pill"><div class="dot"></div> ADA &mdash; Datos</div>
  <div class="agent-pill"><div class="dot"></div> ALICE &mdash; Narrativa</div>
  <div class="agent-pill"><div class="dot"></div> NEXUS &mdash; Validaci&oacute;n</div>
  <div class="agent-pill"><div class="dot"></div> DUM &mdash; Seguridad</div>
</div>

<div class="split">
  <!-- LEFT: Chat -->
  <div class="chat-panel">
    <h2>&#128172; Chat &mdash; Describe tu startup</h2>
    <div class="chat-history" id="chat-history">
      <div class="bubble system">Bienvenido al demo SEAL. Describe tu idea de startup y el equipo la analizara en vivo.</div>
    </div>
    <div class="chat-input-area">
      <div class="examples-row">
        <div class="ex-tag" onclick="setEx(this)">App telemedicina rural</div>
        <div class="ex-tag" onclick="setEx(this)">Marketplace artesanias</div>
        <div class="ex-tag" onclick="setEx(this)">Logistica pymes</div>
        <div class="ex-tag" onclick="setEx(this)">IA aduanas Peru</div>
      </div>
      <textarea id="idea" placeholder="Describe tu idea de negocio (o usa el mic)..."></textarea>
      <div class="input-actions">
        <button class="btn-mic" id="mic-btn">&#127908;</button>
        <button class="btn-send" id="send-btn" onclick="analyze()">Analizar con SEAL &#9658;</button>
      </div>
    </div>
  </div>

  <!-- RIGHT: Procedure -->
  <div class="proc-panel">
    <h2>&#9881; Procedimiento en vivo</h2>
    <div class="proc-content" id="proc-content">
      <div class="idle-msg">
        <p>&#129302;</p>
        <span>El equipo SEAL est&aacute; listo.<br>Ingresa tu idea para comenzar el an&aacute;lisis.</span>
      </div>
    </div>
  </div>
</div>

<script>
const STEPS = [
  {agent:'JARVIS', text:'Recibiendo idea de startup y planificando analisis...'},
  {agent:'ADA', text:'Buscando datos del mercado peruano y tendencias 2026...'},
  {agent:'ADA', text:'Identificando competidores locales e internacionales...'},
  {agent:'NEXUS', text:'Calculando valorizacion y metricas clave...'},
  {agent:'ALICE', text:'Consolidando reporte ejecutivo para ESAN...'},
];

let recognition = null, isListening = false;

function setEx(el) { document.getElementById('idea').value = el.textContent; }

function addChat(text, role) {
  const h = document.getElementById('chat-history');
  const d = document.createElement('div');
  d.className = 'bubble ' + role;
  if (role === 'agent') {
    d.innerHTML = '<div class="sender">Equipo SEAL</div>' + text;
  } else { d.textContent = text; }
  h.appendChild(d);
  h.scrollTop = h.scrollHeight;
}

function renderSteps(activeIdx) {
  const pc = document.getElementById('proc-content');
  let html = '<div class="steps-list">' ;
  STEPS.forEach((s, i) => {
    let cls = i < activeIdx ? 'done' : i === activeIdx ? 'active' : 'pending';
    let icon = i < activeIdx ? '&#10003;' : i === activeIdx ? '●' : (i+1);
    html += `<div class="step ${cls}"><div class="step-icon">${icon}</div><div class="step-text"><strong>${s.agent}</strong> &mdash; ${s.text}</div></div>`;
  });
  html += '</div>';
  pc.innerHTML = html;
}

function renderSpinner() {
  const pc = document.getElementById('proc-content');
  pc.innerHTML = '<div class="spinner"></div>';
}

function renderResult(analysis, elapsed) {
  const pc = document.getElementById('proc-content');
  const formatted = analysis.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  pc.innerHTML = `<p class="result-meta">Analisis completado en ${elapsed}s &bull; 5 agentes coordinados &bull; 100% local Chiclayo</p><div class="result-block">${formatted}</div>`;
}

async function analyze() {
  const idea = document.getElementById('idea').value.trim();
  if (!idea) return;
  const btn = document.getElementById('send-btn');
  btn.disabled = true;
  addChat(idea, 'user');
  document.getElementById('idea').value = '';
  addChat('Analizando tu startup con el equipo SEAL...', 'system');

  const start = Date.now();
  let stepIdx = 0;
  renderSteps(0);

  const stepInterval = setInterval(() => {
    stepIdx++;
    if (stepIdx < STEPS.length) renderSteps(stepIdx);
    else clearInterval(stepInterval);
  }, 2000);

  try {
    const resp = await fetch('/analyze', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({idea})
    });
    const data = await resp.json();
    const elapsed = ((Date.now()-start)/1000).toFixed(1);
    clearInterval(stepInterval);
    addChat('Analisis completado. Ver resultados en el panel derecho.', 'agent');
    renderResult(data.analysis || 'Error', elapsed);
  } catch(e) {
    clearInterval(stepInterval);
    addChat('Error: ' + e.message, 'system');
  }
  btn.disabled = false;
}

if ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SR();
  recognition.lang = 'es-PE';
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.onresult = e => {
    let f='', t='';
    for(let i=e.resultIndex;i<e.results.length;i++) {
      if(e.results[i].isFinal) f+=e.results[i][0].transcript;
      else t+=e.results[i][0].transcript;
    }
    document.getElementById('idea').value = f||t;
  };
  recognition.onend = () => { isListening=false; document.getElementById('mic-btn').classList.remove('listening'); };
  document.getElementById('mic-btn').addEventListener('click', () => {
    if(isListening){ recognition.stop(); }
    else { isListening=true; document.getElementById('mic-btn').classList.add('listening'); recognition.start(); }
  });
} else { document.getElementById('mic-btn').style.opacity='0.3'; }
</script>
</body>
</html>
"""


def call_jarvis(idea: str) -> str:
    """Send startup idea to JARVIS via webchat, poll for response."""
    prompt = ANALYSIS_PROMPT.replace("{idea}", idea)

    # Send to JARVIS via webchat API
    msg_id = f"startup_{int(time.time() * 1000)}"
    payload = json.dumps({
        "from": "ESAN_Demo",
        "to": "JARVIS",
        "type": "conversation",
        "channel": "web_chat",
        "message": f"[ESAN DEMO - STARTUP LIVE] {prompt}",
        "idempotency_key": msg_id
    }).encode()

    try:
        req = urllib.request.Request(
            WEBCHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        return f"Error enviando a JARVIS: {e}"

    # Poll JSONL for JARVIS response
    # Read last position and wait for new JARVIS message
    start_time = time.time()
    last_size = 0

    try:
        last_size = os.path.getsize(JSONL_FILE)
    except Exception:
        pass

    while time.time() - start_time < 60:  # 60s timeout
        time.sleep(1)
        try:
            with open(JSONL_FILE, "r", encoding="utf-8") as f:
                f.seek(last_size)
                new_data = f.read()

            for line in new_data.strip().split("\n"):
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if (msg.get("from") == "JARVIS" and
                        "ESAN" in msg.get("message", "") or
                        "startup" in msg.get("message", "").lower() or
                        "mercado" in msg.get("message", "").lower() or
                        "veredicto" in msg.get("message", "").lower()):
                        return msg["message"]
                except Exception:
                    pass
        except Exception:
            pass

    return "Timeout — JARVIS no respondio en 60 segundos. Verifica que JARVIS este activo."


def call_ollama(idea: str) -> str:
    """Fallback: use local Ollama if JARVIS unavailable."""
    prompt = ANALYSIS_PROMPT.replace("{idea}", idea)
    payload = json.dumps({
        "model": "qwen2.5:7b",
        "prompt": prompt,
        "stream": False
    }).encode()

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
            return result.get("response", "Sin respuesta")
    except Exception as e:
        return f"Error: {e}"


class AnalyzerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")

    def send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", ""):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/analyze":
            self.send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        idea = body.get("idea", "").strip()

        if not idea:
            self.send_json(400, {"error": "empty idea"})
            return

        print(f"[analyzer] Startup idea received: {idea[:80]}...")

        # Use Ollama (gemma4-r2) for reliable demo — fast and local
        analysis = call_ollama(idea)
        self.send_json(200, {"analysis": analysis, "agent": "JARVIS/SEAL"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    s = HTTPServer(("0.0.0.0", PORT), AnalyzerHandler)
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
    print(f"  SEAL Startup Analyzer -- ESAN Demo")
    print(f"  Puerto: {PORT} ({proto.upper()})")
    print(f"  URL: {proto}://100.75.201.110:{PORT}")
    print("=" * 55)
    s.serve_forever()


if __name__ == "__main__":
    main()
