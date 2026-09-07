from __future__ import annotations

import hashlib
from pathlib import Path

from memory.nerves_mission_handoff import claim_handoff
from memory.nerves_native_agent_prompt_hook import (
    NATIVE_PROFILE,
    PROMPT_STUB_PREFIX,
    process_hook,
)
from memory.nerves_native_agent_receipt import (
    NATIVE_SPAWN_DESCRIPTION,
    NATIVE_SPAWN_NAME,
    build_native_reasoner_prompt,
)
from memory.test_nerves_mission_handoff import _deliver, _fixture, _paths


def _claimed(tmp_path: Path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(exist_ok=True)
    audit_dir.chmod(0o700)
    fixture = _fixture(tmp_path)
    handoff = _deliver(fixture, tmp_path)
    inbox, state, _ = _paths(tmp_path)
    claim = claim_handoff(
        fixture.mission["mission_id"],
        fixture.mission["idempotency_key"],
        handoff.handoff_sha256,
        "jarvis-native:prompt-hook-test",
        inbox_dir=inbox,
        state_path=state,
    )
    return fixture, handoff, state, claim


def _payload(
    tmp_path: Path, mission_id: str, claim_id: str
) -> dict:
    session_id = "session-prompt-hook-test"
    transcript = tmp_path / f"{session_id}.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    transcript.chmod(0o600)
    return {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "transcript_path": str(transcript),
        "cwd": str(Path(__file__).resolve().parents[1]),
        "tool_use_id": "toolu-prompt-hook-test",
        "tool_name": "Agent",
        "tool_input": {
            "description": NATIVE_SPAWN_DESCRIPTION,
            "subagent_type": NATIVE_PROFILE,
            "name": NATIVE_SPAWN_NAME,
            "prompt": (
                PROMPT_STUB_PREFIX
                + f"mission_id={mission_id}\n"
                + f"claim_id={claim_id}\n"
            ),
            "run_in_background": True,
        },
    }


def test_hook_renders_exact_canonical_prompt(tmp_path: Path) -> None:
    fixture, _, state, claim = _claimed(tmp_path)
    result = process_hook(
        _payload(
            tmp_path, fixture.mission["mission_id"], claim.claim_id
        ),
        state_path=state,
        audit_dir=tmp_path / "audit",
    )
    assert result is not None
    hook = result["hookSpecificOutput"]
    assert hook["permissionDecision"] == "allow"
    updated = hook["updatedInput"]
    expected = build_native_reasoner_prompt(
        fixture.mission["mission_id"],
        fixture.evidence.read_bytes(),
        fixture.provenance.read_bytes(),
    )
    assert updated["prompt"] == expected
    assert hashlib.sha256(updated["prompt"].encode()).hexdigest() == hashlib.sha256(
        expected.encode()
    ).hexdigest()
    assert updated["run_in_background"] is True
    audit = tmp_path / "audit" / (
        f"{fixture.mission['mission_id']}.prompt-render.json"
    )
    assert audit.stat().st_mode & 0o777 == 0o600


def test_hook_ignores_unrelated_agent(tmp_path: Path) -> None:
    payload = {
        "tool_name": "Agent",
        "tool_input": {
            "description": "Normal worker",
            "subagent_type": "general-purpose",
            "name": "worker",
            "prompt": "Inspect this.",
        },
    }
    assert process_hook(payload, state_path=tmp_path / "absent.json") is None


def test_hook_denies_partial_protected_invocation(tmp_path: Path) -> None:
    fixture, _, state, claim = _claimed(tmp_path)
    payload = _payload(tmp_path, fixture.mission["mission_id"], claim.claim_id)
    payload["tool_input"]["description"] = "lookalike"
    result = process_hook(payload, state_path=state, audit_dir=tmp_path / "audit")
    assert result is not None
    hook = result["hookSpecificOutput"]
    assert hook["permissionDecision"] == "deny"
    assert "description_mismatch" in hook["permissionDecisionReason"]


def test_hook_requires_background_spawn(tmp_path: Path) -> None:
    fixture, _, state, claim = _claimed(tmp_path)
    payload = _payload(tmp_path, fixture.mission["mission_id"], claim.claim_id)
    payload["tool_input"]["run_in_background"] = False
    result = process_hook(payload, state_path=state, audit_dir=tmp_path / "audit")
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "background_required" in result["hookSpecificOutput"][
        "permissionDecisionReason"
    ]


def test_hook_denies_unclaimed_mission(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _deliver(fixture, tmp_path)
    _, state, _ = _paths(tmp_path)
    claim_id = "00000000-0000-4000-8000-000000000001"
    result = process_hook(
        _payload(tmp_path, fixture.mission["mission_id"], claim_id),
        state_path=state,
        audit_dir=tmp_path / "audit",
    )
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "mission_not_claimed" in result["hookSpecificOutput"][
        "permissionDecisionReason"
    ]


def test_hook_denies_evidence_tamper(tmp_path: Path) -> None:
    fixture, _, state, claim = _claimed(tmp_path)
    fixture.evidence.write_bytes(fixture.evidence.read_bytes() + b" ")
    result = process_hook(
        _payload(tmp_path, fixture.mission["mission_id"], claim.claim_id),
        state_path=state,
        audit_dir=tmp_path / "audit",
    )
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "evidence_digest_mismatch" in result["hookSpecificOutput"][
        "permissionDecisionReason"
    ]


def test_hook_replay_is_idempotent_but_other_tool_id_is_denied(
    tmp_path: Path,
) -> None:
    fixture, _, state, claim = _claimed(tmp_path)
    payload = _payload(tmp_path, fixture.mission["mission_id"], claim.claim_id)
    first = process_hook(payload, state_path=state, audit_dir=tmp_path / "audit")
    second = process_hook(payload, state_path=state, audit_dir=tmp_path / "audit")
    assert first == second
    payload["tool_use_id"] = "toolu-other"
    denied = process_hook(
        payload, state_path=state, audit_dir=tmp_path / "audit"
    )
    assert denied is not None
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "audit_replay_mismatch" in denied["hookSpecificOutput"][
        "permissionDecisionReason"
    ]


def test_hook_rejects_subagent_transcript_path(tmp_path: Path) -> None:
    fixture, _, state, claim = _claimed(tmp_path)
    payload = _payload(tmp_path, fixture.mission["mission_id"], claim.claim_id)
    subagents = tmp_path / "subagents"
    subagents.mkdir()
    transcript = subagents / f"{payload['session_id']}.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    transcript.chmod(0o600)
    payload["transcript_path"] = str(transcript)
    denied = process_hook(
        payload, state_path=state, audit_dir=tmp_path / "audit"
    )
    assert denied is not None
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "parent_transcript_invalid" in denied["hookSpecificOutput"][
        "permissionDecisionReason"
    ]
