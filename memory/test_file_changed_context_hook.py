import json
from pathlib import Path

from memory import file_changed_context_hook as hook


def _write(tmp_path: Path, payload: dict) -> Path:
    # The production reader intentionally rejects paths outside messages.
    path = hook.ALLOWED_ROOT / f"pytest_file_changed_{tmp_path.name}.jsonl"
    path.write_text("not-json-old-line\n" + json.dumps(payload) + "\n")
    return path


def test_each_kind_emits_supported_file_changed_schema(tmp_path) -> None:
    path = _write(tmp_path, {"type": "alert", "message": "hola", "count": 2, "from": "William"})
    try:
        for kind in (
            "vscode_command",
            "jarvis_wakeup",
            "ada_soul_alert",
            "jarvis_soul_alert",
            "nexus_inbox",
        ):
            output = hook.build_output(kind, path, expected_agent="ADA", current_agent="ADA")
            specific = output["hookSpecificOutput"]
            assert specific["hookEventName"] == "FileChanged"
            assert isinstance(specific["additionalContext"], str)
            assert specific["additionalContext"]
    finally:
        path.unlink(missing_ok=True)


def test_missing_malformed_and_outside_paths_are_safe_noops(tmp_path) -> None:
    assert hook.build_output(
        "vscode_command", tmp_path / "missing", expected_agent="ADA", current_agent="ADA"
    ) == {}
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps({"message": "private"}))
    assert hook.build_output(
        "vscode_command", outside, expected_agent="ADA", current_agent="ADA"
    ) == {}


def test_last_message_is_bounded(tmp_path) -> None:
    path = _write(tmp_path, {"message": "x" * 1000, "from": "A"})
    try:
        output = hook.build_output(
            "nexus_inbox", path, expected_agent="NEXUS", current_agent="NEXUS"
        )
        context = output["hookSpecificOutput"]["additionalContext"]
        assert len(context) <= 420
    finally:
        path.unlink(missing_ok=True)


def test_cross_agent_event_is_a_safe_noop_before_reading_file(tmp_path) -> None:
    # A NEXUS DM path must never be opened by an ADA/JARVIS process.
    outside = tmp_path / "private.jsonl"
    outside.write_text(json.dumps({"message": "secret"}))
    assert hook.build_output(
        "nexus_inbox", outside, expected_agent="NEXUS", current_agent="ADA"
    ) == {}
