from __future__ import annotations

import hashlib
import sys
import unittest.mock
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from consolidation_apply import (
    DUPLICATE_ACTION,
    _load_agent_protected_categories,
    _verify_rubric_not_violated,
    action_is_supported,
    archive_metadata,
    json_dict,
    load_candidates,
    proposed_importance_for_action,
)


def test_proposed_importance_for_lower_action() -> None:
    assert proposed_importance_for_action("lower_chat_excerpt_importance_to_5") == 5
    assert proposed_importance_for_action("lower_chat_excerpt_importance_to_x") is None
    assert proposed_importance_for_action(DUPLICATE_ACTION) is None


def test_action_support_matrix() -> None:
    assert action_is_supported("lower_chat_excerpt_importance_to_5")
    assert action_is_supported(DUPLICATE_ACTION)
    assert not action_is_supported("review_lower_or_archive_stale_dynamic")


def test_archive_metadata_keeps_original_id_and_rollback_token() -> None:
    candidate = {
        "memory_id": 123,
        "action": "lower_chat_excerpt_importance_to_5",
        "matched_rule": "short_chat_excerpt_importance_cap",
        "current": {"importance": 10},
        "proposed": {"importance": 5},
        "content_hash": "abc",
    }
    metadata = archive_metadata({"existing": True}, candidate, "rollback-1", "batch-1")

    backup = metadata["consolidation_backup"]
    assert metadata["existing"] is True
    assert backup["original_memory_id"] == 123
    assert backup["rollback_token"] == "rollback-1"
    assert backup["batch_id"] == "batch-1"


def test_json_dict_accepts_asyncpg_jsonb_string() -> None:
    assert json_dict('{"a": 1}') == {"a": 1}
    assert json_dict({"b": 2}) == {"b": 2}
    assert json_dict(None) == {}


def test_load_candidates_filters_agent_and_action(tmp_path: Path) -> None:
    path = tmp_path / "candidates.jsonl"
    path.write_text(
        '{"agent":"ADA","action":"a","memory_id":1}\n'
        '{"agent":"JARVIS","action":"a","memory_id":2}\n'
        '{"agent":"ADA","action":"b","memory_id":3}\n',
        encoding="utf-8",
    )

    rows = load_candidates(path, "ADA", "a")

    assert [r["memory_id"] for r in rows] == [1]


def test_load_agent_protected_categories_reads_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_dir = tmp_path / "agents" / "TESTBOT"
    agents_dir.mkdir(parents=True)
    (agents_dir / "rubric.yaml").write_text(
        "protected_categories:\n  - emotion\n  - core\n",
        encoding="utf-8",
    )
    import consolidation_apply
    monkeypatch.setattr(consolidation_apply, "_AGENTS_DIR", tmp_path / "agents")

    cats = _load_agent_protected_categories("TESTBOT")
    assert {"emotion", "core", "identity", "trust", "correction", "rule"} <= cats


def test_load_agent_protected_categories_missing_rubric(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import consolidation_apply
    monkeypatch.setattr(consolidation_apply, "_AGENTS_DIR", tmp_path / "agents")

    with pytest.raises(RuntimeError, match="rubric_missing_fail_closed"):
        _load_agent_protected_categories("NOBODY")


@pytest.mark.asyncio
async def test_verify_rubric_skips_non_duplicate_action() -> None:
    conn = MagicMock()
    # Should not raise and should not call conn at all
    await _verify_rubric_not_violated(conn, "ALICE", [], "lower_chat_excerpt_importance_to_5")
    conn.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_verify_rubric_allows_true_dup_in_protected_category(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = "Alice ignora consistentemente los heartbeats."
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    agents_dir = tmp_path / "agents" / "ALICE"
    agents_dir.mkdir(parents=True)
    (agents_dir / "rubric.yaml").write_text(
        "protected_categories:\n  - emotion\n", encoding="utf-8"
    )
    import consolidation_apply
    monkeypatch.setattr(consolidation_apply, "_AGENTS_DIR", tmp_path / "agents")

    conn = AsyncMock()
    conn.fetchval.return_value = content

    candidate = {
        "memory_id": 31636,
        "content_hash": content_hash,
        "current": {"category": "emotion"},
        "group": {"keep_id": 31484},
    }
    # Should not raise — content_hash matches
    await _verify_rubric_not_violated(conn, "ALICE", [candidate], DUPLICATE_ACTION)


@pytest.mark.asyncio
async def test_verify_rubric_blocks_non_dup_protected_category(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_dir = tmp_path / "agents" / "ALICE"
    agents_dir.mkdir(parents=True)
    (agents_dir / "rubric.yaml").write_text(
        "protected_categories:\n  - emotion\n", encoding="utf-8"
    )
    import consolidation_apply
    monkeypatch.setattr(consolidation_apply, "_AGENTS_DIR", tmp_path / "agents")

    conn = AsyncMock()
    conn.fetchval.return_value = "Different content entirely."

    candidate_hash = hashlib.sha256("Original unique content.".encode()).hexdigest()
    candidate = {
        "memory_id": 99999,
        "content_hash": candidate_hash,
        "current": {"category": "emotion"},
        "group": {"keep_id": 88888},
    }
    with pytest.raises(RuntimeError, match="rubric_protected_category_blocked"):
        await _verify_rubric_not_violated(conn, "ALICE", [candidate], DUPLICATE_ACTION)


@pytest.mark.asyncio
async def test_verify_rubric_blocks_when_keep_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_dir = tmp_path / "agents" / "ALICE"
    agents_dir.mkdir(parents=True)
    (agents_dir / "rubric.yaml").write_text(
        "protected_categories:\n  - emotion\n", encoding="utf-8"
    )
    import consolidation_apply
    monkeypatch.setattr(consolidation_apply, "_AGENTS_DIR", tmp_path / "agents")

    conn = AsyncMock()
    conn.fetchval.return_value = None  # keep_id not found in DB

    candidate = {
        "memory_id": 11111,
        "content_hash": "somehash",
        "current": {"category": "emotion"},
        "group": {"keep_id": 22222},
    }
    with pytest.raises(RuntimeError, match="rubric_protected_category_blocked"):
        await _verify_rubric_not_violated(conn, "ALICE", [candidate], DUPLICATE_ACTION)


@pytest.mark.asyncio
async def test_verify_duplicate_blocks_inactive_keep_for_non_protected_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import consolidation_apply
    monkeypatch.setattr(consolidation_apply, "_AGENTS_DIR", tmp_path / "agents")
    rubric_dir = tmp_path / "agents" / "ALICE"
    rubric_dir.mkdir(parents=True)
    (rubric_dir / "rubric.yaml").write_text("protected_categories: []\n", encoding="utf-8")

    conn = AsyncMock()
    conn.fetchval.return_value = None

    candidate = {
        "memory_id": 33333,
        "content_hash": "somehash",
        "current": {"category": "pattern"},
        "group": {"keep_id": 44444},
    }
    with pytest.raises(RuntimeError, match="duplicate_keep_invalid"):
        await _verify_rubric_not_violated(conn, "ALICE", [candidate], DUPLICATE_ACTION)


@pytest.mark.asyncio
async def test_protected_category_cannot_be_lowered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import consolidation_apply
    rubric_dir = tmp_path / "agents" / "ADA"
    rubric_dir.mkdir(parents=True)
    (rubric_dir / "rubric.yaml").write_text("protected_categories: []\n", encoding="utf-8")
    monkeypatch.setattr(consolidation_apply, "_AGENTS_DIR", tmp_path / "agents")
    candidate = {"memory_id": 9, "current": {"category": "correction"}}
    with pytest.raises(RuntimeError, match="rubric_protected_category_blocked"):
        await _verify_rubric_not_violated(
            AsyncMock(), "ADA", [candidate], "lower_chat_excerpt_importance_to_5"
        )


def test_importance_action_range_is_bounded() -> None:
    assert proposed_importance_for_action("lower_chat_excerpt_importance_to_1") == 1
    assert proposed_importance_for_action("lower_chat_excerpt_importance_to_10") == 10
    assert proposed_importance_for_action("lower_chat_excerpt_importance_to_0") is None
    assert proposed_importance_for_action("lower_chat_excerpt_importance_to_11") is None
