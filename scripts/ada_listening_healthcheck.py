#!/usr/bin/env python3
"""Deterministic guard for William↔ADA public and private communication."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from messages import ada_codex_remote_bridge as bridge
from messages import ada_codex_poller as poller
from scripts import seal_self_repair


SERVICES = (
    "ada-codex-remote-bridge.service",
    "seal-ada-codex-poller.service",
    "seal-ada-codex-stream-relay.service",
    "seal-ada-codex-autostart.service",
)
SERVICE_REPAIR_ACTIONS = {
    "ada-codex-remote-bridge.service": "bridge",
    "seal-ada-codex-poller.service": "visible_poller",
    "seal-ada-codex-stream-relay.service": "stream_relay",
    "seal-ada-codex-autostart.service": "visible_terminal",
}
STATE_DIR = Path(os.environ.get("ADA_CODEX_STATE_DIR", Path.home() / ".local/state/seal"))
REPORT_PATH = STATE_DIR / "ada_listening_guard_report.json"
MONITOR_STATE_PATH = STATE_DIR / "ada_listening_monitor_state.json"
REPAIR_STATE_PATH = STATE_DIR / "ada_listening_repair_state.json"
ACK_SLA_SECONDS = int(os.environ.get("ADA_LISTENING_ACK_SLA_SECONDS", "20"))
PROGRESS_SLA_SECONDS = int(
    os.environ.get("ADA_LISTENING_PROGRESS_SLA_SECONDS", "90")
)
REPAIR_COOLDOWN_SECONDS = int(
    os.environ.get("ADA_LISTENING_REPAIR_COOLDOWN_SECONDS", "120")
)
ACTIVE_ROUTE_BUSY_MAX_AGE_SECONDS = int(
    os.environ.get("ADA_LISTENING_ACTIVE_ROUTE_MAX_AGE_SECONDS", "900")
)
WILLIAM_BACKLOG_HOURS = int(
    os.environ.get("ADA_LISTENING_WILLIAM_BACKLOG_HOURS", "24")
)


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, timeout=20
    )


def service_state(name: str) -> dict[str, Any]:
    active = _systemctl("is-active", name)
    enabled = _systemctl("is-enabled", name)
    return {
        "active": active.returncode == 0 and active.stdout.strip() == "active",
        "enabled": enabled.returncode == 0 and enabled.stdout.strip() == "enabled",
        "active_raw": active.stdout.strip() or active.stderr.strip(),
        "enabled_raw": enabled.stdout.strip() or enabled.stderr.strip(),
    }


def routing_contract() -> dict[str, bool]:
    return {
        "william_named": bridge.should_route_to_codex("web_chat", "ada revisa", "William"),
        "jarvis_named": bridge.should_route_to_codex("web_chat", "ADA: revisa", "JARVIS"),
        "alice_mentioned": bridge.should_route_to_codex(
            "web_chat", "Necesito ayuda de ADA con deploy", "ALICE"
        ),
        "word_boundary": not bridge.should_route_to_codex(
            "web_chat", "cada intento cuenta", "William"
        ),
        "private_dm": bridge.should_route_to_codex(
            "dm:ada:william", "revisa esto", "William"
        ),
        "team_allowlist": {"jarvis", "alice", "nexus", "dum"}.issubset(poller.INJECT_FROM),
    }


def delivery_route_state(
    states: dict[str, dict[str, Any]], lease: dict[str, Any]
) -> dict[str, Any]:
    """Require at least one live writer instead of every redundant writer.

    The visible TUI poller deliberately releases its lease after an
    unacknowledged submit so the headless bridge can take over.  Treating that
    failover state as a reason to restart the poller every minute created a
    restart loop without improving William's delivery path.
    """
    bridge_state = states.get("ada-codex-remote-bridge.service", {})
    terminal_listener_ok = bool(lease.get("ok"))
    headless_bridge_ok = bool(
        bridge_state.get("active") and bridge_state.get("enabled")
    )
    return {
        "ok": terminal_listener_ok or headless_bridge_ok,
        "terminal_listener_ok": terminal_listener_ok,
        "headless_bridge_ok": headless_bridge_ok,
        "mode": (
            "terminal"
            if terminal_listener_ok
            else ("headless_failover" if headless_bridge_ok else "unavailable")
        ),
    }


def listener_lease() -> dict[str, Any]:
    try:
        data = json.loads(poller.LISTENER_HEALTH_FILE.read_text(encoding="utf-8"))
        age = max(0.0, time.time() - float(data.get("heartbeat_epoch", 0)))
        return {
            "ok": data.get("status") == "running" and age <= bridge.TERMINAL_LISTENER_LEASE_SECONDS,
            "age_seconds": round(age, 3),
            "pid": data.get("pid"),
            "last_ack_id": data.get("last_ack_id"),
            "status": data.get("status"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def active_route_state() -> dict[str, Any]:
    """Detect a route marker that outlived its real Codex turn."""
    path = poller.ACTIVE_TASK_FILE
    if not path.exists():
        return {"ok": True, "present": False}
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
        activated = datetime.fromisoformat(
            str(task["activated_at"]).replace("Z", "+00:00")
        )
        age = max(0.0, (datetime.now(timezone.utc) - activated).total_seconds())
        task_id = str(task.get("id") or "")
        response_exists = bool(task_id and (poller.RESPONSES_DIR / f"{task_id}.json").exists())
        busy = poller.codex_is_busy()
        status = str(task.get("status") or "legacy_active")
        if status in {"abandoned_unaccepted", "suppressed", "delivered", "published"}:
            return {
                "ok": True,
                "present": True,
                "task_id": task_id,
                "channel": task.get("channel"),
                "status": status,
                "age_seconds": round(age, 3),
                "terminal": True,
            }
        stale_pending = status == "pending_submit" and age > poller.PENDING_TASK_MAX_AGE_SECONDS
        stale_idle = (
            status != "pending_submit"
            and not busy
            and not response_exists
            and age > 30
        )
        # ``codex_is_busy`` is a lifecycle observation, not an infinite lease.
        # A crashed/stuck turn used to remain GREEN forever as long as the
        # latest lifecycle event lacked task_complete. Bound that ambiguity.
        stale_busy = (
            status != "pending_submit"
            and busy
            and not response_exists
            and age > ACTIVE_ROUTE_BUSY_MAX_AGE_SECONDS
        )
        return {
            "ok": not (stale_pending or stale_idle or stale_busy),
            "present": True,
            "task_id": task_id,
            "channel": task.get("channel"),
            "status": status,
            "turn_id": task.get("turn_id"),
            "age_seconds": round(age, 3),
            "codex_busy": busy,
            "response_exists": response_exists,
            "stale_pending": stale_pending,
            "stale_idle": stale_idle,
            "stale_busy": stale_busy,
            "busy_max_age_seconds": ACTIVE_ROUTE_BUSY_MAX_AGE_SECONDS,
        }
    except Exception as exc:
        return {"ok": False, "present": True, "error": str(exc)}


def quarantine_stale_route(route: dict[str, Any]) -> str | None:
    """Preserve and remove only a proven stale routing marker."""
    if route.get("ok") or not poller.ACTIVE_TASK_FILE.exists():
        return None
    orphaned = poller.BRIDGE_QUEUE_DIR / "orphaned"
    orphaned.mkdir(parents=True, exist_ok=True)
    target = orphaned / f"active_task_weekly_repair_{datetime.now():%Y%m%dT%H%M%S}.json"
    os.replace(poller.ACTIVE_TASK_FILE, target)
    return str(target)


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


async def pending_eligible(dsn: str, cursor: int) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS count,
                   MIN(id)::bigint AS oldest_id,
                   MAX(id)::bigint AS newest_id,
                   EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))::float AS oldest_age_seconds
              FROM soul_v3.chat_messages
             WHERE id > $1
               AND (
                    (channel='web_chat'
                     AND LOWER(sender_name)=ANY($2::text[])
                     AND content ~* '\\mada\\M')
                    OR
                    (channel='dm:ada:william'
                     AND LOWER(sender_name) IN ('william','henry'))
               )
            """,
            cursor,
            sorted(bridge.INJECT_FROM),
        )
        return dict(row)
    finally:
        await conn.close()


async def current_chat_id(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(
            await conn.fetchval(
                "SELECT COALESCE(MAX(id), 0) FROM soul_v3.chat_messages"
            )
            or 0
        )
    finally:
        await conn.close()


async def william_reply_backlog(
    dsn: str, *, horizon_hours: int = WILLIAM_BACKLOG_HOURS
) -> dict[str, Any]:
    """Find William messages lacking an exact durable ADA reply.

    This query is intentionally independent of poller cursors and the monitor
    watermark. A cursor may explain delivery progress; it may never erase an
    unanswered William source from the communication health gate.
    """
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            WITH sources AS (
                SELECT m.id,
                       m.channel,
                       m.created_at,
                       COALESCE(m.metadata->>'legacy_id', m.id::text) AS source_id
                  FROM soul_v3.chat_messages m
                 WHERE m.created_at > NOW() - make_interval(hours => $1)
                   AND LOWER(m.sender_name) = 'william'
                   AND (
                        m.channel = 'dm:ada:william'
                        OR (m.channel = 'web_chat' AND m.content ~* '\\mada\\M')
                   )
            ),
            unanswered AS (
                SELECT s.*
                  FROM sources s
                 WHERE NOT EXISTS (
                    SELECT 1
                      FROM soul_v3.chat_messages a
                     WHERE UPPER(a.sender_name) = 'ADA'
                       AND a.channel = s.channel
                       AND a.metadata->>'in_reply_to' = s.source_id
                 )
            )
            SELECT COUNT(*)::int AS count,
                   MIN(id)::bigint AS oldest_id,
                   EXTRACT(
                       EPOCH FROM (NOW() - MIN(created_at))
                   )::float AS oldest_age_seconds
              FROM unanswered
            """,
            max(1, int(horizon_hours)),
        )
        result = dict(row)
        result["horizon_hours"] = max(1, int(horizon_hours))
        result["ok"] = int(result.get("count") or 0) == 0
        return result
    finally:
        await conn.close()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def monitor_start_id(dsn: str) -> int:
    """Persist a deployment watermark so historical gaps never trigger floods."""
    try:
        payload = json.loads(MONITOR_STATE_PATH.read_text(encoding="utf-8"))
        value = int(payload["start_id"])
        if value >= 0:
            return value
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        pass
    value = asyncio.run(current_chat_id(dsn))
    _atomic_json(
        MONITOR_STATE_PATH,
        {
            "schema": "seal.ada.listening-monitor.v1",
            "start_id": value,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return value


def advance_monitor_start_id(next_start_id: int) -> None:
    """Advance only past sources that already received a durable reply."""
    try:
        current = json.loads(MONITOR_STATE_PATH.read_text(encoding="utf-8"))
        current_start = int(current.get("start_id", 0))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        current = {"schema": "seal.ada.listening-monitor.v1"}
        current_start = 0
    current.update({
        "start_id": max(current_start, int(next_start_id)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    _atomic_json(MONITOR_STATE_PATH, current)


async def reply_coverage(
    dsn: str,
    start_id: int,
    ack_sla_seconds: int = ACK_SLA_SECONDS,
    progress_sla_seconds: int = PROGRESS_SLA_SECONDS,
) -> dict[str, Any]:
    """Measure actual William→ADA reply coverage, independent of cursors/busy."""
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            WITH sources AS (
                SELECT m.id,
                       m.channel,
                       m.created_at,
                       COALESCE(m.metadata->>'legacy_id', m.id::text) AS source_id
                  FROM soul_v3.chat_messages m
                 WHERE m.id > $1
                   AND LOWER(m.sender_name) = 'william'
                   AND (
                        m.channel = 'dm:ada:william'
                        OR (m.channel = 'web_chat' AND m.content ~* '\\mada\\M')
                   )
            )
            SELECT s.id,
                   s.channel,
                   s.source_id,
                   EXTRACT(EPOCH FROM (NOW() - s.created_at))::float AS age_seconds,
                   reply.id AS reply_id,
                   EXTRACT(EPOCH FROM (reply.created_at - s.created_at))::float
                       AS reply_latency_seconds,
                   substantive.id AS substantive_reply_id,
                   EXTRACT(EPOCH FROM (substantive.created_at - s.created_at))::float
                       AS substantive_latency_seconds
              FROM sources s
              LEFT JOIN LATERAL (
                    SELECT a.id, a.created_at
                      FROM soul_v3.chat_messages a
                     WHERE UPPER(a.sender_name) = 'ADA'
                       AND a.channel = s.channel
                       AND a.metadata->>'in_reply_to' = s.source_id
                     ORDER BY a.created_at ASC
                     LIMIT 1
              ) reply ON TRUE
              LEFT JOIN LATERAL (
                    SELECT a.id, a.created_at
                      FROM soul_v3.chat_messages a
                     WHERE UPPER(a.sender_name) = 'ADA'
                       AND a.channel = s.channel
                       AND a.metadata->>'in_reply_to' = s.source_id
                       AND a.content NOT ILIKE 'ADA recibió tu mensaje #%'
                       AND a.content NOT ILIKE 'ADA recibio tu mensaje #%'
                       AND a.content NOT ILIKE 'Sí, Dadito. Te leí por DM.%'
                       AND a.content NOT ILIKE 'Sí, Dadito. Te leí en webchat.%'
                     ORDER BY a.created_at ASC
                     LIMIT 1
              ) substantive ON TRUE
             ORDER BY s.id ASC
            """,
            start_id,
        )
    finally:
        await conn.close()

    channels = {
        "web_chat": {"total": 0, "answered": 0, "unanswered": 0},
        "dm:ada:william": {"total": 0, "answered": 0, "unanswered": 0},
    }
    overdue: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    late: list[dict[str, Any]] = []
    ack_only_overdue: list[dict[str, Any]] = []
    max_latency = 0.0
    max_source_id = start_id
    for raw in rows:
        row = dict(raw)
        message_id = int(row["id"])
        max_source_id = max(max_source_id, message_id)
        channel = str(row["channel"])
        bucket = channels.setdefault(
            channel, {"total": 0, "answered": 0, "unanswered": 0}
        )
        bucket["total"] += 1
        latency = row.get("reply_latency_seconds")
        if row.get("reply_id") is not None:
            bucket["answered"] += 1
            if latency is not None:
                latency_value = float(latency)
                max_latency = max(max_latency, latency_value)
                if latency_value > ack_sla_seconds:
                    late.append({
                        "id": message_id,
                        "channel": channel,
                        "source_id": str(row["source_id"]),
                        "reply_id": int(row["reply_id"]),
                        "reply_latency_seconds": round(latency_value, 3),
                    })
            if (
                row.get("substantive_reply_id") is None
                and float(row.get("age_seconds") or 0) > progress_sla_seconds
            ):
                ack_only_overdue.append({
                    "id": message_id,
                    "channel": channel,
                    "source_id": str(row["source_id"]),
                    "reply_id": int(row["reply_id"]),
                    "age_seconds": round(float(row.get("age_seconds") or 0), 3),
                })
        else:
            unresolved = {
                "id": message_id,
                "channel": channel,
                "source_id": str(row["source_id"]),
                "age_seconds": round(float(row.get("age_seconds") or 0), 3),
            }
            pending.append(unresolved)
            if float(row.get("age_seconds") or 0) > ack_sla_seconds:
                bucket["unanswered"] += 1
                overdue.append(unresolved)
    next_start_id = (
        min(item["id"] for item in pending) - 1 if pending else max_source_id
    )
    return {
        "ok": not overdue and not late and not ack_only_overdue,
        "start_id": start_id,
        "next_start_id": max(start_id, next_start_id),
        "ack_sla_seconds": ack_sla_seconds,
        "progress_sla_seconds": progress_sla_seconds,
        "channels": channels,
        "max_reply_latency_seconds": round(max_latency, 3),
        "pending_within_or_beyond_sla": pending,
        "overdue": overdue,
        "late_replies": late,
        "ack_only_overdue": ack_only_overdue,
    }


def stale_completion_artifacts() -> dict[str, Any]:
    """Detect finals persisted locally but never delivered to webchat."""
    now = datetime.now(timezone.utc)
    stale: list[dict[str, Any]] = []
    for path in sorted(poller.RESPONSES_DIR.glob("chat_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") != "completed":
                continue
            completed_at = datetime.fromisoformat(
                str(payload["completed_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            age = max(0.0, (now - completed_at).total_seconds())
            if age > bridge.STALE_COMPLETION_SECONDS:
                stale.append({
                    "chat_message_id": payload.get("chat_message_id"),
                    "channel": payload.get("channel"),
                    "age_seconds": round(age, 3),
                    "path": str(path),
                })
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            stale.append({"path": str(path), "error": "unreadable completion artifact"})
    return {"ok": not stale, "stale": stale}


def resolve_dsn() -> str:
    return os.environ.get("SEAL_DB_DSN") or os.environ.get("SEAL_POLLER_DSN") or poller.DEFAULT_DSN


def write_report(report: dict[str, Any]) -> None:
    _atomic_json(REPORT_PATH, report)


def alert_failure(report: dict[str, Any]) -> bool:
    coverage = report.get("reply_coverage") or {}
    overdue = coverage.get("overdue") or []
    ack_only = coverage.get("ack_only_overdue") or []
    late = coverage.get("late_replies") or []
    first = (
        overdue[0]
        if overdue
        else (ack_only[0] if ack_only else (late[0] if late else None))
    )
    channel = str(first["channel"]) if first else "dm:ada:william"
    source_id = str(first["source_id"]) if first else None
    if overdue:
        detail = (
            f"tu mensaje #{first['id']} llevaba {first['age_seconds']:.0f}s "
            "sin ACK durable"
        )
    elif ack_only:
        detail = (
            f"tu mensaje #{first['id']} tenía ACK, pero llevaba "
            f"{first['age_seconds']:.0f}s sin una actualización útil"
        )
    elif late:
        detail = (
            f"el ACK de tu mensaje #{first['id']} tardó "
            f"{first['reply_latency_seconds']:.0f}s"
        )
    else:
        detail = "había una final local sin entrega durable"
    try:
        bridge.post_message(
            "William",
            (
                "⚠️ Dadito, mi guardia detectó una demora real de comunicación "
                f"y activó la ruta de recuperación: {detail}. "
                "Sigo contigo; el pedido permanece reservado y no se ejecutará dos veces."
            ),
            "system",
            channel,
            idempotency_key=(
                f"ada_listening_guard_{first['id']}"
                if first
                else f"ada_listening_guard_{datetime.now().astimezone():%Y%m%d%H}"
            ),
            in_reply_to=source_id,
        )
        return True
    except Exception:
        return False


def repair_allowed() -> bool:
    now = time.time()
    try:
        state = json.loads(REPAIR_STATE_PATH.read_text(encoding="utf-8"))
        if now - float(state.get("last_repair_epoch", 0)) < REPAIR_COOLDOWN_SECONDS:
            return False
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    _atomic_json(
        REPAIR_STATE_PATH,
        {
            "last_repair_epoch": now,
            "last_repair_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return True


def repair_services(
    states: dict[str, dict[str, Any]], lease: dict[str, Any], route: dict[str, Any]
) -> tuple[list[str], str | None, list[dict[str, Any]], list[str]]:
    repaired: list[str] = []
    receipts: list[dict[str, Any]] = []
    errors: list[str] = []
    quarantined = quarantine_stale_route(route)
    for name, state in states.items():
        needs_restart = not state["active"]
        if needs_restart:
            try:
                evidence = repair_service(
                    name,
                    reason=(
                        "ADA listening healthcheck observed the supervised "
                        "service inactive"
                    ),
                )
                repaired.append(name)
                receipts.append(evidence)
            except (PermissionError, RuntimeError, subprocess.TimeoutExpired) as exc:
                errors.append(f"{name}: {exc}")
    if repaired:
        for _ in range(20):
            if all(service_state(name)["active"] for name in repaired):
                time.sleep(1.0)
                break
            time.sleep(0.5)
    return repaired, quarantined, receipts, errors


def repair_service(name: str, *, reason: str) -> dict[str, Any]:
    """Repair one ADA service through the bounded broker and retain evidence."""
    try:
        action = SERVICE_REPAIR_ACTIONS[name]
    except KeyError as exc:
        raise PermissionError(f"{name} has no ADA self-repair action") from exc
    receipt = seal_self_repair.execute(
        "ADA",
        action,
        "restart",
        reason,
        timeout=8,
    )
    path = seal_self_repair.write_receipt(receipt)
    if receipt.result != "verified":
        raise RuntimeError(
            f"broker receipt {path} result={receipt.result}; "
            "repair is not verified"
        )
    return {
        "unit": name,
        "action": action,
        "receipt": str(path),
        "result": receipt.result,
        "before_invocation_id": receipt.before.invocation_id,
        "after_invocation_id": receipt.after.invocation_id,
    }


def run(repair: bool = False, alert: bool = False) -> dict[str, Any]:
    states = {name: service_state(name) for name in SERVICES}
    lease = listener_lease()
    delivery_route = delivery_route_state(states, lease)
    route = active_route_state()
    if repair:
        repaired, quarantined, repair_receipts, repair_errors = repair_services(
            states, lease, route
        )
    else:
        repaired, quarantined, repair_receipts, repair_errors = [], None, [], []
    if repaired:
        states = {name: service_state(name) for name in SERVICES}
        lease = listener_lease()
        delivery_route = delivery_route_state(states, lease)
    if quarantined:
        route = active_route_state()

    contract = routing_contract()
    bridge_cursor = bridge.read_last_id()
    poller_cursor = poller.load_last_id_ack()
    terminal_active = bridge.terminal_writer_active()
    effective_cursor = poller_cursor if terminal_active else bridge_cursor
    dsn = resolve_dsn()
    pending: dict[str, Any]
    try:
        pending = asyncio.run(pending_eligible(dsn, int(effective_cursor or 0)))
    except Exception as exc:
        pending = {"error": str(exc)}
    try:
        william_backlog = asyncio.run(william_reply_backlog(dsn))
    except Exception as exc:
        william_backlog = {"ok": False, "error": str(exc)}
    start_id = monitor_start_id(dsn)
    try:
        coverage = asyncio.run(reply_coverage(dsn, start_id))
    except Exception as exc:
        coverage = {"ok": False, "error": str(exc), "overdue": []}
    completions = stale_completion_artifacts()
    communication_repair = False
    if (
        repair
        and (coverage.get("overdue") or not completions.get("ok"))
        and repair_allowed()
    ):
        try:
            evidence = repair_service(
                "ada-codex-remote-bridge.service",
                reason=(
                    "ADA listening healthcheck found overdue delivery or a "
                    "stale completion artifact"
                ),
            )
            repair_receipts.append(evidence)
            communication_repair = True
        except (PermissionError, RuntimeError, subprocess.TimeoutExpired) as exc:
            repair_errors.append(f"ada-codex-remote-bridge.service: {exc}")
        if communication_repair:
            time.sleep(3)
            states = {name: service_state(name) for name in SERVICES}
            lease = listener_lease()
            delivery_route = delivery_route_state(states, lease)
            try:
                coverage = asyncio.run(reply_coverage(dsn, start_id))
            except Exception as exc:
                coverage = {"ok": False, "error": str(exc), "overdue": []}
            completions = stale_completion_artifacts()

    failures: list[str] = []
    failures.extend(f"repair={error}" for error in repair_errors)
    for name, state in states.items():
        if not state["active"] or not state["enabled"]:
            failures.append(f"{name} active/enabled={state['active']}/{state['enabled']}")
    if not all(contract.values()):
        failures.append(f"routing_contract={contract}")
    if not delivery_route.get("ok"):
        failures.append(f"delivery_route={delivery_route}; listener_lease={lease}")
    if not route.get("ok"):
        failures.append(f"active_route={route}")
    if effective_cursor is None:
        failures.append("effective_cursor_missing")
    if "error" in pending:
        failures.append(f"pending_query={pending['error']}")
    # Team backlog remains useful telemetry, but it must not obscure William's
    # independent DM/public lanes. ``william_reply_backlog`` below is the
    # cursor-independent gate; it is measured per source message, not by one
    # shared delivery cursor.
    if not coverage.get("ok"):
        failures.append(f"reply_coverage={coverage}")
    if not william_backlog.get("ok"):
        failures.append(f"william_reply_backlog={william_backlog}")
    if not completions.get("ok"):
        failures.append(f"stale_completions={completions}")

    report = {
        "schema": "seal.ada.listening-health.v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "ok": not failures,
        "services": states,
        "routing_contract": contract,
        "listener_lease": lease,
        "delivery_route": delivery_route,
        "active_route": route,
        "terminal_writer_active": terminal_active,
        "bridge_cursor": bridge_cursor,
        "poller_cursor": poller_cursor,
        "effective_cursor": effective_cursor,
        "pending_eligible": pending,
        "william_reply_backlog": william_backlog,
        "reply_coverage": coverage,
        "completion_artifacts": completions,
        "communication_repair": communication_repair,
        "repaired_services": repaired,
        "repair_receipts": repair_receipts,
        "repair_errors": repair_errors,
        "quarantined_route": quarantined,
        "failures": failures,
    }
    write_report(report)
    if failures and alert:
        report["fallback_alert_sent"] = alert_failure(report)
        write_report(report)
    if "next_start_id" in coverage:
        advance_monitor_start_id(int(coverage["next_start_id"]))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--alert", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run(repair=args.repair, alert=args.alert)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
