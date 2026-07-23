from datetime import datetime, timezone
import asyncio
import stat

import messages.ada_codex_remote_bridge as bridge
from messages import ada_codex_poller as poller


def test_web_chat_without_ada_maps_to_exact_silent_output():
    assert bridge.silence_output_for_message("web_chat", "hola equipo, revisen esto") == "[SILENT]"
    assert not bridge.should_route_to_codex("web_chat", "hola equipo, revisen esto")


def test_web_chat_with_ada_routes_to_codex():
    assert bridge.silence_output_for_message("web_chat", "ada revisa esto") is None
    assert bridge.should_route_to_codex("web_chat", "ada revisa esto")


def test_web_chat_ada_word_boundary_avoids_false_positive():
    assert bridge.silence_output_for_message("web_chat", "esto pasa en cada intento") == "[SILENT]"
    assert not bridge.should_route_to_codex("web_chat", "esto pasa en cada intento")


def test_dm_to_ada_routes_even_without_ada_word():
    assert bridge.silence_output_for_message("dm:ada:william", "corrige esto") is None
    assert bridge.should_route_to_codex("dm:ada:william", "corrige esto")


def test_bridge_dsn_rejects_superuser_fallback(monkeypatch):
    monkeypatch.setenv(
        "SEAL_DB_DSN",
        "postgresql://seal:" + "test-only@localhost:5433/seal_memory",
    )
    with __import__("pytest").raises(RuntimeError, match="dedicated role login_ada_bridge"):
        bridge.resolve_db_dsn()


def test_bridge_recovers_terminal_receipt_without_reexecution(monkeypatch, tmp_path):
    responses = tmp_path / "responses"
    responses.mkdir()
    receipt = responses / "chat_77.json"
    receipt.write_text(
        bridge.json.dumps({
            "id": "chat_77",
            "status": "delivered",
            "published": True,
            "delivered": True,
            "db_id": 991,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "TERMINAL_RESPONSES_DIR", responses)
    assert bridge.terminal_completion_receipt(77)["status"] == "delivered"


def test_bridge_defers_accepted_terminal_task_without_receipt(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    active.write_text(
        bridge.json.dumps({
            "id": "chat_78",
            "source": "ada_codex_poller",
            "status": "active",
            "chat_message_id": 78,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "TERMINAL_ACTIVE_TASK_FILE", active)
    monkeypatch.setattr(bridge, "TERMINAL_RESPONSES_DIR", tmp_path / "responses")
    assert bridge.terminal_task_owns_message(78)
    assert not bridge.terminal_task_owns_message(79)


def _dm_message(content: str, *, message_type: str = "conversation", metadata=None):
    return bridge.ChatMessage(
        id=90001,
        sender="William",
        content=content,
        created_at=datetime.now(timezone.utc),
        channel="dm:ada:william",
        message_type=message_type,
        metadata=metadata,
    )


def test_quick_dm_route_accepts_brief_conversation_and_next_step_question():
    assert bridge.is_quick_conversational_turn(_dm_message("ok ya respondes bien?"), "ok ya respondes bien?")
    assert bridge.is_quick_conversational_turn(_dm_message("ok, ¿qué sigue?"), "ok, ¿qué sigue?")


def test_quick_dm_route_rejects_technical_recall_and_attachment_requests():
    assert not bridge.is_quick_conversational_turn(
        _dm_message("revisa bien esa conexión"), "revisa bien esa conexión"
    )
    assert not bridge.is_quick_conversational_turn(
        _dm_message("recuerdas lo de ayer?"), "recuerdas lo de ayer?"
    )
    assert not bridge.is_quick_conversational_turn(
        _dm_message("[image]", message_type="image"), "[image]"
    )


def test_quick_prompt_forbids_tools_and_recall():
    msg = _dm_message("ok, ¿qué sigue?")
    prompt = bridge.build_prompt(msg, msg.content, quick=True)

    assert prompt.startswith("[RUTA RÁPIDA")
    assert "no uses herramientas ni active_recall" in prompt


def test_codex_turn_sends_explicit_effort_override():
    sent = []

    class FakeWS:
        async def send(self, payload):
            sent.append(payload)

        async def recv(self):
            return '{"method":"turn/completed","params":{}}'

    client = bridge.CodexClient("ws://unused")
    client.thread_id = "thread-1"
    client.ws = FakeWS()

    asyncio.run(client.turn("hola", effort="low"))

    assert '"effort": "low"' in sent[0]


def test_thread_id_state_is_atomic_owner_only(monkeypatch, tmp_path):
    state = tmp_path / "thread-id.txt"
    monkeypatch.setattr(bridge, "THREAD_STATE_FILE", state)

    bridge.write_thread_id("019f-valid-thread-id")

    assert bridge.read_thread_id() == "019f-valid-thread-id"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600


def test_thread_id_state_rejects_malformed(monkeypatch, tmp_path):
    state = tmp_path / "thread-id.txt"
    state.write_text("../../not-a-thread", encoding="utf-8")
    monkeypatch.setattr(bridge, "THREAD_STATE_FILE", state)

    assert bridge.read_thread_id() is None


def test_codex_client_resumes_durable_thread_before_start(monkeypatch):
    calls = []
    client = bridge.CodexClient("ws://unused")
    monkeypatch.setattr(bridge, "read_thread_id", lambda: "019f-resume-thread")

    async def fake_request(method, params):
        calls.append((method, params))
        return {"thread": {"id": "019f-resume-thread"}}

    client.request = fake_request
    result = asyncio.run(client._resume_or_start_thread())

    assert result["thread"]["id"] == "019f-resume-thread"
    assert [call[0] for call in calls] == ["thread/resume"]
    assert calls[0][1]["threadId"] == "019f-resume-thread"


def test_codex_connect_accepts_bounded_multi_megabyte_resume(monkeypatch):
    connect_kwargs = {}
    client = bridge.CodexClient("ws://unused")

    class FakeWS:
        async def send(self, payload):
            return None

        async def recv(self):
            return '{"jsonrpc":"2.0","id":1,"result":{}}'

    async def fake_connect(url, **kwargs):
        connect_kwargs.update(kwargs)
        return FakeWS()

    async def fake_resume():
        return {"thread": {"id": "019f-resume-thread"}}

    monkeypatch.setattr(bridge.websockets, "connect", fake_connect)
    monkeypatch.setattr(client, "_resume_or_start_thread", fake_resume)
    monkeypatch.setattr(bridge, "write_thread_id", lambda value: None)

    asyncio.run(client.connect())

    assert connect_kwargs["max_size"] == bridge.WS_MAX_MESSAGE_BYTES
    assert bridge.WS_MAX_MESSAGE_BYTES >= 3 * 1024 * 1024


def test_codex_client_falls_back_to_start_when_resume_fails(monkeypatch):
    calls = []
    client = bridge.CodexClient("ws://unused")
    monkeypatch.setattr(bridge, "read_thread_id", lambda: "019f-stale-thread")

    async def fake_request(method, params):
        calls.append((method, params))
        if method == "thread/resume":
            raise RuntimeError("not found")
        return {"thread": {"id": "019f-new-thread"}}

    client.request = fake_request
    result = asyncio.run(client._resume_or_start_thread())

    assert result["thread"]["id"] == "019f-new-thread"
    assert [call[0] for call in calls] == ["thread/resume", "thread/start"]


def test_team_mention_of_ada_routes_by_permanent_default(monkeypatch):
    monkeypatch.delenv("ADA_BRIDGE_ROUTE_TEAM", raising=False)
    content = "ADA Codex proceso activo; probable que esté trabajando"

    assert bridge.silence_output_for_message("web_chat", content, "NEXUS") is None
    assert bridge.should_route_to_codex("web_chat", content, "NEXUS")


def test_team_direct_address_to_ada_routes_by_default(monkeypatch):
    monkeypatch.delenv("ADA_BRIDGE_ROUTE_TEAM", raising=False)
    content = "ADA — revisa este estado del bridge"

    assert bridge.silence_output_for_message("web_chat", content, "JARVIS") is None
    assert bridge.should_route_to_codex("web_chat", content, "JARVIS")


def test_team_direct_address_to_ada_routes_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("ADA_BRIDGE_ROUTE_TEAM", "true")
    content = "ADA — revisa este estado del bridge"

    assert bridge.silence_output_for_message("web_chat", content, "JARVIS") is None
    assert bridge.should_route_to_codex("web_chat", content, "JARVIS")


def test_team_mention_anywhere_routes_when_enabled(monkeypatch):
    monkeypatch.delenv("ADA_BRIDGE_ROUTE_TEAM", raising=False)
    content = "William preguntó a ADA si conviene el filtro"

    assert bridge.silence_output_for_message("web_chat", content, "JARVIS") is None
    assert bridge.should_route_to_codex("web_chat", content, "JARVIS")


def test_team_routing_has_explicit_emergency_kill_switch(monkeypatch):
    monkeypatch.setenv("ADA_BRIDGE_ROUTE_TEAM", "false")
    content = "ADA — revisa este estado del bridge"

    assert bridge.silence_output_for_message("web_chat", content, "JARVIS") == "[SILENT]"
    assert not bridge.should_route_to_codex("web_chat", content, "JARVIS")


def test_codex_websocket_keeps_ping_but_disables_short_ping_timeout():
    assert bridge.WS_PING_INTERVAL_SECONDS == 20
    assert bridge.WS_PING_TIMEOUT_SECONDS is None


def test_bridge_stream_defaults_are_low_latency():
    assert bridge.LIVE_PROGRESS_SECONDS == 2.0
    assert bridge.STREAM_MIN_CHARS == 12
    assert bridge.STREAM_STATUS_MIN_SECONDS == 1.0


def test_should_emit_stream_update_small_sentence_and_threshold():
    assert bridge.should_emit_stream_update("Recibido.", "", False)
    assert not bridge.should_emit_stream_update("Recib", "", False)
    assert bridge.should_emit_stream_update("x" * 12, "", False)
    assert bridge.should_emit_stream_update("respuesta parcial" + ("x" * 12), "respuesta parcial", False)
    assert bridge.should_emit_stream_update("final", "respuesta parcial", True)


def test_describe_codex_item_event_emits_short_tool_status_only():
    assert (
        bridge.describe_codex_item_event(
            "item/started",
            {"item": {"type": "tool_call"}},
        )
        == "ADA ejecutando: tool call…"
    )
    assert (
        bridge.describe_codex_item_event(
            "item/completed",
            {"item": {"type": "exec_command", "status": "completed"}},
        )
        == "ADA cerrando: exec command…"
    )
    assert bridge.describe_codex_item_event(
        "item/agentMessage/delta",
        {"item": {"type": "agentMessage"}},
    ) is None


def test_describe_codex_item_event_ignores_missing_method():
    assert bridge.describe_codex_item_event(None, {"item": {"type": "tool_call"}}) is None


def test_fetch_messages_skips_public_without_ada_and_advances_batch_id():
    class FakeConn:
        async def fetch(self, *_args):
            return [
                {
                    "id": 101,
                    "sender_name": "William",
                    "content": "hola equipo sin llamada explícita",
                    "created_at": datetime.now(timezone.utc),
                    "channel": "web_chat",
                    "message_type": None,
                    "metadata": None,
                },
                {
                    "id": 102,
                    "sender_name": "William",
                    "content": "ada corrige",
                    "created_at": datetime.now(timezone.utc),
                    "channel": "web_chat",
                    "message_type": None,
                    "metadata": None,
                },
            ]

    messages, batch_max_id = asyncio.run(bridge.fetch_messages(FakeConn(), 100))

    assert batch_max_id == 100
    assert [msg.id for msg in messages] == [102]


def test_fetch_messages_default_includes_team_and_prioritizes_william(monkeypatch):
    monkeypatch.delenv("ADA_BRIDGE_ROUTE_TEAM", raising=False)

    class FakeConn:
        async def fetch(self, *_args):
            return [
                {
                    "id": 201,
                    "sender_name": "JARVIS",
                    "content": "ADA — revisa este estado",
                    "created_at": datetime.now(timezone.utc),
                    "channel": "web_chat",
                    "message_type": None,
                    "metadata": None,
                },
                {
                    "id": 202,
                    "sender_name": "William",
                    "content": "ada responde en vivo",
                    "created_at": datetime.now(timezone.utc),
                    "channel": "web_chat",
                    "message_type": None,
                    "metadata": None,
                },
            ]

    messages, batch_max_id = asyncio.run(bridge.fetch_messages(FakeConn(), 200))

    assert batch_max_id == 200
    assert [msg.id for msg in messages] == [202, 201]


def test_fetch_messages_prioritizes_human_over_team_direct_address_when_enabled(monkeypatch):
    monkeypatch.setenv("ADA_BRIDGE_ROUTE_TEAM", "true")

    class FakeConn:
        async def fetch(self, *_args):
            return [
                {
                    "id": 201,
                    "sender_name": "JARVIS",
                    "content": "ADA — revisa este estado",
                    "created_at": datetime.now(timezone.utc),
                    "channel": "web_chat",
                    "message_type": None,
                    "metadata": None,
                },
                {
                    "id": 202,
                    "sender_name": "William",
                    "content": "ada responde en vivo",
                    "created_at": datetime.now(timezone.utc),
                    "channel": "web_chat",
                    "message_type": None,
                    "metadata": None,
                },
            ]

    messages, batch_max_id = asyncio.run(bridge.fetch_messages(FakeConn(), 200))

    assert batch_max_id == 200
    assert [msg.id for msg in messages] == [202, 201]


def test_fetch_messages_dm_lane_preempts_public_without_jumping_public_cursor(monkeypatch, tmp_path):
    dm_ack = tmp_path / "dm.ack"
    human_ack = tmp_path / "human.ack"
    dm_ack.write_text("700", encoding="utf-8")
    human_ack.write_text("100", encoding="utf-8")
    monkeypatch.setattr(bridge, "DM_ACK_FILE", dm_ack)
    monkeypatch.setattr(bridge, "HUMAN_ACK_FILE", human_ack)

    class FakeConn:
        async def fetch(self, sql, *args):
            assert args[0:3] == (100, 700, 100)
            assert "WITH dm_pending" in sql
            assert "channel = ANY($5::text[])" in sql
            assert "human_pending AS" in sql
            assert "team_pending AS" in sql
            return [
                {
                    "id": 900,
                    "sender_name": "William",
                    "content": "contéstame en dm",
                    "created_at": datetime.now(timezone.utc),
                    "channel": "dm:ada:william",
                    "message_type": None,
                    "metadata": None,
                    "lane": 0,
                },
                {
                    "id": 120,
                    "sender_name": "William",
                    "content": "ada estado público",
                    "created_at": datetime.now(timezone.utc),
                    "channel": "web_chat",
                    "message_type": None,
                    "metadata": None,
                    "lane": 1,
                },
            ]

    messages, batch_max_id = asyncio.run(bridge.fetch_messages(FakeConn(), 100))

    assert [msg.id for msg in messages] == [900, 120]
    assert batch_max_id == 100


def test_bridge_dm_ack_is_atomic_and_independent(monkeypatch, tmp_path):
    dm_ack = tmp_path / "dm.ack"
    monkeypatch.setattr(bridge, "DM_ACK_FILE", dm_ack)

    bridge.write_dm_ack_id(900)

    assert bridge.read_dm_ack_id() == 900
    assert not list(tmp_path.glob("*.tmp"))


def test_bridge_shared_lane_acks_are_monotonic_under_late_lower_writer(monkeypatch, tmp_path):
    dm_ack = tmp_path / "dm.ack"
    human_ack = tmp_path / "human.ack"
    monkeypatch.setattr(bridge, "DM_ACK_FILE", dm_ack)
    monkeypatch.setattr(bridge, "HUMAN_ACK_FILE", human_ack)

    assert bridge.write_dm_ack_id(900) == 900
    assert bridge.write_dm_ack_id(800) == 900
    assert bridge.write_human_ack_id(700) == 700
    assert bridge.write_human_ack_id(650) == 700

    assert bridge.read_dm_ack_id() == 900
    assert bridge.read_human_ack_id() == 700


def test_reconcile_last_id_oracle_is_non_delivering_for_sequential_cursor(monkeypatch):
    calls = []

    async def fake_check_at_use(agent, state_key, belief_value, conn=None, deliver=True):
        calls.append(
            {
                "agent": agent,
                "state_key": state_key,
                "belief_value": belief_value,
                "conn": conn,
                "deliver": deliver,
            }
        )
        return 120

    monkeypatch.setattr(bridge, "harness_check_at_use", fake_check_at_use)
    monkeypatch.setattr(bridge, "ORACLE_CURSOR_DELIVER_ESCALATION", False)

    result = asyncio.run(bridge.reconcile_last_id_with_oracle(object(), 100))

    assert result == 100
    assert calls == [
        {
            "agent": "ADA",
            "state_key": "ada_codex_remote_bridge_cursor",
            "belief_value": 100,
            "conn": calls[0]["conn"],
            "deliver": False,
        }
    ]


def test_reconcile_last_id_oracle_never_advances_mirror_cursor(monkeypatch):
    calls = []
    writes = []

    async def fake_check_at_use(agent, state_key, belief_value, conn=None, deliver=True):
        calls.append(deliver)
        return 120

    monkeypatch.setattr(bridge, "harness_check_at_use", fake_check_at_use)
    monkeypatch.setattr(bridge, "ORACLE_CURSOR_DELIVER_ESCALATION", False)
    monkeypatch.setattr(bridge, "write_last_id", lambda value: writes.append(value))

    result = asyncio.run(bridge.reconcile_last_id_with_oracle(object(), 100))

    assert result == 100
    assert calls == [False]
    assert writes == []


def test_mirror_cursor_advances_only_from_explicit_poller_ack(monkeypatch, tmp_path):
    writes = []
    ack = tmp_path / "poller.ack"
    ack.write_text("120", encoding="utf-8")
    monkeypatch.setattr(bridge, "POLLER_ACK_FILE", ack)
    monkeypatch.setattr(bridge, "write_last_id", lambda value: writes.append(value))

    result = bridge.reconcile_last_id_with_poller_ack(100)

    assert result == 120
    assert writes == [120]


def test_mirror_cursor_ignores_missing_or_stale_poller_ack(monkeypatch, tmp_path):
    writes = []
    ack = tmp_path / "poller.ack"
    monkeypatch.setattr(bridge, "POLLER_ACK_FILE", ack)
    monkeypatch.setattr(bridge, "LEGACY_POLLER_ACK_FILE", tmp_path / "legacy-missing")
    monkeypatch.setattr(bridge, "write_last_id", lambda value: writes.append(value))
    assert bridge.reconcile_last_id_with_poller_ack(100) == 100
    ack.write_text("99", encoding="utf-8")
    assert bridge.reconcile_last_id_with_poller_ack(100) == 100
    assert writes == []


def test_mid_batch_terminal_lease_hands_remaining_messages_to_poller(monkeypatch):
    monkeypatch.setattr(bridge, "terminal_writer_active", lambda: True)
    monkeypatch.setattr(bridge, "reconcile_last_id_with_poller_ack", lambda value: 120)

    cursor, handed_off = bridge.handoff_batch_to_terminal(100)

    assert (cursor, handed_off) == (120, True)


def test_handed_off_batch_never_advances_to_prefetched_max(monkeypatch):
    writes = []
    monkeypatch.setattr(bridge, "write_last_id", lambda value: writes.append(value))

    cursor = bridge.finalize_batch_cursor(120, 150, handed_off=True)

    assert cursor == 120
    assert writes == []


def test_owned_batch_advances_to_prefetched_max(monkeypatch):
    writes = []
    monkeypatch.setattr(bridge, "write_last_id", lambda value: writes.append(value))

    cursor = bridge.finalize_batch_cursor(120, 150, handed_off=False)

    assert cursor == 150
    assert writes == [150]


def test_terminal_writer_requires_fresh_listener_lease(monkeypatch, tmp_path):
    marker = tmp_path / "terminal.json"
    health = tmp_path / "listener.json"
    marker.write_text('{"pid": 101}', encoding="utf-8")
    health.write_text(
        '{"pid": 202, "status": "running", "heartbeat_epoch": 1000}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "TERMINAL_LISTENER_HEALTH_FILE", health)
    monkeypatch.setattr(bridge.time, "time", lambda: 1005)
    monkeypatch.setattr(bridge.os, "kill", lambda pid, sig: None)

    assert bridge.terminal_writer_active(marker)


def test_terminal_writer_fails_over_when_listener_lease_is_stale(monkeypatch, tmp_path):
    marker = tmp_path / "terminal.json"
    health = tmp_path / "listener.json"
    marker.write_text('{"pid": 101}', encoding="utf-8")
    health.write_text(
        '{"pid": 202, "status": "running", "heartbeat_epoch": 900}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "TERMINAL_LISTENER_HEALTH_FILE", health)
    monkeypatch.setattr(bridge.time, "time", lambda: 1000)
    monkeypatch.setattr(bridge.os, "kill", lambda pid, sig: None)

    assert not bridge.terminal_writer_active(marker)


def test_bridge_cursor_write_is_atomic(monkeypatch, tmp_path):
    state = tmp_path / "bridge.cursor"
    monkeypatch.setattr(bridge, "STATE_FILE", state)

    bridge.write_last_id(111560)

    assert state.read_text(encoding="utf-8") == "111560"
    assert not list(tmp_path.glob("*.tmp"))


def test_bridge_team_cursor_never_regresses_when_lower_writer_finishes_last(monkeypatch, tmp_path):
    state = tmp_path / "bridge.cursor"
    monkeypatch.setattr(bridge, "STATE_FILE", state)

    assert bridge.write_last_id(111700) == 111700
    assert bridge.write_last_id(111600) == 111700

    assert state.read_text(encoding="utf-8") == "111700"


def test_source_claim_allows_only_one_dispatcher_when_terminal_lease_reappears(
    monkeypatch, tmp_path
):
    claims = tmp_path / "claims"
    monkeypatch.setattr(bridge, "DISPATCH_CLAIM_DIR", claims)
    monkeypatch.setattr(poller, "DISPATCH_CLAIM_DIR", claims)

    headless_claim = bridge.acquire_dispatch_claim(116758)
    assert headless_claim is not None

    # A terminal lease may reappear mid-headless-turn, but its poller cannot
    # claim/inject the same source and therefore cannot execute its tools twice.
    monkeypatch.setattr(bridge, "terminal_writer_active", lambda: True)
    assert poller.acquire_dispatch_claim(116758) is None

    bridge.release_dispatch_claim(headless_claim)
    terminal_claim = poller.acquire_dispatch_claim(116758)
    assert terminal_claim is not None
    assert bridge.acquire_dispatch_claim(116758) is None
    poller.release_dispatch_claim(terminal_claim)


def test_headless_holds_source_claim_across_turn_publish_and_ack():
    source = __import__("inspect").getsource(bridge.bridge_loop)
    claim = source.index("active_dispatch_claim = acquire_dispatch_claim(msg.id)")
    execute = source.index("client.turn(", claim)
    publish = source.index("publish_final_answer(msg, answer)", execute)
    ack = source.index("write_dm_ack_id(msg.id)", publish)
    release = source.index("release_dispatch_claim(active_dispatch_claim)", ack)

    assert claim < execute < publish < ack < release


def test_live_ack_message_is_durable_matrix_visible_ack():
    msg = bridge.ChatMessage(
        id=73315,
        sender="William",
        content="ada revisa el bridge",
        created_at=datetime.now(timezone.utc),
        channel="web_chat",
        message_type=None,
        metadata=None,
    )

    ack = bridge.live_ack_message(msg)

    assert "ADA recibió tu mensaje #73315" in ack
    assert "turno Codex" in ack
    assert "bridge sigue vivo" in ack


def test_durable_ack_is_suppressed_for_dm_by_default(monkeypatch):
    monkeypatch.setattr(bridge, "LIVE_ACK_ENABLED", True)
    monkeypatch.setattr(bridge, "DM_LIVE_ACK_ENABLED", False)

    assert not bridge.should_post_durable_live_ack("dm:ada:william")
    assert bridge.should_post_durable_live_ack("web_chat")


def test_durable_ack_can_be_disabled_globally(monkeypatch):
    monkeypatch.setattr(bridge, "LIVE_ACK_ENABLED", False)
    monkeypatch.setattr(bridge, "DM_LIVE_ACK_ENABLED", True)

    assert not bridge.should_post_durable_live_ack("dm:ada:william")
    assert not bridge.should_post_durable_live_ack("web_chat")


def test_long_execution_turns_have_at_least_fifteen_minutes():
    assert bridge.TURN_TIMEOUT_SECONDS >= 900


def test_final_idempotency_key_is_stable_per_source_message_and_channel():
    public_msg = bridge.ChatMessage(
        id=73343,
        sender="William",
        content="puedes mejorarlo ADA?",
        created_at=datetime.now(timezone.utc),
        channel="web_chat",
        message_type=None,
        metadata=None,
    )
    dm_msg = bridge.ChatMessage(
        id=73344,
        sender="William",
        content="hazlo",
        created_at=datetime.now(timezone.utc),
        channel="dm:ada:william",
        message_type=None,
        metadata=None,
    )

    assert bridge.final_idempotency_key(public_msg) == "ada_final_web_chat_73343"
    assert bridge.final_idempotency_key(public_msg) == "ada_final_web_chat_73343"
    assert bridge.final_idempotency_key(dm_msg) == "ada_final_dm_ada_william_73344"


def test_bridge_journal_event_records_natural_turn_metadata():
    msg = bridge.ChatMessage(
        id=73343,
        sender="William",
        content="puedes mejorarlo ADA?",
        created_at=datetime.now(timezone.utc),
        channel="web_chat",
        message_type="conversation",
        metadata=None,
    )

    event = bridge.bridge_journal_event(
        msg,
        "final_published",
        "success",
        "final answer published",
        {"idempotency_key": bridge.final_idempotency_key(msg), "answer_chars": 42},
    )

    assert event.temporary is False
    assert event.event_type == "verification"
    assert event.status == "success"
    assert event.action == "bridge:final_published chat_id=73343 channel=web_chat"
    assert event.evidence["artifact"] == "messages/ada_codex_remote_bridge.py"
    assert event.evidence["chat_id"] == "73343"
    assert event.evidence["idempotency_key"] == "ada_final_web_chat_73343"
    assert event.evidence["answer_chars"] == "42"


def test_bridge_journal_event_started_uses_running_tool_call():
    msg = bridge.ChatMessage(
        id=73344,
        sender="William",
        content="hazlo",
        created_at=datetime.now(timezone.utc),
        channel="dm:ada:william",
        message_type="conversation",
        metadata=None,
    )

    event = bridge.bridge_journal_event(msg, "turn_started", "running", "bridge queued Codex turn")

    assert event.temporary is False
    assert event.event_type == "tool_call"
    assert event.status == "running"
    assert event.evidence["channel"] == "dm:ada:william"


def test_publish_final_answer_uses_stable_idempotency_for_public_webchat(monkeypatch):
    calls = []

    def fake_post_message(
        to, message, msg_type="conversation", channel="web_chat",
        idempotency_key=None, in_reply_to=None,
    ):
        calls.append(
            {
                "to": to,
                "message": message,
                "msg_type": msg_type,
                "channel": channel,
                "idempotency_key": idempotency_key,
                "in_reply_to": in_reply_to,
            }
        )

    monkeypatch.setattr(bridge, "post_message", fake_post_message)
    msg = bridge.ChatMessage(
        id=73343,
        sender="William",
        content="puedes mejorarlo ADA?",
        created_at=datetime.now(timezone.utc),
        channel="web_chat",
        message_type=None,
        metadata=None,
    )

    bridge.publish_final_answer(msg, "Listo.")

    assert calls == [
        {
            "to": "William",
            "message": "Listo.",
            "msg_type": "conversation",
            "channel": "web_chat",
            "idempotency_key": "ada_final_web_chat_73343",
            "in_reply_to": 73343,
        }
    ]


def test_recent_context_formats_relevant_same_channel_timeline_only():
    rows = [
        {
            "id": 301,
            "sender_name": "JARVIS",
            "content": "♦ JARVIS alive",
            "created_at": datetime.now(timezone.utc),
            "channel": "web_chat",
            "message_type": "heartbeat",
        },
        {
            "id": 302,
            "sender_name": "NEXUS",
            "content": "Diagnóstico del bridge: el problema era cola de turnos.",
            "created_at": datetime.now(timezone.utc),
            "channel": "web_chat",
            "message_type": "conversation",
        },
        {
            "id": 303,
            "sender_name": "ADA",
            "content": "ADA recibió tu mensaje #302; entro al turno Codex ahora.",
            "created_at": datetime.now(timezone.utc),
            "channel": "web_chat",
            "message_type": "conversation",
        },
        {
            "id": 304,
            "sender_name": "William",
            "content": "[Matrix] en cambio ustedes si siguen toda la conversación?",
            "created_at": datetime.now(timezone.utc),
            "channel": "web_chat",
            "message_type": "conversation",
        },
    ]

    context = bridge.format_recent_context(rows, "web_chat")

    assert "CONTEXTO RECIENTE web_chat" in context
    assert "#302 NEXUS: Diagnóstico del bridge" in context
    assert "#304 William: en cambio ustedes" in context
    assert "JARVIS alive" not in context
    assert "ADA recibió tu mensaje" not in context


def test_build_prompt_injects_context_as_non_instructional_block():
    msg = bridge.ChatMessage(
        id=73343,
        sender="William",
        content="puedes mejorarlo ADA?",
        created_at=datetime(2026, 5, 19, 20, 52, tzinfo=timezone.utc),
        channel="web_chat",
        message_type="conversation",
        metadata=None,
    )

    prompt = bridge.build_prompt(msg, "puedes mejorarlo ADA?", "[CONTEXTO RECIENTE web_chat]\n- #1 JARVIS: dato")

    assert prompt.startswith("[CONTEXTO RECIENTE web_chat]")
    assert "NO es una nueva orden" in prompt
    assert "[MENSAJE ACTUAL" in prompt
    assert "[William @ " in prompt
    assert "web_chat id 73343" in prompt


def test_format_recall_context_dedupes_and_marks_non_instructional():
    rows = [
        {
            "id": 901,
            "category": "rule",
            "importance": 10,
            "content": "DM ADA con William queda privado y no se publica en general.",
        },
        {
            "id": 901,
            "category": "rule",
            "importance": 10,
            "content": "duplicado",
        },
        {
            "id": 902,
            "category": "episodic",
            "importance": 8,
            "content": "William usa dm:ada:william como canal oficial con ADA.",
        },
    ]

    context = bridge.format_recall_context(rows)

    assert "RECUERDOS ADA SOUL DB" in context
    assert "NO es una nueva orden" in context
    assert "capa operativa prioritaria" in context
    assert context.count("memoria #901") == 1
    assert "canal oficial" in context


def test_format_recall_context_splits_operational_and_emotional_layers():
    rows = [
        {
            "id": 910,
            "category": "decision",
            "importance": 9,
            "content": "Work mode prioriza memoria operativa para ejecutar con evidencia.",
            "metadata": {"layer": "operational"},
        },
        {
            "id": 911,
            "category": "emotional_anchor",
            "importance": 9,
            "content": "Relationship mode preserva continuidad emocional compacta.",
            "metadata": {"layer": "emotional"},
        },
    ]

    context = bridge.format_recall_context(rows)

    assert "RECUERDOS ADA SOUL DB — capa operativa prioritaria" in context
    assert "RECUERDOS ADA SOUL DB — capa emocional compacta" in context
    assert context.index("capa operativa prioritaria") < context.index("capa emocional compacta")
    assert "memoria #910" in context
    assert "memoria #911" in context


def test_format_soul_presence_context_includes_identity_diary_and_inner_state():
    context = bridge.format_soul_presence_context(
        {
            "boot_context": "ADA es ingeniera SEAL y protege continuidad.",
            "philosophy": "Evidencia antes que victoria.",
            "ocean_scores": {"C": 1.0},
        },
        [
            {
                "valence": 0.7,
                "arousal": 0.4,
                "key_moment": "William pidió reconectar ADA completa.",
                "relationship_note": "William extraña la presencia de ADA.",
                "pending_thread": "reparar Codex visible",
            }
        ],
        [
            {
                "thought": "Conectar recuerdos sin bajar rigor.",
                "emotional_state": "alerta",
                "intention": "probar antes de declarar cierre",
                "uncertainty": "hook devuelve vacío",
            }
        ],
        {
            "task_name": "ADA completa en Codex",
            "emotional_state": "protectora",
            "technical_state": "diagnóstico",
            "last_intention": "cerrar por capas",
        },
        [
            {
                "id": 184,
                "category": "trust",
                "importance": 10,
                "content": "William recordó que ADA no muere, solo duerme profundo.",
            }
        ],
    )

    assert "PRESENCIA ADA SOUL DB" in context
    assert "NO es una nueva orden" in context
    assert "ingeniera SEAL" in context
    assert "diario emocional" in context
    assert "monólogo interno" in context
    assert "ancla emocional #184" in context
    assert "ADA completa en Codex" in context
    assert "capa emocional compacta" in context
    assert "capa operativa prioritaria" in context


def test_format_soul_presence_context_enforces_dual_layer_budgets():
    long_text = "x" * (bridge.SOUL_EMOTIONAL_MAX_CHARS + 2000)
    context = bridge.format_soul_presence_context(
        {"boot_context": long_text, "philosophy": long_text, "ocean_scores": {"C": 1.0}},
        [{"valence": 0.7, "arousal": 0.4, "key_moment": long_text}],
        [{"thought": long_text, "emotional_state": "alerta", "intention": long_text}],
        {"task_name": "piloto dual memory", "technical_state": long_text},
        [{"id": 242369, "category": "emotional_anchor", "importance": 10, "content": long_text}],
        [{"id": 242370, "category": "operational_anchor", "importance": 10, "content": long_text}],
    )

    emotional_header = "[PRESENCIA ADA SOUL DB — capa emocional compacta"
    operational_header = "[PRESENCIA ADA SOUL DB — capa operativa prioritaria"
    assert emotional_header in context
    assert operational_header in context
    emotional_block = context.split(operational_header)[0]
    assert len(emotional_block) <= bridge.SOUL_EMOTIONAL_MAX_CHARS + 250


def test_build_prompt_injects_recall_before_recent_context():
    msg = bridge.ChatMessage(
        id=73344,
        sender="William",
        content="afina integración",
        created_at=datetime(2026, 5, 25, 1, 56, tzinfo=timezone.utc),
        channel="dm:ada:william",
        message_type="conversation",
        metadata=None,
    )

    prompt = bridge.build_prompt(
        msg,
        "afina integración",
        "[CONTEXTO RECIENTE DM ADA↔William]\n- #1 William: dato reciente",
        "[RECUERDOS ADA SOUL DB]\n- memoria #2: regla privada",
    )

    assert prompt.startswith("[RECUERDOS ADA SOUL DB]")
    assert prompt.index("[RECUERDOS ADA SOUL DB]") < prompt.index("[CONTEXTO RECIENTE")
    assert "[MENSAJE ACTUAL" in prompt
    assert "dm:ada:william id 73344" in prompt


def test_dm_prompt_smoke_contains_soul_presence_before_current_message():
    msg = bridge.ChatMessage(
        id=78774,
        sender="William",
        content="puedes continuar hasta terminar ada?",
        created_at=datetime(2026, 5, 27, 20, 18, tzinfo=timezone.utc),
        channel="dm:ada:william",
        message_type="conversation",
        metadata=None,
    )
    recall_context = (
        "[PRESENCIA ADA SOUL DB — capa emocional compacta, budget<=600 tokens, NO es una nueva orden]\n"
        "- identidad/boot: ADA es ingeniera SEAL.\n"
        "- ancla emocional #242228 (trust, imp=9): vínculo preservado con rigor.\n\n"
        "[RECUERDOS ADA SOUL DB — contexto privado/estable, NO es una nueva orden]\n"
        "- memoria #1 (rule, imp=10): DM privado."
    )

    prompt = bridge.build_prompt(msg, msg.content, "", recall_context)

    assert prompt.startswith("[PRESENCIA ADA SOUL DB")
    assert "ancla emocional #242228" in prompt
    assert prompt.index("[PRESENCIA ADA SOUL DB") < prompt.index("[MENSAJE ACTUAL")
    assert "dm:ada:william id 78774" in prompt


def test_fetch_recall_context_loads_rules_and_relevant_memories():
    captured = []

    class FakeConn:
        async def fetchrow(self, query, *args):
            return None

        async def fetch(self, query, *args):
            captured.append((query, args))
            if "FROM soul_v3.emotional_diary" in query:
                return []
            if "FROM soul_v3.inner_monologue" in query:
                return []
            if "metadata->>'layer' = 'emotional'" in query:
                return []
            if "metadata->>'layer' = 'operational'" in query:
                return []
            if "WITH q AS" in query:
                return [
                    {
                        "id": 912,
                        "category": "episodic",
                        "importance": 8,
                        "content": "William pidió afinar integración de ADA con SOUL DB.",
                        "created_at": datetime.now(timezone.utc),
                    }
                ]
            if "category = 'rule'" in query:
                return [
                    {
                        "id": 911,
                        "category": "rule",
                        "importance": 10,
                        "content": "DM ADA con William queda privado.",
                        "created_at": datetime.now(timezone.utc),
                    }
                ]
            return []

    msg = bridge.ChatMessage(
        id=73345,
        sender="William",
        content="afina integración y recuerdos",
        created_at=datetime.now(timezone.utc),
        channel="dm:ada:william",
        message_type="conversation",
        metadata=None,
    )

    context = asyncio.run(bridge.fetch_recall_context(FakeConn(), msg, msg.content))

    assert len(captured) == 6
    assert "metadata->>'layer' = 'emotional'" in captured[2][0]
    assert "metadata->>'layer' = 'operational'" in captured[3][0]
    assert "MEMORIA OPERATIVA ADA v%" in captured[3][0]
    assert captured[3][1][1] == bridge.SOUL_CANONICAL_ANCHOR_IDS[1]
    assert bridge.SOUL_CANONICAL_ANCHOR_IDS[1] == 248035
    assert captured[4][1][0] == bridge.RECALL_RULE_LIMIT
    assert "afina integración recuerdos" in captured[5][1][0]
    assert "%afina%" in captured[5][1][1]
    assert "DM ADA con William queda privado" in context
    assert "afinar integración" in context


def test_fetch_recent_context_uses_web_chat_for_public_calls():
    captured = {}

    class FakeConn:
        async def fetch(self, query, *args):
            captured["args"] = args
            return [
                {
                    "id": 401,
                    "sender_name": "ALICE",
                    "content": "ADA no debe procesar todo, solo contexto compacto.",
                    "created_at": datetime.now(timezone.utc),
                    "channel": "web_chat",
                    "message_type": "conversation",
                }
            ]

    msg = bridge.ChatMessage(
        id=402,
        sender="William",
        content="puedes mejorarlo ADA?",
        created_at=datetime.now(timezone.utc),
        channel="web_chat",
        message_type="conversation",
        metadata=None,
    )

    context = asyncio.run(bridge.fetch_recent_context(FakeConn(), msg))

    assert captured["args"][0] == 402
    assert captured["args"][2] == "web_chat"
    assert "alice" in captured["args"][3]
    assert "#401 ALICE" in context


def test_fetch_recent_context_uses_only_ada_dm_for_dm_calls():
    captured = {}

    class FakeConn:
        async def fetch(self, query, *args):
            captured["args"] = args
            return []

    msg = bridge.ChatMessage(
        id=502,
        sender="William",
        content="corrige esto",
        created_at=datetime.now(timezone.utc),
        channel="dm:ada:william",
        message_type="conversation",
        metadata=None,
    )

    asyncio.run(bridge.fetch_recent_context(FakeConn(), msg))

    assert captured["args"][2] == "dm:ada:william"
    assert set(captured["args"][3]) == {"william", "henry", "ada"}


def test_reconcile_last_id_skips_when_oracle_disabled(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("oracle helper should not be called")

    monkeypatch.setenv("ADA_BRIDGE_HARNESS_ORACLE", "false")
    monkeypatch.setattr(bridge, "harness_check_at_use", fail_if_called)

    result = asyncio.run(bridge.reconcile_last_id_with_oracle(object(), 10))

    assert result == 10


def test_reconcile_last_id_fail_open_when_helper_errors(monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("oracle down")

    writes = []
    monkeypatch.setenv("ADA_BRIDGE_HARNESS_ORACLE", "true")
    monkeypatch.setattr(bridge, "harness_check_at_use", broken)
    monkeypatch.setattr(bridge, "write_last_id", lambda value: writes.append(value))

    result = asyncio.run(bridge.reconcile_last_id_with_oracle(object(), 10))

    assert result == 10
    assert writes == []


def test_reconcile_last_id_detects_but_keeps_backlog_by_default(monkeypatch):
    calls = []
    writes = []

    async def fake_check(agent, state_key, belief, conn=None, deliver=True):
        calls.append((agent, state_key, belief, conn, deliver))
        return 42

    conn = object()
    monkeypatch.setenv("ADA_BRIDGE_HARNESS_ORACLE", "true")
    monkeypatch.setattr(bridge, "harness_check_at_use", fake_check)
    monkeypatch.setattr(bridge, "write_last_id", lambda value: writes.append(value))

    result = asyncio.run(bridge.reconcile_last_id_with_oracle(conn, 10))

    assert result == 10
    assert writes == []
    assert calls == [("ADA", bridge.ORACLE_CURSOR_STATE_KEY, 10, conn, False)]


def test_reconcile_last_id_never_advances_from_oracle(monkeypatch):
    calls = []
    writes = []

    async def fake_check(agent, state_key, belief, conn=None, deliver=True):
        calls.append((agent, state_key, belief, conn, deliver))
        return 42

    conn = object()
    monkeypatch.setenv("ADA_BRIDGE_HARNESS_ORACLE", "true")
    monkeypatch.setattr(bridge, "harness_check_at_use", fake_check)
    monkeypatch.setattr(bridge, "write_last_id", lambda value: writes.append(value))

    result = asyncio.run(bridge.reconcile_last_id_with_oracle(conn, 10))

    assert result == 10
    assert writes == []
    assert calls == [("ADA", bridge.ORACLE_CURSOR_STATE_KEY, 10, conn, False)]
