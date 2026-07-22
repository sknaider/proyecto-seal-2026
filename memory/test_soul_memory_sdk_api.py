from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soul_memory_sdk_api import app  # noqa: E402


def test_sdk_api_exposes_phase2_routes() -> None:
    paths = {route.path for route in app.routes}
    assert "/v1/memories" in paths
    assert "/v1/memories/{memory_id}" in paths
    assert "/v1/recall" in paths
    assert "/v1/memory" in paths
    assert "/v1/memory/search" in paths
    assert "/v1/tenant/memories" in paths
    assert "/v1/tenant/recall" in paths


def test_sdk_api_openapi_has_bearer_dependency_surface() -> None:
    schema = app.openapi()
    assert schema["info"]["title"] == "SOUL Memory SDK API"
    assert "/v1/memories" in schema["paths"]
    assert "/v1/recall" in schema["paths"]
    assert "/v1/tenant/memories" in schema["paths"]
    assert "/v1/tenant/recall" in schema["paths"]


def test_legacy_search_route_precedes_dynamic_memory_id_route() -> None:
    response = TestClient(app).get("/v1/memory/search?query=test")
    assert response.status_code == 401
    assert response.json()["detail"] == "authorization_required"


def test_tenant_routes_require_authorization_before_user_id() -> None:
    response = TestClient(app).get("/v1/tenant/memories")
    assert response.status_code == 401
    assert response.json()["detail"] == "authorization_required"
