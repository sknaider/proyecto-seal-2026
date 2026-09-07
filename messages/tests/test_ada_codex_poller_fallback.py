import importlib.util
import json
import os
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "ada_codex_poller.py"
spec = importlib.util.spec_from_file_location("ada_codex_poller_fallback", MODULE)
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)


def _event(message="ada responde", event_id="api_william_1"):
    return {
        "id": event_id,
        "from": "William",
        "to": "equipo",
        "timestamp": "2026-07-11T00:00:00-05:00",
        "type": "conversation",
        "message": message,
        "channel": "web_chat",
        "provenance": {"verified": True, "verified_sender": "William"},
    }


def test_first_cursor_starts_at_eof(tmp_path):
    feed = tmp_path / "feed.jsonl"
    feed.write_text(json.dumps(_event()) + "\n", encoding="utf-8")
    assert poller.load_public_event_offset(feed, tmp_path / "missing.cursor") == feed.stat().st_size


def test_batch_handles_truncation_and_returns_offsets(tmp_path):
    feed = tmp_path / "feed.jsonl"
    rows = [_event("ada uno", "m1"), _event("ada dos", "m2")]
    feed.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    batch, end = poller.read_public_event_batch(feed, 999999)
    assert [row["id"] for _, row in batch] == ["m1", "m2"]
    assert end == feed.stat().st_size
    assert batch[-1][0] == end


def test_only_verified_human_public_ada_event_is_accepted():
    assert poller.is_verified_public_ada_event(_event())
    bad = _event(); bad["provenance"]["verified"] = False
    assert not poller.is_verified_public_ada_event(bad)
    no_name = _event("hola equipo")
    assert not poller.is_verified_public_ada_event(no_name)
    forged = _event(); forged["provenance"]["verified_sender"] = "NEXUS"
    assert not poller.is_verified_public_ada_event(forged)


def test_primary_session_dedup_by_id_or_content(tmp_path, monkeypatch):
    session = tmp_path / "primary.jsonl"
    event = _event("ada arreglate", "api_william_77")
    session.write_text(json.dumps({"type": "event_msg", "payload": event}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(poller, "find_active_session", lambda: session)
    assert poller.primary_session_contains_event(event)
    assert poller.primary_session_contains_event(_event("ada arreglate", "other-id"))
    assert not poller.primary_session_contains_event(_event("ada mensaje diferente", "other-id"))


def test_format_public_event_keeps_source_and_channel():
    rendered = poller.format_public_event(_event("ada hola", "api_william_9"))
    assert rendered.startswith("[William @ 00:00 / web_chat id api_william_9]")
    assert rendered.endswith("ada hola")

