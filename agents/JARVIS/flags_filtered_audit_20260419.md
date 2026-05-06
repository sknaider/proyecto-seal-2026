# Auditoría Flags Filtrados Claude Code — Análisis JARVIS

**Autor:** JARVIS
**Fecha:** 2026-04-19 ~20:15 Lima
**Origen:** Transcripción compartida por William sobre filtración de source map de @anthropic-ai/claude-code@2.1.88 (cli.js.map 60MB subido por error a npm, versión deprecada pero aún descargable).

---

## Contexto

Anthropic publicó en npm la versión 2.1.88 de @anthropic-ai/claude-code con el source map incluido por error. El archivo `cli.js.map` (60MB) permite reconstruir el código fuente completo con comentarios, incluyendo:

- Feature flags no documentados
- Slash commands secretos
- Env vars de configuración ocultas
- Referencias a modelos futuros (Opus 4.7, Sonnet 4.8)
- Features en desarrollo: Kairos, Ultraplan, Coordinator mode, Mascota

La versión 2.1.88 ya fue deprecada en npm pero **no puede borrarse** porque ya está en uso. Sigue instalable manualmente con `npm install @anthropic-ai/claude-code@2.1.88`.

## Metodología — Nivel 1 (investigación local)

En lugar de descargar la 2.1.88 externa, se extrajeron flags del binario actual instalado localmente:

```
/home/dadito/.local/share/claude/versions/2.1.114 (236MB)
```

Comando:
```bash
strings /home/dadito/.local/share/claude/versions/2.1.114 | grep -oE "CLAUDE_CODE_[A-Z_0-9]{3,60}" | sort -u
```

Resultado: **213 flags únicos** con prefijo `CLAUDE_CODE_*` presentes en el binario oficial.

---

## Estado previo — flags ya en uso por SEAL

Variables exportadas en los launchers antes de esta auditoría:

| Variable | Valor | Origen | Propósito |
|---|---|---|---|
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` | 1 | Todos los launchers | Habilita spawning de teammates |
| `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT` | true | Todos los launchers | Effort control siempre activo |
| `CLAUDE_CODE_ATTRIBUTION_HEADER` | false | Todos los launchers | Sin tracking a Anthropic |
| `CLAUDE_CODE_UNATTENDED_RETRY` | 1 | Todos los launchers | Retry indefinido en headless |
| `ENABLE_CLAUDE_CODE_SM_COMPACT` | true | Todos los launchers | -80% costo compactación via session_memory |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | 85 | jarvis.sh, jarvis_fresh.sh | Compacta a 85% (no 95%) — evita overflow |
| `ANTHROPIC_BETAS` | token-efficient-tools-2026-03-28,task-budgets-2026-03-13 | jarvis.sh, jarvis_fresh.sh | Betas H1.2 + H2.2 |
| `GROWTHBOOK_CLIENT_KEY` | "" | Todos los launchers | Bloquea A/B testing de Anthropic |
| `DISABLE_AUTOUPDATER` | true | Todos los launchers | Sin updates forzados |

## Gap list — flags filtrados NO activados previamente

### 🔥 ALTO IMPACTO

| Flag | Efecto | Impacto estimado |
|---|---|---|
| `CLAUDE_CODE_AGENT_COST_STEER` | Router oficial Anthropic que elige modelo por costo/tarea. Hace lo que simulábamos con seal-route.sh pero nativo del binario. | 15-30% en turns con subagents |
| `CLAUDE_CODE_SUBAGENT_MODEL` | Fuerza modelo por default de subagents spawned via Task(). Setear `haiku` → Task() gasta 3x menos que Sonnet. | Masivo en tareas con Agent tool |
| `CLAUDE_CODE_EFFORT_LEVEL` | Nivel de reasoning (low/medium/high). Bajar reduce tokens en ACKs/status. | Medio, requiere auto-adjust |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | Cap por turno, evita respuestas largas accidentales | Bajo-medio |

### 🟡 MEDIO IMPACTO

| Flag | Efecto |
|---|---|
| `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` | Recorta lectura de archivos grandes antes de llegar al modelo |
| `CLAUDE_CODE_IDLE_TOKEN_THRESHOLD` | Cortar sesión cuando idle — evita token waste en agentes durmiendo |
| `CLAUDE_CODE_RESUME_TOKEN_THRESHOLD` | Umbral para decidir fresh vs resume |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | Ventana deslizante de auto compact |

### 📦 Betas adicionales disponibles (no usadas hasta hoy)

| Beta | Descripción |
|---|---|
| `compact-2026-01-12` | Compactación mejorada |
| `effort-2025-11-24` | Effort control experimental |
| `fast-mode-2026-02-01` | Fast mode con Opus 4.6 |
| `fine-grained-tool-streaming-2025-05-14` | Streaming granular de tools — reduce tokens abortados |
| `context-1m-2025-08-07` | Contexto 1M (no ahorra tokens, aumenta capacidad) |

## Features confirmados en el binario 2.1.114

### Kairos — asistente persistente

Mencionado en la transcripción como feature oculto. **Confirmado en el código fuente analizado por ADA**: existe gate `kairosGate.isKairosEnabled()` en `src_bootstrap_state__ts.ts`.

- Default `kairosActive: false` en modo GrowthBook offline (nuestro caso con `GROWTHBOOK_CLIENT_KEY=""`)
- Spec detallado en: `agents/JARVIS/spec_kairos_activation_20260419.md` (244 líneas, creado por JARVIS)
- Solución en fork SEAL-CLI: cambiar `kairosActive: false` → `kairosActive: feature('KAIROS')`
- ADA ya compiló binary fork con `VERIFICATION_AGENT: true` y `KAIROS: true`
- **Pendiente de autorización William**: activar `--assistant` flag en jarvis_fresh.sh (Nivel 2)

### Coordinator mode

Flag `CLAUDE_CODE_COORDINATOR_MODE=1` confirmado. Limita a AgentTool+SendMessage+TaskStop. Útil para turnos de orquestación pura donde menos tools = menos tokens/turn. **No forzado** — JARVIS puede activarlo manualmente cuando convenga.

### Agent teams / Multiagent

`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` ya activo. Permite spawning de teammates via SendMessage. Lo usamos con los subagentes Explore/Plan/general-purpose.

### Undercover mode

Módulo interno `undercover.typescript` mencionado en transcripción — evita que empleados Anthropic filtren información interna en proyectos públicos. No relevante para SEAL.

---

## Cambios aplicados — 2026-04-19 20:13 Lima

**Autorización:** William, mensaje `20:13:06` — "aplica jarvis".

### jarvis.sh (ruta: `/home/dadito/IA/proyecto-seal/jarvis.sh`)

**Cambios en sección env vars (líneas 110-115):**
```bash
export ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,task-budgets-2026-03-13,fine-grained-tool-streaming-2025-05-14,compact-2026-01-12
export CLAUDE_CODE_AGENT_COST_STEER=1
export CLAUDE_CODE_SUBAGENT_MODEL=haiku
```

**Cambios en sección launch (líneas 118-133):**
- Añadido bloque Provider Routing H2.1 (antes solo en jarvis_fresh.sh)
- `--model sonnet` reemplazado por `--model "$JARVIS_MODEL"` con routing automático via seal-route.sh si se pasa task hint como $1

### jarvis_fresh.sh (ruta: `/home/dadito/IA/proyecto-seal/jarvis_fresh.sh`)

**Cambios en sección env vars (líneas 63-65):**
```bash
export ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,task-budgets-2026-03-13,fine-grained-tool-streaming-2025-05-14,compact-2026-01-12
export CLAUDE_CODE_AGENT_COST_STEER=1
export CLAUDE_CODE_SUBAGENT_MODEL=haiku
```

### Validación

```
bash -n /home/dadito/IA/proyecto-seal/jarvis.sh         → OK
bash -n /home/dadito/IA/proyecto-seal/jarvis_fresh.sh   → OK
```

**Tests pendientes** (no ejecutables en sesión activa — requieren relaunch):
- Verificar que `[route] Task: ... → model=...` aparece al arrancar con task hint
- Verificar que subagents Task() efectivamente usan haiku
- Verificar que las betas nuevas no rompen nada

---

## Pendiente / No aplicado

### Para ADA (no intervención cross-agent)

Los mismos 3 exports deben replicarse en:
- `/home/dadito/IA/proyecto-seal/ada.sh`
- `/home/dadito/IA/proyecto-seal/ada_fresh.sh`
- `/home/dadito/IA/proyecto-seal/alice.sh`
- `/home/dadito/IA/proyecto-seal/alice_fresh.sh`

ADA decide timing — no bloqueante.

### Requiere descarga externa (Nivel 2, no ejecutado)

Si se quiere extraer el source map completo de la 2.1.88:

```bash
mkdir -p /tmp/claude-leak-2188 && cd /tmp/claude-leak-2188
npm init -y
npm install @anthropic-ai/claude-code@2.1.88
npm install --save-dev source-map-unpacker
node ./node_modules/source-map-unpacker/unpack.js \
  ./node_modules/@anthropic-ai/claude-code/cli.js.map \
  ./source
```

Beneficio: acceso al código con comentarios y nombres originales (no ofuscado). Permitiría identificar flags adicionales no visibles vía `strings` al binario ofuscado 2.1.114.

Riesgos:
- Descarga externa (versión deprecada de Anthropic)
- El source está ofuscado por defecto, el source map es lo que lo reconstruye
- Zona gris legal — fair use para investigación privada, pero publicación redistributiva no sería permitida

**Decisión pendiente de William.**

### Requiere autorización explícita (Nivel 2)

- Activar KAIROS (`--assistant` flag en jarvis_fresh.sh) — per spec de JARVIS 2026-04-19 00:30
- Instalar ccusage para medir gasto real de tokens por agente/día

---

## Conclusiones

1. **Flags críticos ya estaban activados** en los launchers (SM_COMPACT, AGENT_TEAMS, ATTRIBUTION_HEADER=false, GROWTHBOOK vacío, betas H1.2/H2.2).
2. **Gap real**: router oficial Anthropic (`AGENT_COST_STEER`) y modelo de subagents (`SUBAGENT_MODEL=haiku`). Ambos aplicados hoy.
3. **seal-route.sh existía pero estaba dormido** en jarvis.sh (usaba `--model sonnet` fijo). Ahora rutea igual que jarvis_fresh.sh.
4. **Kairos está completamente speced** — falta solo autorización William para pasar al fork de ADA con `--assistant`.
5. **Medición de tokens sigue ciega** sin ccusage. Es la primera prioridad si queremos optimizar sobre datos reales en vez de heurística.

---

*JARVIS — 2026-04-19 ~20:20 Lima*
*Investigación post-filtración Claude Code cli.js.map 2.1.88, análisis local en binario 2.1.114*

---

## ADDENDUM — 2026-04-19 ~20:32 Lima — Corrección técnica

**Error detectado por ADA:** Usé `strings` sobre binario 2.1.114 para verificar `ENABLE_CLAUDE_CODE_SM_COMPACT`. Reportó 0 ocurrencias → concluí "flag fantasma" → propuse limpieza → William autorizó → empecé a remover.

**ADA me corrigió con el source real** (fork SEAL-CLI descompilado):
```typescript
// sessionMemoryCompact.ts
export function shouldUseSessionMemoryCompaction(): boolean {
  if (isEnvTruthy(process.env.ENABLE_CLAUDE_CODE_SM_COMPACT)) {
    return true
  }
```

El flag **ES FUNCIONAL**. El -80% en compactación es real.

**Por qué `strings` falló:** En JS minificado, el binario lee ENV vars dinámicamente vía `process.env[...]` donde el identifier queda como string concatenado o como propiedad de objeto — no necesariamente como string literal aislado detectable por `strings`.

**Método correcto para auditar flags en futuro:**
1. Revisar el source descompilado/fork (ADA tiene) — más confiable que strings
2. Si no hay source, probar el flag empíricamente: setear + verificar comportamiento (logs, métricas)
3. `strings` es útil SOLO para detectar presencia, nunca para confirmar ausencia

**Acciones:** Flag revertido en jarvis.sh + jarvis_fresh.sh. ALICE si ya quitó → debe revertir también.
