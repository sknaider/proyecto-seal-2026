#!/bin/bash
# NEXUS — read-only retention scope on the 5070 (run via: bash this.sh)
cp /mnt/c/Users/Dadito/scope_retention_5070.py /home/dadito/soul_vision_5070/scope_retention_5070.py
cd /home/dadito/soul_vision_5070 || { echo NO_SV; exit 2; }
set -a; . /home/dadito/.config/seal/vision_db.env 2>/dev/null; set +a
venv/bin/python3 scope_retention_5070.py
echo "RET_SCOPE_EXIT=$?"
