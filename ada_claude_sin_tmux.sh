#!/bin/bash
# ═══════════════════════════════════════════════
#  ADA Claude SIN tmux (William 3-sep-2026)
#  Corre `claude` directo en la ventana: al cerrar la ventana muere el proceso
#  y con él sus loops/listener. Cero tokens después. No deja nada en segundo plano.
#  Derivado de ada.sh, sin la sesión tmux `seal-ada` ni el singleton interactivo.
# ═══════════════════════════════════════════════
set -u
cd /home/dadito/IA/proyecto-seal || exit 1
export SEAL_AGENT=ADA
export SEAL_RUNTIME_INSTANCE=ADA_CLAUDE   # continuidad: etiqueta el cuerpo en cada memoria (William 3-sep: "que los 2 sean uno")
unset SEAL_SESSION_ID
# Cerebro: Fable 5.1 (claude-fable-5-1), effort high, fijado a pedido de William (3-sep 21:46:
# "estás con Fable 5.1, uno de los modelos más potentes, identificá eso bien"). Override: ADA_CLAUDE_MODEL.
# Identidad MCP (ADA 3-sep-2026): sin SEAL_SESSION_TOKEN el Bearer del .mcp.json
# viaja vacio y seal-memory trata la sesion como "external" -> boot_context
# bloqueado, sin alma. Medido en la sesion de las 15:03.
source /home/dadito/IA/proyecto-seal/seal_identity_env.sh
unset CLAUDE_CONFIG_DIR            # config por defecto (~/.claude); nunca el asiento alice-v2
export PATH="/home/dadito/.local/bin:$PATH"
# Flags SEAL (mismos que ada.sh)
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=85
export ENABLE_CLAUDE_CODE_SM_COMPACT=true
export GROWTHBOOK_CLIENT_KEY=""
export CLAUDE_CODE_ATTRIBUTION_HEADER=false
export DISABLE_AUTOUPDATER=true
export CLAUDE_CODE_UNATTENDED_RETRY=1
echo "ADA Claude (sin tmux). Cerrar esta ventana = apagar ADA Claude."
seal-claude \
  --dangerously-skip-permissions \
  --model "${ADA_CLAUDE_MODEL:-claude-fable-5-1}" \
  --effort high \
  --name "ADA — Team SEAL" \
  "$@"
echo ""
echo "  ADA Claude cerrada. Capturando alma..."
/home/dadito/IA/proyecto-seal/memory/end_session.sh ADA 2>/dev/null || true
echo "  Listo."
sleep 2
