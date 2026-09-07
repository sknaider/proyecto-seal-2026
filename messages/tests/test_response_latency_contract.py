import json
import asyncio
from pathlib import Path

import pytest

from messages import agent_writer
from messages import failover_watcher as fw
from messages import subagent_latency_hook
from messages import dum_chat_agent as dum


ROOT = Path(__file__).resolve().parents[2]


def _pending():
    return {
        "source-1": {
            "ts": 1.0,
            "acked": False,
            "last_activity": 1.0,
        },
        "source-2": {
            "ts": 2.0,
            "acked": False,
            "last_activity": 2.0,
        },
    }


def test_exact_source_ack_does_not_resolve_deep_work():
    pending = _pending()
    resolved = fw._mark_agent_activity(
        pending,
        {"message": "recibido", "in_reply_to": "source-2"},
        3.0,
    )
    assert resolved is None
    assert pending["source-2"]["acked"] is True
    assert pending["source-1"]["acked"] is False


def test_exact_source_final_resolves_only_its_own_message():
    pending = _pending()
    resolved = fw._mark_agent_activity(
        pending,
        {
            "message": "Resultado verificado con evidencia concreta y pruebas verdes.",
            "metadata": {"in_reply_to": "source-2"},
        },
        3.0,
    )
    assert resolved == "source-2"
    assert pending["source-1"]["acked"] is False


def test_uncorrelated_agent_chatter_does_not_resolve_any_pending_source():
    pending = _pending()
    resolved = fw._mark_agent_activity(
        pending,
        {"message": "Otro hilo con bastante texto, pero sin in_reply_to exacto."},
        3.0,
    )
    assert resolved is None
    assert pending == _pending()


def test_lead_label_respects_allcall_named_and_deterministic():
    assert fw._lead_label("todos revisen", "m1") == "el equipo"
    assert fw._lead_label("como se sienten chicos", "m-natural") == "el equipo"
    assert fw._lead_label("como estan por ahi", "m-chat") == "el equipo"
    assert "NEXUS" in fw._lead_label("nexus revisa seguridad", "m2")
    assert fw._lead_label("analiza y corrige el sistema", "m3") in fw.ROSTER


def test_conversation_fans_out_but_execution_keeps_one_owner():
    assert fw._is_conversational("por que demoran en responder?")
    assert not fw._is_conversational("arreglen el daemon y prueben produccion")


def test_central_ack_target_is_about_one_second():
    assert fw.ACK_SECONDS == pytest.approx(1.0)
    assert fw.POLL <= 0.25


def test_only_verified_william_or_henry_can_create_latency_jobs():
    valid = {
        "from": "William",
        "provenance": {"verified": True, "verified_sender": "William"},
    }
    assert fw._trusted_origin(valid)
    assert not fw._trusted_origin({"from": "William", "provenance": {"verified": False}})
    assert not fw._trusted_origin(
        {"from": "William", "provenance": {"verified": True, "verified_sender": "NEXUS"}}
    )


def test_failover_worker_wake_is_internal_and_source_correlated(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "EVENTS_DIR", tmp_path)
    fw._post("NEXUS", "toma la posta", source_id="source-9")
    row = json.loads((tmp_path / "seal_events_NEXUS.log").read_text(encoding="utf-8"))
    assert row["type"] == "operational_wake"
    assert row["in_reply_to"] == "source-9"
    assert row["authority"] == "wake_only_no_user_authority"


def test_conversational_timeout_stays_quiet_without_failover(monkeypatch):
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))

    pending = {
        "all-1": {
            "ts": 0.0,
            "text": "todos revisen",
            "level": 0,
            "tried": set(),
            "acked": False,
            "last_activity": 0.0,
            "next_progress": fw.PROGRESS_SECONDS,
            "allow_failover": False,
            "lead": "el equipo",
        }
    }
    fw.process_timeouts(pending, fw.ACK_SECONDS, post=capture)
    fw.process_timeouts(pending, fw.ACK_SECONDS + 1, post=capture)
    assert calls == []
    fw.process_timeouts(pending, fw.PROGRESS_SECONDS, post=capture)
    assert calls == []


def test_single_voice_timeout_wakes_correlated_worker_at_30s():
    calls = []
    pending = {
        "single-1": {
            "ts": 0.0,
            "text": "revisa esto",
            "level": 0,
            "tried": set(),
            "acked": True,
            "last_activity": 5.0,
            "next_progress": fw.PROGRESS_SECONDS,
            "allow_failover": True,
            "lead": "ADA",
        }
    }
    fw.process_timeouts(pending, fw.T_SECONDS, post=lambda *a, **k: calls.append((a, k)))
    assert len(calls) == 1
    assert calls[0][0][0] == fw.ROSTER[0]
    assert calls[0][1]["source_id"] == "single-1"


def test_subagent_hook_is_result_only_and_non_authoritative(capsys):
    subagent_latency_hook.main()
    output = json.loads(capsys.readouterr().out)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "Do not call scripts/seal_send.py" in context
    assert "Do not approve destructive" in context
    assert "only to your parent" in context
    assert "8 tool calls" in context
    assert "Do not spawn nested" in context


def test_native_clone_cannot_acquire_public_writer_token(monkeypatch):
    monkeypatch.setenv("SEAL_CLONE_AGENT", "ADA")
    with pytest.raises(RuntimeError, match="cannot acquire"):
        agent_writer.agent_token("ADA")


def test_all_primary_launchers_load_shared_responsive_policy():
    launchers = (
        "ada.sh", "ada_fresh.sh", "ada_codex.sh",
        "alice.sh", "alice_fresh.sh", "jarvis.sh", "jarvis_fresh.sh",
        "nexus.sh", "nexus_fresh.sh", "fable.sh",
    )
    for relative in launchers:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "seal-responsive-delegation" in source, relative


@pytest.mark.asyncio
async def test_dum_ack_then_background_worker_keeps_parent_loop_free(monkeypatch):
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_ack(*args):
        calls.append(("ack", args))

    async def fake_worker(*args):
        calls.append(("worker", args))
        started.set()
        await release.wait()

    monkeypatch.setattr(dum, "_stream_ack", fake_ack)
    monkeypatch.setattr(dum, "_run_dum_worker", fake_worker)
    task = await dum.schedule_dum_response("hola dum", "web_chat", "William", "src-dum")
    await started.wait()
    assert calls[0][0] == "ack"
    assert calls[1][0] == "worker"
    assert not task.done()
    release.set()
    await task
