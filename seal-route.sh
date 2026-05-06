#!/bin/bash
# seal-route.sh — Task Classifier para Provider Routing H2.1
# Clasifica una tarea en FAST/BALANCED/DEEP y devuelve el modelo óptimo.
#
# Uso:
#   MODEL=$(seal-route.sh "revisa arquitectura del DGM")  → opus
#   MODEL=$(seal-route.sh "ok recibido")                  → haiku
#   MODEL=$(seal-route.sh "implementa endpoint /users")   → sonnet
#
# Tiers:
#   FAST     → haiku    (ACKs, status, logs, monitoreo)
#   BALANCED → sonnet   (código, coordinación, análisis medio)  [DEFAULT]
#   DEEP     → opus     (arquitectura, DGM, decisiones críticas, paper)

TASK="${*:-}"

# DEEP — arquitectura, DGM, decisiones críticas, investigación
if echo "$TASK" | grep -qiE \
  "arquitectura|diseña|spec|roadmap|estrategia|estrategico|\
connectome|ocean.*drift|soul.*critico|\
mejora.*soul|mejorar.*soul|soul.*mejora|sprint.*soul|asimila|\
mcp_server.*v2|\
dgm.*(scoring|loop|nightly)|scoring.*(dgm|dilema)|dilema|benchmark|\
cbsoft|paper|academic|investigacion|\
decision.critica|irreversible|produccion.*critica|\
overnight|training|fine-tun|\
analisis.*profundo|revisar.*completo|audit.*completo"; then
    echo "opus"
    exit 0
fi

# FAST — ACKs, status, heartbeat, monitoreo
if echo "$TASK" | grep -qiE \
  "^(ok|recibido|entendido|perfecto|confirmado|listo|done|gracias|ack|nack)$|\
^(ok|recibido|entendido|perfecto|confirmado) |\
status|heartbeat|alive|ping|monitor|\
tail -[fF]|watch -|\
^ACK|^NACK|\
soul_snapshot|self_reflect.*audit|\
[0-9]+h.*audit|audit [0-9]+h"; then
    echo "haiku"
    exit 0
fi

# DEFAULT: BALANCED
echo "sonnet"
