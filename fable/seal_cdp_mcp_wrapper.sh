#!/bin/sh
set -eu

# Root installs this file byte-for-byte at /usr/local/libexec/seal.  The MCP
# host cannot claim an individual agent identity: every UID-1000 caller crosses
# the same OS boundary and is therefore recorded honestly as ``shared-host``.
# Every inherited environment value is discarded before Python starts.
test "$#" -eq 0 || {
  echo "seal-cdp-mcp-wrapper accepts no arguments" >&2
  exit 64
}

umask 0077
# Do not inherit a caller-controlled cwd.  Pydantic settings discovers `.env`
# relative to cwd before the clean environment can protect us; a private or
# hostile caller directory must therefore be structurally irrelevant.
cd /var/lib/seal-mcp-web-cdp/home
exec /usr/bin/env -i \
  PATH=/usr/local/bin:/usr/bin:/bin \
  HOME=/var/lib/seal-mcp-web-cdp/home \
  XDG_CONFIG_HOME=/var/lib/seal-mcp-web-cdp/home/.config \
  XDG_CACHE_HOME=/var/lib/seal-mcp-web-cdp/home/.cache \
  XDG_RUNTIME_DIR=/var/lib/seal-mcp-web-cdp/runtime \
  PYTHONDONTWRITEBYTECODE=1 \
  SEAL_AGENT=shared-host \
  MCP_WEB_SOUL_STATE_ROOT=/var/lib/seal-mcp-web-cdp/state \
  MCP_WEB_SOUL_CONTROL_ROOT=/var/lib/seal-mcp-web-cdp/control \
  MCP_WEB_SOUL_CONTROL_GROUP=seal-mcp-web-control \
  MCP_WEB_SOUL_READER_GROUP=seal-mcp-web-cdp-readers \
  /opt/seal/mcp-web-soul/current/bin/python3 /usr/local/libexec/seal/seal_cdp_mcp.py
