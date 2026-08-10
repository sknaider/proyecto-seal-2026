import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/activate_claude_u116.sh"
PROVISION = ROOT / "scripts/provision_claude_u116_broker.sh"
DROPIN = ROOT / "systemd/seal-user-clone@JARVIS-u116.service.d/10-claude-broker.conf"
BROKER_UNIT = ROOT / "systemd/seal-claude-u116-broker.service"


def test_activation_requires_root_signed_consent_and_auth_negative_first() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert '[[ "$EUID" -eq 0 ]]' in text
    assert '[[ "${1:-}" == "--activate" ]]' in text
    assert 'cd "$REPO"' in text
    assert text.index('cd "$REPO"') < text.index("from messages.claude_u116_broker")
    assert "load_api_key" in text and "load_client_capability" in text
    assert "load_signed_consent" in text
    assert text.index("assert probe({})[0] == 401") < text.index(
        "JARVIS-u116 claude-sonnet-5"
    )
    assert text.index("enable --now seal-u116-chat-relay.service seal-claude-u116-broker.service") < text.index(
        "JARVIS-u116 claude-sonnet-5"
    )
    assert "anthropic-u116.key" not in text
    assert re.search(r"sk-ant-[A-Za-z0-9_-]{12,}", text) is None
    assert "seal-claude-u116:seal-claude-u116-client:660" in text
    assert "seal-u116-chat-relay:seal-claude-u116-client:660" in text
    restart = text.index("enable --now seal-user-clone@JARVIS-u116.service")
    live_gate = text.index("{{.HostConfig.NetworkMode}}")
    final = text.index("activation verified network=none")
    assert restart < live_gate < final
    assert 'if [[ "$live_network_mode" != "none" ]]' in text
    gate_block = text[text.index('if [[ "$live_network_mode"'):final]
    assert "rollback 78" in gate_block
    rollback = text[text.index("rollback() {"):text.index("trap rollback ERR")]
    assert 'rm -f "$MARKER"' in rollback
    assert 'rm -f "$MARKER" "$DROPIN_TARGET"' not in rollback
    assert "disable --now seal-claude-u116-broker.service" in rollback
    assert "disable --now seal-u116-chat-relay.service" in rollback
    assert "disable --now seal-user-clone@JARVIS-u116.service" in rollback


def test_system_service_has_dedicated_identity_root_credentials_and_hardening() -> None:
    text = BROKER_UNIT.read_text(encoding="utf-8")
    assert "User=seal-claude-u116" in text
    assert "Group=seal-claude-u116-client" in text
    assert "LoadCredential=anthropic.key:/etc/seal/claude-u116/anthropic.key" in text
    assert "LoadCredential=client.capability:" in text
    assert "ProtectHome=true" in text
    assert "ProtectSystem=strict" in text
    assert "/usr/local/libexec/seal/claude_u116_broker.py" in text
    assert "/home/dadito" not in text
    assert "WantedBy=multi-user.target" in text
    assert "%h/.config" not in text and "%t/" not in text


def test_provisioning_is_disabled_and_does_not_create_key_or_consent() -> None:
    text = PROVISION.read_text(encoding="utf-8")
    assert "useradd --system" in text
    assert "systemctl disable --now seal-claude-u116-broker.service" in text
    assert "systemctl disable --now seal-u116-chat-relay.service" in text
    assert "disable --now seal-user-clone@JARVIS-u116.service" in text
    assert 'chown root:seal-claude-u116-client "$CLIENT_DIR/client.capability"' in text
    assert 'chmod 0440 "$CLIENT_DIR/client.capability"' in text
    assert "activated=NO" in text
    assert "anthropic.key" in text and "consent.json" in text
    assert "sk-ant-" not in text


def test_clone_dropin_checks_system_broker_without_cross_manager_requires() -> None:
    text = DROPIN.read_text(encoding="utf-8")
    assert "ExecStartPre=/usr/bin/systemctl is-active --quiet" in text
    assert "seal-u116-chat-relay.service" in text
    assert "ConditionPathExists=/etc/seal/claude-u116.enabled" in text
    assert "Requires=seal-claude-u116-broker.service" not in text
