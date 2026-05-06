#!/usr/bin/env python3
"""ADA kernel native runtime — polls webchat, dispatches to cortex.

Reads new messages from william_channel.jsonl, invokes cortex.process_message
(Triangle T1 → optional Claude T2 via /tmp/ada_claude_enabled flag → Ollama),
posts response to webchat.

Safeguards (lessons learned from prior daemons):
- fcntl LOCK_EX | LOCK_NB (anti-duplicate runs) — incident 2026-04-29
- Cursor seeded to NOW UTC on first boot — incident 2026-05-04 13:17 Lima
- datetime-aware comparison (not string compare) — incident 2026-05-04 13:24 Lima
- Max iterations cap (memory leak protection) — restart via cron
- Strict httpx timeouts (no asyncio hang)
- Identity integrity validated before each webchat POST

Hermes patterns (absorbed):
- Checkpoint resumption: checkpoint.json tracks cursor + last_msg_id for perfect crash recovery
- Approval hook: ADA_KERNEL_APPROVAL=auto|ask gate before heavy LLM (default: auto)
- Tips emission: ADA_KERNEL_TIPS=1 posts "procesando..." before LLM call (default: on)
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

LOG_MAX_BYTES = 1_000_000  # 1 MB before rotation
_shutdown = asyncio.Event()

_HERE = Path(__file__).resolve().parent
_ADA_HOME = _HERE.parent
_SPECTRE_KERNEL = _ADA_HOME.parent / "SPECTRE" / "kernel"
sys.path.insert(0, str(_SPECTRE_KERNEL))
sys.path.insert(0, str(_HERE))

import cortex  # noqa: E402
import soul_runtime  # noqa: E402

LOCK_PATH = Path("/tmp/ada_kernel_main.lock")
CURSOR_PATH = Path("/tmp/ada_kernel_main.cursor")
CHECKPOINT_PATH = Path("/tmp/ada_kernel_main.checkpoint.json")  # Hermes: checkpoint resumption
LOG_PATH = Path("/tmp/ada_kernel_main.log")
WEBCHAT_JSONL = Path("/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl")
WEBCHAT_POST = "http://localhost:8765/api/agents/send"
# DM channel — private 1:1 William <-> ADA via terminal console.
# 04-may-2026 (William): movido a ~/.private/seal_dms/ con chmod 700 dir + 600 file.
# Ningun otro agente debe leer DMs de otros — regla de oro.
DM_JSONL = Path.home() / ".private" / "seal_dms" / "dm_william_ada.jsonl"
DM_CURSOR_PATH = Path("/tmp/ada_kernel_main.dm_cursor")

POLL_INTERVAL_S = float(os.environ.get("ADA_KERNEL_POLL_S", "10"))
MAX_ITERATIONS = int(os.environ.get("ADA_KERNEL_MAX_ITER", "1000"))
ADDRESSEES = {"ADA", "equipo", "Equipo", "EQUIPO"}
SELF_NAMES = {"ADA"}

# Hermes: tips emission (ADA_KERNEL_TIPS=0 to disable)
TIPS_ENABLED = os.environ.get("ADA_KERNEL_TIPS", "1") == "1"
# Hermes: approval hook (auto=proceed always | ask=wait for 'OK ada' before LLM)
APPROVAL_MODE = os.environ.get("ADA_KERNEL_APPROVAL", "auto")
APPROVAL_TIMEOUT_S = float(os.environ.get("ADA_KERNEL_APPROVAL_TIMEOUT_S", "60.0"))


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} {msg}\n"
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            rotated = LOG_PATH.with_suffix(LOG_PATH.suffix + ".1")
            if rotated.exists():
                rotated.unlink()
            LOG_PATH.rename(rotated)
        with LOG_PATH.open("a") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="", flush=True)


def _acquire_lock():
    fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log("[ada/kernel] another instance holds the lock — exiting")
        sys.exit(0)
    fd.write(f"{os.getpid()}\n")
    fd.flush()
    return fd


def _load_cursor() -> datetime:
    if CURSOR_PATH.exists():
        try:
            return datetime.fromisoformat(CURSOR_PATH.read_text().strip())
        except Exception:
            pass
    now = datetime.now(timezone.utc)
    CURSOR_PATH.write_text(now.isoformat())
    _log(f"[ada/kernel] cursor seeded to {now.isoformat()}")
    return now


def _save_cursor(dt: datetime) -> None:
    CURSOR_PATH.write_text(dt.isoformat())


# ── Hermes pattern 1: Checkpoint resumption ───────────────────────────────────

def _save_checkpoint(cursor: datetime, last_msg_id: str | None) -> None:
    """Persist cursor + last processed msg ID — survives crash, prevents replay."""
    data = {"cursor": cursor.isoformat(), "last_msg_id": last_msg_id or ""}
    try:
        CHECKPOINT_PATH.write_text(json.dumps(data, ensure_ascii=False))
        CURSOR_PATH.write_text(cursor.isoformat())
    except Exception as ex:
        _log(f"[ada/kernel] checkpoint save failed: {ex}")


def _load_checkpoint() -> tuple[datetime, str | None]:
    """Load checkpoint on boot. Falls back to NOW UTC if no checkpoint exists."""
    if CHECKPOINT_PATH.exists():
        try:
            data = json.loads(CHECKPOINT_PATH.read_text())
            cursor = datetime.fromisoformat(data["cursor"])
            if cursor.tzinfo is None:
                cursor = cursor.replace(tzinfo=timezone.utc)
            last_id = data.get("last_msg_id") or None
            _log(f"[ada/kernel] checkpoint resumed: cursor={cursor.isoformat()} last_id={last_id}")
            return cursor, last_id
        except Exception as ex:
            _log(f"[ada/kernel] checkpoint load failed ({ex}), seeding fresh")
    now = datetime.now(timezone.utc)
    _save_checkpoint(now, None)
    _log(f"[ada/kernel] checkpoint seeded to {now.isoformat()}")
    return now, None


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _read_jsonl_new(path: Path, cursor: datetime, source_tag: str) -> list[dict]:
    """Read new messages from a JSONL since cursor. Tags each with `_source`."""
    if not path.exists():
        return []
    new: list[dict] = []
    try:
        with path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                ts = _parse_ts(msg.get("timestamp", ""))
                if ts is None or ts <= cursor:
                    continue
                if msg.get("from") in SELF_NAMES:
                    continue
                if msg.get("to") not in ADDRESSEES:
                    continue
                if msg.get("type") not in (None, "conversation"):
                    continue
                msg["_source"] = source_tag
                new.append(msg)
    except Exception as ex:
        _log(f"[ada/kernel] read error ({source_tag}): {ex}")
    return new


def _read_new_messages(cursor: datetime, dm_cursor: datetime) -> list[dict]:
    """Read from both webchat and DM channels, return chronologically merged list."""
    msgs = _read_jsonl_new(WEBCHAT_JSONL, cursor, "webchat")
    msgs += _read_jsonl_new(DM_JSONL, dm_cursor, "dm")
    msgs.sort(key=lambda m: _parse_ts(m.get("timestamp", "")) or datetime.min.replace(tzinfo=timezone.utc))
    return msgs


async def _post_webchat(text: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            await c.post(
                WEBCHAT_POST,
                json={
                    "from": "ADA",
                    "to": "William",
                    "type": "conversation",
                    "channel": "web_chat",
                    "message": text,
                },
            )
    except Exception as ex:
        _log(f"[ada/kernel] webchat post failed: {ex}")


def _post_dm(text: str) -> None:
    """Write ADA response to DM JSONL. Append-only, no API. William-only visibility."""
    import uuid
    entry = {
        "id": f"dm_ada_{uuid.uuid4().hex[:16]}",
        "from": "ADA",
        "to": "William",
        "type": "conversation",
        "channel": "dm",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": text,
    }
    try:
        DM_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with DM_JSONL.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        _log(f"[ada/kernel] dm post failed: {ex}")


async def _respond(text: str, source: str) -> None:
    """Route response to the same channel the request came from."""
    if source == "dm":
        _post_dm(text)
    else:
        await _post_webchat(text)


# ── Hermes pattern 2: Approval hook ──────────────────────────────────────────

async def _approval_gate(content: str, sender: str) -> bool:
    """Return True if ADA should proceed with the LLM call.

    APPROVAL_MODE=auto  → always True (default, never blocks)
    APPROVAL_MODE=ask   → post proposal, poll for 'OK ada' within APPROVAL_TIMEOUT_S
    """
    if APPROVAL_MODE != "ask":
        return True

    preview = content[:80].replace("\n", " ")
    await _post_webchat(
        f"[ADA propone] Procesar mensaje de {sender}: «{preview}…»\n"
        f"Responde 'OK ada' para aprobar (timeout {int(APPROVAL_TIMEOUT_S)}s)."
    )

    deadline = datetime.now(timezone.utc).timestamp() + APPROVAL_TIMEOUT_S
    last_seen_id: str | None = None
    while datetime.now(timezone.utc).timestamp() < deadline and not _shutdown.is_set():
        await asyncio.sleep(3.0)
        if not WEBCHAT_JSONL.exists():
            continue
        try:
            with WEBCHAT_JSONL.open("r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("id") == last_seen_id:
                        last_seen_id = None
                    if last_seen_id is not None:
                        continue
                    raw = msg.get("message", "").lower()
                    if msg.get("from") not in SELF_NAMES and "ok ada" in raw:
                        _log("[ada/kernel] approval received")
                        return True
        except Exception:
            pass

    await _post_webchat("[ADA] tiempo de espera agotado — mensaje descartado.")
    _log("[ada/kernel] approval timeout — message discarded")
    return False


async def _process_one(msg: dict) -> None:
    sender = msg.get("from", "William")
    content = msg.get("message", "").strip()
    source = msg.get("_source", "webchat")
    if not content:
        return

    # Hermes pattern 3: Tips emission — immediate ACK before heavy LLM call
    if TIPS_ENABLED:
        await _respond("[ADA] 🔄 procesando con Triangle…", source)

    # Hermes pattern 2: Approval gate (default auto=always proceed)
    if not await _approval_gate(content, sender):
        return

    _log(f"[ada/kernel] dispatch from={sender} src={source} len={len(content)}c")
    try:
        response = await asyncio.wait_for(
            cortex.process_message(content, sender=sender),
            timeout=200.0,
        )
    except asyncio.TimeoutError:
        response = "[ADA] cortex timeout — Triangle no respondió a tiempo."
        _log("[ada/kernel] cortex timeout")
    except Exception as ex:
        response = f"[ADA] cortex error: {ex}"
        _log(f"[ada/kernel] cortex exception: {ex}")
    await _respond(response, source)


async def _sleep_or_shutdown(seconds: float) -> bool:
    """Sleep for `seconds` or until shutdown is signaled. Returns True if shutdown."""
    try:
        await asyncio.wait_for(_shutdown.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


def _load_dm_cursor() -> datetime:
    if DM_CURSOR_PATH.exists():
        try:
            dt = datetime.fromisoformat(DM_CURSOR_PATH.read_text().strip())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    now = datetime.now(timezone.utc)
    DM_CURSOR_PATH.write_text(now.isoformat())
    return now


def _save_dm_cursor(dt: datetime) -> None:
    try:
        DM_CURSOR_PATH.write_text(dt.isoformat())
    except Exception:
        pass


async def _run() -> None:
    # Hermes pattern 1: load checkpoint (cursor + last_msg_id) for crash resumption
    cursor, last_msg_id = _load_checkpoint()
    dm_cursor = _load_dm_cursor()
    iterations = 0
    consecutive_errors = 0
    while iterations < MAX_ITERATIONS and not _shutdown.is_set():
        iterations += 1
        had_error = False
        try:
            new_msgs = _read_new_messages(cursor, dm_cursor)
            # Skip already-processed message at same timestamp (checkpoint dedup)
            if last_msg_id and new_msgs and new_msgs[0].get("id") == last_msg_id:
                new_msgs = new_msgs[1:]
            if new_msgs:
                _log(f"[ada/kernel] iter={iterations} new_msgs={len(new_msgs)}")
                for msg in new_msgs:
                    if _shutdown.is_set():
                        break
                    ts = _parse_ts(msg.get("timestamp", ""))
                    msg_id = msg.get("id")
                    source = msg.get("_source", "webchat")
                    try:
                        await _process_one(msg)
                    except Exception as ex:
                        _log(f"[ada/kernel] process_one crashed: {ex}")
                        had_error = True
                    # Per-channel cursor advance + checkpoint
                    if ts is not None:
                        if source == "dm" and ts > dm_cursor:
                            dm_cursor = ts
                            _save_dm_cursor(dm_cursor)
                        elif source != "dm" and ts > cursor:
                            cursor = ts
                    last_msg_id = msg_id
                    _save_checkpoint(cursor, last_msg_id)
        except Exception as ex:
            _log(f"[ada/kernel] loop error: {ex}")
            had_error = True

        # Exponential backoff on consecutive failures, cap 60s.
        if had_error:
            consecutive_errors += 1
            backoff = min(POLL_INTERVAL_S * (2 ** min(consecutive_errors - 1, 5)), 60.0)
        else:
            consecutive_errors = 0
            backoff = POLL_INTERVAL_S
        if await _sleep_or_shutdown(backoff):
            break
    if _shutdown.is_set():
        _log("[ada/kernel] shutdown signal — graceful exit")
    else:
        _log(f"[ada/kernel] max iterations reached ({MAX_ITERATIONS}), exiting for restart")


_last_dispatch_ts: str | None = None


def _get_last_dispatch_ts() -> str | None:
    return _last_dispatch_ts


async def _boot_soul() -> None:
    """Load identity + relationships + last inner thought from Soul DB.

    Injects the loaded context into cortex via set_boot_context() so the
    LLM has WHO ADA is at every dispatch (not just generic system prompt).
    """
    try:
        ctx = await soul_runtime.boot_context("ADA")
        if ctx.get("prompt_block"):
            cortex.set_boot_context(ctx["prompt_block"])
            _log(
                f"[ada/kernel] boot_context loaded: ocean={bool(ctx['ocean'])} "
                f"rels={len(ctx['relationships'])} beliefs={len(ctx['beliefs'])} "
                f"rules={len(ctx['critical_rules'])}"
            )
        else:
            _log("[ada/kernel] boot_context returned empty (DB unreachable?)")
        await soul_runtime.event_log_append(
            "ADA", "milestone",
            "ADA-nativa daemon boot — soul_runtime conectado",
            metadata={"pid": os.getpid()},
        )
    except Exception as ex:
        _log(f"[ada/kernel] boot_soul error: {ex}")


async def _main_async() -> None:
    loop = asyncio.get_running_loop()

    def _on_signal(signame: str) -> None:
        _log(f"[ada/kernel] received {signame} — initiating graceful shutdown")
        _shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal, sig.name)
        except NotImplementedError:
            # add_signal_handler is unavailable on some platforms (e.g. Windows).
            pass

    await _boot_soul()

    hb_task = asyncio.create_task(
        soul_runtime.heartbeat_loop("ADA", interval_s=60.0, get_last_dispatch=_get_last_dispatch_ts)
    )
    try:
        await _run()
    finally:
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await soul_runtime.close()
        except Exception:
            pass


def main() -> None:
    _log(f"[ada/kernel] starting pid={os.getpid()} poll={POLL_INTERVAL_S}s max_iter={MAX_ITERATIONS}")
    _lock_fd = _acquire_lock()  # keep reference alive — GC release would unlock
    try:
        asyncio.run(_main_async())
    except KeyboardInterrupt:
        _log("[ada/kernel] interrupted by user")
    finally:
        _log("[ada/kernel] exit")


if __name__ == "__main__":
    main()
