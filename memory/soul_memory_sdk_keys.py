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
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

import asyncpg

from seal_secrets import pg_dsn
from soul_memory_sdk_runtime import hash_api_key, new_api_key


DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000000"
CREATE_CONFIRMATION = "CREATE_TENANT_KEY"
REVOKE_CONFIRMATION = "REVOKE_TENANT_KEY"
ALLOWED_SCOPES = {"read", "write", "tenant:read", "*"}


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


def key_record(raw_key: str, *, scopes: Iterable[str], label: str, created_by: str = "ADA") -> dict[str, Any]:
    clean_label = label.strip()
    if not clean_label:
        raise KeyProvisioningError("label_required")
    if len(clean_label) > 120:
        raise KeyProvisioningError("label_max_120_chars")
    return {
        "sha256": hash_api_key(raw_key),
        "scopes": list(normalize_scopes(scopes)),
        "label": clean_label,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": created_by.strip() or "ADA",
    }


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
        "created_at": record.get("created_at") or "",
        "revoked_at": record.get("revoked_at") or "",
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
    label: str,
    created_by: str = "ADA",
    allow_duplicate_label: bool = False,
) -> dict[str, Any]:
    if not allow_duplicate_label and await active_label_count(conn, tenant_id=tenant_id, label=label):
        raise KeyProvisioningError("active_label_already_exists")
    raw_key = new_api_key()
    record = key_record(raw_key, scopes=scopes, label=label, created_by=created_by)
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
    create.add_argument("--label", required=True)
    create.add_argument("--created-by", default="ADA")
    create.add_argument("--allow-duplicate-label", action="store_true")
    create.add_argument("--confirm", required=True)

    list_cmd = sub.add_parser("list", help="List non-secret key metadata.")
    list_cmd.set_defaults(command="list")

    revoke = sub.add_parser("revoke", help="Revoke active keys by label and/or hash prefix.")
    revoke.add_argument("--label")
    revoke.add_argument("--hash-prefix")
    revoke.add_argument("--confirm", required=True)
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
                label=args.label,
                created_by=args.created_by,
                allow_duplicate_label=args.allow_duplicate_label,
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
