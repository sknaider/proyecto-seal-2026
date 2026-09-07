"""Regression tests for William's explicit user-to-agent authority."""

import pytest

from messages import chat_server


@pytest.mark.asyncio
async def test_legacy_user_keeps_compatibility_but_explicit_empty_means_none(monkeypatch):
    async def no_assignments(_user_id):
        return []

    async def legacy_policy(_user_id):
        return {"configured": False}

    monkeypatch.setattr(chat_server.chat_db, "get_user_agents", no_assignments)
    monkeypatch.setattr(chat_server.chat_db, "get_user_agent_policy", legacy_policy)
    assert await chat_server._resolve_allowed_agents(7, "basic") == set(chat_server._ASSIGNABLE_AGENTS)

    async def explicit_policy(_user_id):
        return {"configured": True}

    monkeypatch.setattr(chat_server.chat_db, "get_user_agent_policy", explicit_policy)
    assert await chat_server._resolve_allowed_agents(7, "basic") == set()


@pytest.mark.asyncio
async def test_explicit_assignment_and_admin_authority(monkeypatch):
    async def configured(_user_id):
        return {"configured": True}

    async def assigned(_user_id):
        return ["ADA", "FABLE"]

    monkeypatch.setattr(chat_server.chat_db, "get_user_agent_policy", configured)
    monkeypatch.setattr(chat_server.chat_db, "get_user_agents", assigned)
    assert await chat_server._resolve_allowed_agents(8, "basic") == {"ADA", "FABLE"}
    assert await chat_server._resolve_allowed_agents(8, "superuser") == set(chat_server._ASSIGNABLE_AGENTS)
