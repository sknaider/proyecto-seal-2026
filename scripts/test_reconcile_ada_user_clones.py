import json
from pathlib import Path

import pytest

import reconcile_ada_user_clones as mod


class FakeConn:
    async def fetch(self, _query, agents):
        assert set(agents) == set(mod.CLONE_AGENTS)
        return [
            {"agent": "ADA", "id": 103},
            {"agent": "ALICE", "id": 103},
        ]

    async def close(self):
        return None


class Jarvis116Conn:
    async def fetch(self, _query, agents):
        assert set(agents) == set(mod.CLONE_AGENTS)
        return [{"agent": "JARVIS", "id": 116}]

    async def close(self):
        return None


def _projection(tmp_path: Path) -> Path:
    path = tmp_path / "technical.sqlite3"
    path.write_bytes(b"projection-v1")
    return path


def test_projection_for_pair_prefers_scoped_override(tmp_path: Path):
    default = _projection(tmp_path)
    scoped = tmp_path / "technical_JARVIS-u116.sqlite3"
    scoped.write_bytes(b"projection-scoped")

    assert mod.technical_projection_for_pair("JARVIS", 116, default=default) == scoped
    assert mod.technical_projection_for_pair("ALICE", 103, default=default) == default


@pytest.mark.asyncio
async def test_reconcile_starts_missing_and_stops_revoked(monkeypatch, tmp_path: Path):
    (tmp_path / ".agent_session_token_ADA-u103").write_text("x")
    (tmp_path / ".agent_session_token_ALICE-u999").write_text("x")
    monkeypatch.setattr(mod.asyncpg, "connect", lambda *_args, **_kwargs: _async(FakeConn()))

    provisioned = []
    calls = []

    async def fake_provision(dsn, agent, user_id):
        provisioned.append((dsn, agent, user_id))
        return f"{agent}-u{user_id}", False

    async def fake_systemctl(*args):
        calls.append(args)
        if args[0] in {"is-active", "is-enabled"} and "ADA" not in args[-1] and "ada" not in args[-1]:
            return 3, "inactive"
        if args[0] in {"is-active", "is-enabled"}:
            return 0, args[0].removeprefix("is-")
        return 0, "ok"

    receipt = await mod.reconcile(
        "dsn",
        token_dir=tmp_path,
        technical_projection=_projection(tmp_path),
        health_root=tmp_path / "health",
        user_data_root=tmp_path / "users",
        provision_pair=fake_provision,
        run_systemctl=fake_systemctl,
    )

    assert receipt["ok"] is True
    assert provisioned == [("dsn", "ADA", 103), ("dsn", "ALICE", 103)]
    assert ("enable", "--now", "seal-user-clone@ALICE-u103.service") in calls
    assert ("disable", "--now", "seal-user-clone@ALICE-u999.service") in calls
    for agent in ("ADA", "ALICE"):
        data_dir = tmp_path / "users" / "u103" / "instances" / agent
        state_dir = tmp_path / "health" / f"{agent}-u103"
        assert data_dir.is_dir()
        assert state_dir.is_dir()
        assert data_dir.stat().st_mode & 0o777 == 0o700
        assert state_dir.stat().st_mode & 0o777 == 0o700
    operations = {(row["agent"], row["user_id"], row["operation"]) for row in receipt["actions"]}
    assert operations == {
        ("ADA", 103, "healthy"),
        ("ALICE", 103, "start"),
        ("ALICE", 999, "stop_revoked"),
    }
    assert receipt["technical_projection_sha256_by_pair"] == {
        "ADA-u103": mod._sha256(tmp_path / "technical.sqlite3"),
        "ALICE-u103": mod._sha256(tmp_path / "technical.sqlite3"),
    }


@pytest.mark.asyncio
async def test_reconcile_restarts_for_rotated_token_or_projection(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mod.asyncpg, "connect", lambda *_args, **_kwargs: _async(FakeConn()))
    projection = _projection(tmp_path)
    health_root = tmp_path / "health"
    alice_health = health_root / "ALICE-u103" / "ALICE-u103.health.json"
    alice_health.parent.mkdir(parents=True)
    alice_health.write_text(json.dumps({"technical_projection_sha256": "sha256:" + "0" * 64}))

    async def fake_provision(_dsn, agent, user_id):
        return f"{agent}-u{user_id}", agent == "ADA"

    calls = []

    async def fake_systemctl(*args):
        calls.append(args)
        if args[0] in {"is-active", "is-enabled"}:
            return 0, args[0].removeprefix("is-")
        return 0, "ok"

    receipt = await mod.reconcile(
        "dsn",
        token_dir=tmp_path,
        technical_projection=projection,
        health_root=health_root,
        user_data_root=tmp_path / "users",
        provision_pair=fake_provision,
        run_systemctl=fake_systemctl,
    )
    operations = {(row["agent"], row["operation"]) for row in receipt["actions"]}
    assert operations == {
        ("ADA", "restart_token_rotated"),
        ("ALICE", "restart_projection_changed"),
    }
    assert ("restart", "seal-ada-user-clone@103.service") in calls
    assert ("restart", "seal-user-clone@ALICE-u103.service") in calls


@pytest.mark.asyncio
async def test_reconcile_keeps_jarvis_u116_disabled_until_activation_marker(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        mod.asyncpg, "connect", lambda *_args, **_kwargs: _async(Jarvis116Conn())
    )
    calls = []

    async def fake_provision(_dsn, agent, user_id):
        return f"{agent}-u{user_id}", False

    async def fake_systemctl(*args):
        calls.append(args)
        if args[0] in {"is-active", "is-enabled"}:
            return 0, args[0].removeprefix("is-")
        return 0, "ok"

    receipt = await mod.reconcile(
        "dsn",
        token_dir=tmp_path,
        technical_projection=_projection(tmp_path),
        health_root=tmp_path / "health",
        user_data_root=tmp_path / "users",
        provision_pair=fake_provision,
        run_systemctl=fake_systemctl,
        claude_u116_marker=tmp_path / "missing-marker",
    )

    assert receipt["ok"] is True
    assert ("disable", "--now", "seal-user-clone@JARVIS-u116.service") in calls
    assert not any(call[0] in {"enable", "restart"} for call in calls)
    assert receipt["actions"] == [
        {
            "agent": "JARVIS",
            "user_id": 116,
            "operation": "hold_activation",
            "detail": "Claude key and signed-consent activation marker absent",
        }
    ]


@pytest.mark.asyncio
async def test_reconcile_allows_jarvis_u116_start_after_activation_marker(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        mod.asyncpg, "connect", lambda *_args, **_kwargs: _async(Jarvis116Conn())
    )
    marker = tmp_path / "claude-u116.enabled"
    marker.write_text("JARVIS-u116 claude-sonnet-5\n", encoding="utf-8")
    calls = []

    async def fake_provision(_dsn, agent, user_id):
        return f"{agent}-u{user_id}", False

    async def fake_systemctl(*args):
        calls.append(args)
        if args[0] in {"is-active", "is-enabled"}:
            return 3, "inactive"
        return 0, "ok"

    receipt = await mod.reconcile(
        "dsn",
        token_dir=tmp_path,
        technical_projection=_projection(tmp_path),
        health_root=tmp_path / "health",
        user_data_root=tmp_path / "users",
        provision_pair=fake_provision,
        run_systemctl=fake_systemctl,
        claude_u116_marker=marker,
    )

    assert receipt["ok"] is True
    assert ("enable", "--now", "seal-user-clone@JARVIS-u116.service") in calls
    assert receipt["actions"][0]["operation"] == "start"


async def _async(value):
    return value


def test_prepare_pair_directories_rejects_invalid_pair(tmp_path: Path):
    with pytest.raises(ValueError):
        mod.prepare_pair_directories(
            "../../JARVIS",
            103,
            user_data_root=tmp_path / "users",
            health_root=tmp_path / "health",
        )
