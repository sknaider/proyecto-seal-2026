#!/usr/bin/env python3
"""SEAL agent stability guard.

Keeps the agent runtime from drifting silently:
- one ws_listener per monitored agent;
- fresh heartbeat files with live Claude PIDs;
- critical per-agent systemd services/timers active;
- ADA Codex MCP memory_store identity still resolving as ADA, not external.
- the shared autonomous-completion contract still present in files and SOUL DB.

The guard is intentionally conservative. It only restarts missing/inactive
services and ws listeners, and it posts to web_chat only when the state changes
or when it had to fix something.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    import asyncpg
except Exception:  # pragma: no cover - guard must degrade if venv changes.
    asyncpg = None


ROOT = Path("/home/dadito/IA/proyecto-seal")
MESSAGES = ROOT / "messages"
PYTHON = Path("/home/dadito/IA/seal-spark/.venv/bin/python3")
WS_LISTENER = MESSAGES / "ws_listener.py"
STABILITY_DB_SECRET = Path.home() / ".config/seal/stability_guard.dsn"
SDK_DB_SECRET = Path.home() / ".config/seal/soul_memory_sdk.env"
AUTH_GUARD_STATE = Path.home() / ".local/state/seal/claude-auth-guard/state.json"
AUTH_GUARD_MAX_AGE_SECONDS = 15 * 60
PROJECT_MCP_CONFIG = ROOT / ".mcp.json"
GLOBAL_MCP_CONFIG = Path.home() / ".claude/.mcp.json"
POSTGRES_MCP_SECRET = Path.home() / ".config/seal/mcp_postgres_observer.env"
STUDIO_DB_SECRET = Path.home() / ".config/seal/seal_studio_db.env"
STUDIO_DB_SOURCES = (
    ROOT / "seal-studio/backend/main.py",
    ROOT / "seal-studio/backend/mcp_gateway.py",
    ROOT / "seal-studio/backend/studio_db.py",
)


def read_private_dsn(
    path: Path,
    expected_user: str,
    *,
    env_key: str | None = None,
) -> str:
    """Read a 0600 DSN and fail closed on principal drift."""
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        raise RuntimeError(f"{path} mode={mode:o}, esperado=600")
    raw = path.read_text(encoding="utf-8").strip()
    if env_key is not None:
        prefix = env_key + "="
        matches = [line[len(prefix):].strip().strip('"').strip("'") for line in raw.splitlines() if line.startswith(prefix)]
        if len(matches) != 1:
            raise RuntimeError(f"{path} no contiene exactamente una entrada {env_key}")
        raw = matches[0]
    if (urlsplit(raw).username or "").lower() != expected_user.lower():
        raise RuntimeError(f"{path} principal inesperado")
    return raw

AUTONOMY_REQUIRED_CLAUSES = (
    "RECEIVED -> EXECUTING -> TESTING -> VERIFIED -> COMPLETED",
    "No volver a pedirla",
    "no cerrar en propuesta",
)
AUTONOMY_LEGACY_PHRASES = (
    "ADA puede sugerir pero no decidir sin aprobación",
    "Propose and consult before acting, as William has corrected this and it is valid.",
)
AUTONOMY_RULE_IDS = (42, 73)

WS_AGENTS = ("ALICE", "FABLE", "JARVIS", "NEXUS")
MONITOR_AGENTS = WS_AGENTS
HEARTBEAT_AGENTS = ("ADA", "ALICE", "FABLE", "JARVIS", "NEXUS")
HEARTBEAT_MAX_AGE_SECONDS = 15 * 60
MONITOR_REARM_COOLDOWN_SECONDS = 10 * 60

CRITICAL_UNITS = (
    "seal-chat.service",
    "seal-mcp-server.service",
    "seal-studio-backend.service",
    "ada-codex-remote-bridge.service",
    "ada-codex-compact-monitor.service",
    "seal-codex-app-bridge.service",
    "seal-codex-app-bridge-poller.service",
    "seal-bridge-alice.service",
    "seal-bridge-jarvis.service",
    "seal-bridge-nexus.service",
    "seal-channel-monitor@ADA.service",
    "seal-channel-monitor@ALICE.service",
    "seal-channel-monitor@FABLE.service",
    "seal-channel-monitor@JARVIS.service",
    "seal-channel-monitor@NEXUS.service",
    "seal-dm-monitor@ALICE.service",
    "seal-dm-monitor@FABLE.service",
    "seal-dm-monitor@JARVIS.service",
    "seal-dm-monitor@NEXUS.service",
    "seal-alice-dm-poller.service",
    "seal-fable-dm-poller.service",
    "seal-jarvis-dm-poller.service",
    "seal-nexus-dm-poller.service",
    "seal-whisper-ADA.service",
    "seal-whisper-ALICE.service",
    "seal-whisper-JARVIS.service",
    "seal-whisper-NEXUS.service",
    "seal-ada-heartbeat.timer",
    "seal-alice-heartbeat.timer",
    "seal-fable-heartbeat.timer",
    "seal-jarvis-heartbeat.timer",
    "seal-nexus-heartbeat.timer",
    "seal-claude-auth-guard.timer",
    "seal-ssai-dual-verify.timer",
)

CRITICAL_ONESHOTS = ("seal-ssai-dual-verify.service",)

LAST_REPORT = MESSAGES / "seal_agent_stability_guard_last.json"
STATE_PATH = MESSAGES / "seal_agent_stability_guard_state.json"
LOG_PATH = MESSAGES / "seal_agent_stability_guard.log"


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    comm: str
    args: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def append_log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{utc_now()} {line}\n")


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ps_rows() -> list[Proc]:
    result = run(["ps", "-eo", "pid=,ppid=,comm=,args="])
    rows: list[Proc] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            rows.append(Proc(int(parts[0]), int(parts[1]), parts[2], parts[3]))
        except ValueError:
            continue
    return rows


def ws_pidfile(agent: str) -> Path:
    return MESSAGES / f".ws_listener_{agent.lower()}.pid"


def read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    return value if value > 0 else None


def write_pid(path: Path, pid: int) -> None:
    path.write_text(f"{pid}\n", encoding="utf-8")


def ws_processes(agent: str, rows: list[Proc]) -> list[Proc]:
    needle = f"{WS_LISTENER} --agent {agent}"
    alt_needle = f"ws_listener.py --agent {agent}"
    return [
        proc
        for proc in rows
        if proc.comm.startswith("python")
        and (needle in proc.args or alt_needle in proc.args)
        and proc.pid != os.getpid()
    ]


def monitor_processes(agent: str, rows: list[Proc]) -> list[Proc]:
    """Return the real in-session channel consumers, not producers/bridges.

    A live Claude PID and a healthy ws_listener are insufficient: the incident
    on 18-Jul left NEXUS alive while no ``tail -F seal_events_NEXUS.log`` was
    attached to its session.  Match the executable and exact event path so the
    path embedded in a launcher's prompt cannot create a false positive.
    """
    event_path = f"/tmp/seal_events_{agent}.log"
    return [
        proc
        for proc in rows
        if proc.comm == "tail" and event_path in proc.args and "-F" in proc.args
    ]


def runtime_processes(agent: str, rows: list[Proc]) -> list[Proc]:
    """Return the primary Claude runtime processes for one sibling.

    ADA intentionally has a dual Codex topology (visible TUI plus headless app
    server), so runtime singleton enforcement applies only to Claude siblings.
    Read ``SEAL_AGENT`` from /proc instead of trusting command-line names.
    """
    if agent == "ADA":
        return []
    matches: list[Proc] = []
    for proc in rows:
        if proc.comm != "claude":
            continue
        # Programmatic Claude workers inherit SEAL_AGENT from their launcher,
        # but they are not interactive sibling runtimes.  Counting short-lived
        # stream-json reviewers as principals produced recurring false duplicate
        # alarms and could tempt an unsafe kill of the real agent session.
        if "--output-format stream-json" in proc.args:
            continue
        if not re.search(
            rf"(?:^|\s)--name(?:=|\s+)[\"']?{re.escape(agent)}(?:\s|—|-|$)",
            proc.args,
            flags=re.IGNORECASE,
        ):
            continue
        try:
            env_raw = Path(f"/proc/{proc.pid}/environ").read_bytes().decode(
                "utf-8", errors="replace"
            )
            env = dict(part.split("=", 1) for part in env_raw.split("\0") if "=" in part)
        except Exception:
            continue
        if env.get("SEAL_AGENT", "").strip().upper() == agent:
            matches.append(proc)
    return matches


def ensure_monitor(agent: str, rows: list[Proc]) -> dict[str, Any]:
    procs = monitor_processes(agent, rows)
    if len(procs) == 1:
        return {
            "agent": agent,
            "ok": True,
            "count": len(procs),
            "pids": [proc.pid for proc in procs],
            "fixes": [],
            "issues": [],
        }
    if len(procs) > 1:
        return {
            "agent": agent,
            "ok": False,
            "count": len(procs),
            "pids": [proc.pid for proc in procs],
            "fixes": [],
            "issues": [
                f"{agent}: {len(procs)} consumidores de monitor; se exige exactamente uno"
            ],
        }

    marker = Path(f"/tmp/seal_monitor_rearm_{agent}.ts")
    now = time.time()
    if marker.exists() and now - marker.stat().st_mtime < MONITOR_REARM_COOLDOWN_SECONDS:
        age = int(now - marker.stat().st_mtime)
        return {
            "agent": agent,
            "ok": False,
            "count": 0,
            "pids": [],
            "fixes": [],
            "issues": [f"{agent}: monitor consumidor ausente; rearme solicitado hace {age}s"],
        }

    socket_name = f"seal-{agent.lower()}"
    session_name = socket_name
    has_session = run(
        ["tmux", "-L", socket_name, "has-session", "-t", session_name], timeout=5
    )
    if has_session.returncode != 0:
        return {
            "agent": agent,
            "ok": False,
            "count": 0,
            "pids": [],
            "fixes": [],
            "issues": [f"{agent}: monitor consumidor ausente y tmux {socket_name} no disponible"],
        }

    message = (
        f"[STABILITY-GUARD] {agent} está vivo pero sin consumidor del canal. "
        f"Rearma AHORA un Monitor persistente para: tail -n 0 -F "
        f"/tmp/seal_events_{agent}.log (persistent=true, timeout_ms=3600000). "
        "No publiques un cierre hasta verificar que el proceso tail quedó vivo."
    )
    sent = run(
        ["tmux", "-L", socket_name, "send-keys", "-t", session_name, "-l", message],
        timeout=5,
    )
    if sent.returncode == 0:
        run(["tmux", "-L", socket_name, "send-keys", "-t", session_name, "Enter"], timeout=5)
        marker.write_text(f"{int(now)}\n", encoding="utf-8")
        return {
            "agent": agent,
            "ok": False,
            "count": 0,
            "pids": [],
            "fixes": [f"{agent}: monitor ausente; rearme inyectado en tmux"],
            "issues": [],
        }
    return {
        "agent": agent,
        "ok": False,
        "count": 0,
        "pids": [],
        "fixes": [],
        "issues": [f"{agent}: monitor ausente y falló inyección de rearme: {sent.stderr.strip()}"],
    }


def terminate_pid(pid: int) -> bool:
    if not process_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return not process_alive(pid)


def start_ws_listener(agent: str) -> int | None:
    if not PYTHON.exists() or not WS_LISTENER.exists():
        return None
    log_path = MESSAGES / f"ws_listener_{agent}.log"
    log_fh = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [str(PYTHON), str(WS_LISTENER), "--agent", agent],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
        text=True,
    )
    time.sleep(0.5)
    return proc.pid if process_alive(proc.pid) else None


def ensure_ws(agent: str, rows: list[Proc]) -> dict[str, Any]:
    fixes: list[str] = []
    issues: list[str] = []
    procs = ws_processes(agent, rows)
    pidfile = ws_pidfile(agent)
    canonical = read_pid(pidfile)
    live_pids = {proc.pid for proc in procs}

    if len(procs) == 0:
        new_pid = start_ws_listener(agent)
        if new_pid:
            fixes.append(f"{agent}: ws_listener iniciado pid={new_pid}")
            write_pid(pidfile, new_pid)
        else:
            issues.append(f"{agent}: ws_listener ausente y no pudo arrancar")
        return {"agent": agent, "count": len(procs), "fixes": fixes, "issues": issues}

    keep_pid = canonical if canonical in live_pids else min(live_pids)
    if len(procs) > 1:
        killed: list[int] = []
        failed: list[int] = []
        for proc in procs:
            if proc.pid == keep_pid:
                continue
            if terminate_pid(proc.pid):
                killed.append(proc.pid)
            else:
                failed.append(proc.pid)
        if killed:
            fixes.append(f"{agent}: ws_listener duplicados terminados={killed}, preservado={keep_pid}")
        if failed:
            issues.append(f"{agent}: no pude terminar duplicados={failed}")

    if canonical != keep_pid:
        write_pid(pidfile, keep_pid)
        fixes.append(f"{agent}: pidfile normalizado {canonical}->{keep_pid}")

    return {"agent": agent, "count": len(procs), "pid": keep_pid, "fixes": fixes, "issues": issues}


def heartbeat_path(agent: str) -> Path:
    return MESSAGES / f"{agent.lower()}_claude_heartbeat.json"


def check_heartbeat(agent: str, rows: list[Proc]) -> dict[str, Any]:
    path = heartbeat_path(agent)
    issues: list[str] = []
    data: dict[str, Any] = {}
    if not path.exists():
        return {"agent": agent, "ok": False, "issues": [f"{agent}: heartbeat JSON faltante"]}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"agent": agent, "ok": False, "issues": [f"{agent}: heartbeat JSON inválido: {exc}"]}
    age = int(time.time() - path.stat().st_mtime)
    if age > HEARTBEAT_MAX_AGE_SECONDS:
        issues.append(f"{agent}: heartbeat stale age={age}s")
    if data.get("alive") is False:
        issues.append(f"{agent}: heartbeat alive=false")
    pid_raw = data.get("process_pid") or data.get("pid")
    try:
        pid = int(pid_raw)
    except Exception:
        pid = 0
    if not pid:
        issues.append(f"{agent}: heartbeat sin PID de runtime")
    elif not process_alive(pid):
        issues.append(f"{agent}: heartbeat apunta a PID muerto {pid}")
    else:
        try:
            comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
            env_raw = Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", errors="replace")
            env = dict(part.split("=", 1) for part in env_raw.split("\0") if "=" in part)
            runtime_agent = env.get("SEAL_AGENT", "").strip().upper()
            if runtime_agent != agent:
                issues.append(
                    f"{agent}: PID {pid} pertenece a SEAL_AGENT={runtime_agent or 'ausente'}"
                )
            if comm not in {"claude", "codex", "node"}:
                issues.append(f"{agent}: PID {pid} runtime inesperado comm={comm}")
        except Exception as exc:
            issues.append(f"{agent}: no pude verificar identidad del PID {pid}: {exc}")
    runtimes = runtime_processes(agent, rows)
    runtime_pids = [proc.pid for proc in runtimes]
    if agent != "ADA":
        if len(runtimes) != 1:
            issues.append(
                f"{agent}: runtimes Claude={len(runtimes)} pids={runtime_pids}; se exige exactamente uno"
            )
        elif pid and runtime_pids[0] != pid:
            issues.append(
                f"{agent}: heartbeat PID={pid} no coincide con runtime único PID={runtime_pids[0]}"
            )
    return {
        "agent": agent,
        "ok": not issues,
        "age_seconds": age,
        "pid": pid or None,
        "runtime_pids": runtime_pids,
        "issues": issues,
    }


def unit_load_state(unit: str) -> str:
    result = run(["systemctl", "--user", "show", unit, "-p", "LoadState", "--value"], timeout=5)
    return result.stdout.strip() or "unknown"


def unit_active_state(unit: str) -> str:
    result = run(["systemctl", "--user", "is-active", unit], timeout=5)
    return result.stdout.strip() or "unknown"


def restart_unit(unit: str) -> tuple[bool, str]:
    result = run(["systemctl", "--user", "restart", unit], timeout=20)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, "restarted"


def ensure_units() -> dict[str, Any]:
    fixes: list[str] = []
    issues: list[str] = []
    skipped: list[str] = []
    states: dict[str, str] = {}
    for unit in CRITICAL_UNITS:
        load_state = unit_load_state(unit)
        if load_state in {"not-found", "masked"}:
            skipped.append(f"{unit}:{load_state}")
            continue
        active = unit_active_state(unit)
        states[unit] = active
        if active == "active":
            continue
        ok, detail = restart_unit(unit)
        active_after = unit_active_state(unit)
        states[unit] = active_after
        if ok and active_after == "active":
            fixes.append(f"{unit}: {active}->active")
        else:
            issues.append(f"{unit}: {active}->{active_after} ({detail})")
    return {"states": states, "fixes": fixes, "issues": issues, "skipped": skipped}


def check_oneshot_results(
    units: tuple[str, ...] = CRITICAL_ONESHOTS,
) -> dict[str, Any]:
    """Catch timers whose oneshot target exits non-zero while the timer stays green."""
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for unit in units:
        result = run(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "-p",
                "LoadState",
                "-p",
                "Result",
                "-p",
                "ExecMainStatus",
            ],
            timeout=5,
        )
        props = {}
        if result.returncode == 0:
            props = dict(
                line.split("=", 1)
                for line in result.stdout.splitlines()
                if "=" in line
            )
        load = props.get("LoadState", "unknown")
        service_result = props.get("Result", "unknown")
        exec_status = props.get("ExecMainStatus", "unknown")
        ok = load == "loaded" and service_result == "success" and exec_status == "0"
        row = {
            "unit": unit,
            "ok": ok,
            "load_state": load,
            "result": service_result,
            "exec_status": exec_status,
        }
        rows.append(row)
        if not ok:
            issues.append(
                f"{unit}: load={load} result={service_result} exec_status={exec_status}"
            )
    return {"ok": not issues, "units": rows, "issues": issues}


def check_auth_guard_status(path: Path = AUTH_GUARD_STATE, *, now: float | None = None) -> dict[str, Any]:
    """Consume the auth guard result so liveness can never mask OAuth failure."""
    now = time.time() if now is None else now
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("state is not an object")
    except Exception as exc:
        return {
            "ok": False,
            "status": "missing",
            "issues": [f"auth guard ausente o inválido: {exc}"],
        }

    checked_at = int(payload.get("checked_at") or 0)
    age = max(0, int(now - checked_at)) if checked_at else AUTH_GUARD_MAX_AGE_SECONDS + 1
    status = str(payload.get("status") or "unknown")
    issues: list[str] = []
    if age > AUTH_GUARD_MAX_AGE_SECONDS:
        issues.append(f"auth guard stale age={age}s")
    if status == "needs_login":
        issues.append("autenticación Claude no disponible: se requiere login")
    elif status == "unknown":
        issues.append("healthcheck de autenticación Claude no disponible")
    elif status not in {"healthy", "suspect"}:
        issues.append(f"auth guard devolvió estado inesperado={status}")
    return {
        "ok": not issues,
        "status": status,
        "raw_status": payload.get("raw_status"),
        "age_seconds": age,
        "consecutive_failures": int(payload.get("consecutive_failures") or 0),
        "issues": issues,
    }


async def check_ada_memory_identity() -> dict[str, Any]:
    if asyncpg is None:
        return {"ok": False, "issues": ["asyncpg no disponible; no pude verificar audit_log memory_store"]}
    conn = None
    try:
        dsn = read_private_dsn(STABILITY_DB_SECRET, "svc_soul_stability_guard")
        conn = await asyncpg.connect(dsn, timeout=5)
        row = await conn.fetchrow(
            """
            SELECT at, actor, action, decision
            FROM soul_v3.stability_guard_ada_memory_audit_v
            WHERE decision='allow'
            ORDER BY at DESC
            LIMIT 1
            """
        )
    except Exception as exc:
        return {"ok": False, "issues": [f"ADA memory_store audit no verificable: {exc}"]}
    finally:
        if conn is not None:
            await conn.close()
    if not row:
        return {"ok": False, "issues": ["ADA memory_store sin allow reciente en audit_log"]}
    created_at = row["at"]
    age = (datetime.now(timezone.utc) - created_at).total_seconds()
    if age > 24 * 3600:
        return {
            "ok": False,
            "last_allow": created_at.isoformat(),
            "issues": [f"ADA memory_store allow viejo age={int(age)}s"],
        }
    return {"ok": True, "last_allow": created_at.isoformat(), "age_seconds": int(age)}


def check_autonomy_files(root: Path = ROOT) -> dict[str, Any]:
    """Fail closed when the executable autonomy contract drifts on disk."""
    issues: list[str] = []
    paths = {
        "contract": root / "CLAUDE.md",
        "sender": root / "scripts/seal_send.py",
        "guard": root / "scripts/seal_autonomy_guard.py",
    }
    contents: dict[str, str] = {}
    for name, path in paths.items():
        try:
            contents[name] = path.read_text(encoding="utf-8")
        except Exception as exc:
            issues.append(f"autonomía: {name} ausente o ilegible: {exc}")

    contract = contents.get("contract", "")
    normalized_contract = " ".join(contract.split())
    for clause in AUTONOMY_REQUIRED_CLAUSES:
        if clause not in normalized_contract:
            issues.append(f"autonomía: CLAUDE.md perdió cláusula obligatoria: {clause}")
    for phrase in AUTONOMY_LEGACY_PHRASES:
        if phrase in normalized_contract:
            issues.append(f"autonomía: CLAUDE.md reintrodujo regla legacy: {phrase}")

    sender = contents.get("sender", "")
    if "AUTONOMY BLOCKED" not in sender or "approval_gate" not in sender:
        issues.append("autonomía: seal_send.py no aplica el bloqueo de aprobación redundante")
    guard = contents.get("guard", "")
    if "def autonomy_warning" not in guard:
        issues.append("autonomía: seal_autonomy_guard.py no expone el detector esperado")
    return {"ok": not issues, "status": "healthy" if not issues else "drift", "issues": issues}


async def check_autonomy_governance() -> dict[str, Any]:
    """Verify persistent rules/identity so reboot and compact cannot restore passivity."""
    if asyncpg is None:
        return {"ok": False, "status": "unverifiable", "issues": ["autonomía: asyncpg no disponible"]}
    conn = None
    try:
        dsn = read_private_dsn(STABILITY_DB_SECRET, "svc_soul_stability_guard")
        conn = await asyncpg.connect(dsn, timeout=5)
        rows = await conn.fetch(
            """
            SELECT id, content
            FROM soul_v3.stability_guard_autonomy_rules_v
            WHERE id = ANY($1::bigint[]) AND active IS TRUE
            """,
            list(AUTONOMY_RULE_IDS),
        )
        identity = await conn.fetchval(
            "SELECT boot_context FROM soul_v3.stability_guard_jarvis_identity_v"
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "unverifiable",
            "issues": [f"autonomía: gobierno SOUL DB no verificable: {exc}"],
        }
    finally:
        if conn is not None:
            await conn.close()

    by_id = {int(row["id"]): str(row["content"] or "") for row in rows}
    issues: list[str] = []
    if set(by_id) != set(AUTONOMY_RULE_IDS):
        issues.append(f"autonomía: reglas activas esperadas={list(AUTONOMY_RULE_IDS)} presentes={sorted(by_id)}")
    if "owner ejecuta" not in by_id.get(42, ""):
        issues.append("autonomía: regla 42 perdió ejecución autónoma del owner")
    if "decide y ejecuta autónomamente" not in by_id.get(73, ""):
        issues.append("autonomía: regla 73 perdió autonomía dentro del scope")
    joined = "\n".join([*by_id.values(), str(identity or "")])
    for phrase in AUTONOMY_LEGACY_PHRASES:
        if phrase in joined:
            issues.append(f"autonomía: SOUL DB reintrodujo regla legacy: {phrase}")
    if not identity:
        issues.append("autonomía: identidad JARVIS ausente")
    return {"ok": not issues, "status": "healthy" if not issues else "drift", "issues": issues}


async def check_autonomy_contract(root: Path = ROOT) -> dict[str, Any]:
    files = check_autonomy_files(root)
    governance = await check_autonomy_governance()
    issues = [*files.get("issues", []), *governance.get("issues", [])]
    return {
        "ok": not issues,
        "status": "healthy" if not issues else "drift",
        "files": files,
        "governance": governance,
        "issues": issues,
    }


def check_mcp_postgres_boundary(
    configs: tuple[Path, ...] = (PROJECT_MCP_CONFIG,),
    secret_path: Path = POSTGRES_MCP_SECRET,
    scan_configs: tuple[Path, ...] = (GLOBAL_MCP_CONFIG,),
) -> dict[str, Any]:
    """Detect drift in the active PostgreSQL MCP and legacy config shadows.

    ``configs`` are authoritative manifests and must expose ``postgres``.
    ``scan_configs`` are optional compatibility manifests: they may omit the
    server entirely, but if they define it the same credential rules apply.
    """
    issues: list[str] = []
    expected_source = "mcp_postgres_observer.env"

    def inspect_entry(path: Path, entry: Any) -> None:
        blob = json.dumps(entry, ensure_ascii=False)
        if "postgresql://" in blob:
            issues.append(f"postgres MCP: {path} volvió a embeber una DSN")
        if expected_source not in blob:
            issues.append(f"postgres MCP: {path} no carga la credencial observer segura")

    for path in configs:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            entry = payload["mcpServers"]["postgres"]
        except Exception as exc:
            issues.append(f"postgres MCP: config {path} ausente o inválida: {exc}")
            continue
        inspect_entry(path, entry)

    for path in scan_configs:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except Exception as exc:
            issues.append(f"postgres MCP: config auxiliar {path} inválida: {exc}")
            continue
        entry = payload.get("mcpServers", {}).get("postgres")
        if entry is not None:
            inspect_entry(path, entry)

    try:
        mode = secret_path.stat().st_mode & 0o777
        raw = secret_path.read_text(encoding="utf-8")
    except Exception as exc:
        issues.append(f"postgres MCP: credencial observer ausente o ilegible: {exc}")
    else:
        if mode != 0o600:
            issues.append(f"postgres MCP: credencial observer mode={mode:o}, esperado=600")
        if "postgresql://mcp_observer:" not in raw:
            issues.append("postgres MCP: credencial no autentica como mcp_observer")
    return {"ok": not issues, "status": "healthy" if not issues else "drift", "issues": issues}


async def check_studio_db_boundary(
    secret_path: Path = STUDIO_DB_SECRET,
    sources: tuple[Path, ...] = STUDIO_DB_SOURCES,
) -> dict[str, Any]:
    """Detect privilege/config drift in the live SEAL Studio database login."""
    issues: list[str] = []
    dsn = ""
    try:
        mode = secret_path.stat().st_mode & 0o777
        for raw in secret_path.read_text(encoding="utf-8").splitlines():
            if raw.startswith("SEAL_STUDIO_DB_DSN="):
                dsn = raw.split("=", 1)[1].strip().strip('"').strip("'")
                break
    except Exception as exc:
        issues.append(f"studio DB: credencial ausente o ilegible: {exc}")
    else:
        if mode != 0o600:
            issues.append(f"studio DB: credencial mode={mode:o}, esperado=600")
        if not dsn.startswith("postgresql://svc_seal_studio:"):
            issues.append("studio DB: DSN no autentica como svc_seal_studio")

    for path in sources:
        try:
            source = path.read_text(encoding="utf-8")
        except Exception as exc:
            issues.append(f"studio DB: fuente {path} ausente o ilegible: {exc}")
            continue
        if "postgresql://seal:" in source or "seal_memory_2026" in source:
            issues.append(f"studio DB: fuente {path} reintrodujo fallback superusuario")

    if asyncpg is None:
        issues.append("studio DB: asyncpg no disponible")
    elif dsn:
        try:
            conn = await asyncpg.connect(dsn, timeout=5)
            try:
                identity = await conn.fetchrow(
                    "SELECT current_user,session_user,current_setting('application_name') app"
                )
                attrs = await conn.fetchrow(
                    "SELECT rolsuper,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=current_user"
                )
                private_chat = await conn.fetchval(
                    "SELECT 1 FROM soul_v3.chat_messages "
                    "WHERE lower(channel) LIKE 'dm:%' OR lower(channel) LIKE 'user:%' LIMIT 1"
                )
                private_memory = await conn.fetchval(
                    "SELECT 1 FROM soul_v3.memories "
                    "WHERE COALESCE(scope,'private') NOT IN ('team','shared','public') LIMIT 1"
                )
                unlisted = await conn.fetchval(
                    "SELECT has_table_privilege(current_user,'soul_v3.chat_sessions','SELECT')"
                )
                delete_allowed = await conn.fetchval(
                    "SELECT has_table_privilege(current_user,'soul_v3.llm_routing','DELETE')"
                )
            finally:
                await conn.close()
            if identity["current_user"] != "svc_seal_studio" or identity["session_user"] != "svc_seal_studio":
                issues.append("studio DB: identidad viva distinta de svc_seal_studio")
            if identity["app"] != "seal_studio_backend":
                issues.append("studio DB: application_name vivo incorrecto")
            if attrs is None or attrs["rolsuper"] or attrs["rolbypassrls"] or attrs["rolinherit"]:
                issues.append("studio DB: atributos de rol dejaron de ser mínimos")
            if private_chat is not None or private_memory is not None:
                issues.append("studio DB: RLS expone filas privadas")
            if unlisted or delete_allowed:
                issues.append("studio DB: privilegio efectivo fuera de allowlist")
        except Exception as exc:
            issues.append(f"studio DB: canario de identidad falló: {type(exc).__name__}: {exc}")

    return {"ok": not issues, "status": "healthy" if not issues else "drift", "issues": issues}


async def check_sdk_db_boundary(
    sdk_secret: Path = SDK_DB_SECRET,
    guard_secret: Path = STABILITY_DB_SECRET,
) -> dict[str, Any]:
    """Verify the Memory SDK stays least-privilege and tenant scoped."""
    issues: list[str] = []
    if asyncpg is None:
        return {"ok": False, "status": "unverifiable", "issues": ["SDK DB: asyncpg no disponible"]}

    sdk_conn = None
    guard_conn = None
    try:
        sdk_dsn = read_private_dsn(
            sdk_secret,
            "svc_soul_memory_sdk",
            env_key="SEAL_DB_URL",
        )
        guard_dsn = read_private_dsn(guard_secret, "svc_soul_stability_guard")
        sdk_conn = await asyncpg.connect(sdk_dsn, timeout=5)
        guard_conn = await asyncpg.connect(guard_dsn, timeout=5)

        identity = await sdk_conn.fetchrow("SELECT session_user,current_user")
        attrs = await sdk_conn.fetchrow(
            "SELECT rolsuper,rolbypassrls,rolinherit "
            "FROM pg_roles WHERE rolname=session_user"
        )
        if identity is None or identity["session_user"] != "svc_soul_memory_sdk":
            issues.append("SDK DB: session_user vivo distinto de svc_soul_memory_sdk")
        if identity is None or identity["current_user"] != "svc_soul_memory_sdk":
            issues.append("SDK DB: current_user inicial inesperado")
        if attrs is None or attrs["rolsuper"] or attrs["rolbypassrls"] or attrs["rolinherit"]:
            issues.append("SDK DB: atributos del login dejaron de ser mínimos")

        async with sdk_conn.transaction():
            await sdk_conn.execute("SET LOCAL ROLE soul_sdk_agent_api")
            await sdk_conn.execute("SELECT set_config('app.agent','ADA',true)")
            await sdk_conn.execute("SELECT set_config('app.tenant','default',true)")
            foreign_private = await sdk_conn.fetchval(
                "SELECT count(*) FROM soul_v3.memories "
                "WHERE agent <> 'ADA' AND COALESCE(scope,'private')='private'"
            )
            if int(foreign_private or 0) != 0:
                issues.append("SDK DB: el rol agent_api expone memorias privadas ajenas")

            unrelated_denied = False
            try:
                await sdk_conn.fetchval("SELECT count(*) FROM soul_v3.agent_tasks")
            except asyncpg.InsufficientPrivilegeError:
                unrelated_denied = True
            if not unrelated_denied:
                issues.append("SDK DB: agent_api conserva SELECT fuera de su allowlist")

        role_escape_denied = False
        try:
            await sdk_conn.execute("SET ROLE soul_sdk_runtime")
        except asyncpg.InsufficientPrivilegeError:
            role_escape_denied = True
        finally:
            await sdk_conn.execute("RESET ROLE")
        if not role_escape_denied:
            issues.append("SDK DB: SET ROLE soul_sdk_runtime no fue denegado")

        superuser_clients = await guard_conn.fetchval(
            "SELECT client_count FROM soul_v3.stability_guard_superuser_clients_v"
        )
        if int(superuser_clients or 0) != 0:
            issues.append(f"SDK DB: clientes de aplicación superusuario vivos={superuser_clients}")
    except Exception as exc:
        issues.append(f"SDK DB: canario de frontera falló: {type(exc).__name__}: {exc}")
    finally:
        if sdk_conn is not None:
            await sdk_conn.close()
        if guard_conn is not None:
            await guard_conn.close()

    return {"ok": not issues, "status": "healthy" if not issues else "drift", "issues": issues}


def post_webchat(message: str, idempotency_key: str) -> bool:
    completed = run(
        [
            str(ROOT / "scripts/seal_send.py"),
            "ADA",
            "equipo",
            message,
            "--channel",
            "web_chat",
            "--type",
            "status",
            "--proactive",
            "--idempotency-key",
            idempotency_key,
        ],
        timeout=15,
    )
    if completed.returncode == 0:
        return True
    diagnostic = (completed.stderr or completed.stdout).strip().splitlines()
    append_log(f"webchat_post_failed {diagnostic[-1][:300] if diagnostic else 'unknown'}")
    return False


def stable_hash(report: dict[str, Any]) -> str:
    # Historical/minimal reports used by tests and recovery tooling predate the
    # Studio boundary.  Missing optional sections must hash as "unknown", not
    # crash the guard before it can publish the actual health report.
    studio_boundary = report.get("studio_db_boundary", {})
    sdk_boundary = report.get("sdk_db_boundary", {})
    oneshots = report.get("oneshot_results", {})
    relevant = {
        "status": report["status"],
        "issues": report["issues"],
        "fixes": report["fixes"],
        "ws": {item["agent"]: item.get("pid") for item in report["ws"]},
        "auth_guard": {
            "status": report["auth_guard"].get("status"),
            "issues": report["auth_guard"].get("issues", []),
        },
        "autonomy_contract": {
            "status": report["autonomy_contract"].get("status"),
            "issues": report["autonomy_contract"].get("issues", []),
        },
        "mcp_postgres_boundary": {
            "status": report["mcp_postgres_boundary"].get("status"),
            "issues": report["mcp_postgres_boundary"].get("issues", []),
        },
        "studio_db_boundary": {
            "status": studio_boundary.get("status"),
            "issues": studio_boundary.get("issues", []),
        },
        "sdk_db_boundary": {
            "status": sdk_boundary.get("status"),
            "issues": sdk_boundary.get("issues", []),
        },
        "oneshot_results": {
            "ok": oneshots.get("ok"),
            "issues": oneshots.get("issues", []),
        },
    }
    blob = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def maybe_post(report: dict[str, Any], *, post_always: bool, post_on_change: bool) -> bool:
    if not post_always and not post_on_change:
        return False
    state: dict[str, Any] = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    digest = stable_hash(report)
    should_post = post_always or bool(report["fixes"]) or state.get("hash") != digest
    if post_on_change and not should_post:
        return False

    lines = [
        f"ADA Stability Guard — {report['status']}",
        f"ws={report['ws_ok']}/4 monitors={report.get('monitor_ok', 0)}/4 hb={report['heartbeat_ok']}/5 auth={report['auth_guard']['status']} autonomy={report['autonomy_contract']['status']} postgres_mcp={report['mcp_postgres_boundary']['status']} studio_db={report['studio_db_boundary']['status']} sdk_db={report['sdk_db_boundary']['status']} units_ok={report['units_ok']} fixes={len(report['fixes'])} issues={len(report['issues'])}",
    ]
    if report["fixes"]:
        lines.append("Fixes: " + "; ".join(report["fixes"][:6]))
    if report["issues"]:
        lines.append("Issues: " + "; ".join(report["issues"][:6]))
    sent = post_webchat("\n".join(lines), f"ada-stability-{digest}")
    STATE_PATH.write_text(
        json.dumps({"hash": digest, "last_post": utc_now(), "sent": sent}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return sent


async def build_report() -> dict[str, Any]:
    rows = ps_rows()
    ws_results = [ensure_ws(agent, rows) for agent in WS_AGENTS]
    monitor_results = [ensure_monitor(agent, rows) for agent in MONITOR_AGENTS]
    # Re-read after possible process changes.
    rows_after = ps_rows()
    for item in ws_results:
        procs = ws_processes(item["agent"], rows_after)
        item["live_count_after"] = len(procs)
        item["live_pids_after"] = [proc.pid for proc in procs]

    heartbeat_results = [check_heartbeat(agent, rows_after) for agent in HEARTBEAT_AGENTS]
    unit_results = ensure_units()
    oneshot_results = check_oneshot_results()
    auth_guard = check_auth_guard_status()
    memory_identity = await check_ada_memory_identity()
    autonomy_contract = await check_autonomy_contract()
    mcp_postgres_boundary = check_mcp_postgres_boundary()
    studio_db_boundary = await check_studio_db_boundary()
    sdk_db_boundary = await check_sdk_db_boundary()

    fixes: list[str] = []
    issues: list[str] = []
    for item in ws_results:
        fixes.extend(item.get("fixes", []))
        issues.extend(item.get("issues", []))
        if item.get("live_count_after") != 1:
            issues.append(f"{item['agent']}: ws_listener count_after={item.get('live_count_after')}")
    for item in monitor_results:
        fixes.extend(item.get("fixes", []))
        issues.extend(item.get("issues", []))
    for item in heartbeat_results:
        issues.extend(item.get("issues", []))
    fixes.extend(unit_results["fixes"])
    issues.extend(unit_results["issues"])
    issues.extend(oneshot_results.get("issues", []))
    issues.extend(auth_guard.get("issues", []))
    issues.extend(memory_identity.get("issues", []))
    issues.extend(autonomy_contract.get("issues", []))
    issues.extend(mcp_postgres_boundary.get("issues", []))
    issues.extend(studio_db_boundary.get("issues", []))
    issues.extend(sdk_db_boundary.get("issues", []))

    ws_ok = sum(1 for item in ws_results if item.get("live_count_after") == 1 and not item.get("issues"))
    heartbeat_ok = sum(1 for item in heartbeat_results if item.get("ok"))
    monitor_ok = sum(1 for item in monitor_results if item.get("ok"))
    units_ok = not unit_results["issues"]
    status = "GREEN"
    if fixes:
        status = "YELLOW_FIXED"
    if issues:
        status = "RED" if any("no pude" in issue or "ausente" in issue for issue in issues) else "YELLOW"

    report = {
        "ts": utc_now(),
        "status": status,
        "ws_ok": ws_ok,
        "heartbeat_ok": heartbeat_ok,
        "monitor_ok": monitor_ok,
        "units_ok": units_ok,
        "ws": ws_results,
        "monitors": monitor_results,
        "heartbeats": heartbeat_results,
        "units": unit_results,
        "oneshot_results": oneshot_results,
        "auth_guard": auth_guard,
        "ada_memory_identity": memory_identity,
        "autonomy_contract": autonomy_contract,
        "mcp_postgres_boundary": mcp_postgres_boundary,
        "studio_db_boundary": studio_db_boundary,
        "sdk_db_boundary": sdk_db_boundary,
        "fixes": fixes,
        "issues": issues,
    }
    LAST_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    append_log(f"status={status} fixes={len(fixes)} issues={len(issues)}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="SEAL agent stability guard")
    parser.add_argument("--post-always", action="store_true")
    parser.add_argument("--post-on-change", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="write report file but do not print JSON")
    parser.add_argument("--strict", action="store_true", help="return non-zero on unresolved issues")
    args = parser.parse_args()

    report = asyncio.run(build_report())
    maybe_post(report, post_always=args.post_always, post_on_change=args.post_on_change)
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and report["issues"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
