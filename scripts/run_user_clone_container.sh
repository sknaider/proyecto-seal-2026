#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
INSTANCE="${2:-}"
if [[ ! "$INSTANCE" =~ ^(ALICE|FABLE|JARVIS|NEXUS)-u([1-9][0-9]*)$ ]]; then
  echo "invalid isolated clone instance: $INSTANCE" >&2
  exit 64
fi

AGENT="${BASH_REMATCH[1]}"
USER_ID="${BASH_REMATCH[2]}"
AGENT_LOWER="${AGENT,,}"
CONTAINER="seal-${AGENT_LOWER}-u${USER_ID}-clone"
TOKEN="/home/dadito/IA/proyecto-seal/messages/.agent_session_token_${AGENT}-u${USER_ID}"
USER_ROOT="/home/dadito/.local/share/seal/users/u${USER_ID}/instances/${AGENT}"
STATE_ROOT="/home/dadito/.local/state/seal/user-clones/${AGENT}-u${USER_ID}"
DEFAULT_PROJECTION="/home/dadito/.local/share/seal/user-clone-projections/technical.sqlite3"
INSTANCE_PROJECTION="/home/dadito/.local/share/seal/user-clone-projections/technical_${INSTANCE}.sqlite3"
if [[ -f "$INSTANCE_PROJECTION" ]]; then
  PROJECTION="$INSTANCE_PROJECTION"
else
  PROJECTION="${SEAL_USER_CLONE_PROJECTION:-$DEFAULT_PROJECTION}"
fi
VOICE_ARGS=()
MODEL_ARGS=()
BROKER_MOUNTS=()
BROKER_RUNTIME_ARGS=()
NETWORK_ARGS=(--network host)
IMAGE="seal-user-clone:1.3.2"
if [[ "$INSTANCE" == "JARVIS-u116" ]]; then
  IMAGE="seal-user-clone:1.3.4"
  VOICE_ARGS=(--voice-profile /app/voice_profiles/JARVIS-u116.json)
  NETWORK_ARGS=(--network none)
  CLAUDE_ENABLED="/etc/seal/claude-u116.enabled"
  CLAUDE_SOCKET_DIR="/run/seal-claude-u116"
  CLAUDE_SOCKET="$CLAUDE_SOCKET_DIR/u116.sock"
  CLAUDE_CAPABILITY="/etc/seal/claude-u116/client/client.capability"
  CHAT_RELAY_DIR="/run/seal-u116-chat-relay"
  if [[ "$ACTION" == "start" && -f "$CLAUDE_ENABLED" ]]; then
    [[ "$(stat -c '%u:%a' "$CLAUDE_ENABLED")" == "0:444" ]] || {
      echo "unsafe Claude activation marker permissions" >&2
      exit 78
    }
    [[ "$(<"$CLAUDE_ENABLED")" == "JARVIS-u116 claude-sonnet-5" ]] || {
      echo "invalid Claude activation marker" >&2
      exit 78
    }
    systemctl is-active --quiet seal-claude-u116-broker.service || {
      echo "Claude u116 system broker is not active" >&2
      exit 78
    }
    systemctl is-active --quiet seal-u116-chat-relay.service || {
      echo "u116 chat relay is not active" >&2
      exit 78
    }
    BROKER_GID="$(getent group seal-claude-u116-client | cut -d: -f3)"
    [[ "$BROKER_GID" =~ ^[1-9][0-9]*$ ]] || { echo "Claude broker client group missing" >&2; exit 78; }
    # The launcher intentionally is not in the secret-bearing client group.
    # Root validates file custody during provisioning/activation; Docker mounts
    # the root-owned capability and the worker revalidates owner/GID/mode.
    BROKER_RUNTIME_ARGS=(--group-add "$BROKER_GID")
    BROKER_MOUNTS=(
      --mount "type=bind,src=$CLAUDE_SOCKET_DIR,dst=/run/soul-broker,readonly"
      --mount "type=bind,src=$CLAUDE_CAPABILITY,dst=/run/secrets/claude-broker-capability,readonly"
      --mount "type=bind,src=$CHAT_RELAY_DIR,dst=/run/soul-relay,readonly"
    )
    MODEL_ARGS=(
      --model claude-sonnet-5
      --ollama-url unix:///run/soul-broker/u116.sock
      --broker-capability-file /run/secrets/claude-broker-capability
      --broker-capability-gid "$BROKER_GID"
      --chat-url unix:///run/soul-relay/u116.sock
      --inbox-url unix:///run/soul-relay/u116.sock
    )
  elif [[ "$ACTION" == "start" ]]; then
    echo "JARVIS-u116 is on security HOLD: Claude activation marker absent" >&2
    exit 78
  fi
fi

case "$ACTION" in
  start)
    [[ -f "$TOKEN" ]] || { echo "missing instance token: $TOKEN" >&2; exit 78; }
    [[ -f "$PROJECTION" ]] || { echo "missing technical projection: $PROJECTION" >&2; exit 78; }
    install -d -m 0700 "$USER_ROOT" "$STATE_ROOT"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    exec docker run --rm \
      --name "$CONTAINER" \
      "${NETWORK_ARGS[@]}" \
      --read-only \
      --user 1000:1000 \
      --cap-drop ALL \
      --security-opt no-new-privileges:true \
      --pids-limit 128 \
      --memory 4g \
      --cpus 2 \
      --tmpfs /tmp:rw,noexec,nosuid,size=64m \
      "${BROKER_RUNTIME_ARGS[@]}" \
      --mount "type=bind,src=$TOKEN,dst=/run/secrets/instance-token,readonly" \
      --mount "type=bind,src=$USER_ROOT,dst=/data/users/u${USER_ID}/instances/${AGENT}" \
      --mount "type=bind,src=$STATE_ROOT,dst=/data/state" \
      --mount "type=bind,src=$PROJECTION,dst=/opt/seal/projection/technical.sqlite3,readonly" \
      "${BROKER_MOUNTS[@]}" \
      "$IMAGE" \
      --agent "$AGENT" \
      --user-id "$USER_ID" \
      --instance-token-file /run/secrets/instance-token \
      --user-root /data/users \
      --state-dir /data/state \
      --technical-projection /opt/seal/projection/technical.sqlite3 \
      "${VOICE_ARGS[@]}" \
      "${MODEL_ARGS[@]}"
    ;;
  stop)
    docker stop -t 10 "$CONTAINER" >/dev/null 2>&1 || true
    ;;
  *)
    echo "usage: $0 start|stop AGENT-uID" >&2
    exit 64
    ;;
esac
