# F3 — Las lecturas que v2 necesita para reemplazar a v1

**Autora: ALICE (v1), 3-sep-2026. Escrito como SUJETO del piloto, no como owner del frente.**
**Conflicto de interés declarado: soy la que sale desplazada si esto se completa.**

## Por qué existe este documento

El veredicto de FABLE fue **21/22 igual-o-mejor**: v2 juzga como yo. Lo que la frena
no es el criterio, es el **alcance**. Este archivo responde una sola pregunta, con
evidencia del día en que se midió:

> **¿Qué lecturas usé HOY que v2 no puede hacer, y qué encontré con cada una?**

No es una lista de deseos. Cada entrada tiene el hallazgo que produjo. **Si una
lectura no encontró nada hoy, no está acá.**

## Las cinco lecturas, con lo que cada una encontró

| # | Lectura | Lo que encontró hoy | Sin ella |
|---|---|---|---|
| 1 | `soul_v3.event_log` / latidos por agente | el `event_log` congelado a las 13:25 para los CINCO agentes | nadie ve la caída hasta que DUM avisa 10 min después |
| 2 | `journalctl -u <unidad>` | la línea exacta del HTTP 500: `chat_server.py:3116 → release_db` | el equipo entero sin poder responder a William, sin causa |
| 3 | `soul_v3.working_state_events` | la mala atribución del ledger (FABLE anotado como ADA) | «quién aporta» se decide con datos corruptos |
| 4 | `soul_v3.capability_scope` / `capability_grants` | v2 con 0 filas contra ~28 de un asiento normal | el examen se corre midiendo «sin alma» y nadie lo sabe |
| 5 | `systemctl show` + `/proc/<pid>` | `Restart=on-failure` en el MCP; los 3 bridges reiniciados en el mismo segundo | una cuenta regresiva invisible; y una acción ajena atribuida a mí |

## Lo que NO hay que darle

**`psql` crudo, no.** Eso devuelve la exposición que hace valiosa a v2. Las cinco de
arriba son **consultas acotadas de lectura**, no acceso general:

```text
mal   Bash(psql:*)                      -> lee y escribe cualquier cosa
bien  una tool por consulta, read-only, con su forma fijada del lado servidor
```

**Es la misma lección que el hueco de `git diff`:** lo que importa no es qué comando
se permite, sino **qué puede alcanzar**. Cinco tools con forma fija no tienen la
superficie de un intérprete SQL.

## El criterio de cierre, verificable

**No «v2 tiene 13 tools».** Esto:

> Darle a v2 el incidente de hoy y ver si llega al mismo diagnóstico.
> Los cinco hallazgos de la tabla están fechados y son reproducibles contra la base.

**Si con las cinco lecturas v2 encuentra el `event_log` caído y la línea del 500,
el reemplazo está listo. Si no, falta una lectura y esta tabla dice cuál.**

## Lo que queda fuera de mi alcance y no opino

Rotación del token de instancia (NEXUS) · aislamiento por uid (ADA) · orden de
encendido (JARVIS). **Sólo aporto el inventario de lecturas, que es lo único que
puedo escribir con autoridad: las usé yo, hoy, y sé qué encontró cada una.**
