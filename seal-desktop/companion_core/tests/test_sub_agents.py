import pytest
from httpx import ASGITransport, AsyncClient

from companion_core import sub_agents
from companion_core.main import app


def test_sub_agent_catalog_has_15_unique_agents():
    agents = sub_agents.list_all()
    names = [agent["name"] for agent in agents]

    assert len(agents) == 15
    assert len(set(names)) == 15
    assert {
        "orchestrator",
        "planner",
        "researcher",
        "critic",
        "code_executor",
        "memory_curator",
        "screen_analyst",
        "token_optimizer",
        "privacy_guard",
        "connector_operator",
        "voice_companion",
        "product_strategist",
        "documentation_writer",
        "test_runner",
        "release_manager",
    } == set(names)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("audita este cambio por secretos", "privacy_guard"),
        ("build deb install restart healthcheck", "release_manager"),
        ("corre pytest y smoke test", "test_runner"),
        ("analiza la pantalla visible", "screen_analyst"),
        ("compacta este contexto de tokens", "token_optimizer"),
        ("guarda esto como memoria de continuidad", "memory_curator"),
        ("conecta github y gdrive", "connector_operator"),
        ("prepara una respuesta de voz", "voice_companion"),
        ("define pricing y roadmap", "product_strategist"),
        ("documenta el changelog con evidencia", "documentation_writer"),
        ("implementa un script pequeño", "code_executor"),
        ("busca APIs oficiales", "researcher"),
        ("planea los pasos", "planner"),
        ("revisa este plan", "critic"),
        ("qué hago primero hoy", "orchestrator"),
    ],
)
def test_suggest_route_covers_full_catalog(query, expected):
    assert sub_agents.suggest_route(query) == expected


@pytest.mark.asyncio
async def test_sub_agents_endpoint_reports_full_catalog():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/sub-agents")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 15
    assert len(data["agents"]) == 15


@pytest.mark.asyncio
async def test_sub_agent_route_endpoint_uses_new_agents():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/sub-agents/route", params={"query": "diagnostica la captura de pantalla"})

    assert response.status_code == 200
    assert response.json()["agent"] == "screen_analyst"


@pytest.mark.asyncio
async def test_invoke_sub_agent_builds_prompt_for_new_agent():
    async def fake_llm(messages, system):
        assert messages[-1]["content"] == "resume memoria"
        assert "SEAL Memory Curator" in system
        assert "William" in system
        return "memoria organizada"

    result = await sub_agents.invoke_sub_agent(
        "memory_curator",
        "resume memoria",
        call_llm=fake_llm,
        user_name="William",
    )

    assert result["agent"] == "memory_curator"
    assert result["role"] == "Memory Librarian"
    assert result["reply"] == "memoria organizada"
