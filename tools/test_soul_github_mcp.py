from __future__ import annotations

import io
import json
import urllib.error

import pytest

import tools.soul_github_mcp as server
from fable.mcp_web_soul_control import BrowserControlPlane
from fable.mcp_web_soul_security import AuditTrail


class _Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.body[:size]


def test_request_is_get_fixed_host_and_bearer_never_returned(monkeypatch):
    seen = {}
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "secret-token")

    def fake_open(request, timeout):
        seen.update(url=request.full_url, method=request.method, headers=dict(request.headers), timeout=timeout)
        return _Response({"items": []})

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_open)
    assert server.search_repositories("user:sknaider") == {
        "total_count": 0,
        "incomplete_results": False,
        "items": [],
    }
    assert seen["url"].startswith("https://api.github.com/search/repositories?")
    assert seen["method"] == "GET"
    assert seen["headers"]["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in json.dumps(server.search_repositories("user:sknaider"))


def test_http_error_is_redacted(monkeypatch):
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "secret-token")

    def fake_open(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.github.com/x?secret=x", 403, "forbidden token=secret", {}, io.BytesIO(b"secret")
        )

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_open)
    with pytest.raises(RuntimeError, match="GitHub API HTTP 403") as exc:
        server._request("/x")
    assert "secret" not in str(exc.value)


def test_file_content_is_bounded_and_binary_omitted(monkeypatch):
    monkeypatch.setattr(
        server,
        "_request",
        lambda *args, **kwargs: {
            "type": "file",
            "name": "blob.bin",
            "path": "blob.bin",
            "sha": "abc",
            "size": 2,
            "encoding": "base64",
            "content": "/wA=",
        },
    )
    result = server.get_file_contents("sknaider", "repo", "blob.bin")
    assert result["content_omitted"] == "binary"
    assert "content" not in result


def test_invalid_repo_fails_before_network():
    with pytest.raises(ValueError):
        server.get_issue("../evil", "repo", 1)


def test_tool_surface_has_native_read_and_mutation_operations():
    mutation_names = {
        "create_or_update_file", "create_repository", "push_files", "create_issue",
        "create_pull_request", "fork_repository", "create_branch", "update_issue",
        "add_issue_comment", "create_pull_request_review", "merge_pull_request",
        "update_pull_request_branch",
    }
    assert mutation_names.issubset(server.mcp._tool_manager._tools)
    assert "github_approval" in server.mcp._tool_manager._tools
    assert len(server.mcp._tool_manager._tools) == 27


def test_mutation_is_denied_then_single_use_approved_and_audited(monkeypatch, tmp_path):
    control = BrowserControlPlane(tmp_path / "control.sqlite3")
    audit = AuditTrail(tmp_path / "audit" / "events.jsonl", agent="TEST")
    monkeypatch.setattr(server, "CONTROL", control)
    monkeypatch.setattr(server, "AUDIT", audit)
    monkeypatch.setattr(server, "_SESSION_ID", None)
    calls = []
    monkeypatch.setattr(
        server,
        "_request",
        lambda path, params=None, **kwargs: calls.append((path, kwargs)) or {"number": 7},
    )
    arguments = {
        "owner": "sknaider", "repo": "repo", "title": "Issue",
        "body": "private body", "labels": None, "assignees": None,
    }

    denied = server.create_issue(**arguments)
    assert denied["error_code"] == "approval_required"
    assert calls == []

    requested = control.request_approval(
        session_id=server._session_id(), tool="create_issue", arguments=arguments
    )
    control.approve(requested["approval_id"], operator="William")
    result = server.create_issue(**arguments, approval_id=requested["approval_id"])
    assert result["number"] == 7
    assert calls[0][1]["method"] == "POST"
    assert "private body" not in audit.path.read_text(encoding="utf-8")

    replay = server.create_issue(**arguments, approval_id=requested["approval_id"])
    assert replay["error_code"] == "approval_state"
    assert len(calls) == 1


def test_every_mutation_fails_before_network_without_approval(monkeypatch, tmp_path):
    control = BrowserControlPlane(tmp_path / "control.sqlite3")
    monkeypatch.setattr(server, "CONTROL", control)
    monkeypatch.setattr(server, "AUDIT", AuditTrail(tmp_path / "audit.jsonl", agent="TEST"))
    monkeypatch.setattr(server, "_SESSION_ID", None)
    monkeypatch.setattr(
        server, "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network reached")),
    )
    cases = [
        (server.create_or_update_file, dict(owner="o", repo="r", path="a", message="m", content="c", branch="main")),
        (server.create_repository, dict(name="r")),
        (server.push_files, dict(owner="o", repo="r", branch="main", files=[{"path": "a", "content": "c"}], message="m")),
        (server.create_issue, dict(owner="o", repo="r", title="t")),
        (server.create_pull_request, dict(owner="o", repo="r", title="t", head="h", base="main")),
        (server.fork_repository, dict(owner="o", repo="r")),
        (server.create_branch, dict(owner="o", repo="r", branch="b")),
        (server.update_issue, dict(owner="o", repo="r", issue_number=1, title="t")),
        (server.add_issue_comment, dict(owner="o", repo="r", issue_number=1, body="b")),
        (server.create_pull_request_review, dict(owner="o", repo="r", pull_number=1, event="COMMENT")),
        (server.merge_pull_request, dict(owner="o", repo="r", pull_number=1)),
        (server.update_pull_request_branch, dict(owner="o", repo="r", pull_number=1)),
    ]
    for function, arguments in cases:
        result = function(**arguments)
        assert result["error_code"] == "approval_required", function.__name__
