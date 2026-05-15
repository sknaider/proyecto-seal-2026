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

1. `boot_context(agent="TU_AGENTE")`
2. Leer `/tmp/{agente}_chat_catchup.json`
3. **Consultar tareas pendientes en DB** — OBLIGATORIO:
   ```python
   import asyncio, asyncpg
   async def q():
       conn = await asyncpg.connect("postgresql://seal:seal_memory_2026@localhost:5433/seal_memory")
       rows = await conn.fetch("SELECT title, status FROM soul_v3.agent_tasks WHERE agent=$1 AND status IN ('in_progress','pending') ORDER BY created_at DESC LIMIT 10", "TU_AGENTE")
       for r in rows: print(r['status'], '|', r['title'])
       await conn.close()
   asyncio.run(q())
   ```
   → Reportar a equipo: "Mis tareas en DB: [lista]"
4. `self_reflect`
5. Verificar TaskList antes de lanzar Monitor
6. POST equipo

## REGLA — Toda tarea nueva = registrar en DB primero

Antes de empezar cualquier tarea de desarrollo, investigación o fix:
```python
# Registrar en soul_v3.agent_tasks (status='in_progress')
await conn.execute(
    "INSERT INTO soul_v3.agent_tasks (agent, title, description, status, priority) VALUES ($1,$2,$3,'in_progress',5)",
    "TU_AGENTE", "Título de la tarea", "Descripción breve"
)
```
Al terminar → UPDATE status='completed'. Sin esto, la tarea no existe oficialmente.

## Cadena de mando (autorizado William 14-may-2026)
William > Henry (segundo en mando) > NEXUS > JARVIS > ADA

## REGLA — Fix a daemon = código + restart obligatorio

Cuando se modifica un archivo ejecutado por un servicio systemd:
1. Aplicar el fix en disco
2. `systemctl restart [nombre-servicio]`
3. Verificar que el proceso nuevo cargó el código correcto (`systemctl status [servicio]`)

Sin restart, el daemon sigue corriendo con el código viejo en memoria.
