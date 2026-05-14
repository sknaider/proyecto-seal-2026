"""Tests for MCP scaffold generator."""
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


@pytest.mark.asyncio
async def test_get_scaffold_template():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/mcp/scaffold/template")
    assert r.status_code == 200
    data = r.json()
    assert data["language"] == "python"
    assert "fastmcp" in data["install"]
    assert "{name}" in data["template"]


@pytest.mark.asyncio
async def test_scaffold_basic():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/scaffold", json={"name": "MyServer"})
    assert r.status_code == 200
    data = r.json()
    assert "myserver_mcp_server.py" == data["filename"]
    assert "FastMCP" in data["code"]
    assert "MyServer" in data["code"]
    assert data["run_cmd"].endswith(".py")


@pytest.mark.asyncio
async def test_scaffold_with_tools():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/scaffold", json={
            "name": "CodeHelper",
            "tools": [
                {"name": "run_test", "description": "Run pytest", "params": [{"name": "path", "description": "Test path"}]},
                {"name": "lint_code", "description": "Lint Python file", "params": [{"name": "file", "description": "File path"}]},
            ]
        })
    assert r.status_code == 200
    code = r.json()["code"]
    assert "run_test" in code
    assert "lint_code" in code
    assert "Run pytest" in code
    assert "@mcp.tool()" in code


@pytest.mark.asyncio
async def test_scaffold_empty_name_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/scaffold", json={"name": ""})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_scaffold_name_with_spaces_sanitized():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/scaffold", json={"name": "My Custom Server"})
    assert r.status_code == 200
    data = r.json()
    assert " " not in data["filename"]
    assert "My_Custom_Server" in data["code"]


@pytest.mark.asyncio
async def test_scaffold_default_tool_when_no_tools_provided():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/mcp/scaffold", json={"name": "Empty"})
    assert r.status_code == 200
    code = r.json()["code"]
    # Should include hello fallback tool
    assert "def hello" in code
