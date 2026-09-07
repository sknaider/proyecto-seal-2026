from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import soul_memory_sdk_onboard as onboard  # noqa: E402


TENANT_ID = "12345678-1234-4abc-8def-1234567890ab"
RAW_KEY = "sk-soul-RAW-ONLY-IN-SECURE-FILE"


class FakeTransaction:
    def __init__(self, *, commit_error: BaseException | None = None) -> None:
        self.started = False
        self.committed = False
        self.rolled_back = False
        self.isolation: str | None = None
        self.commit_error = commit_error

    async def start(self) -> None:
        self.started = True

    async def commit(self) -> None:
        if self.commit_error:
            raise self.commit_error
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeConn:
    def __init__(
        self,
        *,
        authorized: bool = True,
        fail_provision: bool = False,
        duplicate_count: int = 0,
        plan_active: bool = True,
        subscription_ok: bool = True,
        stored_hash_matches: bool = True,
        commit_error: BaseException | None = None,
        outcome: str = "absent",
        expected_record: dict[str, Any] | None = None,
    ) -> None:
        self.tx = FakeTransaction(commit_error=commit_error)
        self.authorized = authorized
        self.fail_provision = fail_provision
        self.duplicate_count = duplicate_count
        self.plan_active = plan_active
        self.subscription_ok = subscription_ok
        self.stored_hash_matches = stored_hash_matches
        self.outcome = outcome
        self.expected_record = expected_record
        self.closed = False
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    def transaction(self, *, isolation: str) -> FakeTransaction:
        self.tx.isolation = isolation
        return self.tx

    async def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append(("execute", sql, args))
        return "OK"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetchval", sql, args))
        if "SELECT count(*)" in sql:
            return self.duplicate_count
        raise AssertionError(sql)

    async def fetch(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetch", sql, args))
        if self.fail_provision:
            raise RuntimeError("injected provision failure")
        assert "sdk_provision_external_tenant_roles($1::uuid)" in sql
        tenant = UUID(str(args[0]))
        return [
            {"resolved_viewer": "agent", "resolved_db_role": f"soul_sdk_t_{tenant.hex}_agent"},
            {"resolved_viewer": "user", "resolved_db_role": f"soul_sdk_t_{tenant.hex}_user"},
        ]

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetchrow", sql, args))
        if "WHERE role.rolname = session_user" in sql:
            allowed = self.authorized
            return {
                "session_user": "svc_soul_sdk_onboard_william",
                "current_matches_session": allowed,
                "rolcanlogin": True,
                "role_restricted": allowed,
                "onboarding_member_inheriting": allowed,
                "schema_usage": True,
                "tenant_select_columns": True,
                "tenant_insert_columns": True,
                "tenant_update_api_keys": True,
                "tenant_dangerous_denied": True,
                "plans_direct_denied": True,
                "plan_lock_execute": True,
                "subscriptions_read": True,
                "subscription_insert_columns": True,
                "subscription_other_insert_denied": True,
                "subscription_dangerous_denied": True,
                "wrapper_execute": True,
                "direct_provision_denied": True,
            }
        if "FROM soul_v3.sdk_lock_external_plan" in sql:
            if not self.plan_active:
                return None
            return {"plan_id": args[0], "window_seconds": 60, "max_requests": 100}
        if "INSERT INTO soul_v3.sdk_tenant_subscriptions" in sql:
            if not self.subscription_ok:
                return None
            return {"plan_id": args[1], "status": "active"}
        if "UPDATE soul_v3.tenants" in sql:
            record = json.loads(args[1])[0]
            digest = record["sha256"] if self.stored_hash_matches else "0" * 64
            return {"key_count": 1, "stored_sha256": digest}
        if "matching_subscriptions" in sql:
            if self.outcome == "absent":
                return None
            roles = onboard._expected_roles(request())
            if self.outcome == "divergent":
                return {
                    "name": "wrong",
                    "is_internal": False,
                    "quotas": {},
                    "total_keys": 0,
                    "matching_keys": 0,
                    "matching_key_record": None,
                    "matching_subscriptions": 0,
                    "agent_role": roles["agent"],
                    "user_role": roles["user"],
                    "agent_role_attrs_ok": True,
                    "user_role_attrs_ok": True,
                    "agent_parents": ["soul_sdk_agent_api", "soul_sdk_tenant_bound"],
                    "user_parents": ["soul_sdk_tenant_api", "soul_sdk_tenant_bound"],
                    "agent_members": ["soul_sdk_onboarding_owner", "svc_soul_memory_sdk"],
                    "user_members": ["soul_sdk_onboarding_owner", "svc_soul_memory_sdk"],
                    "sdk_agent_membership_ok": True,
                    "sdk_user_membership_ok": True,
                }
            record = self.expected_record or onboard.build_expected_key_record(
                request(), RAW_KEY, "William"
            )
            return {
                "name": "Acme Health",
                "is_internal": False,
                "quotas": {},
                "total_keys": 1,
                "matching_keys": 1,
                "matching_key_record": record,
                "matching_subscriptions": 1,
                "agent_role": roles["agent"],
                "user_role": roles["user"],
                "agent_role_attrs_ok": True,
                "user_role_attrs_ok": True,
                "agent_parents": ["soul_sdk_agent_api", "soul_sdk_tenant_bound"],
                "user_parents": ["soul_sdk_tenant_api", "soul_sdk_tenant_bound"],
                "agent_members": ["soul_sdk_onboarding_owner", "svc_soul_memory_sdk"],
                "user_members": ["soul_sdk_onboarding_owner", "svc_soul_memory_sdk"],
                "sdk_agent_membership_ok": True,
                "sdk_user_membership_ok": True,
            }
        raise AssertionError(sql)


def request(**overrides: Any) -> onboard.OnboardingRequest:
    values = {
        "tenant_id": TENANT_ID,
        "tenant_name": "Acme Health",
        "scopes": ["read", "write"],
        "agents": ["ADA"],
        "key_label": "acme-prod-1",
        "plan_id": "pro",
        "expires_at": None,
        "ttl_seconds": 86400,
    }
    values.update(overrides)
    return onboard.build_request(**values)


def args(**overrides: Any) -> argparse.Namespace:
    values = {
        "tenant_id": TENANT_ID,
        "tenant_name": "Acme Health",
        "scope": ["read", "write"],
        "agent": ["ADA"],
        "key_label": "acme-prod-1",
        "plan": "pro",
        "expires_at": None,
        "ttl_seconds": 86400,
        "dry_run": False,
        "apply": True,
        "confirm": None,
        "output_file": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_dry_run_is_offline_non_secret_and_rehashed_for_056(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboard, "new_api_key", lambda: pytest.fail("dry-run generated a key"))
    plan = onboard.dry_run_plan(request())
    assert plan["transaction"] == "serializable_all_or_nothing"
    assert plan["tenant"]["quotas"] == {}
    assert plan["subscription"]["plan_id"] == "pro"
    assert plan["confirmation_is_authentication"] is False
    assert plan["operator_authentication"]["source"] == "postgresql_session_user"
    assert plan["safety_confirmation_token"].startswith(f"ONBOARD_SDK_TENANT:{TENANT_ID}:")
    assert onboard.expected_confirmation(request(plan_id="free")) != plan["safety_confirmation_token"]
    assert onboard.expected_confirmation(request(scopes=["tenant:read"], agents=[])) != plan["safety_confirmation_token"]
    assert "operator" not in onboard._confirmation_payload(request())
    assert "quotas" not in onboard._confirmation_payload(request())
    assert "sk-soul-" not in repr(plan)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"tenant_id": "bad"}, "tenant_id_must_be_uuid"),
        ({"tenant_id": "00000000-0000-0000-0000-000000000000"}, "external_tenant_cannot_use_internal_id"),
        ({"plan_id": "internal"}, "plan_must_be_free_pro_or_enterprise"),
        ({"expires_at": None, "ttl_seconds": None}, "external_key_expiry_required"),
        ({"tenant_name": ""}, "tenant_name_required"),
        ({"scopes": ["read"], "agents": []}, "agent_allowlist_required"),
    ],
)
def test_request_validation_fails_closed(overrides: dict[str, Any], error: str) -> None:
    with pytest.raises((onboard.TenantOnboardingError, ValueError), match=error):
        request(**overrides)


def test_parser_has_no_spoofable_operator_or_quotas() -> None:
    parser = onboard.build_parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--operator" not in options
    assert "--quotas" not in options
    assert "--output-file" in options


@pytest.mark.parametrize(
    ("login", "person"),
    [
        ("svc_soul_sdk_onboard_william", "William"),
        ("svc_soul_sdk_onboard_henry", "Henry"),
    ],
)
def test_operator_is_derived_only_from_exact_dsn_login(login: str, person: str) -> None:
    dsn = f"postgresql://{login}:secret@localhost/db"
    assert onboard._assert_dsn_operator_login(dsn) == login
    assert onboard._person_for_session_user(login) == person
    with pytest.raises(onboard.TenantOnboardingError, match="session_user"):
        onboard._assert_dsn_operator_login("postgresql://seal:secret@localhost/db")


@pytest.mark.asyncio
async def test_db_boundary_requires_restricted_membership_column_acl_and_wrapper() -> None:
    session_user, person = await onboard._assert_operator_db_boundary(FakeConn())
    assert session_user == "svc_soul_sdk_onboard_william"
    assert person == "William"
    with pytest.raises(onboard.TenantOnboardingError, match="boundary_not_authorized"):
        await onboard._assert_operator_db_boundary(FakeConn(authorized=False))


@pytest.mark.asyncio
async def test_apply_uses_wrapper_and_one_transaction_without_raw_key_in_sql() -> None:
    conn = FakeConn()
    result = await onboard.onboard_external_tenant(conn, request(), raw_key=RAW_KEY)
    assert conn.tx.isolation == "serializable"
    assert conn.tx.started and conn.tx.committed and not conn.tx.rolled_back
    assert result["operator"] == "William"
    assert result["first_key"]["raw_key"] == RAW_KEY
    all_sql = "\n".join(sql for _kind, sql, _args in conn.calls)
    assert "sdk_provision_external_tenant_roles($1::uuid)" in all_sql
    assert "provision_sdk_tenant_roles($1::uuid, $2::name)" not in all_sql
    assert "'{}'::jsonb" in all_sql
    for _kind, _sql, call_args in conn.calls:
        assert RAW_KEY not in repr(call_args)
    serialized = next(
        call_args[1]
        for kind, sql, call_args in conn.calls
        if kind == "fetchrow" and "UPDATE soul_v3.tenants" in sql
    )
    assert json.loads(serialized)[0]["created_by"] == "William"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conn", "error"),
    [
        (FakeConn(duplicate_count=1), "tenant_id_or_name_already_exists"),
        (FakeConn(plan_active=False), "active_sdk_usage_plan_not_found"),
        (FakeConn(subscription_ok=False), "tenant_subscription_persistence_failed"),
        (FakeConn(stored_hash_matches=False), "first_key_persistence_failed"),
    ],
)
async def test_failure_rolls_back_without_commit(conn: FakeConn, error: str) -> None:
    with pytest.raises(onboard.TenantOnboardingError, match=error):
        await onboard.onboard_external_tenant(conn, request(), raw_key=RAW_KEY)
    assert conn.tx.rolled_back and not conn.tx.committed


@pytest.mark.asyncio
async def test_wrapper_failure_rolls_back_before_key_persistence() -> None:
    conn = FakeConn(fail_provision=True)
    with pytest.raises(RuntimeError, match="injected provision"):
        await onboard.onboard_external_tenant(conn, request(), raw_key=RAW_KEY)
    assert conn.tx.rolled_back
    assert not any("UPDATE soul_v3.tenants" in sql for _kind, sql, _args in conn.calls)


class RetryableError(RuntimeError):
    sqlstate = "40001"


@pytest.mark.asyncio
async def test_retry_is_bounded_and_reuses_exactly_one_key(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []
    expected_record = onboard.build_expected_key_record(request(), RAW_KEY, "William")

    async def fake_onboard(
        _conn: Any, _request: Any, *, raw_key: str, expected_record: dict[str, Any]
    ) -> dict[str, Any]:
        attempts.append(raw_key)
        assert expected_record is expected_record_value
        if len(attempts) < 3:
            raise RetryableError("serialization")
        return {"ok": True, "first_key": {"raw_key": raw_key}}

    connections: list[FakeConn] = []

    async def connect(_dsn: str) -> FakeConn:
        connection = FakeConn()
        connections.append(connection)
        return connection

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(onboard, "onboard_external_tenant", fake_onboard)
    expected_record_value = expected_record
    result = await onboard.apply_with_retry(
        "dsn", request(), RAW_KEY, expected_record, connect=connect, sleep=no_sleep
    )
    assert result["ok"] is True
    assert attempts == [RAW_KEY, RAW_KEY, RAW_KEY]
    assert len(connections) == 3


@pytest.mark.asyncio
async def test_ambiguous_commit_inspects_uuid_before_any_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    expected_record = onboard.build_expected_key_record(request(), RAW_KEY, "William")

    async def ambiguous(
        _conn: Any, _request: Any, *, raw_key: str, expected_record: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append("apply")
        raise onboard.AmbiguousCommitOutcome("unknown")

    connections = [FakeConn(), FakeConn(outcome="committed", expected_record=expected_record)]

    async def connect(_dsn: str) -> FakeConn:
        return connections.pop(0)

    monkeypatch.setattr(onboard, "onboard_external_tenant", ambiguous)
    result = await onboard.apply_with_retry(
        "dsn", request(), RAW_KEY, expected_record, connect=connect
    )
    assert calls == ["apply"]
    assert result["mode"] == "applied_recovered_after_commit_inspection"
    assert result["first_key"]["raw_key"] == RAW_KEY


@pytest.mark.asyncio
async def test_ambiguous_divergent_state_never_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_record = onboard.build_expected_key_record(request(), RAW_KEY, "William")

    async def ambiguous(
        _conn: Any, _request: Any, *, raw_key: str, expected_record: dict[str, Any]
    ) -> dict[str, Any]:
        raise onboard.AmbiguousCommitOutcome("unknown")

    connections = [FakeConn(), FakeConn(outcome="divergent")]

    async def connect(_dsn: str) -> FakeConn:
        return connections.pop(0)

    monkeypatch.setattr(onboard, "onboard_external_tenant", ambiguous)
    with pytest.raises(onboard.TenantOnboardingError, match="divergent_no_retry"):
        await onboard.apply_with_retry(
            "dsn", request(), RAW_KEY, expected_record, connect=connect
        )


def test_output_file_is_new_regular_0600_fsynced_and_never_overwrites(tmp_path: Path) -> None:
    output = tmp_path / "first-key.json"
    path, fd = onboard.reserve_output_file(str(output))
    expected_record = onboard.build_expected_key_record(request(), RAW_KEY, "William")
    onboard.write_pending_raw_key_file(fd, request(), RAW_KEY, expected_record)
    pending = json.loads(output.read_text())
    assert pending["state"] == "pending_database_outcome"
    assert pending["raw_api_key"] == RAW_KEY
    result = {
        "tenant": {"id": TENANT_ID},
        "first_key": {"raw_key": RAW_KEY, "metadata": {"sha256": "a" * 64}},
    }
    onboard.finalize_raw_key_file(path, fd, result)
    assert path == output
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text())["raw_api_key"] == RAW_KEY
    assert json.loads(output.read_text())["state"] == "committed"
    with pytest.raises(onboard.TenantOnboardingError, match="must_be_new"):
        onboard.reserve_output_file(str(output))


def test_output_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("do-not-touch")
    link = tmp_path / "key.json"
    link.symlink_to(target)
    with pytest.raises(onboard.TenantOnboardingError, match="must_be_new"):
        onboard.reserve_output_file(str(link))
    assert target.read_text() == "do-not-touch"


def test_atomic_finalize_failure_leaves_pending_escrow_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "pending.json"
    path, fd = onboard.reserve_output_file(str(output))
    expected_record = onboard.build_expected_key_record(request(), RAW_KEY, "William")
    onboard.write_pending_raw_key_file(fd, request(), RAW_KEY, expected_record)
    before = output.read_bytes()
    result = {
        "tenant": {"id": TENANT_ID},
        "first_key": {"raw_key": RAW_KEY, "metadata": {"sha256": "a" * 64}},
    }

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("injected atomic replace failure")

    monkeypatch.setattr(onboard.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        onboard.finalize_raw_key_file(path, fd, result)
    assert output.read_bytes() == before
    assert json.loads(output.read_text())["state"] == "pending_database_outcome"
    assert not list(tmp_path.glob(".pending.json.committed.*.tmp"))
    os.close(fd)


@pytest.mark.asyncio
async def test_dry_run_needs_no_dsn_output_or_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboard, "service_pg_dsn", lambda *_args: pytest.fail("loaded DSN"))
    result = await onboard.async_main(
        args(
            scope=["tenant:read"],
            agent=[],
            plan="free",
            expires_at="2027-01-01T00:00:00Z",
            ttl_seconds=None,
            dry_run=True,
            apply=False,
        )
    )
    assert result["mode"] == "dry-run"


@pytest.mark.asyncio
async def test_async_main_requires_safety_token_and_output_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboard, "service_pg_dsn", lambda *_args: pytest.fail("loaded DSN"))
    with pytest.raises(onboard.TenantOnboardingError, match="safety_confirm_required"):
        await onboard.async_main(args(confirm="wrong"))
    with pytest.raises(onboard.TenantOnboardingError, match="requires_output_file"):
        await onboard.async_main(args(confirm=onboard.expected_confirmation(request())))


@pytest.mark.asyncio
async def test_async_main_writes_raw_only_to_secure_file_and_stdout_is_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "first-key.json"
    request_value = request()
    connections: list[FakeConn] = []

    async def connect(_dsn: str) -> FakeConn:
        connection = FakeConn()
        connections.append(connection)
        return connection

    monkeypatch.setattr(
        onboard,
        "service_pg_dsn",
        lambda *_args: "postgresql://svc_soul_sdk_onboard_william:secret@localhost/db",
    )
    monkeypatch.setattr(onboard, "_connect", connect)
    monkeypatch.setattr(onboard, "new_api_key", lambda: RAW_KEY)
    result = await onboard.async_main(
        args(confirm=onboard.expected_confirmation(request_value), output_file=str(output))
    )
    assert RAW_KEY not in json.dumps(result)
    assert result["raw_key_delivery"]["written"] is True
    assert json.loads(output.read_text())["raw_api_key"] == RAW_KEY
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_failed_apply_removes_reserved_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "must-disappear.json"

    async def connect(_dsn: str) -> FakeConn:
        return FakeConn()

    async def failed(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("injected apply failure")

    monkeypatch.setattr(
        onboard,
        "service_pg_dsn",
        lambda *_args: "postgresql://svc_soul_sdk_onboard_william:secret@localhost/db",
    )
    monkeypatch.setattr(onboard, "_connect", connect)
    monkeypatch.setattr(onboard, "apply_with_retry", failed)
    monkeypatch.setattr(onboard, "new_api_key", lambda: RAW_KEY)
    with pytest.raises(RuntimeError, match="injected"):
        await onboard.async_main(
            args(confirm=onboard.expected_confirmation(request()), output_file=str(output))
        )
    assert not output.exists()


@pytest.mark.asyncio
async def test_indeterminate_apply_preserves_pending_raw_key_for_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "preserve-pending.json"

    async def connect(_dsn: str) -> FakeConn:
        return FakeConn()

    async def indeterminate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise onboard.IndeterminateOnboardingError("commit_outcome_unresolved_no_retry")

    monkeypatch.setattr(
        onboard,
        "service_pg_dsn",
        lambda *_args: "postgresql://svc_soul_sdk_onboard_william:secret@localhost/db",
    )
    monkeypatch.setattr(onboard, "_connect", connect)
    monkeypatch.setattr(onboard, "apply_with_retry", indeterminate)
    monkeypatch.setattr(onboard, "new_api_key", lambda: RAW_KEY)
    with pytest.raises(onboard.IndeterminateOnboardingError, match="unresolved"):
        await onboard.async_main(
            args(confirm=onboard.expected_confirmation(request()), output_file=str(output))
        )
    assert output.exists()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    preserved = json.loads(output.read_text())
    assert preserved["raw_api_key"] == RAW_KEY
    assert preserved["state"] == "pending_database_outcome"
    assert preserved["tenant_id"] == TENANT_ID
    assert preserved["tenant_name"] == "Acme Health"
    assert preserved["plan_id"] == "pro"
    assert preserved["expected_key_record"]["created_by"] == "William"
