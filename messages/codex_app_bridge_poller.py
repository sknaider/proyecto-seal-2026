#!/usr/bin/env python3
"""Poll Codex App bridge queue and inject tasks into visible ADA Codex."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ada_codex_poller import (
        BUSY_WAIT_MAX,
        TMUX_SESSION,
        inject_message,
        tmux_session_alive,
        wait_for_idle,
    )
except ModuleNotFoundError:
    from messages.ada_codex_poller import (
        BUSY_WAIT_MAX,
        TMUX_SESSION,
        inject_message,
        tmux_session_alive,
        wait_for_idle,
    )


ROOT = Path("/home/dadito/IA/proyecto-seal")
QUEUE_DIR = Path(os.environ.get("ADA_CODEX_APP_BRIDGE_DIR", ROOT / "messages" / "codex_app_bridge"))
INBOX_FILE = QUEUE_DIR / "inbox.jsonl"
PROCESSED_FILE = QUEUE_DIR / "processed.jsonl"
ACTIVE_TASK_FILE = QUEUE_DIR / "active_task.json"
PID_FILE = Path("/tmp/ada_codex_app_bridge_poller.pid")
POLL_INTERVAL = 0.35


def already_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        return False


def load_processed_ids(processed_file: Path = PROCESSED_FILE) -> set[str]:
    if not processed_file.exists():
        return set()
    ids: set[str] = set()
    for line in processed_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        task_id = data.get("id")
        if task_id:
            ids.add(task_id)
    return ids


def load_tasks(inbox_file: Path = INBOX_FILE) -> list[dict[str, Any]]:
    if not inbox_file.exists():
        return []
    tasks: list[dict[str, Any]] = []
    for line in inbox_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            task = json.loads(line)
        except json.JSONDecodeError:
            continue
        if task.get("status") == "queued":
            tasks.append(task)
    return tasks


def pending_tasks(
    inbox_file: Path = INBOX_FILE,
    processed_file: Path = PROCESSED_FILE,
) -> list[dict[str, Any]]:
    processed = load_processed_ids(processed_file)
    return [task for task in load_tasks(inbox_file) if task.get("id") not in processed]


def format_bridge_task(task: dict[str, Any]) -> str:
    created = task.get("created_at") or ""
    try:
        hour = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except Exception:
        hour = "??:??"
    task_id = task.get("id", "?")
    sender = task.get("from", "William")
    channel = task.get("channel", "dm:ada:william")
    message = " ".join(str(task.get("message", "")).split())
    attachments = task.get("attachments") or []
    attachment_note = f" attachments={len(attachments)}" if attachments else ""
    return (
        f"[{sender} @ {hour} / {channel} bridge_task {task_id}{attachment_note}]: "
        f"{message}"
    )


def mark_processed(task: dict[str, Any], processed_file: Path = PROCESSED_FILE) -> None:
    processed_file.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": task.get("id"),
        "processed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "injected",
    }
    with processed_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")


def mark_active(task: dict[str, Any], active_file: Path = ACTIVE_TASK_FILE) -> None:
    active_file.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": task.get("id"),
        "activated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": task.get("source"),
        "channel": task.get("channel"),
    }
    active_file.write_text(
        json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def task_age_ms(task: dict[str, Any]) -> int | None:
    created = task.get("created_at")
    if not created:
        return None
    try:
        created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - created_dt).total_seconds() * 1000)
    except Exception:
        return None


async def poll_loop() -> None:
    print(f"[codex-app-bridge-poller] watching {INBOX_FILE}", flush=True)
    while True:
        try:
            if not tmux_session_alive(TMUX_SESSION):
                print(f"[codex-app-bridge-poller] tmux {TMUX_SESSION} absent", flush=True)
                await asyncio.sleep(POLL_INTERVAL)
                continue
            for task in pending_tasks():
                payload = format_bridge_task(task)
                idle = await wait_for_idle(BUSY_WAIT_MAX)
                if not idle:
                    print(f"[codex-app-bridge-poller] busy timeout; postponing {task.get('id')}", flush=True)
                    break
                started = time.monotonic()
                if inject_message(TMUX_SESSION, payload):
                    inject_ms = int((time.monotonic() - started) * 1000)
                    queued_ms = task_age_ms(task)
                    mark_active(task)
                    mark_processed(task)
                    print(
                        f"[codex-app-bridge-poller] injected {task.get('id')} "
                        f"queued_ms={queued_ms if queued_ms is not None else '?'} "
                        f"inject_ms={inject_ms}",
                        flush=True,
                    )
                else:
                    print(f"[codex-app-bridge-poller] inject failed {task.get('id')}", flush=True)
                    break
        except Exception as exc:
            print(f"[codex-app-bridge-poller] error: {exc}", flush=True)
        await asyncio.sleep(POLL_INTERVAL)


def main() -> None:
    if already_running():
        print(f"[codex-app-bridge-poller] already running PID {PID_FILE.read_text().strip()}", flush=True)
        sys.exit(0)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    try:
        asyncio.run(poll_loop())
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
