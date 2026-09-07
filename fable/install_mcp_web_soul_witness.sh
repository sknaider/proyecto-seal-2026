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
MODULE="$REPO/fable/mcp_web_soul_witness.py"
UNIT="$REPO/fable/systemd/seal-audit-witness.service"
STATE=/var/lib/seal-audit-witness/witness.sqlite3
IMPORT_ROOT=/var/lib/seal-audit-witness-import

if test "${SEAL_WITNESS_ALLOW_LIVE_CDP_RESTART:-0}" != 1; then
  /usr/bin/python3 - <<'PY'
from pathlib import Path

live = []
for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
    try:
        argv = cmdline.read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        continue
    if "seal_cdp_mcp.py" in argv:
        live.append(cmdline.parent.name)
if live:
    raise SystemExit(
        "refusing witness restart while CDP stdio sessions are live: " + ",".join(live)
    )
PY
fi
/bin/sh "$REPO/fable/install_mcp_web_soul_runtime.sh"
systemctl stop seal-audit-witness.service 2>/dev/null || :
getent group seal-audit-client >/dev/null 2>&1 || groupadd --system seal-audit-client
getent group seal-audit-witness >/dev/null 2>&1 || groupadd --system seal-audit-witness
getent group seal-mcp-web-control >/dev/null 2>&1 || groupadd --system seal-mcp-web-control
getent group seal-mcp-web-cdp-readers >/dev/null 2>&1 || groupadd --system seal-mcp-web-cdp-readers
getent group seal-mcp-web-cdp >/dev/null 2>&1 || groupadd --system seal-mcp-web-cdp
id seal-audit-witness >/dev/null 2>&1 || \
  useradd --system --gid seal-audit-witness --home-dir /nonexistent --shell /usr/sbin/nologin seal-audit-witness
usermod -g seal-audit-witness -G seal-audit-client -d /nonexistent -s /usr/sbin/nologin -L seal-audit-witness
getent group seal-mcp-web-operator >/dev/null 2>&1 || groupadd --system seal-mcp-web-operator
id seal-mcp-web-operator >/dev/null 2>&1 || \
  useradd --system --gid seal-mcp-web-operator --groups seal-audit-client,seal-mcp-web-control \
    --home-dir /nonexistent --shell /usr/sbin/nologin seal-mcp-web-operator
usermod -g seal-mcp-web-operator -G seal-audit-client,seal-mcp-web-control \
  -d /nonexistent -s /usr/sbin/nologin -L seal-mcp-web-operator
id seal-mcp-web-cdp >/dev/null 2>&1 || \
  useradd --system --gid seal-mcp-web-cdp \
    --groups seal-audit-client,seal-mcp-web-control,seal-mcp-web-cdp-readers \
    --home-dir /var/lib/seal-mcp-web-cdp/home --shell /usr/sbin/nologin seal-mcp-web-cdp
usermod -g seal-mcp-web-cdp -G seal-audit-client,seal-mcp-web-control,seal-mcp-web-cdp-readers \
  -d /var/lib/seal-mcp-web-cdp/home -s /usr/sbin/nologin -L seal-mcp-web-cdp
usermod -a -G seal-mcp-web-cdp-readers dadito
gpasswd -M seal-mcp-web-cdp,seal-mcp-web-operator seal-mcp-web-control >/dev/null
control_members=$(getent group seal-mcp-web-control | cut -d: -f4 | tr ',' '\n' | sed '/^$/d' | sort | tr '\n' ' ' | sed 's/ $//')
test "$control_members" = 'seal-mcp-web-cdp seal-mcp-web-operator' \
  || { echo "control group membership contract invalid" >&2; exit 4; }
control_gid=$(getent group seal-mcp-web-control | cut -d: -f3)
primary_control_members=$(getent passwd | awk -F: -v gid="$control_gid" '$4==gid {print $1}')
test -z "$primary_control_members" \
  || { echo "control group must not be any account's primary group" >&2; exit 4; }
for identity in seal-audit-witness seal-mcp-web-operator seal-mcp-web-cdp; do
  case "$identity" in
    seal-audit-witness) expected_groups='seal-audit-client seal-audit-witness'; expected_home=/nonexistent ;;
    seal-mcp-web-operator) expected_groups='seal-audit-client seal-mcp-web-control seal-mcp-web-operator'; expected_home=/nonexistent ;;
    seal-mcp-web-cdp) expected_groups='seal-audit-client seal-mcp-web-cdp seal-mcp-web-cdp-readers seal-mcp-web-control'; expected_home=/var/lib/seal-mcp-web-cdp/home ;;
  esac
  actual_groups=$(id -nG "$identity" | tr ' ' '\n' | sort | tr '\n' ' ' | sed 's/ $//')
  test "$actual_groups" = "$expected_groups" || { echo "$identity group contract invalid" >&2; exit 4; }
  entry=$(getent passwd "$identity")
  test "$(printf '%s' "$entry" | cut -d: -f6)" = "$expected_home" \
    && test "$(printf '%s' "$entry" | cut -d: -f7)" = /usr/sbin/nologin \
    || { echo "$identity account contract invalid" >&2; exit 4; }
  passwd -S "$identity" | awk '$2=="L" || $2=="LK" {ok=1} END {exit !ok}' \
    || { echo "$identity account must be locked" >&2; exit 4; }
done
install -d -o root -g root -m 0755 /usr/local/libexec/seal
install -o root -g root -m 0755 "$MODULE" /usr/local/libexec/seal/mcp_web_soul_witness.py
install -o root -g root -m 0644 "$UNIT" /etc/systemd/system/seal-audit-witness.service
install -d -o seal-audit-witness -g seal-audit-witness -m 0700 /var/lib/seal-audit-witness
install -d -o root -g seal-audit-witness -m 0750 "$IMPORT_ROOT"

# Root copies legacy inputs through the tested anti-TOCTOU primitive. The
# SQLite migration itself then runs as the witness UID.
for source in \
  /home/dadito/IA/proyecto-seal/var/mcp-web-soul/audit/events.jsonl \
  /home/dadito/IA/proyecto-seal/var/mcp-web-soul/audit/operator.jsonl; do
  test -e "$source" || continue
  destination="$IMPORT_ROOT/$(basename "$source")"
  /opt/seal/mcp-web-soul/current/bin/python3 "$REPO/fable/mcp_web_soul_deploy.py" \
    copy-stable-file --source "$source" --destination "$destination" >/dev/null
  chown root:seal-audit-witness "$destination"
  chmod 0640 "$destination"
done

runuser -u seal-audit-witness -- /usr/bin/env -i \
  PATH=/usr/local/bin:/usr/bin:/bin PYTHONPATH=/usr/local/libexec/seal \
  /opt/seal/mcp-web-soul/current/bin/python3 - "$STATE" "$IMPORT_ROOT" <<'PY'
import json, sys
from pathlib import Path

from mcp_web_soul_witness import WitnessStore, seed_and_backfill_jsonl

store = WitnessStore(sys.argv[1])
try:
    import pwd
    import_root = Path(sys.argv[2])
    bindings = (
        ('audit:0cf0c41fefd3040587ddb86fec62bb6cbc9b23913b01e2928958f58b59c13b67', 'seal-mcp-web-operator'),
        ('audit:4f292c4bb17ea06a28c7fe13d4084c1a012c63dc041508749403b15ff5063054', 'seal-mcp-web-cdp'),
    )
    imports = (
        (import_root / 'events.jsonl', Path('/home/dadito/IA/proyecto-seal/var/mcp-web-soul/audit/events.jsonl'), 'seal-mcp-web-cdp'),
        (import_root / 'operator.jsonl', Path('/home/dadito/IA/proyecto-seal/var/mcp-web-soul/audit/operator.jsonl'), 'seal-mcp-web-operator'),
    )
    # Non-empty legacy streams must seed the exact head before bind_stream;
    # pre-claiming sequence zero would conflict with every real history.
    for path, logical, owner in imports:
        if path.is_file():
            print(json.dumps(seed_and_backfill_jsonl(
                store, path, owner_uid=pwd.getpwnam(owner).pw_uid, stream_path=logical,
            ), sort_keys=True))
    for stream, owner in bindings:
        result = store.bind_stream(stream, pwd.getpwnam(owner).pw_uid)
        store.verify(stream, int(result['sequence']), str(result['head_hash']), owner_uid=int(result['owner_uid']))
finally:
    store.close()
PY
for path in "$STATE" "$STATE-wal" "$STATE-shm"; do
  test ! -e "$path" || { chown seal-audit-witness:seal-audit-witness "$path"; chmod 0600 "$path"; }
done
test ! -L "$STATE" && test "$(stat -c '%U:%G:%a' "$STATE")" = "seal-audit-witness:seal-audit-witness:600" || {
  echo "witness state ownership or mode invalid" >&2; exit 7;
}
systemctl daemon-reload
# ``systemctl enable`` may block behind a manager reload even when the unit is
# already enabled.  Keep recovery idempotent and only mutate enablement when
# the persistent symlink is actually absent.
systemctl is-enabled --quiet seal-audit-witness.service \
  || systemctl enable seal-audit-witness.service
systemctl restart seal-audit-witness.service
systemctl is-active --quiet seal-audit-witness.service
for binding in \
  'seal-mcp-web-operator audit:0cf0c41fefd3040587ddb86fec62bb6cbc9b23913b01e2928958f58b59c13b67' \
  'seal-mcp-web-cdp audit:4f292c4bb17ea06a28c7fe13d4084c1a012c63dc041508749403b15ff5063054'; do
  set -- $binding
  runuser -u "$1" -- /usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin PYTHONPATH=/usr/local/libexec/seal \
    /opt/seal/mcp-web-soul/current/bin/python3 - "$2" <<'PY'
import sys
import time
from mcp_web_soul_witness import WitnessClient

# systemd considers the process active before the daemon has completed the
# socket ownership handoff.  Retry only this readiness probe for a bounded
# interval; persistent permission/auth failures still fail the installer.
deadline = time.monotonic() + 5.0
while True:
    try:
        result = WitnessClient('/run/seal-audit-witness/witness.sock').request(
            {'op': 'head', 'stream': sys.argv[1]}
        )
        assert result['ok'] is True
        break
    except (OSError, AssertionError, KeyError):
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.1)
PY
done
