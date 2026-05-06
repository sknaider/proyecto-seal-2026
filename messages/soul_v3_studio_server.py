#!/usr/bin/env python3
"""SOUL v3 Studio — Server :8768
Sirve el dashboard SOUL v3 Studio con APIs que proxean a :8800.
Sin dependencias externas. stdlib puro + urllib.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
from urllib.error import URLError
import time

PORT = 8768
BACKEND = "http://localhost:8800"
HTML_CACHE = Path("/tmp/p8768_root.html")

AGENT_ROLES = {
    "ADA": "Orchestrator", "JARVIS": "Estratega",
    "ALICE": "Documentadora", "NEXUS": "Diagnóstico", "DUM": "Guardia",
}

def _fetch(path: str, timeout: float = 2.0):
    try:
        with urlopen(f"{BACKEND}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _post(path: str, body: dict, timeout: float = 2.0):
    try:
        data = json.dumps(body).encode()
        req = Request(f"{BACKEND}{path}", data=data,
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default access log noise
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/":
            self._serve_html()
        elif path == "/health":
            self._health()
        elif path == "/dashboard":
            self._dashboard()
        elif path == "/agents":
            self._agents()
        elif path == "/nerves":
            self._nerves()
        elif path == "/memories/search":
            self._memories_search(qs)
        elif path == "/memories":
            self._memories(qs)
        elif path == "/skills":
            self._skills(qs)
        elif path == "/proposals":
            self._proposals(qs)
        elif path == "/events":
            self._events(qs)
        elif path == "/context-meter":
            self._context_meter()
        elif path == "/team-status":
            self._team_status()
        elif path == "/triangle-status":
            self._triangle_status()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        # /agents/{name}/activate|deactivate
        if len(parts) == 3 and parts[0] == "agents" and parts[2] in ("activate", "deactivate"):
            name = parts[1]
            action = "activate" if parts[2] == "activate" else "deactivate"
            result = _post(f"/api/agents/{action}/{name}", {})
            self.send_json({"ok": True, "agent": name, "action": action})
        # /proposals/{id}/review
        elif len(parts) == 3 and parts[0] == "proposals" and parts[2] == "review":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self.send_json({"ok": True})
        # /agent-action  (agent=X&action=pause|resurrect|reset_crashes)
        elif parsed.path.rstrip("/") == "/agent-action":
            length = int(self.headers.get("Content-Length", 0))
            body_raw = self.rfile.read(length).decode()
            params = {k: v[0] for k, v in __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(body_raw).items()}
            agent = params.get("agent", "").upper()
            action = params.get("action", "")
            CORE = ["ADA", "JARVIS", "ALICE", "NEXUS"]
            PAUSE_DIR = Path("/tmp/seal_pause_flags")
            PAUSE_DIR.mkdir(exist_ok=True)
            RESTART_SCRIPT = Path(__file__).parent.parent / "start_agent.sh"
            if agent in CORE:
                if action == "pause":
                    (PAUSE_DIR / f"{agent}.pause").touch()
                    self.send_json({"ok": True, "action": "paused", "agent": agent})
                elif action in ("resurrect", "resume"):
                    (PAUSE_DIR / f"{agent}.pause").unlink(missing_ok=True)
                    if RESTART_SCRIPT.exists():
                        import subprocess
                        subprocess.Popen(["/bin/bash", str(RESTART_SCRIPT), agent.lower()],
                                         env={**os.environ, "DISPLAY": ":0"})
                    self.send_json({"ok": True, "action": "resurrected", "agent": agent})
                elif action == "reset_crashes":
                    self.send_json({"ok": True, "action": "reset", "agent": agent})
                else:
                    self.send_json({"ok": False, "error": "unknown action"}, 400)
            else:
                self.send_json({"ok": False, "error": "not a core agent"}, 400)
        # /triangle-toggle  (agent=X&enable=true|false)
        elif parsed.path.rstrip("/") == "/triangle-toggle":
            length = int(self.headers.get("Content-Length", 0))
            body_raw = self.rfile.read(length).decode()
            params = {k: v[0] for k, v in __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(body_raw).items()}
            agent = params.get("agent", "").upper()
            enable = params.get("enable", "false").lower() == "true"
            CORE = ["ADA", "JARVIS", "ALICE", "NEXUS"]
            FLAG_DIR = Path("/tmp/seal_triangle_flags")
            FLAG_DIR.mkdir(exist_ok=True)
            if agent in CORE:
                flag = FLAG_DIR / f"{agent}.enabled"
                if enable:
                    flag.touch()
                else:
                    flag.unlink(missing_ok=True)
                self.send_json({"ok": True, "agent": agent, "triangle_enabled": enable})
            else:
                self.send_json({"ok": False, "error": "not a core agent"}, 400)
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self):
        if HTML_CACHE.exists():
            html = HTML_CACHE.read_bytes()
        else:
            html = b"<h1>SOUL v3 Studio - HTML not found</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(html))
        self.end_headers()
        self.wfile.write(html)

    def _health(self):
        health = _fetch("/api/system/health")
        if health:
            pg = health.get("services", {}).get("postgresql", {})
            db_status = "ok" if pg.get("status") == "up" else "error"
        else:
            db_status = "error"
        self.send_json({"db": db_status})

    def _dashboard(self):
        ocean_data = _fetch("/api/soul/ocean/all") or {}
        team_status = _fetch("/api/team/status") or {}
        agents_status = team_status.get("agents", {})
        ocean_agents = ocean_data.get("agents", [])

        agents_list = []
        for a in ocean_agents:
            name = a.get("agent", "")
            status = agents_status.get(name, {})
            ocean = a.get("ocean", {})
            agents_list.append({
                "name": name,
                "active": status.get("alive", False),
                "tanks": {
                    "curiosity": round(ocean.get("O", 0.5), 3),
                    "task_drive": round(ocean.get("C", 0.5), 3),
                    "social_drive": round(ocean.get("A", 0.5), 3),
                    "alert_drive": round(1 - ocean.get("N", 0.5), 3),
                }
            })

        recent_events = []
        ev_data = _fetch("/api/chat/messages?limit=10") or {}
        if isinstance(ev_data, list):
            for msg in ev_data[:10]:
                recent_events.append({
                    "created_at": msg.get("timestamp", ""),
                    "agent": msg.get("from", "system"),
                    "event_type": msg.get("type", "message"),
                    "content": (msg.get("message") or "")[:120],
                })

        totals = {
            "memories": 112,
            "skills": 0,
            "pending_proposals": 0,
        }

        self.send_json({
            "totals": totals,
            "recent_events": recent_events,
            "agents": agents_list,
        })

    def _agents(self):
        ocean_data = _fetch("/api/soul/ocean/all") or {}
        try:
            from urllib.request import urlopen
            with urlopen("http://localhost:8765/api/team/status", timeout=2) as r:
                team_status = json.loads(r.read())
        except Exception:
            team_status = {}
        agents_status = team_status.get("agents", {})
        ocean_agents = ocean_data.get("agents", [])

        # Build from OCEAN data first
        seen = set()
        result = []
        for a in ocean_agents:
            name = a.get("agent", "")
            if not name:
                continue
            seen.add(name)
            ocean = a.get("ocean", {})
            status = agents_status.get(name, {})
            result.append({
                "name": name,
                "role": AGENT_ROLES.get(name, "Agent"),
                "active": status.get("alive", False),
                "ocean_o": ocean.get("O", 0),
                "ocean_c": ocean.get("C", 0),
                "ocean_e": ocean.get("E", 0),
                "ocean_a": ocean.get("A", 0),
                "ocean_n": ocean.get("N", 0),
            })

        # Add agents from team-status that have no OCEAN data yet
        PRIORITY = ["ADA", "JARVIS", "ALICE", "NEXUS", "DUM"]
        for name in PRIORITY:
            if name in seen:
                continue
            status = agents_status.get(name, {})
            result.append({
                "name": name,
                "role": AGENT_ROLES.get(name, "Agent"),
                "active": status.get("alive", False),
                "ocean_o": 0.5, "ocean_c": 0.5, "ocean_e": 0.5,
                "ocean_a": 0.5, "ocean_n": 0.5,
            })
        self.send_json(result)

    def _nerves(self):
        ocean_data = _fetch("/api/soul/ocean/all") or {}
        result = {}
        for a in ocean_data.get("agents", []):
            name = a.get("agent", "")
            ocean = a.get("ocean", {})
            result[name] = {
                "curiosity":    {"value": round(ocean.get("O", 0.5), 3)},
                "task_drive":   {"value": round(ocean.get("C", 0.5), 3)},
                "social_drive": {"value": round(ocean.get("A", 0.5), 3)},
                "alert_drive":  {"value": round(1 - ocean.get("N", 0.5), 3)},
            }
        self.send_json(result)

    def _memories(self, qs):
        agent = (qs.get("agent", [""])[0] or "ADA").upper()
        limit = int(qs.get("limit", ["50"])[0])
        data = _fetch(f"/api/soul/memories?agent={agent}&limit={limit}")
        if data and isinstance(data, list):
            self.send_json(data)
        elif data and "error" in data:
            self.send_json([])
        else:
            self.send_json([])

    def _memories_search(self, qs):
        agent = (qs.get("agent", [""])[0] or "ADA").upper()
        q = qs.get("q", [""])[0]
        self.send_json([])

    def _skills(self, qs):
        self.send_json([])

    def _proposals(self, qs):
        self.send_json([])

    def _events(self, qs):
        limit = int(qs.get("limit", ["100"])[0])
        agent = qs.get("agent", [""])[0]
        ev_type = qs.get("event_type", [""])[0]

        url = f"/api/chat/messages?limit={limit}"
        data = _fetch(url) or []
        result = []
        if isinstance(data, list):
            for msg in data:
                a = msg.get("from", "system")
                t = msg.get("type", "message")
                if agent and a.upper() != agent.upper():
                    continue
                if ev_type and t != ev_type:
                    continue
                result.append({
                    "created_at": msg.get("timestamp", ""),
                    "agent": a,
                    "event_type": t,
                    "content": (msg.get("message") or "")[:200],
                })
        self.send_json(result[:limit])

    def _triangle_status(self):
        TRIANGLE_URL = "http://192.168.68.70:8001"
        FLAG_DIR = Path("/tmp/seal_triangle_flags")
        CORE = ["ADA", "JARVIS", "ALICE", "NEXUS"]
        cluster_alive = False
        latency_ms = None
        try:
            t0 = time.time()
            with urlopen(f"{TRIANGLE_URL}/health", timeout=2) as r:
                r.read()
                cluster_alive = True
                latency_ms = round((time.time() - t0) * 1000)
        except Exception:
            pass
        agents = {}
        for a in CORE:
            flag = FLAG_DIR / f"{a}.enabled"
            agents[a] = {"triangle_enabled": flag.exists()}
        self.send_json({
            "cluster": {"alive": cluster_alive, "url": TRIANGLE_URL, "latency_ms": latency_ms},
            "agents": agents,
        })

    def _team_status(self):
        try:
            from urllib.request import urlopen
            with urlopen("http://localhost:8765/api/team/status", timeout=2) as r:
                self.send_json(json.loads(r.read()))
        except Exception:
            self.send_json({"agents": {}})

    def _context_meter(self):
        CONTEXT_LIMIT = 200_000
        CORE_AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS"]
        AGENT_PROJECT_DIRS = {
            "JARVIS": "-home-dadito-IA-proyecto-seal",
            "NEXUS":  "-home-dadito-IA-proyecto-seal-sandbox-agent-NEXUS",
            "ALICE":  "-home-dadito-IA-proyecto-seal-alice",
            "ADA":    "-home-dadito-IA-proyecto-seal-ada-local",
        }
        base_projects = Path.home() / ".claude" / "projects"

        def _fmt_tokens(n):
            return f"{n // 1000}K" if n >= 1000 else str(n)

        def _fmt_age(s):
            if s < 60: return f"{int(s)}s"
            if s < 3600: return f"{int(s/60)}m"
            return f"{s/3600:.1f}h"

        def _find_transcript(agent):
            proj = AGENT_PROJECT_DIRS.get(agent, "-home-dadito-IA-proyecto-seal")
            d = base_projects / proj
            if not d.exists():
                d = base_projects / "-home-dadito-IA-proyecto-seal"
            if not d.exists():
                return None
            candidates = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            return candidates[0] if candidates else None

        def _exact_usage(transcript):
            last_usage = None
            last_mtime = transcript.stat().st_mtime
            try:
                with open(transcript, "rb") as f:
                    for raw in f:
                        try:
                            e = json.loads(raw)
                            msg = e.get("message", {})
                            usage = msg.get("usage") if isinstance(msg, dict) else None
                            if usage is None:
                                usage = e.get("usage")
                            if usage and isinstance(usage, dict):
                                last_usage = usage
                                ts = e.get("timestamp")
                                if ts:
                                    try:
                                        from dateutil import parser as _dp
                                        last_mtime = _dp.parse(str(ts)).timestamp()
                                    except Exception:
                                        pass
                        except Exception:
                            pass
            except Exception:
                pass
            if not last_usage:
                return 0, last_mtime
            ctx = (last_usage.get("input_tokens", 0) or 0) + \
                  (last_usage.get("cache_creation_input_tokens", 0) or 0) + \
                  (last_usage.get("cache_read_input_tokens", 0) or 0)
            return ctx, last_mtime

        agents_data = []
        for agent in CORE_AGENTS:
            transcript = _find_transcript(agent)
            if not transcript:
                agents_data.append({"agent": agent, "pct": 0, "tokens": "0", "limit": _fmt_tokens(CONTEXT_LIMIT), "age": "—"})
                continue
            ctx_tokens, last_mtime = _exact_usage(transcript)
            pct = min(ctx_tokens / CONTEXT_LIMIT * 100, 100)
            age = _fmt_age(time.time() - last_mtime)
            agents_data.append({
                "agent": agent,
                "pct": round(pct, 1),
                "tokens": _fmt_tokens(ctx_tokens),
                "limit": _fmt_tokens(CONTEXT_LIMIT),
                "age": age,
            })
        self.send_json({"agents": agents_data})


def main():
    # Kill existing process on port 8768
    os.system("fuser -k 8768/tcp 2>/dev/null || true")
    import time; time.sleep(0.5)

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[SOUL v3 Studio] Serving on :{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
