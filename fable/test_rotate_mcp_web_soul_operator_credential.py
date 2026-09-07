from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import rotate_mcp_web_soul_operator_credential as rotation


OLD_SECRET = "legacy-operator-password"
NEW_SECRET = "new_operator_password_0123456789_ABCDEFGHIJ"
OLD_DSN = f"postgresql://{rotation.ROLE}:{OLD_SECRET}@localhost:5433/seal_memory"
NEW_DSN = f"postgresql://{rotation.ROLE}:{NEW_SECRET}@localhost:5433/seal_memory"


class FakeStopper:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def stop_and_verify(self) -> None:
        self.calls += 1
        if self.fail:
            raise rotation.RotationError("operator remains active after stop request")


class FakeDatabase:
    def __init__(self) -> None:
        self.working = {OLD_DSN}
        self.alter_calls = 0
        self.legacy_sessions = 1
        self.revoke_calls = 0

    def probe(self, dsn: str) -> bool:
        return dsn in self.working

    def alter_own_password_and_revoke_sessions(self, dsn: str, new_password: str) -> None:
        assert dsn == OLD_DSN
        assert new_password == NEW_SECRET
        self.alter_calls += 1
        self.working = {NEW_DSN}
        self.legacy_sessions = 0

    def revoke_other_sessions(self, dsn: str) -> None:
        assert dsn in self.working
        self.revoke_calls += 1
        self.legacy_sessions = 0


def _paths(tmp_path: Path) -> rotation.Paths:
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    legacy = tmp_path / "legacy.env"
    legacy.write_text(f"MCP_WEB_SOUL_OPERATOR_DSN={OLD_DSN}\n", encoding="utf-8")
    os.chmod(legacy, 0o600)
    return rotation.Paths(directory, directory / "operator.dsn", legacy)


def _rotate(paths: rotation.Paths, db: FakeDatabase, stopper: FakeStopper, **kwargs):
    return rotation.rotate(
        rotation_id="release-20260814-1",
        paths=paths,
        database=db,
        stopper=stopper,
        owner_uid=os.getuid(),
        owner_gid=os.getgid(),
        password_factory=lambda: NEW_SECRET,
        **kwargs,
    )


def test_happy_path_rotates_with_restricted_role_promotes_and_tombstones_legacy(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.legacy.write_text(
        f"UNRELATED_SETTING=preserve-me\nMCP_WEB_SOUL_OPERATOR_DSN={OLD_DSN}\n",
        encoding="utf-8",
    )
    os.chmod(paths.legacy, 0o600)
    db = FakeDatabase()
    stopper = FakeStopper()

    result = _rotate(paths, db, stopper)

    assert result == {"status": "rotated", "rotation_id": "release-20260814-1"}
    assert stopper.calls == 1
    assert db.alter_calls == 1
    assert db.legacy_sessions == 0
    assert db.revoke_calls == 1
    assert paths.target.read_text().strip() == NEW_DSN
    assert stat.S_IMODE(paths.target.stat().st_mode) == 0o600
    assert OLD_SECRET not in paths.legacy.read_text()
    assert "invalidated" in paths.legacy.read_text()
    assert "UNRELATED_SETTING=preserve-me" in paths.legacy.read_text()
    assert not paths.candidate.exists()
    assert not paths.state.exists()
    receipt = json.loads(paths.receipt.read_text())
    assert receipt["status"] == "complete"
    assert receipt["role"] == rotation.ROLE
    assert OLD_SECRET not in paths.receipt.read_text()
    assert NEW_SECRET not in paths.receipt.read_text()


def test_crash_after_alter_recovers_from_root_owned_candidate_without_old_login(tmp_path: Path):
    paths = _paths(tmp_path)
    db = FakeDatabase()
    stopper = FakeStopper()

    def crash(point: str) -> None:
        if point == "after_alter":
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        _rotate(paths, db, stopper, fault_hook=crash)

    assert db.working == {NEW_DSN}
    assert paths.candidate.read_text().strip() == NEW_DSN
    assert stat.S_IMODE(paths.candidate.stat().st_mode) == 0o600
    assert paths.target.exists() is False
    assert OLD_SECRET in paths.legacy.read_text()

    result = _rotate(paths, db, stopper)
    assert result["status"] == "rotated"
    assert db.alter_calls == 1
    assert paths.target.read_text().strip() == NEW_DSN
    assert OLD_SECRET not in paths.legacy.read_text()


def test_crash_after_promote_is_idempotently_completed_without_second_alter(tmp_path: Path):
    paths = _paths(tmp_path)
    db = FakeDatabase()
    stopper = FakeStopper()

    def crash(point: str) -> None:
        if point == "after_promote":
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        _rotate(paths, db, stopper, fault_hook=crash)
    assert paths.target.read_text().strip() == NEW_DSN
    assert json.loads(paths.state.read_text())["phase"] == "promoted"

    assert _rotate(paths, db, stopper)["status"] == "rotated"
    assert db.alter_calls == 1
    assert OLD_SECRET not in paths.legacy.read_text()


def test_same_rotation_id_after_completion_is_a_noop(tmp_path: Path):
    paths = _paths(tmp_path)
    db = FakeDatabase()
    stopper = FakeStopper()
    _rotate(paths, db, stopper)

    assert _rotate(paths, db, stopper)["status"] == "already_complete"
    assert db.alter_calls == 1
    assert stopper.calls == 2


def test_refuses_rotation_while_any_operator_remains_active(tmp_path: Path):
    paths = _paths(tmp_path)
    db = FakeDatabase()
    with pytest.raises(rotation.RotationError, match="remains active"):
        _rotate(paths, db, FakeStopper(fail=True))
    assert db.alter_calls == 0
    assert not paths.candidate.exists()


def test_fails_closed_if_old_credential_still_authenticates(tmp_path: Path):
    paths = _paths(tmp_path)

    class AmbiguousDatabase(FakeDatabase):
        def alter_own_password_and_revoke_sessions(self, dsn: str, new_password: str) -> None:
            self.alter_calls += 1
            self.working = {OLD_DSN, NEW_DSN}

    db = AmbiguousDatabase()
    with pytest.raises(rotation.RotationError, match="invalidation is unproven"):
        _rotate(paths, db, FakeStopper())
    assert paths.candidate.exists()
    assert not paths.target.exists()
    assert OLD_SECRET in paths.legacy.read_text()


def test_refuses_promotion_when_a_pre_authenticated_legacy_session_resists_revocation(tmp_path: Path):
    paths = _paths(tmp_path)

    class ResistantSessionDatabase(FakeDatabase):
        def revoke_other_sessions(self, dsn: str) -> None:
            assert dsn == NEW_DSN
            self.revoke_calls += 1
            self.legacy_sessions = 1
            raise rotation.RotationError("legacy database sessions remain active")

    db = ResistantSessionDatabase()
    with pytest.raises(rotation.RotationError, match="legacy database sessions remain active"):
        _rotate(paths, db, FakeStopper())

    assert db.working == {NEW_DSN}  # old password login is invalid...
    assert db.legacy_sessions == 1  # ...but an old authenticated session survived.
    assert paths.candidate.exists()
    assert not paths.target.exists()
    assert not paths.receipt.exists()


def test_pending_rotation_blocks_a_different_rotation_id(tmp_path: Path):
    paths = _paths(tmp_path)
    db = FakeDatabase()
    with pytest.raises(RuntimeError):
        _rotate(paths, db, FakeStopper(), fault_hook=lambda _point: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(rotation.RotationError, match="another credential rotation"):
        rotation.rotate(
            rotation_id="different-release",
            paths=paths,
            database=db,
            stopper=FakeStopper(),
            owner_uid=os.getuid(),
            owner_gid=os.getgid(),
            password_factory=lambda: "another-secret",
        )


def test_asyncpg_path_uses_only_restricted_identity_and_never_prints_password(monkeypatch, capsys):
    executed: list[str] = []
    connected: list[str] = []
    transaction_events: list[str] = []

    class Transaction:
        async def __aenter__(self):
            transaction_events.append("enter")

        async def __aexit__(self, exc_type, exc, traceback):
            transaction_events.append("rollback" if exc_type else "commit")

    class Connection:
        async def fetchrow(self, _query):
            return {"role_name": rotation.ROLE, "rolsuper": False, "rolcanlogin": True}

        async def execute(self, sql):
            executed.append(sql)

        def transaction(self):
            return Transaction()

        async def fetch(self, _sql):
            return [{"pid": 999, "terminated": True}]

        async def fetchval(self, _sql):
            return 0

        async def close(self):
            return None

    async def fake_connect(dsn, **kwargs):
        connected.append(dsn)
        assert kwargs == {"timeout": 5, "command_timeout": 5}
        return Connection()

    monkeypatch.setattr(rotation.asyncpg, "connect", fake_connect)
    db = rotation.AsyncpgDatabase()
    assert db.probe(OLD_DSN)
    db.alter_own_password_and_revoke_sessions(OLD_DSN, NEW_SECRET)
    assert connected == [OLD_DSN, OLD_DSN]
    assert transaction_events == ["enter", "commit"]
    assert executed == [
        "SET LOCAL transaction_read_only=off",
        f"ALTER ROLE CURRENT_USER PASSWORD '{NEW_SECRET}'",
    ]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert "pg_dsn" not in Path(rotation.__file__).read_text()
    assert "CURRENT_USER" in Path(rotation.__file__).read_text()


def test_asyncpg_rejects_superuser_identity_and_unsafe_candidate(monkeypatch):
    class SuperConnection:
        async def fetchrow(self, _query):
            return {"role_name": rotation.ROLE, "rolsuper": True, "rolcanlogin": True}

        async def close(self):
            return None

    async def fake_connect(_dsn, **_kwargs):
        return SuperConnection()

    monkeypatch.setattr(rotation.asyncpg, "connect", fake_connect)
    db = rotation.AsyncpgDatabase()
    assert db.probe(OLD_DSN) is False
    with pytest.raises(rotation.RotationError, match="unsafe format"):
        db.alter_own_password_and_revoke_sessions(OLD_DSN, "bad'; RESET ROLE; --")


def test_asyncpg_rolls_back_password_when_peer_revocation_fails(monkeypatch, capsys):
    events: list[str] = []
    executed: list[str] = []

    class Transaction:
        async def __aenter__(self):
            events.append("enter")

        async def __aexit__(self, exc_type, exc, traceback):
            events.append("rollback" if exc_type else "commit")

    class Connection:
        async def fetchrow(self, _query):
            return {"role_name": rotation.ROLE, "rolsuper": False, "rolcanlogin": True}

        async def execute(self, sql):
            executed.append(sql)

        async def fetch(self, _query):
            return [{"pid": 444, "terminated": False}]

        def transaction(self):
            return Transaction()

        async def close(self):
            return None

    async def fake_connect(_dsn, **_kwargs):
        return Connection()

    monkeypatch.setattr(rotation.asyncpg, "connect", fake_connect)
    with pytest.raises(rotation.RotationError, match="resisted termination"):
        rotation.AsyncpgDatabase().alter_own_password_and_revoke_sessions(OLD_DSN, NEW_SECRET)

    assert events == ["enter", "rollback"]
    assert executed == [
        "SET LOCAL transaction_read_only=off",
        f"ALTER ROLE CURRENT_USER PASSWORD '{NEW_SECRET}'",
    ]
    captured = capsys.readouterr()
    assert OLD_SECRET not in captured.out + captured.err
    assert NEW_SECRET not in captured.out + captured.err


def test_asyncpg_rejects_false_backend_termination(monkeypatch):
    class ResistantConnection:
        async def fetchrow(self, _query):
            return {"role_name": rotation.ROLE, "rolsuper": False, "rolcanlogin": True}

        async def fetch(self, _query):
            return [{"pid": 444, "terminated": False}]

        async def fetchval(self, _query):
            return 1

        async def close(self):
            return None

    async def fake_connect(_dsn, **_kwargs):
        return ResistantConnection()

    monkeypatch.setattr(rotation.asyncpg, "connect", fake_connect)
    with pytest.raises(rotation.RotationError, match="resisted termination"):
        rotation.AsyncpgDatabase().revoke_other_sessions(OLD_DSN)


@pytest.mark.parametrize(
    "dsn",
    [
        f"postgresql://{rotation.ROLE}:{OLD_SECRET}@evil.example:9999/fake_db",
        f"postgresql://{rotation.ROLE}:{OLD_SECRET}@localhost:5432/seal_memory",
        f"postgresql://{rotation.ROLE}:{OLD_SECRET}@localhost:5433/other_db",
        f"postgresql://{rotation.ROLE}:{OLD_SECRET}@[::1]:5433/seal_memory",
        f"postgresql://{rotation.ROLE}:{OLD_SECRET}@localhost:5433/seal_memory?host=evil.example",
    ],
)
def test_rejects_same_role_on_any_unapproved_endpoint_before_probe_or_promotion(
    tmp_path: Path, dsn: str,
):
    paths = _paths(tmp_path)
    paths.legacy.write_text(f"MCP_WEB_SOUL_OPERATOR_DSN={dsn}\n", encoding="utf-8")
    os.chmod(paths.legacy, 0o600)

    class EndpointTrap(FakeDatabase):
        def __init__(self) -> None:
            self.working = {dsn}
            self.alter_calls = 0

        def probe(self, candidate: str) -> bool:
            pytest.fail(f"unapproved endpoint reached probe: {candidate.split('@')[-1]}")

    with pytest.raises(rotation.RotationError, match="approved local database"):
        _rotate(paths, EndpointTrap(), FakeStopper())
    assert not paths.candidate.exists()
    assert not paths.target.exists()


def test_stopper_requests_both_service_stops_and_rejects_remaining_pid(monkeypatch):
    calls: list[list[str]] = []

    class Result:
        returncode = 3
        stdout = ""
        stderr = ""

    stopper = rotation.SystemdOperatorStopper()
    monkeypatch.setattr(rotation.Path, "is_dir", lambda self: str(self) == "/run/user/1000")
    monkeypatch.setattr(stopper, "_run", lambda argv: calls.append(argv) or Result())
    monkeypatch.setattr(stopper, "_operator_pids", lambda: [])
    stopper.stop_and_verify()

    assert calls[0] == ["/usr/bin/systemctl", "stop", rotation.UNIT]
    assert "disable" in calls[1] and "--now" in calls[1]
    assert calls[2] == ["/usr/bin/systemctl", "is-active", "--quiet", rotation.UNIT]

    monkeypatch.setattr(stopper, "_operator_pids", lambda: [1234])
    with pytest.raises(rotation.RotationError, match="remains active"):
        stopper.stop_and_verify()


def test_completed_receipt_recovers_from_crash_before_state_cleanup(tmp_path: Path):
    paths = _paths(tmp_path)
    db = FakeDatabase()
    stopper = FakeStopper()
    _rotate(paths, db, stopper)
    paths.state.write_text(
        json.dumps({"rotation_id": "release-20260814-1", "phase": "promoted"}) + "\n",
        encoding="utf-8",
    )
    os.chmod(paths.state, 0o600)

    assert _rotate(paths, db, stopper)["status"] == "already_complete"
    assert not paths.state.exists()


def test_cli_rejects_non_root_without_touching_services(monkeypatch, capsys):
    monkeypatch.setattr(rotation.os, "geteuid", lambda: 1000)
    assert rotation.main(["--rotation-id", "release-1"]) == 2
    captured = capsys.readouterr()
    assert "root execution required" in captured.err


def test_default_target_is_the_exact_systemd_loadcredential_source():
    unit = (Path(rotation.__file__).parent / "systemd" / rotation.UNIT).read_text()
    assert rotation.DEFAULT_TARGET == Path("/etc/seal/mcp-web-soul-operator/operator.dsn")
    assert f"LoadCredential=operator.dsn:{rotation.DEFAULT_TARGET}" in unit
