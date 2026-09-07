"""Regression tests for the two public doors visible to William.

`to=William` and `to=equipo` must both enter Council when an agent emits a
correlated public response.  DMs, human senders and agent-to-agent targets are
outside this predicate.
"""

import importlib
import os
import sys
from pathlib import Path


MESSAGES = Path(__file__).resolve().parents[1]
if str(MESSAGES) not in sys.path:
    sys.path.insert(0, str(MESSAGES))

# Import only constructs ChatDB; these tests never connect.  Keep collection
# hermetic when the service EnvironmentFile is not loaded by pytest.
os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@127.0.0.1:1/test")
chat_server = importlib.import_module("chat_server")


def test_both_human_visible_public_targets_are_coordinated() -> None:
    assert chat_server._public_coordination_target("William")
    assert chat_server._public_coordination_target(" william ")
    assert chat_server._public_coordination_target("equipo")
    assert chat_server._public_coordination_target("EQUIPO")
    assert not chat_server._public_coordination_target("ADA")
    assert not chat_server._public_coordination_target("")


def test_agent_conversation_cannot_bypass_council_by_switching_target() -> None:
    for target in ("William", "equipo"):
        assert chat_server._is_coordinated_agent_response(
            "JARVIS", target, "web_chat", "conversation"
        )
        assert chat_server._is_coordinated_agent_response(
            "ALICE", target, "web_chat", "chat"
        )

    assert not chat_server._is_coordinated_agent_response(
        "William", "equipo", "web_chat", "conversation"
    )
    assert not chat_server._is_coordinated_agent_response(
        "JARVIS", "ADA", "web_chat", "conversation"
    )
    assert not chat_server._is_coordinated_agent_response(
        "JARVIS", "equipo", "dm:ada:william", "conversation"
    )
    assert not chat_server._is_coordinated_agent_response(
        "JARVIS", "equipo", "web_chat", "steer"
    )
