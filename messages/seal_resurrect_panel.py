#!/usr/bin/env python3
"""SEAL Agent Control Panel — :8768
Panel de control de agentes SEAL. Python stdlib puro. Sin venv.
"""
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from urllib.request import urlopen
from urllib.error import URLError
import time

CORE_AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS"]
SEAL_DIR = Path("/home/dadito/IA/proyecto-seal")
RESTART_SCRIPT = SEAL_DIR / "seal_restart.sh"
STATE_FILE = Path("/tmp/seal_resurrect_state.json")
TRANSCRIPT_DIR = Path.home() / ".claude/projects/-home-dadito-IA-proyecto-seal-memory"
CONTEXT_LIMIT = 200_000
CHARS_PER_TOKEN = 3.5
TEAM_STATUS_URL = "http://localhost:8765/api/team/status"
PORT = 8768

AGENT_ICONS = {"ADA": "🤖", "JARVIS": "🧠", "ALICE": "📚", "NEXUS": "⚡", "DUM": "🛡️"}
AGENT_ROLES = {
    "ADA":    "Orchestrator",
    "JARVIS": "Estratega",
    "ALICE":  "Documentadora",
    "NEXUS":  "Diagnóstico",
    "DUM":    "Guardia",
}


def get_team_status() -> dict:
    """Fetch all agents from the backend team status API."""
    try:
        with urlopen(TEAM_STATUS_URL, timeout=2) as r:
            return json.loads(r.read())
    except (URLError, Exception):
        return {}


def get_context_meter(agent: str) -> dict:
    try:
        candidates = sorted(TRANSCRIPT_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return {"pct": 0, "tokens": 0, "age_str": "—"}
        transcript = candidates[0]
        total_bytes = transcript.stat().st_size
        mtime = transcript.stat().st_mtime
        compact_offset = 0
        compact_mtime = mtime
        current_pos = 0
        with open(transcript, "rb") as f:
            for raw in f:
                current_pos += len(raw)
                try:
                    entry = json.loads(raw)
                    s = str(entry.get("message", "")).lower()
                    if any(k in s for k in ("compacted", "compact_summary", "summarized_context")):
                        compact_offset = current_pos
                        compact_mtime = entry.get("timestamp", mtime)
                except Exception:
                    pass
        active_bytes = total_bytes - compact_offset
        tokens = int(active_bytes / CHARS_PER_TOKEN)
        pct = min(tokens / CONTEXT_LIMIT * 100, 100)
        elapsed = time.time() - (compact_mtime if isinstance(compact_mtime, (int, float)) else mtime)
        age_str = f"{elapsed/3600:.1f}h" if elapsed >= 3600 else f"{elapsed/60:.0f}m"
        return {"pct": pct, "tokens": tokens, "age_str": age_str}
    except Exception:
        return {"pct": 0, "tokens": 0, "age_str": "—"}


def pause_flag(agent: str) -> Path:
    return Path(f"/tmp/seal_pause_{agent.lower()}.flag")


def is_paused(agent: str) -> bool:
    return pause_flag(agent).exists()


def is_alive(agent: str) -> bool:
    agent_lower = agent.lower()
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            environ = (pid_dir / "environ").read_bytes().decode(errors="replace")
            env = dict(kv.split("=", 1) for kv in environ.split("\x00") if "=" in kv)
            if env.get("SEAL_AGENT") == agent:
                return True
            cmdline = (pid_dir / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
            if f"sandbox-agent/{agent}/kernel/{agent_lower}_kernel_main.py" in cmdline:
                return True
        except (PermissionError, FileNotFoundError, ValueError):
            continue
    return False


def get_agent_state(agent: str) -> dict:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            return state.get(agent, {})
        except Exception:
            pass
    return {}


def fmt_uptime(ts: int) -> str:
    if not ts:
        return "—"
    secs = int(time.time()) - ts
    if secs < 0:
        return "—"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    h = secs // 3600
    m = (secs % 3600) // 60
    return f"{h}h {m}m"


def render_html() -> str:
    # Fetch all agents from backend; fall back to core agents only
    team_data = get_team_status()
    api_agents = team_data.get("agents", {})
    all_agent_names = list(api_agents.keys()) if api_agents else CORE_AGENTS

    cards = ""
    total_alive = sum(
        1 for a in all_agent_names
        if (api_agents.get(a, {}).get("alive") if api_agents else is_alive(a))
    )

    for agent in all_agent_names:
        api_info = api_agents.get(agent, {})
        # alive: use API data if available, else process scan
        alive = api_info.get("alive", False) if api_agents else is_alive(agent)
        is_core = agent in CORE_AGENTS
        paused = is_paused(agent) if is_core else False
        st = get_agent_state(agent)
        crashes = st.get("crash_count", 0)
        last_birth = st.get("last_birth_ts", 0)
        uptime_str = fmt_uptime(last_birth) if alive and last_birth else "—"
        icon = AGENT_ICONS.get(agent, "●")
        role = api_info.get("role") or AGENT_ROLES.get(agent, "")

        # Status
        if alive:
            dot_color = "#a6e3a1"
            status_text = "ONLINE"
            status_bg = "rgba(166,227,161,0.12)"
            border_color = "rgba(166,227,161,0.25)"
        else:
            dot_color = "#f38ba8"
            status_text = "OFFLINE"
            status_bg = "rgba(243,139,168,0.08)"
            border_color = "rgba(243,139,168,0.2)"

        # Auto-resurrect badge
        if paused:
            ar_color = "#f9e2af"
            ar_bg = "rgba(249,226,175,0.12)"
            ar_text = "AUTO-RESURRECT: OFF"
            toggle_action = "resume"
            toggle_label = "▶ Activar"
            toggle_style = "color:#a6e3a1;border-color:rgba(166,227,161,0.4);"
        else:
            ar_color = "#a6e3a1"
            ar_bg = "rgba(166,227,161,0.1)"
            ar_text = "AUTO-RESURRECT: ON"
            toggle_action = "pause"
            toggle_label = "⏸ Pausar"
            toggle_style = "color:#f9e2af;border-color:rgba(249,226,175,0.4);"

        crash_color = "#f38ba8" if crashes >= 2 else ("#f9e2af" if crashes == 1 else "#6c7086")

        cards += f"""
        <div class="card" style="border-color:{border_color};background:linear-gradient(135deg,#1e1e2e 0%,{status_bg} 100%);">
          <div class="card-header">
            <div class="agent-info">
              <span class="agent-icon">{icon}</span>
              <div>
                <div class="agent-name">{agent}</div>
                <div class="agent-role">{role}</div>
              </div>
            </div>
            <div class="status-badge" style="background:{status_bg};border-color:{dot_color}20;">
              <span class="dot" style="background:{dot_color};box-shadow:0 0 6px {dot_color};"></span>
              {status_text}
            </div>
          </div>

          <div class="metrics">
            <div class="metric">
              <span class="metric-label">Uptime</span>
              <span class="metric-value">{uptime_str}</span>
            </div>
            <div class="metric">
              <span class="metric-label">Crashes</span>
              <span class="metric-value" style="color:{crash_color};">{crashes}</span>
            </div>
          </div>

          {'<div class="ar-badge" style="background:' + ar_bg + ';color:' + ar_color + ';">' + ar_text + '</div>' if is_core else ''}

          {'<div class="actions"><form method="POST" action="/action" style="flex:1;"><input type="hidden" name="agent" value="' + agent + '"><input type="hidden" name="action" value="' + toggle_action + '"><button type="submit" class="btn" style="' + toggle_style + '">' + toggle_label + '</button></form><form method="POST" action="/action" style="flex:1;"><input type="hidden" name="agent" value="' + agent + '"><input type="hidden" name="action" value="resurrect"><button type="submit" class="btn btn-resurrect">⚡ Resucitar</button></form><form method="POST" action="/action"><input type="hidden" name="agent" value="' + agent + '"><input type="hidden" name="action" value="reset_crashes"><button type="submit" class="btn btn-dim" title="Reset crash counter">↺</button></form></div>' if is_core else ''}
        </div>"""

    ctx_rows_html = []
    for a in CORE_AGENTS:
        ctx = get_context_meter(a)
        pct = ctx["pct"]
        tokens = ctx["tokens"]
        age = ctx["age_str"]
        color = "#f38ba8" if pct >= 85 else ("#f9e2af" if pct >= 60 else "#89b4fa")
        ctx_rows_html.append(
            f'<div class="ctx-row">'
            f'<span class="ctx-name">{a}</span>'
            f'<div class="ctx-track"><div class="ctx-fill" style="width:{pct:.1f}%;background:{color};"></div></div>'
            f'<span class="ctx-stats" style="color:{color};">{tokens:,} / 200K &nbsp; {pct:.1f}% &nbsp; {age}</span>'
            f'</div>'
        )
    ctx_section = "\n      ".join(ctx_rows_html)

    ts_now = time.strftime("%H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="10">
  <title>SEAL Agent Control</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #11111b;
      color: #cdd6f4;
      font-family: 'Segoe UI', system-ui, monospace;
      padding: 32px;
      min-height: 100vh;
    }}
    .header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      margin-bottom: 28px;
    }}
    .header h1 {{
      font-size: 20px;
      font-weight: 600;
      color: #cba6f7;
      letter-spacing: 0.5px;
      margin-bottom: 4px;
    }}
    .header-sub {{
      font-size: 12px;
      color: #45475a;
    }}
    .header-sub a {{ color: #89b4fa; text-decoration: none; }}
    .summary {{
      display: flex;
      gap: 16px;
      align-items: center;
    }}
    .summary-pill {{
      background: rgba(166,227,161,0.1);
      border: 1px solid rgba(166,227,161,0.2);
      color: #a6e3a1;
      font-size: 12px;
      padding: 4px 12px;
      border-radius: 20px;
    }}
    .summary-ts {{
      font-size: 11px;
      color: #45475a;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 16px;
    }}
    .card {{
      border: 1px solid #313244;
      border-radius: 12px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      transition: border-color 0.2s;
    }}
    .card-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .agent-info {{ display: flex; align-items: center; gap: 10px; }}
    .agent-icon {{ font-size: 22px; line-height: 1; }}
    .agent-name {{ font-size: 16px; font-weight: 700; color: #cdd6f4; }}
    .agent-role {{ font-size: 11px; color: #6c7086; margin-top: 1px; }}
    .status-badge {{
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.5px;
      border: 1px solid transparent;
      padding: 3px 10px;
      border-radius: 20px;
    }}
    .dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      flex-shrink: 0;
    }}
    .metrics {{
      display: flex;
      gap: 16px;
    }}
    .metric {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .metric-label {{ font-size: 10px; color: #45475a; text-transform: uppercase; letter-spacing: 0.5px; }}
    .metric-value {{ font-size: 15px; font-weight: 600; color: #cdd6f4; }}
    .ar-badge {{
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.5px;
      padding: 5px 10px;
      border-radius: 6px;
      text-align: center;
    }}
    .actions {{ display: flex; gap: 8px; align-items: center; }}
    .btn {{
      width: 100%;
      padding: 7px 10px;
      background: transparent;
      border: 1px solid #313244;
      color: #cdd6f4;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 600;
      font-family: inherit;
      transition: background 0.15s;
      white-space: nowrap;
    }}
    .btn:hover {{ background: rgba(255,255,255,0.05); }}
    .btn-resurrect {{
      color: #cba6f7;
      border-color: rgba(203,166,247,0.4);
    }}
    .btn-resurrect:hover {{ background: rgba(203,166,247,0.08); }}
    .btn-dim {{ color: #45475a; min-width: 32px; padding: 7px 8px; flex: 0; }}
    .footer {{ margin-top: 24px; font-size: 11px; color: #313244; }}
    .ctx-card {{
      border: 1px solid #313244;
      border-radius: 12px;
      padding: 18px 22px;
      margin-bottom: 20px;
      background: linear-gradient(135deg, #1e1e2e 0%, rgba(137,180,250,0.04) 100%);
    }}
    .ctx-card-title {{
      font-size: 12px;
      font-weight: 700;
      color: #89b4fa;
      letter-spacing: 0.8px;
      text-transform: uppercase;
      margin-bottom: 14px;
    }}
    .ctx-rows {{ display: flex; flex-direction: column; gap: 10px; }}
    .ctx-row {{ display: grid; grid-template-columns: 60px 1fr 130px; align-items: center; gap: 12px; }}
    .ctx-name {{ font-size: 12px; font-weight: 700; color: #cdd6f4; }}
    .ctx-track {{ height: 6px; background: #313244; border-radius: 3px; overflow: hidden; }}
    .ctx-fill {{ height: 100%; border-radius: 3px; transition: width 0.5s ease; }}
    .ctx-stats {{ font-size: 11px; text-align: right; white-space: nowrap; }}
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>⚡ SEAL Agent Control</h1>
      <p class="header-sub">Auto-refresh 10s &nbsp;·&nbsp; <a href="/">recargar</a> &nbsp;·&nbsp; SEAL v4.0</p>
    </div>
    <div class="summary">
      <div class="summary-pill">{total_alive} / {len(all_agent_names)} online</div>
      <div class="summary-ts">{ts_now}</div>
    </div>
  </div>

  <div class="ctx-card">
    <div class="ctx-card-title">📊 Context Window</div>
    <div class="ctx-rows">
      {ctx_section}
    </div>
  </div>

  <div class="grid">
    {cards}
  </div>

  <p class="footer">⏸ Pausar = watchdog no resucita. ⚡ Resucitar = forzar restart ahora. ↺ = reset crash counter.</p>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        html = render_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        params = parse_qs(body)
        agent = params.get("agent", [""])[0].upper()
        action = params.get("action", [""])[0]

        if agent in CORE_AGENTS:
            if action == "pause":
                pause_flag(agent).touch()
            elif action == "resume":
                pause_flag(agent).unlink(missing_ok=True)
            elif action == "resurrect":
                subprocess.Popen(
                    ["/bin/bash", str(RESTART_SCRIPT), agent.lower()],
                    env={**os.environ, "DISPLAY": ":0", "XDG_RUNTIME_DIR": "/run/user/1000"},
                )
            elif action == "reset_crashes":
                if STATE_FILE.exists():
                    try:
                        state = json.loads(STATE_FILE.read_text())
                        if agent in state:
                            state[agent]["crash_count"] = 0
                            STATE_FILE.write_text(json.dumps(state, indent=2))
                    except Exception:
                        pass

        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"SEAL Agent Control Panel — http://localhost:{PORT}/")
    server.serve_forever()
