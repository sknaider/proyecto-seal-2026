import os
from pathlib import Path
import sys


MESSAGES_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MESSAGES_DIR))
# ChatDB only validates that a DSN exists at import; these tests never connect.
os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@127.0.0.1/test")

import chat_server  # noqa: E402


def _routes(sender: str, recipient: str) -> set[Path]:
    chat_server.DIR = MESSAGES_DIR
    chat_server.LOG_ADA = MESSAGES_DIR / "terminal_log.jsonl"
    chat_server._AGENT_LOG["ADA"] = chat_server.LOG_ADA
    chat_server._ROUTING_CONFIG = {}
    chat_server._ROUTING_CHANNEL_PATHS = {}
    chat_server._load_routing_config()
    return set(chat_server._get_log_paths(sender, recipient))


def _direct_destinations(recipient: str) -> set[str]:
    chat_server.DIR = MESSAGES_DIR
    chat_server._ROUTING_CONFIG = {}
    chat_server._ROUTING_CHANNEL_PATHS = {}
    chat_server._load_routing_config()
    matching = [
        route
        for route in chat_server._ROUTING_CONFIG["routes"]
        if route.get("match") == {"agent_to": recipient}
    ]
    assert len(matching) == 1
    return set(matching[0]["deliver_to"])


def test_message_directed_to_ada_reaches_her_terminal_feed():
    routes = _routes("William", "ADA")
    assert MESSAGES_DIR / "terminal_log.jsonl" in routes
    assert MESSAGES_DIR / "vscode_commands.jsonl" not in routes
    assert _direct_destinations("ADA") == {"terminal_log", "trigger_ada", "web_api"}


def test_message_directed_to_other_agent_does_not_leak_to_ada_terminal_feed():
    routes = _routes("William", "JARVIS")
    assert MESSAGES_DIR / "terminal_log.jsonl" not in routes
    assert _direct_destinations("JARVIS") == {"vscode_commands", "web_api"}
