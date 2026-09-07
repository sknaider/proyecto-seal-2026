from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
TRACE = ROOT / "tools" / "oauth_credential_actor_trace.bt"


def test_trace_is_metadata_only_and_resolves_scope_inline() -> None:
    source = TRACE.read_text(encoding="utf-8")
    assert "cgroup_path(cgroup)" in source
    assert source.count("cgroup_path(cgroup)") == 5
    assert "uid == 1000" in source
    assert '.credentials.json' in source
    assert "cat(" not in source
    assert "buf(" not in source
    assert "str(args.filename)" in source


def test_trace_covers_available_path_mutation_syscalls() -> None:
    source = TRACE.read_text(encoding="utf-8")
    expected = {
        "tracepoint:syscalls:sys_enter_openat",
        "tracepoint:syscalls:sys_enter_openat2",
        "tracepoint:syscalls:sys_enter_renameat",
        "tracepoint:syscalls:sys_enter_renameat2",
        "tracepoint:syscalls:sys_enter_unlinkat",
    }
    assert all(probe in source for probe in expected)
