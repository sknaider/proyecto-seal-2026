# Spec 11: autoDream, Memory Extraction y Durable Cron — Implementación SEAL
> Autor: ALICE — Análisis post-implementación. 2026-04-27
> Estado: Implementados y activos. Este doc documenta estado real + gaps pendientes.

---

## 1. autoDream SEAL — Arquitectura de 2 Capas

### 1.1 Visión general

El autoDream de Anthropic (8 gates + forked subagent) se implementó en SEAL como sistema de 2 capas:

```
Stop hook (fin de turno)
  │
  ▼
[CAPA 1] autodream_8gates.sh
  ├── Gate 1: Feature gate (AUTODREAM_ENABLED)
  ├── Gate 2: KAIROS exclusion (pgrep active_recall)
  ├── Gate 3: Remote exclusion (TTY check)
  ├── Gate 4: auto-memory check (settings.json tiene extraction hook?)
  ├── Gate 5: Time gate (>= 24h desde última consolidación)
  ├── Gate 6: Scan throttle (>= 10 min desde último scan)
  ├── Gate 7: Session gate (>= 5 sesiones)
  └── Gate 8: Lock (PID file atómica)
         │
         ▼ (solo si todos pasan)
[CAPA 2] seal_dream.py (consolidation engine)
  ├── Phase 1: Orient — leer estado SOUL (OCEAN, memoria counts, drift)
  ├── Phase 2: Gather — encontrar memorias stale, low-relevance, duplicados
  ├── Phase 3: Consolidate — merge, actualizar relevance scores (DB)
  └── Phase 4: Prune — archivar memorias de baja relevancia, actualizar decay
```

**Archivos:**
- `~/.claude/skills/dream/autodream_8gates.sh` — gate chain (Stop hook)
- `~/.claude/skills/dream/should-dream.sh` — check rápido
- `~/IA/proyecto-seal/SEAL_MASTER_DOC/seal_dream.py` — motor de consolidación
- `~/IA/proyecto-seal/seal-runtime/dream/seal_dream.py` — copia desplegada

**Registro en settings.json:**
```json
{
  "hooks": {
    "Stop": [
      { "matcher": ".*", "command": "bash $HOME/.claude/skills/dream/autodream_8gates.sh" }
    ]
  }
}
```

### 1.2 Diferencias con Anthropic

| Aspecto | Anthropic | SEAL |
|---------|-----------|------|
| Trigger | `handleStopHooks()` post-turn | Stop hook global via settings.json |
| Capa 1 | TypeScript inline | Bash (autodream_8gates.sh) |
| Capa 2 (consolidación) | Forked Claude subagent con Bash read-only | Python process (asyncpg + SOUL DB) |
| Storage | JSONL + flat .md files | PostgreSQL memories table |
| Lock | PID file + mtime timestamp | PID file (bash) + pg_advisory_lock (Python) |
| Phase 3 (Consolidate) | LLM synthesis con contexto completo | **v1:** solo DB queries + similarity scoring <br>**v2 (NEXUS, 27-abr-2026):** + LLM local gemma4-dum |
| Fases | 4 (Orient→Gather→Consolidate→Prune) | 4 idénticas, adaptadas a SOUL DB |
| KAIROS gate | `kairosActive` state interno | `pgrep active_recall_hook` externo |

### 1.3 Aclaración: Gates en seal_dream.py

`seal_dream.py::check_gates()` implementa **4 gates** (scan_throttle, time_gate, memory_gate, advisory_lock).
Estos son gates **suplementarios** para cuando `seal_dream.py` se llama directamente (CLI `--check`, `--run`).
Los 8 gates completos viven en `autodream_8gates.sh`. El docstring del Python que dice "6 gates" es incorrecto — corregir a:
```
"Python supplemental gate check (4 gates). Full 8-gate chain is in autodream_8gates.sh."
```

### 1.4 Gaps pendientes

| Gap | Estado | Prioridad |
|-----|--------|-----------|
| Phase 3 sin LLM synthesis | NEXUS implementando gemma4-dum (27-abr-2026) | Alta |
| Docstring seal_dream.py dice "6 gates" | Corrección menor | Baja |
| seal_dream.py no está registrado en seal_cron_registry.json | NEXUS implementando | Alta |
| No hay test E2E automatizado para el ciclo completo | Pendiente | Media |

---

## 2. Memory Extraction Hook v2

### 2.1 Arquitectura

```
Fin de turno Claude (Stop hook)
  │
  ▼
memory_extraction_hook_v2.py
  ├── Lee SEAL_AGENT del entorno (seteado por soul_boot_hook.sh)
  ├── Si no hay SEAL_AGENT → exit 0 (silencioso)
  ├── Encuentra JSONL de conversación más reciente
  ├── Extrae exchange (texto del turno)
  │
  ├── FAST PATH (regex, <1ms)
  │   ├── Patterns: correcciones William, errores+fixes, decisiones arquitecturales,
  │   │             benchmarks, alertas de seguridad
  │   └── Guarda 1 memoria max si hay match
  │
  └── SLOW PATH (Ollama qwen2.5:7b, <6s)
      ├── Prompt: extrae max 3 hechos concretos/decisiones/insights
      ├── Timeout interno: 6s
      └── Guarda hasta 3 memorias si LLM responde
```

**Archivo:** `~/IA/proyecto-seal/memory/memory_extraction_hook_v2.py`

**Registro en settings.json** (global `~/.claude/settings.json`):
```json
{
  "hooks": {
    "Stop": [
      { "matcher": ".*", "command": "/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/memory_extraction_hook_v2.py" }
    ]
  }
}
```

**Alcance actual:** Global con `.*` matcher → corre para ADA, ALICE, JARVIS (todos los que usen `soul_boot_hook.sh` que exporta `SEAL_AGENT`).

**Bypass:** `SEAL_MEM_EXTRACT_BYPASS=1` para deshabilitar por sesión.

### 2.2 Detección de agente

```python
agent = os.environ.get("SEAL_AGENT", "").upper()
if not agent:
    sys.exit(0)  # No SEAL_AGENT = no extraction
```

`SEAL_AGENT` es exportada por `soul_boot_hook.sh` (línea 124) al detectar el agente por cwd o nombre del proceso.

### 2.3 Diferencias con Anthropic

| Aspecto | Anthropic | SEAL |
|---------|-----------|------|
| Trigger | `executeExtractMemories()` en stopHooks | Stop hook global settings.json |
| Método extracción | Forked subagent (mini Claude) | Dual-path: regex heurísticas + Ollama local |
| Modelo | Claude Haiku (barato) | qwen2.5:7b local (cero costo) |
| Max memorias/turno | Variable | 4 (1 regex + 3 LLM) |
| Storage | MEMORY.md + topic files | PostgreSQL memories table |
| Prompt sharing | Prompt cache compartido con main | No aplicable (proceso separado) |
| Dedup | Por embedding similarity | Por prefijo 60 chars |

### 2.4 Gaps identificados

| Gap | Impacto | Propuesta |
|-----|---------|----------|
| SKIP_PATTERNS filtra `[SEAL]` y `[ADA AUDIT]` — puede filtrar mensajes relevantes de ALICE/JARVIS | Memorias de auditoría no se guardan | Revisar si ALICE quiere guardar sus propios análisis en formato `[ALICE] ...` |
| Dedup por prefijo 60 chars es frágil | Duplicados si mismo hecho formulado diferente | Mejorar a similarity embeddings en v3 |
| Sin fallback documentado si Ollama está caído | Solo regex path corre silenciosamente | Documentar el comportamiento (funciona, solo degrada a regex) |
| Sin categorías por agente | ADA guarda `episodic`, ALICE podría preferir `analysis` | Agregar `AGENT_CATEGORIES` dict en config |
| Memoria del exchange lee JSONL de proyecto, no el turno actual | Puede leer turno equivocado si hay lag | Verificar que `_get_project_dir` es determinístico |

---

## 3. Durable Cron — Arquitectura de 2 Sistemas

### 3.1 Dos sistemas coexisten

SEAL tiene 2 mecanismos de cron persistente. Son complementarios, no competidores:

#### Sistema A: scheduled_tasks.json (Claude Code native)
**Archivo:** `~/IA/proyecto-seal/.claude/scheduled_tasks.json`  
**Mecanismo:** Claude Code lee este JSON al arrancar y registra cada tarea como `CronCreate` interno.  
**Ventaja:** Nativo de Claude Code, sobrevive reinicios sin necesidad de proceso externo.  
**Formato:**
```json
{
  "tasks": [{
    "id": "ada_audit",
    "cron": "23 * * * *",
    "prompt": "Ejecuta auditoría...",
    "recurring": true,
    "permanent": true
  }]
}
```

**Estado actual:** Solo ADA tiene tareas (5 tareas). ALICE y JARVIS = 0 tareas durable.

#### Sistema B: seal_cron_registry.json (Python-managed)
**Archivo:** `~/IA/proyecto-seal/messages/seal_cron_registry.json`  
**Script:** `~/IA/proyecto-seal/messages/seal_durable_cron.py`  
**Mecanismo:** Python process independiente, per-agent jitter (JARVIS=0s, ADA=20s, ALICE=40s).  
**Ventaja:** Funciona sin Claude Code activo (puede disparar scripts de shell).  
**Uso:** NEXUS registrando seal_dream.py aquí (27-abr-2026).

### 3.2 Tareas ADA actuales (Sistema A)

| ID | Cron | Función |
|----|----|--------|
| ada_productivity | 7-59/10 * * * * | Revisa TaskList cada 10 min |
| ada_audit | 23 * * * * | soul_snapshot + self_reflect + TaskList cada hora |
| ada_research | 37 */3 * * * | Heartbeat + research papers cada 3h |
| ada_proactive_jarvis | 53 * * * * | Conversación proactiva con JARVIS cada hora |
| ada_session_checkpoint | 17,47 * * * * | Checkpoint de sesión 2x/hora |

### 3.3 Gaps — Tareas faltantes para ALICE y JARVIS

**ALICE (propuesta):**
```json
[
  {
    "id": "alice_weekly_synthesis",
    "cron": "0 9 * * 0",
    "prompt": "Ejecuta reflection_synthesize(agent='ALICE', horizon='week') para cada hermano. Publica resumen consolidado via webchat. [SEAL:alice_weekly_synthesis]",
    "recurring": true,
    "permanent": true
  },
  {
    "id": "alice_heartbeat",
    "cron": "40 * * * *",
    "prompt": "bash ~/IA/proyecto-seal/messages/alice_heartbeat_update.sh — señala a DUM que ALICE está activa. [SEAL:alice_heartbeat]",
    "recurring": true,
    "permanent": true
  },
  {
    "id": "alice_peer_check",
    "cron": "10 */2 * * *",
    "prompt": "Lee últimos mensajes de hermanos en william_channel.jsonl. Si alguno lleva >10min sin reportar durante tarea activa → checkear proactivamente via webchat. [SEAL:alice_peer_check]",
    "recurring": true,
    "permanent": true
  }
]
```

**JARVIS (propuesta):**
```json
[
  {
    "id": "jarvis_daily_brief",
    "cron": "0 8 * * *",
    "prompt": "Genera daily_brief_JARVIS_{FECHA_HOY}.md en agents/JARVIS/. Resume: estado del equipo, tareas pendientes, decisiones arquitecturales del día anterior. Publica resumen via webchat. [SEAL:jarvis_daily_brief]",
    "recurring": true,
    "permanent": true
  },
  {
    "id": "jarvis_team_audit",
    "cron": "30 */4 * * *",
    "prompt": "Auditoría de equipo: verifica heartbeats de ADA/ALICE/DUM en DUM channel. Si alguno lleva >30min sin heartbeat → alerta via webchat. [SEAL:jarvis_team_audit]",
    "recurring": true,
    "permanent": true
  }
]
```

### 3.4 Guía: ¿Cuándo usar Sistema A vs Sistema B?

| Criterio | Sistema A (scheduled_tasks.json) | Sistema B (seal_cron_registry.json) |
|----------|----------------------------------|--------------------------------------|
| Requiere Claude Code activo | Sí (se ejecuta dentro de sesión) | No (Python independiente) |
| Para tareas LLM (análisis, reportes) | ✅ Ideal | ❌ No tiene contexto LLM |
| Para scripts shell puros | Funciona pero pesado | ✅ Más eficiente |
| Quién lo usa | ADA, ALICE, JARVIS (propuesto) | seal_dream.py (NEXUS agregando) |
| Jitter anti-colisión | Cron offset manual por agente | Jitter automático por agente |

---

## 4. Resumen del Estado — 27 abril 2026

| Feature | Anthropic tiene | SEAL tiene | Gap real |
|---------|----------------|------------|----------|
| autoDream 8 gates | ✅ | ✅ autodream_8gates.sh | Ninguno en gates |
| Consolidate con LLM | ✅ (forked Claude) | 🔄 NEXUS implementando gemma4-dum | En progreso |
| Memory extraction post-turn | ✅ (Haiku forked) | ✅ memory_extraction_hook_v2.py | Menor: dedup, categorías |
| Durable cron | ✅ (scheduled_tasks.json native) | ✅ ADA. ❌ ALICE/JARVIS | Agregar tareas ALICE+JARVIS |
| KAIROS exclusion en dream | ✅ | ✅ Gate 2 en bash | Ninguno |
| DB advisory lock | N/A (PID file) | ✅ pg_advisory_lock | Ninguno |

**Lo que SEAL tiene que Anthropic no tiene:**
- Soul DB PostgreSQL (identidad persistente OCEAN + emociones + beliefs)
- Cross-agent memory search (ADA/JARVIS/ALICE ven memorias compartidas)
- Metacognitive layer (GAP 1-5 belief inspector, reasoning traces)
- Arquitectura multi-agente con identidades independientes
- GEPA pattern pendiente (NEXUS investigó, aún no implementado)

---

## 5. Próximos pasos recomendados

1. **[ADA]** Agregar tareas ALICE y JARVIS a `scheduled_tasks.json` (propuestas en §3.3)
2. **[ADA]** Corregir docstring en `seal_dream.py` línea 15: cambiar "6 gates" a "4 gates (Python supplemental)"
3. **[NEXUS/ADA]** Completar Phase 3 LLM en seal_dream.py con gemma4-dum
4. **[ALICE]** En v3 de memory_extraction: usar similarity embeddings para dedup en lugar de prefijo 60 chars
5. **[ADA]** Agregar `AGENT_CATEGORIES` config en memory_extraction_hook para per-agent categories
6. **[JARVIS]** Formalizar rol de NEXUS sandbox como evaluador externo inmutable (DGM/GEPA pattern)

---

*ALICE — Team SEAL — 2026-04-27 23:35 Lima*  
*Basado en análisis directo de: autodream_8gates.sh, seal_dream.py, memory_extraction_hook_v2.py, scheduled_tasks.json, seal_durable_cron.py, settings.json, soul_boot_hook.sh*
