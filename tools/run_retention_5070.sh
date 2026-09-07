#!/bin/bash
cp /mnt/c/Users/Dadito/seal_vision_retention.py /home/dadito/soul_vision_5070/seal_vision_retention.py
cd /home/dadito/soul_vision_5070 || { echo NO_SV; exit 2; }
set -a; . /home/dadito/.config/seal/vision_db.env 2>/dev/null; set +a
echo "--- DRY-RUN age>90d ---"; venv/bin/python3 seal_vision_retention.py --age-days 90
echo "--- DRY-RUN age>0d (todo, para verificar que el tool lista) ---"; venv/bin/python3 seal_vision_retention.py --age-days 0
echo "RET_EXIT=$?"
