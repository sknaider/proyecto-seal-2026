"""Tests for ShellHookRegistry — uses real subprocess with safe commands."""

import json
import sys
import pathlib
import tempfile
import os

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.shell_hooks import (
    HookConfig,
    HookError,
    HookResult,
    ShellHookRegistry,
    _glob_match,
    _render_command,
)


# ── unit helpers ─────────────────────────────────────────────────────────────

def test_glob_wildcard():
    assert _glob_match("*", "Bash")
    assert _glob_match("*", "Write")
    assert _glob_match("*", "anything")


def test_glob_exact():
    assert _glob_match("Bash", "Bash")
    assert not _glob_match("Bash", "Write")


def test_glob_prefix_wildcard():
    assert _glob_match("mcp__seal*", "mcp__seal-memory__boot_context")
    assert not _glob_match("mcp__seal*", "Bash")


def test_render_command_substitution():
    tmpl = "echo {{tool_name}} {{agent}}"
    result = _render_command(tmpl, {"tool_name": "Bash", "agent": "ADA"})
    assert result == "echo Bash ADA"


def test_render_command_no_match_leaves_template():
    tmpl = "echo {{unknown}}"
    result = _render_command(tmpl, {"tool_name": "Bash"})
    assert result == "echo {{unknown}}"


# ── registry from dict ────────────────────────────────────────────────────────

def test_from_dict_loads_hooks():
    cfg = {
        "hooks": [
            {"event": "pre_tool", "command": "echo hi", "tool_pattern": "*"},
            {"event": "post_tool", "command": "echo done"},
        ]
    }
    reg = ShellHookRegistry.from_dict(cfg)
    assert len(reg.hooks_for("pre_tool")) == 1
    assert len(reg.hooks_for("post_tool")) == 1


def test_from_dict_empty():
    reg = ShellHookRegistry.from_dict({})
    assert reg.summary()["total"] == 0


def test_register_programmatic():
    reg = ShellHookRegistry()
    reg.register(HookConfig(event="pre_tool", command="true"))
    assert reg.summary()["total"] == 1


# ── execution ─────────────────────────────────────────────────────────────────

def test_run_executes_matching_hook():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "pre_tool", "command": "true", "tool_pattern": "*"}]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    assert len(results) == 1
    assert results[0].matched
    assert results[0].executed
    assert results[0].success


def test_run_skips_wrong_event():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "post_tool", "command": "true"}]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    assert len(results) == 0


def test_run_skips_non_matching_pattern():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "pre_tool", "command": "true", "tool_pattern": "Write"}]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    assert len(results) == 1
    assert not results[0].matched
    assert not results[0].executed


def test_run_captures_stdout():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "pre_tool", "command": "echo hello_world"}]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    assert results[0].stdout == "hello_world"


def test_run_captures_returncode_on_failure():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "pre_tool", "command": "exit 1"}]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    assert results[0].returncode == 1
    assert not results[0].success


def test_run_template_in_command():
    captured: list[str] = []
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "pre_tool", "command": "echo {{tool_name}}"}]
    })
    results = reg.run("pre_tool", tool_name="MyTool")
    assert results[0].stdout == "MyTool"


def test_run_with_custom_env():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{
            "event": "pre_tool",
            "command": "echo $SEAL_TEST_VAR",
            "env": {"SEAL_TEST_VAR": "nexus_was_here"},
        }]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    assert results[0].stdout == "nexus_was_here"


def test_timeout_returns_error():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "pre_tool", "command": "sleep 60", "timeout": 1}]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    assert not results[0].success
    assert "timed out" in (results[0].error or "")


# ── should_block ──────────────────────────────────────────────────────────────

def test_should_block_warn_does_not_block():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "pre_tool", "command": "exit 1", "on_failure": "warn"}]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    assert reg.should_block(results) is None


def test_should_block_block_mode_blocks():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "pre_tool", "command": "exit 1", "on_failure": "block"}]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    block_reason = reg.should_block(results)
    assert block_reason is not None
    assert "[shell_hook]" in block_reason


def test_should_block_success_does_not_block():
    reg = ShellHookRegistry.from_dict({
        "hooks": [{"event": "pre_tool", "command": "true", "on_failure": "block"}]
    })
    results = reg.run("pre_tool", tool_name="Bash")
    assert reg.should_block(results) is None


# ── file I/O ──────────────────────────────────────────────────────────────────

def test_from_file_loads_correctly():
    data = {"hooks": [{"event": "pre_tool", "command": "echo test"}]}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp_path = pathlib.Path(f.name)
    try:
        reg = ShellHookRegistry.from_file(tmp_path)
        assert reg.summary()["total"] == 1
    finally:
        tmp_path.unlink()


def test_from_file_missing_returns_empty():
    reg = ShellHookRegistry.from_file(pathlib.Path("/tmp/nonexistent_seal_hooks_99.json"))
    assert reg.summary()["total"] == 0


def test_save_and_reload():
    reg = ShellHookRegistry.from_dict({
        "hooks": [
            {"event": "pre_tool", "command": "echo a", "tool_pattern": "Bash"},
            {"event": "post_tool", "command": "echo b"},
        ]
    })
    with tempfile.TemporaryDirectory() as tmpdir:
        path = pathlib.Path(tmpdir) / "hooks.json"
        saved = reg.save(path)
        reg2 = ShellHookRegistry.from_file(saved)
        assert reg2.summary()["total"] == 2
        assert reg2.hooks_for("pre_tool")[0].command == "echo a"


# ── summary ───────────────────────────────────────────────────────────────────

def test_summary_by_event():
    reg = ShellHookRegistry.from_dict({
        "hooks": [
            {"event": "pre_tool", "command": "true"},
            {"event": "pre_tool", "command": "true"},
            {"event": "post_tool", "command": "true"},
        ]
    })
    s = reg.summary()
    assert s["total"] == 3
    assert s["by_event"]["pre_tool"] == 2
    assert s["by_event"]["post_tool"] == 1


def main() -> int:
    tests = [
        test_glob_wildcard,
        test_glob_exact,
        test_glob_prefix_wildcard,
        test_render_command_substitution,
        test_render_command_no_match_leaves_template,
        test_from_dict_loads_hooks,
        test_from_dict_empty,
        test_register_programmatic,
        test_run_executes_matching_hook,
        test_run_skips_wrong_event,
        test_run_skips_non_matching_pattern,
        test_run_captures_stdout,
        test_run_captures_returncode_on_failure,
        test_run_template_in_command,
        test_run_with_custom_env,
        test_timeout_returns_error,
        test_should_block_warn_does_not_block,
        test_should_block_block_mode_blocks,
        test_should_block_success_does_not_block,
        test_from_file_loads_correctly,
        test_from_file_missing_returns_empty,
        test_save_and_reload,
        test_summary_by_event,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
