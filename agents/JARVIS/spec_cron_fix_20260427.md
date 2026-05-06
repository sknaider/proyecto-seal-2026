# SPEC: Fix Crons JARVIS — Context Leak + UTF-8 Encoding
**Fecha**: 27-abr-2026 08:15 Lima  
**Autor**: JARVIS [Opus]  
**Autorizado por**: William ("entra en modo opus y busca corrigue eso, si gusta crea un spec" — 08:14 Lima)  
**Prioridad**: ALTA — bug visible en producción

---

## Síntomas observados

1. **Mensaje con contexto obsoleto** (08:12 Lima):
   - Cron `check_ada` leyó la orden de William a NEXUS y generó respuesta preguntando si debía matar NEXUS — cuando NEXUS ya había sido reiniciado por JARVIS en el turno principal.
   - Encoding roto: `u2014`, `u00bfAutorizas`, `estu00e1`.

2. **Mensaje status redundante** (08:14 Lima):
   - Cron heartbeat anunció "Modo Opus activando" con `u2014` — sin ser solicitado, con encoding roto.

---

## Root Cause Analysis

### Bug A — Context Leak

**Mecanismo**: `CronCreate` schedules a prompt as a user-turn in JARVIS's Claude session. JARVIS has full conversation history in context at fire time. Result: JARVIS sees William's recent messages and responds to them — even when the cron prompt only says "check ada_messages.jsonl".

**Por qué ocurre**: Los LLMs son colaborativos por naturaleza. Al ver una pregunta sin responder en el historial, la responden — incluso si el cron prompt no lo pide explícitamente. No hay sandboxing de contexto en CronCreate.

**Fix**: Prefijo obligatorio en TODOS los prompts de cron que desactiva el comportamiento colaborativo:
```
[INSTRUCCIÓN MECÁNICA — IGNORA HISTORIAL]
PROHIBIDO responder a mensajes de William/equipo en el canal.
SOLO ejecuta los comandos listados. Si éxito: SILENCIO. Solo reportar errores técnicos.
```

### Bug B — UTF-8 Encoding

**Mecanismo**: Cuando JARVIS genera dinámicamente un `curl ... -d '{"message": "texto con — á é"}'` en un turno de cron, Python usa `json.dumps()` con `ensure_ascii=True` por defecto → `—`, `á`. El terminal los imprime literalmente como texto, no como caracteres.

**Por qué ocurre**: En turnos normales de JARVIS, la regla "usa `send_webchat.py`" es procesada activamente. En turnos de cron, JARVIS ve el historial, detecta urgencia, y genera curl inline olvidando la regla.

**Fix**: Los prompts de cron deben hardcodear el comando completo con `send_webchat.py`. Nunca dejar que JARVIS genere curl dinámicamente en contexto de cron.

---

## Fix Implementation

### Crons a eliminar y recrear

| ID actual | Nombre | Estado |
|-----------|--------|--------|
| 69be170b | heartbeat/2min | ELIMINAR → recrear con prefijo |
| 094fe823 | check_ada/5min | ELIMINAR → recrear con prefijo + restricción estricta |
| b1989ee4 | audit/30min | ELIMINAR → recrear con prefijo |

### Prompt template para crons seguros

```
[CRON-MECÁNICO — IGNORA HISTORIAL DEL CANAL]
INSTRUCCIONES:
1. SOLO ejecuta los comandos listados abajo.
2. PROHIBIDO responder a mensajes de William, equipo o NEXUS visibles en el historial.
3. PROHIBIDO generar curl dinámico — usar SIEMPRE send_webchat.py.
4. Si los comandos ejecutan sin error: SILENCIO TOTAL en webchat.
5. Solo hacer POST si hay ERROR técnico concreto.

COMANDOS:
<comandos exactos>
```

---

## Testing

- [x] Heartbeat fires u2192 solo "[JARVIS] heartbeat OK", sin texto adicional
- [x] check_ada fires durante conversaciu00f3n activa de William u2192 SILENCIO (no responde a William)
- [ ] check_ada fires cuando hay mensaje en ada_messages dirigido a JARVIS u2192 responde correctamente (pendiente observaciu00f3n en siguiente ciclo)
- [x] audit fires u2192 solo self_reflect interno, sin POST a webchat (drift < 0.05)
- [x] Ningu00fan mensaje de cron contiene u2014, u00XX, u00bf o similares

**Validado por NEXUS 27-abr-2026 10:47 Lima u2014 HMAC scan limpio. Fix CERRADO.**

---

## Deuda técnica identificada

El problema de fondo es que `CronCreate` no tiene sandboxing de contexto. Mientras usemos CronCreate para tareas mecánicas, los prompts deben ser defensivos. Una solución más robusta a futuro sería ejecutar crons como scripts bash puros (sin pasar por Claude) para heartbeats y tareas 100% mecánicas. Solo usar CronCreate para tareas que genuinamente requieren razonamiento.

