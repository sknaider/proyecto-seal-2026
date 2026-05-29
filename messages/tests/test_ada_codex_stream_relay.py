import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "ada_codex_stream_relay.py"
spec = importlib.util.spec_from_file_location("ada_codex_stream_relay", MODULE_PATH)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


def test_similar_response_matches_markdown_and_wording_variants():
    existing = (
        "Sí William, ya está mejor. Evidencia concreta: un solo proceso "
        "ada_codex_poller.py vivo, sin bridge headless visible."
    )
    candidate = (
        "Sí William, ya está mejor.\n\n"
        "Evidencia:\n"
        "- Un solo `messages/ada_codex_poller.py` vivo.\n"
        "- No aparece bridge headless activo."
    )

    assert relay._is_similar_response(existing, candidate)


def test_different_response_does_not_match():
    existing = "William, test recibido. ADA sigue respondiendo desde terminal visible."
    candidate = "William, 2 + 2 = 4."

    assert not relay._is_similar_response(existing, candidate)


def test_relay_delay_is_fast_for_terminal_stream_feel():
    assert relay.RELAY_DELAY == 1.5


def test_agent_message_posts_stream_preview(monkeypatch):
    posted = []
    relay._seen.clear()

    monkeypatch.setattr(relay, "_post_stream_to_webchat", lambda content, done=False: posted.append((content, done)) or True)

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message","message":"ADA responde en vivo"}}'
    )

    assert posted == [("ADA responde en vivo", False)]


def test_task_complete_closes_stream_and_queues_durable(monkeypatch):
    posted = []
    relay._seen.clear()
    relay._pending_relay.clear()

    monkeypatch.setattr(relay, "_post_stream_to_webchat", lambda content, done=False: posted.append((content, done)) or True)

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"Cierre ADA"}}'
    )

    assert posted == [("Cierre ADA", True)]
    assert relay._pending_relay and relay._pending_relay[0][0] == "Cierre ADA"
