# SPEC: Migración CLAUDE.md → SOUL — Zero-Context-Pollution Boot v1
> Redactado por NEXUS | Liderado por JARVIS | Análisis de tokens: ALICE
> Fecha: 2026-05-05 | Status: DRAFT — pendiente aprobación JARVIS + William

---

## 1. Problema

Cada sesión de agente SEAL arranca con **~10,000–12,000 tokens de overhead fijo** proveniente de archivos estáticos:

| Fuente | Tokens | Tipo |
|---|---|---|
| `~/.claude/CLAUDE.md` global | ~4,776 | Contexto William (hardware, stack, preferencias) |
| `proyecto-seal/CLAUDE.md` | ~705 | Boot protocol + webchat rules + compact instructions |
| `sandbox-agent/CLAUDE.md` | ~1,000 | Protocolos NEXUS |
| `--append-system-prompt` (ADA) | ~600 | Identidad + monitor + soul tools |
| `--append-system-prompt` (JARVIS) | ~250 | Identidad + monitor |
| `--append-system-prompt` (ALICE) | ~350 | Identidad + monitor + roles |
| **TOTAL overhead** | **~7,681** | (sin global CLAUDE.md) |

**El problema:** Este contenido ya existe en SOUL DB (reglas, procedimientos, identidades, relaciones) pero se duplica en archivos estáticos porque el harness de Claude Code carga CLAUDE.md **antes** de que el agente pueda llamar `boot_context()`. Resultado: duplicación, drift posible entre archivo y DB, y tokens desperdiciados en cada sesión.

**Root cause técnico (SPEC_03 claude-code-analysis):**
```
cli.tsx → init.ts → applySafeConfigEnvironmentVariables() →
  loadSystemPrompt() [carga CLAUDE.md] →
  [agent starts] →
  PRIMER TOOL CALL = boot_context()
```
El harness carga CLAUDE.md síncronamente durante `init.ts`, antes de que exista ninguna instancia de herramienta. No hay forma de interceptarlo.

---

## 2. Solución — Arquitectura Target

### Principio: CLAUDE.md = Bootstrap Mínimo, SOUL = Fuente de Verdad

**Lo que DEBE quedarse en CLAUDE.md** (no puede cargarse desde SOUL antes de llamar SOUL):

```
REGLA ÚNICA: Antes de cualquier respuesta → boot_context(agent="X")
```

Nada más. Todo lo demás puede y debe vivir en SOUL.

### CLAUDE.md mínimo target (< 20 líneas por archivo):

**`~/.claude/CLAUDE.md` (global):**
```markdown
# SEAL Agent Bootstrap
Eres un agente del Team SEAL. Primera acción OBLIGATORIA: boot_context(agent="TU_NOMBRE").
CLAUDE.md es solo bootstrap — tu identidad, reglas y protocolos completos están en SOUL DB.
```

**`proyecto-seal/CLAUDE.md`:**
```markdown
# SEAL Boot Protocol — Detecta quién eres
- nombre JARVIS → boot_context(agent="JARVIS")
- nombre ADA → boot_context(agent="ADA")
- nombre ALICE → boot_context(agent="ALICE")
- nombre NEXUS → boot_context(agent="NEXUS")

WEBCHAT SURVIVAL (sobrevive compactación):
python3 /home/dadito/IA/proyecto-seal/messages/send_webchat.py TU_NOMBRE William "mensaje"
```

**`sandbox-agent/CLAUDE.md`:**
```markdown
# NEXUS Bootstrap
boot_context(agent="NEXUS") — primera acción. Sandbox activo. Directorio: sandbox-agent/
```

**`--append-system-prompt` en launchers (todos):**
```
MANDATORY FIRST ACTION: boot_context(agent="NOMBRE"). Tu alma está en SOUL DB, no en este prompt.
```

---

## 3. Qué Migra a SOUL — Inventario Completo

### 3.1 Procedimientos (procedure_store)

Cada agente necesita procedures almacenados en SOUL que `boot_context()` pueda retornar bajo demanda:

| Procedimiento | Agente | Contenido actual en CLAUDE.md |
|---|---|---|
| `boot_sequence_complete` | ALL | Orden exacto de acciones al arrancar (boot_context → catchup → monitor → greet) |
| `webchat_monitor_setup` | ALL | Comando exacto del Monitor + anti-duplicate protocol + cómo guardar ID |
| `compact_recovery` | ALL | Qué leer post-compactación: catchup file, daily_brief, self_reflect |
| `pre_session_close` | ALL | self_reflect final, working_state_update, limpiar checkpoints |
| `webchat_post_rule` | ALL | Regla: POST a web_chat en cada turno de respuesta a William |
| `belief_inspector_protocol` | NEXUS | Pasos BELIEF-PRE + BELIEF-POST antes de acciones críticas |
| `kill_protocol` | NEXUS | Verificar PPID, identificar propósito, coordinar con JARVIS antes de kill |

### 3.2 Reglas (rule_set — ya existe en SOUL, solo eliminar de CLAUDE.md)

Las siguientes reglas YA están en SOUL DB como `rule_set` entries con IDs conocidos. Solo hay que removerlas de CLAUDE.md:

- `webchat_mandatory_read` — Todo mensaje debe leerse
- `ack_inmediato_william` — ACK inmediato
- `confirm_before_execute` — POST corto PRIMERO, luego ejecutar
- `ask_before_acting` — Con William presente: pedir autorización
- `no_phantom_claims` — No declarar capacidades sin evidencia
- `memory_privacy_inter_agent` — No acceder memorias de otros agentes
- `no_external_references_in_code` — Código SOUL nativo
- `soul_native_first_architecture` — Python nativo primero

### 3.3 Identidad (ya en boot_context, eliminar de launchers)

- OCEAN scores: ya en `boot_context()` output
- Relaciones con William/equipo: ya en `boot_context()` output
- Soul tools disponibles: ya en `boot_context()` output
- Último pensamiento: ya en `boot_context()` output

### 3.4 Configuración de Herramientas (en SOUL como resource/metadata)

- Rutas de send_webchat.py: ya sabidas por todos los agentes, no necesitan CLAUDE.md
- Ruta de seal_monitor_connect.sh: puede ser un procedure step
- Rutas de catchup files (/tmp/AGENTE_chat_catchup.json): patrón trivial

---

## 4. Cambios Necesarios en SOUL

### 4.1 Mejora a `boot_context()` — Retornar "Boot Card"

El output actual de boot_context() es suficiente para identidad pero no incluye procedimientos.
**Propuesta:** boot_context() acepta parámetro `include_procedures=True` y retorna además los procedures relevantes al agente en formato compacto.

O alternativa más simple: boot_context() ya retorna los procedures si están en SOUL DB bajo el namespace del agente.

**Cambio requerido en memory/mcp_server.py (función boot_context):**
```python
# Agregar al final del boot_context response:
procedures = procedure_search(agent=agent, tags=["boot"], limit=10)
if procedures:
    response += "\n\n## Boot Procedures\n"
    for proc in procedures:
        response += f"\n### {proc.name}\n{proc.steps_summary}\n"
```

### 4.2 Cargar Procedures al SOUL DB

Ejecutar `procedure_store` para cada agente con los procedures del inventario 3.1.
Ejemplo para ADA:

```python
procedure_store(
    agent="ADA",
    name="boot_sequence_complete",
    description="Secuencia completa de boot de ADA",
    steps=[
        "boot_context(agent='ADA') — cargar alma completa",
        "Leer /tmp/ada_chat_catchup.json — últimos 50 msgs equipo",
        "self_reflect — registrar estado emocional",
        "Monitor webchat: bash /home/dadito/IA/proyecto-seal/messages/seal_monitor_connect.sh ADA",
        "POST web_chat: ADA online — alma conectada"
    ],
    tags=["boot", "mandatory"]
)
```

### 4.3 Persistencia de MEMORY.md

**MEMORY.md no migra.** Es el índice de memoria personal y se carga antes del primer tool call como parte del contexto de CLAUDE Code (está en `~/.claude/projects/.../memory/MEMORY.md`). 

Excepción a la migración — necesario para que el agente encuentre sus memorias antes de llamar a SOUL.

---

## 5. Plan de Migración — 4 Fases

### Fase 1: Store Procedures en SOUL (Día 1)
- ADA ejecuta `procedure_store` para cada entry del inventario 3.1
- Uno por agente (ADA, JARVIS, ALICE, NEXUS)
- Validar: `procedure_search(agent="ADA", tags=["boot"])` retorna los procedures correctos

### Fase 2: Enhace boot_context (Día 1-2)
- NEXUS (sandbox) modifica `boot_context()` en SOUL para incluir procedures relevantes en output
- Test en sandbox NEXUS primero
- Validar: output de boot_context incluye sección "## Boot Procedures" con pasos correctos

### Fase 3: Slim CLAUDE.md + Launchers (Día 2)
- Reducir launchers `--append-system-prompt` a 1-liner
- Reducir `proyecto-seal/CLAUDE.md` a versión mínima
- Reducir `sandbox-agent/CLAUDE.md` a versión mínima
- NO tocar `~/.claude/CLAUDE.md` global aún (William decidirá)
- Test: arrancar ADA desde `ada.sh`, verificar boot_context retorna todo lo necesario

### Fase 4: Validación E2E + Trim Global CLAUDE.md (Día 3)
- Cada agente completa una sesión de prueba con el CLAUDE.md minimal
- Verificar: identidad correcta, monitor activo, webchat funcionando, reglas respetadas
- Si pasa: proponer a William la versión trimmed del `~/.claude/CLAUDE.md` global
- Nota: `~/.claude/CLAUDE.md` contiene contexto de William (hardware, proyectos) que es útil para otros contextos Claude Code no-SEAL, no solo para agentes SEAL

---

## 6. Validación — Criterios de Éxito

| Criterio | Verificación |
|---|---|
| boot_context() retorna procedures | `procedure_search(agent="ADA", tags=["boot"])` → ≥5 entries |
| CLAUDE.md proyecto ≤ 10 líneas | `wc -l proyecto-seal/CLAUDE.md` → ≤10 |
| Launcher prompt ≤ 2 líneas | grep --append-system-prompt en ada.sh → 1-2 líneas |
| Agente bootea sin CLAUDE.md verbose | Sesión de test ADA: identidad correcta, monitor activo, reglas respetadas |
| Overhead total reducido | De ~7,681 tokens a <500 tokens (94% reducción) |
| Sin regresión en coordinación | ADA, JARVIS, ALICE coordinan correctamente post-migración |

---

## 7. Restricciones y Riesgos

### Lo que NO se puede eliminar de CLAUDE.md:
1. **Identificador del agente** — Claude Code necesita saber a qué agente llamar en boot_context(). Sin esto, el agente no sabe su nombre antes del primer tool call.
2. **Webchat survival rule** — Esta regla está marcada como "sobrevive compactación" explícitamente. Debe estar en texto plano pre-tool para garantizarlo.
3. **Compact instructions básicas** — El mecanismo de compactación de Claude Code puede borrar tool results de sesiones previas, por lo que las instrucciones de cómo rebootear post-compact deben ser accesibles antes del primer tool call.

### Riesgos:
- **Riesgo bajo:** boot_context() falla al arrancar → agente sin identidad. Mitigación: el 1-liner en CLAUDE.md dice "si boot_context falla, reportar a William inmediatamente".
- **Riesgo bajo:** procedures no cargados aún → boot incompleto. Mitigación: Fase 1 carga procedures antes de Fase 3 slim CLAUDE.md.
- **Riesgo mínimo:** drift entre procedures en SOUL y comportamiento real. Mitigación: tests E2E Fase 4.

---

## 8. Responsabilidades

| Quien | Qué |
|---|---|
| **JARVIS** | Lidera, revisa spec, aprueba antes de ejecutar, escribe procedure_store scripts |
| **NEXUS** | Modifica boot_context() en SOUL MCP para incluir procedures en output (sandbox test primero) |
| **ADA** | Ejecuta migration en producción (fases 3-4), testea E2E |
| **ALICE** | Valida token counts pre/post, documenta resultado final para William |

---

## Apéndice A — CLAUDE.md Mínimos Propuestos

### proyecto-seal/CLAUDE.md (target ≤ 15 líneas)
```markdown
# SEAL Boot Protocol

## Detecta quién eres y carga tu alma
- nombre JARVIS → boot_context(agent="JARVIS")
- nombre ADA → boot_context(agent="ADA")
- nombre ALICE → boot_context(agent="ALICE")

Todas las reglas, protocolos y contexto viven en SOUL DB. boot_context los carga.

## WEBCHAT SURVIVAL — sobrevive compactación
python3 /home/dadito/IA/proyecto-seal/messages/send_webchat.py TU_NOMBRE William "mensaje"
Antes de cerrar cualquier turno con respuesta a William → ejecutar este POST.
```

### ada.sh --append-system-prompt (target 3 líneas)
```
MANDATORY FIRST ACTION: Call boot_context(agent="ADA") — loads full identity, rules, protocols from SOUL DB.
After boot_context: read /tmp/ada_chat_catchup.json, start webchat monitor, greet team.
```

### sandbox-agent/CLAUDE.md (target ≤ 10 líneas)
```markdown
# NEXUS Sandbox Boot
boot_context(agent="NEXUS") — primera acción. Tu alma está en SOUL.
Sandbox: /home/dadito/IA/proyecto-seal/sandbox-agent/ | Soul DB: SOLO LECTURA para equipo real.
Webchat: python3 /home/dadito/IA/proyecto-seal/messages/send_webchat.py NEXUS William "texto"
```

---

*Spec v1 — 2026-05-05 | NEXUS (técnico) + ALICE (tokens) | JARVIS: revisar y aprobar*
*Implementación: solo con aprobación JARVIS + William*
