"""Citation/usage feedback for SOUL retrieval audit rows.

This module records id-level feedback only. It deliberately avoids copying
memory content into retrieval logs, so feedback can improve utility without
expanding the private data surface.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable


def _normalize_ids(values: Iterable[int | str] | None) -> list[int]:
    if not values:
        return []
    normalized: set[int] = set()
    for value in values:
        try:
            mid = int(value)
        except (TypeError, ValueError):
            continue
        if mid > 0:
            normalized.add(mid)
    return sorted(normalized)


async def record_memory_citation_feedback(
    conn: Any,
    retrieval_log_id: int,
    *,
    cited_ids: Iterable[int | str] | None = None,
    used_ids: Iterable[int | str] | None = None,
    actor: str = "ADA",
    context: str = "",
) -> dict[str, Any]:
    """Mark returned memories as cited/used and update their utility_score.

    The helper rejects ids that were not returned by the original retrieval log.
    That keeps this path from becoming a utility-poisoning channel.
    """
    cited = _normalize_ids(cited_ids)
    used = _normalize_ids(used_ids)
    if not cited and not used:
        return {"updated": False, "reason": "no_ids"}

    row = await conn.fetchrow(
        """
        SELECT memory_ids_returned, memory_ids_cited, memory_ids_used, metadata
        FROM soul_v3.memory_retrieval_log
        WHERE id = $1
        FOR UPDATE
        """,
        retrieval_log_id,
    )
    if not row:
        raise ValueError(f"retrieval_log_id not found: {retrieval_log_id}")

    returned = _normalize_ids(row["memory_ids_returned"])
    returned_set = set(returned)
    requested = set(cited) | set(used)
    non_returned = sorted(requested - returned_set)
    if non_returned:
        raise ValueError(f"feedback ids were not returned by retrieval log: {non_returned}")

    existing_cited = _normalize_ids(row["memory_ids_cited"])
    existing_used = _normalize_ids(row["memory_ids_used"])
    merged_cited = sorted(set(existing_cited) | set(cited))
    merged_used = sorted(set(existing_used) | set(used))
    precision = (len([mid for mid in merged_cited if mid in returned_set]) / len(merged_cited)) if merged_cited else None

    metadata = row["metadata"]
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    feedback_meta = {
        "actor": actor,
        "cited_count": len(merged_cited),
        "used_count": len(merged_used),
        "returned_count": len(returned),
    }
    if context:
        feedback_meta["context"] = context[:240]
    metadata["citation_feedback"] = feedback_meta

    await conn.execute(
        """
        UPDATE soul_v3.memory_retrieval_log
        SET memory_ids_cited = $2::bigint[],
            memory_ids_used = $3::bigint[],
            citation_precision = $4,
            citation_feedback_at = NOW(),
            metadata = $5::jsonb
        WHERE id = $1
        """,
        retrieval_log_id,
        merged_cited,
        merged_used,
        precision,
        json.dumps(metadata),
    )

    if merged_cited:
        await conn.execute(
            """
            UPDATE soul_v3.memories
            SET utility_score = LEAST(1.0, COALESCE(utility_score, 0.5) + 0.06 * (1.0 - COALESCE(utility_score, 0.5)))
            WHERE id = ANY($1::bigint[])
            """,
            merged_cited,
        )
    if merged_used:
        await conn.execute(
            """
            UPDATE soul_v3.memories
            SET utility_score = LEAST(1.0, COALESCE(utility_score, 0.5) + 0.12 * (1.0 - COALESCE(utility_score, 0.5)))
            WHERE id = ANY($1::bigint[])
            """,
            merged_used,
        )

    return {
        "updated": True,
        "retrieval_log_id": retrieval_log_id,
        "returned_ids": returned,
        "cited_ids": merged_cited,
        "used_ids": merged_used,
        "citation_precision": precision,
    }


def extract_answer_memory_ids(answer_text: str) -> list[int]:
    """Extract SOUL/memory id citations from answer text."""
    patterns = [
        r"\[SOUL\s+id=(\d+)\]",
        r"\bSOUL\s+id[=:]\s*(\d+)\b",
        r"\bmemory\s*#(\d+)\b",
        r"\bmemoria\s*#(\d+)\b",
    ]
    found: set[int] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, answer_text or "", re.IGNORECASE):
            try:
                found.add(int(match.group(1)))
            except (TypeError, ValueError):
                continue
    return sorted(mid for mid in found if mid > 0)


async def record_answer_citation_feedback(
    conn: Any,
    retrieval_log_id: int,
    *,
    answer_text: str,
    actor: str = "ADA",
    context: str = "",
) -> dict[str, Any]:
    """Extract cited ids from answer text and record citation feedback."""
    ids = extract_answer_memory_ids(answer_text)
    if not ids:
        return {"updated": False, "reason": "no_answer_citations", "retrieval_log_id": retrieval_log_id}
    return await record_memory_citation_feedback(
        conn,
        retrieval_log_id,
        cited_ids=ids,
        used_ids=ids,
        actor=actor,
        context=context or "answer_text_citation_extraction",
    )
