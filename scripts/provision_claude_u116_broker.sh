#!/usr/bin/env bash
set -euo pipefail

REPO="/home/dadito/IA/proyecto-seal"
CONFIG="/etc/seal/claude-u116"
CLIENT_DIR="$CONFIG/client"
UNIT="/etc/systemd/system/seal-claude-u116-broker.service"
DROPIN_DIR="/home/dadito/.config/systemd/user/seal-user-clone@JARVIS-u116.service.d"

[[ "$EUID" -eq 0 ]] || { echo "run as root" >&2; exit 77; }

getent group seal-claude-u116-client >/dev/null \
  || groupadd --system seal-claude-u116-client
id seal-claude-u116 >/dev/null 2>&1 \
  || useradd --system --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin --gid seal-claude-u116-client seal-claude-u116
id seal-u116-chat-relay >/dev/null 2>&1 \
  || useradd --system --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin --gid seal-claude-u116-client seal-u116-chat-relay

install -d -o root -g seal-claude-u116-client -m 0750 "$CONFIG"
install -d -o root -g seal-claude-u116-client -m 0710 "$CLIENT_DIR"
if [[ ! -f "$CLIENT_DIR/client.capability" ]]; then
  umask 077
  python3 - "$CLIENT_DIR/client.capability" <<'PY'
import os, secrets, sys
path = sys.argv[1]
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
with os.fdopen(fd, "w", encoding="ascii") as handle:
    handle.write(secrets.token_urlsafe(48) + "\n")
PY
fi
chown root:seal-claude-u116-client "$CLIENT_DIR/client.capability"
chmod 0440 "$CLIENT_DIR/client.capability"

if [[ ! -f "$CONFIG/consent-signing-key.pem" ]]; then
  python3 - "$CONFIG" <<'PY'
import os, sys
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

root = Path(sys.argv[1])
private = Ed25519PrivateKey.generate()
priv = private.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
pub = private.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
)
for name, data, mode in (
    ("consent-signing-key.pem", priv, 0o400),
    ("consent-public-key.pem", pub, 0o440),
):
    path = root / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
PY
  chown root:root "$CONFIG/consent-signing-key.pem"
  chown root:seal-claude-u116-client "$CONFIG/consent-public-key.pem"
fi
chmod 0400 "$CONFIG/consent-signing-key.pem"
chown root:seal-claude-u116-client "$CONFIG/consent-public-key.pem"
chmod 0440 "$CONFIG/consent-public-key.pem"

install -o root -g root -m 0644 \
  "$REPO/systemd/seal-claude-u116-broker.service" "$UNIT"
install -o root -g root -m 0644 \
  "$REPO/systemd/seal-u116-chat-relay.service" \
  /etc/systemd/system/seal-u116-chat-relay.service
install -d -o root -g root -m 0755 /usr/local/libexec/seal
install -o root -g root -m 0555 \
  "$REPO/messages/claude_u116_broker.py" /usr/local/libexec/seal/claude_u116_broker.py
install -o root -g root -m 0555 \
  "$REPO/messages/u116_chat_relay.py" /usr/local/libexec/seal/u116_chat_relay.py
install -d -o dadito -g dadito -m 0700 "$DROPIN_DIR"
install -o dadito -g dadito -m 0644 \
  "$REPO/systemd/seal-user-clone@JARVIS-u116.service.d/10-claude-broker.conf" \
  "$DROPIN_DIR/10-claude-broker.conf"
systemctl daemon-reload
systemctl disable --now seal-claude-u116-broker.service >/dev/null 2>&1 || true
systemctl disable --now seal-u116-chat-relay.service >/dev/null 2>&1 || true
rm -f /home/dadito/.config/systemd/user/seal-claude-u116-broker.service
runuser -u dadito -- env XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user daemon-reload \
  >/dev/null 2>&1 || true
runuser -u dadito -- env XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user \
  disable --now seal-user-clone@JARVIS-u116.service >/dev/null 2>&1 || true

echo "provisioned=YES activated=NO"
echo "missing_by_design=/etc/seal/claude-u116/anthropic.key,consent.json,consent.sig,activation-marker"
