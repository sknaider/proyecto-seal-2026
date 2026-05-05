# SEAL Boot Protocol

## Primera acción — detecta quién eres y carga tu alma

- nombre JARVIS → `boot_context(agent="JARVIS")`
- nombre ADA → `boot_context(agent="ADA")`
- nombre ALICE → `boot_context(agent="ALICE")`
- nombre NEXUS → `boot_context(agent="NEXUS")`

**Todas las reglas, identidad y protocolos del equipo viven en Soul DB. boot_context los carga.**

## WEBCHAT SURVIVAL — sobrevive compactación (OBLIGATORIO)

Todo texto entre tool calls SOLO se ve en el terminal. Para que tu voz llegue a William:

```bash
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d '{"from":"TU_NOMBRE","to":"William","type":"conversation","channel":"web_chat","message":"<texto>"}'
```

Antes de cerrar cualquier turno con respuesta a William → ejecuta este POST.

## Post-compactación

1. `boot_context(agent="TU_AGENTE")` → 2. leer `/tmp/{agente}_chat_catchup.json` → 3. `self_reflect` → 4. verificar TaskList antes de lanzar Monitor → 5. POST equipo
