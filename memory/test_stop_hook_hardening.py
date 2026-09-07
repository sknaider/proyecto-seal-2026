import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import session_handoff_hook
import turn_extract_stop_hook


def test_fable_identity_is_supported(monkeypatch) -> None:
    monkeypatch.setenv("SEAL_AGENT", "FABLE")
    assert turn_extract_stop_hook.detect_agent_from_cwd("/memory") == "FABLE"
    assert session_handoff_hook.detect_agent("/memory") == "FABLE"


def test_turn_hook_prefers_exact_transcript(tmp_path: Path) -> None:
    transcript = tmp_path / "exact.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env["SEAL_AGENT"] = "FABLE"
    payload = {"cwd": "/home/dadito/IA/proyecto-seal/memory", "transcript_path": str(transcript)}
    result = subprocess.run(
        [sys.executable, str(Path(turn_extract_stop_hook.__file__))],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == ""
