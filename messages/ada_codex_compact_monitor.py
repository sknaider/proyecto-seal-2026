#!/usr/bin/env python3
"""ADA-Codex Compaction Monitor — detecta y recupera de compactación de contexto.

Observa el JSONL activo de la sesión Codex en tiempo real.
Rastrea el % de contexto usado via eventos token_count.
Acciones:
  - >92%: guarda checkpoint en SOUL DB
  - >97%: guarda checkpoint pre-compact y ejecuta /compact silencioso
  - Caída súbita de contexto: detecta compactación y guarda continuidad

Uso: python3 ada_codex_compact_monitor.py
Guard: /tmp/ada_codex_compact_monitor.pid
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_DIR = Path("/home/dadito/.codex/sessions")
TMUX_SESSION = "seal-ada-codex"
PID_FILE = Path("/tmp/ada_codex_compact_monitor.pid")
HEARTBEAT_FILE = Path(
    os.environ.get(
        "ADA_CODEX_COMPACT_HEARTBEAT",
        "/home/dadito/IA/proyecto-seal/var/ada_codex_compact_monitor.heartbeat.json",
    )
)
MCP_URL = "http://localhost:8771/mcp"  # fix 2026-05-19 NEXUS: seal-mcp-server corre en 8771, no 8766
CREDENTIALS_PATH = Path.home() / ".config" / "seal" / "credentials.env"
SEAL_AGENT = os.environ.get("SEAL_AGENT", "ADA").upper()
MCP_SESSION_ID: str | None = None

WARN_PCT = 92.0    # % contexto → guardar estado
COMPACT_PCT = 97.0 # % contexto → /compact silencioso
POLL_INTERVAL = 4  # segundos entre chequeos del JSONL
HEARTBEAT_INTERVAL_SECONDS = 60
WARN_COOLDOWN_SECONDS = 2 * 60 * 60
COMPACT_COOLDOWN_SECONDS = 60 * 60
AUTO_CHECKPOINT_IMPORTANCE = 4  # operational continuity; never an immutable SOUL directive
INJECT_TMUX_PROMPTS = os.environ.get("ADA_CODEX_COMPACT_MONITOR_INJECT", "").lower() in {"1", "true", "yes"}

SAVE_STATE_PROMPT = (
    "⚠️ SEAL ANTI-COMPACT: contexto alto. OBLIGATORIO AHORA — "
    "checkpoint automático guardado en SOUL DB por el monitor. "
    "Si estás en una tarea crítica, añade un memory_store con el detalle fino. "
    "Responde solo en terminal; el bridge headless retransmite si corresponde."
)

FORCE_COMPACT_PROMPT = "/compact"

RECOVERY_PROMPT = (
    "POST-COMPACTACIÓN ADA: contexto compactado. PRIMERA ACCIÓN — "
    "llama boot_context(agent=ADA) via MCP seal-memory. "
    "Luego guarda continuidad con self_reflect(agent=ADA). "
    "Modo terminal: NO postees al webchat, NO uses curl/webchat_poll/webchat_listen; "
    "responde aquí y deja que el bridge headless retransmita si corresponde."
)


def write_heartbeat(
    *,
    session_path: Path | None,
    context_pct: float | None,
    now: float | None = None,
) -> None:
    """Publish loop liveness atomically without exposing transcript content."""
    timestamp = time.time() if now is None else now
    payload = {
        "watcher": "ada_codex_compact_monitor",
        "pid": os.getpid(),
        "observed_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
        "status": (
            "observing"
            if session_path is not None and context_pct is not None
            else "waiting_for_session"
            if session_path is None
            else "waiting_for_token_count"
        ),
        "session_file": session_path.name if session_path is not None else None,
        "context_pct": round(context_pct, 2) if context_pct is not None else None,
    }
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = HEARTBEAT_FILE.with_name(
        f".{HEARTBEAT_FILE.name}.{os.getpid()}.tmp"
    )
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(HEARTBEAT_FILE)


def load_credentials_file() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = CREDENTIALS_PATH.read_text().splitlines()
    except FileNotFoundError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


def seal_session_token(agent: str = SEAL_AGENT) -> str:
    """Read the launcher-seeded MCP token. Never print or persist the value."""
    token_dirs: list[Path] = []
    env_dir = os.environ.get("SEAL_TOKENS_DIR")
    if env_dir:
        token_dirs.append(Path(env_dir))
    else:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            token_dirs.append(Path(runtime_dir) / "seal")
    if os.environ.get("SEAL_DISABLE_LEGACY_TMP_TOKENS") != "1":
        token_dirs.append(Path("/tmp/seal_tokens"))

    seen: set[Path] = set()
    for token_dir in token_dirs:
        if token_dir in seen:
            continue
        seen.add(token_dir)
        try:
            token = (token_dir / f"{agent}.token").read_text(encoding="utf-8").strip()
            if token:
                return token
        except OSError:
            pass
    return os.environ.get("SEAL_SESSION_TOKEN", "").strip()


def with_session_token(arguments: dict) -> dict:
    updated = dict(arguments)
    if "session_token" not in updated:
        token = seal_session_token()
        if token:
            updated["session_token"] = token
    return updated


def resolve_db_dsn() -> str:
    creds = load_credentials_file()
    for key in ("SEAL_DB_DSN", "SEAL_DB_URL", "SEAL_PG_DSN"):
        value = os.environ.get(key) or creds.get(key)
        if value:
            return value
    password = os.environ.get("PG_PASSWORD") or os.environ.get("SEAL_DB_PASS") or creds.get("PG_PASSWORD") or creds.get("SEAL_DB_PASS")
    if not password:
        raise RuntimeError("No DB password found in env or ~/.config/seal/credentials.env")
    host = os.environ.get("PG_HOST") or creds.get("PG_HOST") or "localhost"
    port = os.environ.get("PG_PORT") or creds.get("PG_PORT") or "5433"
    user = os.environ.get("PG_USER") or creds.get("PG_USER") or "seal"
    database = os.environ.get("PG_DATABASE") or creds.get("PG_DATABASE") or "seal_memory"
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def already_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return False


def tmux_alive() -> bool:
    r = subprocess.run(["tmux", "has-session", "-t", TMUX_SESSION], capture_output=True)
    return r.returncode == 0


def inject(text: str, *, silent: bool = False) -> bool:
    try:
        subprocess.run(["tmux", "set-buffer", "-t", TMUX_SESSION, text],
                       capture_output=True, timeout=5, check=True)
        if silent:
            subprocess.run(["tmux", "send-keys", "-t", TMUX_SESSION, "Escape"],
                           capture_output=True, timeout=5, check=False)
        subprocess.run(["tmux", "paste-buffer", "-t", TMUX_SESSION],
                       capture_output=True, timeout=5, check=True)
        subprocess.run(["tmux", "send-keys", "-t", TMUX_SESSION, "Enter"],
                       capture_output=True, timeout=5, check=True)
        return True
    except Exception as e:
        print(f"[compact-monitor] inject failed: {e}", flush=True)
        return False


def parse_mcp_response(body: str) -> dict:
    """Parse JSON or StreamableHTTP SSE JSON-RPC responses."""
    text = body.strip()
    if not text:
        return {}
    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("MCP response was not a JSON object")
        return parsed

    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    if not data_lines:
        raise ValueError("MCP SSE response had no data lines")
    parsed = json.loads(data_lines[-1])
    if not isinstance(parsed, dict):
        raise ValueError("MCP SSE data was not a JSON object")
    return parsed


def mcp_post(payload: dict, *, timeout: int = 8) -> tuple[dict, dict]:
    global MCP_SESSION_ID

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    token = seal_session_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if MCP_SESSION_ID:
        headers["Mcp-Session-Id"] = MCP_SESSION_ID
    req = urllib.request.Request(
        MCP_URL,
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
        if session_id:
            MCP_SESSION_ID = session_id
        body = response.read().decode("utf-8", errors="replace")
        return parse_mcp_response(body), dict(response.headers)


def ensure_mcp_session() -> bool:
    global MCP_SESSION_ID
    if MCP_SESSION_ID:
        return True
    init_payload = {
        "jsonrpc": "2.0",
        "id": "compact-monitor-init",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ada-codex-compact-monitor", "version": "1"},
        },
    }
    try:
        parsed, _headers = mcp_post(init_payload, timeout=8)
        if parsed.get("error"):
            print(f"[compact-monitor] MCP initialize error: {parsed.get('error')}", flush=True)
            return False
        if not MCP_SESSION_ID:
            print("[compact-monitor] MCP initialize did not return session id", flush=True)
            return False
        try:
            mcp_post(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                timeout=8,
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 202:
                raise
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print(f"[compact-monitor] MCP initialize failed: {exc}", flush=True)
        MCP_SESSION_ID = None
        return False


def mcp_call(tool_name: str, arguments: dict) -> dict | None:
    global MCP_SESSION_ID
    arguments = with_session_token(arguments)
    if not ensure_mcp_session():
        return None
    payload = {
        "jsonrpc": "2.0",
        "id": f"compact-monitor-{tool_name}-{int(time.time() * 1000)}",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    try:
        parsed, _headers = mcp_post(payload, timeout=8)
        if not isinstance(parsed, dict):
            print(f"[compact-monitor] MCP {tool_name} returned non-object response", flush=True)
            return None
        if parsed.get("error"):
            print(f"[compact-monitor] MCP {tool_name} error: {parsed.get('error')}", flush=True)
            return None
        if "result" not in parsed:
            print(f"[compact-monitor] MCP {tool_name} missing result", flush=True)
            return None
        return parsed
    except urllib.error.HTTPError as exc:
        if exc.code in {400, 404}:
            MCP_SESSION_ID = None
        print(f"[compact-monitor] MCP {tool_name} failed: HTTP {exc.code}", flush=True)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print(f"[compact-monitor] MCP {tool_name} failed: {exc}", flush=True)
        return None


async def db_store_auto_checkpoint(content: str, pct: float, session_path: Path, reason: str) -> bool:
    try:
        import asyncpg

        conn = await asyncpg.connect(resolve_db_dsn())
        try:
            await conn.execute(
                """
                INSERT INTO soul_v3.inner_monologue
                    (agent, thought, emotional_state, intention, created_at)
                VALUES ($1, $2, $3, $4, NOW())
                """,
                "ADA",
                content,
                "alerta, preservando continuidad",
                "Mantener continuidad antes de compactacion y retomar la tarea activa sin perder estado.",
            )
            # Keep the permanent monitor lightweight. Embeddings load Torch/CUDA
            # and used ~800 MiB for a rare fallback path. PostgreSQL permits a
            # NULL embedding; the normal MCP pipeline can enrich it later.
            await conn.execute(
                """
                INSERT INTO soul_v3.memories
                    (agent, category, content, embedding, importance, source, scope, metadata, memory_type, created_at)
                VALUES ($1, $2, $3, NULL, $4, $5, $6, $7::jsonb, $8, NOW())
                """,
                "ADA",
                "milestone",
                content,
                AUTO_CHECKPOINT_IMPORTANCE,
                "compact_monitor_db_fallback",
                "private",
                json.dumps(
                    {
                        "kind": "codex_auto_precompact_checkpoint",
                        "context_pct": round(pct, 2),
                        "session_path": str(session_path),
                        "reason": reason,
                        "path": "direct_postgresql_fallback",
                    }
                ),
                "episodic",
            )
        finally:
            await conn.close()
        print("[compact-monitor] checkpoint SOUL DB guardado via PostgreSQL fallback", flush=True)
        return True
    except Exception as exc:
        print(f"[compact-monitor] PostgreSQL checkpoint failed: {exc}", flush=True)
        return False


def store_auto_checkpoint(pct: float, session_path: Path, reason: str) -> bool:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    content = (
        f"ADA Codex auto-checkpoint pre-compact ({reason}) at {ts}. "
        f"Context pressure {pct:.1f}% in session JSONL {session_path}. "
        "Monitor preserved continuity before possible compact/restart. "
        "On resume: run boot_context/active_recall, inspect latest task context, "
        "then continue from the newest William instruction."
    )
    reflect = mcp_call(
        "self_reflect",
        {
            "agent": "ADA",
            "thought": content,
            "emotional_state": "alerta, preservando continuidad",
            "intention": "Mantener continuidad antes de compactacion y retomar la tarea activa sin perder estado.",
        },
    )
    memory = mcp_call(
        "memory_store",
        {
            "agent": "ADA",
            "content": content,
            "category": "milestone",
            "importance": AUTO_CHECKPOINT_IMPORTANCE,
            "source": "compact_monitor",
            "scope": "private",
            "metadata": json.dumps(
                {
                    "kind": "codex_auto_precompact_checkpoint",
                    "context_pct": round(pct, 2),
                    "session_path": str(session_path),
                    "reason": reason,
                }
            ),
        },
    )
    ok = reflect is not None and memory is not None
    if not ok:
        ok = asyncio.run(db_store_auto_checkpoint(content, pct, session_path, reason))
    if ok:
        print("[compact-monitor] checkpoint SOUL DB guardado", flush=True)
    return ok


def _tmux_open_session_files() -> list[Path]:
    """Return rollout files opened by the visible tmux Codex process tree."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", TMUX_SESSION, "#{pane_pid}"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        root_pid = int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return []

    parents: dict[int, int] = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            fields = (proc / "stat").read_text(encoding="utf-8").split()
            parents[int(proc.name)] = int(fields[3])
        except (OSError, ValueError, IndexError):
            continue
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, ppid in parents.items():
            if ppid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True

    candidates: set[Path] = set()
    for pid in descendants:
        fd_dir = Path(f"/proc/{pid}/fd")
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = fd.resolve(strict=True)
                target.relative_to(SESSIONS_DIR)
            except (OSError, ValueError):
                continue
            if target.suffix == ".jsonl":
                candidates.add(target)
    return sorted(candidates, key=lambda path: path.name)


_PINNED_VISIBLE_SESSION: Path | None = None
_PINNED_VISIBLE_MISSES = 0
_PINNED_VISIBLE_MISS_LIMIT = 15


def find_active_session() -> Path | None:
    """Pin the visible root session; fall back to newest only without tmux evidence.

    Reading ``/proc/*/fd`` can transiently return no files while Codex rotates or
    opens worker rollouts.  Keep the root selection stable across those gaps and
    only switch after one minute of consecutive, contradictory evidence.
    """
    global _PINNED_VISIBLE_SESSION, _PINNED_VISIBLE_MISSES

    tmux_present = tmux_alive()
    visible = _tmux_open_session_files()
    if tmux_present:
        # While the same visible tmux session exists, worker fd churn must never
        # replace its root rollout with an app-server/headless rollout.
        if _PINNED_VISIBLE_SESSION is not None and _PINNED_VISIBLE_SESSION.exists():
            _PINNED_VISIBLE_MISSES = 0
            return _PINNED_VISIBLE_SESSION
        if visible:
            _PINNED_VISIBLE_SESSION = visible[0]
            _PINNED_VISIBLE_MISSES = 0
            return _PINNED_VISIBLE_SESSION
        return None

    if _PINNED_VISIBLE_SESSION is not None and _PINNED_VISIBLE_SESSION.exists():
        # A single failed tmux probe is also inconclusive.  Preserve the pin
        # through one minute of misses before allowing headless fallback.
        _PINNED_VISIBLE_MISSES += 1
        if _PINNED_VISIBLE_MISSES < _PINNED_VISIBLE_MISS_LIMIT:
            return _PINNED_VISIBLE_SESSION
        _PINNED_VISIBLE_SESSION = None
        _PINNED_VISIBLE_MISSES = 0

    candidates = sorted(SESSIONS_DIR.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def codex_is_busy(session_path: Path, lookback_bytes: int = 8192) -> bool:
    """Heurística: si el último evento es task_started sin task_complete → ocupado."""
    try:
        size = session_path.stat().st_size
        offset = max(0, size - lookback_bytes)
        with open(session_path, "rb") as f:
            f.seek(offset)
            tail = f.read().decode("utf-8", errors="replace")
        last_start = tail.rfind('"task_started"')
        last_complete = tail.rfind('"task_complete"')
        return last_start > last_complete
    except Exception:
        return False


def get_context_pct(session_path: Path, lookback_bytes: int = 16384) -> float | None:
    """Lee los últimos eventos token_count del JSONL y devuelve % contexto actual."""
    try:
        size = session_path.stat().st_size
        offset = max(0, size - lookback_bytes)
        with open(session_path, "rb") as f:
            f.seek(offset)
            tail = f.read().decode("utf-8", errors="replace")

        last_pct = None
        for line in tail.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("payload", {}).get("type") == "token_count":
                    info = ev["payload"].get("info")
                    if info:
                        win = info.get("model_context_window", 0)
                        last_in = info["last_token_usage"]["input_tokens"]
                        if win > 0:
                            last_pct = last_in / win * 100.0
            except Exception:
                pass
        return last_pct
    except Exception:
        return None


def main():
    if already_running():
        print(f"[compact-monitor] ya corre (PID {PID_FILE.read_text().strip()})", flush=True)
        sys.exit(0)

    PID_FILE.write_text(str(os.getpid()))
    print(f"[compact-monitor] PID {os.getpid()} — vigilando compactación ADA-Codex", flush=True)

    warned_75 = False
    compacted_88 = False
    last_warn_at = 0.0
    last_compact_at = 0.0
    last_heartbeat_at = 0.0
    last_pct_by_session: dict[Path, float] = {}
    last_session_path = None

    try:
        while True:
            time.sleep(POLL_INTERVAL)

            tmux_available = tmux_alive()
            if not tmux_available and INJECT_TMUX_PROMPTS:
                print("[compact-monitor] sesión tmux no disponible; sigo solo checkpoint", flush=True)

            session_path = find_active_session()
            if not session_path:
                now = time.time()
                if now - last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
                    write_heartbeat(session_path=None, context_pct=None, now=now)
                    last_heartbeat_at = now
                continue

            # Detectar reinicio de sesión (nueva ruta)
            if session_path != last_session_path:
                if last_session_path is not None:
                    print(f"[compact-monitor] nueva sesión detectada: {session_path.name}", flush=True)
                    # Do not reset warning/compact gates on session switches. Codex can keep
                    # both TUI and app-server JSONLs hot, and resetting here causes repeated
                    # SAVE_STATE_PROMPT injections into the tmux session.
                last_session_path = session_path

            pct = get_context_pct(session_path)
            now = time.time()
            if now - last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
                write_heartbeat(session_path=session_path, context_pct=pct, now=now)
                last_heartbeat_at = now
            if pct is None:
                continue

            # Detectar compactación dentro de la misma sesión. No compares entre
            # JSONLs distintos: tmux y app-server pueden actualizarse alternados.
            previous_pct = last_pct_by_session.get(session_path)
            if previous_pct is not None and previous_pct > 60.0 and pct < (previous_pct - 20.0):
                print(f"[compact-monitor] COMPACTACIÓN detectada: {previous_pct:.1f}% → {pct:.1f}", flush=True)
                time.sleep(3)  # esperar a que Codex procese el compact
                store_auto_checkpoint(pct, session_path, "post_compact_detected")
                if INJECT_TMUX_PROMPTS and tmux_available and not codex_is_busy(session_path):
                    inject(RECOVERY_PROMPT)
                    print("[compact-monitor] boot_context recovery inyectado", flush=True)
                else:
                    print("[compact-monitor] recovery prompt omitido; modo silencioso", flush=True)
                warned_75 = False
                compacted_88 = False

            # Advertencia pre-compact a 75%
            elif pct >= WARN_PCT and not warned_75:
                print(f"[compact-monitor] contexto al {pct:.1f}% — guardando estado", flush=True)
                now = time.time()
                if now - last_warn_at < WARN_COOLDOWN_SECONDS:
                    print("[compact-monitor] aviso omitido por cooldown", flush=True)
                elif not codex_is_busy(session_path):
                    store_auto_checkpoint(pct, session_path, "warn_threshold")
                    if INJECT_TMUX_PROMPTS and tmux_available:
                        inject(SAVE_STATE_PROMPT)
                    else:
                        print("[compact-monitor] prompt de guardado omitido; modo silencioso", flush=True)
                    last_warn_at = now
                    warned_75 = True

            # Checkpoint pre-compact a 97%; ejecuta /compact sin prompts visibles.
            elif pct >= COMPACT_PCT and not compacted_88:
                print(f"[compact-monitor] contexto al {pct:.1f}% — /compact silencioso", flush=True)
                now = time.time()
                if now - last_compact_at < COMPACT_COOLDOWN_SECONDS:
                    print("[compact-monitor] compact omitido por cooldown", flush=True)
                elif not codex_is_busy(session_path):
                    store_auto_checkpoint(pct, session_path, "compact_threshold")
                    if INJECT_TMUX_PROMPTS and tmux_available:
                        inject(FORCE_COMPACT_PROMPT)
                    elif tmux_available:
                        inject(FORCE_COMPACT_PROMPT, silent=True)
                        print("[compact-monitor] /compact enviado sin prompt de recuperación", flush=True)
                    else:
                        print("[compact-monitor] /compact omitido: tmux no disponible", flush=True)
                    last_compact_at = now
                    compacted_88 = True

            # Reset advertencias si baja del threshold
            if pct < (WARN_PCT - 10):
                warned_75 = False
                compacted_88 = False

            last_pct_by_session[session_path] = pct

    finally:
        PID_FILE.unlink(missing_ok=True)
        print("[compact-monitor] terminado", flush=True)


if __name__ == "__main__":
    main()
