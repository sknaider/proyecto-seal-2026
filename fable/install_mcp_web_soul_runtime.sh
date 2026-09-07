#!/bin/sh
set -eu

RUNTIME_ROOT=/opt/seal/mcp-web-soul
LOCK=${SEAL_MCP_WEB_SOUL_STAGED_ROOT:?staged bundle required}/fable/requirements-mcp-web-soul.lock
test "$(id -u)" -eq 0 || { echo "run as root" >&2; exit 2; }
test -f "$LOCK" || { echo "locked runtime requirements missing" >&2; exit 3; }

LOCK_SHA=$(/usr/bin/sha256sum "$LOCK" | /usr/bin/cut -d' ' -f1)
CANDIDATE="$RUNTIME_ROOT/venv-$LOCK_SHA"
CURRENT="$RUNTIME_ROOT/current"
install -d -o root -g root -m 0755 "$RUNTIME_ROOT"

if ! test -x "$CANDIDATE/bin/python3"; then
  BUILD="$RUNTIME_ROOT/.candidate-$LOCK_SHA-$$"
  trap 'test ! -e "$BUILD" || rm -r --one-file-system "$BUILD"' EXIT HUP INT TERM
  /usr/bin/python3 -m venv "$BUILD"
  "$BUILD/bin/python3" -m pip install --disable-pip-version-check --require-hashes -r "$LOCK"
  "$BUILD/bin/python3" -m pip check
  "$BUILD/bin/python3" - <<'PY'
from importlib.metadata import version
expected = {"asyncpg":"0.31.0", "cryptography":"47.0.0", "mcp":"1.27.0", "websocket-client":"1.9.0"}
observed = {name: version(name) for name in expected}
if observed != expected:
    raise SystemExit(f"runtime direct pins mismatch: {observed!r}")
PY
  chown -R root:root "$BUILD"
  chmod -R go-w "$BUILD"
  mv "$BUILD" "$CANDIDATE"
  trap - EXIT HUP INT TERM
fi
"$CANDIDATE/bin/python3" -m pip check
LINK="$RUNTIME_ROOT/.current-$$"
ln -s "$(basename "$CANDIDATE")" "$LINK"
mv -Tf "$LINK" "$CURRENT"
test "$(readlink -f "$CURRENT")" = "$CANDIDATE"
