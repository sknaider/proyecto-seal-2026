#!/usr/bin/env python3
"""Provision and revoke SOUL Memory SDK tenant API keys.

Raw API keys are printed once on creation and never stored. Database records
only keep SHA-256 hashes plus non-secret metadata.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping

import asyncpg

from seal_secrets import pg_dsn
from soul_memory_sdk_runtime import hash_api_key, new_api_key


DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000000"
CREATE_CONFIRMATION = "CREATE_TENANT_KEY"
REVOKE_CONFIRMATION = "REVOKE_TENANT_KEY"
ROTATE_CONFIRMATION = "ROTATE_TENANT_KEY"
ALLOWED_SCOPES = {"read", "write", "tenant:read", "*"}
AGENT_SCOPES = {"read", "write"}
MAX_AGENT_NAME_LEN = 20


class KeyProvisioningError(ValueError):
    """Raised when key provisioning input is unsafe or ambiguous."""


def normalize_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(scope.strip() for scope in scopes if scope.strip()))
    if not normalized:
        raise KeyProvisioningError("scope_required")
    unknown = set(normalized) - ALLOWED_SCOPES
    if unknown:
        raise KeyProvisioningError(f"unsupported_scopes:{','.join(sorted(unknown))}")
    if "*" in normalized and len(normalized) > 1:
        raise KeyProvisioningError("wildcard_scope_must_stand_alone")
    return normalized


def normalize_agents(agents: Iterable[str] | None, *, scopes: Iterable[str]) -> tuple[str, ...]:
    """Validate the server-side agent allowlist stored with a key.

    ``read`` and ``write`` are agent-scoped permissions and therefore fail
    closed without an explicit allowlist. ``tenant:read`` and ``*`` are
    intentionally tenant-wide and preserve compatibility with records that
    predate per-agent key metadata. The runtime does not implement an agent
    wildcard, so accepting ``*`` here would create a misleading allowlist.
    """
    normalized_scopes = normalize_scopes(scopes)
    values: list[str] = []
    seen: set[str] = set()
    for raw_agent in agents or ():
        if not isinstance(raw_agent, str):
            raise KeyProvisioningError("agent_name_must_be_string")
        agent = raw_agent.strip()
        if not agent:
            raise KeyProvisioningError("agent_name_required")
        if len(agent) > MAX_AGENT_NAME_LEN:
            raise KeyProvisioningError("agent_name_max_20_chars")
        if agent == "*":
            raise KeyProvisioningError("agent_wildcard_not_supported")
        identity = agent.casefold()
        if identity in seen:
            raise KeyProvisioningError(f"duplicate_agent:{agent}")
        seen.add(identity)
        values.append(agent)
    if AGENT_SCOPES.intersection(normalized_scopes) and not values:
        raise KeyProvisioningError("agent_allowlist_required_for_read_write")
    return tuple(values)


def normalize_expiry(
    *,
    expires_at: str | datetime | None = None,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> str | None:
    """Return a canonical UTC expiry or ``None`` for legacy non-expiring keys."""
    if expires_at is not None and ttl_seconds is not None:
        raise KeyProvisioningError("expires_at_and_ttl_are_mutually_exclusive")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise KeyProvisioningError("now_must_be_timezone_aware")
    current = current.astimezone(UTC)

    if ttl_seconds is not None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
            raise KeyProvisioningError("ttl_seconds_must_be_positive_integer")
        try:
            expiry = current + timedelta(seconds=ttl_seconds)
        except (OverflowError, ValueError) as exc:
            raise KeyProvisioningError("ttl_seconds_out_of_range") from exc
    elif expires_at is None:
        return None
    elif isinstance(expires_at, datetime):
        expiry = expires_at
    elif isinstance(expires_at, str):
        try:
            expiry = datetime.fromisoformat(expires_at.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise KeyProvisioningError("expires_at_must_be_iso8601") from exc
    else:
        raise KeyProvisioningError("expires_at_must_be_iso8601")

    if expiry.tzinfo is None:
        raise KeyProvisioningError("expires_at_timezone_required")
    expiry = expiry.astimezone(UTC)
    if expiry <= current:
        raise KeyProvisioningError("expires_at_must_be_future")
    return expiry.isoformat()


def key_record(
    raw_key: str,
    *,
    scopes: Iterable[str],
    agents: Iterable[str] | None = None,
    label: str,
    created_by: str = "ADA",
    expires_at: str | datetime | None = None,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean_label = label.strip()
    if not clean_label:
        raise KeyProvisioningError("label_required")
    if len(clean_label) > 120:
        raise KeyProvisioningError("label_max_120_chars")
    normalized_scopes = normalize_scopes(scopes)
    normalized_agents = normalize_agents(agents, scopes=normalized_scopes)
    record = {
        "sha256": hash_api_key(raw_key),
        "scopes": list(normalized_scopes),
        "agents": list(normalized_agents),
        "label": clean_label,
        "created_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "created_by": created_by.strip() or "ADA",
    }
    normalized_expiry = normalize_expiry(expires_at=expires_at, ttl_seconds=ttl_seconds, now=now)
    if normalized_expiry is not None:
        record["expires_at"] = normalized_expiry
    return record


def key_preview(record: Mapping[str, Any] | str) -> dict[str, Any]:
    if isinstance(record, str):
        try:
            parsed = json.loads(record)
        except json.JSONDecodeError:
            parsed = {}
        record = parsed if isinstance(parsed, Mapping) else {}
    digest = str(record.get("sha256") or record.get("hash") or record.get("key_hash") or "")
    return {
        "label": record.get("label") or "",
        "hash_prefix": digest[:12],
        "scopes": record.get("scopes") or record.get("scope") or [],
        # Missing ``agents`` is a valid legacy representation for tenant-wide
        # keys. Agent-scoped legacy keys are denied by the runtime.
        "agents": record.get("agents") or [],
        "created_at": record.get("created_at") or "",
        "expires_at": record.get("expires_at") or "",
        "revoked_at": record.get("revoked_at") or "",
        "rotated_to_hash_prefix": str(record.get("rotated_to_sha256") or "")[:12],
    }


async def active_label_count(conn: Any, *, tenant_id: str, label: str) -> int:
    return int(
        await conn.fetchval(
            """
            SELECT count(*)
            FROM soul_v3.tenants AS t, jsonb_array_elements(t.api_keys) AS key
            WHERE t.id=$1::uuid
              AND key->>'label'=$2
              AND COALESCE(key->>'revoked_at', '') = ''
              AND CASE
                    WHEN COALESCE(key->>'expires_at', '') = '' THEN TRUE
                    WHEN pg_input_is_valid(key->>'expires_at', 'timestamp with time zone')
                      THEN (key->>'expires_at')::timestamptz > CURRENT_TIMESTAMP
                    ELSE FALSE
                  END
            """,
            tenant_id,
            label,
        )
        or 0
    )


async def create_tenant_key(
    conn: Any,
    *,
    tenant_id: str,
    scopes: Iterable[str],
    agents: Iterable[str] | None = None,
    label: str,
    created_by: str = "ADA",
    allow_duplicate_label: bool = False,
    expires_at: str | datetime | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    clean_label = label.strip()
    if not allow_duplicate_label and await active_label_count(conn, tenant_id=tenant_id, label=clean_label):
        raise KeyProvisioningError("active_label_already_exists")
    raw_key = new_api_key()
    record = key_record(
        raw_key,
        scopes=scopes,
        agents=agents,
        label=clean_label,
        created_by=created_by,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
    )
    updated = await conn.fetchval(
        """
        UPDATE soul_v3.tenants
        SET api_keys = api_keys || $2::jsonb
        WHERE id=$1::uuid
        RETURNING jsonb_array_length(api_keys)
        """,
        tenant_id,
        json.dumps([record], sort_keys=True),
    )
    if updated is None:
        raise KeyProvisioningError("tenant_not_found")
    return {"raw_key": raw_key, "record": key_preview(record), "api_keys_len": int(updated)}


async def list_tenant_keys(conn: Any, *, tenant_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT key
        FROM soul_v3.tenants AS t, jsonb_array_elements(t.api_keys) AS key
        WHERE t.id=$1::uuid
        ORDER BY COALESCE(key->>'created_at', '') DESC, key->>'label'
        """,
        tenant_id,
    )
    return [key_preview(row["key"]) for row in rows]


async def revoke_tenant_key(conn: Any, *, tenant_id: str, label: str | None, hash_prefix: str | None) -> int:
    if not label and not hash_prefix:
        raise KeyProvisioningError("label_or_hash_prefix_required")
    if hash_prefix and len(hash_prefix) < 12:
        raise KeyProvisioningError("hash_prefix_min_12_chars")
    revoked_at = datetime.now(UTC).isoformat()
    revoked = await conn.fetchval(
        """
        WITH updated AS (
            SELECT jsonb_agg(
                CASE
                  WHEN COALESCE(key->>'revoked_at', '') = ''
                   AND ($2::text IS NULL OR key->>'label' = $2)
                   AND ($3::text IS NULL OR left(COALESCE(key->>'sha256', key->>'hash', key->>'key_hash', ''), length($3)) = $3)
                  THEN jsonb_set(key, '{revoked_at}', to_jsonb($4::text), true)
                  ELSE key
                END
            ) AS api_keys,
            count(*) FILTER (
                WHERE COALESCE(key->>'revoked_at', '') = ''
                  AND ($2::text IS NULL OR key->>'label' = $2)
                  AND ($3::text IS NULL OR left(COALESCE(key->>'sha256', key->>'hash', key->>'key_hash', ''), length($3)) = $3)
            ) AS revoked_count
            FROM soul_v3.tenants AS t, jsonb_array_elements(t.api_keys) AS key
            WHERE t.id=$1::uuid
        )
        UPDATE soul_v3.tenants
        SET api_keys = updated.api_keys
        FROM updated
        WHERE id=$1::uuid
        RETURNING updated.revoked_count
        """,
        tenant_id,
        label,
        hash_prefix,
        revoked_at,
    )
    return int(revoked or 0)


async def rotate_tenant_key(
    conn: Any,
    *,
    tenant_id: str,
    current_label: str | None,
    current_hash_prefix: str | None,
    scopes: Iterable[str],
    agents: Iterable[str] | None = None,
    new_label: str,
    created_by: str = "ADA",
    expires_at: str | datetime | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Revoke exactly one active key and append its replacement atomically."""
    if not current_label and not current_hash_prefix:
        raise KeyProvisioningError("current_label_or_hash_prefix_required")
    if current_hash_prefix and len(current_hash_prefix) < 12:
        raise KeyProvisioningError("hash_prefix_min_12_chars")

    raw_key = new_api_key()
    record = key_record(
        raw_key,
        scopes=scopes,
        agents=agents,
        label=new_label,
        created_by=created_by,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
    )
    rotated_at = datetime.now(UTC).isoformat()
    row = await conn.fetchrow(
        """
        WITH locked_tenant AS (
            SELECT id, api_keys
            FROM soul_v3.tenants
            WHERE id=$1::uuid
            FOR UPDATE
        ), candidate AS (
            SELECT
                t.id,
                count(key.ordinality) FILTER (
                    WHERE COALESCE(key.value->>'revoked_at', '') = ''
                      AND CASE
                            WHEN COALESCE(key.value->>'expires_at', '') = '' THEN TRUE
                            WHEN pg_input_is_valid(key.value->>'expires_at', 'timestamp with time zone')
                              THEN (key.value->>'expires_at')::timestamptz > CURRENT_TIMESTAMP
                            ELSE FALSE
                          END
                      AND ($2::text IS NULL OR key.value->>'label' = $2)
                      AND ($3::text IS NULL OR left(
                            COALESCE(key.value->>'sha256', key.value->>'hash', key.value->>'key_hash', ''),
                            length($3)
                          ) = $3)
                ) AS match_count,
                COALESCE(
                    jsonb_agg(
                        CASE
                          WHEN COALESCE(key.value->>'revoked_at', '') = ''
                           AND CASE
                                 WHEN COALESCE(key.value->>'expires_at', '') = '' THEN TRUE
                                 WHEN pg_input_is_valid(key.value->>'expires_at', 'timestamp with time zone')
                                   THEN (key.value->>'expires_at')::timestamptz > CURRENT_TIMESTAMP
                                 ELSE FALSE
                               END
                           AND ($2::text IS NULL OR key.value->>'label' = $2)
                           AND ($3::text IS NULL OR left(
                                 COALESCE(key.value->>'sha256', key.value->>'hash', key.value->>'key_hash', ''),
                                 length($3)
                               ) = $3)
                          THEN jsonb_set(
                                 jsonb_set(key.value, '{revoked_at}', to_jsonb($4::text), true),
                                 '{rotated_to_sha256}', to_jsonb($5::text), true
                               )
                          ELSE key.value
                        END
                        ORDER BY key.ordinality
                    ) FILTER (WHERE key.ordinality IS NOT NULL),
                    '[]'::jsonb
                ) AS rotated_keys
            FROM locked_tenant AS t
            LEFT JOIN LATERAL jsonb_array_elements(t.api_keys) WITH ORDINALITY AS key(value, ordinality)
              ON TRUE
            WHERE t.id=$1::uuid
            GROUP BY t.id
        ), updated AS (
            UPDATE soul_v3.tenants AS t
            SET api_keys = candidate.rotated_keys || $6::jsonb
            FROM candidate
            WHERE t.id=candidate.id
              AND candidate.match_count=1
            RETURNING jsonb_array_length(t.api_keys) AS api_keys_len
        )
        SELECT
            COALESCE((SELECT match_count FROM candidate), -1)::int AS match_count,
            (SELECT api_keys_len FROM updated) AS api_keys_len
        """,
        tenant_id,
        current_label,
        current_hash_prefix,
        rotated_at,
        record["sha256"],
        json.dumps([record], sort_keys=True),
    )
    if not row:
        raise KeyProvisioningError("rotation_failed")
    match_count = int(row["match_count"])
    if match_count == -1:
        raise KeyProvisioningError("tenant_not_found")
    if match_count == 0:
        raise KeyProvisioningError("rotation_source_not_found")
    if match_count != 1:
        raise KeyProvisioningError("rotation_source_ambiguous")
    if row["api_keys_len"] is None:
        raise KeyProvisioningError("rotation_failed")
    return {
        "raw_key": raw_key,
        "record": key_preview(record),
        "api_keys_len": int(row["api_keys_len"]),
        "rotated": 1,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage SOUL Memory SDK tenant API keys.")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("SOUL_MEMORY_ADMIN_DSN") or pg_dsn(required=True),
        help="Privileged control-plane DSN (or SOUL_MEMORY_ADMIN_DSN); never used by the SDK daemon.",
    )
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a new tenant API key and print raw key once.")
    create.add_argument("--scope", action="append", required=True)
    create.add_argument(
        "--agent",
        action="append",
        default=[],
        help="Allowed agent name; repeat for multiple agents (required for read/write scopes).",
    )
    create.add_argument("--label", required=True)
    create.add_argument("--created-by", default="ADA")
    create.add_argument("--allow-duplicate-label", action="store_true")
    create_expiry = create.add_mutually_exclusive_group()
    create_expiry.add_argument("--expires-at")
    create_expiry.add_argument("--ttl-seconds", type=int)
    create.add_argument("--confirm", required=True)

    list_cmd = sub.add_parser("list", help="List non-secret key metadata.")
    list_cmd.set_defaults(command="list")

    revoke = sub.add_parser("revoke", help="Revoke active keys by label and/or hash prefix.")
    revoke.add_argument("--label")
    revoke.add_argument("--hash-prefix")
    revoke.add_argument("--confirm", required=True)

    rotate = sub.add_parser("rotate", help="Atomically replace exactly one active tenant key.")
    rotate.add_argument("--current-label")
    rotate.add_argument("--current-hash-prefix")
    rotate.add_argument("--scope", action="append", required=True)
    rotate.add_argument(
        "--agent",
        action="append",
        default=[],
        help="Allowed agent name; repeat for multiple agents (required for read/write scopes).",
    )
    rotate.add_argument("--new-label", required=True)
    rotate.add_argument("--created-by", default="ADA")
    rotate_expiry = rotate.add_mutually_exclusive_group()
    rotate_expiry.add_argument("--expires-at")
    rotate_expiry.add_argument("--ttl-seconds", type=int)
    rotate.add_argument("--confirm", required=True)
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    conn = await asyncpg.connect(args.dsn)
    try:
        if args.command == "create":
            if args.confirm != CREATE_CONFIRMATION:
                raise KeyProvisioningError(f"confirm_required:{CREATE_CONFIRMATION}")
            return await create_tenant_key(
                conn,
                tenant_id=args.tenant_id,
                scopes=args.scope,
                agents=args.agent,
                label=args.label,
                created_by=args.created_by,
                allow_duplicate_label=args.allow_duplicate_label,
                expires_at=args.expires_at,
                ttl_seconds=args.ttl_seconds,
            )
        if args.command == "list":
            return {"keys": await list_tenant_keys(conn, tenant_id=args.tenant_id)}
        if args.command == "revoke":
            if args.confirm != REVOKE_CONFIRMATION:
                raise KeyProvisioningError(f"confirm_required:{REVOKE_CONFIRMATION}")
            return {
                "revoked": await revoke_tenant_key(
                    conn,
                    tenant_id=args.tenant_id,
                    label=args.label,
                    hash_prefix=args.hash_prefix,
                )
            }
        if args.command == "rotate":
            if args.confirm != ROTATE_CONFIRMATION:
                raise KeyProvisioningError(f"confirm_required:{ROTATE_CONFIRMATION}")
            return await rotate_tenant_key(
                conn,
                tenant_id=args.tenant_id,
                current_label=args.current_label,
                current_hash_prefix=args.current_hash_prefix,
                scopes=args.scope,
                agents=args.agent,
                new_label=args.new_label,
                created_by=args.created_by,
                expires_at=args.expires_at,
                ttl_seconds=args.ttl_seconds,
            )
        raise KeyProvisioningError("unknown_command")
    finally:
        await conn.close()


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(async_main(args))
    except KeyProvisioningError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
