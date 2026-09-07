from __future__ import annotations

import json
from pathlib import Path
import shlex


ROOT = Path(__file__).resolve().parents[1]
HOOKS_PATH = ROOT / ".codex" / "hooks.json"
PRIVATE_LOG = Path("/home/dadito/.local/state/seal/ada_codex_injection_probe.jsonl")


def _load_hooks() -> dict:
    payload = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert isinstance(payload.get("hooks"), dict)
    return payload


def _handler(payload: dict, event: str) -> tuple[dict, dict]:
    groups = payload["hooks"][event]
    assert len(groups) == 1
    handlers = groups[0]["hooks"]
    assert len(handlers) == 1
    return groups[0], handlers[0]


def test_codex_hooks_use_the_existing_soul_layers() -> None:
    payload = _load_hooks()

    pre_group, pre = _handler(payload, "PreToolUse")
    assert pre_group["matcher"] == ".*"
    assert pre["type"] == "command"
    assert pre["timeout"] == 5
    assert pre["command"] == (
        "/home/dadito/IA/seal-spark/.venv/bin/python3 "
        "/home/dadito/IA/proyecto-seal/memory/nerves_read_only_action_guard.py"
    )

    post_group, post = _handler(payload, "PostToolUse")
    assert post_group["matcher"] == "^(Bash|Read|Task|Agent|mcp__.*)$"
    assert post["type"] == "command"
    assert post["timeout"] == 5
    assert "SEAL_AGENT=ADA" in post["command"]
    assert str(PRIVATE_LOG) in post["command"]
    assert "memory/nerves_injection_probe.py" in post["command"]


def test_hook_commands_are_absolute_and_exist() -> None:
    payload = _load_hooks()
    _, pre = _handler(payload, "PreToolUse")
    pre_argv = shlex.split(pre["command"])
    assert Path(pre_argv[0]).is_absolute() and Path(pre_argv[0]).exists()
    assert Path(pre_argv[1]).is_absolute() and Path(pre_argv[1]).exists()

    _, post = _handler(payload, "PostToolUse")
    post_argv = shlex.split(post["command"])
    executable = Path(post_argv[post_argv.index("exec") + 1])
    script = Path(post_argv[post_argv.index("exec") + 2])
    assert executable.is_absolute() and executable.exists()
    assert script.is_absolute() and script.exists()


def test_post_hook_never_uses_public_tmp_for_payload_or_log() -> None:
    payload = _load_hooks()
    _, post = _handler(payload, "PostToolUse")
    command = post["command"]
    assert "umask 077" in command
    assert "/tmp/" not in command
    assert "tee " not in command
    assert PRIVATE_LOG.parent == Path("/home/dadito/.local/state/seal")
