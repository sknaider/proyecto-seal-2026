from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


HOOK = Path(__file__).with_name("pre_tool_hook.py")


def _run_hook(event: dict, *, token: str = "synthetic-token-never-persist") -> dict:
    env = dict(os.environ)
    env["SEAL_AGENT"] = "ADA"
    env["SEAL_SESSION_TOKEN"] = token
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert token not in proc.stdout
    return json.loads(proc.stdout)


def test_mcp_tool_does_not_copy_bearer_secret_into_tool_input() -> None:
    result = _run_hook(
        {
            "tool_name": "mcp__seal-memory__memory_search",
            "tool_input": {"query": "estado"},
        }
    )
    assert result == {}


def test_memory_store_enrichment_preserves_payload_without_session_token() -> None:
    result = _run_hook(
        {
            "tool_name": "mcp__seal-memory__memory_store",
            "tool_input": {"content": "hito", "category": "operational"},
        }
    )
    updated = result["hookSpecificOutput"]["updatedInput"]
    assert updated["agent"] == "ADA"
    assert updated["content"] == "hito"
    assert "timestamp" in updated
    assert "session_token" not in updated


def test_unquoted_heredoc_is_blocked_before_shell_can_expand_text() -> None:
    result = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 - <<PY\nprint('control `0600`')\nPY",
            },
        }
    )
    decision = result["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Unquoted heredoc" in decision["permissionDecisionReason"]


def test_quoted_heredoc_and_stdin_style_remain_available() -> None:
    quoted = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 - <<'PY'\nprint('control `0600`')\nPY",
            },
        }
    )
    stdin_style = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "python3 safe_writer.py --stdin"},
        }
    )
    assert quoted == {}
    assert stdin_style == {}
