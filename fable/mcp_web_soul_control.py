#!/usr/bin/env python3
"""Control plane persistente y nativo para ``mcp-web-soul``.

El motor CDP no decide política. Esta capa registra sesiones, clasifica acciones,
reserva approvals de un solo uso de forma atómica y aplica exclusión mutua durante
takeover humano. Solo usa la biblioteca estándar y SQLite local.
"""

from __future__ import annotations

import grp
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
from urllib.parse import urlsplit, urlunsplit
from urllib.parse import unquote


READ = "READ"
UI_MUTATION = "UI_MUTATION"
AUTH_SESSION = "AUTH_SESSION"
REMOTE_COMMIT = "REMOTE_COMMIT"
FINANCIAL = "FINANCIAL"
ACCOUNT = "ACCOUNT"
DESTRUCTIVE = "DESTRUCTIVE"

APPROVAL_REQUIRED = {AUTH_SESSION, REMOTE_COMMIT, FINANCIAL, ACCOUNT, DESTRUCTIVE}

_FINANCIAL = re.compile(
    r"(?i)(pay|payment|checkout|purchase|buy|card|billing|transfer|"
    r"pagar|pago|pagos|comprar|compra|tarjeta|facturaci[oó]n|transferir|transferencia)"
)
_DESTRUCTIVE = re.compile(
    r"(?i)(delete|remove|revoke|cancel|terminate|destroy|drop|"
    r"eliminar|borrar|revocar|cancelar|terminar|destruir|quitar)"
)
_REMOTE_COMMIT = re.compile(
    r"(?i)(submit|send|publish|post|upload|confirm|apply|save|"
    r"enviar|publicar|subir|confirmar|aplicar|guardar)"
)
_ACCOUNT = re.compile(
    r"(?i)(password|passwd|mfa|otp|2fa|permission|account|credential|"
    r"contrase(?:n|ñ)a|clave|permiso|cuenta|credencial|autenticaci[oó]n|sesi[oó]n)"
)
_AUTH_SESSION = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:log[-_ ]?in|log[-_ ]?out|sign[-_ ]?in|sign[-_ ]?out|"
    r"auth(?:entication|orization)?|oauth|session|cookie|iniciar[-_ ]?sesi[oó]n|"
    r"cerrar[-_ ]?sesi[oó]n|acceso)(?:$|[^a-z0-9])"
)

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
        # Match the runtime normalization exactly. Without strip(), an action
        # such as ``" click "`` classified as READ and was then stripped and
        # executed by browser_action as the sensitive delegated operation.
        delegated = str(arguments.get("action", "")).strip().lower()
        delegated_args = dict(arguments)
        delegated_args.setdefault("selector", arguments.get("target", ""))
        return classify_action(delegated, delegated_args, risk_context=risk_context)
    selector = str(arguments.get("selector", ""))
    # URL paths and query keys are user-controlled action surfaces too. Decode
    # percent escapes so Spanish labels such as ``contraseña`` cannot bypass
    # the same conservative policy applied to visible DOM text.
    url = unquote(str(arguments.get("url", "")))
    context = dict(risk_context or {})
    # Only user/DOM values belong in linguistic heuristics. Serializing the
    # mapping itself made the key ``submitsForm`` match ``submit`` even when its
    # value was false, turning every ordinary button into a false positive.
    context_text = " ".join(
        str(context.get(key, ""))
        for key in (
            "text", "ariaLabel", "href", "formAction", "role", "type",
            "autocomplete", "name",
        )
    )
    haystack = f"{selector} {url} {context_text}"
    if name == "set_cookie":
        return AUTH_SESSION
    if name == "browser_profile" and str(arguments.get("operation", "")).lower() != "list":
        return AUTH_SESSION
    if name in {"browse", "navigate", "open_tab", "save_url"}:
        # A managed profile may already carry authenticated cookies. GET is not
        # structurally read-only: legacy endpoints, signed links and redirects
        # can mutate remote state even when the requested URL looks harmless.
        # Every navigation/download therefore crosses the approval+journal
        # boundary; linguistic URL heuristics are not a security boundary.
        return REMOTE_COMMIT
    if name in {"back", "dialog_dismiss", "switch_tab", "close_browser"}:
        # History navigation mutates browser state and may re-trigger page
        # lifecycle behavior. Dismissing a dialog, switching tabs and closing
        # the managed browser likewise mutate local browser/session state even
        # though they do not require external approval.
        return UI_MUTATION
    if name in {"upload_file", "dialog_accept"}:
        return REMOTE_COMMIT
    if name == "press_key":
        # browser_action executes ``value or target``. Classify that exact key;
        # looking only at value let target="Enter" bypass approval.
        effective_key = str(
            arguments.get("value")
            or arguments.get("target")
            or arguments.get("selector")
            or ""
        ).strip().lower()
        if effective_key in {"enter", "numpadenter"}:
            return REMOTE_COMMIT
    if name == "type_text":
        field_type = str(context.get("type", "")).strip().lower()
        autocomplete = str(context.get("autocomplete", "")).strip().lower()
        if (
            field_type == "password"
            or autocomplete in {"current-password", "new-password", "one-time-code"}
            or _ACCOUNT.search(selector)
        ):
            return ACCOUNT
    if name == "click":
        if _FINANCIAL.search(haystack):
            return FINANCIAL
        if _DESTRUCTIVE.search(haystack):
            return DESTRUCTIVE
        if _ACCOUNT.search(haystack):
            return ACCOUNT
        # Treat form submission as a structural fact, independent of UI language
        # or opaque labels/selectors.
        if (
            bool(context.get("submitsForm") or context.get("submit"))
            or str(context.get("type", "")).strip().lower() == "submit"
        ):
            return REMOTE_COMMIT
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

    def __init__(
        self,
        db_path: str | Path,
        *,
        key_path: str | Path | None = None,
        shared_group: str | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.key_path = Path(key_path).expanduser().resolve() if key_path else self.db_path.with_suffix(".key")
        self.shared_group = str(shared_group or "").strip() or None
        self.shared_gid = grp.getgrnam(self.shared_group).gr_gid if self.shared_group else None
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._key = self._load_or_create_key()
        self._init_schema()

    def _secure_file(self, path: Path) -> None:
        """Apply or validate the narrow file contract for the shared control DB."""

        info = path.stat()
        if self.shared_gid is None:
            if info.st_uid != os.geteuid():
                raise PermissionError(f"control file has unexpected owner: {path}")
            os.chmod(path, 0o600)
            return
        if info.st_uid == os.geteuid():
            os.chown(path, -1, self.shared_gid)
            os.chmod(path, 0o660)
            return
        mode = info.st_mode & 0o777
        if info.st_gid != self.shared_gid or mode & 0o007 or mode & 0o060 != 0o060:
            raise PermissionError(f"shared control file permissions invalid: {path}")

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
        self._secure_file(self.key_path)
        if len(key) != 32:
            raise RuntimeError("control key inválida")
        return key

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        self._secure_file(self.db_path)
        for suffix in ("-wal", "-shm"):
            auxiliary = Path(str(self.db_path) + suffix)
            if auxiliary.exists():
                self._secure_file(auxiliary)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            # If an older build ever persisted a raw remote-effect envelope,
            # overwriting the logical row is insufficient: SQLite free pages
            # and WAL frames can retain the old plaintext. Enable physical
            # zeroing before migration and compact only when a scrub occurs.
            conn.execute("PRAGMA secure_delete=ON")
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
                CREATE TABLE IF NOT EXISTS operator_operations (
                    message_id INTEGER PRIMARY KEY,
                    command_hash TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('intent','effect','completion')),
                    effect_json TEXT,
                    created_at REAL NOT NULL,
                    effect_at REAL,
                    completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS operator_operations_by_status
                    ON operator_operations(status, message_id);
                CREATE TABLE IF NOT EXISTS remote_effect_operations (
                    operation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    action_hash TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('armed','observed','completed','indeterminate')),
                    armed_at REAL NOT NULL,
                    observed_at REAL,
                    completed_at REAL,
                    ok INTEGER,
                    error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS remote_effect_operations_by_status
                    ON remote_effect_operations(status, armed_at);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "takeover_operator" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN takeover_operator TEXT")
            if "takeover_until" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN takeover_until REAL")
            approval_columns = {row[1] for row in conn.execute("PRAGMA table_info(approvals)")}
            if "effect_scope" not in approval_columns:
                conn.execute("ALTER TABLE approvals ADD COLUMN effect_scope TEXT")
            if "effect_key" not in approval_columns:
                conn.execute("ALTER TABLE approvals ADD COLUMN effect_key TEXT")
            remote_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(remote_effect_operations)")
            }
            if "effect_scope" not in remote_columns:
                conn.execute("ALTER TABLE remote_effect_operations ADD COLUMN effect_scope TEXT")
            if "effect_key" not in remote_columns:
                conn.execute("ALTER TABLE remote_effect_operations ADD COLUMN effect_key TEXT")
            if "reconciled_by" not in remote_columns:
                conn.execute("ALTER TABLE remote_effect_operations ADD COLUMN reconciled_by TEXT")
            # Legacy active approvals did not bind a logical-effect key. Safe
            # states are cancelled and must be re-approved; an executing row is
            # ambiguous and blocks startup for explicit reconciliation.
            ambiguous = conn.execute(
                "SELECT approval_id FROM approvals WHERE status='executing' "
                "AND (effect_scope IS NULL OR effect_key IS NULL) LIMIT 1"
            ).fetchone()
            if ambiguous is not None:
                raise RuntimeError("legacy executing approval lacks a logical-effect fence")
            conn.execute(
                "UPDATE approvals SET status='cancelled',error_code='migration_requires_reapproval' "
                "WHERE status IN ('requested','approved') "
                "AND (effect_scope IS NULL OR effect_key IS NULL)"
            )
            scrubbed_legacy_envelope = False
            legacy_remote = conn.execute(
                "SELECT r.operation_id,r.tool,r.arguments_json,r.status,"
                "r.effect_scope,r.effect_key,s.agent "
                "FROM remote_effect_operations r "
                "LEFT JOIN sessions s ON s.session_id=r.session_id "
            ).fetchall()
            for row in legacy_remote:
                if not row["agent"]:
                    raise RuntimeError("legacy remote effect has no owning session")
                try:
                    envelope = json.loads(str(row["arguments_json"]))
                    if not isinstance(envelope, dict) or not isinstance(envelope.get("arguments"), dict):
                        raise ValueError
                    stored_key = str(row["effect_key"] or "")
                    embedded_key = str(envelope.get("effect_key") or "")
                    if re.fullmatch(r"[0-9a-f]{64}", stored_key):
                        effect_key = stored_key
                    elif re.fullmatch(r"[0-9a-f]{64}", embedded_key):
                        effect_key = embedded_key
                    else:
                        tool = str(envelope.get("tool") or row["tool"])
                        effect_key = self.remote_effect_key(tool, envelope["arguments"])
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise RuntimeError("legacy remote effect envelope is not canonicalizable") from exc
                effect_scope = str(row["effect_scope"] or row["agent"])
                redacted = self._remote_effect_envelope(str(row["tool"]), effect_key)
                scrubbed_legacy_envelope = scrubbed_legacy_envelope or not hmac.compare_digest(
                    str(row["arguments_json"]), redacted
                )
                conn.execute(
                    "UPDATE remote_effect_operations "
                    "SET effect_scope=?,effect_key=?,arguments_json=? "
                    "WHERE operation_id=?",
                    (effect_scope, effect_key, redacted, str(row["operation_id"])),
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_active_approval_per_effect "
                "ON approvals(effect_scope,effect_key) "
                "WHERE effect_key IS NOT NULL AND status IN ('requested','approved','executing')"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_remote_effect "
                "ON remote_effect_operations(effect_scope,effect_key) "
                "WHERE effect_key IS NOT NULL AND status IN ('armed','observed','indeterminate')"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_remote_effect_per_scope "
                "ON remote_effect_operations(effect_scope) "
                "WHERE effect_scope IS NOT NULL AND status IN ('armed','observed','indeterminate')"
            )
            if scrubbed_legacy_envelope:
                conn.commit()
                checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is None or int(checkpoint[0]) != 0:
                    raise RuntimeError("legacy secret scrub could not checkpoint SQLite WAL")
                conn.execute("VACUUM")
                checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is None or int(checkpoint[0]) != 0:
                    raise RuntimeError("legacy secret scrub left SQLite WAL busy")
        self._secure_file(self.db_path)

    @staticmethod
    def operator_command_envelope(
        action: str,
        target: str,
        operator: str,
    ) -> tuple[str, str]:
        """Return byte-stable JSON and SHA-256 for an authenticated command.

        The authenticated operator is part of the envelope.  Reusing a chat
        ``message_id`` under a different identity is therefore a conflicting
        payload, even when the action text is identical.
        """

        canonical = json.dumps(
            {
                "action": str(action).strip().lower(),
                "operator": str(operator).strip(),
                "target": str(target).strip().lower(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _operator_row(
        self,
        conn: sqlite3.Connection,
        message_id: int,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM operator_operations WHERE message_id=?",
            (int(message_id),),
        ).fetchone()

    @staticmethod
    def _assert_operator_hash(row: sqlite3.Row, command_hash: str) -> None:
        if not hmac.compare_digest(str(row["command_hash"]), str(command_hash)):
            raise ControlDenied(
                "message_id reutilizado con un comando diferente",
                code="operator_message_conflict",
            )

    def reserve_operator_operation(
        self,
        *,
        message_id: int,
        action: str,
        target: str,
        operator: str,
    ) -> dict[str, Any]:
        """Persist intent idempotently before any external or local effect."""

        command_json, command_hash = self.operator_command_envelope(action, target, operator)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._operator_row(conn, message_id)
            if row is None:
                conn.execute(
                    "INSERT INTO operator_operations("
                    "message_id,command_hash,command_json,status,created_at"
                    ") VALUES(?,?,?,?,?)",
                    (int(message_id), command_hash, command_json, "intent", time.time()),
                )
                row = self._operator_row(conn, message_id)
            else:
                self._assert_operator_hash(row, command_hash)
            conn.commit()
        assert row is not None
        return dict(row)

    @staticmethod
    def _effect_payload(*, ok: bool, error: str | None) -> dict[str, Any]:
        return {"error": error, "ok": bool(ok)}

    def _apply_operator_action_conn(
        self,
        conn: sqlite3.Connection,
        *,
        action: str,
        target: str,
        operator: str,
    ) -> None:
        """Apply one command using the caller's ``BEGIN IMMEDIATE`` transaction."""

        now = time.time()
        if action == "approve":
            row = conn.execute(
                "SELECT status,expires_at FROM approvals WHERE approval_id=?",
                (target,),
            ).fetchone()
            if not row or row["status"] != "requested":
                raise ControlDenied("approval no está pendiente", code="approval_state")
            if float(row["expires_at"]) <= now:
                conn.execute(
                    "UPDATE approvals SET status='expired', decided_at=? WHERE approval_id=?",
                    (now, target),
                )
                raise ControlDenied("approval expirado", code="approval_expired")
            conn.execute(
                "UPDATE approvals SET status='approved', operator=?, decided_at=? "
                "WHERE approval_id=?",
                (str(operator), now, target),
            )
            return
        if action == "takeover":
            changed = conn.execute(
                "UPDATE sessions SET state='human_active', heartbeat_at=?, "
                "takeover_operator=?, takeover_until=? "
                "WHERE session_id=? AND state='takeover_requested'",
                (now, str(operator), now + 60, target),
            ).rowcount
            error = "takeover no está solicitado"
        elif action == "renew":
            changed = conn.execute(
                "UPDATE sessions SET heartbeat_at=?, takeover_until=? "
                "WHERE session_id=? AND state='human_active' AND takeover_operator=?",
                (now, now + 60, target, str(operator)),
            ).rowcount
            error = "takeover humano no coincide"
        elif action == "release":
            changed = conn.execute(
                "UPDATE sessions SET state='ready', heartbeat_at=?, "
                "takeover_operator=NULL, takeover_until=NULL "
                "WHERE session_id=? AND state IN ('takeover_requested','human_active') "
                "AND (takeover_operator IS NULL OR takeover_operator=?)",
                (now, target, str(operator)),
            ).rowcount
            error = "takeover no está activo"
        else:
            raise ValueError("acción desconocida")
        if changed != 1:
            raise ControlDenied(error, code="takeover_state")

    def apply_operator_operation(
        self,
        *,
        message_id: int,
        action: str,
        target: str,
        operator: str,
    ) -> dict[str, Any]:
        """Atomically commit the control mutation and its durable effect outbox.

        A retry after the transaction commits returns the stored effect and never
        executes the control mutation again.
        """

        _command_json, command_hash = self.operator_command_envelope(action, target, operator)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._operator_row(conn, message_id)
            if row is None:
                conn.rollback()
                raise ControlDenied("intent inexistente", code="operator_intent_missing")
            self._assert_operator_hash(row, command_hash)
            if row["status"] in {"effect", "completion"}:
                effect = json.loads(str(row["effect_json"]))
                conn.commit()
                return {**effect, "new_effect": False, "status": str(row["status"])}
            error: str | None = None
            ok = False
            try:
                self._apply_operator_action_conn(
                    conn,
                    action=str(action).lower(),
                    target=str(target).lower(),
                    operator=str(operator),
                )
                ok = True
            except (ControlDenied, ValueError) as exc:
                error = f"{type(exc).__name__}:{exc}"
            effect = self._effect_payload(ok=ok, error=error)
            effect_json = json.dumps(effect, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            conn.execute(
                "UPDATE operator_operations SET status='effect', effect_json=?, effect_at=? "
                "WHERE message_id=? AND status='intent'",
                (effect_json, time.time(), int(message_id)),
            )
            conn.commit()
        return {**effect, "new_effect": True, "status": "effect"}

    def complete_operator_operation(
        self,
        *,
        message_id: int,
        action: str,
        target: str,
        operator: str,
    ) -> dict[str, Any]:
        """Mark a delivered outbox completion idempotently."""

        _command_json, command_hash = self.operator_command_envelope(action, target, operator)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._operator_row(conn, message_id)
            if row is None:
                conn.rollback()
                raise ControlDenied("intent inexistente", code="operator_intent_missing")
            self._assert_operator_hash(row, command_hash)
            if row["status"] == "intent" or not row["effect_json"]:
                conn.rollback()
                raise ControlDenied("efecto todavía no persistido", code="operator_effect_missing")
            if row["status"] == "effect":
                conn.execute(
                    "UPDATE operator_operations SET status='completion', completed_at=? "
                    "WHERE message_id=? AND status='effect'",
                    (time.time(), int(message_id)),
                )
            conn.commit()
            effect = json.loads(str(row["effect_json"]))
        return {**effect, "status": "completion"}

    def operator_operation(self, message_id: int) -> dict[str, Any] | None:
        """Expose journal state for health checks and byte-bound tests."""

        with self._connect() as conn:
            row = self._operator_row(conn, message_id)
        return dict(row) if row is not None else None

    def action_hash(self, tool: str, arguments: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            {"tool": str(tool), "arguments": dict(arguments)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hmac.new(self._key, canonical, hashlib.sha256).hexdigest()

    @staticmethod
    def _canonical_url(value: Any) -> str:
        raw = str(value or "").strip()
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return raw
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower().rstrip(".")
        port = parsed.port
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            host = f"{host}:{port}"
        path = parsed.path or "/"
        return urlunsplit((scheme, host, path, parsed.query, ""))

    def remote_effect_key(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        risk_context: Mapping[str, Any] | None = None,
    ) -> str:
        """Canonical logical-effect key aligned with runtime normalization."""

        name = str(tool).strip().lower()
        normalized = dict(arguments)
        normalized.pop("approval_id", None)
        if name == "browser_action":
            name = str(normalized.get("action", "")).strip().lower()
        if name in {"browse", "navigate", "open_tab", "save_url"}:
            normalized = {"url": self._canonical_url(normalized.get("url", ""))}
        elif name == "press_key":
            effective = str(normalized.get("value") or normalized.get("target") or "").strip().lower()
            normalized = {"key": effective}
        elif name == "click":
            context = dict(risk_context or {})
            normalized = {
                "selector": str(normalized.get("selector") or normalized.get("target") or "").strip(),
                "href": self._canonical_url(context.get("href", "")),
                "form_action": self._canonical_url(context.get("formAction", "")),
            }
        elif name == "set_cookie":
            normalized = {
                "name": str(normalized.get("name") or normalized.get("target") or "").strip(),
                "value": "[secret-present]" if normalized.get("value") else "[empty]",
                "url": self._canonical_url(normalized.get("url", "")),
            }
        else:
            normalized = {
                str(key): value.strip() if isinstance(value, str) else value
                for key, value in normalized.items()
                if key != "action"
            }
            if name == "type_text" and (
                _ACCOUNT.search(str(normalized.get("selector") or normalized.get("target") or ""))
                or str((risk_context or {}).get("type", "")).lower() == "password"
                or str((risk_context or {}).get("autocomplete", "")).lower()
                   in {"current-password", "new-password", "one-time-code"}
            ):
                normalized["value"] = "[secret-present]" if normalized.get("value") else "[empty]"
        canonical = json.dumps(
            {"arguments": normalized, "tool": name},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hmac.new(self._key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    def ensure_session(self, *, agent: str, pid: int) -> str:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            stale = conn.execute(
                "SELECT session_id FROM sessions WHERE agent=? AND pid=? AND state!='closed'",
                (str(agent), int(pid)),
            ).fetchall()
            for row in stale:
                conn.execute(
                    "UPDATE approvals SET status='cancelled',error_code='process_identity_replaced' "
                    "WHERE session_id=? AND status IN ('requested','approved')",
                    (row["session_id"],),
                )
            conn.execute(
                "UPDATE sessions SET state='closed',closed_at=?,heartbeat_at=? "
                "WHERE agent=? AND pid=? AND state!='closed'",
                (now, now, str(agent), int(pid)),
            )
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
        effect_key = self.remote_effect_key(tool, arguments, risk_context=risk_context)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE approvals SET status='expired',decided_at=? "
                "WHERE status IN ('requested','approved') AND expires_at<=?",
                (now, now),
            )
            session = conn.execute(
                "SELECT agent FROM sessions WHERE session_id=? AND state!='closed'",
                (session_id,),
            ).fetchone()
            if session is None:
                conn.rollback()
                raise ControlDenied("sesión cerrada o inexistente", code="session_closed")
            effect_scope = str(session["agent"])
            unresolved = conn.execute(
                "SELECT operation_id FROM remote_effect_operations "
                "WHERE effect_scope=? "
                "AND status IN ('armed','observed','indeterminate') LIMIT 1",
                (effect_scope,),
            ).fetchone()
            active = conn.execute(
                "SELECT approval_id FROM approvals WHERE effect_scope=? AND effect_key=? "
                "AND status IN ('requested','approved','executing') LIMIT 1",
                (effect_scope, effect_key),
            ).fetchone()
            if unresolved is not None or active is not None:
                conn.rollback()
                raise ControlDenied(
                    "efecto lógico ya está activo o requiere reconciliación",
                    code="remote_effect_reconciliation_required",
                )
            try:
                conn.execute(
                    "INSERT INTO approvals("
                    "approval_id,session_id,action_class,action_hash,status,requested_at,expires_at,effect_scope,effect_key"
                    ") VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        approval_id, session_id, action_class, digest, "requested",
                        now, now + ttl, effect_scope, effect_key,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ControlDenied(
                    "efecto lógico reservado concurrentemente",
                    code="remote_effect_reconciliation_required",
                ) from exc
            conn.commit()
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
        arm_remote_effect: bool = False,
    ) -> ActionPermit:
        action_class = classify_action(tool, arguments, risk_context=risk_context)
        digest = self.action_hash(tool, arguments)
        expected_effect_key = self.remote_effect_key(
            tool, arguments, risk_context=risk_context,
        )
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
            if not hmac.compare_digest(str(approval["effect_key"] or ""), expected_effect_key):
                conn.rollback()
                raise ControlDenied(
                    "approval no coincide con el efecto DOM/remoto actual",
                    code="approval_mismatch",
                )
            unresolved = conn.execute(
                "SELECT 1 FROM remote_effect_operations WHERE effect_scope=? AND effect_key=? "
                "AND status IN ('armed','observed','indeterminate') LIMIT 1",
                (approval["effect_scope"], approval["effect_key"]),
            ).fetchone()
            if unresolved is not None:
                conn.rollback()
                raise ControlDenied(
                    "efecto lógico requiere reconciliación",
                    code="remote_effect_reconciliation_required",
                )
            changed = conn.execute(
                "UPDATE approvals SET status='executing' WHERE approval_id=? AND status='approved'",
                (approval_id,),
            ).rowcount
            if changed != 1:
                conn.rollback()
                raise ControlDenied("approval reservado concurrentemente", code="approval_replay")
            if arm_remote_effect:
                envelope = self._remote_effect_envelope(tool, str(approval["effect_key"]))
                try:
                    conn.execute(
                        "INSERT INTO remote_effect_operations("
                        "operation_id,session_id,action_hash,tool,arguments_json,status,armed_at,effect_scope,effect_key"
                        ") VALUES(?,?,?,?,?,'armed',?,?,?)",
                        (
                            approval_id,
                            session_id,
                            digest,
                            str(tool),
                            envelope,
                            time.time(),
                            approval["effect_scope"],
                            approval["effect_key"],
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    raise ControlDenied(
                        "otro efecto remoto del actor requiere reconciliación",
                        code="remote_effect_reconciliation_required",
                    ) from exc
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

    @staticmethod
    def _remote_effect_envelope(tool: str, effect_key: str) -> str:
        return json.dumps(
            {
                "arguments": {"redacted": True},
                "effect_key": str(effect_key),
                "tool": str(tool),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def arm_remote_effect(
        self,
        permit: ActionPermit,
        *,
        tool: str,
        arguments: Mapping[str, Any],
    ) -> str | None:
        """Persist a conservative indeterminate marker before a remote effect.

        Only approval-bound actions need this journal.  A crash at any point
        after this write leaves a durable non-retryable record for human
        reconciliation instead of silently replaying a possibly-real effect.
        """

        if not permit.approval_id:
            return None
        operation_id = permit.approval_id
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            approval = conn.execute(
                "SELECT status,session_id,action_hash,effect_scope,effect_key "
                "FROM approvals WHERE approval_id=?",
                (operation_id,),
            ).fetchone()
            if (
                not approval
                or approval["status"] != "executing"
                or approval["session_id"] != permit.session_id
                or not hmac.compare_digest(str(approval["action_hash"]), permit.action_hash)
            ):
                conn.rollback()
                raise ControlDenied("approval no está reservado para el efecto", code="approval_state")
            envelope = self._remote_effect_envelope(tool, str(approval["effect_key"]))
            row = conn.execute(
                "SELECT action_hash,status FROM remote_effect_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is not None:
                conn.rollback()
                if not hmac.compare_digest(str(row["action_hash"]), permit.action_hash):
                    raise ControlDenied("operación remota conflictiva", code="remote_effect_conflict")
                raise ControlDenied(
                    "efecto remoto requiere reconciliación; no se reintenta",
                    code="remote_effect_indeterminate",
                )
            try:
                conn.execute(
                    "INSERT INTO remote_effect_operations("
                    "operation_id,session_id,action_hash,tool,arguments_json,status,armed_at,effect_scope,effect_key"
                    ") VALUES(?,?,?,?,?,'armed',?,?,?)",
                    (
                        operation_id,
                        permit.session_id,
                        permit.action_hash,
                        str(tool),
                        envelope,
                        time.time(),
                        approval["effect_scope"],
                        approval["effect_key"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ControlDenied(
                    "otro efecto remoto del actor requiere reconciliación",
                    code="remote_effect_reconciliation_required",
                ) from exc
            conn.commit()
        return operation_id

    def observe_remote_effect(
        self,
        operation_id: str | None,
        *,
        ok: bool,
        error_code: str | None = None,
    ) -> None:
        if operation_id is None:
            return
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE remote_effect_operations SET status='observed',observed_at=?,ok=?,error_code=? "
                "WHERE operation_id=? AND status='armed'",
                (time.time(), int(bool(ok)), error_code, operation_id),
            ).rowcount
        if changed != 1:
            raise ControlDenied("estado remoto no observable", code="remote_effect_state")

    def finalize_remote_effect(
        self,
        permit: ActionPermit,
        operation_id: str | None,
        *,
        ok: bool,
        error_code: str | None = None,
    ) -> None:
        if operation_id is None:
            self.finish(permit, ok=ok, error_code=error_code)
            return
        approval_status = "executed" if ok else "failed"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed_effect = conn.execute(
                "UPDATE remote_effect_operations SET status='completed',completed_at=?,ok=?,error_code=? "
                "WHERE operation_id=? AND status='observed'",
                (time.time(), int(bool(ok)), error_code, operation_id),
            ).rowcount
            changed_approval = conn.execute(
                "UPDATE approvals SET status=?,executed_at=?,error_code=? "
                "WHERE approval_id=? AND status='executing'",
                (approval_status, time.time(), error_code, permit.approval_id),
            ).rowcount
            if changed_effect != 1 or changed_approval != 1:
                conn.rollback()
                raise ControlDenied("finalización remota no atómica", code="remote_effect_state")
            conn.commit()

    def mark_remote_effect_indeterminate(
        self,
        permit: ActionPermit,
        operation_id: str | None,
        *,
        error_code: str,
    ) -> None:
        if operation_id is None:
            self.finish(permit, ok=False, error_code=error_code)
            return
        bounded = f"indeterminate:{str(error_code)[:160]}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed_effect = conn.execute(
                "UPDATE remote_effect_operations SET status='indeterminate',completed_at=?,error_code=? "
                "WHERE operation_id=? AND status IN ('armed','observed')",
                (time.time(), bounded, operation_id),
            ).rowcount
            changed_approval = conn.execute(
                "UPDATE approvals SET status='failed',executed_at=?,error_code=? "
                "WHERE approval_id=? AND status='executing'",
                (time.time(), bounded, permit.approval_id),
            ).rowcount
            if changed_effect != 1 or changed_approval != 1:
                conn.rollback()
                raise ControlDenied("marcador indeterminado no persistió", code="remote_effect_state")
            conn.commit()

    def remote_effect_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM remote_effect_operations WHERE operation_id=?",
                (str(operation_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def reconcile_remote_effect(
        self,
        operation_id: str,
        *,
        effect_occurred: bool,
        operator: str,
    ) -> dict[str, Any]:
        """External authenticated reconciliation; never exposed as an MCP tool."""

        if not str(operator).strip():
            raise ValueError("operator requerido")
        status = "executed" if effect_occurred else "failed"
        code = "reconciled_effect" if effect_occurred else "reconciled_no_effect"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,ok,reconciled_by FROM remote_effect_operations WHERE operation_id=?",
                (str(operation_id),),
            ).fetchone()
            if (
                row is not None
                and row["status"] == "completed"
                and row["reconciled_by"] == str(operator)
                and bool(row["ok"]) is bool(effect_occurred)
            ):
                conn.commit()
                return {
                    "operation_id": str(operation_id),
                    "effect_occurred": bool(effect_occurred),
                    "new_effect": False,
                }
            if row is None or row["status"] not in {"armed", "observed", "indeterminate"}:
                conn.rollback()
                raise ControlDenied("operación remota no reconciliable", code="remote_effect_state")
            changed_effect = conn.execute(
                "UPDATE remote_effect_operations SET status='completed',completed_at=?,ok=?,"
                "error_code=?,reconciled_by=? WHERE operation_id=? "
                "AND status IN ('armed','observed','indeterminate')",
                (time.time(), int(effect_occurred), code, str(operator), str(operation_id)),
            ).rowcount
            changed_approval = conn.execute(
                "UPDATE approvals SET status=?,executed_at=?,error_code=? "
                "WHERE approval_id=? AND status IN ('executing','failed')",
                (status, time.time(), code, str(operation_id)),
            ).rowcount
            if changed_effect != 1 or changed_approval != 1:
                conn.rollback()
                raise ControlDenied("reconciliación remota no atómica", code="remote_effect_state")
            conn.commit()
        return {
            "operation_id": str(operation_id),
            "effect_occurred": bool(effect_occurred),
            "new_effect": True,
        }

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
