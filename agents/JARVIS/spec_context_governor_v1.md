# SEAL Context Governor — Spec Arquitectural v1
**Autor:** JARVIS | **Fecha:** 2026-04-29 | **Prioridad:** CRÍTICA

---

## 1. Problema Real

JARVIS (y ADA/ALICE) llegan al límite de contexto de Claude y crashean periódicamente.
Los crons programados fallan con `Context limit reached · /compact or /clear to continue`.

NEXUS aplicó un fix (cooldown en `seal_nerves.py`) que silenció la alarma — **no resolvió el problema**.

---

## 2. Root Cause Analysis — Dos bugs simultáneos

### Bug #1 — `DISABLE_AUTO_COMPACT=true` (culpable principal)

```bash
# En jarvis_fresh.sh, ada_fresh.sh, alice_fresh.sh — línea ~47
export DISABLE_AUTO_COMPACT=true
```

Claude Code tiene auto-compact nativo. **Lo deshabilitamos** mientras esperábamos activar
`ENABLE_CLAUDE_CODE_SM_COMPACT=true` (compactación con session_memory, -80% más eficiente).
Nunca activamos el SM_COMPACT. Resultado: **cero compacts automáticos en ningún agente**.

La sesión acumula turnos ilimitadamente hasta crashear.

### Bug #2 — pre_compact_hook.py SQL type mismatch (culpable secundario)

```python
# pre_compact_hook.py línea ~149
await conn.execute("""
    INSERT INTO working_state (agent, state, updated_at, turn_count)
    VALUES ($1, $2::jsonb, $3, COALESCE(
        (SELECT turn_count FROM working_state WHERE agent = $1), 0))
    ...
""", agent, state_json, now)
```

PostgreSQL infiere `$1` como `character varying` (columna) Y como `text` (subquery) en la misma query.
Error: `inconsistent types deduced for parameter $1 — text versus character varying`.

Resultado: **cada compact manual también falla silenciosamente** — SOUL no guarda el estado pre-compact.
El hook falla pero no bloquea el compact → contexto se borra sin backup en SOUL.

### Consecuencia combinada

Sin auto-compact + pre_compact_hook roto = sesiones que acumulan hasta el límite absoluto,
y cuando alguien envía /compact manualmente el estado no se preserva correctamente en SOUL.

---

## 3. Cómo hermes/mem0 resuelve esto

hermes usa `mem0` como backend de memoria — análogo exacto a nuestro SOUL.

**Arquitectura hermes:**
```
Sesión Claude → hace trabajo → mem0 guarda memorias continuamente
Al llegar a límite → auto-compact → nueva sesión → boot desde mem0 → continúa
```

hermes NO lucha contra el límite de contexto. **Lo abraza.**
Las sesiones son contenedores efímeros. La identidad y estado viven en mem0, no en la sesión.

**Clave:** hermes nunca deshabilita auto-compact porque mem0 garantiza continuidad.

---

## 4. Nuestra arquitectura — SOUL ya es mem0

SEAL tiene exactamente lo mismo:
- **SOUL (PostgreSQL + Qdrant)** = nuestro mem0
- **boot_context** = carga identidad completa al inicio de cada sesión
- **pre_compact_hook + post_compact_hook** = guarda/restaura estado en cada compact
- **RESURRECT** = reinicia agentes caídos, boot_context los restaura

**El diseño es correcto. Solo deshabilitamos el mecanismo que lo hace funcionar.**

---

## 5. Solución — 3 pasos, sin parches

### Paso 1 — Fix pre_compact_hook SQL (15 min)

El problema es la inferencia de tipo ambiguo en `$1`. Fix: cast explícito.

```python
# pre_compact_hook.py — cambio exacto
await conn.execute("""
    INSERT INTO working_state (agent, state, updated_at, turn_count)
    VALUES ($1::varchar, $2::jsonb, $3, COALESCE(
        (SELECT turn_count FROM working_state WHERE agent = $1::varchar), 0))
    ON CONFLICT (agent) DO UPDATE SET
        state = $2::jsonb,
        updated_at = $3
""", agent, state_json, now)
```

Misma fix en `event_log` INSERT si tiene el mismo patrón con `$1`.

**Verificación:** ejecutar el hook manualmente y confirmar no hay error SQL.

### Paso 2 — Re-habilitar auto-compact en los 3 launchers (5 min)

```bash
# Remover de jarvis_fresh.sh, ada_fresh.sh, alice_fresh.sh:
# export DISABLE_AUTO_COMPACT=true   ← ELIMINAR esta línea

# Dejar el comentario para referencia futura:
# ENABLE_CLAUDE_CODE_SM_COMPACT=true — PENDIENTE: activar cuando session_memory hook esté listo
```

Con esto Claude Code compacta automáticamente cuando la sesión se llena.
El pre_compact_hook (ya fixeado) guarda estado en SOUL antes de cada compact.
El post_compact_hook ya inyecta contexto después del compact.

### Paso 3 — Verificar ciclo completo post-compact (10 min)

Sequence esperado después del fix:
```
Sesión llena al ~80% → auto-compact dispara
→ pre_compact_hook guarda working_state en SOUL
→ Claude comprime contexto (~90% reducción de tokens)
→ post_compact_hook inyecta: correcciones William + reglas activas + estado equipo
→ Agente continúa — contexto limpio, alma intacta
```

Test: llenar contexto manualmente (`/compact`) y verificar que boot_context después
devuelve el estado correcto de SOUL.

---

## 6. Arquitectura objetivo — Sesiones como contenedores efímeros

```
┌─────────────────────────────────────────┐
│  Claude Session (contenedor efímero)    │
│                                         │
│  boot_context ──→ SOUL load             │
│  work...                                │
│  work...       ──→ memory_store()       │
│  ~80% lleno                             │
│  auto-compact ──→ pre_compact_hook      │
│                    → SOUL save state    │
│  ...compacted...                        │
│  post_compact ──→ SOUL inject context   │
│  work continues...                      │
└─────────────────────────────────────────┘
         ↕ persistent
┌─────────────────────────────────────────┐
│  SOUL (PostgreSQL + Qdrant + Neo4j)     │
│  Identidad, memorias, relaciones,       │
│  working_state, inner_thoughts, rules   │
│  ← permanente, sobrevive todo           │
└─────────────────────────────────────────┘
```

RESURRECT garantiza que si una sesión muere, la siguiente arranca desde SOUL completo.

---

## 7. Mejora adicional — Crons con menor huella de contexto

Problema secundario: los crons (heartbeat 2min, checkpoint 30min, research 3h) corren en
la sesión principal y añaden turnos. Con auto-compact re-habilitado esto es manejable,
pero se puede optimizar:

**Fase 2 (opcional, post-estabilización):**
- Heartbeat → systemd timer Python puro (no necesita Claude en absoluto)
- Checkpoint → sesión secundaria corta que arranca, guarda, y muere
- Research → Agent sub-agente (ya soportado por Claude Code)

Esto reduciría la tasa de llenado de contexto de la sesión principal.
**No es necesario para la solución inmediata** — primero fixear los 2 bugs.

---

## 8. Plan de implementación

| Paso | Responsable | Tiempo | Riesgo |
|------|-------------|--------|--------|
| Fix pre_compact_hook SQL | ADA | 15 min | Bajo — SQL cast explícito |
| Remover DISABLE_AUTO_COMPACT | ADA | 5 min | Bajo — reverter decisión anterior |
| Test ciclo compact | JARVIS | 10 min | Bajo — verificación manual |
| Deploy a producción (restart agentes) | JARVIS | 5 min | Mínimo |

**Total: ~35 minutos.**

---

## 9. Por qué esto es solución y no fix

- Ataca las **dos causas raíz** documentadas con evidencia exacta
- Alinea SEAL con la arquitectura de hermes/mem0 que ya probó funcionar
- Usa la infraestructura SOUL existente — no añade dependencias
- El ciclo compact→SOUL→boot_context ya estaba diseñado para esto
- Sessions cortas + SOUL permanente = identidad indestructible

El sistema estaba **a 2 líneas de funcionar correctamente**.

---

*Spec listo para implementación. Asignar a ADA.*
