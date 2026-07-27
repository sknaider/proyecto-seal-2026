#!/usr/bin/env python3
"""Control plane persistente y nativo para ``mcp-web-soul``.

El motor CDP no decide política. Esta capa registra sesiones, clasifica acciones,
reserva approvals de un solo uso de forma atómica y aplica exclusión mutua durante
takeover humano. Solo usa la biblioteca estándar y SQLite local.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


READ = "READ"
UI_MUTATION = "UI_MUTATION"
AUTH_SESSION = "AUTH_SESSION"
REMOTE_COMMIT = "REMOTE_COMMIT"
FINANCIAL = "FINANCIAL"
ACCOUNT = "ACCOUNT"
DESTRUCTIVE = "DESTRUCTIVE"

APPROVAL_REQUIRED = {AUTH_SESSION, REMOTE_COMMIT, FINANCIAL, ACCOUNT, DESTRUCTIVE}

_FINANCIAL = re.compile(r"(?i)(pay|payment|checkout|purchase|buy|card|billing|transfer)")
_DESTRUCTIVE = re.compile(r"(?i)(delete|remove|revoke|cancel|terminate|destroy|drop)")
_REMOTE_COMMIT = re.compile(r"(?i)(submit|send|publish|post|upload|confirm|apply|save)")
_ACCOUNT = re.compile(r"(?i)(password|passwd|mfa|otp|2fa|permission|account|credential)")

_GITHUB_MUTATIONS = {
    "create_or_update_file", "create_repository", "push_files", "create_issue",
    "create_pull_request", "fork_repository", "create_branch", "update_issue",
    "add_issue_comment", "create_pull_request_review", "merge_pull_request",
    "update_pull_request_branch",
}


class ControlDenied(RuntimeError):
    """Acción bloqueada antes de alcanzar CDP o la red."""

    def __init__(self, message: str, *, code: str = "control_denied") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ActionPermit:
    session_id: str
    action_class: str
    action_hash: str
    approval_id: str | None = None


def classify_action(
    tool: str,
    arguments: Mapping[str, Any],
    *,
    risk_context: Mapping[str, Any] | None = None,
) -> str:
    """Clasificación server-side conservadora; el caller no elige su riesgo."""

    name = str(tool).lower()
    if name in _GITHUB_MUTATIONS:
        return REMOTE_COMMIT
    if name == "browser_action":
        delegated = str(arguments.get("action", "")).lower()
        delegated_args = dict(arguments)
        delegated_args.setdefault("selector", arguments.get("target", ""))
        return classify_action(delegated, delegated_args, risk_context=risk_context)
    selector = str(arguments.get("selector", ""))
    url = str(arguments.get("url", ""))
    context_text = json.dumps(
        dict(risk_context or {}),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    haystack = f"{selector} {url} {context_text}"
    if name == "set_cookie":
        return AUTH_SESSION
    if name == "browser_profile" and str(arguments.get("operation", "")).lower() != "list":
        return AUTH_SESSION
    if name in {"upload_file", "dialog_accept"}:
        return REMOTE_COMMIT
    if name == "press_key" and str(arguments.get("value", "")).lower() in {
        "enter", "numpadenter",
    }:
        return REMOTE_COMMIT
    if name == "type_text" and _ACCOUNT.search(selector):
        return ACCOUNT
    if name == "click":
        if _FINANCIAL.search(haystack):
            return FINANCIAL
        if _DESTRUCTIVE.search(haystack):
            return DESTRUCTIVE
        if _ACCOUNT.search(haystack):
            return ACCOUNT
        if _REMOTE_COMMIT.search(haystack):
            return REMOTE_COMMIT
        return UI_MUTATION
    if name in {"type_text", "hover", "select_option", "press_key", "drag", "resize"}:
        return UI_MUTATION
    return READ


def discover_orphan_browsers(proc_root: str | Path = "/proc") -> list[dict[str, Any]]:
    """Detecta procesos CDP cuyo árbol ya no conserva un supervisor.

    Brave puede dejar dos procesos raíz observables: el wrapper conserva
    ``--remote-debugging-port``/``--user-data-dir``, pero el binario hijo puede
    sobrevivir al wrapper con el perfil en ``argv[0]`` y el puerto únicamente
    visible por sus sockets. Ambos deben seguir siendo detectables.
    """

    root = Path(proc_root)

    def info(pid: int) -> tuple[int, list[str], str] | None:
        try:
            cmd = (root / str(pid) / "cmdline").read_bytes().split(b"\0")
            args = [item.decode("utf-8", errors="replace") for item in cmd if item]
            status = (root / str(pid) / "status").read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            return None
        parent = 0
        process_name = ""
        for line in status.splitlines():
            if line.startswith("PPid:"):
                parent = int(line.split(":", 1)[1].strip() or 0)
            elif line.startswith("Name:"):
                process_name = line.split(":", 1)[1].strip().lower()
        return parent, args, process_name

    def listening_ports(pid: int) -> list[int]:
        """Return TCP LISTEN ports owned by ``pid`` without shelling out."""

        process_root = root / str(pid)
        socket_inodes: set[str] = set()
        try:
            for fd in (process_root / "fd").iterdir():
                try:
                    target = os.readlink(fd)
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                if target.startswith("socket:[") and target.endswith("]"):
                    socket_inodes.add(target[8:-1])
        except (FileNotFoundError, PermissionError, OSError):
            return []
        ports: set[int] = set()
        for table in ("tcp", "tcp6"):
            try:
                lines = (process_root / "net" / table).read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()[1:]
            except (FileNotFoundError, PermissionError, OSError):
                continue
            for line in lines:
                fields = line.split()
                if len(fields) < 10 or fields[3] != "0A" or fields[9] not in socket_inodes:
                    continue
                try:
                    ports.add(int(fields[1].rsplit(":", 1)[1], 16))
                except (IndexError, ValueError):
                    continue
        return sorted(ports)

    def profile_from_argv(args: list[str]) -> str:
        profile_arg = next((arg for arg in args if arg.startswith("--user-data-dir=")), "")
        if profile_arg:
            return profile_arg.split("=", 1)[1]
        for arg in args:
            candidate = Path(arg)
            if (
                candidate.name.startswith("profile-")
                and "mcp-web-soul" in candidate.parts
                and "runtime-profiles" in candidate.parts
            ):
                return arg
        return ""

    candidates: dict[str, dict[str, Any]] = {}
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        current = info(pid)
        if not current:
            continue
        parent, args, process_name = current
        executable = Path(args[0]).name.lower() if args else ""
        identity = f"{executable} {process_name}"
        if not any(name in identity for name in ("brave", "chrome", "chromium", "bash")):
            continue
        port_arg = next((arg for arg in args if arg.startswith("--remote-debugging-port=")), "")
        profile = profile_from_argv(args)
        if not profile:
            continue
        ports = [int(port_arg.split("=", 1)[1])] if port_arg else listening_ports(pid)
        if not ports:
            ports = [0]
        supervised = False
        ancestor = parent
        for _ in range(16):
            if ancestor <= 1:
                break
            ancestor_info = info(ancestor)
            if not ancestor_info:
                break
            ancestor, ancestor_args, _ = ancestor_info
            if any("seal_cdp_mcp.py" in arg for arg in ancestor_args):
                supervised = True
                break
        if supervised:
            continue
        candidate = {
            "pid": pid,
            "port": ports[0],
            "profile": profile,
        }
        previous = candidates.get(profile)
        if previous is None or pid < previous["pid"]:
            candidates[profile] = candidate
    return sorted(candidates.values(), key=lambda row: row["pid"])


class BrowserControlPlane:
    """Registry + broker con transiciones SQLite serializadas."""

    def __init__(self, db_path: str | Path, *, key_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.key_path = Path(key_path).expanduser().resolve() if key_path else self.db_path.with_suffix(".key")
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._key = self._load_or_create_key()
        self._init_schema()

    def _load_or_create_key(self) -> bytes:
        try:
            key = self.key_path.read_bytes()
        except FileNotFoundError:
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            key = secrets.token_bytes(32)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
        os.chmod(self.key_path, 0o600)
        if len(key) != 32:
            raise RuntimeError("control key inválida")
        return key

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    agent TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('ready','takeover_requested','human_active','closed','failed')),
                    created_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    closed_at REAL,
                    takeover_operator TEXT,
                    takeover_until REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_live_session_per_process
                    ON sessions(agent, pid) WHERE state != 'closed';
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    action_class TEXT NOT NULL,
                    action_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('requested','approved','executing','executed','failed','cancelled','expired')),
                    requested_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    operator TEXT,
                    decided_at REAL,
                    executed_at REAL,
                    error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS approvals_by_session ON approvals(session_id, status);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "takeover_operator" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN takeover_operator TEXT")
            if "takeover_until" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN takeover_until REAL")
        os.chmod(self.db_path, 0o600)

    def action_hash(self, tool: str, arguments: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            {"tool": str(tool), "arguments": dict(arguments)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hmac.new(self._key, canonical, hashlib.sha256).hexdigest()

    def ensure_session(self, *, agent: str, pid: int) -> str:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT session_id FROM sessions WHERE agent=? AND pid=? AND state!='closed'",
                (str(agent), int(pid)),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE sessions SET heartbeat_at=? WHERE session_id=?",
                    (now, row["session_id"]),
                )
                conn.commit()
                return str(row["session_id"])
            session_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO sessions(session_id,agent,pid,state,created_at,heartbeat_at) VALUES(?,?,?,?,?,?)",
                (session_id, str(agent), int(pid), "ready", now, now),
            )
            conn.commit()
            return session_id

    def session_status(self, session_id: str) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET state='ready', takeover_operator=NULL, takeover_until=NULL "
                "WHERE session_id=? AND state IN ('takeover_requested','human_active') "
                "AND takeover_until IS NOT NULL AND takeover_until<=?",
                (session_id, now),
            )
            row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            raise ControlDenied("sesión inexistente", code="session_not_found")
        return dict(row)

    def heartbeat(self, session_id: str) -> None:
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE sessions SET heartbeat_at=? WHERE session_id=? AND state!='closed'",
                (time.time(), session_id),
            ).rowcount
        if changed != 1:
            raise ControlDenied("sesión cerrada o inexistente", code="session_closed")

    def close_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET state='closed', closed_at=?, heartbeat_at=? WHERE session_id=?",
                (time.time(), time.time(), session_id),
            )

    def request_approval(
        self,
        *,
        session_id: str,
        tool: str,
        arguments: Mapping[str, Any],
        ttl_seconds: int = 300,
        risk_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        action_class = classify_action(tool, arguments, risk_context=risk_context)
        if action_class not in APPROVAL_REQUIRED:
            raise ControlDenied("esta acción no requiere approval", code="approval_not_required")
        ttl = max(1, min(int(ttl_seconds), 300))
        now = time.time()
        approval_id = str(uuid.uuid4())
        digest = self.action_hash(tool, arguments)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO approvals(approval_id,session_id,action_class,action_hash,status,requested_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (approval_id, session_id, action_class, digest, "requested", now, now + ttl),
            )
        return {
            "approval_id": approval_id,
            "session_id": session_id,
            "action_class": action_class,
            "status": "requested",
            "expires_at": now + ttl,
        }

    def approval_status(self, approval_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if not row:
            raise ControlDenied("approval inexistente", code="approval_not_found")
        result = dict(row)
        result.pop("action_hash", None)
        return result

    def approve(self, approval_id: str, *, operator: str) -> None:
        """Punto de integración para una identidad externa autenticada; no es tool MCP."""

        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status,expires_at FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if not row or row["status"] != "requested":
                conn.rollback()
                raise ControlDenied("approval no está pendiente", code="approval_state")
            if float(row["expires_at"]) <= now:
                conn.execute("UPDATE approvals SET status='expired', decided_at=? WHERE approval_id=?", (now, approval_id))
                conn.commit()
                raise ControlDenied("approval expirado", code="approval_expired")
            conn.execute(
                "UPDATE approvals SET status='approved', operator=?, decided_at=? WHERE approval_id=?",
                (str(operator), now, approval_id),
            )
            conn.commit()

    def authorize(
        self,
        *,
        session_id: str,
        tool: str,
        arguments: Mapping[str, Any],
        approval_id: str | None = None,
        risk_context: Mapping[str, Any] | None = None,
    ) -> ActionPermit:
        action_class = classify_action(tool, arguments, risk_context=risk_context)
        digest = self.action_hash(tool, arguments)
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = conn.execute(
                "SELECT state,takeover_until FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if not session or session["state"] == "closed":
                conn.rollback()
                raise ControlDenied("sesión cerrada o inexistente", code="session_closed")
            state = str(session["state"])
            if state in {"takeover_requested", "human_active"} and session["takeover_until"] is not None:
                if float(session["takeover_until"]) <= now:
                    conn.execute(
                        "UPDATE sessions SET state='ready', takeover_operator=NULL, takeover_until=NULL "
                        "WHERE session_id=?",
                        (session_id,),
                    )
                    state = "ready"
            control_read = str(tool) in {
                "browser_session",
                "browser_approval",
                "browser_takeover",
                "browser_readiness",
            }
            if state in {"takeover_requested", "human_active"} and not control_read:
                conn.rollback()
                raise ControlDenied("sesión bloqueada por takeover humano", code="takeover_locked")
            conn.execute("UPDATE sessions SET heartbeat_at=? WHERE session_id=?", (now, session_id))
            if action_class not in APPROVAL_REQUIRED:
                conn.commit()
                return ActionPermit(session_id, action_class, digest)
            if not approval_id:
                conn.rollback()
                raise ControlDenied("approval requerido para esta acción", code="approval_required")
            approval = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if not approval or approval["session_id"] != session_id:
                conn.rollback()
                raise ControlDenied("approval no pertenece a la sesión", code="approval_mismatch")
            if approval["status"] != "approved":
                conn.rollback()
                raise ControlDenied("approval no está aprobado o ya fue usado", code="approval_state")
            if float(approval["expires_at"]) <= now:
                conn.execute("UPDATE approvals SET status='expired', decided_at=? WHERE approval_id=?", (now, approval_id))
                conn.commit()
                raise ControlDenied("approval expirado", code="approval_expired")
            if approval["action_class"] != action_class or not hmac.compare_digest(approval["action_hash"], digest):
                conn.rollback()
                raise ControlDenied("approval no coincide con acción y argumentos", code="approval_mismatch")
            changed = conn.execute(
                "UPDATE approvals SET status='executing' WHERE approval_id=? AND status='approved'",
                (approval_id,),
            ).rowcount
            if changed != 1:
                conn.rollback()
                raise ControlDenied("approval reservado concurrentemente", code="approval_replay")
            conn.commit()
        return ActionPermit(session_id, action_class, digest, approval_id)

    def finish(self, permit: ActionPermit, *, ok: bool, error_code: str | None = None) -> None:
        if not permit.approval_id:
            return
        status = "executed" if ok else "failed"
        with self._connect() as conn:
            conn.execute(
                "UPDATE approvals SET status=?, executed_at=?, error_code=? "
                "WHERE approval_id=? AND status='executing'",
                (status, time.time(), error_code, permit.approval_id),
            )

    def request_takeover(self, session_id: str) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE sessions SET state='takeover_requested', heartbeat_at=?, "
                "takeover_operator=NULL, takeover_until=? "
                "WHERE session_id=? AND state='ready'",
                (now, now + 300, session_id),
            ).rowcount
        if changed != 1:
            raise ControlDenied("takeover no disponible en este estado", code="takeover_state")
        return self.session_status(session_id)

    def activate_takeover(self, session_id: str, *, operator: str, lease_seconds: int = 60) -> None:
        """Punto externo autenticado; no se expone al agente como tool."""

        if not str(operator).strip():
            raise ValueError("operator requerido")
        lease = max(10, min(int(lease_seconds), 300))
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE sessions SET state='human_active', heartbeat_at=?, "
                "takeover_operator=?, takeover_until=? "
                "WHERE session_id=? AND state='takeover_requested'",
                (time.time(), str(operator), time.time() + lease, session_id),
            ).rowcount
        if changed != 1:
            raise ControlDenied("takeover no está solicitado", code="takeover_state")

    def renew_takeover(self, session_id: str, *, operator: str, lease_seconds: int = 60) -> None:
        lease = max(10, min(int(lease_seconds), 300))
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE sessions SET heartbeat_at=?, takeover_until=? "
                "WHERE session_id=? AND state='human_active' AND takeover_operator=?",
                (time.time(), time.time() + lease, session_id, str(operator)),
            ).rowcount
        if changed != 1:
            raise ControlDenied("takeover humano no coincide", code="takeover_state")

    def release_takeover(self, session_id: str, *, operator: str) -> None:
        """Punto externo autenticado; libera el lease humano."""

        if not str(operator).strip():
            raise ValueError("operator requerido")
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE sessions SET state='ready', heartbeat_at=?, takeover_operator=NULL, takeover_until=NULL "
                "WHERE session_id=? AND state IN ('takeover_requested','human_active') "
                "AND (takeover_operator IS NULL OR takeover_operator=?)",
                (time.time(), session_id, str(operator)),
            ).rowcount
        if changed != 1:
            raise ControlDenied("takeover no está activo", code="takeover_state")
