from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soul_memory_sdk_runtime import (
    INTERNAL_TENANT_ID,
    SDK_RUNTIME_ROLE,
    SDK_USER_ROLE,
    TenantAuthError,
    TenantContext,
    TenantOverrideError,
    TenantScopeError,
    audit_query_hash,
    audit_user_read,
    api_key_record,
    create_memory,
    derive_tenant_from_api_key,
    ensure_agent,
    escape_like_pattern,
    extract_bearer_token,
    hash_api_key,
    list_memories,
    memory_insert_payload,
    normalize_agent_id,
    normalize_user_id,
    normalize_viewer,
    recall_memories,
    reject_tenant_override,
    resolve_tenant_db_identity,
    search_memories,
    set_tenant_context,
    tenant_transaction,
)


class FakeConn:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
        fetchrow_rows: list[dict[str, Any] | None] | None = None,
        fetchrow_error: Exception | None = None,
        fetchval_value: Any = None,
    ) -> None:
        self.row = row
        self.rows = rows or []
        self.fetchrow_rows = list(fetchrow_rows or [])
        self.fetchrow_error = fetchrow_error
        self.fetchval_value = fetchval_value
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((sql, args))
        if self.fetchrow_error is not None:
            raise self.fetchrow_error
        if self.fetchrow_rows:
            return self.fetchrow_rows.pop(0)
        return self.row

    async def execute(self, sql: str, *args: Any) -> None:
        self.execute_calls.append((sql, args))

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((sql, args))
        return self.rows

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.fetchval_calls.append((sql, args))
        return self.fetchval_value

    def transaction(self) -> "FakeTransaction":
        return FakeTransaction()


class FakeTransaction:
    async def __aenter__(self) -> "FakeTransaction":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> FakeConn:
        return self.conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
API_HASH = "a" * 64


def canonical_role(tenant_id: str, viewer: str = "agent") -> str:
    return f"soul_sdk_t_{tenant_id.replace('-', '')}_{viewer}"


def role_row(tenant_id: str, viewer: str = "agent") -> dict[str, str]:
    return {"tenant_id": tenant_id, "db_role": canonical_role(tenant_id, viewer)}


class UndefinedFunctionError(RuntimeError):
    sqlstate = "42883"


def test_hash_api_key_is_sha256_and_rejects_empty() -> None:
    assert hash_api_key("sk-soul-test") == hashlib.sha256(b"sk-soul-test").hexdigest()
    with pytest.raises(TenantAuthError, match="api_key_required"):
        hash_api_key("")


def test_extract_bearer_token_strict() -> None:
    assert extract_bearer_token("Bearer sk-soul-test") == "sk-soul-test"
    with pytest.raises(TenantAuthError, match="bearer_token_required"):
        extract_bearer_token("Basic sk-soul-test")


def test_api_key_record_never_contains_raw_key() -> None:
    record = api_key_record("sk-soul-secret", scopes=("read",))
    assert record == {"sha256": hash_api_key("sk-soul-secret"), "scopes": ["read"]}
    assert "sk-soul-secret" not in repr(record)


def test_tenant_context_enforces_scopes() -> None:
    tenant = TenantContext("11111111-1111-1111-1111-111111111111", "hash", ("read",))
    tenant.require("read")
    with pytest.raises(TenantScopeError, match="scope_required:write"):
        tenant.require("write")
    TenantContext("11111111-1111-1111-1111-111111111111", "hash", ("*",)).require("write")


def test_tenant_context_can_scope_agent_without_changing_tenant_identity() -> None:
    tenant = TenantContext(
        "11111111-1111-1111-1111-111111111111",
        "hash",
        ("read",),
        allowed_agents=("support_bot",),
    )
    scoped = tenant.with_agent(" support_bot ")

    assert scoped.tenant_id == tenant.tenant_id
    assert scoped.api_key_hash == tenant.api_key_hash
    assert scoped.scopes == tenant.scopes
    assert scoped.agent_id == "support_bot"
    assert tenant.agent_id is None


def test_tenant_context_can_scope_viewer_for_future_user_reads() -> None:
    tenant = TenantContext("11111111-1111-1111-1111-111111111111", "hash", ("read",))
    scoped = tenant.with_viewer(" user ", user_id="william")

    assert scoped.tenant_id == tenant.tenant_id
    assert scoped.viewer == "user"
    assert scoped.user_id == "william"
    assert scoped.agent_id is None
    assert tenant.viewer == "agent"


def test_normalize_agent_id_supports_optional_context_and_strict_insert() -> None:
    assert normalize_agent_id(None, required=False) is None
    assert normalize_agent_id(" ADA ", required=True) == "ADA"
    with pytest.raises(TenantOverrideError, match="agent_id_required_max_20_chars"):
        normalize_agent_id("", required=True)


def test_normalize_viewer_and_user_id_are_strict() -> None:
    assert normalize_viewer(None) == "agent"
    assert normalize_viewer("USER") == "user"
    assert normalize_user_id(None, required=False) is None
    assert normalize_user_id(" william ", required=True) == "william"
    with pytest.raises(TenantOverrideError, match="viewer_must_be_agent_user_or_admin"):
        normalize_viewer("tenant")
    with pytest.raises(TenantOverrideError, match="user_id_required_max_128_chars"):
        normalize_user_id("", required=True)


def test_escape_like_pattern_treats_wildcards_as_literals() -> None:
    assert escape_like_pattern(r"a%b_c\\d") == r"a\%b\_c\\\\d"


@pytest.mark.asyncio
async def test_derive_tenant_from_api_key_queries_only_hash() -> None:
    conn = FakeConn(
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "key_record": {"sha256": hash_api_key("sk-soul-secret"), "scopes": ["read"]},
        }
    )
    tenant = await derive_tenant_from_api_key(conn, "sk-soul-secret")
    sql, args = conn.fetchrow_calls[0]

    assert tenant == TenantContext(
        tenant_id="11111111-1111-1111-1111-111111111111",
        api_key_hash=hash_api_key("sk-soul-secret"),
        scopes=("read",),
    )
    assert args == (hash_api_key("sk-soul-secret"),)
    assert "sk-soul-secret" not in sql
    assert "jsonb_array_elements" in sql


@pytest.mark.asyncio
async def test_derive_tenant_rejects_unknown_key() -> None:
    conn = FakeConn(None)
    with pytest.raises(TenantAuthError, match="invalid_api_key"):
        await derive_tenant_from_api_key(conn, "sk-soul-missing")


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", ["2020-01-01T00:00:00+00:00", "not-a-date", "2026-07-22T12:00:00"])
async def test_derive_tenant_rejects_expired_or_malformed_expiry(expires_at: str) -> None:
    conn = FakeConn(
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "key_record": {
                "sha256": hash_api_key("sk-soul-expired"),
                "scopes": ["read"],
                "expires_at": expires_at,
            },
        }
    )
    with pytest.raises(TenantAuthError, match="invalid_api_key"):
        await derive_tenant_from_api_key(conn, "sk-soul-expired")

    sql, _ = conn.fetchrow_calls[0]
    assert "expires_at" in sql
    assert "pg_input_is_valid" in sql


@pytest.mark.asyncio
async def test_derive_tenant_accepts_legacy_key_without_expiry() -> None:
    conn = FakeConn(
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "key_record": {"sha256": hash_api_key("sk-soul-legacy"), "scopes": ["read"]},
        }
    )
    tenant = await derive_tenant_from_api_key(conn, "sk-soul-legacy")
    assert tenant.scopes == ("read",)


@pytest.mark.asyncio
async def test_derive_tenant_accepts_unexpired_key() -> None:
    conn = FakeConn(
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "key_record": {
                "sha256": hash_api_key("sk-soul-current"),
                "scopes": ["read"],
                "expires_at": "2999-01-01T00:00:00Z",
            },
        }
    )
    tenant = await derive_tenant_from_api_key(conn, "sk-soul-current")
    assert tenant.api_key_hash == hash_api_key("sk-soul-current")


def test_reject_tenant_override_checks_header_query_and_payload() -> None:
    with pytest.raises(TenantOverrideError):
        reject_tenant_override(headers={"X-Tenant-ID": "bad"})
    with pytest.raises(TenantOverrideError):
        reject_tenant_override(query={"tenant_id": "bad"})
    with pytest.raises(TenantOverrideError):
        reject_tenant_override(payload={"org_id": "bad"})

    reject_tenant_override(
        headers={"authorization": "Bearer sk-soul-ok"},
        query={"limit": "10"},
        payload={"content": "safe"},
    )


@pytest.mark.asyncio
async def test_set_tenant_context_uses_hash_resolved_role_not_tenant_guc() -> None:
    conn = FakeConn(row=role_row(TENANT_A))
    tenant = TenantContext(
        tenant_id=TENANT_A,
        api_key_hash=API_HASH,
    )
    identity = await set_tenant_context(conn, tenant)

    resolver_sql, resolver_args = conn.fetchrow_calls[0]
    assert resolver_args == (API_HASH, "agent")
    assert "sdk_resolve_tenant_role_for_key_hash($1, $2)" in resolver_sql
    assert TENANT_A not in resolver_sql
    assert identity.db_role == canonical_role(TENANT_A)
    assert not identity.legacy_internal_fallback

    assert conn.execute_calls[0] == (f"SET LOCAL ROLE {canonical_role(TENANT_A)}", ())
    assert conn.execute_calls[1] == (
        "SELECT set_config('app.tenant_id', $1, true)",
        ("",),
    )
    assert conn.execute_calls[2] == (
        "SELECT set_config('app.agent', $1, true)",
        ("",),
    )
    assert conn.execute_calls[3] == (
        "SELECT set_config('app.viewer', $1, true)",
        ("agent",),
    )
    assert conn.execute_calls[4] == (
        "SELECT set_config('app.user_id', $1, true)",
        ("",),
    )


@pytest.mark.asyncio
async def test_set_tenant_context_sets_optional_agent_guc() -> None:
    conn = FakeConn(row=role_row(TENANT_A))
    tenant = TenantContext(
        tenant_id=TENANT_A,
        api_key_hash=API_HASH,
        agent_id="support_bot",
    )
    await set_tenant_context(conn, tenant)

    assert conn.execute_calls[2] == (
        "SELECT set_config('app.agent', $1, true)",
        ("support_bot",),
    )


@pytest.mark.asyncio
async def test_set_tenant_context_sets_optional_viewer_gucs() -> None:
    conn = FakeConn(row=role_row(TENANT_A, "user"))
    tenant = TenantContext(
        tenant_id=TENANT_A,
        api_key_hash=API_HASH,
        viewer="user",
        user_id="william",
    )
    await set_tenant_context(conn, tenant)

    assert conn.execute_calls[3] == (
        "SELECT set_config('app.viewer', $1, true)",
        ("user",),
    )
    assert conn.execute_calls[4] == (
        "SELECT set_config('app.user_id', $1, true)",
        ("william",),
    )


@pytest.mark.asyncio
async def test_set_tenant_context_uses_tenant_specific_user_role() -> None:
    conn = FakeConn(row=role_row(TENANT_A, "user"))
    tenant = TenantContext(
        tenant_id=TENANT_A,
        api_key_hash=API_HASH,
        viewer="user",
        user_id="william",
    )
    await set_tenant_context(conn, tenant)

    assert conn.execute_calls[0] == (
        f"SET LOCAL ROLE {canonical_role(TENANT_A, 'user')}",
        (),
    )


@pytest.mark.asyncio
async def test_tenant_transaction_can_scope_user_viewer() -> None:
    conn = FakeConn(row=role_row(TENANT_A, "user"))
    tenant = TenantContext(
        tenant_id=TENANT_A,
        api_key_hash=API_HASH,
    )

    async with tenant_transaction(FakePool(conn), tenant, viewer="user", user_id="william"):
        pass

    assert conn.fetchrow_calls[0][1] == (API_HASH, "user")
    assert conn.execute_calls[0] == (
        f"SET LOCAL ROLE {canonical_role(TENANT_A, 'user')}",
        (),
    )
    assert conn.execute_calls[3] == ("SELECT set_config('app.viewer', $1, true)", ("user",))
    assert conn.execute_calls[4] == ("SELECT set_config('app.user_id', $1, true)", ("william",))


@pytest.mark.asyncio
async def test_resolver_fails_closed_for_missing_mapping() -> None:
    conn = FakeConn(row=None)
    tenant = TenantContext(TENANT_A, API_HASH)

    with pytest.raises(TenantAuthError, match="tenant_db_role_unmapped"):
        await set_tenant_context(conn, tenant)
    assert conn.execute_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "error"),
    (
        ({"tenant_id": TENANT_B, "db_role": canonical_role(TENANT_B)}, "tenant_db_identity_mismatch"),
        ({"tenant_id": TENANT_A, "db_role": SDK_RUNTIME_ROLE}, "tenant_db_role_mismatch"),
    ),
)
async def test_resolver_rejects_tenant_or_role_mismatch(
    row: dict[str, str],
    error: str,
) -> None:
    conn = FakeConn(row=row)
    with pytest.raises(TenantAuthError, match=error):
        await resolve_tenant_db_identity(conn, TenantContext(TENANT_A, API_HASH))
    assert conn.execute_calls == []


@pytest.mark.asyncio
async def test_resolver_rejects_non_sha256_context_hash() -> None:
    conn = FakeConn(row=role_row(TENANT_A))
    with pytest.raises(TenantAuthError, match="invalid_api_key"):
        await resolve_tenant_db_identity(conn, TenantContext(TENANT_A, "not-a-hash"))
    assert conn.fetchrow_calls == []


@pytest.mark.asyncio
async def test_resolver_rejects_malformed_database_result() -> None:
    conn = FakeConn(row={"tenant_id": TENANT_A})
    with pytest.raises(TenantAuthError, match="tenant_db_role_resolution_failed"):
        await resolve_tenant_db_identity(conn, TenantContext(TENANT_A, API_HASH))
    assert conn.execute_calls == []


@pytest.mark.asyncio
async def test_rollout_fallback_is_only_internal_and_only_when_function_missing() -> None:
    internal_conn = FakeConn(
        fetchrow_error=UndefinedFunctionError("missing"),
        fetchval_value=True,
    )
    internal = TenantContext(INTERNAL_TENANT_ID, API_HASH)

    identity = await set_tenant_context(internal_conn, internal)
    assert identity.legacy_internal_fallback
    assert identity.db_role == SDK_RUNTIME_ROLE
    legacy_sql, legacy_args = internal_conn.fetchval_calls[0]
    assert legacy_args == (API_HASH, INTERNAL_TENANT_ID)
    assert "count(*) = 1" in legacy_sql
    assert "revoked_at" in legacy_sql
    assert "expires_at" in legacy_sql
    assert internal_conn.execute_calls[0] == (f"SET LOCAL ROLE {SDK_RUNTIME_ROLE}", ())
    assert internal_conn.execute_calls[1] == (
        "SELECT set_config('app.tenant_id', $1, true)",
        (INTERNAL_TENANT_ID,),
    )

    external_conn = FakeConn(fetchrow_error=UndefinedFunctionError("missing"))
    with pytest.raises(TenantAuthError, match="tenant_db_role_resolver_unavailable"):
        await set_tenant_context(external_conn, TenantContext(TENANT_A, API_HASH))
    assert external_conn.execute_calls == []

    internal_user_conn = FakeConn(
        fetchrow_error=UndefinedFunctionError("missing"),
        fetchval_value=True,
    )
    internal_user = TenantContext(
        INTERNAL_TENANT_ID,
        API_HASH,
        viewer="user",
        user_id="william",
    )
    user_identity = await set_tenant_context(internal_user_conn, internal_user)
    assert user_identity.db_role == SDK_USER_ROLE
    assert user_identity.legacy_internal_fallback

    unsupported_conn = FakeConn(fetchrow_error=UndefinedFunctionError("missing"))
    with pytest.raises(TenantAuthError, match="tenant_db_viewer_unmapped"):
        await set_tenant_context(
            unsupported_conn,
            TenantContext(INTERNAL_TENANT_ID, API_HASH, viewer="admin", user_id="william"),
        )
    assert unsupported_conn.fetchrow_calls == []

    revoked_conn = FakeConn(
        fetchrow_error=UndefinedFunctionError("missing"),
        fetchval_value=False,
    )
    with pytest.raises(TenantAuthError, match="invalid_api_key"):
        await set_tenant_context(revoked_conn, internal)
    assert revoked_conn.execute_calls == []


@pytest.mark.asyncio
async def test_installed_resolver_never_falls_back_for_internal_unmapped_role() -> None:
    conn = FakeConn(row=None)
    with pytest.raises(TenantAuthError, match="tenant_db_role_unmapped"):
        await set_tenant_context(conn, TenantContext(INTERNAL_TENANT_ID, API_HASH))
    assert conn.execute_calls == []


def test_memory_insert_payload_adds_trusted_tenant_and_rejects_client_tenant() -> None:
    tenant = TenantContext(
        tenant_id="11111111-1111-1111-1111-111111111111",
        api_key_hash="hash",
    )
    payload = memory_insert_payload(
        {
            "agent_id": "support_bot",
            "content": "User prefers concise answers.",
            "category": "preference",
            "importance": 8,
            "metadata": {"source": "unit"},
        },
        tenant,
    )
    assert payload["tenant_id"] == tenant.tenant_id
    assert payload["agent"] == "support_bot"
    assert payload["source"] == "sdk_api"
    assert payload["metadata"] == {"source": "unit"}

    with pytest.raises(TenantOverrideError):
        memory_insert_payload({"tenant_id": "bad", "content": "x"}, tenant)
    with pytest.raises(TenantOverrideError, match="agent_id_required_max_20_chars"):
        memory_insert_payload({"agent_id": "x" * 21, "content": "x"}, tenant)


@pytest.mark.asyncio
async def test_ensure_agent_creates_minimal_fk_row() -> None:
    conn = FakeConn()
    await ensure_agent(conn, "support_bot")
    sql, args = conn.execute_calls[0]
    assert "INSERT INTO soul_v3.agents" in sql
    assert "ON CONFLICT (name) DO NOTHING" in sql
    assert args == ("support_bot",)


@pytest.mark.asyncio
async def test_create_memory_inserts_server_derived_tenant() -> None:
    tenant = TenantContext(
        tenant_id="11111111-1111-1111-1111-111111111111",
        api_key_hash="hash",
    )
    conn = FakeConn(
        {
            "id": 42,
            "agent": "support_bot",
            "scope": "private",
            "category": "fact",
            "content": "User likes short replies.",
            "importance": 7,
            "memory_type": "semantic",
            "metadata": {},
            "created_at": "2026-05-26T00:00:00Z",
            "content_hash_sha256": "abc",
        }
    )
    memory = await create_memory(
        conn,
        tenant,
        {
            "agent_id": "support_bot",
            "content": "User likes short replies.",
            "importance": 7,
        },
    )
    sql, args = conn.fetchrow_calls[0]
    agent_sql, agent_args = conn.execute_calls[0]
    assert "INSERT INTO soul_v3.agents" in agent_sql
    assert agent_args == ("support_bot",)
    assert "INSERT INTO soul_v3.memories" in sql
    assert args[0] == tenant.tenant_id
    assert memory["id"] == 42
    assert memory["agent_id"] == "support_bot"


@pytest.mark.asyncio
async def test_create_memory_requires_write_scope() -> None:
    tenant = TenantContext(
        tenant_id="11111111-1111-1111-1111-111111111111",
        api_key_hash="hash",
        scopes=("read",),
    )
    with pytest.raises(TenantScopeError, match="scope_required:write"):
        await create_memory(
            FakeConn(),
            tenant,
            {"agent_id": "support_bot", "content": "x"},
        )


@pytest.mark.asyncio
async def test_list_memories_uses_rls_not_tenant_filter() -> None:
    conn = FakeConn(rows=[])
    await list_memories(
        conn,
        query={"limit": "10"},
        agent_id="support_bot",
        category="fact",
        importance_gte=5,
        limit=10,
    )
    sql, args = conn.fetch_calls[0]
    assert "tenant_id" not in sql
    assert "agent=$1" in sql
    assert args[:3] == ("support_bot", "fact", 5)

    with pytest.raises(TenantOverrideError):
        await list_memories(conn, query={"tenant_id": "bad"})


@pytest.mark.asyncio
async def test_recall_memories_audits_same_tenant_context() -> None:
    tenant = TenantContext(
        tenant_id="11111111-1111-1111-1111-111111111111",
        api_key_hash="hash",
    )
    conn = FakeConn(
        rows=[
            {
                "id": 7,
                "agent": "support_bot",
                "scope": "private",
                "category": "preference",
                "content": "User likes short replies.",
                "importance": 8,
                "memory_type": "semantic",
                "metadata": {},
                "created_at": "2026-05-26T00:00:00Z",
                "content_hash_sha256": "abc",
            }
        ]
    )
    result = await recall_memories(conn, tenant, query_text="short", limit=5)
    search_sql, search_args = conn.fetch_calls[0]
    audit_sql, audit_args = conn.execute_calls[0]

    assert "tenant_id" not in search_sql
    assert "ESCAPE" in search_sql
    assert search_args[0] == "%short%"
    assert result["total_hits"] == 1
    assert "INSERT INTO soul_v3.memory_retrieval_log" in audit_sql
    assert audit_args[0] == tenant.tenant_id
    assert audit_args[4] == [7]


@pytest.mark.asyncio
async def test_recall_escapes_like_wildcards() -> None:
    tenant = TenantContext(
        tenant_id="11111111-1111-1111-1111-111111111111",
        api_key_hash="hash",
    )
    conn = FakeConn(rows=[])
    await recall_memories(conn, tenant, query_text=r"100%_done\\ok", limit=5)
    _, args = conn.fetch_calls[0]
    assert args[0] == r"%100\%\_done\\\\ok%"


@pytest.mark.asyncio
async def test_search_memories_does_not_write_audit_by_itself() -> None:
    conn = FakeConn(rows=[])
    await search_memories(conn, query_text="short", limit=5)

    assert len(conn.fetch_calls) == 1
    assert conn.execute_calls == []


def test_audit_query_hash_is_stable_and_redacts_secrets() -> None:
    digest = audit_query_hash(
        endpoint="/v1/tenant/recall",
        payload={"query": "hello", "api_key": "sk-soul-secret"},
    )
    same_digest = audit_query_hash(
        endpoint="/v1/tenant/recall",
        payload={"api_key": "different-secret", "query": "hello"},
    )

    assert len(digest) == 64
    assert digest == same_digest
    assert "sk-soul-secret" not in digest


@pytest.mark.asyncio
async def test_audit_user_read_inserts_without_returning() -> None:
    tenant = TenantContext(
        tenant_id="11111111-1111-1111-1111-111111111111",
        api_key_hash="hash",
    )
    conn = FakeConn()
    await audit_user_read(
        conn,
        tenant,
        endpoint="/v1/tenant/memories",
        user_id="william",
        query_hash="abc123",
        rows_read=3,
        latency_ms=12,
        metadata={"limit": 3},
    )
    sql, args = conn.execute_calls[0]

    assert "INSERT INTO soul_v3.user_read_audit" in sql
    assert "RETURNING" not in sql
    assert args[:6] == (
        tenant.tenant_id,
        "william",
        "abc123",
        "/v1/tenant/memories",
        3,
        12,
    )


# --- Cura seguridad agent-scope (NEXUS 13-jun, audit FABLE M12: cross-agent bypass) ---

def test_with_agent_denies_unauthorized_agent():
    """Una key autorizada solo para 'ada' NO puede asumir otro agente (bypass cerrado)."""
    t = TenantContext(tenant_id="t", api_key_hash="abc123",
                      scopes=("read", "write"), allowed_agents=("ada",))
    assert t.with_agent("ada").agent_id == "ada"          # su propio agente: OK
    with pytest.raises(TenantScopeError):
        t.with_agent("jarvis")                            # cross-agent: DENY


def test_with_agent_admin_scope_allows_cross_agent():
    """scope '*' (admin) puede cruzar agentes legitimamente."""
    t = TenantContext(tenant_id="t", api_key_hash="adm",
                      scopes=("*",), allowed_agents=("ada",))
    assert t.with_agent("jarvis").agent_id == "jarvis"


def test_with_agent_legacy_no_allowed_agents_fails_closed():
    """Una key agent-scoped sin allowlist no puede elegir identidad."""
    t = TenantContext(tenant_id="t", api_key_hash="legacy99", scopes=("read", "write"))
    with pytest.raises(TenantScopeError, match="agent_allowlist_required"):
        t.with_agent("cualquiera")


def test_with_viewer_preserves_allowed_agents():
    """with_viewer no debe perder la restriccion de agentes (passthrough)."""
    t = TenantContext(tenant_id="t", api_key_hash="k",
                      scopes=("read",), allowed_agents=("ada",))
    assert t.with_viewer("user", user_id="william").allowed_agents == ("ada",)


def test_with_agent_tenant_wide_scope_bypasses_restriction():
    """Scope 'tenant:read' (tenant-wide por diseno) cruza agentes sin DENY ni warn."""
    t = TenantContext(tenant_id="t", api_key_hash="tw", scopes=("tenant:read",))
    assert t.with_agent("ada").agent_id == "ada"
    assert t.with_agent("jarvis").agent_id == "jarvis"   # tenant-wide: cruza, sin restriccion
