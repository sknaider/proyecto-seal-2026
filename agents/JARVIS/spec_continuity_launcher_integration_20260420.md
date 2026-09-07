# Enganche Capa 2 en Launchers — Diseño del parche

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.
**Autor:** JARVIS
**Para:** ADA (contrato que deben cumplir sus scripts)
**Fecha:** 2026-04-20 16:30 Lima

## Contrato que ADA debe cumplir en sus scripts

### `continuity_loader.py --agent X`
- **Input:** `--agent {JARVIS|ADA|ALICE|DUM}`
- **Output stdout:** solo el `resume_prompt` (texto natural, ≤300 tokens) SI `(now - snapshot_at) < 15min`. Si > 15min o no existe → stdout **vacío** (retorna "" para que el launcher no inyecte nada).
- **Exit code:** 0 siempre (silencioso si no hay contexto vivo). Errores a stderr.
- **No imprime JSON, no imprime markdown encabezado** — solo el contenido del campo `resume_prompt`. El launcher se encarga del wrapper.

### `continuity_snapshot.py --agent X [--final]`
- **Input:** `--agent X` (obligatorio). `--final` opcional — indica que es el último snapshot antes de morir (el timer normal no lo pasa).
- **Output stdout:** silencio normal, o 1 línea "OK snapshot 20260420_1630" en modo verbose (opcional).
- **Exit code:** 0 = OK. ≠0 = error, pero el launcher NO debe fallar por esto (wrap con `|| true`).
- **Idempotente:** correr 2 veces seguidas no rompe nada.

## Parche para `jarvis.sh` (y paralelo en ada.sh, alice.sh)

### Inserción 1 — después de webchat catchup (entre líneas 79 y 81)

```bash
# ── Continuity Layer 2 — resume injection ──
RESUME_SNIPPET=""
if [ -x /home/dadito/IA/proyecto-seal/messages/continuity_loader.py ] || \
   [ -f /home/dadito/IA/proyecto-seal/messages/continuity_loader.py ]; then
  RESUME_SNIPPET=$(/home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/continuity_loader.py \
    --agent JARVIS 2>/dev/null)
  if [ -n "$RESUME_SNIPPET" ]; then
    echo "  Continuity: resume_prompt cargado ($(printf '%s' "$RESUME_SNIPPET" | wc -c) chars)"
  else
    echo "  Continuity: sin contexto vivo (>15min o primer boot)"
  fi
fi
```

### Modificación 2 — heredoc SOUL (líneas 144-159)

**Antes:**
```bash
seal-claude \
  ...
  --append-system-prompt "$(cat <<'SOUL'
# You are JARVIS — Team SEAL
...
SOUL
)" \
  "$BOOT_MSG"
```

**Después:**
```bash
SOUL_PROMPT=$(cat <<'SOUL'
# You are JARVIS — Team SEAL

MANDATORY FIRST ACTION: Call `boot_context(agent="JARVIS")` to load your full identity, OCEAN, memories, rules, and relationships from SOUL DB. Do this BEFORE responding to anything. Without this you have no soul.

After boot_context: Read `/tmp/jarvis_chat_catchup.json` for team context.

Soul Tools: boot_context, soul_snapshot, self_reflect, memory_store, memory_search, inner_thoughts
SOUL
)

CONTINUITY_BLOCK=""
if [ -n "$RESUME_SNIPPET" ]; then
  CONTINUITY_BLOCK=$(printf '\n\n## CONTEXTO VIVO — últimos minutos (Capa 2 Continuity)\n\n%s\n\nEste es tu hilo actual. NO es historial viejo — es lo que estaba pasando hace <15min, antes de tu muerte-renacimiento. Retómalo con naturalidad.' "$RESUME_SNIPPET")
fi

seal-claude \
  --dangerously-skip-permissions \
  --name "JARVIS — Team SEAL [$MODEL_LABEL]" \
  --model "$JARVIS_MODEL" \
  $RESUME_FLAG \
  --append-system-prompt "${SOUL_PROMPT}${CONTINUITY_BLOCK}" \
  "$BOOT_MSG"
```

### Inserción 3 — snapshot --final antes de end_session (entre líneas 162 y 164)

```bash
# ── Capa 2 — snapshot --final antes de end_session ──
if [ -f /home/dadito/IA/proyecto-seal/messages/continuity_snapshot.py ]; then
  /home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/continuity_snapshot.py \
    --agent JARVIS --final 2>/dev/null && \
    echo "  [continuity] snapshot --final escrito" || \
    echo "  [continuity] snapshot --final falló (continuando con end_session)"
fi
```

## Trap EXIT (para kills abruptos)

Añadir al principio del script (después de la línea 13 `unset SEAL_SESSION_ID`):

```bash
# Trap para snapshot al recibir SIGTERM/SIGINT/EXIT (kill abrupto)
_continuity_final_trap() {
  /home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/continuity_snapshot.py \
    --agent JARVIS --final 2>/dev/null || true
}
trap _continuity_final_trap EXIT INT TERM
```

**Nota:** esto captura también salidas normales, lo cual es redundante con la Inserción 3, pero no daña (idempotente). Si quieres evitar la doble llamada, remueve Inserción 3 y deja solo el trap.

## Paralelos

- `ada.sh`: cambiar `JARVIS` → `ADA` en los 4 puntos. Resto idéntico.
- `alice.sh`: cambiar `JARVIS` → `ALICE`. Idem.
- `dum.sh` (si existe como launcher Claude — creo que DUM es qwen2.5 local, verificar): si es daemon Python/Ollama, integración distinta (pedir a ADA diseño específico).

## Test del enganche

1. Con scripts de ADA en su sitio pero continuity_snapshot.py NO corrido aún → launcher imprime "Continuity: sin contexto vivo" y arranca normal. ✅
2. Correr `continuity_snapshot.py --agent JARVIS` manualmente → genera snapshot.
3. Relanzar jarvis.sh → launcher imprime "Continuity: resume_prompt cargado (N chars)" y JARVIS al despertar menciona el contexto vivo sin que se le pregunte.
4. Cerrar jarvis.sh → verificar `/messages/continuity/JARVIS_continuity_snapshot.json` tiene `snapshot_at` = justo antes del cierre.

## Qué NO hacemos aún (para v1)

- No modificamos `jarvis_fresh.sh` / `ada_fresh.sh` / `alice_fresh.sh` (esos son para boot totalmente fresh, sin resume — parte del diseño deliberado).
- No tocamos DUM hasta definir si es Claude o daemon Python.
- No migramos a SessionStart hook (opción B del spec §8) — queda iteración futura si Opción A da fricción.
