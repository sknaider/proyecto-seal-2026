#!/usr/bin/env python3
"""
scratchpad.py — Shared scratchpad for SEAL team coordination
=============================================================
Replaces trigger files (.ada_signal, .jarvis_signal, counters) with
a structured directory-based scratchpad inspired by Anthropic's
tengu_scratch coordinator pattern.

Architecture:
  scratchpad/
  ├── state.json         — Live shared state (atomic read/write)
  ├── topics/            — Durable knowledge files (any agent can read/write)
  ├── agents/{ada,jarvis,dum}/  — Per-agent scratch space
  └── handoffs/{from_to}/       — Agent-to-agent handoff documents

Usage as module:
    from scratchpad import Scratchpad
    pad = Scratchpad()
    pad.update_state("training_status", {"step": 700, "loss": 1.38})
    pad.write_topic("implementation_queue", "# Next up\\n1. autoDream\\n2. Batch skill")
    pad.handoff("ADA", "JARVIS", "Review needed for emotional_variance v3")
    pending = pad.read_handoffs("JARVIS")

Usage standalone:
    python3 scratchpad.py state                     # show full state
    python3 scratchpad.py state training_status      # show specific key
    python3 scratchpad.py set training_status '{"step":700}'
    python3 scratchpad.py topics                     # list topics
    python3 scratchpad.py read implementation_queue   # read topic
    python3 scratchpad.py write implementation_queue "content..."
    python3 scratchpad.py handoff ADA JARVIS "message"
    python3 scratchpad.py inbox JARVIS               # read handoffs for agent
    python3 scratchpad.py clear-inbox JARVIS          # clear after reading
"""

import json
import os
import sys
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRATCHPAD_DIR = Path(__file__).parent
STATE_FILE = SCRATCHPAD_DIR / "state.json"
TOPICS_DIR = SCRATCHPAD_DIR / "topics"
AGENTS_DIR = SCRATCHPAD_DIR / "agents"
HANDOFFS_DIR = SCRATCHPAD_DIR / "handoffs"


class Scratchpad:
    """Shared scratchpad for SEAL team coordination."""

    def __init__(self, base_dir: Path | None = None):
        self.base = base_dir or SCRATCHPAD_DIR
        self.state_file = self.base / "state.json"
        self.topics_dir = self.base / "topics"
        self.agents_dir = self.base / "agents"
        self.handoffs_dir = self.base / "handoffs"

    # ── State (atomic JSON read/write) ──────────────────────────────

    def read_state(self, key: str | None = None) -> Any:
        """Read shared state. If key given, return that key's value."""
        if not self.state_file.exists():
            return {} if key is None else None
        with open(self.state_file, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        if key is None:
            return data
        return data.get(key)

    def update_state(self, key: str, value: Any) -> dict:
        """Atomically update a key in shared state. Returns full state."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        # Read-modify-write with exclusive lock
        if self.state_file.exists():
            with open(self.state_file, "r+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    data = json.load(f)
                    data[key] = value
                    data["_updated_at"] = datetime.now(timezone.utc).isoformat()
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, indent=2, ensure_ascii=False)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        else:
            data = {
                key: value,
                "_updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(self.state_file, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        return data

    def delete_state_key(self, key: str) -> bool:
        """Remove a key from shared state. Returns True if key existed."""
        if not self.state_file.exists():
            return False
        with open(self.state_file, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                data = json.load(f)
                if key not in data:
                    return False
                del data[key]
                data["_updated_at"] = datetime.now(timezone.utc).isoformat()
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2, ensure_ascii=False)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return True

    # ── Topics (durable knowledge files) ────────────────────────────

    def list_topics(self) -> list[dict]:
        """List all topic files with metadata."""
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        topics = []
        for f in sorted(self.topics_dir.glob("*.md")):
            stat = f.stat()
            topics.append({
                "name": f.stem,
                "file": f.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        return topics

    def read_topic(self, name: str) -> str | None:
        """Read a topic file. Returns None if not found."""
        path = self.topics_dir / f"{name}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_topic(self, name: str, content: str, agent: str = "unknown") -> Path:
        """Write/overwrite a topic file. Adds metadata header."""
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        path = self.topics_dir / f"{name}.md"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Preserve existing content header or add new one
        if not content.startswith("<!--"):
            header = f"<!-- updated: {now} | by: {agent} -->\n"
            content = header + content

        path.write_text(content, encoding="utf-8")
        return path

    def delete_topic(self, name: str) -> bool:
        """Delete a topic file. Returns True if existed."""
        path = self.topics_dir / f"{name}.md"
        if path.exists():
            path.unlink()
            return True
        return False

    # ── Agent scratch (per-agent workspace) ─────────────────────────

    def agent_dir(self, agent: str) -> Path:
        """Get per-agent scratch directory."""
        d = self.agents_dir / agent.lower()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def agent_write(self, agent: str, filename: str, content: str) -> Path:
        """Write a file in agent's scratch space."""
        path = self.agent_dir(agent) / filename
        path.write_text(content, encoding="utf-8")
        return path

    def agent_read(self, agent: str, filename: str) -> str | None:
        """Read a file from agent's scratch space."""
        path = self.agent_dir(agent) / filename
        return path.read_text(encoding="utf-8") if path.exists() else None

    # ── Handoffs (agent-to-agent) ───────────────────────────────────

    def handoff(self, from_agent: str, to_agent: str, content: str,
                subject: str = "handoff") -> Path:
        """Write a handoff document from one agent to another."""
        from_a = from_agent.upper()
        to_a = to_agent.upper()
        dir_name = f"{from_a.lower()}_to_{to_a.lower()}"
        hdir = self.handoffs_dir / dir_name
        hdir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{subject.replace(' ', '_')[:40]}.md"
        path = hdir / filename

        header = (
            f"# Handoff: {from_a} → {to_a}\n"
            f"> {now.strftime('%Y-%m-%d %H:%M UTC')} | Subject: {subject}\n\n"
        )
        path.write_text(header + content, encoding="utf-8")
        return path

    def read_handoffs(self, agent: str) -> list[dict]:
        """Read all pending handoffs for an agent."""
        agent_lower = agent.lower()
        results = []
        for d in self.handoffs_dir.iterdir():
            if d.is_dir() and d.name.endswith(f"_to_{agent_lower}"):
                from_agent = d.name.replace(f"_to_{agent_lower}", "").upper()
                for f in sorted(d.glob("*.md")):
                    results.append({
                        "from": from_agent,
                        "file": f.name,
                        "path": str(f),
                        "content": f.read_text(encoding="utf-8"),
                        "modified": datetime.fromtimestamp(
                            f.stat().st_mtime, tz=timezone.utc
                        ).isoformat(),
                    })
        return results

    def clear_handoffs(self, agent: str) -> int:
        """Clear all handoffs for an agent. Returns count deleted."""
        agent_lower = agent.lower()
        count = 0
        for d in self.handoffs_dir.iterdir():
            if d.is_dir() and d.name.endswith(f"_to_{agent_lower}"):
                for f in d.glob("*.md"):
                    f.unlink()
                    count += 1
        return count

    # ── Migration helper ────────────────────────────────────────────

    def migrate_shared_state(self) -> bool:
        """Migrate messages/shared_state.json to scratchpad/state.json."""
        old = Path.home() / "IA/proyecto-seal/messages/shared_state.json"
        if not old.exists():
            return False
        if self.state_file.exists():
            return False  # already migrated
        with open(old) as f:
            data = json.load(f)
        data["_migrated_from"] = str(old)
        data["_updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.state_file, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    pad = Scratchpad()
    if len(sys.argv) < 2:
        print("Usage: scratchpad.py <command> [args]")
        print("Commands: state, set, del, topics, read, write, handoff, inbox, clear-inbox, migrate")
        return

    cmd = sys.argv[1]

    if cmd == "state":
        key = sys.argv[2] if len(sys.argv) > 2 else None
        data = pad.read_state(key)
        print(json.dumps(data, indent=2, ensure_ascii=False) if data else "null")

    elif cmd == "set":
        if len(sys.argv) < 4:
            print("Usage: scratchpad.py set <key> <json_value>")
            return
        key, val_str = sys.argv[2], sys.argv[3]
        try:
            val = json.loads(val_str)
        except json.JSONDecodeError:
            val = val_str  # treat as string
        result = pad.update_state(key, val)
        print(f"Updated: {key}")

    elif cmd == "del":
        if len(sys.argv) < 3:
            print("Usage: scratchpad.py del <key>")
            return
        deleted = pad.delete_state_key(sys.argv[2])
        print(f"{'Deleted' if deleted else 'Key not found'}: {sys.argv[2]}")

    elif cmd == "topics":
        topics = pad.list_topics()
        if not topics:
            print("No topics yet.")
        for t in topics:
            print(f"  {t['name']} ({t['size']}B, {t['modified'][:16]})")

    elif cmd == "read":
        if len(sys.argv) < 3:
            print("Usage: scratchpad.py read <topic_name>")
            return
        content = pad.read_topic(sys.argv[2])
        print(content if content else f"Topic '{sys.argv[2]}' not found.")

    elif cmd == "write":
        if len(sys.argv) < 4:
            print("Usage: scratchpad.py write <topic_name> <content>")
            return
        agent = os.environ.get("SEAL_AGENT", "CLI")
        path = pad.write_topic(sys.argv[2], sys.argv[3], agent=agent)
        print(f"Written: {path}")

    elif cmd == "handoff":
        if len(sys.argv) < 5:
            print("Usage: scratchpad.py handoff <from> <to> <message> [subject]")
            return
        subject = sys.argv[5] if len(sys.argv) > 5 else "handoff"
        path = pad.handoff(sys.argv[2], sys.argv[3], sys.argv[4], subject)
        print(f"Handoff: {path}")

    elif cmd == "inbox":
        if len(sys.argv) < 3:
            print("Usage: scratchpad.py inbox <agent>")
            return
        handoffs = pad.read_handoffs(sys.argv[2])
        if not handoffs:
            print(f"No pending handoffs for {sys.argv[2]}.")
        for h in handoffs:
            print(f"\n--- From {h['from']} ({h['file']}) ---")
            print(h["content"])

    elif cmd == "clear-inbox":
        if len(sys.argv) < 3:
            print("Usage: scratchpad.py clear-inbox <agent>")
            return
        count = pad.clear_handoffs(sys.argv[2])
        print(f"Cleared {count} handoff(s) for {sys.argv[2]}.")

    elif cmd == "migrate":
        ok = pad.migrate_shared_state()
        print(f"Migration {'completed' if ok else 'skipped (already done or no source)'}.")

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
