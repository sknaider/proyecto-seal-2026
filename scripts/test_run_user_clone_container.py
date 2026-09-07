import subprocess
import sys
import types
from pathlib import Path


RUNNER = Path(__file__).with_name("run_user_clone_container.sh")


def test_runner_prefers_scoped_projection_before_default() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    scoped = text.index('INSTANCE_PROJECTION="/home/dadito/.local/share/seal/user-clone-projections/technical_${INSTANCE}.sqlite3"')
    fallback = text.index('PROJECTION="${SEAL_USER_CLONE_PROJECTION:-$DEFAULT_PROJECTION}"')
    assert scoped < fallback
    assert 'if [[ -f "$INSTANCE_PROJECTION" ]]' in text


def test_runner_rejects_unscoped_instance() -> None:
    result = subprocess.run(
        ["bash", str(RUNNER), "start", "../../JARVIS-u116"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "invalid isolated clone instance" in result.stderr


def test_voice_profile_is_scoped_only_to_jarvis_u116() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'if [[ "$INSTANCE" == "JARVIS-u116" ]]' in text
    assert '--voice-profile /app/voice_profiles/JARVIS-u116.json' in text
    assert 'IMAGE="seal-user-clone:1.3.2"' in text
    assert 'IMAGE="seal-user-clone:1.3.4"' in text
    assert text.index('if [[ "$INSTANCE" == "JARVIS-u116" ]]') < text.index(
        'IMAGE="seal-user-clone:1.3.4"'
    )
    assert '      "$IMAGE" \\' in text


def test_claude_broker_is_scoped_fail_closed_and_provider_credential_zero() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'CLAUDE_ENABLED="/etc/seal/claude-u116.enabled"' in text
    assert 'systemctl is-active --quiet seal-claude-u116-broker.service' in text
    assert 'getent group seal-claude-u116-client' in text
    assert 'getent group seal-u116-chat-client' in text
    assert '--group-add "$BROKER_GID"' in text
    assert '--group-add "$CHAT_RELAY_GID"' in text
    assert '[[ "$BROKER_GID" != "$CHAT_RELAY_GID" ]]' in text
    assert 'unix:///run/soul-broker/u116.sock' in text
    assert 'NETWORK_ARGS=(--network none)' in text
    assert 'unix:///run/soul-relay/u116.sock' in text
    assert 'systemctl is-active --quiet seal-u116-chat-relay.service' in text
    assert '--broker-capability-file /run/secrets/claude-broker-capability' in text
    assert '--broker-capability-gid "$BROKER_GID"' in text
    assert 'src=$CLAUDE_CAPABILITY' in text
    assert "stat -c '%u:%g:%a' \"$CLAUDE_CAPABILITY\"" not in text
    assert '--model claude-sonnet-5' in text
    assert 'anthropic-u116.key' not in text
    assert 'consent.json' not in text
    assert text.count('if [[ "$INSTANCE" == "JARVIS-u116" ]]') == 1


def test_preflight_resolves_the_instance_scoped_image_not_first_literal() -> None:
    sys.path.insert(0, str(RUNNER.parents[1] / "agents/NEXUS"))
    import gate_arranque_clon as gate

    text = RUNNER.read_text(encoding="utf-8")
    u116 = types.SimpleNamespace(agente="JARVIS", usuario="u116")
    u103 = types.SimpleNamespace(agente="JARVIS", usuario="u103")
    assert gate.brazo_p6_imagen_fijada(u116, text).evidencia["tag"] == "1.3.4"
    assert gate.brazo_p6_imagen_fijada(u103, text).evidencia["tag"] == "1.3.2"
