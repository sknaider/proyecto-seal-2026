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
LIBEXEC=/usr/local/libexec/seal
STATE_ROOT=/var/lib/seal-mcp-web-cdp
LEGACY_ROOT=/home/dadito/IA/proyecto-seal/var/mcp-web-soul
FROZEN_ROOT=$STATE_ROOT/legacy-frozen
SUDOERS_SOURCE="$REPO/fable/sudoers/seal-mcp-web-cdp"
SUDOERS_TARGET=/etc/sudoers.d/seal-mcp-web-cdp

test -x /usr/bin/python3 || { echo "/usr/bin/python3 unavailable" >&2; exit 3; }
test -x /usr/sbin/visudo || { echo "visudo unavailable" >&2; exit 3; }
if test ! -e "$STATE_ROOT/control/control.sqlite3"; then
  test -f "$FROZEN_ROOT/control.sqlite3" || test -f "$LEGACY_ROOT/control.sqlite3" || {
    echo "legacy control.sqlite3 missing" >&2; exit 6;
  }
fi

# Establish the start barrier before the first process scan.  The current
# candidate refuses shared-UID startup while this root-owned marker exists, and
# the old witness is stopped so any stale copy fails its required pre-effect
# audit rather than mutating a split control plane.
install -d -o root -g root -m 0755 /run/seal-mcp-web-soul-cutover
install -o root -g root -m 0444 /dev/null /run/seal-mcp-web-soul-cutover/legacy-disabled
systemctl stop seal-audit-witness.service 2>/dev/null || :

# Freeze both legacy writers before any control-plane snapshot. A CDP stdio
# process cannot be stopped safely by the installer because its parent owns the
# protocol stream; fail before touching identities or state.
/usr/bin/python3 - <<'PY'
from pathlib import Path
live=[]
for path in Path('/proc').glob('[0-9]*/cmdline'):
    try: argv=path.read_bytes().replace(b'\0',b' ').decode(errors='replace')
    except OSError: continue
    if 'seal_cdp_mcp.py' in argv:
        live.append(f'{path.parent.name}:{argv.strip()}')
if live:
    raise SystemExit('stop all legacy CDP writers before cutover: '+'; '.join(live))
PY
/bin/sh "$REPO/fable/install_mcp_web_soul_runtime.sh"
if test -d /run/user/1000; then
  runuser -u dadito -- env XDG_RUNTIME_DIR=/run/user/1000 \
    systemctl --user stop seal-mcp-web-soul-operator.service 2>/dev/null || :
fi
if test -e "$STATE_ROOT/control/control.sqlite3"; then
  /opt/seal/mcp-web-soul/current/bin/python3 "$REPO/fable/mcp_web_soul_deploy.py" \
    read-snapshot-cursor --database "$STATE_ROOT/control/control.sqlite3" >/dev/null
fi
/usr/bin/python3 - <<'PY'
from pathlib import Path
live=[]
for path in Path('/proc').glob('[0-9]*/cmdline'):
    try: argv=path.read_bytes().replace(b'\0',b' ').decode(errors='replace')
    except OSError: continue
    if 'seal_cdp_mcp.py' in argv or 'mcp_web_soul_operator.py' in argv:
        live.append(f'{path.parent.name}:{argv.strip()}')
if live:
    raise SystemExit('legacy writer remained alive after freeze: '+'; '.join(live))
PY

# Move the exact snapshot inputs out of the UID-1000-writable repository before
# inspecting them.  chmod on the files alone is not a boundary: their old parent
# permits rename/replacement, and an already-open FD survives chmod.  The frozen
# directory is root-only; after the atomic moves, reject every remaining process
# descriptor to those inodes before SQLite is allowed to read them.
if test ! -e "$STATE_ROOT/control/control.sqlite3"; then
  install -d -o root -g root -m 0700 "$FROZEN_ROOT"
  for name in control.sqlite3 control.key operator.cursor control.sqlite3-wal control.sqlite3-shm; do
    if test ! -e "$FROZEN_ROOT/$name" && test -e "$LEGACY_ROOT/$name"; then
      mv -- "$LEGACY_ROOT/$name" "$FROZEN_ROOT/$name"
    fi
  done
  test -f "$FROZEN_ROOT/control.sqlite3" || { echo "frozen control.sqlite3 missing" >&2; exit 6; }
  test -f "$FROZEN_ROOT/control.key" || { echo "frozen control.key missing" >&2; exit 6; }
  test -f "$FROZEN_ROOT/operator.cursor" || { echo "frozen operator.cursor missing" >&2; exit 6; }
  for path in "$FROZEN_ROOT"/*; do
    test -e "$path" || continue
    test -f "$path" && test ! -L "$path" || { echo "unsafe frozen legacy input" >&2; exit 6; }
    chown root:root "$path"
    chmod 0400 "$path"
  done
  set --
  for path in "$FROZEN_ROOT"/*; do
    test -e "$path" || continue
    set -- "$@" --path "$path"
  done
  # Any inherited writer FD blocks the cutover.
  /opt/seal/mcp-web-soul/current/bin/python3 "$REPO/fable/mcp_web_soul_deploy.py" \
    assert-closed "$@"
  /opt/seal/mcp-web-soul/current/bin/python3 - "$FROZEN_ROOT/control.sqlite3" <<'PY'
import sqlite3, sys
db=sqlite3.connect(sys.argv[1])
try:
    checkpoint = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    if checkpoint is None or int(checkpoint[0]) != 0:
        raise SystemExit(f'legacy WAL checkpoint remained busy: {checkpoint!r}')
    if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
        raise SystemExit('legacy control database failed quick_check')
finally:
    db.close()
PY
fi

for group in seal-audit-client seal-mcp-web-control seal-mcp-web-cdp-readers seal-mcp-web-cdp; do
  getent group "$group" >/dev/null 2>&1 || groupadd --system "$group"
done
id seal-mcp-web-cdp >/dev/null 2>&1 || \
  useradd --system --gid seal-mcp-web-cdp \
    --groups seal-audit-client,seal-mcp-web-control,seal-mcp-web-cdp-readers \
    --home-dir "$STATE_ROOT/home" --shell /usr/sbin/nologin seal-mcp-web-cdp
usermod -g seal-mcp-web-cdp -G seal-audit-client,seal-mcp-web-control,seal-mcp-web-cdp-readers \
  -d "$STATE_ROOT/home" -s /usr/sbin/nologin -L seal-mcp-web-cdp
usermod -a -G seal-mcp-web-cdp-readers dadito
actual_groups=$(id -nG seal-mcp-web-cdp | tr ' ' '\n' | sort | tr '\n' ' ' | sed 's/ $//')
test "$actual_groups" = 'seal-audit-client seal-mcp-web-cdp seal-mcp-web-cdp-readers seal-mcp-web-control' \
  || { echo "CDP group contract invalid" >&2; exit 4; }
entry=$(getent passwd seal-mcp-web-cdp)
test "$(printf '%s' "$entry" | cut -d: -f6)" = "$STATE_ROOT/home" \
  && test "$(printf '%s' "$entry" | cut -d: -f7)" = /usr/sbin/nologin \
  || { echo "CDP account contract invalid" >&2; exit 4; }
passwd -S seal-mcp-web-cdp | awk '$2=="L" || $2=="LK" {ok=1} END {exit !ok}' \
  || { echo "CDP account must be locked" >&2; exit 4; }
control_members=$(getent group seal-mcp-web-control | cut -d: -f4 | tr ',' '\n' | sed '/^$/d')
for member in $control_members; do
  case "$member" in
    seal-mcp-web-cdp|seal-mcp-web-operator) ;;
    *) echo "unexpected control group member: $member" >&2; exit 4 ;;
  esac
done
control_gid=$(getent group seal-mcp-web-control | cut -d: -f3)
primary_control_members=$(getent passwd | awk -F: -v gid="$control_gid" '$4==gid {print $1}')
test -z "$primary_control_members" \
  || { echo "control group must not be any account's primary group" >&2; exit 4; }

# Fail if the dedicated identity ever inherits broad host privilege.
for forbidden in dadito sudo docker incus lxd; do
  if id -nG seal-mcp-web-cdp | tr ' ' '\n' | grep -Fxq "$forbidden"; then
    echo "seal-mcp-web-cdp must not belong to $forbidden" >&2
    exit 4
  fi
done

install -d -o root -g root -m 0755 "$LIBEXEC"
for module in \
  seal_cdp_mcp.py seal_cdp.py mcp_web_soul_security.py mcp_web_soul_witness.py \
  mcp_web_soul_control.py mcp_web_soul_visual.py mcp_web_soul_profiles.py \
  mcp_web_soul_proxy.py; do
  install -o root -g root -m 0755 "$REPO/fable/$module" "$LIBEXEC/$module"
done
install -o root -g root -m 0755 \
  "$REPO/fable/seal_cdp_mcp_wrapper.sh" "$LIBEXEC/seal-cdp-mcp-wrapper"
install -d -o root -g root -m 0755 /etc/seal/mcp-web-soul/config
install -o root -g root -m 0644 \
  "$REPO/fable/config/mcp-web-soul.mcp.json" \
  /etc/seal/mcp-web-soul/config/mcp-web-soul.mcp.json
install -o root -g root -m 0644 \
  "$REPO/fable/config/mcp-web-soul.codex.toml" \
  /etc/seal/mcp-web-soul/config/mcp-web-soul.codex.toml

/usr/sbin/visudo -cf "$SUDOERS_SOURCE" >/dev/null
install -o root -g root -m 0440 "$SUDOERS_SOURCE" "$SUDOERS_TARGET"
/usr/sbin/visudo -cf "$SUDOERS_TARGET" >/dev/null

install -d -o root -g root -m 0711 "$STATE_ROOT"
install -d -o seal-mcp-web-cdp -g seal-mcp-web-cdp -m 0700 \
  "$STATE_ROOT/home" "$STATE_ROOT/home/.config" "$STATE_ROOT/home/.cache" "$STATE_ROOT/runtime" \
  "$STATE_ROOT/state/profiles" "$STATE_ROOT/state/runtime-profiles"
install -d -o seal-mcp-web-cdp -g seal-mcp-web-cdp-readers -m 0710 "$STATE_ROOT/state"
install -d -o seal-mcp-web-cdp -g seal-mcp-web-cdp-readers -m 2750 \
  "$STATE_ROOT/state/audit" "$STATE_ROOT/state/artifacts"
CONTROL_CREATED=0
if test -e "$STATE_ROOT/control/control.sqlite3"; then
  test ! -L "$STATE_ROOT/control" && test ! -L "$STATE_ROOT/control/control.sqlite3" \
    && { test ! -e "$STATE_ROOT/control/control.key" || test ! -L "$STATE_ROOT/control/control.key"; } || {
      echo "existing control state contains a symlink" >&2; exit 7;
    }
  control_contract=$(stat -c '%U:%G:%a' "$STATE_ROOT/control")
  case "$control_contract" in
    seal-mcp-web-cdp:seal-mcp-web-control:2770) ;;
    root:root:700)
      # Restartable intermediate: a prior run may have promoted the verified
      # snapshot (or key) but crashed before the final ownership handoff.
      test -f "$FROZEN_ROOT/control.sqlite3" && test -f "$FROZEN_ROOT/control.key" \
        && test -f "$FROZEN_ROOT/operator.cursor" || {
          echo "incomplete root-owned control state lacks frozen recovery inputs" >&2; exit 7;
        }
      /opt/seal/mcp-web-soul/current/bin/python3 "$REPO/fable/mcp_web_soul_deploy.py" \
        read-snapshot-cursor --database "$STATE_ROOT/control/control.sqlite3" >/dev/null
      CONTROL_CREATED=1
      ;;
    *) echo "existing control directory contract invalid" >&2; exit 7 ;;
  esac
else
  # Root owns the candidate namespace until snapshot+key promotion is complete.
  install -d -o root -g root -m 0700 "$STATE_ROOT/control"
  CONTROL_CREATED=1
fi

# This migration is non-destructive: backup+quick_check+row fingerprints happen
# in a candidate; fsync+atomic rename promotes only a complete snapshot. Cursor
# is stored in that same SQLite candidate, so DB and watermark cannot tear.
if test ! -e "$STATE_ROOT/control/control.sqlite3" && test -f "$FROZEN_ROOT/control.sqlite3"; then
  test -f "$FROZEN_ROOT/control.key" || {
    echo "legacy control.key missing; refusing an unverifiable migration" >&2
    exit 6
  }
  test -f "$FROZEN_ROOT/operator.cursor" || {
    echo "legacy operator.cursor missing; refusing incoherent cutover" >&2; exit 6;
  }
  /opt/seal/mcp-web-soul/current/bin/python3 "$REPO/fable/mcp_web_soul_deploy.py" \
    snapshot-control --source "$FROZEN_ROOT/control.sqlite3" \
    --target "$STATE_ROOT/control/control.sqlite3" --cursor "$FROZEN_ROOT/operator.cursor"
fi
if test ! -e "$STATE_ROOT/control/control.key" && test -f "$FROZEN_ROOT/control.key"; then
  /opt/seal/mcp-web-soul/current/bin/python3 - "$FROZEN_ROOT/control.key" "$STATE_ROOT/control/control.key" <<'PY'
import hashlib, os, secrets, sys
from pathlib import Path
src, dst = map(Path, sys.argv[1:])
before = hashlib.sha256(src.read_bytes()).hexdigest()
candidate = dst.with_name(f'.control.key.{secrets.token_hex(16)}.candidate')
fd = os.open(candidate, os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0), 0o600)
with os.fdopen(fd, 'wb') as out:
    out.write(src.read_bytes()); out.flush(); os.fsync(out.fileno())
after = hashlib.sha256(src.read_bytes()).hexdigest()
if before != after or hashlib.sha256(candidate.read_bytes()).hexdigest() != before:
    candidate.unlink(missing_ok=True); raise SystemExit('control.key changed during copy')
os.replace(candidate, dst)
dirfd=os.open(dst.parent, os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(dirfd)
finally: os.close(dirfd)
PY
fi
if test "$CONTROL_CREATED" = 1; then
  for path in "$STATE_ROOT/control/control.sqlite3" "$STATE_ROOT/control/control.sqlite3-wal" \
              "$STATE_ROOT/control/control.sqlite3-shm" "$STATE_ROOT/control/control.key"; do
    if test -e "$path"; then
      test -f "$path" && test ! -L "$path" || { echo "unsafe promoted control file" >&2; exit 7; }
      chown seal-mcp-web-cdp:seal-mcp-web-control "$path"
      chmod 0660 "$path"
    fi
  done
  chown seal-mcp-web-cdp:seal-mcp-web-control "$STATE_ROOT/control"
  chmod 2770 "$STATE_ROOT/control"
fi

# Validate dependencies under the exact clean environment used by the wrapper.
runuser -u seal-mcp-web-cdp -- /usr/bin/env -i \
  PATH=/usr/local/bin:/usr/bin:/bin HOME="$STATE_ROOT/home" \
  /opt/seal/mcp-web-soul/current/bin/python3 -c 'import cryptography,mcp,websocket'

echo "CDP identity installed; no live MCP session was restarted."
echo "Cutover: stop MCP hosts, run this installer, then adopt the versioned .mcp.json command."
