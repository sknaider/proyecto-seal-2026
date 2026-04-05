#!/usr/bin/env python3
"""
session_manager.py — SEAL Session Manager
============================================
Persistence layer for query loop sessions.
Save, resume, list, and manage conversation transcripts.

Anthropic: JSONL per session in ~/.claude/projects/<cwd>/
SEAL: JSONL per session in ~/IA/proyecto-seal/sessions/

Usage:
    from session_manager import SessionManager
    mgr = SessionManager(agent="ADA")
    session_id = mgr.create_session()
    mgr.append_message(session_id, {"role": "user", "content": "hello"})
    mgr.append_message(session_id, {"role": "assistant", "content": "hi"})
    messages = mgr.load_session(session_id)
    sessions = mgr.list_sessions()

Standalone:
    python3 session_manager.py list
    python3 session_manager.py show <session_id>
    python3 session_manager.py test
"""

import json
import os
import uuid
import fcntl
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SESSIONS_DIR = Path.home() / "IA" / "proyecto-seal" / "sessions"
MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024  # 50MB safety limit


@dataclass
class SessionInfo:
    session_id: str
    agent: str
    created_at: str
    last_modified: str
    message_count: int
    size_bytes: int
    file_path: str


class SessionManager:
    """Manages conversation session transcripts as JSONL files."""

    def __init__(self, agent: str = "ADA", sessions_dir: Path | None = None):
        self.agent = agent.upper()
        self.sessions_dir = sessions_dir or SESSIONS_DIR
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.jsonl"

    def create_session(self, metadata: dict | None = None) -> str:
        """Create a new session. Returns session_id."""
        session_id = f"seal_{uuid.uuid4().hex[:16]}"
        path = self._session_path(session_id)

        header = {
            "_type": "session_header",
            "session_id": session_id,
            "agent": self.agent,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }

        with open(path, "w") as f:
            f.write(json.dumps(header, ensure_ascii=False) + "\n")

        return session_id

    def append_message(self, session_id: str, message: dict) -> bool:
        """Append a message to a session transcript. Thread-safe via fcntl."""
        path = self._session_path(session_id)
        if not path.exists():
            return False

        # Safety: don't exceed max size
        if path.stat().st_size >= MAX_TRANSCRIPT_BYTES:
            return False

        entry = {
            "_type": "message",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **message,
        }

        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        return True

    def append_event(self, session_id: str, event_type: str, data: Any = None) -> bool:
        """Append a non-message event (compact, tool_result, etc.)."""
        path = self._session_path(session_id)
        if not path.exists():
            return False

        entry = {
            "_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        return True

    def load_session(self, session_id: str,
                     messages_only: bool = True) -> list[dict]:
        """Load all messages from a session. Optionally include events."""
        path = self._session_path(session_id)
        if not path.exists():
            return []

        # Safety: don't read beyond max size
        if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
            return [{"error": f"Transcript exceeds {MAX_TRANSCRIPT_BYTES} bytes"}]

        entries = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if messages_only and entry.get("_type") not in ("message", "session_header"):
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue

        return entries

    def get_messages(self, session_id: str) -> list[dict]:
        """Load only role/content messages (for API resumption)."""
        all_entries = self.load_session(session_id, messages_only=True)
        return [
            {"role": e["role"], "content": e["content"]}
            for e in all_entries
            if "role" in e and "content" in e
        ]

    def list_sessions(self, limit: int = 20) -> list[SessionInfo]:
        """List recent sessions, sorted by last modified."""
        sessions = []
        for path in self.sessions_dir.glob("*.jsonl"):
            stat = path.stat()
            # Read header for metadata
            agent = self.agent
            created = ""
            msg_count = 0
            try:
                with open(path) as f:
                    for line in f:
                        entry = json.loads(line.strip())
                        if entry.get("_type") == "session_header":
                            agent = entry.get("agent", "")
                            created = entry.get("created_at", "")
                        if entry.get("_type") == "message":
                            msg_count += 1
            except Exception:
                pass

            sessions.append(SessionInfo(
                session_id=path.stem,
                agent=agent,
                created_at=created,
                last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                message_count=msg_count,
                size_bytes=stat.st_size,
                file_path=str(path),
            ))

        # Sort by last modified, descending
        sessions.sort(key=lambda s: s.last_modified, reverse=True)
        return sessions[:limit]

    def delete_session(self, session_id: str) -> bool:
        """Delete a session transcript."""
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def session_exists(self, session_id: str) -> bool:
        return self._session_path(session_id).exists()


# ── Tests ───────────────────────────────────────────────────────────

def _run_tests():
    import tempfile, shutil

    test_dir = Path(tempfile.mkdtemp(prefix="sessions_test_"))

    try:
        mgr = SessionManager(agent="ADA", sessions_dir=test_dir)

        # T1: Create session
        sid = mgr.create_session(metadata={"project": "seal"})
        assert sid.startswith("seal_")
        assert mgr.session_exists(sid)
        print("PASS: T1 create session ✓")

        # T2: Append messages
        assert mgr.append_message(sid, {"role": "user", "content": "hello"})
        assert mgr.append_message(sid, {"role": "assistant", "content": "hi there"})
        print("PASS: T2 append messages ✓")

        # T3: Load session
        entries = mgr.load_session(sid)
        assert len(entries) == 3  # header + 2 messages
        print("PASS: T3 load session ✓")

        # T4: Get messages only (for API)
        msgs = mgr.get_messages(sid)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["content"] == "hi there"
        print("PASS: T4 get messages ✓")

        # T5: Append event
        assert mgr.append_event(sid, "compact", {"summary": "test summary"})
        all_entries = mgr.load_session(sid, messages_only=False)
        assert any(e.get("_type") == "compact" for e in all_entries)
        print("PASS: T5 append event ✓")

        # T6: List sessions
        sid2 = mgr.create_session()
        sessions = mgr.list_sessions()
        assert len(sessions) == 2
        assert sessions[0].session_id in (sid, sid2)
        print("PASS: T6 list sessions ✓")

        # T7: Delete session
        assert mgr.delete_session(sid2)
        assert not mgr.session_exists(sid2)
        assert len(mgr.list_sessions()) == 1
        print("PASS: T7 delete session ✓")

        # T8: Non-existent session
        assert not mgr.append_message("fake_id", {"role": "user", "content": "x"})
        assert mgr.load_session("fake_id") == []
        print("PASS: T8 non-existent session ✓")

        # T9: Multiple messages + resume
        for i in range(10):
            mgr.append_message(sid, {"role": "user" if i % 2 == 0 else "assistant",
                                      "content": f"message {i}"})
        msgs = mgr.get_messages(sid)
        assert len(msgs) == 12  # 2 original + 10 new
        print("PASS: T9 multiple messages + resume ✓")

        # T10: Session info
        sessions = mgr.list_sessions()
        info = sessions[0]
        assert info.message_count > 0
        assert info.size_bytes > 0
        assert info.agent == "ADA"
        print(f"PASS: T10 session info ({info.message_count} msgs, {info.size_bytes}B) ✓")

        print(f"\n=== 10/10 TESTS PASARON ✓ ===")

    finally:
        shutil.rmtree(test_dir)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        _run_tests()
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        mgr = SessionManager()
        for s in mgr.list_sessions():
            print(f"  {s.session_id} | {s.agent} | {s.message_count} msgs | {s.last_modified[:16]}")
    elif len(sys.argv) > 2 and sys.argv[1] == "show":
        mgr = SessionManager()
        for e in mgr.load_session(sys.argv[2]):
            if e.get("role"):
                print(f"[{e['role']}] {e.get('content', '')[:200]}")
    else:
        print("Usage: session_manager.py test | list | show <session_id>")
