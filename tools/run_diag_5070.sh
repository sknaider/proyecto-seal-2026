#!/bin/bash
# NEXUS — run JARVIS's custody diagnostic on the 5070 (run via: bash this.sh)
cp /mnt/c/Users/Dadito/diagnose_custody_mismatch.py /home/dadito/soul_vision_5070/diagnose_custody_mismatch.py
cd /home/dadito/soul_vision_5070 || { echo NO_SV; exit 2; }
set -a
. /home/dadito/.config/seal/vision_db.env 2>/dev/null
set +a
venv/bin/python3 diagnose_custody_mismatch.py 36 134
echo "DIAG_EXIT=$?"
