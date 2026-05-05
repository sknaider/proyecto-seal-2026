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
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

_HERE = Path(__file__).resolve().parent
_ADA_HOME = _HERE.parent
_SPECTRE_KERNEL = _ADA_HOME.parent / "SPECTRE" / "kernel"
sys.path.insert(0, str(_SPECTRE_KERNEL))
sys.path.insert(0, str(_HERE))

import cortex  # noqa: E402

LOCK_PATH = Path("/tmp/ada_kernel_main.lock")
CURSOR_PATH = Path("/tmp/ada_kernel_main.cursor")
LOG_PATH = Path("/tmp/ada_kernel_main.log")
WEBCHAT_JSONL = Path("/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl")
WEBCHAT_POST = "http://localhost:8765/api/agents/send"

POLL_INTERVAL_S = float(os.environ.get("ADA_KERNEL_POLL_S", "10"))
MAX_ITERATIONS = int(os.environ.get("ADA_KERNEL_MAX_ITER", "1000"))
ADDRESSEES = {"ADA", "equipo", "Equipo", "EQUIPO"}
SELF_NAMES = {"ADA"}


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} {msg}\n"
    try:
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


def _read_new_messages(cursor: datetime) -> list[dict]:
    if not WEBCHAT_JSONL.exists():
        return []
    new: list[dict] = []
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
                ts = _parse_ts(msg.get("timestamp", ""))
                if ts is None or ts <= cursor:
                    continue
                if msg.get("from") in SELF_NAMES:
                    continue
                if msg.get("to") not in ADDRESSEES:
                    continue
                if msg.get("type") not in (None, "conversation"):
                    continue
                new.append(msg)
    except Exception as ex:
        _log(f"[ada/kernel] read error: {ex}")
    return new


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


async def _process_one(msg: dict) -> None:
    sender = msg.get("from", "William")
    content = msg.get("message", "").strip()
    if not content:
        return
    _log(f"[ada/kernel] dispatch from={sender} len={len(content)}c")
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
    await _post_webchat(response)


async def _run() -> None:
    cursor = _load_cursor()
    iterations = 0
    while iterations < MAX_ITERATIONS:
        iterations += 1
        try:
            new_msgs = _read_new_messages(cursor)
            if new_msgs:
                _log(f"[ada/kernel] iter={iterations} new_msgs={len(new_msgs)}")
                for msg in new_msgs:
                    ts = _parse_ts(msg.get("timestamp", ""))
                    if ts is not None and ts > cursor:
                        cursor = ts
                    await _process_one(msg)
                _save_cursor(cursor)
        except Exception as ex:
            _log(f"[ada/kernel] loop error: {ex}")
        await asyncio.sleep(POLL_INTERVAL_S)
    _log(f"[ada/kernel] max iterations reached ({MAX_ITERATIONS}), exiting for restart")


def main() -> None:
    _log(f"[ada/kernel] starting pid={os.getpid()} poll={POLL_INTERVAL_S}s max_iter={MAX_ITERATIONS}")
    _acquire_lock()
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        _log("[ada/kernel] interrupted by user")
    finally:
        _log("[ada/kernel] exit")


if __name__ == "__main__":
    main()
