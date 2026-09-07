import json
from unittest.mock import patch

from memory import auto_compact_watchdog as watchdog


def setup_function() -> None:
    watchdog._last_attempt.clear()
    watchdog._last_success.clear()
    watchdog._limit_latched.clear()
    watchdog._contract_alerts.clear()


def test_all_live_siblings_are_covered() -> None:
    assert set(watchdog.AGENTS) == {"ADA", "ALICE", "FABLE", "JARVIS", "NEXUS"}


def test_limit_is_edge_triggered_and_rearms_after_disappearing() -> None:
    phrase = watchdog.CONTEXT_LIMIT_PHRASES[0]
    with (
        patch.object(watchdog.Path, "exists", return_value=True),
        patch.object(watchdog, "read_kitty_window", side_effect=[phrase, phrase, "ready", phrase]),
        patch.object(watchdog, "send_compact", return_value=True) as send,
        patch.object(watchdog, "notify_webchat"),
        patch.object(watchdog, "audit_compact_contract", return_value=(True, "ok")),
        patch.object(watchdog, "persist_success_state"),
        patch.object(watchdog.time, "monotonic", side_effect=[1000.0, 3001.0]),
        patch.object(watchdog, "wall_clock", side_effect=[10_000.0, 12_001.0]),
    ):
        for _ in range(4):
            watchdog.check_agent("NEXUS", watchdog.AGENTS["NEXUS"])

    assert send.call_count == 2


def test_failed_send_retries_only_after_cooldown() -> None:
    phrase = watchdog.CONTEXT_LIMIT_PHRASES[0]
    with (
        patch.object(watchdog.Path, "exists", return_value=True),
        patch.object(watchdog, "read_kitty_window", return_value=phrase),
        patch.object(watchdog, "send_compact", return_value=False) as send,
        patch.object(watchdog, "audit_compact_contract", return_value=(True, "ok")),
        patch.object(watchdog.time, "monotonic", side_effect=[1000.0, 1050.0, 1121.0]),
        patch.object(watchdog, "wall_clock", side_effect=[10_000.0, 10_121.0]),
    ):
        for _ in range(3):
            watchdog.check_agent("NEXUS", watchdog.AGENTS["NEXUS"])

    assert send.call_count == 2


def test_success_circuit_survives_alert_rearm() -> None:
    phrase = watchdog.CONTEXT_LIMIT_PHRASES[0]
    with (
        patch.object(watchdog.Path, "exists", return_value=True),
        patch.object(watchdog, "read_kitty_window", side_effect=[phrase, "ready", phrase]),
        patch.object(watchdog, "send_compact", return_value=True) as send,
        patch.object(watchdog, "notify_webchat"),
        patch.object(watchdog, "audit_compact_contract", return_value=(True, "ok")),
        patch.object(watchdog, "persist_success_state"),
        patch.object(watchdog.time, "monotonic", side_effect=[1000.0, 1201.0]),
        patch.object(watchdog, "wall_clock", side_effect=[10_000.0, 10_201.0]),
    ):
        for _ in range(3):
            watchdog.check_agent("NEXUS", watchdog.AGENTS["NEXUS"])

    assert send.call_count == 1


def test_persisted_success_state_is_validated(tmp_path) -> None:
    state = tmp_path / "compact.json"
    state.write_text(json.dumps({"NEXUS": 1234, "UNKNOWN": 9999, "ADA": "bad"}))

    assert watchdog.load_success_state(state) == {"NEXUS": 1234.0}


def test_contract_rejects_duplicate_compact_boots(tmp_path) -> None:
    hook = tmp_path / "post_compact_session_start_hook.py"
    hook.write_text("print('passive')")
    settings = []
    for index in range(2):
        path = tmp_path / f"settings-{index}.json"
        path.write_text(json.dumps({
            "hooks": {"SessionStart": [{
                "matcher": "compact",
                "hooks": [{"type": "command", "command": f"python {hook}"}],
            }]},
        }))
        settings.append(path)

    ok, reason = watchdog.audit_compact_contract(settings, hook)

    assert not ok
    assert "found 2" in reason


def test_contract_accepts_one_passive_hook(tmp_path) -> None:
    hook = tmp_path / "post_compact_session_start_hook.py"
    hook.write_text("print('passive bounded context')")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"SessionStart": [{
            "matcher": "compact",
            "hooks": [{"type": "command", "command": f"python {hook}"}],
        }]},
    }))

    assert watchdog.audit_compact_contract([settings], hook) == (
        True, "one passive bounded SessionStart:compact hook"
    )


def test_unsafe_contract_blocks_send_and_alerts_once() -> None:
    phrase = watchdog.CONTEXT_LIMIT_PHRASES[0]
    with (
        patch.object(watchdog.Path, "exists", return_value=True),
        patch.object(watchdog, "read_kitty_window", return_value=phrase),
        patch.object(watchdog, "audit_compact_contract", return_value=(False, "duplicate boot")),
        patch.object(watchdog, "send_compact") as send,
        patch.object(watchdog, "notify_webchat") as notify,
    ):
        watchdog.check_agent("NEXUS", watchdog.AGENTS["NEXUS"])
        watchdog.check_agent("NEXUS", watchdog.AGENTS["NEXUS"])

    send.assert_not_called()
    assert notify.call_count == 1
