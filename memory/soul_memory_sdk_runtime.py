#!/usr/bin/env python3
"""Runtime guards for SOUL Memory SDK multi-tenant API access.

This layer is intentionally small: it derives tenant identity from a raw API
key, rejects client-supplied tenant overrides, and opens transactions with the
Postgres tenant context required by Phase 1 RLS policies.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Mapping
from uuid import UUID


SDK_RUNTIME_ROLE = "soul_sdk_agent_api"
SDK_USER_ROLE = "soul_sdk_tenant_api"
SDK_ADMIN_ROLE = "soul_sdk_admin"
SDK_VIEWERS = {"agent", "user", "admin"}
SDK_VIEWER_ROLES = {
    "agent": SDK_RUNTIME_ROLE,
    "user": SDK_USER_ROLE,
    "admin": SDK_ADMIN_ROLE,
}
INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000000"
TENANT_DB_VIEWERS = {"agent", "user"}
TENANT_DB_ROLE_PREFIX = "soul_sdk_t_"
API_KEY_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TENANT_OVERRIDE_FIELDS = {
    "tenant_id",
    "tenant",
    "org_id",
    "x_tenant_id",
    "x_tenant",
    "x_org_id",
}
MAX_AGENT_NAME_LEN = 20


class TenantAuthError(ValueError):
    """Raised when API key authentication fails."""


class TenantOverrideError(ValueError):
    """Raised when a public request tries to choose its own tenant."""


class TenantScopeError(ValueError):
    """Raised when an API key lacks the scope required for an operation."""


@dataclass(frozen=True)
class ResolvedTenantDbIdentity:
    """Database identity revalidated from the API-key hash for one transaction."""

    tenant_id: str
    db_role: str
    viewer: str
    legacy_internal_fallback: bool = False


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    api_key_hash: str
    scopes: tuple[str, ...] = ("read", "write")
    agent_id: str | None = None
    viewer: str = "agent"
    user_id: str | None = None
    # Cura seguridad (NEXUS 13-jun, audit FABLE M12): agente(s) que ESTA key puede asumir.
    # Vacío en una key agent-scoped = DENY. Con tenant único compartido, el
    # aislamiento entre principals depende de ESTO, no solo del tenant_id.
    allowed_agents: tuple[str, ...] = ()

    def allows(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes

    def require(self, scope: str) -> None:
        if not self.allows(scope):
            raise TenantScopeError(f"scope_required:{scope}")

    def with_agent(self, agent_id: str | None) -> "TenantContext":
        agent = normalize_agent_id(agent_id, required=False)
        # Cura seguridad (NEXUS 13-jun, audit FABLE M12): el agent-scope NO se deriva libre del
        # input del cliente. agent_id fuera de los autorizados de la key = DENY.
        # Bypass para scopes TENANT-WIDE ('*' o 'tenant:*'): leen todo el tenant POR DISENO, no se
        # restringen por agente ni se warnean (refinamiento por ground-truth: las keys vivas son
        # tenant:read). La restriccion+warn aplican SOLO a keys agent-scoped (read/write).
        tenant_wide = "*" in self.scopes or any(str(s).startswith("tenant:") for s in self.scopes)
        if agent is not None and not tenant_wide:
            if self.allowed_agents:
                if agent not in self.allowed_agents:
                    raise TenantScopeError(f"agent_not_authorized:{agent}")
            else:
                # Agent-scoped keys without an explicit allowlist used to pass
                # through with a warning.  That made a missing migration field
                # an authorization bypass.  Public runtime identity now fails
                # closed; tenant-wide keys remain intentionally tenant-wide.
                raise TenantScopeError("agent_allowlist_required")
        return TenantContext(
            tenant_id=self.tenant_id,
            api_key_hash=self.api_key_hash,
            scopes=self.scopes,
            agent_id=agent,
            viewer=self.viewer,
            user_id=self.user_id,
            allowed_agents=self.allowed_agents,
        )

    def with_viewer(self, viewer: str, *, user_id: str | None = None) -> "TenantContext":
        normalized_viewer = normalize_viewer(viewer)
        return TenantContext(
            tenant_id=self.tenant_id,
            api_key_hash=self.api_key_hash,
            scopes=self.scopes,
            agent_id=self.agent_id,
            viewer=normalized_viewer,
            user_id=normalize_user_id(user_id, required=normalized_viewer in {"user", "admin"}),
            allowed_agents=self.allowed_agents,
        )

    def pg_role(self) -> str:
        """Return the rollout-only generic role for the internal tenant.

        Normal transactions must use ``resolve_tenant_db_identity`` instead.
        """
        return SDK_VIEWER_ROLES[normalize_viewer(self.viewer)]


@dataclass(frozen=True)
class MemoryRetrievalResult:
    """Memories plus the retrieval implementation exercised for observability."""

    memories: list[dict[str, Any]]
    mode: str


RETRIEVAL_MODE_HYBRID = "hybrid_pgvector_fts"
RETRIEVAL_MODE_LEXICAL_FALLBACK = "lexical_fallback"


def normalize_agent_id(agent_id: Any, *, required: bool) -> str | None:
    agent = str(agent_id or "").strip()
    if not agent:
        if required:
            raise TenantOverrideError("agent_id_required_max_20_chars")
        return None
    if len(agent) > MAX_AGENT_NAME_LEN:
        raise TenantOverrideError("agent_id_required_max_20_chars")
    return agent


def normalize_viewer(viewer: Any) -> str:
    normalized = str(viewer or "agent").strip().lower()
    if normalized not in SDK_VIEWERS:
        raise TenantOverrideError("viewer_must_be_agent_user_or_admin")
    return normalized


def normalize_user_id(user_id: Any, *, required: bool) -> str | None:
    value = str(user_id or "").strip()
    if not value:
        if required:
            raise TenantOverrideError("user_id_required_max_128_chars")
        return None
    if len(value) > 128:
        raise TenantOverrideError("user_id_required_max_128_chars")
    return value


def hash_api_key(api_key: str) -> str:
    if not api_key:
        raise TenantAuthError("api_key_required")
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def new_api_key() -> str:
    """Generate a raw API key shown once during tenant/key creation."""
    return "sk-soul-" + secrets.token_urlsafe(32)


def api_key_record(api_key: str, *, scopes: tuple[str, ...] = ("read", "write")) -> dict[str, Any]:
    """Return the JSONB-safe record stored in soul_v3.tenants.api_keys."""
    return {"sha256": hash_api_key(api_key), "scopes": list(scopes)}


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise TenantAuthError("authorization_required")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise TenantAuthError("bearer_token_required")
    return parts[1]


def _scopes_from_record(record: Mapping[str, Any]) -> tuple[str, ...]:
    raw_scopes = record.get("scopes")
    if isinstance(raw_scopes, list):
        scopes = tuple(str(scope) for scope in raw_scopes if str(scope))
        return scopes or ("read", "write")
    scope = record.get("scope")
    if isinstance(scope, str) and scope:
        return (scope,)
    return ("read", "write")


def _record_is_unexpired(record: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """Fail closed for malformed or elapsed API-key expiry metadata.

    Records created before expiry support have no ``expires_at`` and remain
    valid for backward compatibility. New records use timezone-aware ISO-8601.
    """
    raw_expiry = record.get("expires_at")
    if raw_expiry in (None, ""):
        return True
    if not isinstance(raw_expiry, str):
        return False
    try:
        expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        return False
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now_must_be_timezone_aware")
    return expiry.astimezone(UTC) > current.astimezone(UTC)


def _tenant_lookup_sql() -> str:
    return """
        SELECT t.id::text AS tenant_id, key_record
        FROM soul_v3.tenants AS t
        CROSS JOIN LATERAL jsonb_array_elements(t.api_keys) AS key_record
        WHERE (
            key_record->>'sha256' = $1
            OR key_record->>'hash' = $1
            OR key_record->>'key_hash' = $1
        )
          AND COALESCE(key_record->>'revoked_at', '') = ''
          AND CASE
                WHEN COALESCE(key_record->>'expires_at', '') = '' THEN TRUE
                WHEN pg_input_is_valid(key_record->>'expires_at', 'timestamp with time zone')
                  THEN (key_record->>'expires_at')::timestamptz > CURRENT_TIMESTAMP
                ELSE FALSE
              END
        LIMIT 1
    """


def _internal_rollout_key_check_sql() -> str:
    """Revalidate the one allowed legacy key path without trusting prior auth."""
    return """
        SELECT count(*) = 1 AND bool_and(t.id = $2::uuid)
        FROM soul_v3.tenants AS t
        CROSS JOIN LATERAL jsonb_array_elements(t.api_keys) AS key_record
        WHERE (
            key_record->>'sha256' = $1
            OR key_record->>'hash' = $1
            OR key_record->>'key_hash' = $1
        )
          AND COALESCE(key_record->>'revoked_at', '') = ''
          AND CASE
                WHEN COALESCE(key_record->>'expires_at', '') = '' THEN TRUE
                WHEN pg_input_is_valid(key_record->>'expires_at', 'timestamp with time zone')
                  THEN (key_record->>'expires_at')::timestamptz > CURRENT_TIMESTAMP
                ELSE FALSE
              END
    """


def escape_like_pattern(text: str) -> str:
    """Escape user text for ILIKE so %, _ and backslash stay literal."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


async def derive_tenant_from_api_key(conn: Any, api_key: str) -> TenantContext:
    """Resolve a tenant from a raw API key without ever querying by raw key."""
    api_key_hash = hash_api_key(api_key)
    row = await conn.fetchrow(_tenant_lookup_sql(), api_key_hash)
    if not row:
        raise TenantAuthError("invalid_api_key")

    record = row["key_record"]
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except json.JSONDecodeError:
            record = {}
    if not isinstance(record, Mapping):
        record = {}
    if not _record_is_unexpired(record):
        # Deliberately indistinguishable from an unknown/revoked key.
        raise TenantAuthError("invalid_api_key")
    return TenantContext(
        tenant_id=str(row["tenant_id"]),
        api_key_hash=api_key_hash,
        scopes=_scopes_from_record(record),
        allowed_agents=tuple(
            str(a).strip() for a in (record.get("agents") or ()) if str(a).strip()
        ),
    )


def reject_tenant_override(
    *,
    headers: Mapping[str, Any] | None = None,
    query: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Reject public attempts to override tenant identity.

    The tenant must come from the API key only. Headers are checked in normalized
    form so X-Tenant-ID and x-tenant-id are both rejected.
    """
    header_keys = {str(k).lower().replace("-", "_") for k in (headers or {})}
    query_keys = {str(k).lower() for k in (query or {})}
    payload_keys = _nested_mapping_keys(payload or {})
    if TENANT_OVERRIDE_FIELDS & (header_keys | query_keys | payload_keys):
        raise TenantOverrideError("tenant_id_must_come_from_api_key")


def _nested_mapping_keys(value: Any) -> set[str]:
    """Return normalized keys from nested user payloads."""
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key).lower())
            keys.update(_nested_mapping_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_nested_mapping_keys(item))
    return keys


async def set_tenant_context(conn: Any, tenant: TenantContext) -> ResolvedTenantDbIdentity:
    """Revalidate and assume the tenant-bound Postgres identity.

    Tenant authority comes from the role returned by the database resolver and
    therefore from ``current_user`` under migration 055. ``app.tenant_id`` is
    cleared for canonical roles so a forged/stale GUC cannot select a tenant.
    The sole rollout fallback is the existing internal tenant when the resolver
    function has not been installed yet; an installed-but-empty mapping always
    fails closed.
    """
    identity = await resolve_tenant_db_identity(conn, tenant)
    await conn.execute(f"SET LOCAL ROLE {identity.db_role}")
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, true)",
        INTERNAL_TENANT_ID if identity.legacy_internal_fallback else "",
    )
    await conn.execute("SELECT set_config('app.agent', $1, true)", tenant.agent_id or "")
    await conn.execute("SELECT set_config('app.viewer', $1, true)", normalize_viewer(tenant.viewer))
    await conn.execute("SELECT set_config('app.user_id', $1, true)", tenant.user_id or "")
    return identity


def _canonical_tenant_db_role(tenant_id: str, viewer: str) -> str:
    try:
        tenant_uuid = UUID(str(tenant_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TenantAuthError("invalid_tenant_identity") from exc
    normalized_viewer = normalize_viewer(viewer)
    if normalized_viewer not in TENANT_DB_VIEWERS:
        raise TenantAuthError("tenant_db_viewer_unmapped")
    return f"{TENANT_DB_ROLE_PREFIX}{tenant_uuid.hex}_{normalized_viewer}"


def _is_missing_tenant_role_resolver(exc: Exception) -> bool:
    return (
        getattr(exc, "sqlstate", None) == "42883"
        or type(exc).__name__ == "UndefinedFunctionError"
    )


async def resolve_tenant_db_identity(
    conn: Any,
    tenant: TenantContext,
) -> ResolvedTenantDbIdentity:
    """Resolve ``api_key_hash + viewer`` to one canonical DB role.

    The raw key is never passed here. The DB resolver rechecks key lifecycle,
    the active role binding, role attributes and SET-role membership. Runtime
    additionally verifies the returned tenant and deterministic role name.
    """
    viewer = normalize_viewer(tenant.viewer)
    expected_role = _canonical_tenant_db_role(tenant.tenant_id, viewer)
    if not API_KEY_HASH_PATTERN.fullmatch(str(tenant.api_key_hash or "")):
        raise TenantAuthError("invalid_api_key")

    try:
        row = await conn.fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, db_role::text AS db_role
            FROM soul_v3.sdk_resolve_tenant_role_for_key_hash($1::text, $2::text)
            """,
            tenant.api_key_hash,
            viewer,
        )
    except Exception as exc:
        if (
            _is_missing_tenant_role_resolver(exc)
            and tenant.tenant_id == INTERNAL_TENANT_ID
            and viewer in TENANT_DB_VIEWERS
        ):
            try:
                internal_key_is_unique = bool(
                    await conn.fetchval(
                        _internal_rollout_key_check_sql(),
                        tenant.api_key_hash,
                        INTERNAL_TENANT_ID,
                    )
                )
            except Exception as validation_exc:
                raise TenantAuthError("tenant_db_role_resolution_failed") from validation_exc
            if not internal_key_is_unique:
                raise TenantAuthError("invalid_api_key")
            return ResolvedTenantDbIdentity(
                tenant_id=INTERNAL_TENANT_ID,
                db_role=tenant.pg_role(),
                viewer=viewer,
                legacy_internal_fallback=True,
            )
        if _is_missing_tenant_role_resolver(exc):
            raise TenantAuthError("tenant_db_role_resolver_unavailable") from exc
        raise TenantAuthError("tenant_db_role_resolution_failed") from exc

    if not row:
        raise TenantAuthError("tenant_db_role_unmapped")
    try:
        resolved_tenant = str(row["tenant_id"])
        resolved_role = str(row["db_role"])
    except (KeyError, TypeError) as exc:
        raise TenantAuthError("tenant_db_role_resolution_failed") from exc
    if resolved_tenant != str(UUID(tenant.tenant_id)):
        raise TenantAuthError("tenant_db_identity_mismatch")
    if resolved_role != expected_role:
        raise TenantAuthError("tenant_db_role_mismatch")
    return ResolvedTenantDbIdentity(
        tenant_id=resolved_tenant,
        db_role=resolved_role,
        viewer=viewer,
    )


@asynccontextmanager
async def tenant_transaction(
    pool: Any,
    tenant: TenantContext,
    *,
    agent_id: str | None = None,
    viewer: str | None = None,
    user_id: str | None = None,
) -> AsyncIterator[Any]:
    """Acquire a connection, open a transaction, and set RLS context locally."""
    scoped_tenant = tenant.with_agent(agent_id) if agent_id is not None else tenant
    if viewer is not None:
        scoped_tenant = scoped_tenant.with_viewer(viewer, user_id=user_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await set_tenant_context(conn, scoped_tenant)
            yield conn


def audit_query_hash(
    *,
    endpoint: str,
    query: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Stable hash of safe request shape for user-read audit rows."""
    safe_query = {str(k): str(v) for k, v in sorted((query or {}).items())}
    safe_payload = {
        str(k): v
        for k, v in sorted((payload or {}).items())
        if str(k).lower() not in {"authorization", "api_key", "token", "password", "secret"}
    }
    material = json.dumps(
        {"endpoint": endpoint, "query": safe_query, "payload": safe_payload},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def audit_user_read(
    conn: Any,
    tenant: TenantContext,
    *,
    endpoint: str,
    user_id: str,
    query_hash: str,
    rows_read: int,
    latency_ms: int,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Mandatory tenant-owner read audit. No RETURNING; user role has no audit SELECT."""
    await conn.execute(
        """
        INSERT INTO soul_v3.user_read_audit
            (tenant_id, user_id, query_hash, endpoint, rows_read, latency_ms, metadata)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb)
        """,
        tenant.tenant_id,
        normalize_user_id(user_id, required=True),
        query_hash,
        endpoint,
        max(0, int(rows_read)),
        max(0, int(latency_ms)),
        json.dumps(dict(metadata or {}), sort_keys=True, default=str),
    )


def memory_insert_payload(payload: Mapping[str, Any], tenant: TenantContext) -> dict[str, Any]:
    """Server-side insert payload for soul_v3.memories.

    Client-provided tenant fields must already have been rejected. This function
    adds the trusted tenant_id derived from the API key.
    """
    reject_tenant_override(payload=payload)
    agent = normalize_agent_id(payload.get("agent_id") or payload.get("agent") or "default", required=True)
    return {
        "tenant_id": tenant.tenant_id,
        "agent": agent,
        "scope": str(payload.get("scope") or "private"),
        "category": str(payload.get("category") or "fact"),
        "content": str(payload.get("content") or ""),
        "importance": int(payload.get("importance") or 5),
        "memory_type": str(payload.get("memory_type") or "semantic"),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "source": "sdk_api",
        "valid_from": payload.get("valid_at"),
    }


async def ensure_agent(conn: Any, agent_name: str) -> None:
    """Ensure the global agent dimension row exists for the memory FK.

    The public API does not expose soul_v3.agents directly. This helper only
    creates the minimal FK row required by soul_v3.memories.
    """
    await conn.execute(
        """
        INSERT INTO soul_v3.agents (name, role, active)
        VALUES ($1, 'SDK Agent', true)
        ON CONFLICT (name) DO NOTHING
        """,
        agent_name,
    )


def _row_to_memory(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "id": row.get("id"),
        "agent_id": row.get("agent"),
        "content": row.get("content"),
        "category": row.get("category"),
        "importance": row.get("importance"),
        "memory_type": row.get("memory_type"),
        "scope": row.get("scope"),
        "metadata": metadata,
        "created_at": row.get("created_at").isoformat() if hasattr(row.get("created_at"), "isoformat") else row.get("created_at"),
        "content_hash": row.get("content_hash_sha256"),
    }


async def create_memory(conn: Any, tenant: TenantContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Create a memory for the authenticated tenant."""
    tenant.require("write")
    data = memory_insert_payload(payload, tenant)
    await ensure_agent(conn, data["agent"])
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.memories
            (tenant_id, agent, scope, category, content, importance,
             memory_type, metadata, source, valid_from)
        VALUES
            ($1::uuid, $2, $3, $4, $5, $6, $7, $8::jsonb, $9,
             COALESCE($10::timestamptz, now()))
        RETURNING id, agent, scope, category, content, importance,
                  memory_type, metadata, created_at, content_hash_sha256
        """,
        data["tenant_id"],
        data["agent"],
        data["scope"],
        data["category"],
        data["content"],
        data["importance"],
        data["memory_type"],
        json.dumps(data["metadata"]),
        data["source"],
        data["valid_from"],
    )
    return _row_to_memory(row)


async def get_memory(conn: Any, memory_id: int) -> dict[str, Any] | None:
    """Read one memory. RLS makes other tenants indistinguishable from missing rows."""
    row = await conn.fetchrow(
        """
        SELECT id, agent, scope, category, content, importance, memory_type,
               metadata, created_at, content_hash_sha256
        FROM soul_v3.memories
        WHERE id=$1 AND invalid_at IS NULL
        """,
        memory_id,
    )
    return _row_to_memory(row) if row else None


async def list_memories(
    conn: Any,
    *,
    query: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    category: str | None = None,
    importance_gte: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List memories for the current tenant. Tenant filtering is handled by RLS."""
    reject_tenant_override(query=query)
    clauses = ["invalid_at IS NULL"]
    params: list[Any] = []
    if agent_id:
        params.append(agent_id)
        clauses.append(f"agent=${len(params)}")
    if category:
        params.append(category)
        clauses.append(f"category=${len(params)}")
    if importance_gte is not None:
        params.append(int(importance_gte))
        clauses.append(f"importance >= ${len(params)}")
    params.append(max(1, min(int(limit), 100)))
    limit_param = len(params)
    params.append(max(0, int(offset)))
    offset_param = len(params)

    rows = await conn.fetch(
        f"""
        SELECT id, agent, scope, category, content, importance, memory_type,
               metadata, created_at, content_hash_sha256
        FROM soul_v3.memories
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC, id DESC
        LIMIT ${limit_param} OFFSET ${offset_param}
        """,
        *params,
    )
    return [_row_to_memory(row) for row in rows]


async def recall_memories(
    conn: Any,
    tenant: TenantContext,
    *,
    query_text: str,
    agent_id: str | None = None,
    importance_gte: int = 1,
    limit: int = 10,
) -> dict[str, Any]:
    """Tenant-safe hybrid recall with a privacy-preserving retrieval audit."""
    tenant.require("read")
    if agent_id is not None:
        # Defense in depth for direct runtime callers. The API transaction also
        # validates this allowlist before selecting the tenant-bound DB role.
        tenant.with_agent(agent_id)
    started = time.monotonic()
    retrieval = await retrieve_memories(
        conn,
        query_text=query_text,
        agent_id=agent_id,
        importance_gte=importance_gte,
        limit=limit,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    await audit_retrieval(
        conn,
        tenant,
        endpoint="/v1/recall",
        query_text=query_text,
        memory_ids=[
            int(memory["id"])
            for memory in retrieval.memories
            if memory.get("id") is not None
        ],
        latency_ms=latency_ms,
        retrieval_mode=retrieval.mode,
    )
    return {
        "memories": retrieval.memories,
        "total_hits": len(retrieval.memories),
        "latency_ms": latency_ms,
        "retrieval_mode": retrieval.mode,
    }


async def search_memories(
    conn: Any,
    *,
    query_text: str,
    agent_id: str | None = None,
    importance_gte: int = 1,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Backward-compatible search wrapper; prefer ``retrieve_memories``."""
    result = await retrieve_memories(
        conn,
        query_text=query_text,
        agent_id=agent_id,
        importance_gte=importance_gte,
        limit=limit,
    )
    return result.memories


async def _vector_retrieval_available(conn: Any) -> bool:
    """Return false only when the required pgvector/FTS schema is absent.

    Probe errors are deliberately not swallowed: an authorization or database
    failure must not be mislabeled as a healthy lexical fallback.
    """
    available = await conn.fetchval(
        """
        SELECT to_regtype('vector') IS NOT NULL
          AND EXISTS (
                SELECT 1
                FROM pg_catalog.pg_attribute AS a
                JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'soul_v3'
                  AND c.relname = 'memories'
                  AND a.attname = 'embedding'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
          )
          AND EXISTS (
                SELECT 1
                FROM pg_catalog.pg_attribute AS a
                JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'soul_v3'
                  AND c.relname = 'memories'
                  AND a.attname = 'embedding_bm25'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
          )
        """
    )
    return bool(available)


async def _query_embedding(query_text: str) -> list[float]:
    """Use SOUL's local, CPU-only embedding implementation (no remote API)."""
    from embeddings import get_query_embedding

    return await get_query_embedding(query_text)


async def retrieve_memories(
    conn: Any,
    *,
    query_text: str,
    agent_id: str | None = None,
    importance_gte: int = 1,
    limit: int = 10,
) -> MemoryRetrievalResult:
    """Use pgvector+FTS when present; lexical fallback only if infra is absent.

    The query intentionally contains no tenant predicate. Tenant isolation is
    enforced by the already-selected ``current_user`` and RLS policy. Optional
    ``agent_id`` remains an additional, never broader filter.
    """
    bounded_limit = max(1, min(int(limit), 50))
    if not await _vector_retrieval_available(conn):
        rows = await _search_memories_lexical(
            conn,
            query_text=query_text,
            agent_id=agent_id,
            importance_gte=importance_gte,
            limit=bounded_limit,
        )
        return MemoryRetrievalResult(rows, RETRIEVAL_MODE_LEXICAL_FALLBACK)

    embedding = await _query_embedding(query_text)
    if not embedding:
        raise RuntimeError("sdk_query_embedding_empty")

    params: list[Any] = [json.dumps(embedding), query_text, int(importance_gte)]
    agent_clause = ""
    if agent_id:
        params.append(agent_id)
        agent_clause = f" AND agent = ${len(params)}"
    candidate_limit = min(200, max(bounded_limit * 4, 20))
    params.extend([candidate_limit, bounded_limit])
    candidate_param = len(params) - 1
    limit_param = len(params)

    rows = await conn.fetch(
        f"""
        WITH semantic AS (
            SELECT id,
                   row_number() OVER (ORDER BY embedding <=> $1::vector) AS rank
            FROM soul_v3.memories
            WHERE invalid_at IS NULL
              AND embedding IS NOT NULL
              AND importance >= $3
              {agent_clause}
            ORDER BY embedding <=> $1::vector
            LIMIT ${candidate_param}
        ),
        lexical AS (
            SELECT id,
                   row_number() OVER (
                       ORDER BY ts_rank_cd(
                           COALESCE(embedding_bm25, ''::tsvector) ||
                           to_tsvector(
                               'simple',
                               regexp_replace(COALESCE(content, ''), '[_./:\\-]+', ' ', 'g')
                           ),
                           websearch_to_tsquery('simple', $2)
                       ) DESC
                   ) AS rank
            FROM soul_v3.memories
            WHERE invalid_at IS NULL
              AND importance >= $3
              {agent_clause}
              AND (
                  COALESCE(embedding_bm25, ''::tsvector) ||
                  to_tsvector(
                      'simple',
                      regexp_replace(COALESCE(content, ''), '[_./:\\-]+', ' ', 'g')
                  )
              ) @@ websearch_to_tsquery('simple', $2)
            ORDER BY rank
            LIMIT ${candidate_param}
        ),
        fused AS (
            SELECT COALESCE(semantic.id, lexical.id) AS id,
                   COALESCE(1.0 / (60 + semantic.rank), 0.0) +
                   COALESCE(1.0 / (60 + lexical.rank), 0.0) AS score
            FROM semantic
            FULL OUTER JOIN lexical ON lexical.id = semantic.id
        )
        SELECT m.id, m.agent, m.scope, m.category, m.content, m.importance,
               m.memory_type, m.metadata, m.created_at,
               m.content_hash_sha256, fused.score AS retrieval_score
        FROM fused
        JOIN soul_v3.memories AS m ON m.id = fused.id
        WHERE m.invalid_at IS NULL
        ORDER BY fused.score DESC, m.importance DESC, m.created_at DESC, m.id DESC
        LIMIT ${limit_param}
        """,
        *params,
    )
    return MemoryRetrievalResult(
        [_row_to_memory(row) for row in rows],
        RETRIEVAL_MODE_HYBRID,
    )


async def _search_memories_lexical(
    conn: Any,
    *,
    query_text: str,
    agent_id: str | None = None,
    importance_gte: int = 1,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Literal lexical fallback for deployments without vector infrastructure."""
    params: list[Any] = [f"%{escape_like_pattern(query_text)}%", int(importance_gte)]
    clauses = ["invalid_at IS NULL", "content ILIKE $1 ESCAPE '\\'", "importance >= $2"]
    if agent_id:
        params.append(agent_id)
        clauses.append(f"agent=${len(params)}")
    params.append(max(1, min(int(limit), 50)))
    limit_param = len(params)
    rows = await conn.fetch(
        f"""
        SELECT id, agent, scope, category, content, importance, memory_type,
               metadata, created_at, content_hash_sha256
        FROM soul_v3.memories
        WHERE {' AND '.join(clauses)}
        ORDER BY importance DESC, created_at DESC, id DESC
        LIMIT ${limit_param}
        """,
        *params,
    )
    return [_row_to_memory(row) for row in rows]


async def audit_retrieval(
    conn: Any,
    tenant: TenantContext,
    *,
    endpoint: str,
    query_text: str | None,
    memory_ids: list[int],
    latency_ms: int,
    retrieval_mode: str,
) -> None:
    """Write non-reversible query telemetry behind the same RLS context."""
    query_length = len(query_text) if query_text is not None else 0
    query_sha256 = (
        hashlib.sha256(query_text.encode("utf-8")).hexdigest()
        if query_text is not None
        else None
    )
    query_reference = f"sha256:{query_sha256}" if query_sha256 is not None else None
    await conn.execute(
        """
        INSERT INTO soul_v3.memory_retrieval_log
            (tenant_id, agent_requesting, query_text, tool_used,
             memory_ids_returned, result_count, metadata)
        VALUES ($1::uuid, $2, $3, $4, $5::bigint[], $6, $7::jsonb)
        """,
        tenant.tenant_id,
        "sdk_api",
        query_reference,
        endpoint,
        memory_ids,
        len(memory_ids),
        json.dumps(
            {
                "api_key_hash": tenant.api_key_hash,
                "latency_ms": latency_ms,
                "query_length": query_length,
                "query_sha256": query_sha256,
                "retrieval_mode": retrieval_mode,
            },
            sort_keys=True,
        ),
    )
