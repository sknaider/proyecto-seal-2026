#!/bin/bash
# NEXUS — run custody boundary+timing discriminator on the 5070 (run via: bash this.sh)
cp /mnt/c/Users/Dadito/custody_boundary_5070.py /home/dadito/soul_vision_5070/custody_boundary_5070.py
cd /home/dadito/soul_vision_5070 || { echo NO_SV; exit 2; }
set -a
. /home/dadito/.config/seal/vision_db.env 2>/dev/null
set +a
venv/bin/python3 custody_boundary_5070.py
echo "BOUNDARY_EXIT=$?"
