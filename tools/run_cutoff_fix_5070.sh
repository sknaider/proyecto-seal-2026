#!/bin/bash
# NEXUS — deploy ts_wall-cutoff fix to the 5070 + test by effect (run via: bash this.sh)
# Change is VERIFY-only + env-gated; write_event byte-behavior unchanged (safe for live pipeline).
SV=/home/dadito/soul_vision_5070
ENVF=/home/dadito/.config/seal/vision_db.env
CUTOFF="2026-06-28T03:00:00"   # entre last-legacy 02:05:42 y first-post-fix 05:16:24 (gap limpio)

cp /mnt/c/Users/Dadito/event_writer.py "$SV/event_writer.py";            echo "EW_COPIED=$?"
cp /mnt/c/Users/Dadito/seal_vision_custody_verify.py "$SV/seal_vision_custody_verify.py"; echo "CV_COPIED=$?"

cd "$SV" || { echo NO_SV; exit 2; }

# persist el corte per-DB en vision_db.env (idempotente)
grep -q SOUL_VISION_HASH_FORMAT_CUTOFF_TS "$ENVF" 2>/dev/null \
  || echo "SOUL_VISION_HASH_FORMAT_CUTOFF_TS=\"$CUTOFF\"" >> "$ENVF"
echo "ENV_LINE:"; grep SOUL_VISION_HASH_FORMAT_CUTOFF_TS "$ENVF"

set -a; . "$ENVF" 2>/dev/null; set +a
echo "CUTOFF_IN_ENV=$SOUL_VISION_HASH_FORMAT_CUTOFF_TS"

# test POR EFECTO: verify debe dar OK (0 breaks) + DISCLOSE legacy_skipped>0
SEAL_SEND_WEBCHAT=/dev/null venv/bin/python3 seal_vision_custody_verify.py
echo "VERIFY_EXIT=$?"
