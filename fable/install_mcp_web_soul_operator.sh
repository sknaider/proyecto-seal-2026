#!/bin/sh
set -eu

REPO=${SEAL_MCP_WEB_SOUL_STAGED_ROOT:?staged bundle required}
EXPECTED=${SEAL_MCP_WEB_SOUL_BUNDLE_SHA256:?reviewed bundle manifest sha256 required}
BOOTSTRAP=/usr/local/sbin/seal-mcp-web-soul-stage
test "$(id -u)" -eq 0 || { echo "run as root" >&2; exit 2; }
test -x "$BOOTSTRAP" && test "$(stat -c '%U:%G:%a' "$BOOTSTRAP")" = root:root:755 \
  || { echo "trusted root-owned stage launcher unavailable" >&2; exit 3; }
/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/python3 -I "$BOOTSTRAP" \
  verify-stage --stage-root "$REPO" --expected-manifest-sha256 "$EXPECTED" >/dev/null
UNIT=seal-mcp-web-soul-operator.service
SOURCE="$REPO/fable/systemd/$UNIT"
TARGET="/etc/systemd/system/$UNIT"
DSN_SOURCE=${MCP_WEB_SOUL_OPERATOR_DSN_FILE:-/etc/seal/mcp-web-soul-operator/operator.dsn}
CONTROL_ROOT=/var/lib/seal-mcp-web-cdp/control
OPERATOR_STATE=/var/lib/seal-mcp-web-soul-operator

test -S /run/seal-audit-witness/witness.sock || {
  echo "audit witness unavailable" >&2
  exit 3
}
test -r "$DSN_SOURCE" || {
  echo "rotated DSN unavailable; run only the root-owned staged release orchestrator" >&2
  exit 4
}
test "$(stat -c '%u:%a' "$DSN_SOURCE")" = "0:600" || {
  echo "operator DSN must be root-owned mode 0600" >&2; exit 4;
}
/bin/sh "$REPO/fable/install_mcp_web_soul_runtime.sh"

# Apply the byte-bound SECURITY DEFINER boundary as DBA before the restricted
# service can ever be enabled.  Docker control is root-only on this host; the
# migration itself contains transactional negative/positive spoof controls.
test -S /var/run/docker.sock || { echo "local PostgreSQL DBA transport unavailable" >&2; exit 4; }
docker inspect seal-memory-db >/dev/null 2>&1 || { echo "canonical PostgreSQL container unavailable" >&2; exit 4; }
docker exec -i seal-memory-db psql -X -v ON_ERROR_STOP=1 -U seal -d seal_memory \
  < "$REPO/fable/sql/mcp_web_soul_operator_boundary.sql" >/dev/null
function_def=$(docker exec seal-memory-db psql -X -At -U seal -d seal_memory -c \
  "SELECT pg_get_functiondef('soul_v3.mcp_web_soul_operator_commands(bigint)'::regprocedure)")
printf '%s' "$function_def" | grep -Fq 'u.id = 1' \
  && printf '%s' "$function_def" | grep -Fq "u.role, '')) = 'superuser'" \
  && ! printf '%s' "$function_def" | grep -Fq display_name \
  || { echo "operator SQL postcondition failed" >&2; exit 4; }
public_exec=$(docker exec seal-memory-db psql -X -At -U seal -d seal_memory -c \
  "SELECT has_function_privilege('public','soul_v3.mcp_web_soul_operator_commands(bigint)','EXECUTE')")
restricted_exec=$(docker exec seal-memory-db psql -X -At -U seal -d seal_memory -c \
  "SELECT has_function_privilege('svc_mcp_web_soul_operator','soul_v3.mcp_web_soul_operator_commands(bigint)','EXECUTE')")
test "$public_exec:$restricted_exec" = 'f:t' || { echo "operator SQL ACL postcondition failed" >&2; exit 4; }

getent group seal-audit-client >/dev/null 2>&1 || groupadd --system seal-audit-client
getent group seal-mcp-web-control >/dev/null 2>&1 || groupadd --system seal-mcp-web-control
getent group seal-mcp-web-operator >/dev/null 2>&1 || groupadd --system seal-mcp-web-operator
id seal-mcp-web-operator >/dev/null 2>&1 || \
  useradd --system --gid seal-mcp-web-operator --groups seal-audit-client,seal-mcp-web-control \
    --home-dir /nonexistent --shell /usr/sbin/nologin seal-mcp-web-operator
usermod -g seal-mcp-web-operator -G seal-audit-client,seal-mcp-web-control \
  -d /nonexistent -s /usr/sbin/nologin -L seal-mcp-web-operator
actual_groups=$(id -nG seal-mcp-web-operator | tr ' ' '\n' | sort | tr '\n' ' ' | sed 's/ $//')
test "$actual_groups" = 'seal-audit-client seal-mcp-web-control seal-mcp-web-operator' \
  || { echo "operator group contract invalid" >&2; exit 4; }
entry=$(getent passwd seal-mcp-web-operator)
test "$(printf '%s' "$entry" | cut -d: -f6)" = /nonexistent \
  && test "$(printf '%s' "$entry" | cut -d: -f7)" = /usr/sbin/nologin \
  || { echo "operator account contract invalid" >&2; exit 4; }
passwd -S seal-mcp-web-operator | awk '$2=="L" || $2=="LK" {ok=1} END {exit !ok}' \
  || { echo "operator account must be locked" >&2; exit 4; }
gpasswd -M seal-mcp-web-cdp,seal-mcp-web-operator seal-mcp-web-control >/dev/null
control_members=$(getent group seal-mcp-web-control | cut -d: -f4 | tr ',' '\n' | sed '/^$/d' | sort | tr '\n' ' ' | sed 's/ $//')
test "$control_members" = 'seal-mcp-web-cdp seal-mcp-web-operator' \
  || { echo "control group membership contract invalid" >&2; exit 4; }
control_gid=$(getent group seal-mcp-web-control | cut -d: -f3)
primary_control_members=$(getent passwd | awk -F: -v gid="$control_gid" '$4==gid {print $1}')
test -z "$primary_control_members" \
  || { echo "control group must not be any account's primary group" >&2; exit 4; }
test -d "$CONTROL_ROOT" || {
  echo "run install_mcp_web_soul_cdp.sh before the operator installer" >&2
  exit 5
}

install -d -o root -g root -m 0755 /usr/local/libexec/seal
for module in mcp_web_soul_operator.py mcp_web_soul_reconcile.py mcp_web_soul_control.py mcp_web_soul_security.py mcp_web_soul_witness.py mcp_web_soul_deploy.py; do
  install -o root -g root -m 0755 "$REPO/fable/$module" "/usr/local/libexec/seal/$module"
done
install -o root -g root -m 0644 "$SOURCE" "$TARGET"
install -d -o root -g root -m 0700 /etc/seal/mcp-web-soul-operator
test "$DSN_SOURCE" = /etc/seal/mcp-web-soul-operator/operator.dsn || {
  echo "installer refuses to copy credentials; rotate directly into canonical path" >&2; exit 4;
}

install -d -o seal-mcp-web-operator -g seal-mcp-web-operator -m 0700 "$OPERATOR_STATE"
install -d -o seal-mcp-web-operator -g seal-mcp-web-operator -m 0700 "$OPERATOR_STATE/audit"
if ! test -e "$OPERATOR_STATE/operator.cursor"; then
  CURSOR=$(/opt/seal/mcp-web-soul/current/bin/python3 /usr/local/libexec/seal/mcp_web_soul_deploy.py \
    read-snapshot-cursor --database "$CONTROL_ROOT/control.sqlite3")
  runuser -u seal-mcp-web-operator -- /opt/seal/mcp-web-soul/current/bin/python3 \
    /usr/local/libexec/seal/mcp_web_soul_deploy.py write-private-cursor \
    --path "$OPERATOR_STATE/operator.cursor" --value "$CURSOR" \
    --owner-user seal-mcp-web-operator
fi
# The old audit remains immutable at its old path and was backfilled by the
# witness installer. Copying it would change the path-derived stream identity;
# the dedicated operator therefore starts a fresh, empty audit stream.

# The operator and CDP identities share only the control registry. UID 1000 is
# deliberately not a member of this group and cannot forge approvals directly.
for path in "$CONTROL_ROOT/control.key" "$CONTROL_ROOT/control.sqlite3" \
            "$CONTROL_ROOT/control.sqlite3-wal" "$CONTROL_ROOT/control.sqlite3-shm"; do
  if test -e "$path"; then
    chgrp seal-mcp-web-control "$path"
    chmod 0660 "$path"
  fi
done

# Stop the superseded UID-1000 user service before enabling the system unit.
if test -d /run/user/1000; then
  runuser -u dadito -- env XDG_RUNTIME_DIR=/run/user/1000 \
    systemctl --user disable --now "$UNIT" 2>/dev/null || :
fi
# Reassert the exact membership boundary immediately before service start.
control_members=$(getent group seal-mcp-web-control | cut -d: -f4 | tr ',' '\n' | sed '/^$/d' | sort | tr '\n' ' ' | sed 's/ $//')
test "$control_members" = 'seal-mcp-web-cdp seal-mcp-web-operator' \
  || { echo "control group membership changed before operator start" >&2; exit 4; }
primary_control_members=$(getent passwd | awk -F: -v gid="$control_gid" '$4==gid {print $1}')
test -z "$primary_control_members" \
  || { echo "control primary-group membership changed before operator start" >&2; exit 4; }
systemctl daemon-reload
# Avoid re-entering the enable transaction during an idempotent recovery run.
# Restart is still unconditional so the live daemon always consumes the
# reviewed bytes and freshly rotated credential.
systemctl is-enabled --quiet "$UNIT" || systemctl enable "$UNIT"
systemctl restart "$UNIT"
systemctl is-active --quiet "$UNIT"
