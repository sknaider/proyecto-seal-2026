from __future__ import annotations

import asyncio

import spectre_sandbox_tools as tools


class _Result:
    returncode = 0
    stdout = ""
    stderr = ""


def test_write_file_never_interpolates_path_into_shell(monkeypatch):
    seen = {}

    def fake(args, input_data=None):
        seen["args"] = args
        seen["input"] = input_data
        return _Result()

    monkeypatch.setattr(tools, "_docker_exec", fake)
    payload = "notes/x; printf INJECTED >/tmp/pwned #"
    result = asyncio.run(tools.write_file(payload, "contenido"))
    assert "write_file OK" in result
    assert seen["args"][:2] == ["python3", "-c"]
    assert seen["args"][-4:] == ["notes", "x; printf INJECTED >", "tmp", "pwned #"]
    assert "bash" not in seen["args"][:2]


def test_metacharacters_are_literal_argv_parts(monkeypatch):
    seen = {}
    def fake(args, input_data=None):
        seen["call"] = (args, input_data)
        return _Result()
    monkeypatch.setattr(tools, "_docker_exec", fake)
    asyncio.run(tools.write_file("notes/a;$(touch pwned).txt", "x"))
    args, body = seen["call"]
    assert args[-2:] == ["notes", "a;$(touch pwned).txt"]
    assert "bash" not in args[:2]
    assert body == "x"


def test_traversal_newlines_and_empty_paths_are_rejected(monkeypatch):
    monkeypatch.setattr(
        tools, "_docker_exec", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )
    for path in ("", "../escape", "a/../../escape", "/absolute", "a\nname"):
        assert "solo rutas relativas" in asyncio.run(tools.write_file(path, "x"))
