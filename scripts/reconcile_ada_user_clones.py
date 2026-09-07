#!/usr/bin/env python3
"""Reconcile isolated SEAL clone processes from live user-agent assignments.

The database assignment is the source of truth.  This controller only creates
or starts the per-pair runtime; it never deletes identities, tokens, sessions,
or user data.  A revoked/non-basic pair is stopped and disabled.  The chat
server still re-validates role and assignment on every inbox/outbox request.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import asyncpg


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))
sys.path.insert(0, str(ROOT / "scripts"))

from operational_db_credentials import service_pg_dsn  # noqa: E402
from provision_user_agent_clone_session import provision, token_path  # noqa: E402


CLONE_AGENTS = ("ADA", "ALICE", "FABLE", "JARVIS", "NEXUS")
TOKEN_RE = re.compile(
    r"^\.agent_session_token_(ADA|ALICE|FABLE|JARVIS|NEXUS)-u([1-9][0-9]*)$"
)
STATE_DIR = Path.home() / ".local" / "state" / "seal" / "user-clone-reconciler"
LOCK_PATH = STATE_DIR / "ada.lock"
RECEIPT_PATH = STATE_DIR / "ada.json"
HEALTH_ROOT = Path.home() / ".local/state/seal/user-clones"
USER_DATA_ROOT = Path.home() / ".local/share/seal/users"
TECHNICAL_PROJECTION = (
    Path.home() / ".local/share/seal/user-clone-projections/technical.sqlite3"
)
CLAUDE_U116_MARKER = Path("/etc/seal/claude-u116.enabled")


def technical_projection_for_pair(
    agent: str,
    user_id: int,
    *,
    default: Path = TECHNICAL_PROJECTION,
) -> Path:
    instance_projection = default.with_name(
        f"technical_{str(agent).upper()}-u{int(user_id)}.sqlite3"
    )
    return instance_projection if instance_projection.is_file() else default


@dataclass(frozen=True)
class Action:
    agent: str
    user_id: int
    operation: str
    detail: str


def unit_name(agent: str, user_id: int) -> str:
    normalized = str(agent).upper()
    if normalized == "ADA":
        return f"seal-ada-user-clone@{int(user_id)}.service"
    return f"seal-user-clone@{normalized}-u{int(user_id)}.service"


async def eligible_pairs(conn: asyncpg.Connection) -> set[tuple[str, int]]:
    rows = await conn.fetch(
        """SELECT DISTINCT upper(ua.agent) agent,u.id
             FROM soul_v3.chat_users u
             JOIN soul_v3.user_agents ua ON ua.user_id=u.id
            WHERE lower(u.role)='basic' AND upper(ua.agent)=ANY($1::text[])
            ORDER BY upper(ua.agent),u.id""",
        list(CLONE_AGENTS),
    )
    return {(str(row["agent"]), int(row["id"])) for row in rows}


def known_pairs(token_dir: Path) -> set[tuple[str, int]]:
    result: set[tuple[str, int]] = set()
    if not token_dir.exists():
        return result
    for path in token_dir.iterdir():
        match = TOKEN_RE.fullmatch(path.name)
        if match:
            result.add((match.group(1), int(match.group(2))))
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _loaded_projection_digest(health_root: Path, agent: str, user_id: int) -> str:
    instance = f"{agent}-u{user_id}"
    path = health_root / instance / f"{instance}.health.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8"))["technical_projection_sha256"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def prepare_pair_directories(
    agent: str,
    user_id: int,
    *,
    user_data_root: Path = USER_DATA_ROOT,
    health_root: Path = HEALTH_ROOT,
) -> tuple[Path, Path]:
    """Create the private bind-mount roots required by the start preflight.

    The container initializes the files inside these pair-scoped roots, but
    ``ExecStartPre`` intentionally verifies that the roots already exist and
    are private.  Creating them in ``ExecStart`` is therefore too late.
    """
    normalized = str(agent).strip().upper()
    if normalized not in CLONE_AGENTS or int(user_id) <= 0:
        raise ValueError("invalid isolated clone pair")
    data_dir = user_data_root / f"u{int(user_id)}" / "instances" / normalized
    state_dir = health_root / f"{normalized}-u{int(user_id)}"
    for path in (data_dir, state_dir):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    return data_dir, state_dir


async def systemctl(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "systemctl",
        "--user",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return int(proc.returncode or 0), stdout.decode("utf-8", "replace").strip()


async def reconcile(
    dsn: str,
    *,
    token_dir: Path,
    technical_projection: Path = TECHNICAL_PROJECTION,
    health_root: Path = HEALTH_ROOT,
    user_data_root: Path = USER_DATA_ROOT,
    provision_pair: Callable[[str, str, int], Awaitable[tuple[str, bool]]] = provision,
    run_systemctl: Callable[..., Awaitable[tuple[int, str]]] = systemctl,
    claude_u116_marker: Path = CLAUDE_U116_MARKER,
) -> dict[str, object]:
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        eligible = await eligible_pairs(conn)
    finally:
        await conn.close()

    if not technical_projection.is_file():
        raise RuntimeError(f"technical projection missing: {technical_projection}")
    projection_digest = _sha256(technical_projection)
    known = known_pairs(token_dir)
    actions: list[Action] = []
    errors: list[str] = []
    pair_projection_digests: dict[str, str] = {}

    for agent, user_id in sorted(eligible):
        try:
            instance, rotated = await provision_pair(dsn, agent, user_id)
            prepare_pair_directories(
                agent,
                user_id,
                user_data_root=user_data_root,
                health_root=health_root,
            )
            unit = unit_name(agent, user_id)
            active_rc, _ = await run_systemctl("is-active", unit)
            enabled_rc, _ = await run_systemctl("is-enabled", unit)
            pair_projection_digest = _sha256(
                technical_projection_for_pair(
                    agent,
                    user_id,
                    default=technical_projection,
                )
            )
            pair_projection_digests[f"{agent}-u{user_id}"] = pair_projection_digest
            if agent == "JARVIS" and user_id == 116 and not claude_u116_marker.is_file():
                active_rc, _ = await run_systemctl("is-active", unit)
                enabled_rc, _ = await run_systemctl("is-enabled", unit)
                if active_rc == 0 or enabled_rc == 0:
                    rc, output = await run_systemctl("disable", "--now", unit)
                    if rc != 0:
                        raise RuntimeError(f"systemctl security HOLD failed: {output}")
                actions.append(
                    Action(
                        agent,
                        user_id,
                        "hold_activation",
                        "Claude key and signed-consent activation marker absent",
                    )
                )
                continue
            if active_rc != 0 or enabled_rc != 0:
                rc, output = await run_systemctl("enable", "--now", unit)
                if rc != 0:
                    raise RuntimeError(f"systemctl enable --now failed: {output}")
                actions.append(Action(agent, user_id, "start", instance))
            elif rotated or (
                _loaded_projection_digest(health_root, agent, user_id)
                and _loaded_projection_digest(health_root, agent, user_id) != pair_projection_digest
            ):
                rc, output = await run_systemctl("restart", unit)
                if rc != 0:
                    raise RuntimeError(f"systemctl restart failed: {output}")
                reason = "restart_token_rotated" if rotated else "restart_projection_changed"
                actions.append(Action(agent, user_id, reason, instance))
            else:
                actions.append(Action(agent, user_id, "healthy", instance))
        except Exception as exc:  # fail the run but continue protecting other pairs
            errors.append(f"{agent}-u{user_id}: {type(exc).__name__}: {exc}")

    # Revocation is deliberately non-destructive.  The server also checks the
    # live role/assignment, so even a valid residual token cannot cross scope.
    for agent, user_id in sorted(known - eligible):
        unit = unit_name(agent, user_id)
        rc, output = await run_systemctl("disable", "--now", unit)
        if rc != 0 and "not loaded" not in output.lower():
            errors.append(f"{agent}-u{user_id}: disable failed: {output}")
        else:
            actions.append(Action(agent, user_id, "stop_revoked", "data retained"))

    receipt: dict[str, object] = {
        "schema": "seal.user-clone-reconcile.v2",
        "agents": list(CLONE_AGENTS),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "technical_projection_sha256": projection_digest,
        "technical_projection_sha256_by_pair": pair_projection_digests,
        "eligible_pairs": [f"{agent}-u{uid}" for agent, uid in sorted(eligible)],
        "known_pairs": [f"{agent}-u{uid}" for agent, uid in sorted(known | eligible)],
        "actions": [action.__dict__ for action in actions],
        "errors": errors,
        "ok": not errors,
    }
    return receipt


def write_receipt(receipt: dict[str, object], path: Path = RECEIPT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        tmp.unlink(missing_ok=True)


async def async_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one reconciliation")
    args = parser.parse_args()
    if not args.once:
        parser.error("only --once is supported; use the systemd timer for repetition")

    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("status=skipped reason=already_running")
            return 0
        dsn = service_pg_dsn(
            "SEAL_MEMORY_ADMIN_PG_DSN",
            allow_private_transition=True,
            transition_database="seal_memory",
        )
        receipt = await reconcile(dsn, token_dir=ROOT / "messages")
        write_receipt(receipt)
        print(json.dumps(receipt, sort_keys=True))
        return 0 if bool(receipt["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
