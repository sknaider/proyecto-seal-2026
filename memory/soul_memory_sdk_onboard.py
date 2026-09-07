#!/usr/bin/env python3
"""Fail-closed onboarding for an external SOUL Memory SDK tenant.

Dry-run is offline and never generates a key. Apply authenticates with one of
the two dedicated PostgreSQL operator logins, creates the tenant, subscription,
canonical roles and first expiring key in one SERIALIZABLE transaction, and
writes the raw key only to a newly-created owner-only file.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import unquote, urlsplit
from uuid import UUID

import asyncpg

from operational_db_credentials import service_pg_dsn
from soul_memory_sdk_keys import (
    KeyProvisioningError,
    key_preview,
    key_record,
    normalize_agents,
    normalize_expiry,
    normalize_scopes,
)
from soul_memory_sdk_runtime import new_api_key


OPERATOR_LOGIN_TO_PERSON = {
    "svc_soul_sdk_onboard_william": "William",
    "svc_soul_sdk_onboard_henry": "Henry",
}
ALLOWED_EXTERNAL_PLAN_IDS = frozenset({"free", "pro", "enterprise"})
INTERNAL_TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")
SDK_LOGIN_ROLE = "svc_soul_memory_sdk"
ONBOARDING_GROUP_ROLE = "soul_sdk_onboarding_operator"
MAX_TENANT_NAME_LEN = 120
MAX_SERIALIZABLE_ATTEMPTS = 3
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})


class TenantOnboardingError(ValueError):
    """External tenant onboarding could not proceed safely."""


class AmbiguousCommitOutcome(RuntimeError):
    """The client could not prove whether COMMIT reached PostgreSQL."""


class IndeterminateOnboardingError(TenantOnboardingError):
    """State may be committed; preserve the pending raw-key file for recovery."""


@dataclass(frozen=True)
class OnboardingRequest:
    tenant_id: UUID
    tenant_name: str
    scopes: tuple[str, ...]
    agents: tuple[str, ...]
    key_label: str
    plan_id: str
    expires_at: str | None
    ttl_seconds: int | None


def _normalized_tenant_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise TenantOnboardingError("tenant_name_required")
    if len(name) > MAX_TENANT_NAME_LEN:
        raise TenantOnboardingError("tenant_name_max_120_chars")
    if any(ord(char) < 32 for char in name):
        raise TenantOnboardingError("tenant_name_control_chars_forbidden")
    return name


def _normalized_label(value: str) -> str:
    label = value.strip()
    if not label:
        raise TenantOnboardingError("key_label_required")
    if len(label) > 120:
        raise TenantOnboardingError("key_label_max_120_chars")
    return label


def build_request(
    *,
    tenant_id: str | UUID,
    tenant_name: str,
    scopes: Iterable[str],
    agents: Iterable[str] | None,
    key_label: str,
    plan_id: str,
    expires_at: str | None,
    ttl_seconds: int | None,
) -> OnboardingRequest:
    try:
        parsed_tenant_id = tenant_id if isinstance(tenant_id, UUID) else UUID(str(tenant_id))
    except (TypeError, ValueError) as exc:
        raise TenantOnboardingError("tenant_id_must_be_uuid") from exc
    if parsed_tenant_id == INTERNAL_TENANT_ID:
        raise TenantOnboardingError("external_tenant_cannot_use_internal_id")
    normalized_plan_id = plan_id.strip()
    if normalized_plan_id not in ALLOWED_EXTERNAL_PLAN_IDS:
        raise TenantOnboardingError("plan_must_be_free_pro_or_enterprise")
    if expires_at is None and ttl_seconds is None:
        raise TenantOnboardingError("external_key_expiry_required")

    normalized_scopes = normalize_scopes(scopes)
    normalized_agents = normalize_agents(agents, scopes=normalized_scopes)
    normalize_expiry(expires_at=expires_at, ttl_seconds=ttl_seconds)
    return OnboardingRequest(
        tenant_id=parsed_tenant_id,
        tenant_name=_normalized_tenant_name(tenant_name),
        scopes=normalized_scopes,
        agents=normalized_agents,
        key_label=_normalized_label(key_label),
        plan_id=normalized_plan_id,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
    )


def _confirmation_payload(request: OnboardingRequest) -> dict[str, Any]:
    return {
        "agents": list(request.agents),
        "expires_at": request.expires_at,
        "key_label": request.key_label,
        "plan_id": request.plan_id,
        "scopes": list(request.scopes),
        "tenant_id": str(request.tenant_id),
        "tenant_name": request.tenant_name,
        "ttl_seconds": request.ttl_seconds,
    }


def expected_confirmation(request: OnboardingRequest) -> str:
    """Safety token binding reviewed inputs; it is not authentication."""
    canonical = json.dumps(
        _confirmation_payload(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:16]
    return f"ONBOARD_SDK_TENANT:{request.tenant_id}:{digest}"


def dry_run_plan(request: OnboardingRequest) -> dict[str, Any]:
    """Return a non-secret offline plan; no DB access or key generation."""
    return {
        "mode": "dry-run",
        "operation": "onboard_external_sdk_tenant",
        "transaction": "serializable_all_or_nothing",
        "tenant": {
            "id": str(request.tenant_id),
            "name": request.tenant_name,
            "is_internal": False,
            "quotas": {},
        },
        "roles": [
            f"soul_sdk_t_{request.tenant_id.hex}_agent",
            f"soul_sdk_t_{request.tenant_id.hex}_user",
        ],
        "sdk_login_role": SDK_LOGIN_ROLE,
        "subscription": {
            "plan_id": request.plan_id,
            "status": "active",
            "database_validation": "required_during_apply",
        },
        "first_key": {
            "label": request.key_label,
            "scopes": list(request.scopes),
            "agents": list(request.agents),
            "expires_at": request.expires_at or "computed_at_apply_from_ttl",
            "ttl_seconds": request.ttl_seconds,
            "raw_key": "generated_once_only_during_apply",
            "database_storage": "sha256_and_metadata_only",
            "delivery": "mandatory_new_0600_output_file",
        },
        "operator_authentication": {
            "source": "postgresql_session_user",
            "allowed_logins": sorted(OPERATOR_LOGIN_TO_PERSON),
            "required_membership": ONBOARDING_GROUP_ROLE,
        },
        "safety_confirmation_token": expected_confirmation(request),
        "confirmation_is_authentication": False,
    }


def _person_for_session_user(session_user: str) -> str:
    person = OPERATOR_LOGIN_TO_PERSON.get(session_user)
    if person is None:
        raise TenantOnboardingError("operator_session_user_not_William_or_Henry")
    return person


def _assert_dsn_operator_login(dsn: str) -> str:
    login = unquote(urlsplit(dsn).username or "")
    _person_for_session_user(login)
    return login


async def _assert_operator_db_boundary(conn: Any) -> tuple[str, str]:
    """Verify exact identity, membership, narrow column ACLs and wrapper use."""
    row = await conn.fetchrow(
        """
        SELECT
          session_user::text AS session_user,
          current_user = session_user AS current_matches_session,
          role.rolcanlogin,
          NOT (role.rolsuper OR role.rolbypassrls OR role.rolcreaterole
               OR role.rolcreatedb OR role.rolreplication) AS role_restricted,
          EXISTS (
            SELECT 1
            FROM pg_catalog.pg_auth_members AS membership
            JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
            JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
            WHERE parent.rolname = 'soul_sdk_onboarding_operator'
              AND member.rolname = session_user
              AND membership.inherit_option IS TRUE
          ) AS onboarding_member_inheriting,
          has_schema_privilege(session_user, 'soul_v3', 'USAGE') AS schema_usage,
          has_column_privilege(session_user, 'soul_v3.tenants', 'id', 'SELECT')
            AND has_column_privilege(session_user, 'soul_v3.tenants', 'name', 'SELECT')
            AND has_column_privilege(session_user, 'soul_v3.tenants', 'is_internal', 'SELECT')
            AND has_column_privilege(session_user, 'soul_v3.tenants', 'api_keys', 'SELECT')
            AND has_column_privilege(session_user, 'soul_v3.tenants', 'quotas', 'SELECT')
            AS tenant_select_columns,
          has_column_privilege(session_user, 'soul_v3.tenants', 'id', 'INSERT')
            AND has_column_privilege(session_user, 'soul_v3.tenants', 'name', 'INSERT')
            AND has_column_privilege(session_user, 'soul_v3.tenants', 'is_internal', 'INSERT')
            AND has_column_privilege(session_user, 'soul_v3.tenants', 'api_keys', 'INSERT')
            AND has_column_privilege(session_user, 'soul_v3.tenants', 'quotas', 'INSERT')
            AS tenant_insert_columns,
          has_column_privilege(session_user, 'soul_v3.tenants', 'api_keys', 'UPDATE')
            AS tenant_update_api_keys,
          NOT has_table_privilege(
            session_user, 'soul_v3.tenants', 'DELETE,TRUNCATE,REFERENCES,TRIGGER'
          ) AS tenant_dangerous_denied,
          NOT has_table_privilege(session_user, 'soul_v3.sdk_usage_plans', 'SELECT,UPDATE')
            AS plans_direct_denied,
          has_function_privilege(
            session_user, 'soul_v3.sdk_lock_external_plan(text)', 'EXECUTE'
          ) AS plan_lock_execute,
          has_table_privilege(session_user, 'soul_v3.sdk_tenant_subscriptions', 'SELECT')
            AS subscriptions_read,
          has_column_privilege(
            session_user, 'soul_v3.sdk_tenant_subscriptions', 'tenant_id', 'INSERT'
          ) AND has_column_privilege(
            session_user, 'soul_v3.sdk_tenant_subscriptions', 'plan_id', 'INSERT'
          ) AND has_column_privilege(
            session_user, 'soul_v3.sdk_tenant_subscriptions', 'status', 'INSERT'
          ) AS subscription_insert_columns,
          NOT has_column_privilege(
            session_user, 'soul_v3.sdk_tenant_subscriptions', 'valid_from', 'INSERT'
          ) AND NOT has_column_privilege(
            session_user, 'soul_v3.sdk_tenant_subscriptions', 'valid_until', 'INSERT'
          ) AND NOT has_column_privilege(
            session_user, 'soul_v3.sdk_tenant_subscriptions', 'updated_at', 'INSERT'
          ) AS subscription_other_insert_denied,
          NOT has_table_privilege(
            session_user, 'soul_v3.sdk_tenant_subscriptions',
            'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
          ) AS subscription_dangerous_denied,
          has_function_privilege(
            session_user,
            'soul_v3.sdk_provision_external_tenant_roles(uuid)',
            'EXECUTE'
          ) AS wrapper_execute,
          NOT has_function_privilege(
            session_user,
            'soul_v3.provision_sdk_tenant_roles(uuid,name)',
            'EXECUTE'
          ) AS direct_provision_denied
        FROM pg_catalog.pg_roles AS role
        WHERE role.rolname = session_user
        """
    )
    required = (
        "current_matches_session",
        "rolcanlogin",
        "role_restricted",
        "onboarding_member_inheriting",
        "schema_usage",
        "tenant_select_columns",
        "tenant_insert_columns",
        "tenant_update_api_keys",
        "tenant_dangerous_denied",
        "plans_direct_denied",
        "plan_lock_execute",
        "subscriptions_read",
        "subscription_insert_columns",
        "subscription_other_insert_denied",
        "subscription_dangerous_denied",
        "wrapper_execute",
        "direct_provision_denied",
    )
    if not row or not all(bool(row[field]) for field in required):
        raise TenantOnboardingError("operator_db_boundary_not_authorized")
    session_user = str(row["session_user"])
    return session_user, _person_for_session_user(session_user)


def _expected_roles(request: OnboardingRequest) -> dict[str, str]:
    return {
        "agent": f"soul_sdk_t_{request.tenant_id.hex}_agent",
        "user": f"soul_sdk_t_{request.tenant_id.hex}_user",
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TenantOnboardingError("database_json_object_expected")
    return dict(value)


def build_expected_key_record(
    request: OnboardingRequest,
    raw_key: str,
    operator: str,
) -> dict[str, Any]:
    """Build key metadata once so retries and recovery compare one exact record."""
    return key_record(
        raw_key,
        scopes=request.scopes,
        agents=request.agents,
        label=request.key_label,
        created_by=operator,
        expires_at=request.expires_at,
        ttl_seconds=request.ttl_seconds,
    )


def _record_matches_request(
    record: dict[str, Any],
    request: OnboardingRequest,
    raw_key: str,
    operator: str,
) -> bool:
    return (
        record.get("sha256") == hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        and record.get("scopes") == list(request.scopes)
        and record.get("agents") == list(request.agents)
        and record.get("label") == request.key_label
        and record.get("created_by") == operator
        and isinstance(record.get("expires_at"), str)
        and bool(record.get("expires_at"))
    )


async def _onboarding_body(
    conn: Any,
    request: OnboardingRequest,
    raw_key: str,
    expected_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await conn.execute("SET LOCAL lock_timeout = '5s'")
    await conn.execute("SET LOCAL statement_timeout = '30s'")
    db_identity, operator = await _assert_operator_db_boundary(conn)

    await conn.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1::text, 0))",
        "sdk-tenant-onboard:" + request.tenant_name.casefold(),
    )
    duplicate = await conn.fetchval(
        """
        SELECT count(*) FROM soul_v3.tenants
        WHERE id=$1::uuid OR lower(name)=lower($2::text)
        """,
        request.tenant_id,
        request.tenant_name,
    )
    if int(duplicate or 0) != 0:
        raise TenantOnboardingError("tenant_id_or_name_already_exists")

    plan = await conn.fetchrow(
        """
        SELECT plan_id, window_seconds, max_requests
        FROM soul_v3.sdk_lock_external_plan($1::text)
        """,
        request.plan_id,
    )
    if not plan or str(plan["plan_id"]) != request.plan_id:
        raise TenantOnboardingError("active_sdk_usage_plan_not_found")

    await conn.execute(
        """
        INSERT INTO soul_v3.tenants (id, name, is_internal, api_keys, quotas)
        VALUES ($1::uuid, $2::text, false, '[]'::jsonb, '{}'::jsonb)
        """,
        request.tenant_id,
        request.tenant_name,
    )
    rows = await conn.fetch(
        """
        SELECT resolved_viewer, resolved_db_role::text AS resolved_db_role
        FROM soul_v3.sdk_provision_external_tenant_roles($1::uuid)
        ORDER BY resolved_viewer
        """,
        request.tenant_id,
    )
    expected_roles = _expected_roles(request)
    actual_roles = {str(row["resolved_viewer"]): str(row["resolved_db_role"]) for row in rows}
    if actual_roles != expected_roles:
        raise TenantOnboardingError("canonical_tenant_roles_not_provisioned")

    subscription = await conn.fetchrow(
        """
        INSERT INTO soul_v3.sdk_tenant_subscriptions (tenant_id, plan_id, status)
        VALUES ($1::uuid, $2::text, 'active')
        RETURNING plan_id, status
        """,
        request.tenant_id,
        request.plan_id,
    )
    if (
        not subscription
        or str(subscription["plan_id"]) != request.plan_id
        or str(subscription["status"]) != "active"
    ):
        raise TenantOnboardingError("tenant_subscription_persistence_failed")

    record = dict(expected_record or build_expected_key_record(request, raw_key, operator))
    if not _record_matches_request(record, request, raw_key, operator):
        raise TenantOnboardingError("first_key_metadata_mismatch")
    serialized_record = json.dumps([record], sort_keys=True, separators=(",", ":"))
    if raw_key in serialized_record:
        raise TenantOnboardingError("raw_key_serialization_violation")
    stored = await conn.fetchrow(
        """
        UPDATE soul_v3.tenants
        SET api_keys = api_keys || $2::jsonb
        WHERE id=$1::uuid AND jsonb_array_length(api_keys)=0
        RETURNING jsonb_array_length(api_keys) AS key_count,
                  api_keys->0->>'sha256' AS stored_sha256
        """,
        request.tenant_id,
        serialized_record,
    )
    if (
        not stored
        or int(stored["key_count"]) != 1
        or str(stored["stored_sha256"]) != str(record["sha256"])
    ):
        raise TenantOnboardingError("first_key_persistence_failed")

    return {
        "mode": "applied",
        "tenant": {"id": str(request.tenant_id), "name": request.tenant_name},
        "roles": actual_roles,
        "subscription": {"plan_id": request.plan_id, "status": "active"},
        "first_key": {"raw_key": raw_key, "metadata": key_preview(record)},
        "db_operator_identity": db_identity,
        "operator": operator,
    }


async def onboard_external_tenant(
    conn: Any,
    request: OnboardingRequest,
    *,
    raw_key: str | None = None,
    expected_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one explicit transaction and surface unknown COMMIT outcomes."""
    one_raw_key = raw_key or new_api_key()
    transaction = conn.transaction(isolation="serializable")
    await transaction.start()
    try:
        result = await _onboarding_body(conn, request, one_raw_key, expected_record)
    except BaseException:
        try:
            await transaction.rollback()
        except BaseException:
            pass
        raise
    try:
        await transaction.commit()
    except BaseException as exc:
        raise AmbiguousCommitOutcome("postgres_commit_outcome_unknown") from exc
    return result


async def _inspect_outcome(
    conn: Any,
    request: OnboardingRequest,
    raw_key: str,
    expected_record: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    """Inspect tenant UUID after an ambiguous COMMIT: committed/absent/divergent."""
    db_identity, operator = await _assert_operator_db_boundary(conn)
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    expected_roles = _expected_roles(request)
    row = await conn.fetchrow(
        """
        SELECT tenant.name, tenant.is_internal, tenant.quotas,
          jsonb_array_length(tenant.api_keys) AS total_keys,
          (SELECT count(*) FROM jsonb_array_elements(tenant.api_keys) AS item
           WHERE item->>'sha256'=$2::text) AS matching_keys,
          (SELECT item FROM jsonb_array_elements(tenant.api_keys) AS item
           WHERE item->>'sha256'=$2::text LIMIT 1) AS matching_key_record,
          (SELECT count(*) FROM soul_v3.sdk_tenant_subscriptions AS subscription
           WHERE subscription.tenant_id=tenant.id
             AND subscription.plan_id=$3::text AND subscription.status='active')
             AS matching_subscriptions,
          pg_catalog.to_regrole($4::text)::text AS agent_role,
          pg_catalog.to_regrole($5::text)::text AS user_role,
          EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles AS role
            WHERE role.rolname=$4::text AND NOT role.rolcanlogin
              AND NOT role.rolsuper AND NOT role.rolcreatedb
              AND NOT role.rolcreaterole AND NOT role.rolreplication
              AND NOT role.rolbypassrls AND role.rolinherit
          ) AS agent_role_attrs_ok,
          EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles AS role
            WHERE role.rolname=$5::text AND NOT role.rolcanlogin
              AND NOT role.rolsuper AND NOT role.rolcreatedb
              AND NOT role.rolcreaterole AND NOT role.rolreplication
              AND NOT role.rolbypassrls AND role.rolinherit
          ) AS user_role_attrs_ok,
          (SELECT array_agg(parent.rolname ORDER BY parent.rolname)
           FROM pg_catalog.pg_auth_members AS membership
           JOIN pg_catalog.pg_roles AS parent ON parent.oid=membership.roleid
           JOIN pg_catalog.pg_roles AS member ON member.oid=membership.member
           WHERE member.rolname=$4::text
             AND membership.inherit_option IS TRUE
             AND membership.set_option IS TRUE) AS agent_parents,
          (SELECT array_agg(parent.rolname ORDER BY parent.rolname)
           FROM pg_catalog.pg_auth_members AS membership
           JOIN pg_catalog.pg_roles AS parent ON parent.oid=membership.roleid
           JOIN pg_catalog.pg_roles AS member ON member.oid=membership.member
           WHERE member.rolname=$5::text
             AND membership.inherit_option IS TRUE
             AND membership.set_option IS TRUE) AS user_parents,
          (SELECT array_agg(member.rolname ORDER BY member.rolname)
           FROM pg_catalog.pg_auth_members AS membership
           JOIN pg_catalog.pg_roles AS parent ON parent.oid=membership.roleid
           JOIN pg_catalog.pg_roles AS member ON member.oid=membership.member
           WHERE parent.rolname=$4::text) AS agent_members,
          (SELECT array_agg(member.rolname ORDER BY member.rolname)
           FROM pg_catalog.pg_auth_members AS membership
           JOIN pg_catalog.pg_roles AS parent ON parent.oid=membership.roleid
           JOIN pg_catalog.pg_roles AS member ON member.oid=membership.member
           WHERE parent.rolname=$5::text) AS user_members,
          EXISTS (
            SELECT 1 FROM pg_catalog.pg_auth_members AS membership
            JOIN pg_catalog.pg_roles AS parent ON parent.oid=membership.roleid
            JOIN pg_catalog.pg_roles AS member ON member.oid=membership.member
            WHERE parent.rolname=$4::text AND member.rolname='svc_soul_memory_sdk'
              AND membership.inherit_option IS FALSE
              AND membership.set_option IS TRUE
          ) AS sdk_agent_membership_ok,
          EXISTS (
            SELECT 1 FROM pg_catalog.pg_auth_members AS membership
            JOIN pg_catalog.pg_roles AS parent ON parent.oid=membership.roleid
            JOIN pg_catalog.pg_roles AS member ON member.oid=membership.member
            WHERE parent.rolname=$5::text AND member.rolname='svc_soul_memory_sdk'
              AND membership.inherit_option IS FALSE
              AND membership.set_option IS TRUE
          ) AS sdk_user_membership_ok
        FROM soul_v3.tenants AS tenant
        WHERE tenant.id=$1::uuid
        """,
        request.tenant_id,
        digest,
        request.plan_id,
        expected_roles["agent"],
        expected_roles["user"],
    )
    if row is None:
        return "absent", None
    exact = (
        str(row["name"]) == request.tenant_name
        and row["is_internal"] is False
        and _json_object(row["quotas"]) == {}
        and int(row["total_keys"]) == 1
        and int(row["matching_keys"]) == 1
        and _json_object(row["matching_key_record"]) == expected_record
        and int(row["matching_subscriptions"]) == 1
        and str(row["agent_role"]) == expected_roles["agent"]
        and str(row["user_role"]) == expected_roles["user"]
        and bool(row["agent_role_attrs_ok"])
        and bool(row["user_role_attrs_ok"])
        and list(row["agent_parents"] or [])
        == ["soul_sdk_agent_api", "soul_sdk_tenant_bound"]
        and list(row["user_parents"] or [])
        == ["soul_sdk_tenant_api", "soul_sdk_tenant_bound"]
        and list(row["agent_members"] or [])
        == ["soul_sdk_onboarding_owner", "svc_soul_memory_sdk"]
        and list(row["user_members"] or [])
        == ["soul_sdk_onboarding_owner", "svc_soul_memory_sdk"]
        and bool(row["sdk_agent_membership_ok"])
        and bool(row["sdk_user_membership_ok"])
    )
    if not exact:
        return "divergent", None
    record = _json_object(row["matching_key_record"])
    return "committed", {
        "mode": "applied_recovered_after_commit_inspection",
        "tenant": {"id": str(request.tenant_id), "name": request.tenant_name},
        "roles": expected_roles,
        "subscription": {"plan_id": request.plan_id, "status": "active"},
        "first_key": {"raw_key": raw_key, "metadata": key_preview(record)},
        "db_operator_identity": db_identity,
        "operator": operator,
    }


def _sqlstate(exc: BaseException) -> str | None:
    value = getattr(exc, "sqlstate", None)
    return str(value) if value else None


async def _connect(dsn: str) -> Any:
    return await asyncpg.connect(dsn)


async def apply_with_retry(
    dsn: str,
    request: OnboardingRequest,
    raw_key: str,
    expected_record: dict[str, Any],
    *,
    connect: Callable[[str], Awaitable[Any]] | None = None,
    sleep: Callable[[float], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Retry only 40001/40P01 and reuse exactly one generated raw key."""
    connect = connect or _connect
    sleep = sleep or asyncio.sleep
    last_retryable: BaseException | None = None
    for attempt in range(1, MAX_SERIALIZABLE_ATTEMPTS + 1):
        conn = await connect(dsn)
        try:
            return await onboard_external_tenant(
                conn, request, raw_key=raw_key, expected_record=expected_record
            )
        except AmbiguousCommitOutcome:
            await conn.close()
            inspector = await connect(dsn)
            try:
                outcome, recovered = await _inspect_outcome(
                    inspector, request, raw_key, expected_record
                )
            except BaseException as exc:
                raise IndeterminateOnboardingError("commit_outcome_unresolved_no_retry") from exc
            finally:
                await inspector.close()
            if outcome == "committed" and recovered is not None:
                return recovered
            if outcome == "divergent":
                raise IndeterminateOnboardingError("commit_outcome_divergent_no_retry")
            if attempt == MAX_SERIALIZABLE_ATTEMPTS:
                raise TenantOnboardingError("commit_absent_retry_limit_exhausted")
            await sleep(0.05 * attempt)
            continue
        except BaseException as exc:
            if _sqlstate(exc) not in RETRYABLE_SQLSTATES:
                raise
            last_retryable = exc
            if attempt == MAX_SERIALIZABLE_ATTEMPTS:
                break
            await sleep(0.05 * attempt)
        finally:
            if not getattr(conn, "is_closed", lambda: False)():
                await conn.close()
    raise TenantOnboardingError("serializable_retry_limit_exhausted") from last_retryable


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def reserve_output_file(path_value: str) -> tuple[Path, int]:
    """Atomically reserve a new non-symlink owner-only output file."""
    path = Path(path_value).expanduser()
    if not path.name or not path.parent.is_dir():
        raise TenantOnboardingError("output_file_parent_must_exist")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise TenantOnboardingError("output_file_must_be_new_non_symlink") from exc
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        raise TenantOnboardingError("output_file_must_be_owned_regular_0600")
    _fsync_directory(path.parent)
    return path, fd


def _write_fd_payload(fd: int, payload: dict[str, Any], *, close: bool) -> None:
    data = (json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    offset = 0
    while offset < len(data):
        offset += os.write(fd, data[offset:])
    os.fsync(fd)
    if close:
        os.close(fd)


def write_pending_raw_key_file(
    fd: int,
    request: OnboardingRequest,
    raw_key: str,
    expected_record: dict[str, Any],
) -> None:
    """Durably escrow the one generated key before a DB COMMIT can succeed."""
    _write_fd_payload(
        fd,
        {
            "tenant_id": str(request.tenant_id),
            "tenant_name": request.tenant_name,
            "plan_id": request.plan_id,
            "raw_api_key": raw_key,
            "expected_key_record": expected_record,
            "state": "pending_database_outcome",
        },
        close=False,
    )


def finalize_raw_key_file(path: Path, pending_fd: int, result: dict[str, Any]) -> None:
    """Atomically replace a durable pending escrow with committed metadata."""
    secret_payload = {
        "tenant_id": result["tenant"]["id"],
        "raw_api_key": result["first_key"]["raw_key"],
        "key_metadata": result["first_key"]["metadata"],
        "state": "committed",
    }
    pending_metadata = os.fstat(pending_fd)
    temp_path = path.with_name(
        f".{path.name}.committed.{secrets.token_hex(16)}.tmp"
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    temp_fd: int | None = None
    try:
        temp_fd = os.open(temp_path, flags, 0o600)
        temp_metadata = os.fstat(temp_fd)
        if (
            not stat.S_ISREG(temp_metadata.st_mode)
            or temp_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(temp_metadata.st_mode) != 0o600
        ):
            raise TenantOnboardingError("committed_temp_must_be_owned_regular_0600")
        _write_fd_payload(temp_fd, secret_payload, close=False)
        os.close(temp_fd)
        temp_fd = None

        current = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != pending_metadata.st_dev
            or current.st_ino != pending_metadata.st_ino
        ):
            raise TenantOnboardingError("pending_output_identity_changed")
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
        os.close(pending_fd)
    except BaseException:
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _remove_reserved_output(path: Path, fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except FileNotFoundError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atomically onboard an external SOUL Memory SDK tenant.")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--scope", action="append", required=True)
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--key-label", required=True)
    parser.add_argument("--plan", choices=sorted(ALLOWED_EXTERNAL_PLAN_IDS), required=True)
    expiry = parser.add_mutually_exclusive_group(required=True)
    expiry.add_argument("--expires-at")
    expiry.add_argument("--ttl-seconds", type=int)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", help="Safety token from dry-run; not authentication.")
    parser.add_argument("--output-file", help="Mandatory new 0600 file for the one-time raw key.")
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    request = build_request(
        tenant_id=args.tenant_id,
        tenant_name=args.tenant_name,
        scopes=args.scope,
        agents=args.agent,
        key_label=args.key_label,
        plan_id=args.plan,
        expires_at=args.expires_at,
        ttl_seconds=args.ttl_seconds,
    )
    if args.dry_run:
        return dry_run_plan(request)

    required_confirmation = expected_confirmation(request)
    if args.confirm != required_confirmation:
        raise TenantOnboardingError(f"safety_confirm_required:{required_confirmation}")
    if not args.output_file:
        raise TenantOnboardingError("apply_requires_output_file")

    dsn = service_pg_dsn("SOUL_MEMORY_ONBOARDING_DSN")
    _assert_dsn_operator_login(dsn)

    # Preflight exact DB identity and ACLs before reserving output or generating
    # secret material. The same boundary is rechecked inside every transaction.
    preflight = await _connect(dsn)
    try:
        _db_identity, operator = await _assert_operator_db_boundary(preflight)
    finally:
        await preflight.close()

    output_path, output_fd = reserve_output_file(args.output_file)
    raw_key = new_api_key()  # exactly once; the same key is reused across retries
    expected_record = build_expected_key_record(request, raw_key, operator)
    try:
        # Escrow before PostgreSQL can commit. If the process dies after COMMIT,
        # the raw key remains recoverable with an explicit pending-state marker.
        write_pending_raw_key_file(output_fd, request, raw_key, expected_record)
        result = await apply_with_retry(dsn, request, raw_key, expected_record)
        try:
            finalize_raw_key_file(output_path, output_fd, result)
            output_fd = None
        except BaseException as exc:
            try:
                os.close(output_fd)
            except OSError:
                pass
            output_fd = None
            raise IndeterminateOnboardingError(
                "database_committed_raw_key_file_left_pending"
            ) from exc
        _fsync_directory(output_path.parent)
    except IndeterminateOnboardingError:
        if output_fd is not None:
            try:
                os.close(output_fd)
            except OSError:
                pass
            output_fd = None
        _fsync_directory(output_path.parent)
        raise
    except BaseException:
        _remove_reserved_output(output_path, output_fd)
        raise

    public_result = dict(result)
    public_result["first_key"] = dict(result["first_key"]["metadata"])
    public_result["raw_key_delivery"] = {
        "written": True,
        "mode": "new_owner_only_0600_file",
    }
    public_result["safety_confirmation_was_authentication"] = False
    return public_result


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(async_main(args))
    except (TenantOnboardingError, KeyProvisioningError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
