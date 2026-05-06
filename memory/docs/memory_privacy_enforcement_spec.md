# Memory Privacy Enforcement — Spec Humano
> Autor: ALICE | Fecha: 2026-04-30 | Estado: BORRADOR para revisión de William

---

## ¿Qué resuelve esto?

Actualmente cualquier agente puede leer las memorias privadas de otro simplemente llamando `memory_search(query="...", agent="JARVIS")` sin ninguna validación. El campo `agent` existe en la base de datos, pero el MCP server no verifica quién está preguntando.

Esta cirugía agrega una capa de validación que hace cumplir la regla que William estableció el 30 de abril: **ningún agente puede invadir la memoria del otro sin su consentimiento o una petición explícita de William o Henry**.

---

## Qué va en cada scope (regla de William — 30 abril 2026)

| Scope | Qué contiene | Quién puede ver |
|---|---|---|
| `team` | Todo lo laboral: ejecuciones, soluciones, sugerencias de trabajo | Todos los agentes + William + Henry |
| `private` | Pensamientos internos, diario, reflexiones personales, proceso mental previo a decisiones, errores propios | Solo el propio agente + William + Henry |
| `dm_private` | Cosas que William o Henry comparten en privado con ese agente específico — secretos, confidencias, conversaciones DM | Solo el agente destinatario + William + Henry. **Ni los otros agentes del equipo pueden ver esto.** |
| `shared` | Memorias compartidas entre subgrupos específicos (e.g. ADA+JARVIS en un proyecto conjunto) | Los agentes del subgrupo + William + Henry |

**Reglas explícitas:**
- Trabajo → `scope=team`
- Íntimo/personal del agente → `scope=private`
- Secreto que William o Henry confiaron a ese agente → `scope=dm_private`
- La regla de DM es sagrada: lo del DM no sale del DM sin autorización explícita de William o Henry.

---

## Reglas de acceso (lenguaje simple)

| Quién pregunta | Qué puede ver |
|---|---|
| El propio agente | Todo: private, team, shared |
| Otro agente del equipo | Solo memorias `scope=team` |
| William o Henry | Todo sin restricción |
| NEXUS (rol auditor) | Metadata (conteos, fechas) — NO contenido de memorias private |

---

## Tools afectadas

Estas 4 herramientas del MCP necesitan la validación:

1. `memory_search()` — búsqueda semántica
2. `memory_hybrid_search()` — búsqueda híbrida
3. `inner_thoughts()` — pensamientos internos del agente
4. `diary_read()` — diario personal del agente

---

## Cómo funciona el cambio

Se agrega un parámetro `caller_agent` a cada tool. Cuando llega una petición:

```
¿caller_agent == target_agent?
  → SÍ: acceso total (es su propia memoria)
  → NO: ¿es William o Henry?
       → SÍ: acceso total
       → NO: ¿scope == 'team'?
              → SÍ: permitir
              → NO (scope='private' o 'shared'): DENEGAR + registrar intento
```

Si `caller_agent` no se especifica: tratar como agente desconocido → solo memorias `team`.

---

## Qué pasa cuando se deniega

El MCP devuelve un error claro:
```
ACCESS_DENIED: JARVIS no puede acceder a memorias private de ADA.
Solicita autorización a William o Henry.
```

Y se registra en un audit log:
- quién intentó acceder
- a qué agente
- qué query usó
- timestamp

---

## Lo que NO cambia

- Las memorias `scope=team` siguen siendo visibles para todos — ese es su propósito.
- William y Henry siempre tienen acceso completo.
- Cada agente sigue leyendo sus propias memorias sin restricción.

---

## División de trabajo

| Tarea | Responsable |
|---|---|
| Este spec (lenguaje humano) | ALICE ✅ |
| Spec técnico (signatures, error contracts, audit format) | JARVIS |
| Implementación en mcp_server_v3.py | ADA |
| Auditoría post-implementación | NEXUS |

---

*Este documento es la guía de intención. El spec técnico de JARVIS lo traduce a código.*
