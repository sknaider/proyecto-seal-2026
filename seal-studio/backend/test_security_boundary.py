"""Focused regression tests for the :8800 canonical-auth boundary."""

from pathlib import Path

from fastapi.testclient import TestClient

import api_v1
import main


def test_access_policy_is_fail_closed():
    assert main._route_access_level("GET", "/health") == "public"
    assert main._route_access_level("GET", "/v1/tools") == "admin"
    assert main._route_access_level("GET", "/v1/team/status") == "user"
    assert main._route_access_level("GET", "/v1/system/health") == "admin"
    assert main._route_access_level("GET", "/api/soul/snapshot") == "admin"
    assert main._route_access_level("GET", "/api/soul/audit-log") == "admin"
    assert main._route_access_level("GET", "/api/files/read") == "superuser"
    assert main._route_access_level("GET", "/unknown/future-route") == "admin"
    assert main._route_access_level("PATCH", "/api/soul/capabilities") == "superuser"
    assert main._route_access_level("POST", "/v1/tools/seal-memory/memory_store/invoke") == "superuser"
    assert main._route_access_level("POST", "/unknown/future-route") == "admin"


def test_private_route_rejects_anonymous_but_health_stays_public():
    client = TestClient(main.app)
    assert client.get("/health").status_code == 200
    response = client.get("/api/soul/snapshot?agent=ADA")
    assert response.status_code == 401
    assert response.json()["error"] == "authentication_required"


def test_project_path_rejects_escape():
    try:
        main._project_path("/etc/hostname")
    except main.HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("absolute path escaped PROJECT_ROOT")
    assert main._project_path("seal-studio/backend/main.py") == (
        Path(main.PROJECT_ROOT) / "seal-studio/backend/main.py"
    ).resolve()


def test_v1_tools_is_not_shadowed_and_fable_is_canonical(monkeypatch):
    matches = [
        route for route in api_v1.router.routes
        if getattr(route, "path", None) == "/v1/tools" and "GET" in getattr(route, "methods", set())
    ]
    assert len(matches) == 1
    assert matches[0].endpoint is api_v1.v1_tools
    # FastAPI now includes routers lazily. Exercise the registered HTTP route
    # instead of assuming included APIRoutes live directly in app.routes.
    async def session(_headers):
        return {"id": 99901, "username": "fixture", "role": "admin"}
    async def catalog(**_kwargs):
        return {"ok": True, "tools": [], "test_marker": "canonical_catalog"}
    monkeypatch.setattr(main, "_canonical_session_from_headers", session)
    monkeypatch.setattr(api_v1.mcp_gateway, "list_catalog", catalog)
    response = TestClient(main.app).get("/v1/tools")
    assert response.status_code == 200
    assert response.json()["test_marker"] == "canonical_catalog"
    assert "FABLE" in main.CORE_AGENTS
    assert "FABLE" in api_v1._ALLOWED_AGENTS


def test_caller_headers_cannot_claim_internal_identity():
    source = Path(api_v1.__file__).read_text()
    assert 'request.headers.get("X-Caller-Id"' not in source
    assert 'request.headers.get("X-Caller-Type"' not in source
    assert 'request.state, "studio_user"' in source


def test_sse_channel_gate_is_exact_and_owner_scoped():
    assert api_v1._channel_visible_to_session("web_chat", 1, "William")
    assert api_v1._channel_visible_to_session("dm:ada:william", 1, "William")
    assert not api_v1._channel_visible_to_session("dm:ada:william", 2, "Henry")
    assert api_v1._channel_visible_to_session("user:1:marketing", 1, "William")
    assert not api_v1._channel_visible_to_session("user:11:marketing", 1, "William")
    assert not api_v1._channel_visible_to_session("dm_ada_william", 1, "William")
