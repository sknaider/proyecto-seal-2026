from messages import agent_writer


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
