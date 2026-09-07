from __future__ import annotations

import json
import io
import os
import sqlite3
import stat
import types
from pathlib import Path

import pytest

from messages.ada_user_clone_worker import (
    _decrypt,
    _default_model,
    _ensure_instance,
    _history,
    _identity_projection,
    _instance_root,
    _load_key,
    _load_voice_profile,
    _ollama_reply,
    _post_with_fresh_health,
    _read_broker_capability,
    _system_prompt,
    _technical_context,
    _voice_fewshot_messages,
    _remember,
)


PROJECTION_SHA = "sha256:" + "a" * 64
VOICE_PATH = Path(__file__).parents[1] / "voice_profiles/JARVIS-u116.json"


def test_broker_capability_accepts_only_root_expected_group_and_exact_mode(
    monkeypatch, tmp_path
) -> None:
    capability = tmp_path / "client.capability"
    capability.write_text("opaque-capability\n", encoding="utf-8")
    capability.chmod(0o440)
    expected_gid = 981
    real_fstat = os.fstat

    def root_group_fstat(fd):
        meta = real_fstat(fd)
        return types.SimpleNamespace(
            st_mode=meta.st_mode,
            st_uid=0,
            st_gid=expected_gid,
        )

    monkeypatch.setattr("messages.ada_user_clone_worker.os.getgroups", lambda: [expected_gid])
    monkeypatch.setattr("messages.ada_user_clone_worker.os.fstat", root_group_fstat)
    assert _read_broker_capability(capability, expected_gid) == "opaque-capability"

    capability.chmod(0o400)
    with pytest.raises(RuntimeError, match="exactly 0440"):
        _read_broker_capability(capability, expected_gid)

    capability.chmod(0o440)
    with pytest.raises(RuntimeError, match="group is not active"):
        _read_broker_capability(capability, expected_gid + 1)


def _ensure(base, user_id, username, agent="ADA"):
    return _ensure_instance(
        base,
        user_id,
        username,
        agent=agent,
        technical_projection_sha256=PROJECTION_SHA,
    )


def test_instances_have_separate_roots_keys_and_encrypted_memory(tmp_path) -> None:
    m1 = _ensure(tmp_path, 103, "katy")
    m2 = _ensure(tmp_path, 104, "newuser")
    assert m1["instance_key"] == "ADA-u103"
    assert m2["instance_key"] == "ADA-u104"
    r1 = _instance_root(tmp_path, 103)
    r2 = _instance_root(tmp_path, 104)
    assert _load_key(r1) != _load_key(r2)

    _remember(r1, 1, "user", "secreto exclusivo de katy")
    _remember(r2, 2, "user", "secreto exclusivo del usuario nuevo")
    assert _history(r1) == [{"role": "user", "content": "secreto exclusivo de katy"}]
    assert _history(r2) == [{"role": "user", "content": "secreto exclusivo del usuario nuevo"}]

    raw1 = sqlite3.connect(r1 / "mini-soul.db").execute(
        "SELECT content_enc FROM conversation"
    ).fetchone()[0]
    assert "secreto exclusivo" not in raw1
    with pytest.raises(Exception):
        _decrypt(raw1, _load_key(r2))


def test_manifest_is_fail_closed_and_private(tmp_path) -> None:
    manifest = _ensure(tmp_path, 103, "katy")
    root = _instance_root(tmp_path, 103)
    assert manifest["canonical_memory_read"] is False
    assert manifest["core_code_visibility"] is False
    assert manifest["tools_enabled"] is False
    assert manifest["technical_projection_read"] is True
    assert manifest["technical_projection_sha256"] == PROJECTION_SHA
    assert manifest["memory_namespace"] == "user:103:agent:ADA"
    persisted = json.loads((root / "manifest.json").read_text())
    assert persisted == manifest
    assert stat.S_IMODE((root / "manifest.json").stat().st_mode) == 0o400
    assert stat.S_IMODE((root / "runtime/atrest.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "mini-soul.db").stat().st_mode) == 0o600


def test_instance_rejects_invalid_user_id(tmp_path) -> None:
    with pytest.raises(ValueError, match="positive"):
        _ensure(tmp_path, 0, "ghost")


def test_publish_refreshes_health_immediately_before_gate(monkeypatch, tmp_path) -> None:
    manifest = _ensure(tmp_path / "users", 103, "katy")
    calls: list[str] = []

    def fake_health(*args, **kwargs):
        calls.append("health")
        return ("ollama-http-local", False)

    def fake_post(**kwargs):
        calls.append("post")
        return {"ok": True, "id": "test"}

    monkeypatch.setattr("messages.ada_user_clone_worker._write_health", fake_health)
    monkeypatch.setattr("messages.ada_user_clone_worker._post_chat", fake_post)
    result = _post_with_fresh_health(
        state_dir=tmp_path / "state",
        manifest=manifest,
        token="token",
        chat_url="http://127.0.0.1:8765/api/agents/send",
        agent="ADA",
        username="katy",
        channel="dm:ada:katy",
        message="respuesta",
        source_id=123,
        instance_key="ADA-u103",
        kind="final",
        model="qwen2.5:7b",
        model_url="http://127.0.0.1:11434/api/chat",
    )
    assert result["ok"] is True
    assert calls == ["health", "post"]


def test_physical_memory_is_scoped_by_user_and_agent(tmp_path) -> None:
    root = _instance_root(tmp_path, 103)
    assert root == tmp_path / "u103" / "instances" / "ADA"


def test_agents_have_distinct_instance_roots_and_identities(tmp_path) -> None:
    alice = _ensure(tmp_path, 103, "katy", agent="ALICE")
    jarvis = _ensure(tmp_path, 103, "katy", agent="JARVIS")
    assert alice["instance_key"] == "ALICE-u103"
    assert jarvis["instance_key"] == "JARVIS-u103"
    assert _instance_root(tmp_path, 103, "ALICE") != _instance_root(tmp_path, 103, "JARVIS")
    assert alice["memory_namespace"] == "user:103:agent:ALICE"
    assert jarvis["memory_namespace"] == "user:103:agent:JARVIS"


def test_public_identity_is_soulful_without_canonical_memory() -> None:
    identity = _identity_projection("JARVIS")
    rendered = json.dumps(identity, ensure_ascii=False)
    assert identity["schema"] == "seal.user-agent-identity.v3"
    assert identity["source_scope"] == "public-identity-only"
    assert identity["role"] == "arquitecto y guía técnico de SOUL"
    assert "cálido" in identity["style"]
    assert "primera persona" in identity["voice"]
    assert "criterio" in identity["voice_example"]
    assert identity["ocean"] == {"O": 1.0, "C": 1.0, "E": 0.401, "A": 0.82, "N": 0.115}
    assert "memoria canónica" in identity["identity_source"]
    for private_carrier in ("postgresql://", "dm:jarvis:", "api_key", "session_key"):
        assert private_carrier not in rendered.casefold()


def test_jarvis_uses_soulful_local_model_without_changing_other_agents(monkeypatch) -> None:
    monkeypatch.delenv("SEAL_USER_CLONE_MODEL", raising=False)
    monkeypatch.delenv("SEAL_USER_CLONE_MODEL_JARVIS", raising=False)
    assert _default_model("JARVIS") == "gemma3-hermes:12b"
    assert _default_model("ADA") == "qwen2.5:7b"
    monkeypatch.setenv("SEAL_USER_CLONE_MODEL_JARVIS", "override:model")
    assert _default_model("JARVIS") == "override:model"


def test_system_prompt_uses_public_voice_and_keeps_private_boundary() -> None:
    prompt = _system_prompt(
        "JARVIS", "usuario-demo", "routing fail closed", ["¿podrías detallar?"]
    )
    assert "arquitecto y guía técnico de SOUL" in prompt
    assert "criterio propio y calidez" in prompt
    assert "No recita políticas ni suena como un bot de soporte" in prompt
    assert "Evita cierres de call-center" in prompt
    assert "O=1.0, C=1.0, E=0.401, A=0.82, N=0.115" in prompt
    assert "No anuncies que sos un clon" in prompt
    assert "routing fail closed" in prompt
    assert "ni acceso a la memoria canónica o privada" in prompt
    assert "¿podrías detallar?" in prompt


def test_jarvis_voice_fewshot_is_bounded_public_and_ordered() -> None:
    messages = _voice_fewshot_messages("JARVIS", VOICE_PATH)
    assert len(messages) == 12
    assert [row["role"] for row in messages] == ["user", "assistant"] * 6
    assert messages[0]["content"] == "hola"
    assert "Acá estoy" in messages[1]["content"]
    assert "no manejo información interna" in messages[7]["content"]
    assert "William" not in json.dumps(messages, ensure_ascii=False)
    assert "pip install" not in json.dumps(messages, ensure_ascii=False)
    rendered = json.dumps(messages, ensure_ascii=False).casefold()
    for private_carrier in (
        "postgresql://", "session_key", "api_key", "dm:jarvis:",
        "/home/dadito", "192.168.68.", "soul_v3",
    ):
        assert private_carrier not in rendered


def _write_voice_fixture(
    tmp_path, *, assistant="respuesta", instance="JARVIS-u116",
    forbidden="¿podrías detallar?",
):
    source_dir = tmp_path / "sources"
    source_dir.mkdir(parents=True)
    source = source_dir / "source.md"
    source.write_text("fuente pública", encoding="utf-8")
    source.chmod(0o444)
    import hashlib
    payload = {
        "schema": "seal.user-clone-public-voice.v1",
        "instance": instance,
        "agent": "JARVIS",
        "version": 1,
        "locale": "es-PE",
        "source_scope": "public-synthetic-voice-examples",
        "source_file": "source.md",
        "source_sha256": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
        "forbidden_phrases": [forbidden],
        "examples": [{"user": f"hola {i}", "assistant": assistant} for i in range(4)],
    }
    profile = tmp_path / "JARVIS-u116.json"
    profile.write_text(json.dumps(payload), encoding="utf-8")
    profile.chmod(0o444)
    return profile


def test_voice_fewshot_rejects_private_carrier(tmp_path) -> None:
    profile = _write_voice_fixture(tmp_path, assistant="usa postgresql://private")
    with pytest.raises(RuntimeError, match="private carrier"):
        _voice_fewshot_messages("JARVIS", profile)


def test_voice_profile_rejects_wrong_instance_bidi_symlink_and_tampered_source(tmp_path) -> None:
    wrong = _write_voice_fixture(tmp_path / "wrong", instance="ADA-u116")
    with pytest.raises(RuntimeError, match="instance mismatch"):
        _load_voice_profile("JARVIS", wrong)

    bidi = _write_voice_fixture(tmp_path / "bidi", assistant="hola\u202esecreto")
    with pytest.raises(RuntimeError, match="control characters"):
        _load_voice_profile("JARVIS", bidi)

    tampered = _write_voice_fixture(tmp_path / "tampered")
    (tampered.parent / "sources/source.md").chmod(0o644)
    (tampered.parent / "sources/source.md").write_text("cambiada", encoding="utf-8")
    (tampered.parent / "sources/source.md").chmod(0o444)
    with pytest.raises(RuntimeError, match="source hash mismatch"):
        _load_voice_profile("JARVIS", tampered)

    link_root = tmp_path / "link"
    link_root.mkdir()
    link = link_root / "JARVIS-u116.json"
    link.symlink_to(VOICE_PATH)
    with pytest.raises(RuntimeError, match="artifact rejected"):
        _load_voice_profile("JARVIS", link)


@pytest.mark.parametrize("forbidden", ["oculto\u202e", "postgresql://private"])
def test_voice_profile_rejects_bidi_or_secret_in_forbidden_phrase(tmp_path, forbidden) -> None:
    profile = _write_voice_fixture(tmp_path, forbidden=forbidden)
    with pytest.raises(RuntimeError, match="forbidden phrase contains a private carrier"):
        _load_voice_profile("JARVIS", profile)


def test_ollama_payload_orders_system_then_fewshot_then_real_history(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured.update(json.loads(req.data.decode("utf-8")))
        return io.BytesIO(b'{"message":{"content":"respuesta"}}')

    monkeypatch.setattr("messages.ada_user_clone_worker.request.urlopen", fake_urlopen)
    profile = _load_voice_profile("JARVIS", VOICE_PATH)
    result = _ollama_reply(
        "gemma3-hermes:12b", "http://127.0.0.1:11434/api/chat", "JARVIS",
        "usuario-demo", [{"role": "user", "content": "MENSAJE_REAL"}], "", profile,
    )
    assert result == "respuesta"
    rows = captured["messages"]
    assert rows[0]["role"] == "system"
    assert rows[1:13] == profile["messages"]
    assert rows[13] == {"role": "user", "content": "MENSAJE_REAL"}


def test_technical_projection_retrieval_is_bounded_and_read_only(tmp_path) -> None:
    projection = tmp_path / "technical.sqlite3"
    conn = sqlite3.connect(projection)
    conn.execute("CREATE TABLE knowledge(id INTEGER PRIMARY KEY,title,content,source,priority)")
    conn.execute(
        "CREATE VIRTUAL TABLE knowledge_fts USING fts5(title,content,source UNINDEXED,kind UNINDEXED)"
    )
    conn.execute(
        "INSERT INTO knowledge VALUES(1,'routing','fail closed instance routing','routing.py',10)"
    )
    conn.execute(
        "INSERT INTO knowledge_fts(rowid,title,content,source,kind) VALUES(1,'routing','fail closed instance routing','routing.py','code')"
    )
    conn.commit()
    conn.close()
    projection.chmod(0o444)
    context = _technical_context(projection, "¿cómo funciona el routing de instancia?")
    assert "fail closed instance routing" in context
    assert "routing.py" in context
