"""Webchat → Event Bus bridge (Level 2 Nervios sensor).

Watches william_channel.jsonl and publishes each new line as
'message_incoming' event to the SEAL PostgreSQL event bus.

This closes the PCA loop (Bridging Brains paper, Neural Brain paper):
    william_channel.jsonl → this bridge → EventBus → event_handlers/nexus.py

Run modes:
    python3 webchat_to_eventbus.py          # foreground (terminal)
    systemctl --user start seal-webchat-bridge.service

State file: /tmp/nexus_bridge_last_id  (survives restarts, dedups replay)
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from event_bus import EventBus  # noqa: E402

CHANNEL_FILE = Path(__file__).parent / "william_channel.jsonl"
STATE_FILE = Path("/tmp/nexus_bridge_last_id")
AGENT = "NEXUS"
POLL_INTERVAL = 0.5  # seconds between tail polls

_SKIP_SENDERS = {"NEXUS", "NEXUS_BRIDGE", "EVENT_BUS_DAEMON"}


async def _tail_and_publish(bus: EventBus) -> None:
    """Tail the jsonl file and publish new messages to the event bus."""
    seen_ids: set[str] = set()

    # Load last known ID to avoid replaying old messages on restart
    if STATE_FILE.exists():
        last_id = STATE_FILE.read_text().strip()
        if last_id:
            seen_ids.add(last_id)

    if not CHANNEL_FILE.exists():
        print(f"[bridge] {CHANNEL_FILE} not found, waiting...", flush=True)
        await asyncio.sleep(5)
        return

    # Seek to end of file on first cold start (no state file)
    pos = CHANNEL_FILE.stat().st_size if not STATE_FILE.exists() else 0

    print(f"[bridge] Watching {CHANNEL_FILE} from pos={pos}", flush=True)

    while True:
        try:
            file_size = CHANNEL_FILE.stat().st_size
            if file_size < pos:
                # File was rotated/truncated
                pos = 0
                print("[bridge] File truncated — resetting position", flush=True)

            if file_size > pos:
                with open(CHANNEL_FILE, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    while True:
                        line = f.readline()
                        if not line:
                            break
                        pos = f.tell()
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        msg_id = msg.get("id", "")
                        if not msg_id or msg_id in seen_ids:
                            continue

                        sender = msg.get("from", "")
                        if sender in _SKIP_SENDERS:
                            seen_ids.add(msg_id)
                            continue

                        seen_ids.add(msg_id)
                        STATE_FILE.write_text(msg_id)

                        content = msg.get("message", "")
                        await bus.publish(
                            event_type="message_incoming",
                            content=content[:500],
                            ref_id=msg_id,
                            metadata={
                                "from": sender,
                                "to": msg.get("to", "equipo"),
                                "channel": msg.get("channel", "web_chat"),
                                "msg_type": msg.get("type", "conversation"),
                                "original_id": msg_id,
                            },
                            notify_webchat=False,
                        )
                        print(f"[bridge] message_incoming from {sender}: {content[:50]}", flush=True)

                        # Keep seen_ids bounded
                        if len(seen_ids) > 500:
                            seen_ids = set(list(seen_ids)[-200:])

        except Exception as ex:
            print(f"[bridge] Read error: {ex}", flush=True)

        await asyncio.sleep(POLL_INTERVAL)


async def main() -> None:
    print(f"[bridge] SEAL Webchat→EventBus bridge starting. Agent: {AGENT}", flush=True)
    while True:
        bus = EventBus(agent=AGENT)
        try:
            await _tail_and_publish(bus)
        except (ConnectionError, OSError) as ex:
            print(f"[bridge] Connection lost: {ex}. Retry in 5s...", flush=True)
            await asyncio.sleep(5)
        except Exception as ex:
            print(f"[bridge] Unexpected error: {type(ex).__name__}: {ex}. Retry in 5s...", flush=True)
            await asyncio.sleep(5)
        finally:
            try:
                await bus.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[bridge] Shutdown via SIGINT", flush=True)
