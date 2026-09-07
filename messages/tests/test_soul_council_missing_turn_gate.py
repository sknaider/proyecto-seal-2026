import importlib
import sys
from pathlib import Path


MESSAGES = Path(__file__).resolve().parents[1]
if str(MESSAGES) not in sys.path:
    sys.path.insert(0, str(MESSAGES))

chat_server = importlib.import_module("chat_server")


def test_enforce_rejects_agent_or_nonexistent_source_without_council_turn():
    assert chat_server._coordination_requires_turn_rejection(
        "ENFORCE",
        is_agent_reply=True,
        in_reply_to="api_ada_source",
        council_turn_found=False,
        legacy_human_source=False,
    )


def test_enforce_preserves_legacy_verified_human_source_for_response_lease():
    assert not chat_server._coordination_requires_turn_rejection(
        "ENFORCE",
        is_agent_reply=True,
        in_reply_to="api_william_legacy",
        council_turn_found=False,
        legacy_human_source=True,
    )


def test_shadow_does_not_enforce_missing_turn_gate():
    assert not chat_server._coordination_requires_turn_rejection(
        "SHADOW",
        is_agent_reply=True,
        in_reply_to="api_ada_source",
        council_turn_found=False,
        legacy_human_source=False,
    )
