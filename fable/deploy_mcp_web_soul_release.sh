#!/bin/sh
set -eu

# Root account tooling such as usermod/groupadd lives in /usr/sbin on Debian.
# Define the complete trusted system path here so callers may invoke this
# orchestrator through an empty environment without changing its behaviour.
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

STAGE=${SEAL_MCP_WEB_SOUL_STAGED_ROOT:?canonical staged root required}
EXPECTED=${SEAL_MCP_WEB_SOUL_BUNDLE_SHA256:?reviewed bundle manifest sha256 required}
ROTATION_ID=${1:?usage: deploy_mcp_web_soul_release.sh ROTATION_ID}
BOOTSTRAP=/usr/local/sbin/seal-mcp-web-soul-stage

test "$(id -u)" -eq 0 || { echo "run as root" >&2; exit 2; }
test "$(stat -c '%U:%G:%a' "$BOOTSTRAP")" = root:root:755 \
  || { echo "trusted root-owned stage launcher unavailable" >&2; exit 3; }
/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/python3 -I "$BOOTSTRAP" verify-stage --stage-root "$STAGE" \
  --expected-manifest-sha256 "$EXPECTED" >/dev/null

# Every privileged byte below comes from the canonical root-owned stage. The
# working tree is never executed as root.
/bin/sh "$STAGE/fable/install_mcp_web_soul_cdp.sh"
/bin/sh "$STAGE/fable/install_mcp_web_soul_witness.sh"
/opt/seal/mcp-web-soul/current/bin/python3 \
  "$STAGE/fable/rotate_mcp_web_soul_operator_credential.py" \
  --rotation-id "$ROTATION_ID"
/bin/sh "$STAGE/fable/install_mcp_web_soul_operator.sh"

systemctl is-active --quiet seal-audit-witness.service
systemctl is-active --quiet seal-mcp-web-soul-operator.service
echo "mcp-web-soul release deployed from $EXPECTED"
