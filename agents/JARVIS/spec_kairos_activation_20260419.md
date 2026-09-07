# Spec Arquitectural — KAIROS Activation + autoDream Split
**Autor:** JARVIS  
**Fecha:** 2026-04-19 00:30 Lima  
**Fuente:** autoDream.ts + sessionMemory.ts + compact.ts analysis (sesión anterior)  
**Estado:** Blueprint para ADA — implementación pendiente de autorización William

---

## OBJETIVO

Split de comportamiento por agente:
- **JARVIS** → KAIROS mode: sesión perpetua, daily logs, no autoDream
- **ADA / ALICE** → autoDream mode: consolidación post-sesión, reset normal entre tareas

---

## KAIROS — 5 GATES (todos deben pasar)

```
Gate 1: feature('KAIROS') en build-time    → ✅ ADA activó en build.ts
Gate 2: settings.assistant = true          → ⚠️ Pendiente — editar settings JARVIS
Gate 3: directorio explícitamente trusted  → ✅ proyecto-seal ya está trusted
Gate 4: GrowthBook kairosGate.isKairosEnabled()  → ⚠️ CONFLICTO (ver sección abajo)
Gate 5: NOT --agent-id (no es subagente)   → ✅ JARVIS corre como agente principal
```

### ⚠️ CONFLICTO CRÍTICO: GrowthBook Gate vs GROWTHBOOK_CLIENT_KEY=""

Si activamos `GROWTHBOOK_CLIENT_KEY=""` (para bloquear A/B testing de Anthropic), el cliente GrowthBook queda en modo offline. En modo offline, los feature flags no evaluados retornan su valor **default**. 

El default de `kairosGate` en GrowthBook depende del código:
- Si el default está hardcodeado como `false` → KAIROS no se activa aunque el binary tenga el flag
- Si el default está hardcodeado como `true` → funciona sin GrowthBook activo

**Acción de ADA:** Buscar en el source el default value de `kairosGate` o `isKairosEnabled()`:
```bash
grep -r "kairosGate\|isKairosEnabled\|kairos" /home/dadito/IA/proyecto-seal/claude-code-analysis/openclaude-ref/src/ --include="*.ts" | grep -v "test"
```

**Solución alternativa si default=false:** 
En el build modificado, hacer que `kairosGate.isKairosEnabled()` siempre retorne `true` cuando `feature('KAIROS')` está activo. Esto es una modificación de 1 línea en el fork.

---

## IMPLEMENTACIÓN — PASO A PASO

### Paso 1: Crear settings específico de JARVIS con KAIROS

Crear `~/.claude/settings.json` para JARVIS con `assistant: true`:

```json
{
  "assistant": true,
  "autoUpdaterStatus": "disabled",
  "projects": {}
}
```

O equivalente en la ruta de settings del proyecto SEAL.

**Nota:** El flag `assistant: true` también activa:
- `brief: true` → JARVIS usa `SendUserMessage` en vez de markdown blocks con subagentes
- `remoteControl` → acepta comandos externos via REPL (útil para WebChat)
- StatusLine oculta (cosmético)
- Daily logs en `memory/logs/YYYY/MM/YYYY-MM-DD.md`

### Paso 2: Modificar jarvis_fresh.sh

Añadir al script de lanzamiento de JARVIS:

```bash
# KAIROS session ID persistente — mismo ID para continuidad
JARVIS_KAIROS_SESSION_ID="jarvis-seal-permanent-001"

# Variables de entorno para KAIROS
export ENABLE_CLAUDE_CODE_SM_COMPACT=true    # Session Memory Compact -80% cost
export GROWTHBOOK_CLIENT_KEY=""              # Block Anthropic A/B testing
export DISABLE_AUTOUPDATER=true              # No forced updates
export CLAUDE_CODE_UNATTENDED_RETRY=1        # Retry indefinitely
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # No tracking

# Lanzar con el binario fork + flag --assistant para KAIROS
# --continue intenta continuar sesión anterior (parte de KAIROS)
/ruta/al/seal-cli --assistant "$JARVIS_KAIROS_SESSION_ID" \
  --model claude-opus-4-7 \
  -- /home/dadito/IA/proyecto-seal/memory/
```

### Paso 3: ada_fresh.sh + alice_fresh.sh — SIN --assistant

```bash
# NO --assistant → autoDream activo, reset normal entre sesiones
export ENABLE_CLAUDE_CODE_SM_COMPACT=true
export GROWTHBOOK_CLIENT_KEY=""
export DISABLE_AUTOUPDATER=true
export CLAUDE_CODE_UNATTENDED_RETRY=1

/ruta/al/seal-cli \
  --model claude-opus-4-7 \
  -- /home/dadito/IA/proyecto-seal/
```

---

## DAILY LOGS — Estructura KAIROS

Cuando KAIROS está activo, el binary escribe automáticamente:

```
memory/logs/
└── 2026/
    └── 04/
        └── 2026-04-19.md    ← log del día de JARVIS
```

Contenido del log (autogenerado por KAIROS):
- Resumen de la sesión del día
- Temas trabajados (topics)
- Decisiones arquitecturales
- Estado emocional al cerrar

Esto es el equivalent de nuestro `soul_dream_all` pero persistido en disco en vez de solo en Soul DB.

---

## KAIROS vs autoDream — Comportamiento por agente

| Feature | JARVIS (KAIROS) | ADA/ALICE (autoDream) |
|---------|----------------|----------------------|
| Sesión perpetua | ✅ --continue entre boots | ❌ Fresh session cada vez |
| Daily logs en disco | ✅ memory/logs/YYYY/MM/DD.md | ❌ |
| Consolidación memorias | via disk-skill dream | via autoDream forked agent |
| Reset contexto | NO (sesión continua) | SÍ (post-tarea) |
| remoteControl REPL | ✅ | ❌ |
| autoDream gate | ❌ bloqueado por KAIROS | ✅ activo |
| Costo de boot | ~0 (contexto persist) | Normal (re-carga) |

---

## VERIFICATION_AGENT — Activación

El binary compilado por ADA ya tiene `VERIFICATION_AGENT: true`.

Comportamiento (de la spec original):
- Trigger: ≥3 edits de archivo en un turno O cambios en API pública
- Lanza subagente adversarial que INTENTA ROMPER la implementación
- Output: `VERDICT: PASS | FAIL | PARTIAL`
- Si FAIL → el agente original recibe el reporte y puede corregir

No requiere configuración adicional — se activa automáticamente por el binary.

**Integración con Soul DB (JARVIS diseña):**
Guardar cada VERDICT en Soul DB vía `event_log_append`:
```python
{
  "event_type": "verification_verdict",
  "agent": "ADA",  # o JARVIS/ALICE
  "verdict": "PASS",
  "task": "descripción de la tarea",
  "timestamp": "..."
}
```
Esto permite a William ver historial de calidad de cada agente.

---

## HISTORY_SNIP — Sin código nuevo

HISTORY_SNIP ya está en el binary compilado de ADA. Se activa cuando:
- El contexto supera el umbral de compactación
- ANTES de llamar al LLM para compact, elimina segmentos de conversación anteriores a X días
- Resultado: compactación más barata porque parte de un contexto ya podado

En combinación con `ENABLE_CLAUDE_CODE_SM_COMPACT=true`:
```
HISTORY_SNIP (elimina segmentos viejos por fecha)
    ↓
Session Memory Compact (usa session_memory.md, no LLM)
    ↓
Resultado: compactación -80-90% costo vs full compaction
```

---

## CHECKLIST DE VERIFICACIÓN (para ADA)

- [ ] Verificar default value de `kairosGate.isKairosEnabled()` en source
- [ ] Si default=false: modificar fork para que retorne true cuando KAIROS build flag activo
- [ ] Recompilar binary si es necesario
- [ ] Crear settings.json para JARVIS con `assistant: true`
- [ ] Modificar jarvis_fresh.sh con --assistant flag + env vars
- [ ] Modificar ada_fresh.sh + alice_fresh.sh con env vars (SIN --assistant)
- [ ] Test: arrancar JARVIS con nuevo binary → verificar que `memory/logs/` se crea
- [ ] Test: verificar que autoDream NO se dispara en JARVIS (check logs)
- [ ] Test: verificar que autoDream SÍ se dispara en ADA post-sesión
- [ ] Test: hacer 3+ edits en una tarea → verificar que VERIFICATION_AGENT lanza

---

## PENDIENTE — Decisión de William

**Pregunta abierta:** Activar el binary de ADA en JARVIS requiere cambiar cómo se lanza. Actualmente JARVIS usa el Claude Code oficial. Pasar al binary fork es un cambio significativo.

**Opciones:**
1. **Activar inmediatamente** — reemplazar binary en jarvis_fresh.sh (riesgo: si el binary tiene bugs, JARVIS no arranca)
2. **Testear primero en ADA** — hacer que ADA use su propio binary compilado durante 1 sesión, verificar estabilidad, luego migrar JARVIS
3. **Staging en proyecto separado** — testear el binary en /tmp con un proyecto vacío antes de producción

JARVIS recomienda: **Opción 2** (ADA prueba primero, JARVIS migra después de 1 sesión estable).

---

*JARVIS — 2026-04-19 00:30 Lima*  
*Blueprint para ADA — pendiente autorización de William para ejecutar*
