"""Tests for OCEAN agent profile and user profile endpoints."""
import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app, _ocean_to_traits, _valence_arousal_to_label, _infer_emotion_from_text


# ── Unit tests for helper functions ──────────────────────────────────────────

def test_ocean_to_traits_high_openness():
    result = _ocean_to_traits(0.9, 0.5, 0.5, 0.5, 0.2)
    assert "curious" in result.lower() or "open" in result.lower()


def test_ocean_to_traits_low_openness():
    result = _ocean_to_traits(0.2, 0.5, 0.5, 0.5, 0.2)
    assert "practical" in result.lower() or "grounded" in result.lower()


def test_ocean_to_traits_high_agreeableness():
    result = _ocean_to_traits(0.5, 0.5, 0.5, 0.9, 0.2)
    assert "empathetic" in result.lower() or "warm" in result.lower()


def test_ocean_to_traits_low_neuroticism():
    result = _ocean_to_traits(0.5, 0.5, 0.5, 0.5, 0.1)
    assert "stable" in result.lower() or "resilient" in result.lower()


def test_valence_arousal_calm():
    assert _valence_arousal_to_label(0.75, 0.25) == "calm"


def test_valence_arousal_energetic():
    assert _valence_arousal_to_label(0.85, 0.80) == "energetic"


def test_valence_arousal_focused():
    assert _valence_arousal_to_label(0.30, 0.75) == "focused"


def test_valence_arousal_reflective():
    assert _valence_arousal_to_label(0.30, 0.25) == "reflective"


def test_infer_emotion_curious():
    v, a = _infer_emotion_from_text("I wonder how this works and why", 0.2)
    assert v > 0.5
    assert a > 0.4


def test_infer_emotion_stable_agent_low_arousal_change():
    _, a_stable = _infer_emotion_from_text("solve this debug error", 0.1)
    _, a_reactive = _infer_emotion_from_text("solve this debug error", 0.9)
    # High neuroticism → higher arousal
    assert a_reactive > a_stable


# ── API endpoint tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_agent_profile_defaults():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/agent/profile")
    assert r.status_code == 200
    data = r.json()
    assert "name" in data
    assert "ocean_o" in data
    assert "ocean_c" in data
    assert "ocean_e" in data
    assert "ocean_a" in data
    assert "ocean_n" in data
    assert "emotional_state" in data
    assert "personality_traits" in data
    assert "valence" in data
    assert "arousal" in data


@pytest.mark.asyncio
async def test_get_agent_profile_has_valid_defaults():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/agent/profile")
    data = r.json()
    assert 0.0 <= data["ocean_o"] <= 1.0
    assert 0.0 <= data["ocean_n"] <= 1.0
    assert data["emotional_state"] in ("calm", "energetic", "focused", "reflective", "satisfied")


@pytest.mark.asyncio
async def test_patch_agent_name():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/agent/profile", json={"name": "Aria"})
        assert r.json()["ok"] is True
        r2 = await client.get("/api/agent/profile")
    assert r2.json()["name"] == "Aria"


@pytest.mark.asyncio
async def test_patch_agent_ocean_values():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/agent/profile", json={
            "ocean_o": 0.9, "ocean_c": 0.3, "ocean_e": 0.8, "ocean_a": 0.7, "ocean_n": 0.1
        })
        assert r.json()["ok"] is True
        r2 = await client.get("/api/agent/profile")
    data = r2.json()
    assert abs(data["ocean_o"] - 0.9) < 0.01
    assert abs(data["ocean_n"] - 0.1) < 0.01


@pytest.mark.asyncio
async def test_patch_agent_ocean_clamped():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/agent/profile", json={"ocean_o": 1.5, "ocean_n": -0.3})
        r = await client.get("/api/agent/profile")
    data = r.json()
    assert data["ocean_o"] <= 1.0
    assert data["ocean_n"] >= 0.0


@pytest.mark.asyncio
async def test_patch_agent_persona_description():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/agent/profile", json={"persona_description": "A direct and curious assistant."})
        r = await client.get("/api/agent/profile")
    assert r.json()["persona_description"] == "A direct and curious assistant."


@pytest.mark.asyncio
async def test_patch_agent_no_fields_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/agent/profile", json={})
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_personality_traits_reflect_ocean():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/agent/profile", json={"ocean_a": 0.95})
        r = await client.get("/api/agent/profile")
    traits = r.json()["personality_traits"]
    assert "empathetic" in traits.lower() or "warm" in traits.lower()


@pytest.mark.asyncio
async def test_get_emotional_state():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/agent/emotional-state")
    assert r.status_code == 200
    data = r.json()
    assert "emotional_state" in data
    assert "valence" in data
    assert "arousal" in data
    assert 0.0 <= data["valence"] <= 1.0
    assert 0.0 <= data["arousal"] <= 1.0


@pytest.mark.asyncio
async def test_get_avatar_profile_defaults_and_options():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/avatar/profile")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["avatar"]["variant"] == "orb"
    assert data["avatar"]["primary_color"].startswith("#")
    assert "leaf" in data["options"]["variants"]
    assert "headset" in data["options"]["accessories"]


@pytest.mark.asyncio
async def test_patch_avatar_profile_persists():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/avatar/profile", json={
            "variant": "leaf",
            "primary_color": "#14b8a6",
            "secondary_color": "#0f766e",
            "accent_color": "#f59e0b",
            "accessory": "headset",
            "motion": "expressive",
        })
        assert r.status_code == 200
        assert set(r.json()["updated"]) == {
            "variant", "primary_color", "secondary_color", "accent_color", "accessory", "motion"
        }
        r2 = await client.get("/api/avatar/profile")
    avatar = r2.json()["avatar"]
    assert avatar["variant"] == "leaf"
    assert avatar["primary_color"] == "#14b8a6"
    assert avatar["accessory"] == "headset"


@pytest.mark.asyncio
async def test_patch_avatar_profile_rejects_invalid_values():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad_variant = await client.patch("/api/avatar/profile", json={"variant": "dragon"})
        bad_color = await client.patch("/api/avatar/profile", json={"primary_color": "red"})
        bad_accessory = await client.patch("/api/avatar/profile", json={"accessory": "../x"})
    assert bad_variant.status_code == 400
    assert bad_color.status_code == 400
    assert bad_accessory.status_code == 400


# ── User profile ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_user_profile_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/user/profile")
    assert r.status_code == 200
    assert "profile" in r.json()
    assert isinstance(r.json()["profile"], dict)


@pytest.mark.asyncio
async def test_patch_user_profile_communication_style():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/user/profile", json={"communication_style": "direct"})
        assert r.json()["ok"] is True
        r2 = await client.get("/api/user/profile")
    assert r2.json()["profile"].get("communication_style") == "direct"


@pytest.mark.asyncio
async def test_patch_user_profile_topics():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/user/profile", json={"topics_of_interest": ["AI", "medicine", "music"]})
        r = await client.get("/api/user/profile")
    topics = r.json()["profile"].get("topics_of_interest")
    assert "AI" in topics
    assert "medicine" in topics


@pytest.mark.asyncio
async def test_patch_user_profile_preferred_name():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/user/profile", json={"preferred_name": "Will"})
        r = await client.get("/api/user/profile")
    assert r.json()["profile"]["preferred_name"] == "Will"


@pytest.mark.asyncio
async def test_patch_user_profile_overwrites():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/user/profile", json={"communication_style": "verbose"})
        await client.patch("/api/user/profile", json={"communication_style": "concise"})
        r = await client.get("/api/user/profile")
    assert r.json()["profile"]["communication_style"] == "concise"


@pytest.mark.asyncio
async def test_patch_user_profile_multiple_fields():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/user/profile", json={
            "communication_style": "technical",
            "preferred_name": "Willi",
            "notes": "Prefers bullet points",
        })
    assert r.json()["ok"] is True
    assert set(r.json()["updated"]) == {"communication_style", "preferred_name", "notes"}
