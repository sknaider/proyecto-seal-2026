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


def test_durable_split_preserves_every_character():
    original = ("línea con datos\n" * 900) + "FIN"
    chunks = relay._split_durable_message(original, limit=800)

    assert len(chunks) > 1
    assert "".join(chunks) == original
    assert all(len(chunk) <= 800 for chunk in chunks)


def test_durable_post_chunks_long_answer_without_truncation(monkeypatch):
    sent = []
    original = ("resultado verificable " * 700) + "CIERRE"
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(
        relay,
        "send_agent_message_sync",
        lambda agent, to, message, **kwargs: sent.append((message, kwargs))
        or {"ok": True, "id": f"api_{len(sent)}"},
    )

    assert relay._post_to_webchat(original, source_id="api_william_1")
    assert len(sent) > 1
    rebuilt = "".join(message.split("\n", 1)[1] for message, _ in sent)
    assert rebuilt == original
    assert all("_part_" in kwargs["idempotency_key"] for _, kwargs in sent)


def test_durable_team_reply_targets_the_calling_sibling(monkeypatch):
    recipients = []
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(
        relay,
        "send_agent_message_sync",
        lambda agent, to, message, **kwargs: recipients.append(to)
        or {"ok": True, "id": f"api_{len(recipients)}"},
    )

    assert relay._post_to_webchat(
        "Recibido, Alice.", source_id="api_alice_1", recipient="ALICE"
    )
    assert recipients == ["ALICE"]


def test_relay_delay_is_fast_for_terminal_stream_feel():
    assert relay.RELAY_DELAY == 0.4


def test_agent_message_posts_stream_preview(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", tmp_path / "active_task.json")
    (tmp_path / "active_task.json").write_text(
        '{"id":"chat_1","source":"ada_codex_poller","channel":"web_chat","chat_message_id":1}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": posted.append((content, done, channel)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message","message":"ADA responde en vivo"}}'
    )

    assert posted == [("ADA responde en vivo", False, "web_chat")]


def test_commentary_is_persisted_when_enabled_and_correlated(monkeypatch, tmp_path):
    streamed = []
    persisted = []
    relay._seen.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "PERSIST_COMMENTARY", True)
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", tmp_path / "active_task.json")
    (tmp_path / "active_task.json").write_text(
        '{"id":"chat_1","source":"ada_codex_poller","channel":"web_chat",'
        '"chat_message_id":110583,"response_source_id":"api_william_1"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": streamed.append((content, done, channel)) or True,
    )
    monkeypatch.setattr(
        relay,
        "_post_to_webchat",
        lambda content, channel="web_chat", source_id=None, message_kind="final", recipient="William":
            persisted.append((content, channel, source_id, message_kind)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message",'
        '"phase":"commentary","message":"Avance visible"}}'
    )

    assert streamed == [("Avance visible", False, "web_chat")]
    assert persisted == [("Avance visible", "web_chat", "api_william_1", "progress")]


def test_final_phase_is_not_persisted_as_commentary(monkeypatch, tmp_path):
    persisted = []
    relay._seen.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "PERSIST_COMMENTARY", True)
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", tmp_path / "active_task.json")
    (tmp_path / "active_task.json").write_text(
        '{"id":"chat_1","source":"ada_codex_poller","channel":"web_chat",'
        '"response_source_id":"api_william_1"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(relay, "_post_stream_to_webchat", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        relay,
        "_post_to_webchat",
        lambda *args, **kwargs: persisted.append((args, kwargs)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message",'
        '"phase":"final","message":"Cierre"}}'
    )

    assert persisted == []


def test_agent_message_terminal_only_by_default(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", False)
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", tmp_path / "active_task.json")

    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": posted.append((content, done, channel)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message","message":"ADA solo terminal"}}'
    )

    assert posted == []


def test_agent_message_respects_channel_allowlist(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ALLOWED_CHANNELS", {"dm:ada:william"})
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", tmp_path / "active_task.json")

    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": posted.append((content, done, channel)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message","message":"No debe ir a general"}}'
    )

    assert posted == []

    (tmp_path / "active_task.json").write_text(
        '{"id":"chat_1","source":"ada_codex_poller","channel":"dm:ada:william","chat_message_id":1}\n',
        encoding="utf-8",
    )
    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message","message":"Debe ir a DM"}}'
    )

    assert posted == [("Debe ir a DM", False, "dm:ada:william")]


def test_agent_message_exact_silent_is_not_posted(monkeypatch):
    posted = []
    relay._seen.clear()

    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": posted.append((content, done, channel)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message","message":"[SILENT]"}}'
    )

    assert posted == []


def test_task_complete_silent_closes_marker_without_publishing(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    relay._pending_relay.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)

    active = tmp_path / "active_task.json"
    responses = tmp_path / "responses"
    responses_jsonl = tmp_path / "responses.jsonl"
    active.write_text(
        '{"id":"chat_silent","source":"ada_codex_poller",'
        '"channel":"web_chat","chat_message_id":24}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", active)
    monkeypatch.setattr(relay, "BRIDGE_RESPONSES_DIR", responses)
    monkeypatch.setattr(relay, "BRIDGE_RESPONSES_JSONL", responses_jsonl)
    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda *args, **kwargs: posted.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        relay,
        "_post_to_webchat",
        lambda *args, **kwargs: posted.append((args, kwargs)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"task_complete",'
        '"last_agent_message":"[SILENT]"}}'
    )

    assert not active.exists()
    assert (responses / "chat_silent.json").exists()
    assert "[SILENT]" in (responses / "chat_silent.json").read_text()
    assert relay._pending_relay == []
    assert posted == []


def test_stream_post_redacts_secret_shaped_content(monkeypatch):
    captured = []
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=2):
        captured.append(req.data.decode())
        return Resp()

    monkeypatch.setattr(relay.urllib.request, "urlopen", fake_urlopen)

    assert relay._post_stream_to_webchat("client_secret ABC", done=False)
    assert "client_secret" not in captured[0]
    assert "[REDACTED]" in captured[0]


def test_stream_post_uses_requested_channel(monkeypatch):
    captured = []
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=2):
        captured.append(req.data.decode())
        return Resp()

    monkeypatch.setattr(relay.urllib.request, "urlopen", fake_urlopen)

    assert relay._post_stream_to_webchat("respuesta privada", done=False, channel="dm:ada:william")
    payload = relay.json.loads(captured[0])
    assert payload["channel"] == "dm:ada:william"
    assert payload["id"].startswith("ada_terminal_stream_dm_ada_william_")


def test_stream_post_blocked_by_channel_allowlist(monkeypatch):
    captured = []
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ALLOWED_CHANNELS", {"dm:ada:william"})

    def fake_urlopen(req, timeout=2):
        captured.append(req.data.decode())
        raise AssertionError("should not post disallowed channel")

    monkeypatch.setattr(relay.urllib.request, "urlopen", fake_urlopen)

    assert not relay._post_stream_to_webchat("respuesta publica", done=False, channel="web_chat")
    assert captured == []


def test_task_complete_closes_stream_and_queues_durable(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    relay._pending_relay.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", tmp_path / "active_task.json")
    monkeypatch.setattr(relay, "BRIDGE_RESPONSES_DIR", tmp_path / "responses")
    monkeypatch.setattr(relay, "BRIDGE_RESPONSES_JSONL", tmp_path / "responses.jsonl")
    (tmp_path / "active_task.json").write_text(
        '{"id":"chat_1","source":"ada_codex_poller","channel":"web_chat","chat_message_id":1}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": posted.append((content, done, channel)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"Cierre ADA"}}'
    )

    assert posted == [("Cierre ADA", True, "web_chat")]
    assert relay._pending_relay and relay._pending_relay[0][0] == "Cierre ADA"
    assert relay._pending_relay[0][2] == "web_chat"
    assert relay._pending_relay[0][3] == "db_1"


def test_task_complete_terminal_only_records_without_posting(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    relay._pending_relay.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", False)

    active = tmp_path / "active_task.json"
    responses = tmp_path / "responses"
    responses_jsonl = tmp_path / "responses.jsonl"
    active.write_text(
        '{"id":"bridge_task_terminal","source":"ada_codex_poller","channel":"web_chat","chat_message_id":22}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", active)
    monkeypatch.setattr(relay, "BRIDGE_RESPONSES_DIR", responses)
    monkeypatch.setattr(relay, "BRIDGE_RESPONSES_JSONL", responses_jsonl)
    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": posted.append((content, done, channel)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"Solo terminal"}}'
    )

    assert posted == []
    assert relay._pending_relay == []
    payload = relay.json.loads(
        (responses / "bridge_task_terminal.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "completed"
    assert payload["published"] is False
    assert active.exists()


def test_task_complete_records_bridge_response(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    relay._pending_relay.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)

    active = tmp_path / "active_task.json"
    responses = tmp_path / "responses"
    responses_jsonl = tmp_path / "responses.jsonl"
    active.write_text(
        '{"id":"bridge_task_1","source":"ada_codex_poller","channel":"dm:ada:william","chat_message_id":23}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", active)
    monkeypatch.setattr(relay, "BRIDGE_RESPONSES_DIR", responses)
    monkeypatch.setattr(relay, "BRIDGE_RESPONSES_JSONL", responses_jsonl)
    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": posted.append((content, done, channel)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"Respuesta para Windows"}}'
    )

    assert active.exists()
    completed = relay.json.loads(
        (responses / "bridge_task_1.json").read_text(encoding="utf-8")
    )
    assert completed["status"] == "completed"
    assert completed["published"] is False
    assert posted == [("Respuesta para Windows", True, "dm:ada:william")]
    assert relay._pending_relay[0][2] == "dm:ada:william"
    task = relay._pending_relay[0][5]
    assert relay._mark_task_delivered(
        task,
        api_ids=["api_ada_1"],
        db_ids=[991],
        idempotency_keys=["ada_final_1"],
    )
    assert not active.exists()
    payload = relay.json.loads((responses / "bridge_task_1.json").read_text())
    assert payload["status"] == "delivered"
    assert payload["published"] is True
    assert payload["delivered"] is True
    assert payload["db_id"] == 991
    assert (responses / "bridge_task_1.json").stat().st_mode & 0o077 == 0
    assert responses_jsonl.stat().st_mode & 0o077 == 0


def test_relay_fails_closed_without_poller_task(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    relay._pending_relay.clear()
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": posted.append((content, done, channel)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message","message":"Terminal privado"}}'
    )
    relay._process_line(
        '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"Terminal privado"}}'
    )

    assert posted == []
    assert relay._pending_relay == []


def test_direct_terminal_turn_mirrors_only_to_private_dm(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    relay._pending_relay.clear()
    relay._direct_terminal_turn = None
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "DIRECT_TERMINAL_DM_ENABLED", True)
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William":
            posted.append((content, done, channel, recipient)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"user_message","message":"William escribe en terminal"}}'
    )
    relay._process_line(
        '{"type":"event_msg","payload":{"type":"agent_message","message":"Avance ADA"}}'
    )
    relay._process_line(
        '{"type":"event_msg","payload":{"type":"task_complete",'
        '"turn_id":"turn-terminal-1","last_agent_message":"Cierre ADA"}}'
    )

    assert posted == [
        ("Avance ADA", False, "dm:ada:william", "William"),
        ("Cierre ADA", True, "dm:ada:william", "William"),
    ]
    assert relay._pending_relay[0][2:5] == (
        "dm:ada:william", "terminal_turn-terminal-1", "William"
    )
    assert relay._direct_terminal_turn is None


def test_direct_terminal_mode_does_not_compete_with_poller_task(monkeypatch, tmp_path):
    relay._direct_terminal_turn = {"message": "stale"}
    monkeypatch.setattr(relay, "DIRECT_TERMINAL_DM_ENABLED", True)
    active = tmp_path / "active_task.json"
    active.write_text(
        '{"id":"chat_1","source":"ada_codex_poller","channel":"dm:ada:william",'
        '"chat_message_id":111782}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", active)

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"user_message","message":"DM inyectado"}}'
    )

    assert relay._direct_terminal_turn is None


def test_direct_terminal_recovery_finds_only_unfinished_turn(monkeypatch, tmp_path):
    monkeypatch.setattr(relay, "DIRECT_TERMINAL_DM_ENABLED", True)
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        '\n'.join([
            '{"type":"event_msg","payload":{"type":"user_message","message":"turno viejo"}}',
            '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"cerrado"}}',
            '{"timestamp":"2026-07-16T20:40:00Z","type":"event_msg",'
            '"payload":{"type":"user_message","message":"turno abierto"}}',
        ]) + '\n',
        encoding="utf-8",
    )

    recovered = relay._recover_open_direct_turn(rollout)

    assert recovered == {
        "message": "turno abierto",
        "started_at": "2026-07-16T20:40:00Z",
    }


def test_relay_rejects_untrusted_task_source(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    relay._pending_relay.clear()
    active = tmp_path / "active_task.json"
    active.write_text(
        '{"id":"manual_1","source":"manual_terminal","channel":"web_chat","chat_message_id":99}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", active)
    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda content, done=False, channel="web_chat", recipient="William": posted.append((content, done, channel)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"No publicar"}}'
    )

    assert posted == []
    assert relay._pending_relay == []
    assert active.exists()


def test_relay_rejects_pending_unacknowledged_route(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    active.write_text(
        '{"id":"chat_pending","source":"ada_codex_poller",'
        '"status":"pending_submit","channel":"dm:ada:william",'
        '"chat_message_id":111609}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", active)

    assert relay._validated_active_task() is None


def test_task_complete_must_match_committed_turn_id(monkeypatch, tmp_path):
    posted = []
    relay._seen.clear()
    relay._pending_relay.clear()
    active = tmp_path / "active_task.json"
    active.write_text(
        '{"id":"chat_dm","source":"ada_codex_poller","status":"active",'
        '"turn_id":"turn-correct","channel":"dm:ada:william",'
        '"chat_message_id":111609}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(relay, "WEBCHAT_RELAY_ENABLED", True)
    monkeypatch.setattr(relay, "BRIDGE_ACTIVE_TASK_FILE", active)
    monkeypatch.setattr(
        relay,
        "_post_stream_to_webchat",
        lambda *args, **kwargs: posted.append((args, kwargs)) or True,
    )

    relay._process_line(
        '{"type":"event_msg","payload":{"type":"task_complete",'
        '"turn_id":"turn-wrong","last_agent_message":"No desviar"}}'
    )

    assert active.exists()
    assert posted == []
    assert relay._pending_relay == []
