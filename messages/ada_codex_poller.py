#!/usr/bin/env python3
"""ADA-Codex Webchat/DM Poller — inyecta mensajes dirigidos a ADA en la sesión Codex via tmux.

Problema: Codex TUI es interactivo — no tiene Monitor tool como Claude Code.
Solución: pollear DB cada 3s → filtrar DM ADA y web_chat con llamada explícita → inyectar via tmux send-keys.
Mejora ALICE: busy-detection via session JSONL antes de inyectar (evita corrupción).

Uso: python3 ada_codex_poller.py [--tmux-session seal-ada-codex]
Guard: usa /tmp/ada_codex_poller.pid para evitar duplicados.
"""
import asyncio
import asyncpg
import fcntl
import json
import os
import subprocess
import sys
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from glob import glob

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory.operational_db_credentials import service_pg_dsn
try:
    from messages.codex_session_selector import newest_primary_tui_session
except ModuleNotFoundError:  # ejecución directa: sys.path apunta a messages/
    from codex_session_selector import newest_primary_tui_session

def resolve_db_dsn() -> str:
    """Load only ADA's dedicated bridge identity; never fall back to ``seal``."""
    return service_pg_dsn("SEAL_DB_DSN", expected_role="login_ada_bridge")


TMUX_SESSION = "seal-ada-codex"
SESSIONS_DIR = Path("/home/dadito/.codex/sessions")
STATE_DIR = Path(os.environ.get("ADA_CODEX_STATE_DIR", Path.home() / ".local/state/seal"))
STATE_FILE = STATE_DIR / "ada_codex_poller_last_ts.txt"
ACK_FILE = STATE_DIR / "ada_codex_poller_last_id.txt"
DM_ACK_FILE = STATE_DIR / "ada_codex_poller_dm_last_id.txt"
HUMAN_ACK_FILE = STATE_DIR / "ada_codex_poller_human_last_id.txt"
DISPATCH_CLAIM_DIR = STATE_DIR / "ada_codex_dispatch_claims"
HEADLESS_CURSOR_FILE = STATE_DIR / "ada_codex_remote_bridge_last_id.txt"
FAILED_SUBMISSION_FILE = STATE_DIR / "ada_codex_poller_failed_submit.json"
LEGACY_STATE_FILE = Path("/tmp/ada_codex_poller_last_ts.txt")
LEGACY_ACK_FILE = Path("/tmp/ada_codex_poller_last_id.txt")
LISTENER_HEALTH_FILE = STATE_DIR / "ada_codex_poller_health.json"
PID_FILE = Path("/tmp/ada_codex_poller.pid")
PUBLIC_EVENT_FILE = Path("/tmp/seal_events_ADA.log")
PUBLIC_EVENT_CURSOR = Path("/tmp/ada_codex_public_event_offset.txt")
BRIDGE_QUEUE_DIR = Path("/home/dadito/IA/proyecto-seal/messages/codex_app_bridge")
UPLOAD_ROOT = Path(__file__).resolve().parent / "uploads"
ACTIVE_TASK_FILE = BRIDGE_QUEUE_DIR / "active_task.json"
RESPONSES_DIR = BRIDGE_QUEUE_DIR / "responses"
PENDING_TASK_MAX_AGE_SECONDS = 15
SUBMISSION_LEDGER_WINDOW_SECONDS = 60
SUBMIT_CONFIRM_TIMEOUT_SECONDS = 3.0
TERMINAL_COMPLETION_TIMEOUT_SECONDS = int(
    os.environ.get("ADA_CODEX_TERMINAL_COMPLETION_TIMEOUT_SECONDS", "21600")
)
ORPHAN_ROUTE_GRACE_SECONDS = max(
    0.0, float(os.environ.get("ADA_CODEX_ORPHAN_ROUTE_GRACE_SECONDS", "3"))
)
# A claimed task must remain authoritative for the full completion window.
# Expiring it after ten minutes could let a restarted poller inject the same
# DM while Codex is still executing a long tool turn.
ACTIVE_TASK_MAX_AGE_SECONDS = TERMINAL_COMPLETION_TIMEOUT_SECONDS
COMPLETION_STATUSES = {"delivered", "published", "suppressed"}
POLL_INTERVAL = 3
HUMAN_SENDERS = {"william", "henry"}
TEAM_SENDERS = {"jarvis", "alice", "nexus", "dum"}
INJECT_FROM = HUMAN_SENDERS | TEAM_SENDERS
PUBLIC_TRIGGER_RE = re.compile(r"\bada\b", re.IGNORECASE)
ACK_ONLY_RE = re.compile(
    r"(?i)(?:qued[oó] reservado|ya estoy trabajando en (?:tu )?mensaje|"
    r"ada recibi[oó] tu mensaje|sigo trabajando en (?:tu )?mensaje|"
    r"terminando el turno activo|entro al turno codex ahora)"
)
INTERNAL_DM_SENDERS = {
    "ada", "ada-clone", "alice", "dum", "event_bus_daemon", "fable",
    "failover", "jarvis", "nexus", "seal_cron", "system",
}
WEB_CHANNEL = "web_chat"
# User-scoped chats where Henry/William explicitly authorized ADA to participate.
# These are not other agents' DMs; context and replies must stay on the same channel.
DEFAULT_USER_CHANNELS = ["user:3:gtl-sistemas", "user:3:tareas"]
# Max espera si Codex está ocupado (segundos)
BUSY_WAIT_MAX = 30
# Contexto previo: adaptativo y pequeño. El historial completo permanece en DB,
# pero no debe viajar en cada turno ni competir con el mensaje actual.
CONTEXT_RECENT_LIMIT = max(0, int(os.environ.get("ADA_CODEX_CONTEXT_LIMIT", "2")))
CONTEXT_MAX_CHARS = max(0, int(os.environ.get("ADA_CODEX_CONTEXT_MAX_CHARS", "600")))
CONTEXT_MODE = os.environ.get("ADA_CODEX_CONTEXT_MODE", "adaptive").strip().lower()
if CONTEXT_MODE not in {"adaptive", "always", "off"}:
    CONTEXT_MODE = "adaptive"
CONTEXT_REFERENCE_RE = re.compile(
    r"(?i)(?:\b(?:revisa|lee|mira)\s+(?:el\s+)?contexto\b|\bhilo\b|"
    r"\banterior(?:es)?\b|\bmensajes? hist[oó]ricos?\b|"
    r"\bqu[eé] opinas\b|\bc[oó]mo va\b|\bya est[aá]\b|\bcontin[uú]a\b|"
    r"\btermina\b|\btermin[ae]lo\b|\bes[oa]s?\b|\baquello\b)"
)
SENSITIVE_RE = re.compile(
    r"(?i)(client[_-]?secret|client[_-]?id|refresh[_-]?token|access[_-]?token|"
    r"api[_-]?key|password|passwd|authorization|bearer\s+[A-Za-z0-9._~+/-]+|"
    r"credentials?\.json|\.env\b|private[_-]?key)"
)


def is_ada_dm_channel(channel: str | None) -> bool:
    """Recognize only canonical three-part DMs where ADA is a participant."""

    parts = str(channel or "").lower().split(":")
    return len(parts) == 3 and parts[0] == "dm" and (
        parts[1] == "ada" or parts[2] == "ada"
    )


def ada_dm_peer(channel: str | None) -> str | None:
    if not is_ada_dm_channel(channel):
        return None
    _, left, right = str(channel).lower().split(":")
    return right if left == "ada" else left


def parse_csv_channels(raw: str | None, default: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Parse a comma-separated channel allowlist, preserving order without duplicates."""
    source = raw if raw is not None and raw.strip() else ",".join(default)
    channels: list[str] = []
    seen: set[str] = set()
    for item in source.split(","):
        channel = item.strip()
        if channel and channel not in seen:
            channels.append(channel)
            seen.add(channel)
    return channels


ADA_USER_CHANNELS = parse_csv_channels(
    os.environ.get("ADA_CODEX_USER_CHANNELS"),
    DEFAULT_USER_CHANNELS,
)


def discover_tmux_socket(session: str = TMUX_SESSION) -> str | None:
    tmux_env = os.environ.get("TMUX", "").split(",", 1)[0]
    candidates = []
    if tmux_env:
        candidates.append(tmux_env)
    uid = os.getuid()
    candidates.extend(sorted(glob(f"/tmp/tmux-{uid}/*")))
    candidates.extend(sorted(glob(f"/run/user/{uid}/tmux-{uid}/*")))
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not Path(candidate).is_socket():
            continue
        seen.add(candidate)
        result = subprocess.run(
            ["tmux", "-S", candidate, "has-session", "-t", session],
            capture_output=True,
        )
        if result.returncode == 0:
            return candidate
    return None


def tmux_base(session: str = TMUX_SESSION) -> list[str]:
    socket = discover_tmux_socket(session)
    if socket:
        return ["tmux", "-S", socket]
    return ["tmux"]


def resolve_codex_pane_target(session: str = TMUX_SESSION) -> str | None:
    """Return the concrete pane id that owns the Codex TUI.

    Targeting only ``session:window`` is unsafe when that window is split:
    tmux sends keys to whichever pane is active, which can be a read-only
    ``watch`` panel.  Fail closed unless a live, non-viewer pane is found.
    """
    try:
        result = subprocess.run(
            [
                *tmux_base(session),
                "list-panes",
                "-t",
                f"{session}:ADA[Codex]",
                "-F",
                "#{pane_id}\t#{pane_current_command}\t#{pane_dead}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    viewer_commands = {"watch", "tail", "less", "more"}
    candidates: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pane_id, command, dead = parts
        if dead == "0" and command not in viewer_commands and pane_id.startswith("%"):
            candidates.append(pane_id)
    return candidates[0] if candidates else None


def redact_sensitive(text: str) -> str:
    """Redact secret-shaped material before injecting context into Codex."""
    return SENSITIVE_RE.sub("[REDACTED]", text)


def is_sensitive_content(text: str) -> bool:
    return bool(SENSITIVE_RE.search(text or ""))


def already_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        return False


def tmux_session_alive(session: str) -> bool:
    result = subprocess.run(
        [*tmux_base(session), "has-session", "-t", session],
        capture_output=True
    )
    return result.returncode == 0


def find_active_session() -> Path | None:
    return newest_primary_tui_session(SESSIONS_DIR)


def latest_lifecycle_event(session: Path | None = None, max_bytes: int = 8 * 1024 * 1024) -> dict | None:
    """Return the newest real task_started/task_complete event.

    Reading a fixed 8 KiB tail was unsafe: a single large tool result could push
    task_started outside that window, making an active turn look idle.  Walk
    complete JSONL lines backwards instead and fail closed if no lifecycle event
    is found inside the bounded window.
    """
    session = session or find_active_session()
    if not session or not session.exists():
        return None
    try:
        size = session.stat().st_size
        start = max(0, size - max_bytes)
        with open(session, "rb") as handle:
            handle.seek(start)
            data = handle.read()
        lines = data.splitlines()
        if start and lines:
            lines = lines[1:]  # first line may be a partial JSON record
        for raw in reversed(lines):
            try:
                event = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if event.get("type") != "event_msg":
                continue
            payload = event.get("payload") or {}
            if payload.get("type") in {"task_started", "task_complete"}:
                return payload
    except Exception:
        return None
    return None


def codex_is_busy() -> bool:
    """Return True unless the latest validated lifecycle event is task_complete."""
    event = latest_lifecycle_event()
    return not event or event.get("type") != "task_complete"


async def wait_for_idle(timeout: int = BUSY_WAIT_MAX) -> bool:
    """Espera hasta que Codex esté idle. Devuelve True si logró idle, False si timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        write_listener_health("running")
        if not codex_is_busy() and not active_task_inflight():
            return True
        await asyncio.sleep(1.5)
    return False


def _accepted_submission_from_ledger(task: dict) -> dict | None:
    """Recover an accepted turn from the exact JSONL marker after a crash."""
    marker = str(task.get("turn_marker") or "").strip()
    session_value = str(task.get("submission_session_file") or "").strip()
    if not marker or not session_value:
        return None
    session_file = Path(session_value)
    try:
        start_offset = max(0, int(task.get("submission_start_offset", 0)))
        with session_file.open("rb") as handle:
            handle.seek(start_offset)
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except (OSError, TypeError, ValueError):
        return None
    try:
        submitted_at = datetime.fromisoformat(
            str(task.get("submission_started_at") or task.get("activated_at"))
            .replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None
    task_age = (datetime.now(timezone.utc) - submitted_at).total_seconds()
    turn_id: str | None = None
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        raw_event_at = event.get("timestamp")
        if raw_event_at:
            try:
                event_at = datetime.fromisoformat(
                    str(raw_event_at).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            elapsed = (event_at - submitted_at).total_seconds()
            if elapsed < -2 or elapsed > SUBMISSION_LEDGER_WINDOW_SECONDS:
                continue
        elif task_age > SUBMISSION_LEDGER_WINDOW_SECONDS:
            # Legacy/test ledgers may lack event timestamps, but an ancient
            # marker must never capture an unrelated future turn merely because
            # that future prompt quotes the old SEAL_TURN token.
            continue
        if event.get("type") != "event_msg":
            continue
        payload = event.get("payload") or {}
        if payload.get("type") == "task_started":
            turn_id = str(payload.get("turn_id") or "").strip() or None
        elif payload.get("type") == "user_message" and marker in str(payload.get("message") or ""):
            if turn_id:
                return {"turn_id": turn_id, "session_file": str(session_file)}
    return None


def reconcile_pending_submission(path: Path = ACTIVE_TASK_FILE) -> bool:
    """Promote a crash-window pending marker or keep it quarantined."""
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    if task.get("status") != "pending_submit" or not task.get("turn_marker"):
        return False
    accepted = _accepted_submission_from_ledger(task)
    if accepted:
        task.update({
            "status": "submitted",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "turn_id": accepted["turn_id"],
            "session_file": accepted["session_file"],
            "recovered_after_crash": True,
        })
        _write_active_task(task, path=path)
        return True
    try:
        started = datetime.fromisoformat(
            str(task.get("submission_started_at") or task.get("activated_at"))
            .replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        age = (datetime.now(timezone.utc) - started).total_seconds()
    except (TypeError, ValueError):
        age = 0
    if age <= SUBMISSION_LEDGER_WINDOW_SECONDS:
        # Keep the short crash ambiguity fail-closed while a delayed Enter can
        # still be accepted by the TUI.
        return True
    task.update({
        "status": "abandoned_unaccepted",
        "abandoned_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "abandon_reason": "no matching lifecycle event inside submit window",
    })
    _write_active_task(task, path=path)
    return False


def active_task_inflight(path: Path = ACTIVE_TASK_FILE) -> bool:
    """Prevent a second injection from overwriting the current reply routing.

    Submitted work is a durable ownership record, not a renewable lease.  It
    must never expire into a second execution merely because a response took
    longer than ten minutes or a process restarted.  Only an unsubmitted
    ``pending_submit`` marker has a bounded lifetime.
    """
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
        task_id = str(task.get("id") or "")
        activated = datetime.fromisoformat(str(task["activated_at"]).replace("Z", "+00:00"))
    except Exception:
        return False
    if task.get("source") not in {"ada_codex_poller", "ada_codex_poller_public_fallback"}:
        return False
    if task_id and load_completion_receipt(task_id) is not None:
        return False
    if task.get("status") == "pending_submit" and task.get("turn_marker"):
        return reconcile_pending_submission(path)
    age = (datetime.now(timezone.utc) - activated.astimezone(timezone.utc)).total_seconds()
    if task.get("status") == "pending_submit":
        return age <= PENDING_TASK_MAX_AGE_SECONDS
    # Status-less markers are from the pre-ledger bridge and represent an
    # accepted turn; preserve their ownership during rolling upgrades.
    return task.get("status") in {None, "active", "submitted", "completed"}


def load_completion_receipt(task_id: str, responses_dir: Path | None = None) -> dict | None:
    """Return only a terminal completion that is safe to acknowledge.

    Accepting a Codex turn is not delivery.  The stream relay writes this
    receipt only after the final was durably published, or after an explicit
    ``[SILENT]`` completion.  Old response artifacts without lifecycle fields
    deliberately fail closed.
    """
    directory = RESPONSES_DIR if responses_dir is None else responses_dir
    try:
        receipt = json.loads((directory / f"{task_id}.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    if receipt.get("id") != task_id or receipt.get("status") not in COMPLETION_STATUSES:
        return None
    if receipt["status"] in {"published", "delivered"}:
        if receipt.get("published") is not True or receipt.get("db_id") in {None, ""}:
            return None
    if receipt["status"] == "delivered" and receipt.get("delivered") is not True:
        return None
    return receipt


def active_route_owns_task(task_id: str, path: Path | None = None) -> bool:
    """Confirm that the terminal routing marker still owns this exact turn."""
    target = ACTIVE_TASK_FILE if path is None else path
    try:
        task = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    return (
        str(task.get("id") or "") == task_id
        and str(task.get("status") or "")
        not in {"abandoned", "abandoned_unaccepted", "orphaned"}
    )


async def wait_for_terminal_completion(
    task_id: str,
    timeout: float = TERMINAL_COMPLETION_TIMEOUT_SECONDS,
) -> dict | None:
    """Hold the source claim until the final publication is durably receipted."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    route_missing_since: float | None = None
    while loop.time() < deadline:
        receipt = load_completion_receipt(task_id)
        if receipt is not None:
            return receipt
        if active_route_owns_task(task_id):
            route_missing_since = None
        else:
            route_missing_since = route_missing_since or loop.time()
            if (
                ORPHAN_ROUTE_GRACE_SECONDS == 0
                or loop.time() - route_missing_since >= ORPHAN_ROUTE_GRACE_SECONDS
            ):
                print(
                    f"[ada-codex-poller] ruta huérfana para {task_id}; "
                    "liberando espera y reconsultando DB sin avanzar cursor",
                    flush=True,
                )
                return None
        write_listener_health("running")
        await asyncio.sleep(0.25)
    return None


def inject_message(session: str, text: str) -> bool:
    """Type one message and submit it once; acceptance is verified separately.

    A successful tmux return code only proves key delivery, not that the Codex
    TUI accepted the turn.  confirm_submission() is the commit acknowledgement.
    """
    import time
    # Limpieza: text como una sola línea sin newlines internos
    clean_text = text.replace("\n", " ").replace("\r", " ").strip()
    target = resolve_codex_pane_target(session)
    if not target:
        print("[ada-codex-poller] pane Codex real no encontrado; fail-closed", flush=True)
        return False
    try:
        # 1) Enviar el texto en modo literal (-l) — no interpreta keys especiales
        base = tmux_base(session)
        subprocess.run(
            [*base, "send-keys", "-t", target, "-l", clean_text],
            capture_output=True, timeout=5, check=True
        )
        # 2) Delay 500ms para que Codex TUI digiera el input completo antes del submit
        time.sleep(0.5)
        # 3) Submit once. A blind second Enter can race the TUI; retry only if
        # the JSONL acknowledgement is absent.
        subprocess.run(
            [*base, "send-keys", "-t", target, "Enter"],
            capture_output=True, timeout=5, check=True
        )
        return True
    except Exception as e:
        # Fallback por buffer, conservando el mismo pane concreto. Nunca volver
        # al target de sesión: podría escribir en un panel watch activo.
        try:
            base = tmux_base(session)
            subprocess.run(
                [*base, "load-buffer", "-"], input=clean_text, text=True,
                capture_output=True, timeout=5, check=True,
            )
            subprocess.run([*base, "paste-buffer", "-t", target], capture_output=True, timeout=5, check=True)
            time.sleep(0.15)
            subprocess.run([*base, "send-keys", "-t", target, "C-m"], capture_output=True, timeout=5, check=True)
            return True
        except Exception as e2:
            print(f"[ada-codex-poller] tmux inject failed: {e} / fallback: {e2}", flush=True)
            return False


def retry_submit_key(session: str) -> bool:
    """Retry only the submit key, without duplicating the typed payload."""
    target = resolve_codex_pane_target(session)
    if not target:
        print("[ada-codex-poller] submit retry sin pane Codex real", flush=True)
        return False
    try:
        subprocess.run(
            [*tmux_base(session), "send-keys", "-t", target, "Enter"],
            capture_output=True, timeout=5, check=True,
        )
        return True
    except Exception as exc:
        print(f"[ada-codex-poller] submit retry failed: {exc}", flush=True)
        return False


async def confirm_submission(
    session_file: Path,
    start_offset: int,
    marker: str,
    timeout: float = SUBMIT_CONFIRM_TIMEOUT_SECONDS,
) -> dict | None:
    """Wait for a new task_started and matching user_message in Codex JSONL."""
    deadline = asyncio.get_event_loop().time() + timeout
    offset = start_offset
    turn_id: str | None = None
    remainder = b""
    while asyncio.get_event_loop().time() < deadline:
        try:
            size = session_file.stat().st_size
            if size > offset:
                with open(session_file, "rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                offset += len(chunk)
                data = remainder + chunk
                parts = data.split(b"\n")
                remainder = parts.pop()
                for raw in parts:
                    try:
                        event = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if event.get("type") != "event_msg":
                        continue
                    payload = event.get("payload") or {}
                    if payload.get("type") == "task_started":
                        turn_id = str(payload.get("turn_id") or "") or None
                    elif (
                        payload.get("type") == "user_message"
                        and marker in str(payload.get("message") or "")
                        and turn_id
                    ):
                        return {"turn_id": turn_id, "session_file": str(session_file)}
        except OSError:
            return None
        await asyncio.sleep(0.1)
    return None


async def submit_message_confirmed(
    session: str,
    text: str,
    marker: str,
    expected_task_id: str | None = None,
) -> dict | None:
    """Submit to tmux and commit only after Codex acknowledges the exact turn."""
    session_file = find_active_session()
    if not session_file:
        return None
    start_offset = session_file.stat().st_size
    if expected_task_id and not bind_pending_submission_attempt(
        expected_task_id, marker, session_file, start_offset
    ):
        return None
    if not inject_message(session, text):
        return None
    confirmed = await confirm_submission(session_file, start_offset, marker)
    if confirmed:
        return confirmed
    print(f"[ada-codex-poller] submit sin ACK, reintentando Enter: {marker}", flush=True)
    if not retry_submit_key(session):
        return None
    return await confirm_submission(session_file, start_offset, marker)


def load_last_ts() -> datetime:
    for path in (STATE_FILE, LEGACY_STATE_FILE):
        try:
            value = datetime.fromisoformat(path.read_text().strip())
        except Exception:
            continue
        if path != STATE_FILE:
            save_last_ts(value)
        return value
    # Default: mensajes de los últimos 5 minutos
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def save_last_ts(ts: datetime):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(ts.isoformat())


def save_last_id_ack(message_id: int, path: Path | None = None) -> int:
    """Durably advance an ACK without allowing concurrent writers to regress it.

    The terminal poller and the headless bridge share the lane ACK files.  An
    atomic replace prevents torn reads, but by itself still permits this race:
    writer A stores 900, then writer B (which read the old value) stores 800.
    Serialize the read/compare/replace operation with a stable sidecar lock and
    persist only ``max(current, candidate)``.
    """
    if path is None:
        path = ACK_FILE
    candidate = int(message_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(f".{path.name}.lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                current = int(path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                current = None
            persisted = candidate if current is None else max(current, candidate)
            if current != persisted:
                tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
                with tmp.open("w", encoding="utf-8") as handle:
                    handle.write(str(persisted))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
                os.chmod(path, 0o600)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            return persisted
    except Exception:
        # ``fdopen`` owns/closed lock_fd once entered.  Before that point it is
        # still ours, so close it without masking the original failure.
        try:
            os.close(lock_fd)
        except OSError:
            pass
        raise


def load_last_id_ack(path: Path | None = None) -> int | None:
    paths = (path,) if path is not None else (ACK_FILE, LEGACY_ACK_FILE)
    for candidate in paths:
        try:
            value = int(candidate.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if candidate != ACK_FILE and path is None:
            save_last_id_ack(value)
        return value
    return None


def acquire_dispatch_claim(message_id: int) -> int | None:
    """Nonblocking cross-process claim for one canonical chat source id."""
    DISPATCH_CLAIM_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    claim_path = DISPATCH_CLAIM_DIR / f"chat_{int(message_id)}.lock"
    fd = os.open(claim_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def release_dispatch_claim(fd: int | None) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


async def fetch_pending_turns(
    conn,
    team_last_id: int,
    dm_last_id: int,
    human_last_id: int,
    *,
    limit: int = 10,
):
    """Fetch direct DMs independently and ahead of public backlog.

    A single global cursor made a direct William DM wait behind every older
    public mention of ADA.  The two CTEs deliberately use separate cursors:
    consuming a high-id DM can never advance/skip the public lane.
    """
    return await conn.fetch(
        """
        WITH dm_pending AS (
            SELECT id, sender_name, content, created_at, channel, metadata, 0 AS lane
              FROM soul_v3.chat_messages
             WHERE id > $2
               AND lower(channel) LIKE 'dm:%'
               AND (
                    lower(split_part(channel, ':', 2)) = 'ada'
                    OR lower(split_part(channel, ':', 3)) = 'ada'
               )
               AND LOWER(sender_name) = CASE
                    WHEN lower(split_part(channel, ':', 2)) = 'ada'
                    THEN lower(split_part(channel, ':', 3))
                    ELSE lower(split_part(channel, ':', 2))
               END
               AND NOT (LOWER(sender_name) = ANY($5::text[]))
             ORDER BY id ASC
             LIMIT $7
        ),
        human_pending AS (
            SELECT id, sender_name, content, created_at, channel, metadata, 1 AS lane
              FROM soul_v3.chat_messages
             WHERE id > $3
               AND LOWER(sender_name) = ANY($9::text[])
               AND (
                    channel = ANY($6::text[])
                    OR (channel = $8 AND content ~* '\\mada\\M')
               )
             ORDER BY id ASC
             LIMIT $7
        ),
        team_pending AS (
            SELECT id, sender_name, content, created_at, channel, metadata, 2 AS lane
              FROM soul_v3.chat_messages
             WHERE id > $1
               AND LOWER(sender_name) = ANY($4::text[])
               AND NOT (LOWER(sender_name) = ANY($9::text[]))
               AND (
                    channel = ANY($6::text[])
                    OR (channel = $8 AND content ~* '\\mada\\M')
               )
             ORDER BY id ASC
             LIMIT $7
        )
        SELECT * FROM dm_pending
        UNION ALL
        SELECT * FROM human_pending
        UNION ALL
        SELECT * FROM team_pending
        ORDER BY lane ASC, id ASC
        """,
        team_last_id,
        dm_last_id,
        human_last_id,
        list(INJECT_FROM),
        sorted(INTERNAL_DM_SENDERS),
        ADA_USER_CHANNELS,
        limit,
        WEB_CHANNEL,
        list(HUMAN_SENDERS),
    )


async def priority_human_turn_pending(
    conn,
    dm_last_id: int,
    human_last_id: int,
) -> bool:
    """Recheck the canonical priority lanes immediately before fallback input.

    ACK files are re-read because the headless writer may have advanced either
    lane while this process was waiting for the TUI to become idle.
    """
    disk_dm = load_last_id_ack(DM_ACK_FILE)
    disk_human = load_last_id_ack(HUMAN_ACK_FILE)
    effective_dm = max(dm_last_id, disk_dm if disk_dm is not None else dm_last_id)
    effective_human = max(
        human_last_id,
        disk_human if disk_human is not None else human_last_id,
    )
    return bool(await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
              FROM soul_v3.chat_messages
             WHERE id > $1
               AND lower(channel) LIKE 'dm:%'
               AND (
                    lower(split_part(channel, ':', 2)) = 'ada'
                    OR lower(split_part(channel, ':', 3)) = 'ada'
               )
               AND LOWER(sender_name) = CASE
                    WHEN lower(split_part(channel, ':', 2)) = 'ada'
                    THEN lower(split_part(channel, ':', 3))
                    ELSE lower(split_part(channel, ':', 2))
               END
               AND NOT (LOWER(sender_name) = ANY($3::text[]))
        ) OR EXISTS (
            SELECT 1
              FROM soul_v3.chat_messages
             WHERE id > $2
               AND LOWER(sender_name) = ANY($6::text[])
               AND (
                    channel = ANY($4::text[])
                    OR (channel = $5 AND content ~* '\\mada\\M')
               )
        )
        """,
        effective_dm,
        effective_human,
        sorted(INTERNAL_DM_SENDERS),
        ADA_USER_CHANNELS,
        WEB_CHANNEL,
        list(HUMAN_SENDERS),
    ))


def prioritize_turn_rows(rows):
    """Return one priority lane without advancing/skipping the other lanes."""
    dm_rows = [row for row in rows if is_ada_dm_channel(row["channel"])]
    if dm_rows:
        return dm_rows, "dm"
    human_rows = [row for row in rows if str(row["sender_name"]).lower() in HUMAN_SENDERS]
    if human_rows:
        return human_rows, "human"
    return list(rows), "team"


def quarantine_failed_submission(
    message_id: int,
    marker: str,
    path: Path = FAILED_SUBMISSION_FILE,
    channel: str = WEB_CHANNEL,
    lane: str = "team",
) -> None:
    """Persist a failed TUI submit so the payload is never typed twice.

    While quarantined, the listener advertises ``degraded``. That releases the
    single-writer lease to the headless bridge, which can consume the same DB
    row without losing it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "message_id": int(message_id),
        "marker": marker,
        "channel": channel,
        "lane": lane,
        "failed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def recover_quarantined_from_headless(
    last_id: int,
    failure_path: Path = FAILED_SUBMISSION_FILE,
    headless_cursor_path: Path = HEADLESS_CURSOR_FILE,
) -> tuple[int, bool]:
    """Return ``(cursor, still_quarantined)`` for a failed terminal submit.

    The quarantine clears only after the headless bridge durably advances past
    the failed DB id. This makes failover lossless and prevents tmux buffer
    amplification when Codex does not acknowledge Enter.
    """
    try:
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        failed_id = int(failure["message_id"])
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return last_id, False
    try:
        headless_id = int(headless_cursor_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        headless_id = last_id
    if headless_id < failed_id:
        return last_id, True
    recovered_id = max(last_id, headless_id)
    # Pass the current constant explicitly; the helper's default is bound at
    # import time and would ignore a runtime state-dir override in tests/tools.
    recovered_id = save_last_id_ack(recovered_id, ACK_FILE)
    failure_path.unlink(missing_ok=True)
    return recovered_id, False


def recover_quarantined_lanes(
    public_last_id: int,
    dm_last_id: int,
    human_last_id: int,
    failure_path: Path = FAILED_SUBMISSION_FILE,
) -> tuple[int, int, int, bool]:
    """Recover the failed lane without letting a DM jump the public cursor."""
    try:
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        failed_id = int(failure["message_id"])
        channel = str(failure.get("channel") or WEB_CHANNEL)
        lane = str(failure.get("lane") or "team")
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return public_last_id, dm_last_id, human_last_id, False

    if is_ada_dm_channel(channel):
        observed = load_last_id_ack(DM_ACK_FILE)
        if observed is None or observed < failed_id:
            return public_last_id, dm_last_id, human_last_id, True
        dm_last_id = max(dm_last_id, observed)
    elif lane == "human":
        observed = load_last_id_ack(HUMAN_ACK_FILE)
        if observed is None or observed < failed_id:
            return public_last_id, dm_last_id, human_last_id, True
        human_last_id = max(human_last_id, observed)
    else:
        try:
            observed = int(HEADLESS_CURSOR_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            observed = public_last_id
        if observed < failed_id:
            return public_last_id, dm_last_id, human_last_id, True
        public_last_id = max(public_last_id, observed)
        public_last_id = save_last_id_ack(public_last_id, ACK_FILE)
    failure_path.unlink(missing_ok=True)
    return public_last_id, dm_last_id, human_last_id, False


def write_listener_health(status: str, path: Path = LISTENER_HEALTH_FILE) -> None:
    """Renew the terminal-listener lease consumed by the headless bridge."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "pid": os.getpid(),
        "status": status,
        "heartbeat_epoch": __import__("time").time(),
        "last_ack_id": load_last_id_ack(),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


async def public_event_db_id(conn, row: dict) -> int | None:
    """Resolve a legacy public-feed event to its canonical chat source id."""
    event_id = str(row.get("id") or "").strip()
    if not event_id:
        return None
    try:
        db_id = await conn.fetchval(
            """
            SELECT id
              FROM soul_v3.chat_messages
             WHERE channel = $1
               AND metadata->>'legacy_id' = $2
             ORDER BY id DESC
             LIMIT 1
            """,
            WEB_CHANNEL,
            event_id,
        )
    except Exception:
        return None
    return int(db_id) if db_id is not None else None


def is_ack_only_reply(content: str | None) -> bool:
    """Separate visibility ACKs from replies that prove substantive handling."""
    return bool(ACK_ONLY_RE.search(re.sub(r"\s+", " ", str(content or "")).strip()))


async def substantive_reply_id(conn, row: dict) -> int | None:
    """Find a durable ADA reply so an orphaned source is not executed twice.

    A busy/live ACK deliberately does not count: the original source remains
    pending. Any later substantive reply proves another trusted delivery path
    already handled the source, so replaying it would duplicate tools.
    """
    message_id = int(row["id"])
    metadata = _message_metadata(row)
    references = {str(message_id), f"db_{message_id}"}
    legacy_id = str(metadata.get("legacy_id") or "").strip()
    if legacy_id:
        references.add(legacy_id)
    replies = await conn.fetch(
        """SELECT id, content
             FROM soul_v3.chat_messages
            WHERE id > $1
              AND channel = $2
              AND UPPER(sender_name) = 'ADA'
              AND metadata->>'in_reply_to' = ANY($3::text[])
            ORDER BY id DESC
            LIMIT 12""",
        message_id,
        str(row["channel"]),
        sorted(references),
    )
    for reply in replies:
        if not is_ack_only_reply(reply["content"]):
            return int(reply["id"])
    return None


async def public_event_already_acknowledged(conn, row: dict) -> bool:
    """Deduplicate the local public feed against the canonical DB delivery.

    Public feed ids are legacy API ids while the DB poller acknowledges numeric
    chat ids. Matching only JSONL text is racy: the fallback can run before the
    first response finishes and inject the same user message a second time.
    """
    sender = str(row.get("from") or row.get("sender") or "").lower()
    last_ack = load_last_id_ack(HUMAN_ACK_FILE) if sender in HUMAN_SENDERS else load_last_id_ack()
    if last_ack is None:
        return False
    db_id = await public_event_db_id(conn, row)
    return db_id is not None and db_id <= last_ack


def load_public_event_offset(path: Path = PUBLIC_EVENT_FILE, cursor: Path = PUBLIC_EVENT_CURSOR) -> int:
    """Load byte cursor; first boot starts at EOF to avoid replaying history."""
    if cursor.exists():
        try:
            value = int(cursor.read_text(encoding="utf-8").strip())
            return max(0, value)
        except (OSError, ValueError):
            pass
    try:
        return path.stat().st_size
    except OSError:
        return 0


def save_public_event_offset(offset: int, cursor: Path = PUBLIC_EVENT_CURSOR) -> None:
    cursor.write_text(str(max(0, int(offset))), encoding="utf-8")


def read_public_event_batch(path: Path, offset: int) -> tuple[list[tuple[int, dict]], int]:
    """Read complete JSONL records after offset and return each record's next offset."""
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if offset > size:  # monitor restarted/truncated its output
        offset = 0
    rows: list[tuple[int, dict]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                next_offset = handle.tell()
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    offset = next_offset
                    continue
                if isinstance(row, dict):
                    rows.append((next_offset, row))
                offset = next_offset
    except OSError:
        return [], offset
    return rows, offset


def is_verified_public_ada_event(row: dict) -> bool:
    sender = str(row.get("from") or "").lower()
    channel = str(row.get("channel") or "").lower()
    content = str(row.get("message") or row.get("content") or "")
    provenance = row.get("provenance") or {}
    return (
        sender in INJECT_FROM
        and channel == WEB_CHANNEL
        and bool(PUBLIC_TRIGGER_RE.search(content))
        and provenance.get("verified") is True
        and str(provenance.get("verified_sender") or "").lower() == sender
    )


def primary_session_contains_event(row: dict, lookback_bytes: int = 2_000_000) -> bool:
    """Prevent duplicate tmux injection when another trusted path already delivered it."""
    session = find_active_session()
    if not session:
        return False
    event_id = str(row.get("id") or "")
    content = str(row.get("message") or row.get("content") or "").strip()
    if not event_id and not content:
        return False
    try:
        size = session.stat().st_size
        with session.open("rb") as handle:
            handle.seek(max(0, size - lookback_bytes))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    if event_id and event_id in tail:
        return True
    escaped = json.dumps(content, ensure_ascii=False)[1:-1]
    return bool(escaped and escaped in tail)


def format_public_event(row: dict) -> str:
    sender = str(row.get("from") or "William")
    content = re.sub(r"\s+", " ", str(row.get("message") or row.get("content") or "")).strip()
    if len(content) > 1500:
        content = content[:1500] + " …[truncado, msg largo]"
    timestamp = str(row.get("timestamp") or "")
    try:
        hour = datetime.fromisoformat(timestamp).astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        hour = datetime.now().astimezone().strftime("%H:%M")
    return f"[{sender} @ {hour} / {WEB_CHANNEL} id {row.get('id', '?')}]: {content}"


def _write_active_task(record: dict, path: Path | None = None) -> None:
    target = ACTIVE_TASK_FILE if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target.parent, 0o700)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    os.chmod(target, 0o600)
    directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def bind_pending_submission_attempt(
    expected_id: str,
    marker: str,
    session_file: Path,
    start_offset: int,
) -> bool:
    """Durably record the exact injection boundary before touching tmux."""
    try:
        record = json.loads(ACTIVE_TASK_FILE.read_text(encoding="utf-8"))
        if record.get("id") != expected_id or record.get("status") != "pending_submit":
            return False
        record.update({
            "turn_marker": marker,
            "submission_session_file": str(session_file),
            "submission_start_offset": int(start_offset),
            "submission_started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        _write_active_task(record)
        return True
    except Exception as exc:
        print(f"[ada-codex-poller] bind submit attempt failed: {exc}", flush=True)
        return False


def mark_active_public_event(row: dict, status: str = "pending_submit") -> None:
    try:
        _write_active_task({
            "id": f"public_{row.get('id', 'unknown')}",
            "activated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": "ada_codex_poller_public_fallback",
            "channel": WEB_CHANNEL,
            "response_source_id": str(row.get("id") or ""),
            "reply_to": str(row.get("from") or row.get("sender") or "William"),
            "status": status,
        })
    except Exception as exc:
        print(f"[ada-codex-poller] public active turn marker failed: {exc}", flush=True)


def _message_metadata(row: dict) -> dict:
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            return {}
    return metadata if isinstance(metadata, dict) else {}


def resolve_message_image(row: dict) -> Path | None:
    """Resolve a chat attachment without allowing paths outside uploads/."""
    file_url = str(_message_metadata(row).get("file_url") or "").strip()
    if not file_url.startswith("/uploads/"):
        return None
    candidate = (UPLOAD_ROOT / file_url.removeprefix("/uploads/")).resolve()
    root = UPLOAD_ROOT.resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def format_message(row: dict) -> str:
    """Formatea un mensaje dirigido a ADA para inyectar en Codex."""
    sender = row["sender_name"]
    content = str(row["content"] or "")
    image_path = resolve_message_image(row)
    if image_path is not None:
        caption = content.strip()
        content = f"[IMAGEN] archivo={image_path}"
        if caption and caption.lower() not in {"[image]", "[imagen]"}:
            content += f" caption={caption}"
    ts = row["created_at"]
    hour = ts.astimezone().strftime("%H:%M")
    channel = row["channel"] if "channel" in row else ""
    msg_id = row["id"] if "id" in row else "?"
    # Limpiar prefijo [Matrix] si existe
    content = content.replace("[Matrix] ", "").strip()
    content = re.sub(r"\s+", " ", content)
    # Cap msg length: a giant message (e.g. 61KB code paste) overflows tmux
    # send-keys arg limit -> inject fails for ALL messages (NEXUS 2026-06-22 fix).
    if len(content) > 1500:
        content = content[:1500] + " …[truncado, msg largo]"
    return f"[{sender} @ {hour} / {channel} id {msg_id}]: {content}"


def mark_active_chat_turn(row: dict, status: str = "pending_submit") -> None:
    """Expose the current DB-driven turn channel to the terminal relay.

    The visible Codex terminal writes the answer, but the stream relay is the
    component that persists it back to chat. Without this marker, relay output
    falls back to web_chat and DM replies never reach William's DM.
    """
    try:
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, ValueError):
                metadata = {}
        legacy_id = str(metadata.get("legacy_id") or "").strip() if isinstance(metadata, dict) else ""
        response_source_id = legacy_id or f"db_{row['id']}"
        record = {
            "id": f"chat_{row['id']}",
            "activated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": "ada_codex_poller",
            "channel": row["channel"],
            "chat_message_id": row["id"],
            # Council keys public turns by the authenticated API legacy id.
            # Raw numeric ids are not accepted by its compatibility lookup;
            # db_<id> is the canonical fallback for DB-only/DM messages.
            "response_source_id": response_source_id,
            "reply_to": str(row.get("sender_name") or "William"),
            "status": status,
        }
        _write_active_task(record)
    except Exception as exc:
        print(f"[ada-codex-poller] active turn marker failed: {exc}", flush=True)


def activate_pending_turn(expected_id: str, submission: dict) -> bool:
    """Atomically bind a pending route marker to the accepted Codex turn."""
    try:
        record = json.loads(ACTIVE_TASK_FILE.read_text(encoding="utf-8"))
        if record.get("id") != expected_id or record.get("status") != "pending_submit":
            return False
        record.update({
            "status": "submitted",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "turn_id": submission["turn_id"],
            "session_file": submission["session_file"],
        })
        _write_active_task(record)
        return True
    except Exception as exc:
        print(f"[ada-codex-poller] activate pending turn failed: {exc}", flush=True)
        return False


def discard_pending_turn(expected_id: str) -> None:
    """Remove only a marker for which tmux injection was never attempted.

    Once ``turn_marker`` is durable, timeout is ambiguous: Codex may accept it
    late.  Such a marker remains quarantined for JSONL reconciliation.
    """
    try:
        record = json.loads(ACTIVE_TASK_FILE.read_text(encoding="utf-8"))
        if (
            record.get("id") == expected_id
            and record.get("status") == "pending_submit"
            and not record.get("turn_marker")
        ):
            ACTIVE_TASK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def should_attach_recent_context(row: dict) -> bool:
    """Keep continuity only when it is useful, never as a default transcript."""
    if CONTEXT_MODE == "off" or CONTEXT_RECENT_LIMIT == 0 or CONTEXT_MAX_CHARS == 0:
        return False
    if CONTEXT_MODE == "always" or is_ada_dm_channel(row.get("channel")):
        return True
    if str(row.get("sender_name") or "").lower() not in HUMAN_SENDERS:
        return False
    content = re.sub(r"\s+", " ", str(row.get("content") or "")).strip()
    return bool(CONTEXT_REFERENCE_RE.search(content))


async def fetch_recent_context(
    conn,
    channel: str,
    before_id: int,
    limit: int = CONTEXT_RECENT_LIMIT,
    current_sender: str | None = None,
):
    """Trae los últimos N mensajes del canal previos al mensaje dirigido.
    Excluye ruido (status/heartbeat/SILENT) y contenido con forma de secreto."""
    sender_filter: list[str] = []
    if is_ada_dm_channel(channel):
        peer = ada_dm_peer(channel)
        sender_filter = [peer, "ada"] if peer else ["ada"]
        if peer == "william":
            sender_filter.append("henry")
    elif str(current_sender or "").lower() in HUMAN_SENDERS:
        # In General, continuity with William/Henry must not drag the whole
        # team's transcript into ADA. Team context is recalled on demand.
        sender_filter = [str(current_sender).lower(), "ada"]
    rows = await conn.fetch(
        """SELECT id, sender_name, content, created_at
           FROM soul_v3.chat_messages
           WHERE channel = $1
             AND id < $2
             AND (
                  cardinality($4::text[]) = 0
                  OR LOWER(sender_name) = ANY($4::text[])
             )
             AND content NOT ILIKE '[STATUS]%'
             AND content NOT ILIKE '[HEARTBEAT]%'
             AND content !~ '^\\[SILENT\\]$'
             AND content !~* '(client[_-]?secret|client[_-]?id|refresh[_-]?token|access[_-]?token|api[_-]?key|password|passwd|authorization|bearer[[:space:]]+[A-Za-z0-9._~+/-]+|credentials?\\.json|\\.env\\y|private[_-]?key)'
           ORDER BY id DESC
           LIMIT $3""",
        channel, before_id, limit, sender_filter
    )
    return list(reversed(rows))


def format_context_block(rows) -> str:
    """Format bounded history as data, explicitly separated from the order."""
    if not rows:
        return ""
    parts = ["[HISTORIAL_NO_ORDEN]"]
    total = len(parts[0])
    for r in rows:
        t = r["created_at"].astimezone().strftime("%H:%M")
        content = re.sub(r"\s+", " ", redact_sensitive(r["content"])).strip()
        if is_sensitive_content(r["content"]):
            content = "[REDACTED]"
        if len(content) > 200:
            content = content[:197] + "..."
        part = f"{t} {r['sender_name']}: {content}"
        if total + len(part) + 3 > CONTEXT_MAX_CHARS:
            break
        parts.append(part)
        total += len(part) + 3
    # Separador visual ║ entre mensajes - todo en una línea
    return " ║ ".join(parts) if len(parts) > 1 else ""


async def poll_loop():
    conn = await asyncpg.connect(resolve_db_dsn())
    last_ts = load_last_ts()
    last_id = load_last_id_ack()
    if last_id is None:
        last_id = int(await conn.fetchval(
            "SELECT COALESCE(MAX(id), 0) FROM soul_v3.chat_messages"
        ))
        last_id = save_last_id_ack(last_id)
    dm_last_id = load_last_id_ack(DM_ACK_FILE)
    if dm_last_id is None:
        # Before the lane split, the global cursor was the only durable ACK.
        # Starting from it preserves already-consumed history while allowing
        # every newer direct DM to preempt the public backlog.
        dm_last_id = last_id
        dm_last_id = save_last_id_ack(dm_last_id, DM_ACK_FILE)
    human_last_id = load_last_id_ack(HUMAN_ACK_FILE)
    if human_last_id is None:
        human_last_id = last_id
        human_last_id = save_last_id_ack(human_last_id, HUMAN_ACK_FILE)
    public_offset = load_public_event_offset()
    print(
        f"[ada-codex-poller] iniciado — desde {last_ts.strftime('%H:%M:%S')} "
        f"dm_scope=dynamic_assigned_users user_channels={ADA_USER_CHANNELS}",
        flush=True,
    )

    while True:
        try:
            last_id, dm_last_id, human_last_id, submit_quarantined = recover_quarantined_lanes(
                last_id, dm_last_id, human_last_id
            )
            if submit_quarantined:
                # Status != running releases the bridge's terminal-writer lease.
                write_listener_health("degraded")
                await asyncio.sleep(POLL_INTERVAL)
                continue
            write_listener_health("running")
            # Verificar que la sesión Codex sigue viva
            if not tmux_session_alive(TMUX_SESSION):
                print(f"[ada-codex-poller] sesión {TMUX_SESSION} no encontrada — saliendo", flush=True)
                break

            rows = await fetch_pending_turns(conn, last_id, dm_last_id, human_last_id)
            rows, active_lane = prioritize_turn_rows(rows)
            # A direct DM is the priority lane.  Do not begin a public turn in
            # the same cycle: the TUI may still be completing the DM response.

            for row in rows:
                task_id = f"chat_{row['id']}"
                completed = load_completion_receipt(task_id)
                if completed is not None:
                    # Crash recovery: publication finished, but the poller died
                    # before advancing the lane cursor. ACK without reinjection.
                    if is_ada_dm_channel(row["channel"]):
                        dm_last_id = save_last_id_ack(int(row["id"]), DM_ACK_FILE)
                    elif str(row["sender_name"]).lower() in HUMAN_SENDERS:
                        human_last_id = save_last_id_ack(int(row["id"]), HUMAN_ACK_FILE)
                    else:
                        last_id = save_last_id_ack(int(row["id"]))
                    continue
                prior_reply_id = await substantive_reply_id(conn, row)
                if prior_reply_id is not None:
                    # Another trusted writer already produced a durable,
                    # substantive reply. Reconcile the lane instead of
                    # replaying tools after a quarantined routing marker.
                    if is_ada_dm_channel(row["channel"]):
                        dm_last_id = save_last_id_ack(int(row["id"]), DM_ACK_FILE)
                    elif str(row["sender_name"]).lower() in HUMAN_SENDERS:
                        human_last_id = save_last_id_ack(int(row["id"]), HUMAN_ACK_FILE)
                    else:
                        last_id = save_last_id_ack(int(row["id"]))
                    print(
                        f"[ada-codex-poller] reconciliado chat_{row['id']} "
                        f"por respuesta durable db_id={prior_reply_id}; sin reinyección",
                        flush=True,
                    )
                    continue
                msg = format_message(row)
                # La continuidad es adaptativa: un mensaje autocontenido entra
                # limpio; sólo DMs o referencias explícitas reciben hasta 2
                # antecedentes del humano/ADA, nunca el transcript del equipo.
                context_rows = []
                context_block = ""
                if should_attach_recent_context(row):
                    try:
                        context_rows = await fetch_recent_context(
                            conn,
                            row["channel"],
                            row["id"],
                            current_sender=row.get("sender_name"),
                        )
                        context_block = format_context_block(context_rows)
                    except Exception as ctx_err:
                        print(f"[ada-codex-poller] context fetch failed: {ctx_err}", flush=True)
                # El mensaje actual queda rotulado y separado del historial.
                if context_block:
                    payload = f"{context_block} ═══ MENSAJE_ACTUAL: {msg}"
                else:
                    payload = msg
                turn_marker = f"SEAL_TURN=chat_{row['id']}"
                payload = f"{payload} [{turn_marker}]"
                # Esperar idle antes de inyectar — evita corrupción si Codex procesa
                idle = await wait_for_idle(BUSY_WAIT_MAX)
                if not idle:
                    print(f"[ada-codex-poller] timeout esperando idle — posponiendo: {msg[:60]}", flush=True)
                    break
                dispatch_claim = acquire_dispatch_claim(int(row["id"]))
                if dispatch_claim is None:
                    print(
                        f"[ada-codex-poller] source claim ocupado — posponiendo chat_{row['id']}",
                        flush=True,
                    )
                    break
                if is_ada_dm_channel(row["channel"]):
                    print(
                        f"[ada-codex-poller] inyectando DM id={row['id']} "
                        f"ctx={len(context_rows) if context_block else 0}",
                        flush=True,
                    )
                else:
                    print(f"[ada-codex-poller] inyectando (ctx={len(context_rows) if context_block else 0}): {msg[:80]}", flush=True)
                try:
                    mark_active_chat_turn(row, status="pending_submit")
                    submission = await submit_message_confirmed(
                        TMUX_SESSION, payload, turn_marker, expected_task_id=task_id
                    )
                    if not submission or not activate_pending_turn(task_id, submission):
                        discard_pending_turn(task_id)
                        quarantine_failed_submission(
                            int(row["id"]), turn_marker,
                            channel=row["channel"], lane=active_lane,
                        )
                        write_listener_health("degraded")
                        print(
                            f"[ada-codex-poller] NO ACK — {task_id} en cuarentena; "
                            "sin reinyectar hasta reconciliar el marcador JSONL",
                            flush=True,
                        )
                        break
                    print(
                        f"[ada-codex-poller] ACK Codex turn={submission['turn_id']} source={task_id}",
                        flush=True,
                    )
                    completion = await wait_for_terminal_completion(task_id)
                    if completion is None:
                        write_listener_health("degraded")
                        print(
                            f"[ada-codex-poller] SIN RECIBO durable para {task_id}; "
                            "cursor congelado para impedir pérdida o doble ejecución",
                            flush=True,
                        )
                        break
                    last_ts = row["created_at"].astimezone(timezone.utc)
                    if is_ada_dm_channel(row["channel"]):
                        dm_last_id = save_last_id_ack(int(row["id"]), DM_ACK_FILE)
                    elif str(row["sender_name"]).lower() in HUMAN_SENDERS:
                        human_last_id = save_last_id_ack(int(row["id"]), HUMAN_ACK_FILE)
                    else:
                        last_id = save_last_id_ack(int(row["id"]))
                    write_listener_health("running")
                finally:
                    release_dispatch_claim(dispatch_claim)
                await asyncio.sleep(1.0)  # pausa entre mensajes

            if rows:
                save_last_ts(last_ts)

            # Never let the legacy public JSONL fallback race or overtake a
            # direct DM.  The next cycle re-checks the DM lane first.
            if rows:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # RLS puede ocultar el webchat al rol de poller. El feed local ya
            # está filtrado por agente/provenance; úsalo como fallback sin
            # ampliar grants. Dedup contra el rollout primario evita doble input.
            public_rows, observed_offset = read_public_event_batch(PUBLIC_EVENT_FILE, public_offset)
            for next_offset, event in public_rows:
                if not is_verified_public_ada_event(event):
                    public_offset = next_offset
                    save_public_event_offset(public_offset)
                    continue
                canonical_public_id = await public_event_db_id(conn, event)
                event_sender = str(event.get("from") or event.get("sender") or "").lower()
                public_task_id = f"public_{event.get('id', 'unknown')}"
                completed_public = load_completion_receipt(public_task_id)
                if completed_public is not None:
                    if canonical_public_id is not None:
                        if event_sender in HUMAN_SENDERS:
                            human_last_id = save_last_id_ack(
                                canonical_public_id, HUMAN_ACK_FILE
                            )
                        else:
                            last_id = save_last_id_ack(canonical_public_id)
                    public_offset = next_offset
                    save_public_event_offset(public_offset)
                    continue
                public_ack = (
                    load_last_id_ack(HUMAN_ACK_FILE)
                    if event_sender in HUMAN_SENDERS
                    else load_last_id_ack()
                )
                if (
                    canonical_public_id is not None and public_ack is not None
                    and canonical_public_id <= public_ack
                ):
                    print(f"[ada-codex-poller] fallback dedup: {event.get('id')}", flush=True)
                    public_offset = next_offset
                    save_public_event_offset(public_offset)
                    continue
                if primary_session_contains_event(event):
                    print(
                        f"[ada-codex-poller] fallback {public_task_id} ya fue sometido "
                        "pero no tiene entrega durable; offset congelado",
                        flush=True,
                    )
                    break
                idle = await wait_for_idle(BUSY_WAIT_MAX)
                if not idle:
                    print(f"[ada-codex-poller] fallback espera idle: {event.get('id')}", flush=True)
                    break
                # ``wait_for_idle`` can take 30 seconds.  A William DM/human
                # command arriving during that wait must preempt this legacy
                # public fallback, and its JSONL offset must remain unconsumed.
                if await priority_human_turn_pending(conn, dm_last_id, human_last_id):
                    print(
                        f"[ada-codex-poller] fallback pospuesto por DM/humano: {event.get('id')}",
                        flush=True,
                    )
                    break
                fallback_claim = (
                    acquire_dispatch_claim(canonical_public_id)
                    if canonical_public_id is not None
                    else None
                )
                if canonical_public_id is not None and fallback_claim is None:
                    print(
                        f"[ada-codex-poller] fallback source claim ocupado: {event.get('id')}",
                        flush=True,
                    )
                    break
                try:
                    message = format_public_event(event)
                    print(f"[ada-codex-poller] fallback inyectando: {message[:100]}", flush=True)
                    turn_marker = f"SEAL_TURN={public_task_id}"
                    mark_active_public_event(event, status="pending_submit")
                    submission = await submit_message_confirmed(
                        TMUX_SESSION,
                        f"{message} [{turn_marker}]",
                        turn_marker,
                        expected_task_id=public_task_id,
                    )
                    if not submission or not activate_pending_turn(public_task_id, submission):
                        discard_pending_turn(public_task_id)
                        print(f"[ada-codex-poller] fallback NO ACK: {public_task_id}", flush=True)
                        break
                    completion = await wait_for_terminal_completion(public_task_id)
                    if completion is None:
                        write_listener_health("degraded")
                        print(
                            f"[ada-codex-poller] fallback SIN RECIBO durable: {public_task_id}; "
                            "ACK y offset congelados",
                            flush=True,
                        )
                        break
                    if canonical_public_id is not None:
                        if event_sender in HUMAN_SENDERS:
                            human_last_id = save_last_id_ack(
                                canonical_public_id, HUMAN_ACK_FILE
                            )
                        else:
                            last_id = save_last_id_ack(canonical_public_id)
                    public_offset = next_offset
                    save_public_event_offset(public_offset)
                finally:
                    release_dispatch_claim(fallback_claim)
                await asyncio.sleep(1.0)

            # Si solo hubo líneas no-ADA y no se procesaron individualmente,
            # persistir hasta el final observado para mantener el cursor acotado.
            if not public_rows and observed_offset != public_offset:
                public_offset = observed_offset
                save_public_event_offset(public_offset)

        except Exception as e:
            print(f"[ada-codex-poller] error: {e}", flush=True)
            try:
                conn = await asyncpg.connect(DSN)
            except Exception:
                pass

        await asyncio.sleep(POLL_INTERVAL)


def main():
    if already_running():
        print(f"[ada-codex-poller] ya corre (PID {PID_FILE.read_text().strip()})", flush=True)
        sys.exit(0)

    PID_FILE.write_text(str(os.getpid()))
    print(f"[ada-codex-poller] PID {os.getpid()} → {PID_FILE}", flush=True)

    try:
        asyncio.run(poll_loop())
    finally:
        write_listener_health("stopped")
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
