#!/usr/bin/env bash
set -euo pipefail

REPO="/home/dadito/IA/proyecto-seal"
CONFIG="/etc/seal/claude-u116"
MARKER="/etc/seal/claude-u116.enabled"
DROPIN_SOURCE="$REPO/systemd/seal-user-clone@JARVIS-u116.service.d/10-claude-broker.conf"
DROPIN_DIR="/home/dadito/.config/systemd/user/seal-user-clone@JARVIS-u116.service.d"
DROPIN_TARGET="$DROPIN_DIR/10-claude-broker.conf"
SOCKET="/run/seal-claude-u116/u116.sock"
CAPABILITY="$CONFIG/client/client.capability"

[[ "$EUID" -eq 0 ]] || { echo "activation requires root" >&2; exit 77; }
[[ "${1:-}" == "--activate" ]] || { echo "usage: sudo $0 --activate" >&2; exit 64; }
[[ ! -e "$MARKER" ]] || { echo "already activated" >&2; exit 65; }
cd "$REPO"

python3 - "$CONFIG" <<'PY'
import sys
from pathlib import Path
from messages.claude_u116_broker import load_api_key, load_client_capability, load_signed_consent
p = Path(sys.argv[1])
load_api_key(p / "anthropic.key")
load_client_capability(p / "client/client.capability")
load_signed_consent(p / "consent.json", p / "consent.sig", p / "consent-public-key.pem")
print("activation_artifacts=VALID")
PY

user_systemctl() {
  runuser -u dadito -- env XDG_RUNTIME_DIR=/run/user/1000 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user "$@"
}

rollback() {
  local rc="${1:-$?}"
  trap - ERR
  rm -f "$MARKER"
  user_systemctl daemon-reload || true
  user_systemctl disable --now seal-user-clone@JARVIS-u116.service || true
  systemctl disable --now seal-claude-u116-broker.service || true
  systemctl disable --now seal-u116-chat-relay.service || true
  exit "$rc"
}
trap rollback ERR

install -d -o dadito -g dadito -m 0700 "$DROPIN_DIR"
install -o dadito -g dadito -m 0644 "$DROPIN_SOURCE" "$DROPIN_TARGET"
systemctl enable --now seal-u116-chat-relay.service seal-claude-u116-broker.service

for _ in {1..30}; do [[ -S "$SOCKET" ]] && break; sleep 1; done
[[ -S "$SOCKET" ]] || { echo "broker socket not ready" >&2; exit 78; }
[[ -S /run/seal-u116-chat-relay/u116.sock ]] || { echo "chat relay socket not ready" >&2; exit 78; }
[[ "$(stat -c '%U:%G:%a' "$SOCKET")" == \
   "seal-claude-u116:seal-claude-u116-client:660" ]] || {
  echo "broker socket custody mismatch" >&2; exit 78;
}
[[ "$(stat -c '%U:%G:%a' /run/seal-u116-chat-relay/u116.sock)" == \
   "seal-u116-chat-relay:seal-claude-u116-client:660" ]] || {
  echo "chat relay socket custody mismatch" >&2; exit 78;
}

# The negative control must fail before the positive probe is accepted.
python3 - "$SOCKET" "$CAPABILITY" <<'PY'
import http.client, json, socket, sys
sock_path, cap_path = sys.argv[1:]
class C(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(sock_path)
def probe(headers):
    c=C("localhost", timeout=3); c.request("GET", "/health", headers=headers)
    r=c.getresponse(); body=r.read(); c.close(); return r.status, json.loads(body)
assert probe({})[0] == 401
cap=open(cap_path, encoding="ascii").read().strip()
status, body=probe({"Authorization":f"Bearer {cap}","X-SEAL-Instance":"JARVIS-u116"})
assert status == 200 and body == {"ok":True,"instance":"JARVIS-u116","model":"claude-sonnet-5"}
print("broker_auth_negative=401 broker_auth_positive=200")
PY

marker_tmp="$(mktemp /etc/seal/.claude-u116.enabled.XXXXXX)"
printf '%s\n' 'JARVIS-u116 claude-sonnet-5' >"$marker_tmp"
install -o root -g root -m 0444 "$marker_tmp" "$MARKER"
rm -f "$marker_tmp"
user_systemctl daemon-reload
user_systemctl enable --now seal-user-clone@JARVIS-u116.service
container_ready=0
for _ in {1..30}; do
  if docker inspect seal-jarvis-u116-clone >/dev/null 2>&1; then
    container_ready=1
    break
  fi
  sleep 1
done
if [[ "$container_ready" != "1" ]]; then
  echo "u116 container did not start" >&2
  rollback 78
fi
live_network_mode="$(docker inspect seal-jarvis-u116-clone --format '{{.HostConfig.NetworkMode}}')"
if [[ "$live_network_mode" != "none" ]]; then
  echo "LIVE NETWORK GATE FAILED: expected none, got $live_network_mode" >&2
  rollback 78
fi
docker inspect seal-jarvis-u116-clone --format '{{json .Args}} {{json .Mounts}}' \
  | grep -F 'unix:///run/soul-broker/u116.sock' \
  | grep -F 'claude-broker-capability' >/dev/null
echo "JARVIS-u116 Claude activation verified network=none"
trap - ERR
