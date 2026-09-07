"""Process-level crash gates for ADA's DM exactly-once state machine.

These tests deliberately SIGKILL a child at the two dangerous boundaries.
The counter file stands for a durable tool side effect: recovery is valid only
when the final count remains one.
"""

from __future__ import annotations

import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

from messages import ada_codex_poller as poller
import messages.ada_codex_remote_bridge as bridge


def _increment(path: Path) -> None:
    value = int(path.read_text(encoding="utf-8") or "0") if path.exists() else 0
    path.write_text(str(value + 1), encoding="utf-8")


def _wait_killed(pid: int) -> None:
    waited, status = os.waitpid(pid, 0)
    assert waited == pid
    assert os.WIFSIGNALED(status)
    assert os.WTERMSIG(status) == signal.SIGKILL


def test_sigkill_after_submit_reconciles_marker_without_second_tool(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    responses = tmp_path / "responses"
    session = tmp_path / "rollout.jsonl"
    counter = tmp_path / "tool_effect_count"
    marker = "SEAL_TURN=chat_9001"
    session.write_text("", encoding="utf-8")
    active.write_text(json.dumps({
        "id": "chat_9001",
        "source": "ada_codex_poller",
        "status": "pending_submit",
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "turn_marker": marker,
        "submission_session_file": str(session),
        "submission_start_offset": 0,
    }), encoding="utf-8")

    pid = os.fork()
    if pid == 0:  # accepted by Codex; poller dies before status= submitted
        with session.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-9001"},
            }) + "\n")
            handle.write(json.dumps({
                "type": "event_msg",
                "payload": {"type": "user_message", "message": f"hazlo [{marker}]"},
            }) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _increment(counter)
        os.kill(os.getpid(), signal.SIGKILL)
    _wait_killed(pid)

    monkeypatch.setattr(poller, "RESPONSES_DIR", responses)
    assert poller.active_task_inflight(active)
    recovered = json.loads(active.read_text(encoding="utf-8"))
    assert recovered["status"] == "submitted"
    assert recovered["turn_id"] == "turn-9001"
    # A restarted writer sees ownership and therefore never runs the tool.
    if not poller.active_task_inflight(active):
        _increment(counter)
    assert counter.read_text(encoding="utf-8") == "1"


def test_sigkill_after_post_before_receipt_recovers_without_repost(monkeypatch, tmp_path):
    responses = tmp_path / "responses"
    journal = tmp_path / "responses.jsonl"
    db_row = tmp_path / "committed_chat_row.json"
    counter = tmp_path / "tool_effect_count"
    msg = bridge.ChatMessage(
        id=9002,
        sender="William",
        content="hazlo",
        created_at=datetime.now(timezone.utc),
        channel="dm:ada:william",
        message_type="conversation",
        metadata=None,
    )

    pid = os.fork()
    if pid == 0:
        bridge.TERMINAL_RESPONSES_DIR = responses
        bridge.TERMINAL_RESPONSES_JSONL = journal
        bridge.record_headless_completion(msg, "resultado único")
        _increment(counter)
        # This is the server-side commit. The process dies before its local
        # delivered receipt/cursor ACK is written.
        db_row.write_text(json.dumps({
            "id": 99002,
            "api_id": "api_ada_9002",
            "channel": msg.channel,
            "in_reply_to": "9002",
            "content": "resultado único",
        }), encoding="utf-8")
        os.kill(os.getpid(), signal.SIGKILL)
    _wait_killed(pid)

    monkeypatch.setattr(bridge, "TERMINAL_RESPONSES_DIR", responses)
    monkeypatch.setattr(bridge, "TERMINAL_RESPONSES_JSONL", journal)
    monkeypatch.setattr(
        bridge,
        "post_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not repost")),
    )

    class Conn:
        async def fetch(self, query, channel, in_reply_to, content):
            row = json.loads(db_row.read_text(encoding="utf-8"))
            assert (channel, in_reply_to, content) == (
                row["channel"], row["in_reply_to"], row["content"]
            )
            return [{"id": row["id"], "api_id": row["api_id"]}]

    recovered = bridge.asyncio.run(bridge.recover_headless_completion(Conn(), msg))
    assert recovered["status"] == "delivered"
    assert recovered["db_id"] == 99002
    assert counter.read_text(encoding="utf-8") == "1"


def test_dispatch_claim_is_kernel_released_after_sigkill(monkeypatch, tmp_path):
    claims = tmp_path / "claims"
    monkeypatch.setattr(poller, "DISPATCH_CLAIM_DIR", claims)
    ready_r, ready_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_r)
        poller.DISPATCH_CLAIM_DIR = claims
        claim = poller.acquire_dispatch_claim(9003)
        assert claim is not None
        os.write(ready_w, b"1")
        os.close(ready_w)
        os.kill(os.getpid(), signal.SIGKILL)
    os.close(ready_w)
    assert os.read(ready_r, 1) == b"1"
    os.close(ready_r)
    _wait_killed(pid)

    recovered_claim = poller.acquire_dispatch_claim(9003)
    assert recovered_claim is not None
    poller.release_dispatch_claim(recovered_claim)
