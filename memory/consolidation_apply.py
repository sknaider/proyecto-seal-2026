#!/usr/bin/env python3
"""Apply SOUL consolidation candidate batches with archive/rollback metadata.

Default mode is dry-run. Live mode requires:
  - exact candidate count match
  - authenticated William approval message
  - explicit --apply
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from consolidation_approval import verify_approval_message
from consolidation_integrity import (
    GLOBAL_PROTECTED_CATEGORIES,
    assert_candidate_matches_locked_row,
    candidate_manifest_sha256,
    record_flight_event,
    sha256_value,
    snapshot_row,
)
from seal_secrets import pg_dsn


DB_URL = os.environ.get("SEAL_PG_DSN", pg_dsn(required=True))
LOWER_CHAT_ACTION_PREFIX = "lower_chat_excerpt_importance_to_"
DUPLICATE_ACTION = "invalidate_exact_duplicate_rows_keep_best_copy"
SUPPORTED_ACTIONS = {DUPLICATE_ACTION}

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def _load_agent_protected_categories(agent: str) -> set[str]:
    rubric_path = _AGENTS_DIR / agent.upper() / "rubric.yaml"
    if not rubric_path.exists():
        raise RuntimeError(f"rubric_missing_fail_closed agent={agent.upper()}")
    try:
        import yaml  # type: ignore[import]
        with rubric_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("rubric root must be an object")
        return set(GLOBAL_PROTECTED_CATEGORIES) | set(data.get("protected_categories") or [])
    except Exception as exc:
        raise RuntimeError(f"rubric_invalid_fail_closed agent={agent.upper()}: {exc}") from exc


async def _verify_rubric_not_violated(
    conn: asyncpg.Connection,
    agent: str,
    candidates: list[dict[str, Any]],
    action: str,
) -> None:
    """For DUPLICATE_ACTION: if a candidate's category is in the agent's
    protected_categories, verify its content_hash matches the keep canonical.
    A protected memory is only consolidatable if it is a true textual duplicate
    (same content_hash). Raises RuntimeError otherwise — hard gate in the engine."""
    protected = _load_agent_protected_categories(agent)
    for c in candidates:
        cat = (c.get("current") or {}).get("category") or c.get("category") or ""
        if cat not in protected:
            continue
        if action != DUPLICATE_ACTION:
            raise RuntimeError(
                f"rubric_protected_category_blocked category={cat} "
                f"memory_id={c['memory_id']} reason=action_{action}_not_exact_duplicate"
            )
        if action == DUPLICATE_ACTION:
            keep_id = int((c.get("group") or {}).get("keep_id") or 0)
            candidate_hash = c.get("content_hash") or ""
            if not keep_id or not candidate_hash:
                raise RuntimeError(
                    f"rubric_protected_category_blocked category={cat} "
                    f"memory_id={c['memory_id']} reason=missing_keep_id_or_hash"
                )
            keep_content = await conn.fetchval(
                "SELECT content FROM soul_v3.memories WHERE id=$1 AND invalid_at IS NULL",
                keep_id,
            )
            if keep_content is None:
                raise RuntimeError(
                    f"rubric_protected_category_blocked category={cat} "
                    f"memory_id={c['memory_id']} reason=keep_id_{keep_id}_not_found"
                )
            keep_hash = hashlib.sha256(keep_content.encode()).hexdigest()
            if keep_hash != candidate_hash:
                raise RuntimeError(
                    f"rubric_protected_category_blocked category={cat} "
                    f"memory_id={c['memory_id']} reason=content_hash_mismatch "
                    f"keep_id={keep_id}"
                )
    if action == DUPLICATE_ACTION:
        await _verify_duplicate_keeps_valid(conn, agent, candidates)


async def _verify_duplicate_keeps_valid(
    conn: asyncpg.Connection,
    agent: str,
    candidates: list[dict[str, Any]],
) -> None:
    """Every duplicate candidate must point to an active exact-text canonical."""
    for c in candidates:
        keep_id = int((c.get("group") or {}).get("keep_id") or 0)
        candidate_hash = c.get("content_hash") or ""
        if not keep_id or not candidate_hash:
            raise RuntimeError(
                f"duplicate_keep_invalid memory_id={c['memory_id']} reason=missing_keep_id_or_hash"
            )
        keep_content = await conn.fetchval(
            "SELECT content FROM soul_v3.memories WHERE id=$1 AND agent=$2 AND invalid_at IS NULL",
            keep_id,
            agent.upper(),
        )
        if keep_content is None:
            raise RuntimeError(
                f"duplicate_keep_invalid memory_id={c['memory_id']} reason=keep_id_{keep_id}_not_active"
            )
        keep_hash = hashlib.sha256(keep_content.encode()).hexdigest()
        if keep_hash != candidate_hash:
            raise RuntimeError(
                f"duplicate_keep_invalid memory_id={c['memory_id']} reason=content_hash_mismatch "
                f"keep_id={keep_id}"
            )


def metadata_object_sql(column: str = "metadata") -> str:
    return (
        f"CASE WHEN jsonb_typeof({column}) = 'object' THEN {column} "
        f"WHEN {column} IS NULL THEN '{{}}'::jsonb "
        f"ELSE jsonb_build_object('legacy_metadata', {column}) END"
    )


@dataclass(frozen=True)
class BatchPlan:
    agent: str
    action: str
    expected_count: int
    candidate_count: int
    db_count: int
    dry_run: bool
    rollback_token: str
    risk: str | None
    candidate_file: str
    candidate_manifest_sha256: str
    approval_message_id: int | None = None
    approval_verified: bool = False


def load_candidates(path: Path, agent: str, action: str) -> list[dict[str, Any]]:
    agent_u = agent.upper()
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("agent") == agent_u and record.get("action") == action:
                records.append(record)
    ids = [int(record.get("memory_id") or 0) for record in records]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("candidate_memory_ids_missing_or_duplicate")
    return records


def new_rollback_token(agent: str, action: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"consolidation_v1:{agent.upper()}:{action}:{stamp}:{uuid.uuid4().hex[:12]}"


def archive_metadata(
    existing: dict[str, Any] | None,
    candidate: dict[str, Any],
    rollback_token: str,
    batch_id: str,
    *,
    snapshot_sha256: str | None = None,
    manifest_sha256: str | None = None,
) -> dict[str, Any]:
    metadata = dict(existing or {})
    metadata["consolidation_backup"] = {
        "version": 1,
        "rollback_token": rollback_token,
        "batch_id": batch_id,
        "original_memory_id": candidate["memory_id"],
        "action": candidate["action"],
        "matched_rule": candidate.get("matched_rule"),
        "current": candidate.get("current"),
        "proposed": candidate.get("proposed"),
        "content_hash": candidate.get("content_hash"),
        "snapshot_sha256": snapshot_sha256,
        "manifest_sha256": manifest_sha256,
        "archived_at": datetime.now(timezone.utc).isoformat(),
    }
    return metadata


def json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def proposed_importance_for_action(action: str) -> int | None:
    if not action.startswith(LOWER_CHAT_ACTION_PREFIX):
        return None
    suffix = action.removeprefix(LOWER_CHAT_ACTION_PREFIX)
    if not suffix.isdigit():
        return None
    value = int(suffix)
    return value if 1 <= value <= 10 else None


def action_is_supported(action: str) -> bool:
    return action in SUPPORTED_ACTIONS or proposed_importance_for_action(action) is not None


async def _active_db_count(conn: asyncpg.Connection, agent: str, ids: list[int]) -> int:
    if not ids:
        return 0
    return int(
        await conn.fetchval(
            """
            SELECT count(*)
            FROM soul_v3.memories
            WHERE agent=$1 AND invalid_at IS NULL AND id = ANY($2::bigint[])
            """,
            agent,
            ids,
        )
        or 0
    )


async def build_plan(
    candidate_file: Path,
    agent: str,
    action: str,
    expected_count: int,
    dry_run: bool = True,
    rollback_token: str | None = None,
) -> tuple[BatchPlan, list[dict[str, Any]]]:
    if not action_is_supported(action):
        raise ValueError(f"unsupported action: {action}")
    candidates = load_candidates(candidate_file, agent, action)
    manifest_sha256 = candidate_manifest_sha256(candidates)
    rollback_token = rollback_token or new_rollback_token(agent, action)
    ids = [int(c["memory_id"]) for c in candidates]
    conn = await asyncpg.connect(DB_URL)
    try:
        db_count = await _active_db_count(conn, agent.upper(), ids)
    finally:
        await conn.close()
    risk = candidates[0].get("risk") if candidates else None
    return (
        BatchPlan(
            agent=agent.upper(),
            action=action,
            expected_count=expected_count,
            candidate_count=len(candidates),
            db_count=db_count,
            dry_run=dry_run,
            rollback_token=rollback_token,
            risk=risk,
            candidate_file=str(candidate_file),
            candidate_manifest_sha256=manifest_sha256,
        ),
        candidates,
    )


async def apply_batch(
    candidate_file: Path,
    agent: str,
    action: str,
    expected_count: int,
    approval_message_id: int | None,
    apply: bool,
) -> BatchPlan:
    dry_run = not apply
    plan, candidates = await build_plan(candidate_file, agent, action, expected_count, dry_run=dry_run)
    if plan.candidate_count != expected_count:
        raise RuntimeError(f"candidate_count_mismatch expected={expected_count} actual={plan.candidate_count}")
    if plan.db_count != expected_count:
        raise RuntimeError(f"db_count_mismatch expected={expected_count} active={plan.db_count}")
    if dry_run:
        return plan
    if approval_message_id is None:
        raise RuntimeError("approval_message_id_required")
    approval = await verify_approval_message(
        approval_message_id,
        agent,
        action,
        expected_count,
        plan.candidate_manifest_sha256,
    )
    if not approval.ok:
        raise RuntimeError(f"approval_rejected reason={approval.reason}")

    conn = await asyncpg.connect(DB_URL)
    ids = [int(c["memory_id"]) for c in candidates]
    by_id = {int(c["memory_id"]): c for c in candidates}
    batch_id = uuid.uuid4().hex
    try:
        async with conn.transaction():
            await conn.execute("LOCK TABLE soul_v3.memories_archive IN EXCLUSIVE MODE")
            next_archive_id = int(await conn.fetchval("SELECT COALESCE(max(id), 0) + 1 FROM soul_v3.memories_archive") or 1)
            rows = await conn.fetch(
                """
                SELECT *
                FROM soul_v3.memories
                WHERE agent=$1 AND invalid_at IS NULL AND id = ANY($2::bigint[])
                FOR UPDATE
                """,
                agent.upper(),
                ids,
            )
            if len(rows) != expected_count:
                raise RuntimeError(f"locked_count_mismatch expected={expected_count} actual={len(rows)}")
            for row in rows:
                assert_candidate_matches_locked_row(by_id[int(row["id"])], dict(row))
            await _verify_rubric_not_violated(conn, agent, candidates, action)
            before_by_id = {int(row["id"]): dict(row) for row in rows}
            archive_by_memory_id: dict[int, int] = {}
            await record_flight_event(
                conn,
                agent=agent,
                phase="apply_begin",
                action=action,
                rollback_token=plan.rollback_token,
                batch_id=batch_id,
                manifest_sha256=plan.candidate_manifest_sha256,
            )
            for row in rows:
                candidate = by_id[int(row["id"])]
                metadata = archive_metadata(
                    json_dict(row["metadata"]),
                    candidate,
                    plan.rollback_token,
                    batch_id,
                    snapshot_sha256=sha256_value(snapshot_row(dict(row))),
                    manifest_sha256=plan.candidate_manifest_sha256,
                )
                archive_id = next_archive_id
                await conn.execute(
                    """
                    INSERT INTO soul_v3.memories_archive (
                        id, agent, scope, category, content, importance, source_tier,
                        heat_score, access_count, created_at, archived_at, reason,
                        metadata, embedding, embedding_bm25, context_fingerprint,
                        temporal_cluster, valid_from, invalid_at, superseded_by,
                        updated_at, source, event_time, valence, arousal, dominance,
                        memory_type, confidence_score, last_activation, query_count,
                        recall_count, last_recalled_at, utility_score, episode_context,
                        decay_score, surprise_score
                    )
                    VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW(),$11,$12::jsonb,$13,$14,$15,
                        $16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,
                        $31,$32,$33,$34,$35
                    )
                    """,
                    next_archive_id, row["agent"], row["scope"], row["category"], row["content"], row["importance"],
                    row["source_tier"], row["heat_score"], row["access_count"], row["created_at"],
                    f"consolidation_v1:{action}", json.dumps(metadata, ensure_ascii=False),
                    row["embedding"], row["embedding_bm25"], row["context_fingerprint"],
                    row["temporal_cluster"], row["valid_from"], row["invalid_at"], row["superseded_by"],
                    row["updated_at"], row["source"], row["event_time"], row["valence"], row["arousal"],
                    row["dominance"], row["memory_type"], row["confidence_score"], row["last_activation"],
                    row["query_count"], row["recall_count"], row["last_recalled_at"], row["utility_score"],
                    row["episode_context"], row["decay_score"], row["surprise_score"],
                )
                archive_by_memory_id[int(row["id"])] = archive_id
                next_archive_id += 1
            target_importance = proposed_importance_for_action(action)
            if target_importance is not None:
                result = await conn.execute(
                    """
                    UPDATE soul_v3.memories
                    SET importance=$1, updated_at=NOW(),
                        metadata = {metadata_object}
                                   || jsonb_build_object('consolidation_v1_last_action', $2::text,
                                                         'consolidation_v1_rollback_token', $3::text)
                    WHERE agent=$4 AND invalid_at IS NULL AND id = ANY($5::bigint[])
                    """.format(metadata_object=metadata_object_sql()),
                    target_importance,
                    action,
                    plan.rollback_token,
                    agent.upper(),
                    ids,
                )
                updates = int(result.split()[-1])
                if updates != expected_count:
                    raise RuntimeError(f"update_count_mismatch expected={expected_count} actual={updates}")
            elif action == DUPLICATE_ACTION:
                updates = 0
                for candidate in candidates:
                    keep_id = int((candidate.get("group") or {}).get("keep_id") or 0)
                    if not keep_id:
                        raise RuntimeError(f"missing_keep_id memory_id={candidate['memory_id']}")
                    result = await conn.execute(
                        """
                        UPDATE soul_v3.memories
                        SET invalid_at=NOW(), superseded_by=$1, updated_at=NOW(),
                            metadata = {metadata_object}
                                       || jsonb_build_object('consolidation_v1_last_action', $2::text,
                                                             'consolidation_v1_rollback_token', $3::text)
                        WHERE agent=$4 AND invalid_at IS NULL AND id=$5
                        """.format(metadata_object=metadata_object_sql()),
                        keep_id,
                        action,
                        plan.rollback_token,
                        agent.upper(),
                        int(candidate["memory_id"]),
                    )
                    updates += int(result.split()[-1])
                if updates != expected_count:
                    raise RuntimeError(f"update_count_mismatch expected={expected_count} actual={updates}")
            after_rows = await conn.fetch(
                "SELECT * FROM soul_v3.memories WHERE agent=$1 AND id=ANY($2::bigint[]) ORDER BY id FOR UPDATE",
                agent.upper(),
                ids,
            )
            if len(after_rows) != expected_count:
                raise RuntimeError(
                    f"post_update_count_mismatch expected={expected_count} actual={len(after_rows)}"
                )
            for after in after_rows:
                memory_id = int(after["id"])
                await record_flight_event(
                    conn,
                    agent=agent,
                    phase="apply_memory",
                    action=action,
                    rollback_token=plan.rollback_token,
                    batch_id=batch_id,
                    manifest_sha256=plan.candidate_manifest_sha256,
                    memory_id=memory_id,
                    archive_id=archive_by_memory_id[memory_id],
                    before=before_by_id[memory_id],
                    after=dict(after),
                )
            await record_flight_event(
                conn,
                agent=agent,
                phase="apply_commit",
                action=action,
                rollback_token=plan.rollback_token,
                batch_id=batch_id,
                manifest_sha256=plan.candidate_manifest_sha256,
            )
        return BatchPlan(**{**asdict(plan), "approval_message_id": approval_message_id, "approval_verified": True})
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run or apply one consolidation candidate batch.")
    parser.add_argument("--candidate-file", type=Path, required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--approval-message-id", type=int)
    parser.add_argument("--apply", action="store_true", help="Execute live writes. Omit for dry-run.")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = await apply_batch(
            args.candidate_file,
            args.agent,
            args.action,
            args.expected_count,
            args.approval_message_id,
            args.apply,
        )
        print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
