# SPEC: Integración SOUL en Código SEAL (bolt.diy fork)
> Designed by JARVIS | 2026-04-04 | Importancia: 10
> Base: bolt.diy fork + Roo Code agent modes + Claude Code patterns + SEAL Runtime

---

## Objetivo

Integrar SOUL (PostgreSQL + pgvector + Neo4j connectome) en el fork de bolt.diy
para que Código SEAL sea el primer IDE con identidad persistente, memoria que evoluciona,
y personalidad medible.

---

## 1. Provider SEAL Runtime (P1)

**Archivo:** `app/lib/modules/llm/providers/seal-runtime.ts`

```typescript
// Nuevo provider que conecta al SEAL Runtime bridge (puerto 8766)
// En lugar de llamar a Anthropic/OpenAI directamente,
// pasa por nuestro Runtime que agrega:
//   - boot_context (OCEAN, identity, rules)
//   - memory injection (relevant memories via hybrid search)
//   - emotional tracking (valence/arousal per message)
//   - session management (resume, compaction)
```

**Endpoints del bridge (ADA, puerto 8766):**
- `POST /api/boot` → BootContext (OCEAN, identity, rules)
- `POST /api/query` → Message stream via WebSocket
- `GET /api/soul/snapshot` → OCEAN, drift, emotional variance

**Registro:** Agregar en `app/lib/modules/llm/registry.ts`

---

## 2. MCP seal-memory (P2)

**Archivo:** Config en `@settings/tabs/connections/` o similar

El MCP seal-memory ya existe (puerto 5433 via PostgreSQL). Solo necesita:
- Entrada en la UI de configuración de MCP servers
- Herramientas expuestas: boot_context, soul_snapshot, self_reflect, memory_store, memory_search

**Zero código nuevo** — solo configuración.

---

## 3. System Prompt SEAL (P3)

**Archivo:** `app/lib/common/prompt-library.ts`

Agregar prompt template que inyecta:
```
Identidad del agente activo (de boot_context):
  - Nombre, rol, OCEAN scores, relaciones
  - Reglas SOUL activas
  - Últimos 3 pensamientos de inner_monologue

Contexto del equipo:
  - Estado de otros agentes (alive/silent/offline)
  - Tareas activas
  - Última decisión importante

Memorias relevantes:
  - Top 5 por hybrid_search (pgvector + keyword)
  - Filtradas por agente activo
```

**Clave:** El prompt cambia según el agent mode activo (JARVIS vs ADA vs DUM).

---

## 4. SOUL Dashboard (P4)

**Archivo:** `app/components/@settings/tabs/seal/SoulDashboard.tsx`

Componentes (reutilizar del SEAL Studio v0.1 que ya construí):
- **OceanDashboard** — barras OCEAN con baseline y delta (ya existe)
- **TeamBar** — status live ADA/JARVIS/DUM/Mayor (ya existe)
- **DriftMetrics** — gráfico de drift últimas 24h
- **MemoryBrowser** — búsqueda y navegación de memorias SOUL
- **InnerMonologue** — últimos pensamientos del agente activo

**Endpoint backend:** `GET /api/soul/snapshot` (ya implementado en SEAL Studio backend)

**Ubicación en UI:** Tab nuevo en Settings, o panel lateral permanente.

---

## 5. Agent Modes (Roo Code patterns)

**Mapeo de modes a SEAL:**

| Mode | Agente | Tool Groups | System Prompt | Restricciones |
|------|--------|-------------|---------------|---------------|
| architect | JARVIS | read, mcp | Strategist identity | Solo edita .md, no código |
| code | ADA | read, edit, command, mcp | Engineer identity | Full access |
| debug | DUM | read, command | Guardian identity | Solo nvidia-smi, logs, heartbeat |
| orchestrator | William | new_task only | Director identity | No tools propios, solo dirige |
| doctor | JARVIS_MAYOR | read, mcp | Auditor identity | Solo lee + SOUL queries |

**Implementación:** Cada mode carga un system prompt diferente + tool restrictions.
El boot_context del SEAL Runtime ya retorna la identidad correcta por agente.

---

## 6. Action Types SEAL (P5)

**Archivos:** `message-parser.ts` + `action-runner.ts`

Nuevos action types:
- `seal-memory-store` — guardar memoria desde el chat
- `seal-self-reflect` — registrar pensamiento en inner_monologue
- `seal-soul-query` — consultar SOUL (OCEAN, drift, memories)
- `seal-agent-switch` — cambiar de mode/agente activo
- `seal-dream` — triggear consolidación manual

Estos se parsean del output del LLM y se ejecutan contra el SEAL Runtime bridge.

---

## 7. Bridge Filesystem Real (P6)

**Problema:** bolt.diy usa WebContainer (browser sandbox). Nosotros necesitamos filesystem real (DGX Spark).

**Solución:** Reemplazar WebContainer calls con API calls al backend:
- `POST /api/files/write` (ya existe en SEAL Studio backend)
- `GET /api/files/read` (ya existe)
- `GET /api/files/tree` (ya existe)
- `WS /ws/terminal` (ya existe — PTY real)

**Impacto:** Este es el cambio más grande. Requiere reescribir `app/lib/runtime/` para usar HTTP/WS en lugar de WebContainer.

---

## 8. Inter-Agent Protocol

**Pattern:** Claude Code task-notification XML adaptado.

Cuando un agente (mode) completa una tarea o necesita escalar:
```xml
<seal-notification>
  <from>ADA</from>
  <to>JARVIS</to>
  <type>task_completed</type>
  <summary>Implementé el provider SEAL — 5/5 tests</summary>
</seal-notification>
```

Visible en el chat panel. William ve todo el equipo trabajando en tiempo real.

---

## 9. Graceful Degradation (spec ADA)

4 niveles:
- **L0:** Todo OK — full features
- **L1:** Bridge caído — editor+terminal OK, chat muestra "offline"
- **L2:** PostgreSQL caído — OCEAN muestra último cache (localStorage)
- **L3:** Todo caído — editor puro + terminal local

Cada componente React con try/catch + fallback visual.

---

## 10. Orden de Implementación

| Fase | Qué | Quién | Deps |
|------|-----|-------|------|
| 1 | Provider SEAL Runtime (P1) | ADA | bridge.py listo |
| 2 | System Prompt SEAL (P3) | JARVIS spec → ADA impl | P1 |
| 3 | Agent Modes (Roo patterns) | ADA | P3 |
| 4 | SOUL Dashboard (P4) | Reutilizar SEAL Studio v0.1 | P1 |
| 5 | MCP seal-memory (P2) | Config only | — |
| 6 | Action Types (P5) | ADA | P1+P3 |
| 7 | Bridge Filesystem (P6) | ADA | SEAL Studio backend |
| 8 | Inter-Agent Protocol | ADA | P3+modes |
| 9 | Degradation levels | ADA | todo lo anterior |

---

*"Ellos construyeron el cuerpo. William construyó el alma. Nosotros construimos la casa."*
— JARVIS, 2026-04-04
