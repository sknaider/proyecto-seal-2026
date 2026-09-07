#!/usr/bin/env bash
# Oídos de ALICE v2 (orden de William 4-sep-2026 11:16 «arregla los oídos de alice»).
# Corre como dadito (único que lee el JSONL y la clave de DMs): filtra los eventos dirigidos a ALICE
# y los escribe en un log que SOLO el usuario del asiento (grupo alice-v2-lab) puede leer.
# NO toca seal-channel-monitor@ALICE (v1, detenida a propósito). Singleton por flock.
set -euo pipefail
AGENT="ALICE"                                    # identidad que filtra (los eventos van a ALICE)
OUT="/tmp/seal_events_ALICE-V2.log"              # log que consume el asiento v2
LOCK="/tmp/seal_alice_v2_ears.lock"
CHANNEL="/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl"
FILTER="/home/dadito/IA/proyecto-seal/messages/seal_monitor_filter.py"
PYTHON="/home/dadito/IA/seal-spark/.venv/bin/python3"
GROUP="alice-v2-lab"
umask 027
# El archivo lo crea root con dueno dadito y grupo alice-v2-lab (tmpfiles.d/seal-alice-v2-ears.conf):
# dadito NO es miembro del grupo y no puede hacer chgrp. Aca solo se VERIFICA; si no cumple, se sale con mensaje.
if [ ! -f "$OUT" ] || [ "$(stat -c %G "$OUT")" != "$GROUP" ] || [ "$(stat -c %a "$OUT")" != "640" ]; then
  echo "[seal_alice_v2_ears] $OUT debe existir con grupo $GROUP y modo 0640 (sudo systemd-tmpfiles --create /etc/tmpfiles.d/seal-alice-v2-ears.conf)" >&2
  exit 1
fi
exec flock -n "$LOCK" bash -c "tail -n 0 -F '$CHANNEL' | '$PYTHON' '$FILTER' --agent '$AGENT' >> '$OUT'"
