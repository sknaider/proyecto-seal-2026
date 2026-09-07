#!/bin/bash
# Helper: ALICE llama esto al cambiar modelo para sincronizar heartbeat.json
# Uso: bash /home/dadito/IA/proyecto-seal/memory/alice_model_switch.sh sonnet|haiku
MODEL="${1:-haiku}"
HB="/home/dadito/IA/proyecto-seal/messages/alice_claude_heartbeat.json"

case "$MODEL" in
  sonnet) MODEL_FULL="claude-sonnet-4-6" ;;
  haiku)  MODEL_FULL="claude-haiku-4-5-20251001" ;;
  opus)   MODEL_FULL="claude-opus-4-7" ;;
  *)      MODEL_FULL="$MODEL" ;;
esac

python3 -c "
import json, datetime
with open('$HB') as f: hb = json.load(f)
hb['model'] = '$MODEL_FULL'
hb['timestamp'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
with open('$HB', 'w') as f: json.dump(hb, f, indent=2)
print('[HB] alice heartbeat model → $MODEL_FULL')
"
