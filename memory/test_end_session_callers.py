from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CALLERS = (
    "ada.sh",
    "ada_fresh.sh",
    "alice.sh",
    "alice_fresh.sh",
    "jarvis.sh",
    "jarvis_fresh.sh",
    "nexus.sh",
    "nexus_fresh.sh",
)


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_callers_do_not_hide_end_session_stderr() -> None:
    for name in CALLERS:
        for line in _source(name).splitlines():
            if "end_session.sh" in line and not line.lstrip().startswith("#"):
                assert "2>/dev/null" not in line, name


def test_callers_gate_success_claim_on_end_session_exit() -> None:
    for name in CALLERS:
        source = _source(name)
        assert "soul capture failed" in source, name
        if name != "jarvis.sh":
            assert "if /home/dadito/IA/proyecto-seal/memory/end_session.sh" in source, name


def test_jarvis_captures_once_across_explicit_and_exit_trap_paths() -> None:
    source = _source("jarvis.sh")
    assert "_JARVIS_END_SESSION_DONE=0" in source
    assert "_capture_jarvis_end_session_once" in source
    assert "|| true" not in next(
        line for line in source.splitlines() if "bash /home/dadito/IA/proyecto-seal/memory/end_session.sh" in line
    )


def test_jarvis_marks_capture_done_only_after_success() -> None:
    source = _source("jarvis.sh")
    function = source.split("_capture_jarvis_end_session_once() {", 1)[1].split("\n}", 1)[0]
    run_at = function.index("bash /home/dadito/IA/proyecto-seal/memory/end_session.sh JARVIS")
    done_at = function.index("_JARVIS_END_SESSION_DONE=1")
    assert run_at < done_at
    assert 'if [ "$rc" -eq 0 ]; then' in function
    assert 'return "$rc"' in function
