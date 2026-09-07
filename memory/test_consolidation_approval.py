from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from consolidation_approval import _hash_session_key, _is_william_user, parse_approval_text


def test_parse_exact_approval_phrase() -> None:
    parsed = parse_approval_text("OK ADA aplica lote JARVIS lower_chat_excerpt_importance_to_5 count=1290")

    assert parsed is not None
    assert parsed.agent == "JARVIS"
    assert parsed.action == "lower_chat_excerpt_importance_to_5"
    assert parsed.count == 1290
    assert parsed.manifest_sha256 is None


def test_parse_approval_phrase_with_manifest() -> None:
    digest = "a" * 64
    parsed = parse_approval_text(
        f"OK ADA aplica lote ADA lower_chat_excerpt_importance_to_5 count=2 manifest={digest}"
    )
    assert parsed is not None
    assert parsed.manifest_sha256 == digest


def test_parse_matrix_prefixed_approval_phrase() -> None:
    parsed = parse_approval_text("[Matrix] OK ADA aplica lote ADA lower_chat_excerpt_importance_to_5 count=867")

    assert parsed is not None
    assert parsed.agent == "ADA"
    assert parsed.action == "lower_chat_excerpt_importance_to_5"
    assert parsed.count == 867


def test_reject_ambiguous_approval_phrase() -> None:
    assert parse_approval_text("hazlo todo") is None
    assert parse_approval_text("OK ADA aplica lote JARVIS lower_chat_excerpt_importance_to_5") is None
    assert parse_approval_text("OK ADA aplica lote JARVIS lower_chat_excerpt_importance_to_5 count=mucho") is None


def test_session_hash_never_depends_on_plaintext_storage() -> None:
    token = "jwt-or-session-token"

    assert _hash_session_key(token) == "21ca63d56271b23df907cad25a3e0e154e69aee5a81e035a1470bdeebfc48119"
    assert _hash_session_key(token) != token


def test_william_user_match_is_case_insensitive() -> None:
    assert _is_william_user("William", None)
    assert _is_william_user(None, "Dadito")
    assert not _is_william_user("alice", "ALICE")
