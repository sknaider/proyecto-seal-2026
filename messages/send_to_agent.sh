#!/bin/bash
# send_to_agent.sh — send a message to an agent's kitty terminal with Enter
# Usage: send_to_agent.sh <agent_name> <message>
# Example: send_to_agent.sh NEXUS "check your webchat"
# Always appends \r (Enter) — required or agent won't process the message

set -e

AGENT="${1:?Usage: send_to_agent.sh <AGENT> <message>}"
MSG="${2:?Usage: send_to_agent.sh <AGENT> <message>}"
AGENT_UPPER="${AGENT^^}"
KITTY_SOCK="/tmp/seal-${AGENT_LOWER:=$(echo "$AGENT" | tr '[:upper:]' '[:lower:]')}-kitty.sock"

if [ ! -S "$KITTY_SOCK" ]; then
    echo "ERROR: kitty socket not found: $KITTY_SOCK" >&2
    exit 1
fi

kitten @ --to "unix:$KITTY_SOCK" send-text "${MSG}"$'\r'
