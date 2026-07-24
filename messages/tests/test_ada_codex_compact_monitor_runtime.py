from pathlib import Path
import importlib.util
import json


MODULE_PATH = Path(__file__).resolve().parents[1] / "ada_codex_compact_monitor.py"
SPEC = importlib.util.spec_from_file_location("ada_codex_compact_monitor_runtime", MODULE_PATH)
monitor = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(monitor)


def test_visible_session_is_pinned_before_global_mtime(monkeypatch, tmp_path):
    root = tmp_path / "rollout-2026-07-16-root.jsonl"
    worker = tmp_path / "rollout-2026-07-17-worker.jsonl"
    root.write_text("{}\n")
    worker.write_text("{}\n")
    monitor._PINNED_VISIBLE_SESSION = None
    monitor._PINNED_VISIBLE_MISSES = 0
    monkeypatch.setattr(monitor, "_tmux_open_session_files", lambda: [root, worker])
    monkeypatch.setattr(monitor, "tmux_alive", lambda: True)
    assert monitor.find_active_session() == root


def test_visible_pin_survives_transient_fd_gap(monkeypatch, tmp_path):
    root = tmp_path / "rollout-root.jsonl"
    worker = tmp_path / "rollout-worker.jsonl"
    root.write_text("{}\n")
    worker.write_text("{}\n")
    monitor._PINNED_VISIBLE_SESSION = root
    monitor._PINNED_VISIBLE_MISSES = 0
    monkeypatch.setattr(monitor, "_tmux_open_session_files", lambda: [worker])
    monkeypatch.setattr(monitor, "tmux_alive", lambda: True)

    assert monitor.find_active_session() == root
    assert monitor._PINNED_VISIBLE_MISSES == 0


def test_live_tmux_never_switches_pin_to_worker(monkeypatch, tmp_path):
    root = tmp_path / "rollout-root.jsonl"
    replacement = tmp_path / "rollout-replacement.jsonl"
    root.write_text("{}\n")
    replacement.write_text("{}\n")
    monitor._PINNED_VISIBLE_SESSION = root
    monitor._PINNED_VISIBLE_MISSES = monitor._PINNED_VISIBLE_MISS_LIMIT - 1
    monkeypatch.setattr(monitor, "_tmux_open_session_files", lambda: [replacement])
    monkeypatch.setattr(monitor, "tmux_alive", lambda: True)

    assert monitor.find_active_session() == root
    assert monitor._PINNED_VISIBLE_MISSES == 0


def test_live_tmux_without_fd_evidence_does_not_watch_headless_session(monkeypatch, tmp_path):
    monitor._PINNED_VISIBLE_SESSION = None
    monitor._PINNED_VISIBLE_MISSES = 0
    monkeypatch.setattr(monitor, "_tmux_open_session_files", lambda: [])
    monkeypatch.setattr(monitor, "tmux_alive", lambda: True)
    monkeypatch.setattr(monitor, "SESSIONS_DIR", tmp_path)
    (tmp_path / "headless.jsonl").write_text("{}\n")

    assert monitor.find_active_session() is None


def test_pinned_session_survives_transient_tmux_probe_failure(monkeypatch, tmp_path):
    root = tmp_path / "rollout-root.jsonl"
    root.write_text("{}\n")
    monitor._PINNED_VISIBLE_SESSION = root
    monitor._PINNED_VISIBLE_MISSES = 0
    monkeypatch.setattr(monitor, "_tmux_open_session_files", lambda: [])
    monkeypatch.setattr(monitor, "tmux_alive", lambda: False)
    monkeypatch.setattr(monitor, "SESSIONS_DIR", tmp_path)

    assert monitor.find_active_session() == root
    assert monitor._PINNED_VISIBLE_MISSES == 1


def test_monitor_fallback_does_not_import_torch_embeddings():
    source = MODULE_PATH.read_text(encoding="utf-8")
    fallback = source.split("async def db_store_auto_checkpoint", 1)[1].split("def store_auto_checkpoint", 1)[0]
    assert "from embeddings import" not in fallback
    assert "NULL" in fallback


def test_heartbeat_is_atomic_private_and_contains_no_transcript_content(
    monkeypatch, tmp_path
):
    heartbeat = tmp_path / "compact-monitor.heartbeat.json"
    session = tmp_path / "rollout-private-session.jsonl"
    secret = "PRIVATE_TRANSCRIPT_CONTENT_MUST_NOT_LEAK"
    session.write_text(secret)
    monkeypatch.setattr(monitor, "HEARTBEAT_FILE", heartbeat)

    monitor.write_heartbeat(session_path=session, context_pct=42.125, now=1234.0)

    payload = json.loads(heartbeat.read_text())
    assert payload["watcher"] == "ada_codex_compact_monitor"
    assert payload["status"] == "observing"
    assert payload["session_file"] == session.name
    assert payload["context_pct"] == 42.12
    assert secret not in heartbeat.read_text()
    assert heartbeat.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_heartbeat_distinguishes_idle_from_missing_token_count(monkeypatch, tmp_path):
    heartbeat = tmp_path / "compact-monitor.heartbeat.json"
    session = tmp_path / "rollout.jsonl"
    monkeypatch.setattr(monitor, "HEARTBEAT_FILE", heartbeat)

    monitor.write_heartbeat(session_path=None, context_pct=None, now=1234.0)
    assert json.loads(heartbeat.read_text())["status"] == "waiting_for_session"

    monitor.write_heartbeat(session_path=session, context_pct=None, now=1235.0)
    assert json.loads(heartbeat.read_text())["status"] == "waiting_for_token_count"
