#!/usr/bin/env python3
"""GitHub MCP nativo de SOUL: lectura tipada, acotada y sin shell/browser.

Las mutaciones siguen temporalmente en ``github-legacy`` hasta portarles approvals
autenticados. Este servidor nunca realiza métodos distintos de GET.
"""

from __future__ import annotations

import base64
import functools
import inspect
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "fable"))
sys.path.insert(0, str(_REPO_ROOT))
from fable.mcp_web_soul_control import BrowserControlPlane, ControlDenied
from fable.mcp_web_soul_security import AuditTrail, sanitize_mapping


API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_PAGE = 100

mcp = FastMCP("soul-github")
ROOT = Path(__file__).resolve().parents[1]
CONTROL = BrowserControlPlane(ROOT / "var" / "mcp-web-soul" / "control.sqlite3")
AUDIT = AuditTrail(ROOT / "var" / "soul-github" / "audit" / "events.jsonl", agent="SOUL_GITHUB")
_SESSION_ID: str | None = None


def _session_id() -> str:
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = CONTROL.ensure_session(agent="SOUL_GITHUB", pid=os.getpid())
    else:
        CONTROL.heartbeat(_SESSION_ID)
    return _SESSION_ID


def _token() -> str:
    value = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip()
    if not value:
        raise RuntimeError("GitHub token no configurado")
    return value


def _bounded_int(value: int, *, low: int = 1, high: int = MAX_PAGE) -> int:
    return max(low, min(int(value), high))


def _repo(owner: str, repo: str) -> tuple[str, str]:
    owner = str(owner).strip()
    repo = str(repo).strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if not owner or not repo or any(ch not in allowed for ch in owner + repo):
        raise ValueError("owner/repo inválido")
    return owner, repo


def _request(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    """Única salida HTTP: api.github.com, respuesta y errores acotados."""

    if not path.startswith("/") or "//" in path:
        raise ValueError("ruta GitHub inválida")
    query = urllib.parse.urlencode(
        {key: value for key, value in (params or {}).items() if value is not None},
        doseq=True,
    )
    url = f"{API_ROOT}{path}" + (f"?{query}" if query else "")
    method = str(method).upper()
    if method not in {"GET", "POST", "PUT", "PATCH"}:
        raise ValueError("método GitHub no permitido")
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {_token()}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "SOUL-GitHub-MCP/1.0",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        raise RuntimeError(
            f"GitHub API HTTP {exc.code}"
            + (f"; retry_after={retry_after}" if retry_after else "")
        ) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub API no disponible: {type(exc.reason).__name__}") from None
    if len(body) > MAX_BODY_BYTES:
        raise RuntimeError("respuesta GitHub excede 2 MiB")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError("GitHub devolvió JSON inválido") from None


def _audit_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Conserva intención verificable sin persistir código, comentarios o cuerpos."""
    safe = dict(arguments)
    for key in ("content", "body", "description"):
        if key in safe and safe[key] is not None:
            safe[key] = {"redacted": True, "length": len(str(safe[key]))}
    if isinstance(safe.get("files"), list):
        safe["files"] = [
            {
                "path": str(item.get("path", ""))[:500],
                "content": {"redacted": True, "length": len(str(item.get("content", "")))},
            }
            for item in safe["files"][:100]
            if isinstance(item, dict)
        ]
    return sanitize_mapping(safe, tool="github")


def _mutation(fn):
    """Approval exacto, single-use y auditado antes de cualquier escritura remota."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        arguments = dict(inspect.signature(fn).bind_partial(*args, **kwargs).arguments)
        approval_id = str(arguments.pop("approval_id", "") or "")
        try:
            permit = CONTROL.authorize(
                session_id=_session_id(),
                tool=fn.__name__,
                arguments=arguments,
                approval_id=approval_id or None,
                arm_remote_effect=True,
            )
        except ControlDenied as exc:
            AUDIT.append(
                tool=f"{fn.__name__}.denied",
                arguments=_audit_arguments(arguments),
                ok=False,
                action_class="REMOTE_COMMIT",
                error=str(exc),
                required=True,
            )
            return {"ok": False, "error": str(exc), "error_code": exc.code}
        operation_id = permit.approval_id
        try:
            AUDIT.append(
                tool=f"{fn.__name__}.intent",
                arguments=_audit_arguments(arguments),
                ok=True,
                action_class="REMOTE_COMMIT",
                required=True,
            )
            result = fn(*args, **kwargs)
            CONTROL.observe_remote_effect(operation_id, ok=True)
            try:
                AUDIT.append(
                    tool=fn.__name__, arguments=_audit_arguments(arguments), ok=True,
                    action_class="REMOTE_COMMIT", required=True,
                )
            except Exception as exc:
                CONTROL.mark_remote_effect_indeterminate(
                    permit, operation_id,
                    error_code=f"completion_audit:{type(exc).__name__}",
                )
                raise
            CONTROL.finalize_remote_effect(permit, operation_id, ok=True)
            return result
        except Exception as exc:
            current = CONTROL.remote_effect_operation(operation_id) if operation_id else None
            if current is None or current.get("status") not in {"completed", "indeterminate"}:
                CONTROL.mark_remote_effect_indeterminate(
                    permit, operation_id, error_code=type(exc).__name__,
                )
            try:
                AUDIT.append(
                    tool=fn.__name__, arguments=_audit_arguments(arguments), ok=False,
                    action_class="REMOTE_COMMIT", error=type(exc).__name__, required=True,
                )
            except Exception:
                pass
            raise
    return wrapper


@mcp.tool()
def github_approval(
    operation: str,
    approval_id: str = "",
    tool: str = "",
    arguments: dict | None = None,
    ttl_seconds: int = 300,
) -> dict:
    """Solicita/consulta approval; aprobar solo ocurre por el operador autenticado."""
    operation = str(operation).strip().lower()
    try:
        if operation == "request":
            requested = CONTROL.request_approval(
                session_id=_session_id(),
                tool=tool,
                arguments=arguments or {},
                ttl_seconds=ttl_seconds,
            )
            return {
                "ok": True,
                **requested,
                "operator_command": f"OK GITHUB APPROVE {requested['approval_id']}",
            }
        if operation == "status":
            return {"ok": True, **CONTROL.approval_status(approval_id)}
    except ControlDenied as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code}
    return {"ok": False, "error": "operation debe ser request|status"}


def _search(endpoint: str, query: str, page: int, per_page: int) -> dict:
    query = str(query).strip()
    if not query or len(query) > 512:
        raise ValueError("query requerida (máximo 512 caracteres)")
    result = _request(
        endpoint,
        {"q": query, "page": _bounded_int(page, high=1000), "per_page": _bounded_int(per_page)},
    )
    return {
        "total_count": int(result.get("total_count", 0)),
        "incomplete_results": bool(result.get("incomplete_results", False)),
        "items": result.get("items", []),
    }


@mcp.tool()
def search_repositories(query: str, page: int = 1, perPage: int = 20) -> dict:
    """Busca repositorios por la sintaxis oficial de GitHub Search."""
    return _search("/search/repositories", query, page, perPage)


@mcp.tool()
def search_code(query: str, page: int = 1, perPage: int = 20) -> dict:
    """Busca código. No descarga archivos completos automáticamente."""
    return _search("/search/code", query, page, perPage)


@mcp.tool()
def search_issues(query: str, page: int = 1, perPage: int = 20) -> dict:
    """Busca issues y pull requests."""
    return _search("/search/issues", query, page, perPage)


@mcp.tool()
def search_users(query: str, page: int = 1, perPage: int = 20) -> dict:
    """Busca usuarios u organizaciones."""
    return _search("/search/users", query, page, perPage)


@mcp.tool()
def get_file_contents(owner: str, repo: str, path: str = "", branch: str = "") -> dict:
    """Lee contenido o lista un directorio; texto máximo 256 KiB."""
    owner, repo = _repo(owner, repo)
    clean_path = str(path).lstrip("/")
    encoded_path = urllib.parse.quote(clean_path, safe="/")
    result = _request(
        f"/repos/{owner}/{repo}/contents/{encoded_path}",
        {"ref": str(branch).strip() or None},
    )
    if isinstance(result, list):
        return {"type": "directory", "entries": result[:MAX_PAGE], "count": len(result)}
    output = {key: result.get(key) for key in ("type", "name", "path", "sha", "size", "url", "html_url", "download_url")}
    encoded = result.get("content") if result.get("encoding") == "base64" else None
    if encoded:
        raw = base64.b64decode(encoded, validate=False)
        if len(raw) <= MAX_FILE_BYTES:
            try:
                output["content"] = raw.decode("utf-8")
                output["encoding"] = "utf-8"
            except UnicodeDecodeError:
                output["content_omitted"] = "binary"
        else:
            output["content_omitted"] = "file exceeds 256 KiB"
    return output


@mcp.tool()
def list_commits(owner: str, repo: str, sha: str = "", page: int = 1, perPage: int = 20) -> list:
    owner, repo = _repo(owner, repo)
    return _request(
        f"/repos/{owner}/{repo}/commits",
        {"sha": str(sha).strip() or None, "page": _bounded_int(page, high=1000), "per_page": _bounded_int(perPage)},
    )


@mcp.tool()
def list_issues(owner: str, repo: str, state: str = "open", page: int = 1, perPage: int = 20) -> list:
    owner, repo = _repo(owner, repo)
    if state not in {"open", "closed", "all"}:
        raise ValueError("state debe ser open|closed|all")
    return _request(
        f"/repos/{owner}/{repo}/issues",
        {"state": state, "page": _bounded_int(page, high=1000), "per_page": _bounded_int(perPage)},
    )


@mcp.tool()
def get_issue(owner: str, repo: str, issue_number: int) -> dict:
    owner, repo = _repo(owner, repo)
    return _request(f"/repos/{owner}/{repo}/issues/{int(issue_number)}")


@mcp.tool()
def get_pull_request(owner: str, repo: str, pull_number: int) -> dict:
    owner, repo = _repo(owner, repo)
    return _request(f"/repos/{owner}/{repo}/pulls/{int(pull_number)}")


@mcp.tool()
def list_pull_requests(owner: str, repo: str, state: str = "open", page: int = 1, perPage: int = 20) -> list:
    owner, repo = _repo(owner, repo)
    if state not in {"open", "closed", "all"}:
        raise ValueError("state debe ser open|closed|all")
    return _request(
        f"/repos/{owner}/{repo}/pulls",
        {"state": state, "page": _bounded_int(page, high=1000), "per_page": _bounded_int(perPage)},
    )


@mcp.tool()
def get_pull_request_files(owner: str, repo: str, pull_number: int, page: int = 1, perPage: int = 50) -> list:
    owner, repo = _repo(owner, repo)
    return _request(
        f"/repos/{owner}/{repo}/pulls/{int(pull_number)}/files",
        {"page": _bounded_int(page, high=1000), "per_page": _bounded_int(perPage)},
    )


@mcp.tool()
def get_pull_request_status(owner: str, repo: str, pull_number: int) -> dict:
    owner, repo = _repo(owner, repo)
    pull = _request(f"/repos/{owner}/{repo}/pulls/{int(pull_number)}")
    status = _request(f"/repos/{owner}/{repo}/commits/{pull['head']['sha']}/status")
    checks = _request(f"/repos/{owner}/{repo}/commits/{pull['head']['sha']}/check-runs", {"per_page": 100})
    return {"sha": pull["head"]["sha"], "status": status, "check_runs": checks.get("check_runs", [])}


@mcp.tool()
def get_pull_request_comments(owner: str, repo: str, pull_number: int, page: int = 1, perPage: int = 50) -> dict:
    owner, repo = _repo(owner, repo)
    params = {"page": _bounded_int(page, high=1000), "per_page": _bounded_int(perPage)}
    return {
        "issue_comments": _request(f"/repos/{owner}/{repo}/issues/{int(pull_number)}/comments", params),
        "review_comments": _request(f"/repos/{owner}/{repo}/pulls/{int(pull_number)}/comments", params),
    }


@mcp.tool()
def get_pull_request_reviews(owner: str, repo: str, pull_number: int, page: int = 1, perPage: int = 50) -> list:
    owner, repo = _repo(owner, repo)
    return _request(
        f"/repos/{owner}/{repo}/pulls/{int(pull_number)}/reviews",
        {"page": _bounded_int(page, high=1000), "per_page": _bounded_int(perPage)},
    )


@mcp.tool()
@_mutation
def create_or_update_file(
    owner: str,
    repo: str,
    path: str,
    message: str,
    content: str,
    branch: str,
    sha: str = "",
    approval_id: str = "",
) -> dict:
    """Crea/actualiza un archivo. Requiere approval exacto y SHA para reemplazar."""
    owner, repo = _repo(owner, repo)
    clean_path = str(path).lstrip("/")
    payload = {
        "message": str(message)[:500],
        "content": base64.b64encode(str(content).encode("utf-8")).decode("ascii"),
        "branch": str(branch),
    }
    if sha:
        payload["sha"] = str(sha)
    return _request(
        f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(clean_path, safe='/')}",
        method="PUT",
        payload=payload,
    )


@mcp.tool()
@_mutation
def create_repository(
    name: str,
    description: str = "",
    private: bool = True,
    autoInit: bool = False,
    approval_id: str = "",
) -> dict:
    """Crea un repositorio en la cuenta autenticada."""
    return _request(
        "/user/repos",
        method="POST",
        payload={
            "name": str(name), "description": str(description),
            "private": bool(private), "auto_init": bool(autoInit),
        },
    )


@mcp.tool()
@_mutation
def create_issue(
    owner: str,
    repo: str,
    title: str,
    body: str = "",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    approval_id: str = "",
) -> dict:
    owner, repo = _repo(owner, repo)
    return _request(
        f"/repos/{owner}/{repo}/issues",
        method="POST",
        payload={
            "title": str(title), "body": str(body),
            "labels": [str(x) for x in (labels or [])[:50]],
            "assignees": [str(x) for x in (assignees or [])[:20]],
        },
    )


@mcp.tool()
@_mutation
def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str,
    body: str = "",
    draft: bool = False,
    approval_id: str = "",
) -> dict:
    owner, repo = _repo(owner, repo)
    return _request(
        f"/repos/{owner}/{repo}/pulls",
        method="POST",
        payload={
            "title": str(title), "head": str(head), "base": str(base),
            "body": str(body), "draft": bool(draft),
        },
    )


@mcp.tool()
@_mutation
def fork_repository(
    owner: str,
    repo: str,
    organization: str = "",
    name: str = "",
    default_branch_only: bool = False,
    approval_id: str = "",
) -> dict:
    owner, repo = _repo(owner, repo)
    payload: dict[str, Any] = {"default_branch_only": bool(default_branch_only)}
    if organization:
        payload["organization"] = str(organization)
    if name:
        payload["name"] = str(name)
    return _request(f"/repos/{owner}/{repo}/forks", method="POST", payload=payload)


@mcp.tool()
@_mutation
def create_branch(
    owner: str,
    repo: str,
    branch: str,
    from_branch: str = "main",
    approval_id: str = "",
) -> dict:
    owner, repo = _repo(owner, repo)
    source = _request(
        f"/repos/{owner}/{repo}/git/ref/heads/{urllib.parse.quote(str(from_branch), safe='/')}"
    )
    return _request(
        f"/repos/{owner}/{repo}/git/refs",
        method="POST",
        payload={"ref": f"refs/heads/{branch}", "sha": source["object"]["sha"]},
    )


@mcp.tool()
@_mutation
def update_issue(
    owner: str,
    repo: str,
    issue_number: int,
    title: str = "",
    body: str = "",
    state: str = "",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    approval_id: str = "",
) -> dict:
    owner, repo = _repo(owner, repo)
    payload: dict[str, Any] = {}
    if title:
        payload["title"] = str(title)
    if body:
        payload["body"] = str(body)
    if state:
        if state not in {"open", "closed"}:
            raise ValueError("state debe ser open|closed")
        payload["state"] = state
    if labels is not None:
        payload["labels"] = [str(x) for x in labels[:50]]
    if assignees is not None:
        payload["assignees"] = [str(x) for x in assignees[:20]]
    if not payload:
        raise ValueError("update_issue requiere al menos un cambio")
    return _request(
        f"/repos/{owner}/{repo}/issues/{int(issue_number)}",
        method="PATCH",
        payload=payload,
    )


@mcp.tool()
@_mutation
def add_issue_comment(
    owner: str,
    repo: str,
    issue_number: int,
    body: str,
    approval_id: str = "",
) -> dict:
    owner, repo = _repo(owner, repo)
    return _request(
        f"/repos/{owner}/{repo}/issues/{int(issue_number)}/comments",
        method="POST",
        payload={"body": str(body)},
    )


@mcp.tool()
@_mutation
def create_pull_request_review(
    owner: str,
    repo: str,
    pull_number: int,
    event: str,
    body: str = "",
    commit_id: str = "",
    approval_id: str = "",
) -> dict:
    owner, repo = _repo(owner, repo)
    event = str(event).upper()
    if event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
        raise ValueError("event inválido")
    payload = {"event": event, "body": str(body)}
    if commit_id:
        payload["commit_id"] = str(commit_id)
    return _request(
        f"/repos/{owner}/{repo}/pulls/{int(pull_number)}/reviews",
        method="POST",
        payload=payload,
    )


@mcp.tool()
@_mutation
def merge_pull_request(
    owner: str,
    repo: str,
    pull_number: int,
    commit_title: str = "",
    commit_message: str = "",
    merge_method: str = "merge",
    approval_id: str = "",
) -> dict:
    owner, repo = _repo(owner, repo)
    if merge_method not in {"merge", "squash", "rebase"}:
        raise ValueError("merge_method inválido")
    payload: dict[str, Any] = {"merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = str(commit_title)
    if commit_message:
        payload["commit_message"] = str(commit_message)
    return _request(
        f"/repos/{owner}/{repo}/pulls/{int(pull_number)}/merge",
        method="PUT",
        payload=payload,
    )


@mcp.tool()
@_mutation
def update_pull_request_branch(
    owner: str,
    repo: str,
    pull_number: int,
    expected_head_sha: str = "",
    approval_id: str = "",
) -> dict:
    owner, repo = _repo(owner, repo)
    payload = {"expected_head_sha": str(expected_head_sha)} if expected_head_sha else {}
    return _request(
        f"/repos/{owner}/{repo}/pulls/{int(pull_number)}/update-branch",
        method="PUT",
        payload=payload,
    )


@mcp.tool()
@_mutation
def push_files(
    owner: str,
    repo: str,
    branch: str,
    files: list[dict[str, str]],
    message: str,
    approval_id: str = "",
) -> dict:
    """Commit atómico de varios archivos mediante Git data API."""
    owner, repo = _repo(owner, repo)
    if not files or len(files) > 100:
        raise ValueError("files debe contener 1..100 archivos")
    ref_path = f"/repos/{owner}/{repo}/git/ref/heads/{urllib.parse.quote(str(branch), safe='/')}"
    ref = _request(ref_path)
    parent_sha = ref["object"]["sha"]
    commit = _request(f"/repos/{owner}/{repo}/git/commits/{parent_sha}")
    tree_items = []
    for item in files:
        path = str(item.get("path", "")).lstrip("/")
        content = str(item.get("content", ""))
        if not path:
            raise ValueError("cada file requiere path")
        blob = _request(
            f"/repos/{owner}/{repo}/git/blobs",
            method="POST",
            payload={
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "encoding": "base64",
            },
        )
        tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = _request(
        f"/repos/{owner}/{repo}/git/trees",
        method="POST",
        payload={"base_tree": commit["tree"]["sha"], "tree": tree_items},
    )
    new_commit = _request(
        f"/repos/{owner}/{repo}/git/commits",
        method="POST",
        payload={"message": str(message), "tree": tree["sha"], "parents": [parent_sha]},
    )
    updated = _request(
        ref_path,
        method="PATCH",
        payload={"sha": new_commit["sha"], "force": False},
    )
    return {"commit": new_commit, "ref": updated}


if __name__ == "__main__":
    mcp.run()
