import datetime
import json

from messages import seal_monitor_filter as monitor


NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()


def _line(mode, lead, assignments, *, enforced=True):
    return json.dumps({
        "id": "source-council-1",
        "from": "William",
        "to": "equipo",
        "type": "conversation",
        "message": "todos revisen y ADA consolida",
        "channel": "web_chat",
        "timestamp": NOW,
        "coordination": {
            "policy": "soul-council-v1",
            "mode": mode,
            "lead": lead,
            "source_id": "source-council-1",
            "enforced": enforced,
            "assignments": assignments,
        },
    })


def test_enforced_execution_delivers_lead_and_internal_contributors_only():
    line = _line("execution", "ADA", [
        {"agent": "ADA", "role": "lead", "public_write": True},
        {"agent": "NEXUS", "role": "contributor", "public_write": False},
    ])
    ada = json.loads(monitor.filter_line(line, "ADA", {}))
    nexus = json.loads(monitor.filter_line(line, "NEXUS", {}))
    assert "rol público lead" in ada["_post_rule"]
    assert "--multi-response" in ada["_post_rule"]
    assert "No uses --multi-response" in ada["_post_rule"]
    assert "CONTRIBUTOR INTERNO" in nexus["_post_rule"]
    assert "NO publiques" in nexus["_post_rule"]
    assert monitor.filter_line(line, "JARVIS", {}) is None


def test_roundtable_delivers_only_server_assigned_speakers():
    line = _line("roundtable", "ADA", [
        {"agent": "ADA", "role": "speaker", "public_write": True},
        {"agent": "FABLE", "role": "speaker", "public_write": True},
    ])
    assert monitor.filter_line(line, "ADA", {}) is not None
    assert monitor.filter_line(line, "FABLE", {}) is not None
    assert monitor.filter_line(line, "ALICE", {}) is None


def test_shadow_plan_does_not_change_legacy_delivery(monkeypatch):
    monkeypatch.setattr(monitor, "CLAIM_URL", "http://127.0.0.1:1")
    line = _line("execution", "ADA", [
        {"agent": "ADA", "role": "lead", "public_write": True},
    ], enforced=False)
    winners = [
        agent for agent in monitor.SINGLE_VOICE_ROSTER
        if monitor.filter_line(line, agent, {}) is not None
    ]
    # SHADOW observes only: the old all-call rule still fans "todos" to all.
    assert set(winners) == set(monitor.SINGLE_VOICE_ROSTER)
