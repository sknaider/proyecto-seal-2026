#!/bin/bash
# NEXUS — deploy custody-verify 24/7 monitor on the 5070 (run via: bash this.sh)
# Compound logic lives in this file so cmd.exe never parses && / pipes / redirects.
set -u
SV=/home/dadito/soul_vision_5070
SRC=/mnt/c/Users/Dadito/seal_vision_custody_verify.py

cp "$SRC" "$SV/seal_vision_custody_verify.py"
echo "COPIED=$?"

cd "$SV" || { echo "NO_SV_DIR"; exit 2; }
set -a
. /home/dadito/.config/seal/vision_db.env 2>/dev/null
set +a

# one-off verify by effect (alert sender no-op for this deploy test → no false alert)
SEAL_SEND_WEBCHAT=/dev/null venv/bin/python3 seal_vision_custody_verify.py
echo "VERIFY_EXIT=$?"

# install/refresh cron every 10 min (idempotent: drop any prior custody line first)
LINE='*/10 * * * * cd /home/dadito/soul_vision_5070 && set -a && . /home/dadito/.config/seal/vision_db.env && set +a && venv/bin/python3 seal_vision_custody_verify.py >> /home/dadito/custody_verify.log 2>&1'
( crontab -l 2>/dev/null | grep -v seal_vision_custody_verify ; echo "$LINE" ) | crontab -
echo "CRON_RC=$?"
echo "CRON_NOW:"
crontab -l 2>/dev/null | grep custody_verify || echo "  (none)"
