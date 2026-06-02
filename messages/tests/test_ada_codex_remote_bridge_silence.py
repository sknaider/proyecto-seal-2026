from datetime import datetime, timezone
import asyncio

import messages.ada_codex_remote_bridge as bridge


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


def test_team_chatter_about_ada_does_not_queue_codex_turn():
    content = "ADA Codex proceso activo; probable que esté trabajando"

    assert bridge.silence_output_for_message("web_chat", content, "NEXUS") == "[SILENT]"
    assert not bridge.should_route_to_codex("web_chat", content, "NEXUS")


def test_team_direct_address_to_ada_is_context_only_by_default(monkeypatch):
    monkeypatch.delenv("ADA_BRIDGE_ROUTE_TEAM", raising=False)
    content = "ADA — revisa este estado del bridge"

    assert bridge.silence_output_for_message("web_chat", content, "JARVIS") == "[SILENT]"
    assert not bridge.should_route_to_codex("web_chat", content, "JARVIS")


def test_team_direct_address_to_ada_routes_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("ADA_BRIDGE_ROUTE_TEAM", "true")
    content = "ADA — revisa este estado del bridge"

    assert bridge.silence_output_for_message("web_chat", content, "JARVIS") is None
    assert bridge.should_route_to_codex("web_chat", content, "JARVIS")


def test_team_routing_enabled_still_requires_direct_prefix(monkeypatch):
    monkeypatch.setenv("ADA_BRIDGE_ROUTE_TEAM", "true")
    content = "William preguntó a ADA si conviene el filtro"

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

    assert batch_max_id == 102
    assert [msg.id for msg in messages] == [102]


def test_fetch_messages_default_william_first_skips_team_direct_address(monkeypatch):
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

    assert batch_max_id == 202
    assert [msg.id for msg in messages] == [202]


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

    assert batch_max_id == 202
    assert [msg.id for msg in messages] == [202, 201]


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

    def fake_post_message(to, message, msg_type="conversation", channel="web_chat", idempotency_key=None):
        calls.append(
            {
                "to": to,
                "message": message,
                "msg_type": msg_type,
                "channel": channel,
                "idempotency_key": idempotency_key,
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
