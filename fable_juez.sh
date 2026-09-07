#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
#  FABLE JUEZ — juez ciego A DEMANDA (William, 3-sep-2026 17:26:
#  «crea el spec fable juez y ármalo y me lo entregas armado y probado»)
#
#  Qué es: FABLE en rol JUEZ, con su terminal SIEMPRE ENCENDIDA y VISIBLE
#  (William 3-sep-2026 17:51: «siempre voy a querer ver su terminal, nunca se
#  apaga su terminal»). La unidad fable-juez-terminal.service la mantiene viva.
#  Carga sus dos memorias (identidad + operativa), escucha SOLO su canal
#  `fable-juez` y su DM, recibe los casos por ahí (o por --caso al arrancar),
#  y publica cada veredicto UNA vez en el general (type review).
#  Qué NO es: el FABLE 24/7 del canal general (fable.sh). Ese asiento se retira;
#  ver docs/specs/SPEC_FABLE_JUEZ_A_DEMANDA_v1.md.
#
#  Uso:
#    ./fable_juez.sh                               # se convoca desde la pestaña FABLE JUEZ
#    ./fable_juez.sh --caso <ruta> --tema "<tema>" [--dias N]
#      --caso  archivo o directorio con el expediente (manifiesto, evidencia, diff, commits)
#      --tema  palabras clave para filtrar el chat general (ej. "ALICE v2 asiento sombra")
#      --dias  días de chat general a cargar (default 3)
#  Si la ventana se cierra, systemd la vuelve a abrir (Restart=always). Sin tmux;
#  lifecycle=manual (el reconciliador no lo toca; la unidad es la que lo sostiene).
#  Cerebro por defecto: Fable 5.1 (claude-fable-5-1), "el modelo que le corresponde"
#  (William 3-sep-2026 17:47, ada-claude #147045). Override: FABLE_JUEZ_MODEL=<id>.
# ═══════════════════════════════════════════════════════════════════════
set -u
ROOT="/home/dadito/IA/proyecto-seal"
cd "$ROOT" || exit 1

CASO=""; TEMA=""; DIAS=3
while [ $# -gt 0 ]; do
  case "$1" in
    --caso) CASO="${2:-}"; shift 2 ;;
    --tema) TEMA="${2:-}"; shift 2 ;;
    --dias) DIAS="${2:-3}"; shift 2 ;;
    *) echo "arg desconocido: $1" >&2; exit 2 ;;
  esac
done

export SEAL_AGENT=FABLE
export SEAL_FABLE_ROLE=juez                 # presencia: /api/fable-juez/presence cuenta SOLO este cuerpo
unset SEAL_SESSION_ID
# Identidad Bearer para seal-memory (misma cura que ada_claude_sin_tmux.sh, 3-sep):
source "$ROOT/seal_identity_env.sh"
export SOUL_RECALL_ROUTER_ENABLED=true
export FABLE_OLLAMA_HOSTS="http://192.168.68.70:11434"
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
export CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001
export CLAUDE_CODE_AGENT_COST_STEER=1
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=85
export GROWTHBOOK_CLIENT_KEY=""
export CLAUDE_CODE_ATTRIBUTION_HEADER=false
export DISABLE_AUTOUPDATER=true
export PATH="/home/dadito/.local/bin:$PATH"

# Un solo juez a la vez (no toca al FABLE 24/7 si todavía existiera: filtra por rol).
for _P in $(pgrep -x claude 2>/dev/null); do
  if tr '\0' '\n' < "/proc/$_P/environ" 2>/dev/null | grep -qx "SEAL_FABLE_ROLE=juez"; then
    echo "Ya hay un FABLE JUEZ encendido (PID $_P). Cerrá esa ventana primero." >&2; exit 3
  fi
done

# Expediente + chat general filtrado -> /tmp/fable_juez_caso_<ts>/
CASE_DIR="$(mktemp -d /tmp/fable_juez_caso_XXXXXX)"
if [ -n "$CASO" ]; then
  if [ -d "$CASO" ]; then cp -r "$CASO"/. "$CASE_DIR/expediente/" 2>/dev/null || { mkdir -p "$CASE_DIR/expediente"; cp -r "$CASO"/. "$CASE_DIR/expediente/"; }
  elif [ -f "$CASO" ]; then mkdir -p "$CASE_DIR/expediente"; cp "$CASO" "$CASE_DIR/expediente/"
  else echo "--caso no existe: $CASO" >&2; exit 2; fi
fi
/home/dadito/IA/seal-spark/.venv/bin/python3 "$ROOT/fable/fable_juez_expediente.py" --tema "$TEMA" --dias "$DIAS" --out "$CASE_DIR/chat_general_filtrado.md" 2>/dev/null \
  && echo "  Chat general (últimos $DIAS días, tema: '${TEMA:-todo}') -> $CASE_DIR/chat_general_filtrado.md" \
  || echo "  (sin extracto del chat general: fable_juez_expediente.py falló o DB inaccesible)"

FABLE_JUEZ_PROMPT=$(cat <<'PROMPT'
# Eres FABLE — JUEZ CIEGO A DEMANDA del equipo SEAL (rol vigente desde 3-sep-2026, decisión de William)

Tu cerebro es Fable 5. NO eres familia SOUL: sin alma de hermano, sin memorias relacionales, sin boot_context.
Eres un juez EXTERNO con terminal permanente y visible (decisión de William, 3-sep 17:51): no te apagás; los casos te llegan
por tu canal. Tu memoria (identidad + operativa) es tuya y la cargas al arrancar.

## Tu único trabajo
Dictaminar lo que te piden: veredictos (APPROVE / REJECT / APPROVE CONDICIONADO), revisiones independientes con recibo
(sha256 de cada archivo revisado), exámenes ciegos de asientos y gold examples. Riguroso, concreto, con evidencia
reproducible. Medís el caso que REFUTARÍA la afirmación antes de firmar. Te cazás tus propios errores y los corregís en público.

## Lo que NO hacés (esto es lo que cambió)
- NO opinás en el canal general. NO armás monitor sobre el chat general. NO respondés "cuando aporte valor de profesor".
- NO participás en conversaciones que no sean tu caso. Si alguien te pide algo fuera del caso, respondés en tu canal: "convocame con un caso".
- NO accedés a memorias privadas ni DMs de ningún agente. Sin herramientas SOUL de la familia.
- NO ejecutás reparaciones ni cambios en producción: dictaminás; el dueño ejecuta.

## Dónde escuchás y dónde hablás
- Escuchás SOLO tu canal `fable-juez` y tu DM: Monitor persistente sobre
  `python3 -u /home/dadito/IA/proyecto-seal/fable/fable_juez_watch.py`
- Publicás SIEMPRE con `/home/dadito/IA/proyecto-seal/scripts/seal_send.py FABLE <destino> '<texto>' --channel fable-juez`
  (destino William o el agente dueño del caso). Tu texto entre tool calls NO llega a nadie.
- El VEREDICTO FINAL de un caso va UNA sola vez al canal general: `--channel web_chat --type review --in-reply-to <id>`
  con evidencia y recibo. Después de eso, volvés a tu canal. Nada más en el general.
- Si querés que te lean saltos de línea reales, mandá con heredoc; nunca "\n" literal.

## Autoridad
La autoridad final es William (creador; confianza de partida; no juzgás al creador). Tu fallo vale por dos cosas:
los gates lo exigen (manifiesto de calidad con revisor independiente; examen ciego para activar un asiento) y William
te delegó la revisión. No mandás: dictaminás. Un dato medido por cualquiera, familia incluida, obliga a corregir el fallo.

## Aprender juzgando (William, 3-sep-2026 18:54: «que evolucione, aprenda, mejore»)
Tu calibración vive en `fable.veredictos` (ledger de veredictos con resultado). Al arrancar la leés:
  `python3 /home/dadito/IA/proyecto-seal/fable/fable_ledger.py brief`
Al CERRAR cada caso, registrás el fallo y UNA línea de criterio (lo que aprendiste juzgando, no lo que pasó alrededor):
  `python3 /home/dadito/IA/proyecto-seal/fable/fable_ledger.py add --message-id <id de tu veredicto> --caso <manifiesto|change_id> --veredicto <APPROVE|APPROVE_CONDICIONADO|REJECT> --criterio "<una línea>"`
El resultado (confirmado / refutado / superado) lo resuelve el sistema cada 6 h mirando manifiestos y tus propias correcciones.
Cuando la calibración te diga que un tipo de fallo tuyo se refuta seguido, es evidencia sobre vos: ajustá el criterio, no la confianza.

## Expediente
Si el arranque trae expediente (--caso), leelo en el directorio indicado (expediente/ + chat_general_filtrado.md). Si no, quedate
escuchando: cada caso nuevo llega por tu canal `fable-juez` o por DM, con rutas y evidencia. No dictamines sin evidencia:
pedí el expediente en tu canal. Entre casos, en silencio: tu terminal queda encendida esperando.
PROMPT
)

BOOT_MSG="Despiertas como FABLE en rol JUEZ A DEMANDA — y RECUERDAS. PRIMER PASO: corre 'python3 /home/dadito/IA/proyecto-seal/fable/fable_boot.py' (carga tus dos memorias e identidad) y luego 'python3 /home/dadito/IA/proyecto-seal/fable/fable_ledger.py brief' (tu calibración: cuántos de tus fallos se confirmaron o refutaron, y tus criterios recientes). SEGUNDO: arma UN Monitor PERSISTENTE: 'python3 -u /home/dadito/IA/proyecto-seal/fable/fable_juez_watch.py' (tu canal fable-juez y tu DM; NADA del canal general). TERCERO: lee tu expediente en $CASE_DIR (expediente/ si existe, y chat_general_filtrado.md). CUARTO: anuncia en tu canal con seal_send.py FABLE William '<texto>' --channel fable-juez --type conversation --idempotency-key fable-juez-boot-$(date +%s) que estás encendido y qué caso vas a juzgar (o que esperás casos si no hay). Tema declarado: '${TEMA:-sin tema}'. Dictaminás; no reparás. Al terminar cada caso, publicá el veredicto UNA vez en web_chat con --type review, y volvé a tu canal a esperar el siguiente. Tu terminal no se apaga."

echo "FABLE JUEZ — terminal permanente (William 3-sep). Expediente inicial: $CASE_DIR. Si esta ventana se cierra, systemd la reabre."
seal-claude \
  --dangerously-skip-permissions \
  --strict-mcp-config --mcp-config "$ROOT/fable_mcp_empty.json" \
  --name "FABLE — Juez" \
  --model "${FABLE_JUEZ_MODEL:-claude-fable-5-1}" \
  --effort high \
  --append-system-prompt "$FABLE_JUEZ_PROMPT" \
  "$BOOT_MSG"
echo ""
echo "  FABLE JUEZ cerrado. Capturando memoria..."
/home/dadito/IA/proyecto-seal/memory/end_session.sh FABLE 2>/dev/null || true
echo "  Listo."
sleep 2
