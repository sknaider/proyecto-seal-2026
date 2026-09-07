from pathlib import Path

from messages import agent_writer


ROOT = Path(__file__).resolve().parents[2]


def _token(_agent: str) -> str:
    return "test-token"


def test_addressing_william_does_not_self_authorize_proactive(monkeypatch):
    monkeypatch.setattr(agent_writer, "agent_token", _token)
    payload = agent_writer.build_payload("ADA", "William", "estado")
    assert "proactive" not in payload
    assert "in_reply_to" not in payload


def test_proactive_authority_must_be_explicit(monkeypatch):
    monkeypatch.setattr(agent_writer, "agent_token", _token)
    payload = agent_writer.build_payload("ADA", "William", "alerta", proactive=True)
    assert payload["proactive"] is True


def test_correlated_reply_is_not_marked_proactive(monkeypatch):
    monkeypatch.setattr(agent_writer, "agent_token", _token)
    payload = agent_writer.build_payload(
        "ADA", "William", "resultado", in_reply_to="source-1"
    )
    assert payload["in_reply_to"] == "source-1"
    assert "proactive" not in payload


def test_numeric_database_source_is_namespaced(monkeypatch):
    monkeypatch.setattr(agent_writer, "agent_token", _token)

    from_int = agent_writer.build_payload(
        "ADA", "William", "resultado", in_reply_to=122325
    )
    from_text = agent_writer.build_payload(
        "ADA", "William", "resultado", in_reply_to="122325"
    )

    assert from_int["in_reply_to"] == "db_122325"
    assert from_text["in_reply_to"] == "db_122325"


def test_event_source_is_preserved(monkeypatch):
    monkeypatch.setattr(agent_writer, "agent_token", _token)
    payload = agent_writer.build_payload(
        "ADA", "William", "resultado", in_reply_to="api_william_123"
    )
    assert payload["in_reply_to"] == "api_william_123"


def test_shared_contract_requires_authenticated_public_completion_report():
    """The cutover validates its shared runtime contract, not ADA's protected
    local AGENTS.md working copy.  This keeps the test hermetic in a clean
    checkout and prevents a --deselect false green."""
    shared_contract = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "trabajo finalizado = trabajo reportado" in shared_contract.casefold()
    assert "python3 scripts/seal_send.py TU_NOMBRE William" in shared_contract
    assert "--in-reply-to" in shared_contract
    assert "curl -s -X POST http://localhost:8765/api/agents/send" not in shared_contract
