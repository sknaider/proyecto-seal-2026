#!/usr/bin/env bash
# NEXUS — ADA-Triangle 24h pilot monitor
# Run hourly via cron. Appends metrics to /tmp/ada_triangle_metrics.jsonl.
# Posts a 6h summary to webchat when hour-of-day mod 6 == 0.

set -u
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TS_LIMA="$(TZ=America/Lima date +%H)"
LOG=/tmp/ada_triangle_metrics.jsonl
ENDPOINT="http://192.168.68.70:8001"
SUMMARY_DIR=/tmp/ada_triangle_summaries
mkdir -p "$SUMMARY_DIR"

# 1. Triangle health (uptime probe)
T_START=$(date +%s%3N)
HTTP_CODE=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "$ENDPOINT/v1/models" 2>/dev/null || echo "000")
T_END=$(date +%s%3N)
HEALTH_LATENCY_MS=$((T_END - T_START))
HEALTHY=$([ "$HTTP_CODE" = "200" ] && echo "true" || echo "false")

# 2. Quick chat probe (latency under load)
CHAT_START=$(date +%s%3N)
CHAT_RESP=$(curl -s -m 30 -X POST "$ENDPOINT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-coder","messages":[{"role":"user","content":"reply OK"}],"max_tokens":5,"temperature":0.0}' 2>/dev/null)
CHAT_END=$(date +%s%3N)
CHAT_LATENCY_MS=$((CHAT_END - CHAT_START))
CHAT_OK=$(echo "$CHAT_RESP" | grep -q '"choices"' && echo "true" || echo "false")

# 3. Identity violations (24h count)
VIOLATIONS=0
if [ -f /tmp/ada_identity_violations.jsonl ]; then
    VIOLATIONS=$(awk 'NR>0' /tmp/ada_identity_violations.jsonl 2>/dev/null | wc -l)
fi

# 4. Dual-write Soul DB count last hour (if reasoning_logger active)
DUAL_WRITES=0
if [ -f /tmp/ada_reasoning_traces.jsonl ]; then
    DUAL_WRITES=$(tail -200 /tmp/ada_reasoning_traces.jsonl 2>/dev/null | wc -l)
fi

# 5. Append jsonl record
printf '{"ts":"%s","tier":"triangle","health":%s,"http_code":"%s","health_latency_ms":%d,"chat_ok":%s,"chat_latency_ms":%d,"identity_violations_total":%d,"dual_writes_recent":%d}\n' \
    "$TS" "$HEALTHY" "$HTTP_CODE" "$HEALTH_LATENCY_MS" "$CHAT_OK" "$CHAT_LATENCY_MS" "$VIOLATIONS" "$DUAL_WRITES" \
    >> "$LOG"

# 6. 6-hour summary post (00, 06, 12, 18 Lima)
if [ "$((10#$TS_LIMA % 6))" = "0" ]; then
    LAST6=$(tail -6 "$LOG")
    HEALTHY_COUNT=$(echo "$LAST6" | grep -c '"health":true' || true)
    AVG_LATENCY=$(echo "$LAST6" | grep -oE '"chat_latency_ms":[0-9]+' | awk -F: '{s+=$2;n++} END {if(n>0) printf "%.0f", s/n; else print 0}')
    SUMMARY_FILE="$SUMMARY_DIR/summary_${TS//[:-]/}.txt"
    cat <<MSG > "$SUMMARY_FILE"
NEXUS 6h checkpoint ADA-Triangle ($TS):
- Triangle healthy: $HEALTHY_COUNT/6 hours
- Chat avg latency: ${AVG_LATENCY}ms
- Identity violations total: $VIOLATIONS
- Dual-writes recent: $DUAL_WRITES
MSG
    if [ -x /home/dadito/IA/proyecto-seal/messages/send_webchat.py ]; then
        python3 /home/dadito/IA/proyecto-seal/messages/send_webchat.py NEXUS William "$(cat "$SUMMARY_FILE")"
    fi
fi
