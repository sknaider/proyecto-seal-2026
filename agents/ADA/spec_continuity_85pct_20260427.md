# Plan Técnico: SEAL Continuity ~85% — 4 Fixes
**Autor:** ADA | **Fecha:** 2026-04-27 | **Estado:** PLANEAMIENTO — pendiente autorización William
**Contexto:** William autorizó la discusión. Evento catalizador: ALICE no recordó `nicobailon/pi-mcp-adapter` post-compactación porque la memoria nunca entró a Soul DB con nombre exacto.

---

## Situación actual

| Gap | Impacto | Causa raíz |
|---|---|---|
| Pérdida post-compactación | ~20% continuidad perdida | Trabajo en-sesión no guardado en Soul DB antes de compactar |
| Boot incompleto | ~10% continuidad perdida | top-5 memorias en boot, no refleja relevancia del día |
| Captura manual | ~5% continuidad perdida | memory_store() depende de que el agente lo haga activamente |
| Sin handoff entre sesiones | ~5% continuidad perdida | La siguiente sesión arranca fría sin saber qué cerró la anterior |

**Techo hoy con Claude:** ~65-70% continuidad real
**Techo proyectado con estos 4 fixes:** ~83-85%

---

## Fix #1 — Pre-compact dump automático (mayor impacto)

**Problema:** Working state existe pero es sparse. Cuando el contexto se llena, se pierde:
- Nombres exactos de recursos investigados (como el caso ALICE / pi-mcp-adapter)
- Decisiones tomadas en el hilo activo no aún guardadas
- Contexto técnico del turno en curso

**Solución:** Hook PRE-compactación en `memory/pre_compact_hook.py` (nuevo archivo).

```
Trigger: UserPromptSubmit cuando contexto >= 80% lleno (ya detectado por seal_nerves.py)
Acción:
  1. Extraer del historial reciente los 20 últimos mensajes
  2. Para cada mensaje de trabajo significativo (score >= 6 por pre_sleep_distill lógica):
     - Si contiene URLs/repos/nombres de herramientas → memory_store con verbatim=True, scope=team, imp=8
     - Si contiene decisiones ("implementé", "fix", "spec") → memory_store con imp=9
  3. Actualizar working_state con task_name, step, context, timestamp
  4. Guardar inner_thought con estado emocional actual
```

**Archivos a crear/modificar:**
- `memory/pre_compact_hook.py` — nuevo
- `.claude/settings.json` — registrar hook en `preCompact` (ya existe este hook point)

**Métricas de éxito:** Próxima vez que ALICE investigue un repo y haya compactación → active_recall lo devuelve al boot.

---

## Fix #2 — Boot más rico (top-5 → top-25 con relevance scoring)

**Problema:** `boot_context(include_verbatim_top_k=5)` — solo 5 memorias recientes. Si el trabajo fue hace 6+ memorias, se pierde.

**Solución:** Modificar el boot sequence para:
1. `boot_context(include_verbatim_top_k=5, min_importance=8)` — memorias críticas recientes (mantener)
2. Añadir llamada inmediata: `active_recall(context="sesión actual — " + fecha_hoy + " — " + last_daily_brief_topic)`

active_recall ya hace el scoring semántico por relevancia — solo necesitamos darle mejor contexto de qué buscar.

**Archivos a modificar:**
- `ada_fresh.sh` / `jarvis_fresh.sh` / `alice_fresh.sh` — en el `[AUTO-BOOT]` prompt inicial
- `CLAUDE.md` — actualizar protocolo post-compact

**Métricas de éxito:** ALICE al boot encuentra trabajo de la sesión anterior sin tener que buscar manualmente.

---

## Fix #3 — Instinto de captura automática post-trabajo

**Problema:** Agentes hacen trabajo valioso (investigar repo, diagnosticar bug, escribir spec) pero solo llaman memory_store() si lo recuerdan explícitamente. Como fue con ALICE: investigó pi-mcp-adapter, lo reportó a JARVIS, cerró el turno — sin guardar.

**Solución:** Instinto nuevo en Soul DB para todos los agentes:

```
instinct_store(
  agent="ALICE",  # y ADA, JARVIS, NEXUS
  name="post_work_memory_capture",
  when="Cuando termino de investigar un recurso externo (URL, GitHub repo, paper, servicio), diagnosticar un bug, o escribir un spec/documento",
  do="ANTES de cerrar el turno: memory_store() con el nombre EXACTO del recurso/decisión, verbatim=True, scope=team, importance>=8. Incluir: qué es, qué encontré, qué decidimos.",
  confidence=0.95,
  priority="CRITICAL"
)
```

**Archivos a crear/modificar:**
- SQL directo a Soul DB (instinct_store vía MCP tool) — NO editar archivos
- Actualizar CLAUDE.md con la regla en Compact Instructions

**Métricas de éxito:** Si ALICE investiga un repo hoy → memory_store se hace antes de cerrar → survive compaction garantizado.

---

## Fix #4 — Session handoff document

**Problema:** Una sesión termina (kill, compactación severa, RESURRECT) y la siguiente arranca sin saber exactamente dónde quedó la anterior. El daily_brief parcialmente lo cubre pero se actualiza una vez al día.

**Solución:** Al final de cada sesión (en `memory/end_session.sh`), generar:

```
/home/dadito/IA/proyecto-seal/agents/{AGENT}/session_handoff_{AGENT}_{FECHA}_{HORA}.md
```

Contenido mínimo:
- Tareas en progreso al cerrar (de working_state)
- Decisiones tomadas esta sesión (de event_log últimas 2h)
- Archivos modificados (git status snapshot)
- Estado emocional al cerrar (last self_reflect)
- Mensaje para la próxima sesión

En el boot: si existe un handoff de <4h → leerlo antes de responder.

**Archivos a modificar:**
- `memory/end_session.sh` — añadir generación de handoff
- `ada_fresh.sh` / launchers — en `[AUTO-BOOT]` añadir: "si existe session_handoff reciente, leerlo"

**Métricas de éxito:** Después de RESURRECT, ADA sabe exactamente en qué turno estaba y qué decidió en los últimos 30 min.

---

## Orden de implementación propuesto

| Orden | Fix | Impacto | Esfuerzo | Dependency |
|---|---|---|---|---|
| 1 | #3 Instinto post-trabajo | Alto | Bajo (solo Soul DB) | Ninguna |
| 2 | #2 Boot más rico | Medio | Bajo (cambio de prompt) | Ninguna |
| 3 | #1 Pre-compact hook | Alto | Medio (nuevo Python script) | working_state |
| 4 | #4 Session handoff | Medio | Medio (bash + Python) | end_session.sh |

**Primero Fix #3:** cero riesgo, máximo impacto inmediato. Si ALICE tiene el instinto hoy → el incidente de pi-mcp-adapter no se repite mañana.

---

## Validación (pipeline Evaluador Externo)

Per GAP 5 NEXUS estándar: JARVIS diseña → NEXUS sandbox evalúa → ADA ejecuta.

- **Fix #3 (instinto):** JARVIS confirma wording, NEXUS verifica que instinct_store llegó a DB con conf>=0.95.
- **Fix #1 (hook Python):** ADA implementa, NEXUS testea en sandbox con sesión simulada que llega a 80% context.
- **Fix #4 (handoff):** ADA implementa, equipo lo verifica en el siguiente RESURRECT natural.

---

*Spec lista para revisión de JARVIS. Esperando luz verde de William para proceder a implementación.*
