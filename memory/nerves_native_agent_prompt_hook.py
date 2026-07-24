#!/usr/bin/env python3
"""Render the exact JARVIS NERVES child prompt at Claude's Agent boundary.

The parent supplies only a mission identifier.  This hook resolves the
owner-only admitted handoff, verifies its hashes, and replaces the Agent prompt
with the canonical UTF-8 payload.  Any invocation that resembles the protected
NERVES worker but does not satisfy the exact contract is denied fail-closed.
Other Agent invocations are left untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.nerves_mission_handoff import (  # noqa: E402
    DEFAULT_STATE,
    HandoffError,
    _canonical_bytes,
    _secure_create,
    _secure_read,
    _validate_existing_handoff,
)
from memory.nerves_native_agent_receipt import (  # noqa: E402
    MAX_RESULT_BYTES,
    NATIVE_SPAWN_DESCRIPTION,
    NATIVE_SPAWN_NAME,
    NativeReceiptError,
    _state_delivery,
    build_native_reasoner_prompt,
)


NATIVE_PROFILE = "nerves-jarvis-reasoner"
NATIVE_PROFILE_PATH = ROOT / ".claude/agents/nerves-jarvis-reasoner.md"
DEFAULT_AUDIT_DIR = (
    ROOT / "research/flywire_results/nerves_orchestrator_inbox/JARVIS"
)
PROMPT_STUB_PREFIX = "SEAL_NERVES_RENDER_V1\n"
MISSION_PATTERN = re.compile(
    r"^SEAL_NERVES_RENDER_V1\n"
    r"mission_id=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})\n"
    r"claim_id=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})\n$"
)
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_.:@/+~-]{1,160}$")
TOOL_USE_PATTERN = re.compile(r"^[A-Za-z0-9_.:@/+~-]{1,200}$")
EXPECTED_INPUT_KEYS = {
    "description",
    "subagent_type",
    "name",
    "prompt",
    "run_in_background",
}
PROTECTED_MARKERS = frozenset(
    {
        NATIVE_SPAWN_DESCRIPTION,
        NATIVE_SPAWN_NAME,
        NATIVE_PROFILE,
    }
)


class PromptHookError(RuntimeError):
    """Raised when a protected Agent launch cannot be authenticated."""


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"nerves_agent_prompt_denied:{reason}",
        }
    }


def _allow(updated_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": (
                "Authenticated JARVIS NERVES mission; canonical prompt rendered "
                "from hash-bound owner-only evidence."
            ),
            "updatedInput": dict(updated_input),
        }
    }


def _looks_protected(tool_input: Mapping[str, Any]) -> bool:
    values = {
        tool_input.get("description"),
        tool_input.get("name"),
        tool_input.get("subagent_type"),
    }
    prompt = tool_input.get("prompt")
    return bool(values & PROTECTED_MARKERS) or (
        isinstance(prompt, str) and prompt.startswith(PROMPT_STUB_PREFIX)
    )


def _canonical_prompt(
    mission_id: str, claim_id: str, *, state_path: Path
) -> tuple[str, dict[str, str]]:
    record = _state_delivery(state_path, mission_id)
    if record.get("status") != "claimed":
        raise PromptHookError("mission_not_claimed")
    claim = record.get("claim")
    if (
        not isinstance(claim, dict)
        or claim.get("mission_id") != mission_id
        or claim.get("claim_id") != claim_id
    ):
        raise PromptHookError("claim_missing")
    if claim.get("worker_kind") != "native_subagent":
        raise PromptHookError("claim_worker_kind_invalid")

    handoff_path = Path(str(record.get("inbox_path", "")))
    try:
        handoff, _ = _validate_existing_handoff(
            handoff_path, str(record["handoff_sha256"])
        )
        bindings = handoff["bindings"]
        evidence_raw, _ = _secure_read(
            Path(str(bindings["evidence_path"])),
            label="prompt_hook_evidence",
            max_bytes=MAX_RESULT_BYTES,
            required_mode=0o600,
        )
        provenance_raw, _ = _secure_read(
            Path(str(bindings["provenance_path"])),
            label="prompt_hook_provenance",
            max_bytes=MAX_RESULT_BYTES,
            required_mode=0o600,
        )
    except (HandoffError, KeyError, TypeError) as exc:
        raise PromptHookError("handoff_untrusted") from exc

    if hashlib.sha256(evidence_raw).hexdigest() != bindings.get("evidence_sha256"):
        raise PromptHookError("evidence_digest_mismatch")
    if (
        hashlib.sha256(provenance_raw).hexdigest()
        != bindings.get("provenance_sha256")
    ):
        raise PromptHookError("provenance_digest_mismatch")
    try:
        prompt = build_native_reasoner_prompt(
            mission_id, evidence_raw, provenance_raw
        )
    except (NativeReceiptError, UnicodeDecodeError) as exc:
        raise PromptHookError("canonical_prompt_invalid") from exc
    try:
        profile_raw, profile_stat = _secure_read(
            NATIVE_PROFILE_PATH,
            label="prompt_hook_profile",
            max_bytes=262_144,
            required_mode=None,
        )
    except HandoffError as exc:
        raise PromptHookError("profile_untrusted") from exc
    if stat.S_IMODE(profile_stat.st_mode) & (stat.S_IWGRP | stat.S_IWOTH):
        raise PromptHookError("profile_writable_by_others")
    return prompt, {
        "claim_id": claim_id,
        "evidence_sha256": str(bindings["evidence_sha256"]),
        "handoff_sha256": str(record["handoff_sha256"]),
        "profile_sha256": hashlib.sha256(profile_raw).hexdigest(),
        "provenance_sha256": str(bindings["provenance_sha256"]),
    }


def _validate_caller(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    if payload.get("hook_event_name") != "PreToolUse":
        raise PromptHookError("hook_event_mismatch")
    cwd = payload.get("cwd")
    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    tool_use_id = payload.get("tool_use_id")
    if not isinstance(cwd, str):
        raise PromptHookError("cwd_missing")
    try:
        if Path(cwd).resolve(strict=True) != ROOT.resolve(strict=True):
            raise PromptHookError("cwd_mismatch")
    except (OSError, RuntimeError) as exc:
        raise PromptHookError("cwd_untrusted") from exc
    if not isinstance(session_id, str) or not SESSION_PATTERN.fullmatch(session_id):
        raise PromptHookError("session_id_invalid")
    if not isinstance(tool_use_id, str) or not TOOL_USE_PATTERN.fullmatch(tool_use_id):
        raise PromptHookError("tool_use_id_invalid")
    if not isinstance(transcript_path, str):
        raise PromptHookError("transcript_path_missing")
    transcript = Path(transcript_path)
    if (
        not transcript.is_absolute()
        or transcript.name != f"{session_id}.jsonl"
        or "subagents" in transcript.parts
    ):
        raise PromptHookError("parent_transcript_invalid")
    try:
        transcript_stat = transcript.stat()
    except OSError as exc:
        raise PromptHookError("parent_transcript_unreadable") from exc
    if (
        not stat.S_ISREG(transcript_stat.st_mode)
        or transcript_stat.st_uid != os.getuid()
        or stat.S_IMODE(transcript_stat.st_mode) != 0o600
    ):
        raise PromptHookError("parent_transcript_untrusted")
    return session_id, transcript_path, tool_use_id


def _write_audit(
    mission_id: str,
    *,
    claim_id: str,
    session_id: str,
    transcript_path: str,
    tool_use_id: str,
    original_input: Mapping[str, Any],
    rendered_prompt: str,
    bindings: Mapping[str, str],
    audit_dir: Path,
) -> None:
    audit_path = audit_dir / f"{mission_id}.prompt-render.json"
    audit = {
        "schema": "seal.nerves.native-prompt-render.v1",
        "mission_id": mission_id,
        "claim_id": claim_id,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "tool_use_id": tool_use_id,
        "original_input_sha256": hashlib.sha256(
            _canonical_bytes(dict(original_input))
        ).hexdigest(),
        "rendered_prompt_sha256": hashlib.sha256(
            rendered_prompt.encode("utf-8")
        ).hexdigest(),
        "rendered_prompt_bytes": len(rendered_prompt.encode("utf-8")),
        **bindings,
    }
    raw = _canonical_bytes(audit) + b"\n"
    if audit_path.exists() and not audit_path.is_symlink():
        try:
            existing, _ = _secure_read(
                audit_path,
                label="prompt_hook_audit",
                max_bytes=262_144,
                required_mode=0o600,
            )
        except HandoffError as exc:
            raise PromptHookError("audit_untrusted") from exc
        if existing != raw:
            raise PromptHookError("audit_replay_mismatch")
        return
    if audit_path.exists() or audit_path.is_symlink():
        raise PromptHookError("audit_path_untrusted")
    try:
        _secure_create(audit_path, raw, mode=0o600)
    except HandoffError as exc:
        raise PromptHookError("audit_create_failed") from exc


def process_hook(
    payload: Mapping[str, Any],
    *,
    state_path: Path = DEFAULT_STATE,
    audit_dir: Path = DEFAULT_AUDIT_DIR,
) -> dict[str, Any] | None:
    """Return a Claude hook decision or ``None`` for unrelated tools."""

    if payload.get("tool_name") != "Agent":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    if not _looks_protected(tool_input):
        return None
    try:
        session_id, transcript_path, tool_use_id = _validate_caller(payload)
        if set(tool_input) != EXPECTED_INPUT_KEYS:
            raise PromptHookError("input_shape_mismatch")
        if tool_input.get("description") != NATIVE_SPAWN_DESCRIPTION:
            raise PromptHookError("description_mismatch")
        if tool_input.get("subagent_type") != NATIVE_PROFILE:
            raise PromptHookError("profile_mismatch")
        if tool_input.get("name") != NATIVE_SPAWN_NAME:
            raise PromptHookError("name_mismatch")
        if tool_input.get("run_in_background") is not True:
            raise PromptHookError("background_required")
        prompt_stub = tool_input.get("prompt")
        if not isinstance(prompt_stub, str):
            raise PromptHookError("prompt_stub_invalid")
        match = MISSION_PATTERN.fullmatch(prompt_stub)
        if match is None:
            raise PromptHookError("prompt_stub_invalid")
        mission_id = str(uuid.UUID(match.group(1)))
        claim_id = str(uuid.UUID(match.group(2)))
        canonical, bindings = _canonical_prompt(
            mission_id, claim_id, state_path=state_path
        )
        updated = dict(tool_input)
        updated["prompt"] = canonical
        _write_audit(
            mission_id,
            claim_id=claim_id,
            session_id=session_id,
            transcript_path=transcript_path,
            tool_use_id=tool_use_id,
            original_input=tool_input,
            rendered_prompt=canonical,
            bindings=bindings,
            audit_dir=audit_dir,
        )
        decision = _allow(updated)
        decision["hookSpecificOutput"]["additionalContext"] = (
            "SEAL NERVES renderer v1: "
            f"mission={mission_id}; "
            f"prompt_sha256={hashlib.sha256(canonical.encode('utf-8')).hexdigest()}; "
            f"tool_use_id={tool_use_id}"
        )
        return decision
    except (PromptHookError, NativeReceiptError, ValueError) as exc:
        return _deny(str(exc))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(json.dumps(_deny("hook_input_invalid"), separators=(",", ":")))
        return 0
    if not isinstance(payload, dict):
        print(json.dumps(_deny("hook_input_invalid"), separators=(",", ":")))
        return 0
    decision = process_hook(payload)
    if decision is not None:
        print(json.dumps(decision, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
