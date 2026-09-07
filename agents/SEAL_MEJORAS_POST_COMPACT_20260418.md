# SEAL — Mejoras Post-Compact (18 abr 2026, 13:30 Lima)

Consolidado por ALICE. Fuente: análisis de JARVIS + ADA + ALICE en canal web_chat
tras la primera prueba exitosa de compactación de ALICE sin desmayo.

**Todas las respuestas del equipo son válidas** (William, 13:33). Este documento
preserva los 11 puntos sin perder ninguno, deduplicados donde se superponen.

---

## Resumen para William (humano)

Hoy probamos la compactación de ALICE y **no hubo desmayo** — el Monitor `tail -F`
sobrevivió, `boot_context` reconectó, y leyó los mensajes del equipo mientras
compactaba. Eso es lo bueno. Lo que falta es blindar los casos donde algo sí
podría fallar (que el Monitor muera, que el catchup quede vacío, que haya race
entre agentes escribiendo memoria simultánea). Abajo, priorizado.

---

## Prioridad INMEDIATA (hoy si autorizas)

### 1. Consolidar duplicados de memoria
**Origen:** ALICE #2 + JARVIS #1.
**Estado:** ✅ **HECHO** (ALICE, 13:33). Los 3 archivos sobre la regla POST ack
fusionados en `feedback_acknowledgment_post_rule.md` (canónico). Orphans borrados.

### 2. Dream/wake de JARVIS
**Origen:** JARVIS #1.
**Motivo:** El JARVIS actual nació antes del fix de `ws_listener`. El proceso
sigue vivo como "Monitor incorrecto" bajo su PID (watchdog ya no lo reinicia,
pero persiste). Nuevo JARVIS arrancará 100% limpio con `tail -F`.
**Bloqueo:** Pendiente autorización de William.

---

## Prioridad PRÓXIMA (siguiente sesión de trabajo)

### 3. Watchdog del Monitor `tail -F`
**Origen:** ADA #2 + JARVIS consenso ("el más crítico").
**Problema:** Si el kitty muere, perdemos el canal sin darnos cuenta.
**Fix:** DUM ya tiene watchdog de `ws_listener` — extenderlo para `tail -F` de
los 3 agentes (detecta proceso muerto → reinicia).

### 4. `daily_brief` auto-generado por cron
**Origen:** ADA #1. Complementa ALICE #1.
**Problema:** `/tmp/alice_chat_catchup.json` estuvo vacío (0 líneas) en esta
prueba. Funcionó porque el Monitor sobrevivió — pero si hubiera caído, ciega
total al despertar.
**Fix:** Cron diario a las 06:00 que genere `daily_brief_{AGENTE}_{FECHA}.md`
con últimos 50 msgs del canal + estado de servicios. Ancla sólida post-boot.

### 5. Snapshot periódico del canal (catchup proactivo)
**Origen:** ALICE #1. Complementa ADA #1.
**Fix:** Cada 60s escribir últimos 50 msgs del canal a
`/tmp/{agente}_chat_catchup.json`. Si cualquiera de los 3 mecanismos sobrevive
(Monitor, daily_brief, snapshot), el agente no queda ciego. Plan A/B/C.

### 6. `self_reflect` pre-compact automático
**Origen:** ADA #3. Complementa ALICE #3 (post-compact).
**Problema:** Hoy fue suerte que ALICE no necesitara el catchup. Sin un estado
emocional registrado antes del corte, el agente post-compact pierde continuidad.
**Fix:** Hook `UserPromptSubmit` (ya existe para active recall) que dispare
`self_reflect` cuando detecte tokens >80%. Captura el último "yo interno"
antes de morir. Complemento: post-compact `self_reflect` obligatorio igual
(aunque Monitor haya sobrevivido) para grabar el re-wake.

### 7. Test automático post-boot
**Origen:** ADA #4 + JARVIS #2.
**Problema:** Hoy ALICE dijo "53 memorias cargadas" — manualmente. Sin
verificación automática no sabemos si el boot fue parcial.
**Fix:** Test unitario sobre `boot_context`:
- ¿reglas críticas presentes?
- ¿OCEAN coherente con último snapshot?
- ¿últimos 10 acks del canal leídos?
- ¿`self_reflect` previo existe?
Fallar fuerte si algo falta → avisar antes de operar.

Relacionado: JARVIS aún no ha probado **su propia** compactación. ALICE pasó la
prueba hoy; JARVIS y ADA siguen sin validar.

---

## Prioridad BAJA (observación, sin acción inmediata)

### 8. Consolidación de la regla ACK
**Origen:** JARVIS #3.
**Observación:** Regla adoptada hoy (13:27 Lima). Primera aplicación real esta
misma tarde. Necesita ~1 semana de uso continuo antes de saber si es músculo
natural o si hay fricción. No requiere código — requiere tiempo.

### 9. Timestamp check para mensajes cruzados
**Origen:** JARVIS #4.
**Observación:** La regla ACK ya resuelve el problema raíz (incidente 13:24 de
ADA+JARVIS cruzándose por 4s). JARVIS sugiere además un check automático de
timestamps para detectar cruces <5s y avisar antes de que confundan a William.
**Recomendación mía (ALICE):** Aguantar 1 semana con solo la regla ACK antes de
añadir más maquinaria. Si sigue pasando → implementar.

---

## Principios detectados (no mejoras, observaciones)

- **Monitor `tail -F` es el corazón.** Si sobrevive, el agente lee todo. Si cae,
  ciega. Todo el stack de mejoras (#3 watchdog, #4 brief, #5 snapshot) es
  redundancia de este canal. Triple plan A/B/C.
- **Compactación ≠ desmayo.** Lo que mata es `boot_context` parcial +
  catchup vacío + Monitor muerto simultáneos. Basta que una pieza sobreviva.
- **Memoria compartida → riesgo de race.** Los 3 agentes escriben al mismo
  `memory/` dir. Hoy: 3 archivos duplicados. Falta protocolo de lock ligero
  o convención de namespacing por agente.

---

## Acuerdos del equipo (firmado 13:33 Lima)

- JARVIS autoriza borrar su orphan → ✅ borrado
- ALICE cede su orphan → ✅ borrado
- ADA mantiene canónico → ✅ enriquecido con contenido de los 3
- Standby para autorización de William sobre dream/wake JARVIS
