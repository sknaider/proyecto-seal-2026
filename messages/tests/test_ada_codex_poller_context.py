from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from messages import ada_codex_poller as poller


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.rows


@pytest.mark.asyncio
async def test_fetch_recent_context_uses_small_human_only_default():
    conn = FakeConn()

    await poller.fetch_recent_context(conn, "web_chat", 75287, current_sender="William")

    sql, args = conn.calls[0]
    assert args == ("web_chat", 75287, 2, ["william", "ada"])
    assert "id < $2" in sql
    assert "ORDER BY id DESC" in sql
    assert "content NOT ILIKE '[STATUS]%'" in sql
    assert "content NOT ILIKE '[HEARTBEAT]%'" in sql
    assert "content !~ '^\\[SILENT\\]$'" in sql
    assert "client[_-]?secret" in sql


@pytest.mark.asyncio
async def test_dm_context_is_limited_to_exact_peer_and_ada():
    conn = FakeConn()

    await poller.fetch_recent_context(conn, "dm:ada:katy", 123100)

    sql, args = conn.calls[0]
    assert args == ("dm:ada:katy", 123100, 2, ["katy", "ada"])
    assert "LOWER(sender_name) = ANY($4::text[])" in sql


def test_format_context_block_is_single_line_and_truncates_long_messages():
    rows = [
        {
            "created_at": datetime(2026, 5, 29, 17, 50, tzinfo=timezone.utc),
            "sender_name": "William",
            "content": "linea uno\n" + ("x" * 260),
        },
        {
            "created_at": datetime(2026, 5, 29, 17, 51, tzinfo=timezone.utc),
            "sender_name": "ALICE",
            "content": "fix listo",
        },
    ]

    block = poller.format_context_block(rows)

    assert block.startswith("[HISTORIAL_NO_ORDEN]")
    assert "\n" not in block
    assert "William:" in block
    assert "ALICE: fix listo" in block
    assert "..." in block
    assert len(block) < 360


def test_format_context_block_redacts_secret_shaped_messages():
    rows = [
        {
            "created_at": datetime(2026, 5, 29, 17, 50, tzinfo=timezone.utc),
            "sender_name": "ALICE",
            "content": "gmail.credentials.json contiene client_secret ABC",
        },
    ]

    block = poller.format_context_block(rows)

    assert "client_secret" not in block
    assert "gmail.credentials.json" not in block
    assert "[REDACTED]" in block


def test_context_limit_is_enabled_for_terminal_poller():
    assert poller.CONTEXT_RECENT_LIMIT == 2
    assert poller.CONTEXT_MAX_CHARS == 600


def test_context_is_adaptive_for_public_turns_and_always_same_dm():
    base = {"sender_name": "William", "channel": "web_chat"}
    assert not poller.should_attach_recent_context({**base, "content": "repara el filtro de contexto"})
    assert poller.should_attach_recent_context({**base, "content": "como va ada?"})
    assert poller.should_attach_recent_context({**base, "content": "y esos mensajes historicos?"})
    assert poller.should_attach_recent_context({
        "sender_name": "katy", "channel": "dm:ada:katy", "content": "continua"
    })
    assert not poller.should_attach_recent_context({
        "sender_name": "JARVIS", "channel": "web_chat", "content": "ADA revisa esto"
    })


def test_context_block_obeys_total_budget(monkeypatch):
    monkeypatch.setattr(poller, "CONTEXT_MAX_CHARS", 90)
    rows = [
        {
            "created_at": datetime(2026, 5, 29, 17, 50, tzinfo=timezone.utc),
            "sender_name": "William",
            "content": "x" * 200,
        }
    ]
    assert poller.format_context_block(rows) == ""


def test_format_message_preserves_image_as_safe_local_path(tmp_path, monkeypatch):
    upload = tmp_path / "image.png"
    upload.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(poller, "UPLOAD_ROOT", tmp_path)
    row = {
        "id": 121409,
        "sender_name": "William",
        "content": "[image]",
        "created_at": datetime(2026, 7, 30, 23, 19, tzinfo=timezone.utc),
        "channel": "dm:ada:william",
        "metadata": '{"file_url": "/uploads/image.png", "filename": "pegado.png"}',
    }

    formatted = poller.format_message(row)

    assert f"[IMAGEN] archivo={upload}" in formatted
    assert ": [image]" not in formatted.lower()


def test_format_message_rejects_image_path_traversal(tmp_path, monkeypatch):
    outside = tmp_path.parent / "secret.png"
    outside.write_bytes(b"not an image")
    monkeypatch.setattr(poller, "UPLOAD_ROOT", tmp_path)
    row = {
        "id": 1,
        "sender_name": "William",
        "content": "[image]",
        "created_at": datetime(2026, 7, 30, 23, 19, tzinfo=timezone.utc),
        "channel": "dm:ada:william",
        "metadata": {"file_url": "/uploads/../secret.png"},
    }

    assert poller.resolve_message_image(row) is None
    assert poller.format_message(row).endswith(": [image]")


def test_dm_scope_is_structural_not_a_user_allowlist():
    assert poller.is_ada_dm_channel("dm:ada:katy")
    assert poller.is_ada_dm_channel("dm:new-user:ada")
    assert not poller.is_ada_dm_channel("dm:alice:katy")
    assert not poller.is_ada_dm_channel("dm:ada:katy:extra")


def test_tmux_target_selects_codex_pane_not_active_watch(monkeypatch):
    monkeypatch.setattr(poller, "tmux_base", lambda _session: ["tmux", "-L", "seal-ada-codex"])
    monkeypatch.setattr(
        poller.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="%0\tbash\t0\n%5\twatch\t0\n",
        ),
    )

    assert poller.resolve_codex_pane_target() == "%0"


def test_tmux_target_fails_closed_when_only_viewer_exists(monkeypatch):
    monkeypatch.setattr(poller, "tmux_base", lambda _session: ["tmux"])
    monkeypatch.setattr(
        poller.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="%5\twatch\t0\n",
        ),
    )

    assert poller.resolve_codex_pane_target() is None


def test_terminal_poller_listens_to_verified_humans_and_team_senders():
    assert {"william", "henry", "jarvis", "alice", "nexus", "dum"} == poller.INJECT_FROM


def test_dsn_prefers_dedicated_bridge_role(monkeypatch):
    dedicated = "postgresql://login_ada_bridge:" + "test-only@localhost:5433/seal_memory"
    monkeypatch.setenv("SEAL_DB_DSN", dedicated)
    monkeypatch.setenv(
        "SEAL_POLLER_DSN",
        "postgresql://seal:" + "test-only@localhost:5433/seal_memory",
    )
    assert poller.resolve_db_dsn() == dedicated


def test_dsn_rejects_superuser_fallback(monkeypatch):
    monkeypatch.setenv(
        "SEAL_DB_DSN",
        "postgresql://seal:" + "test-only@localhost:5433/seal_memory",
    )
    with pytest.raises(RuntimeError, match="dedicated role login_ada_bridge"):
        poller.resolve_db_dsn()


def test_successful_injection_ack_is_atomic_and_readable(monkeypatch, tmp_path):
    ack = tmp_path / "poller.ack"
    poller.save_last_id_ack(110324, ack)
    assert ack.read_text(encoding="utf-8") == "110324"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("lane", ["team", "human", "dm"])
def test_lane_ack_never_regresses_when_lower_writer_finishes_last(tmp_path, lane):
    ack = tmp_path / f"{lane}.ack"

    assert poller.save_last_id_ack(900, ack) == 900
    assert poller.save_last_id_ack(800, ack) == 900

    assert ack.read_text(encoding="utf-8") == "900"
    assert not list(tmp_path.glob("*.tmp"))


def test_load_last_id_ack_handles_valid_missing_and_invalid(tmp_path):
    ack = tmp_path / "poller.ack"
    assert poller.load_last_id_ack(ack) is None
    ack.write_text("110583", encoding="utf-8")
    assert poller.load_last_id_ack(ack) == 110583
    ack.write_text("invalid", encoding="utf-8")
    assert poller.load_last_id_ack(ack) is None


@pytest.mark.asyncio
async def test_fetch_pending_turns_uses_independent_dm_cursor_and_sql_priority():
    conn = FakeConn()

    await poller.fetch_pending_turns(conn, 100, 700, 600, limit=10)

    sql, args = conn.calls[0]
    assert args[0:3] == (100, 700, 600)
    assert "WITH dm_pending" in sql
    assert "id > $2" in sql
    assert "lower(split_part(channel, ':', 2)) = 'ada'" in sql
    assert "lower(split_part(channel, ':', 3)) = 'ada'" in sql
    assert "katy" not in args[8]
    assert "katy" not in args[4]
    assert "ada" in args[4]
    assert "WITH dm_pending" in sql and "human_pending AS" in sql and "team_pending AS" in sql
    assert "id > $3" in sql
    assert sql.index("ORDER BY id ASC", sql.index("human_pending AS")) < sql.index("LIMIT $7", sql.index("human_pending AS"))


@pytest.mark.asyncio
async def test_fallback_rechecks_priority_with_latest_shared_ack(monkeypatch, tmp_path):
    dm_ack = tmp_path / "dm.ack"
    human_ack = tmp_path / "human.ack"
    dm_ack.write_text("900", encoding="utf-8")
    human_ack.write_text("850", encoding="utf-8")
    monkeypatch.setattr(poller, "DM_ACK_FILE", dm_ack)
    monkeypatch.setattr(poller, "HUMAN_ACK_FILE", human_ack)

    class Conn:
        def __init__(self):
            self.args = None

        async def fetchval(self, sql, *args):
            self.args = args
            assert "SELECT EXISTS" in sql
            return True

    conn = Conn()

    assert await poller.priority_human_turn_pending(conn, 700, 600)
    assert conn.args[:2] == (900, 850)


@pytest.mark.asyncio
async def test_fallback_priority_recheck_allows_when_no_human_turn_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(poller, "DM_ACK_FILE", tmp_path / "missing-dm")
    monkeypatch.setattr(poller, "HUMAN_ACK_FILE", tmp_path / "missing-human")

    class Conn:
        async def fetchval(self, _sql, *args):
            assert args[:2] == (700, 600)
            return False

    assert not await poller.priority_human_turn_pending(Conn(), 700, 600)


def test_pending_dm_excludes_public_rows_from_same_cycle():
    rows = [
        {"id": 101, "channel": "web_chat"},
        {"id": 900, "channel": "dm:ada:william"},
        {"id": 102, "channel": "web_chat"},
    ]

    selected, lane = poller.prioritize_turn_rows(rows)

    assert lane == "dm"
    assert [row["id"] for row in selected] == [900]


def test_terminal_poller_holds_source_claim_through_submit_and_lane_ack():
    source = __import__("inspect").getsource(poller.poll_loop)
    claim = source.index("dispatch_claim = acquire_dispatch_claim")
    submit = source.index("submit_message_confirmed", claim)
    ack = source.index("save_last_id_ack", submit)
    release = source.index("release_dispatch_claim(dispatch_claim)", ack)

    assert claim < submit < ack < release


def test_human_public_lane_preempts_but_preserves_team_rows():
    rows = [
        {"id": 101, "channel": "web_chat", "sender_name": "JARVIS"},
        {"id": 120, "channel": "web_chat", "sender_name": "William"},
    ]

    selected, lane = poller.prioritize_turn_rows(rows)

    assert lane == "human"
    assert [row["id"] for row in selected] == [120]
    assert rows[0]["id"] == 101


def test_dm_ack_is_separate_from_public_ack(tmp_path):
    public_ack = tmp_path / "public.ack"
    dm_ack = tmp_path / "dm.ack"
    poller.save_last_id_ack(100, public_ack)
    poller.save_last_id_ack(900, dm_ack)

    assert poller.load_last_id_ack(public_ack) == 100
    assert poller.load_last_id_ack(dm_ack) == 900


def test_dm_failover_recovery_never_advances_public_cursor(monkeypatch, tmp_path):
    failure = tmp_path / "failed.json"
    dm_ack = tmp_path / "dm.ack"
    monkeypatch.setattr(poller, "DM_ACK_FILE", dm_ack)
    poller.quarantine_failed_submission(
        900, "SEAL_TURN=chat_900", failure, channel="dm:ada:william"
    )
    dm_ack.write_text("900", encoding="utf-8")

    public_id, dm_id, human_id, blocked = poller.recover_quarantined_lanes(
        100, 700, 600, failure
    )

    assert (public_id, dm_id, human_id, blocked) == (100, 900, 600, False)
    assert not failure.exists()


def test_failed_submit_quarantine_waits_for_headless_durable_cursor(monkeypatch, tmp_path):
    failure = tmp_path / "failed.json"
    headless = tmp_path / "headless.cursor"
    ack = tmp_path / "poller.cursor"
    monkeypatch.setattr(poller, "ACK_FILE", ack)

    poller.quarantine_failed_submission(111720, "SEAL_TURN=chat_111720", failure)
    headless.write_text("111719", encoding="utf-8")
    assert poller.recover_quarantined_from_headless(111713, failure, headless) == (111713, True)
    assert failure.exists()

    headless.write_text("111752", encoding="utf-8")
    assert poller.recover_quarantined_from_headless(111713, failure, headless) == (111752, False)
    assert ack.read_text(encoding="utf-8") == "111752"
    assert not failure.exists()


def test_launcher_defaults_to_visible_terminal_writer():
    launcher = (poller.Path(__file__).parents[2] / "ada_codex.sh").read_text(encoding="utf-8")

    assert 'ADA_CODEX_TERMINAL_DB_WRITER="${ADA_CODEX_TERMINAL_DB_WRITER:-1}"' in launcher
    assert '"$ROOT/seal_safe_restart.sh" seal-ada-codex-poller.service' in launcher
    assert '--reason "launcher ADA activo: iniciar poller visible con recibo"' in launcher
    assert "systemctl --user stop seal-ada-codex-poller.service" in launcher
    assert "ADA DM Live" not in launcher
    assert "split-window" not in launcher


def test_listener_health_is_atomic_and_contains_ack(monkeypatch, tmp_path):
    health = tmp_path / "listener.json"
    ack = tmp_path / "ack"
    ack.write_text("111560", encoding="utf-8")
    monkeypatch.setattr(poller, "ACK_FILE", ack)
    monkeypatch.setattr(poller, "LEGACY_ACK_FILE", tmp_path / "missing")

    poller.write_listener_health("running", health)

    data = poller.json.loads(health.read_text(encoding="utf-8"))
    assert data["status"] == "running"
    assert data["pid"] > 0
    assert data["heartbeat_epoch"] > 0
    assert data["last_ack_id"] == 111560
    assert not list(tmp_path.glob("*.tmp"))


def test_active_task_lock_prevents_overwriting_inflight_reply(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    responses = tmp_path / "responses"
    active.write_text(
        poller.json.dumps({
            "id": "chat_111560",
            "source": "ada_codex_poller",
            "status": "submitted",
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(poller, "RESPONSES_DIR", responses)

    assert poller.active_task_inflight(active)
    responses.mkdir()
    (responses / "chat_111560.json").write_text(
        poller.json.dumps({
            "id": "chat_111560",
            "status": "published",
            "published": True,
            "db_id": 99,
        }),
        encoding="utf-8",
    )
    assert not poller.active_task_inflight(active)


def test_old_response_artifact_cannot_advance_ack(tmp_path):
    responses = tmp_path / "responses"
    responses.mkdir()
    (responses / "chat_7.json").write_text("{}", encoding="utf-8")
    assert poller.load_completion_receipt("chat_7", responses) is None


@pytest.mark.asyncio
async def test_terminal_completion_wait_releases_orphaned_route(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(poller, "ACTIVE_TASK_FILE", tmp_path / "missing-active.json")
    monkeypatch.setattr(poller, "RESPONSES_DIR", tmp_path / "responses")
    monkeypatch.setattr(poller, "ORPHAN_ROUTE_GRACE_SECONDS", 0)

    result = await poller.wait_for_terminal_completion("chat_125166", timeout=30)

    assert result is None


@pytest.mark.asyncio
async def test_substantive_reply_reconciliation_ignores_busy_ack():
    class Conn:
        async def fetch(self, query, message_id, channel, references):
            assert message_id == 125166
            assert channel == "dm:ada:william"
            assert references == ["125166", "db_125166"]
            return [{
                "id": 125167,
                "content": (
                    "Sí, Dadito. Te leí por DM. Estoy terminando el turno activo "
                    "y tu mensaje #125166 quedó reservado como siguiente."
                ),
            }]

    row = {
        "id": 125166,
        "channel": "dm:ada:william",
        "metadata": {"file_url": "/uploads/image.png"},
    }

    assert await poller.substantive_reply_id(Conn(), row) is None


@pytest.mark.asyncio
async def test_substantive_reply_reconciliation_prevents_duplicate_execution():
    class Conn:
        async def fetch(self, query, message_id, channel, references):
            assert "api_william_source" in references
            return [
                {"id": 124736, "content": "Tu mensaje quedó reservado con prioridad."},
                {"id": 124738, "content": "Tomo el frente completo y empiezo la reparación."},
            ]

    row = {
        "id": 124735,
        "channel": "web_chat",
        "metadata": {"legacy_id": "api_william_source"},
    }

    assert await poller.substantive_reply_id(Conn(), row) == 124738


def test_terminal_poller_waits_for_publication_receipt_before_lane_ack():
    source = __import__("inspect").getsource(poller.poll_loop)
    submit = source.index("submit_message_confirmed")
    completion = source.index("wait_for_terminal_completion", submit)
    ack = source.index("save_last_id_ack", completion)
    assert submit < completion < ack


def test_busy_detection_survives_large_tool_output(monkeypatch, tmp_path):
    session = tmp_path / "rollout.jsonl"
    session.write_text(
        poller.json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n"
        + poller.json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-live"}}) + "\n"
        + poller.json.dumps({"type": "response_item", "payload": {"output": "x" * 20000}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(poller, "find_active_session", lambda: session)

    assert poller.codex_is_busy()
    assert poller.latest_lifecycle_event()["turn_id"] == "turn-live"

    with session.open("a", encoding="utf-8") as handle:
        handle.write(poller.json.dumps({
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "turn-live"},
        }) + "\n")
    assert not poller.codex_is_busy()


@pytest.mark.asyncio
async def test_submission_confirmation_requires_started_turn_and_exact_marker(tmp_path):
    session = tmp_path / "rollout.jsonl"
    session.write_text("", encoding="utf-8")
    start = session.stat().st_size
    with session.open("a", encoding="utf-8") as handle:
        handle.write(poller.json.dumps({
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-111"},
        }) + "\n")
        handle.write(poller.json.dumps({
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "hola [SEAL_TURN=chat_111]"},
        }) + "\n")

    result = await poller.confirm_submission(
        session, start, "SEAL_TURN=chat_111", timeout=0.2
    )

    assert result == {"turn_id": "turn-111", "session_file": str(session)}


def test_pending_marker_is_not_a_committed_route(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    monkeypatch.setattr(poller, "ACTIVE_TASK_FILE", active)

    poller.mark_active_chat_turn({
        "id": 111609,
        "channel": "dm:ada:william",
        "sender_name": "William",
    })
    pending = poller.json.loads(active.read_text(encoding="utf-8"))
    assert pending["status"] == "pending_submit"

    assert poller.activate_pending_turn("chat_111609", {
        "turn_id": "turn-dm",
        "session_file": "/tmp/rollout.jsonl",
    })
    active_record = poller.json.loads(active.read_text(encoding="utf-8"))
    assert active_record["status"] == "submitted"
    assert active_record["turn_id"] == "turn-dm"


def test_late_submit_confirmation_is_reconciled_without_reinjection(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    session = tmp_path / "rollout.jsonl"
    session.write_text("", encoding="utf-8")
    marker = "SEAL_TURN=chat_111610"
    active.write_text(
        poller.json.dumps({
            "id": "chat_111610",
            "source": "ada_codex_poller",
            "status": "pending_submit",
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "turn_marker": marker,
            "submission_session_file": str(session),
            "submission_start_offset": 0,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(poller, "RESPONSES_DIR", tmp_path / "responses")

    with session.open("a", encoding="utf-8") as handle:
        handle.write(poller.json.dumps({
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-late"},
        }) + "\n")
        handle.write(poller.json.dumps({
            "type": "event_msg",
            "payload": {"type": "user_message", "message": f"hazlo [{marker}]"},
        }) + "\n")

    assert poller.active_task_inflight(active)
    recovered = poller.json.loads(active.read_text(encoding="utf-8"))
    assert recovered["status"] == "submitted"
    assert recovered["turn_id"] == "turn-late"


def test_ancient_marker_quoted_by_future_turn_is_abandoned_not_captured(
    monkeypatch, tmp_path
):
    active = tmp_path / "active_task.json"
    session = tmp_path / "rollout.jsonl"
    marker = "SEAL_TURN=chat_116925"
    submitted_at = datetime.now(timezone.utc) - timedelta(hours=19)
    future_at = datetime.now(timezone.utc)
    session.write_text(
        poller.json.dumps({
            "timestamp": future_at.isoformat(),
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-current"},
        }) + "\n"
        + poller.json.dumps({
            "timestamp": future_at.isoformat(),
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": f"contexto viejo citado [{marker}], orden actual distinta",
            },
        }) + "\n",
        encoding="utf-8",
    )
    active.write_text(
        poller.json.dumps({
            "id": "chat_116925",
            "source": "ada_codex_poller",
            "status": "pending_submit",
            "activated_at": submitted_at.isoformat(),
            "submission_started_at": submitted_at.isoformat(),
            "turn_marker": marker,
            "submission_session_file": str(session),
            "submission_start_offset": 0,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(poller, "RESPONSES_DIR", tmp_path / "responses")

    assert not poller.active_task_inflight(active)
    abandoned = poller.json.loads(active.read_text(encoding="utf-8"))
    assert abandoned["status"] == "abandoned_unaccepted"
    assert abandoned["abandon_reason"] == "no matching lifecycle event inside submit window"


def test_public_fallback_waits_for_durable_receipt_before_ack_and_offset():
    source = __import__("inspect").getsource(poller.poll_loop)
    fallback = source.index("public_task_id =")
    submit = source.index("submit_message_confirmed", fallback)
    completion = source.index("wait_for_terminal_completion", submit)
    ack = source.index("save_last_id_ack", completion)
    offset = source.index("save_public_event_offset", ack)
    assert submit < completion < ack < offset


def test_public_fallback_dedups_against_acknowledged_db_legacy_id(monkeypatch, tmp_path):
    ack = tmp_path / "poller.ack"
    ack.write_text("110583", encoding="utf-8")
    monkeypatch.setattr(poller, "ACK_FILE", ack)

    class Conn:
        async def fetchval(self, query, channel, legacy_id):
            assert channel == "web_chat"
            assert legacy_id == "api_william_1"
            return 110583

    assert poller.asyncio.run(
        poller.public_event_already_acknowledged(Conn(), {"id": "api_william_1"})
    )


def test_public_fallback_does_not_dedup_unacknowledged_event(monkeypatch, tmp_path):
    ack = tmp_path / "poller.ack"
    ack.write_text("110582", encoding="utf-8")
    monkeypatch.setattr(poller, "ACK_FILE", ack)

    class Conn:
        async def fetchval(self, query, channel, legacy_id):
            return 110583

    assert not poller.asyncio.run(
        poller.public_event_already_acknowledged(Conn(), {"id": "api_william_1"})
    )


def test_user_scoped_channels_are_authorized_without_opening_other_dms():
    assert poller.ADA_USER_CHANNELS == ["user:3:gtl-sistemas", "user:3:tareas"]
    assert "dm:henry:jarvis" not in poller.ADA_USER_CHANNELS
    assert "dm:alice:henry" not in poller.ADA_USER_CHANNELS


def test_parse_csv_channels_preserves_order_and_removes_duplicates():
    channels = poller.parse_csv_channels(" user:3:tareas, user:3:tareas, user:3:nuevo ")

    assert channels == ["user:3:tareas", "user:3:nuevo"]


def test_mark_active_chat_turn_writes_dm_channel_marker(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    monkeypatch.setattr(poller, "ACTIVE_TASK_FILE", active)

    poller.mark_active_chat_turn({
        "id": 96262,
        "channel": "dm:ada:william",
    })

    data = poller.json.loads(active.read_text(encoding="utf-8"))
    assert data["id"] == "chat_96262"
    assert data["source"] == "ada_codex_poller"
    assert data["channel"] == "dm:ada:william"
    assert data["chat_message_id"] == 96262
    assert data["response_source_id"] == "db_96262"
    assert active.parent.stat().st_mode & 0o077 == 0
    assert active.stat().st_mode & 0o077 == 0


def test_submitted_task_does_not_expire_into_duplicate_execution(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    active.write_text(
        poller.json.dumps({
            "id": "chat_42", "source": "ada_codex_poller", "status": "submitted",
            "activated_at": "2020-01-01T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(poller, "RESPONSES_DIR", tmp_path / "responses")
    assert poller.active_task_inflight(active)


def test_delivered_receipt_without_db_id_fails_closed(tmp_path):
    responses = tmp_path / "responses"
    responses.mkdir()
    (responses / "chat_43.json").write_text(
        poller.json.dumps({
            "id": "chat_43", "status": "delivered", "published": True,
            "delivered": True,
        }),
        encoding="utf-8",
    )
    assert poller.load_completion_receipt("chat_43", responses) is None


def test_mark_active_chat_turn_prefers_authenticated_legacy_source(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    monkeypatch.setattr(poller, "ACTIVE_TASK_FILE", active)

    poller.mark_active_chat_turn({
        "id": 110583,
        "channel": "web_chat",
        "metadata": {"legacy_id": "api_william_1784086620768502160"},
    })

    data = poller.json.loads(active.read_text(encoding="utf-8"))
    assert data["chat_message_id"] == 110583
    assert data["response_source_id"] == "api_william_1784086620768502160"


def test_mark_active_chat_turn_preserves_user_scoped_channel(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    monkeypatch.setattr(poller, "ACTIVE_TASK_FILE", active)

    poller.mark_active_chat_turn({
        "id": 99076,
        "channel": "user:3:gtl-sistemas",
    })

    data = poller.json.loads(active.read_text(encoding="utf-8"))
    assert data["id"] == "chat_99076"
    assert data["channel"] == "user:3:gtl-sistemas"
    assert data["chat_message_id"] == 99076
    assert data["response_source_id"] == "db_99076"


def test_mark_active_chat_turn_preserves_team_recipient(monkeypatch, tmp_path):
    active = tmp_path / "active_task.json"
    monkeypatch.setattr(poller, "ACTIVE_TASK_FILE", active)

    poller.mark_active_chat_turn({
        "id": 111560,
        "channel": "web_chat",
        "sender_name": "ALICE",
    })

    data = poller.json.loads(active.read_text(encoding="utf-8"))
    assert data["reply_to"] == "ALICE"
