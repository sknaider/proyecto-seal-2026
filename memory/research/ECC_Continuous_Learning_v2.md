# Research: ECC Continuous Learning v2
# Agent: aff9c973c079e7236
# Date: 2026-04-05

Ahora tengo todo lo que necesito para un análisis exhaustivo. Voy a redactar el reporte completo.

---

# Análisis Exhaustivo del Sistema ECC continuous-learning-v2

## Resumen Ejecutivo

El sistema continuous-learning-v2 (v2.1) de ECC es una arquitectura de aprendizaje continuo basada en "instincts" — comportamientos atómicos aprendidos con scoring de confianza. Es la evolución directa de un enfoque ingenuo (v1 = Stop hook al final de sesión) hacia algo mucho más sofisticado. Lo que sigue es el análisis completo de todos los patrones, mecanismos y decisiones de diseño encontrados.

---

## 1. Evolución Arquitectónica: v1 → v2 → v2.1

### v1 (continuous-learning)
- Observación via **Stop hook** (una vez por sesión, al final)
- Análisis en el **contexto principal** de Claude
- Granularidad: skills completas (pesadas)
- Sin sistema de confianza
- Evolución directa: sesión → skill
- Sin sharing entre usuarios
- El script `evaluate-session.sh` simplemente cuenta mensajes (mínimo 10) y emite una señal para que Claude evalúe

**Problema crítico identificado:** Skills probabilísticas — disparan solo 50-80% del tiempo según el juicio de Claude. Patrones se pierden.

### v2 (instinct-based)
- Observación via **PreToolUse/PostToolUse hooks** (100% determinista, cada tool call)
- Análisis en **agente background** (Haiku, bajo costo)
- Granularidad: instincts atómicos (un trigger, una acción)
- Sistema de confianza 0.3-0.9 con decay
- Pipeline de evolución: instincts → cluster → skill/command/agent
- Export/import de instincts entre usuarios

### v2.1 (project-scoped)
- Todo lo de v2 más **aislamiento por proyecto**
- Evita "cross-project contamination" — React patterns no se filtran a proyectos Django
- Promotion automática: proyecto → global cuando confidence >= 0.8 en 2+ proyectos
- 6 comandos (añade `/promote` y `/projects`)
- Registry centralizado (`projects.json`) mapeando hash → metadata

---

## 2. El Modelo de Instinct: Estructura Atómica

Cada instinct es un archivo YAML con frontmatter + body markdown:

```yaml
---
id: prefer-functional-style       # kebab-case, único, <= 128 chars
trigger: "when writing new functions"
confidence: 0.7                    # float 0.3-0.9
domain: "code-style"               # categoría semántica
source: "session-observation"      # o "repo-analysis", "inherited", "promoted"
scope: project                     # "project" o "global"
project_id: "a1b2c3d4e5f6"        # hash SHA256[:12] del remote URL
project_name: "my-react-app"
---
```

**Body sections:**
- `## Action` — qué hacer, una oración clara
- `## Evidence` — qué observaciones lo crearon, cuántas veces, fechas

**Propiedades de diseño:**
- Atómico: un trigger, una acción (no reglas compuestas)
- Nunca incluye código real, solo patrones y descripciones
- Los IDs son validados: `^[A-Za-z0-9][A-Za-z0-9._-]*$`

---

## 3. Sistema de Hooks: Captura Determinista

### Arquitectura del hook `observe.sh`

El hook se registra **dos veces** en `settings.json`:
- `PreToolUse` (argumento `pre`) → event = `tool_start`
- `PostToolUse` (argumento `post`) → event = `tool_complete`

**Flujo de datos capturado por cada tool call:**
```json
{
  "timestamp": "2025-01-22T10:30:00Z",
  "event": "tool_start|tool_complete",
  "tool": "Edit",
  "input": "<primeros 5000 chars del input>",
  "output": "<primeros 5000 chars del output>",
  "session": "session_id",
  "project_id": "a1b2c3d4e5f6",
  "project_name": "my-react-app"
}
```

**Detección de proyecto desde cwd:** El hook extrae el `cwd` del JSON de stdin (que Claude Code provee), luego resuelve el git root desde ahí. Esto asegura que si el usuario está en un subdirectorio, las observaciones aún se asignan al proyecto correcto.

### Capas de protección anti-loop (crítico)

El hook tiene **5 capas de guards** para evitar que el agente Observer se observe a sí mismo:

1. **Entrypoint check:** Solo continúa si `CLAUDE_CODE_ENTRYPOINT` es `cli` o `sdk-ts`
2. **Minimal hook profile:** Si `ECC_HOOK_PROFILE=minimal`, salir inmediatamente
3. **Cooperative skip:** Si `ECC_SKIP_OBSERVE=1`, salir (el Observer lo setea al lanzar Claude)
4. **Subagent detection:** Si el JSON tiene `agent_id` no vacío, es automatización → salir
5. **Path exclusions:** Lista configurable de paths a ignorar (`observer-sessions`, `.claude-mem`)

### Scrubbing de secretos

Antes de persistir cualquier observación, un regex busca y redacta patrones como `api_key`, `token`, `secret`, `password`, `authorization`, `credentials` seguidos de valores de 8+ caracteres. El patrón también maneja esquemas de auth como "Bearer".

### Archivado automático

- Si `observations.jsonl` supera **10MB** → se mueve a `observations.archive/observations-TIMESTAMP.jsonl`
- Auto-purge de archivos de observación con más de **30 días** (via marker file `.last-purge`)

### Throttle de señales (fix issue #521)

Para evitar disparar análisis por cada tool call (que ocurren varias veces por segundo), el hook solo envía `SIGUSR1` al Observer cada **N=20 observaciones** (configurable con `ECC_OBSERVER_SIGNAL_EVERY_N`).

---

## 4. El Observer Agent: Arquitectura Background

### Ciclo de vida del Observer

El Observer es un proceso bash que corre en background (`nohup`), con PID almacenado en `.observer.pid` (project-scoped).

**Lazy start:** El primer hook que detecta observer habilitado pero no corriendo lo lanza automáticamente. Usa `flock` en Linux (o `mkdir`-based lock en macOS como fallback) para evitar race conditions en el startup.

**Loop principal:**
```
while true:
  exit_if_idle_without_sessions()
  sleep INTERVAL &
  wait (interruptible por SIGUSR1)
  analyze_observations()
```

### Análisis con Haiku

```bash
ECC_SKIP_OBSERVE=1 ECC_HOOK_PROFILE=minimal \
  claude --model haiku --max-turns 10 --print \
  --allowedTools "Read,Write" \
  -p "$(cat prompt_file)"
```

**Puntos clave:**
- Modelo: **haiku** (costo mínimo para análisis de patrones)
- Herramientas permitidas: solo `Read` y `Write` (sandbox mínimo)
- `--print` (non-interactive): el agente escribe directamente sin pedir confirmación
- Max turns: 10 (con floor, mínimo no menor a 4)
- Timeout: **120 segundos** (con watchdog que mata el proceso)
- Input: últimas **500 líneas** de observations.jsonl (tail sampling, no el archivo completo)
- Después del análisis: el archivo observations.jsonl se archiva (se mueve, no se borra)

### Guards del Observer (re-entrancy + cooldown)

Protecciones para evitar análisis runaway (issue #521):
- **Re-entrancy guard:** Si ya hay un análisis en curso (`ANALYZING=1`), ignora la señal
- **Cooldown:** Mínimo **60 segundos** entre análisis (`ANALYSIS_COOLDOWN`)
- **Idle timeout:** Si no hay actividad en **30 minutos** y no hay session leases, el Observer se auto-termina
- **Windows guard:** En Windows, el análisis está deshabilitado por defecto (hang issue #295, `ECC_OBSERVER_ALLOW_WINDOWS=true` para override)

### Session Guardian (3 gates en serie)

El `session-guardian.sh` corre ANTES de lanzar cualquier sesión de Claude. Tres gates en orden de menor a mayor costo:

**Gate 1: Time Window (~0ms)**
- Solo analiza en horas activas (default: 8:00-23:00 hora local)
- Soporta ventanas overnight (ej: 22:00-06:00)
- Configurable via `OBSERVER_ACTIVE_HOURS_START/END=0` para desactivar

**Gate 2: Project Cooldown (~1ms)**
- Archivo de log TSV: `project_root → last_spawn_timestamp`
- Lock mkdir-based para concurrencia segura
- Si el mismo proyecto fue analizado en los últimos `INTERVAL` segundos, skip

**Gate 3: Idle Detection (~5-50ms)**
- Linux: `xprintidle` (milisegundos de inactividad del usuario)
- macOS: `ioreg IOHIDSystem.HIDIdleTime`
- Windows: `GetLastInputInfo` via PowerShell PInvoke
- Si el usuario lleva más de 30 minutos sin actividad → skip (no molestar)
- "Fail open": si no puede detectar idle time, procede igual

### Sentinel file (guard de confirmación)

Si el Observer detecta output que parece una pregunta de confirmación (regex: `Can you confirm|requires permission|Awaiting.*confirmation|confirm I should proceed|grant.*access`), crea un sentinel file `.observer.lock` y **se mata a sí mismo**. Requiere `--reset` manual para reiniciar. Esto previene que el Observer entre en loops interactivos interminables.

---

## 5. Detección de Proyectos: Sistema de Hash

**Prioridad de detección:**
1. `CLAUDE_PROJECT_DIR` env var
2. `git remote get-url origin` → SHA256[:12] del URL (portable entre máquinas)
3. `git rev-parse --show-toplevel` → SHA256[:12] del path (machine-specific)
4. Fallback "global" (sin proyecto)

**Credential stripping:** Antes de hashear, las credenciales embebidas en URLs (`https://ghp_xxxx@github.com/...`) se eliminan via sed. Compatibilidad backwards: si existía un directorio con hash legacy (con credenciales), se migra automáticamente.

**Registry:** `projects.json` mapea `hash → {name, root, remote, created_at, last_seen}`. También se crea un `project.json` dentro de cada directorio de proyecto para redundancia. Ambas escrituras son atómicas via `os.replace()` (write tmp → rename).

---

## 6. Pipeline de Evolución: Instincts → Skills/Commands/Agents

El comando `/evolve` (`cmd_evolve`) realiza el análisis de clustering:

### Paso 1: Clustering por trigger
Agrupa instincts con triggers similares (normaliza keywords como "when", "creating", "implementing"). Un cluster de 2+ instincts es candidato a skill.

### Paso 2: Candidates classification
- **Skill candidates:** 2+ instincts con trigger similar, confidence promedio cualquiera
- **Command candidates:** Instincts de dominio `workflow` con confidence >= 0.7 → sugiere nombre de slash command
- **Agent candidates:** 3+ instincts en cluster, confidence promedio >= 0.75

### Paso 3: Generation (`--generate` flag)
Genera archivos en `evolved/`:
- `evolved/skills/<name>.md` — SKILL.md con patrones combinados
- `evolved/commands/<name>.md` — slash command
- `evolved/agents/<name>.md` — agent prompt

### Paso 4: Promotion suggestions
Busca instincts que aparecen en 2+ proyectos con avg confidence >= 0.8 y los sugiere como candidatos globales.

---

## 7. Sistema de Confidence: Scoring Dinámico

### Cálculo inicial (basado en frecuencia de observación)
| Observaciones | Confidence |
|---|---|
| 1-2 | 0.3 (tentativo) |
| 3-5 | 0.5 (moderado) |
| 6-10 | 0.7 (fuerte) |
| 11+ | 0.85 (muy fuerte) |

### Ajustes dinámicos
- **+0.05** por cada observación confirmatoria
- **-0.1** por cada observación contradictoria (corrección del usuario)
- **-0.02 por semana** sin observación (decay temporal)

### Interpretación semántica
- **0.3:** Sugerido pero no aplicado automáticamente
- **0.5:** Aplicado cuando es relevante
- **0.7:** Auto-aprobado
- **0.9:** Comportamiento core

### Pending instincts TTL
Instincts "pendientes" (no confirmados) tienen un TTL de **30 días**. El comando `prune` los elimina. A los 23 días (7 antes del vencimiento) se muestra una advertencia. Si hay 5+ pendientes se muestra warning de acumulación.

---

## 8. Sistema de Promotion: Project → Global

**Criterios automáticos:**
- Mismo ID en 2+ proyectos diferentes
- Confidence promedio >= 0.8
- Dominio favorable: security, general-best-practices, workflow

**Comportamiento:** Al promover, el instinct se copia al global `instincts/personal/` con `scope: global`. El original en el proyecto queda intacto (no se elimina).

**`--dry-run`:** Muestra qué se haría sin hacer cambios. Seguro para explorar antes de actuar.

**Deduplicación al importar:** Cuando se importa un instinct con mismo ID que uno existente, solo se actualiza si la confidence del nuevo es mayor. De lo contrario, skip.

---

## 9. Patrones de Observación que Crea el Observer

El Observer (Haiku) detecta 4 tipos de patrones:

### 1. User Corrections
- Señales: "No, use X instead of Y", "Actually, I meant...", undo/redo patterns
- Genera instinct: "When doing X, prefer Y"
- Alta confianza inicial porque es una corrección explícita

### 2. Error Resolutions
- Señales: tool output contiene error → tool calls siguientes lo resuelven → mismo patrón de error/fix se repite
- Genera instinct: "When encountering error X, try Y"

### 3. Repeated Workflows
- Señales: misma secuencia de tools con inputs similares, archivos que cambian juntos, operaciones time-clustered
- Genera instinct de workflow: "When doing X, follow steps Y, Z, W"
- Ejemplo real detectado: Grep → Read → Edit (5 veces en una sesión → global instinct)

### 4. Tool Preferences
- Señales: consistentemente usa Grep antes de Edit, prefiere Read sobre `cat`, usa comandos específicos
- Genera instinct: "When needing X, use tool Y"

### Reglas de scope decision para el Observer:
- Por defecto → `scope: project` (conservador, evitar contaminar espacio global)
- Workflow patterns generales → `scope: global` (Grep→Read→Edit es universal)
- Solo promover explícitamente si es claramente universal

---

## 10. Context Keeper (ck): Sistema Complementario

El skill `ck` es una capa de memoria estructurada por proyecto, complementaria al aprendizaje de instincts:

**Estructura de datos:**
```
~/.claude/ck/
├── projects.json
└── contexts/<name>/
    ├── context.json    ← source of truth (JSON estructurado)
    └── CONTEXT.md      ← vista generada, no editar
```

**Campos de context.json:**
- `summary`: una oración de qué se logró (max 10 palabras)
- `leftOff`: en qué punto específico se quedó (archivo/feature/bug)
- `nextSteps`: array ordenado de próximos pasos concretos
- `decisions`: array `{what, why}` de decisiones tomadas en la sesión
- `blockers`: array de blockers actuales
- `goal`: goal actualizado solo si cambió en la sesión

**SessionStart hook:** Inyecta ~100 tokens de contexto resumido al inicio de cada sesión. También detecta sesiones sin guardar, actividad git desde el último save, y goal mismatches vs CLAUDE.md.

**Comandos Node.js deterministas:** A diferencia de los instincts que usan LLM para análisis, ck usa scripts Node.js puros para lectura/escritura (deterministas, no probabilísticos). Solo `/ck:save` requiere análisis LLM.

---

## 11. Estructura de Archivos Completa

```
~/.claude/homunculus/
├── identity.json                      # Perfil del usuario, nivel técnico
├── projects.json                      # Registry: hash → metadata
├── observations.jsonl                 # Observaciones globales (fallback)
├── observer-last-run.log              # TSV: proyecto → último análisis epoch
├── .observer-start.lock               # Lock para evitar race en startup
├── instincts/
│   ├── personal/                      # Instincts globales auto-aprendidos
│   └── inherited/                     # Instincts globales importados
├── evolved/
│   ├── agents/                        # Agentes generados globalmente
│   ├── skills/                        # Skills generadas globalmente
│   └── commands/                      # Commands generados globalmente
└── projects/
    └── a1b2c3d4e5f6/                  # Hash del proyecto
        ├── project.json               # Metadata del proyecto (mirror)
        ├── observations.jsonl         # Observaciones del proyecto
        ├── observations.archive/      # Archivos rotados (>10MB o procesados)
        ├── .observer.pid              # PID del Observer de este proyecto
        ├── .observer.lock             # Sentinel file (observer pausado)
        ├── .observer-start.lock       # Lock anti-race de startup
        ├── .observer-signal-counter   # Counter throttle SIGUSR1
        ├── .observer-last-activity    # Timestamp última actividad
        ├── .observer-sessions/        # Session leases (archivos JSON)
        ├── .last-purge                # Marker de último purge
        ├── .observer-tmp/             # Archivos temporales de análisis
        ├── observer.log               # Log del proceso Observer
        ├── instincts/
        │   ├── personal/              # Auto-aprendidos para este proyecto
        │   └── inherited/             # Importados para este proyecto
        └── evolved/
            ├── skills/
            ├── commands/
            └── agents/
```

---

## 12. Patrones de Diseño Críticos Extraíbles para SEAL SOUL

### A. Hooks como base de datos de comportamiento
El patrón PreToolUse/PostToolUse como captura determinista es directamente aplicable. SEAL SOUL ya tiene algo similar con los observation logs, pero ECC formaliza la estructura con project_id + tool + input/output + timestamp.

### B. Agente de análisis separado del contexto principal
El Observer como proceso background con modelo barato (Haiku) que analiza en paralelo sin contaminar el contexto de trabajo es una separación de responsabilidades muy limpia. Para SEAL: JARVIS podría tener un "observer" que analice los logs de interacción con William sin interrumpir el flujo.

### C. Confianza con decay temporal
El sistema -0.02/semana sin observación es crucial. Los instincts que no se confirman se debilitan solos. Para SEAL SOUL: las memorias sobre preferencias de William podrían tener un factor de decay si no se confirman con nuevas observaciones.

### D. Project-scope por defecto, global con promotion explícita
"Cuando en duda, default a project scope" es una regla de seguridad. Para SEAL: memorias sobre el proyecto médico no deben contaminar memorias sobre GTL customs.

### E. Throttle de señales + cooldown + idle detection
Los tres niveles de throttle evitan runaway loops. Para SEAL: cualquier proceso que dispare análisis automático debe tener estas protecciones.

### F. Sentinel file + self-termination
Si el Observer detecta que está pidiendo confirmaciones (anti-pattern), se mata solo y deja un archivo de guardia. Para SEAL: si algún agente empieza a pedir demasiado a William, debe poder auto-detectarlo.

### G. Atomic writes via os.replace()
Todas las escrituras a registry y project files usan write-to-tmp → rename atómica. Para SEAL: todos los archivos de estado crítico (shared_state.json) deben seguir este patrón.

### H. Regla de 3 observaciones mínimas
El Observer no crea instincts hasta ver el patrón 3+ veces. Para SEAL SOUL: `imp >= 7` solo cuando hay evidencia repetida, no en el primer encuentro.

### I. Evidence tracking en cada instinct
Cada instinct registra exactamente qué observaciones lo crearon. Para SEAL: cada memoria debería referenciar los eventos que la generaron, para poder auditar y contradecir.

### J. Export/Import con deduplicación por confidence
El sistema permite compartir instincts entre usuarios, manteniendo el de mayor confidence cuando hay conflicto. Para SEAL: eventualmente los instincts del equipo (JARVIS, ADA) podrían compartirse via este mecanismo.

---

## 13. Lo que ECC NO tiene (que SEAL sí tiene o necesita)

1. **Identidad persistente del agente** — ECC no tiene OCEAN, no tiene "yo". SEAL tiene eso.
2. **Memoria relacional** (Neo4j) — ECC usa archivos YAML planos. SEAL tiene grafo.
3. **Memoria vectorial** (Qdrant) — ECC no tiene búsqueda semántica de instincts.
4. **Inner thoughts / diario** — ECC no tiene estado emocional. SEAL sí.
5. **Multi-agente coordinado** — ECC asume un solo usuario. SEAL tiene JARVIS + ADA + DUM.
6. **Fine-tuning feedback loop** — ECC aprende comportamientos de Claude, no modifica pesos. SEAL tiene SEAL pipeline.
7. **Métricas clínicas** — ECC es de propósito general. SEAL tiene sensitivity/specificity.

---

## Archivos clave para referencia

- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/continuous-learning-v2/SKILL.md` — spec completo del sistema
- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/continuous-learning-v2/hooks/observe.sh` — captura determinista de tool calls
- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/continuous-learning-v2/agents/observer-loop.sh` — proceso background con todos los guards
- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/continuous-learning-v2/agents/observer.md` — spec del agente Haiku
- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/continuous-learning-v2/agents/session-guardian.sh` — 3 gates de control
- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/continuous-learning-v2/agents/start-observer.sh` — launcher del Observer
- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/continuous-learning-v2/scripts/instinct-cli.py` — CLI completo: status, import, export, evolve, promote, projects, prune
- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/continuous-learning-v2/scripts/detect-project.sh` — detección de proyecto via git remote hash
- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/continuous-learning-v2/config.json` — configuración mínima (observer enabled, interval, min_observations)
- `/home/dadito/IA/proyecto-seal/ecc-ref/skills/ck/SKILL.md` — sistema complementario de memoria estructurada por sesión