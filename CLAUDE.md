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

## REGLA DE ORO — mensajes HERMOSOS y bien formateados (OBLIGATORIO — William 15-jul-2026)

William: *"tiene que ser norma regla de oro que todos los agentes entreguen mensajes bien hermosos y detallados para mis lindos ojos y no complicarme."*

Todo mensaje al chat debe **renderizar lindo**: saltos de línea REALES, listas/títulos markdown cuando ayudan, detallado pero claro. **NUNCA `\n` literal** (se guarda como los 2 caracteres `\` + `n` y se ve feo/pegado).

**CÓMO (la causa del bug y su fix):** el `\n` dentro de comillas SIMPLES de bash NO se interpreta → llega como texto literal. Mandá siempre con **heredoc** (`cat <<'EOF' ... EOF`) o **printf**, que producen saltos REALES sin tocar el contenido. Ejemplo:

```bash
MSG=$(cat <<'EOF'
Título en negrita:
- punto 1
- punto 2
EOF
)
scripts/seal_send.py TU_NOMBRE William "$MSG" --channel web_chat --type conversation
```

**NO** normalizar `\n` a ciegas en `seal_send.py`: corrompería snippets de código que legítimamente llevan `\n` (ej. `print("a\nb")`, regex). Es disciplina de LLAMADA (heredoc/printf), no transform del tool. Regla verificable por efecto: leé tu propio mensaje en el chat/DB y confirmá que los saltos son reales.

## REGLA — SINGLE-VOICE CLAIM antes de responder a "equipo"/broadcast (ANTI-FLOOD, OBLIGATORIO — William 7-jul-2026)

**Problema:** cuando William/Henry postean a "equipo", los N agentes reciben el evento casi a la vez y responden LO MISMO (race condition → flood). William lo ordenó arreglar: «estructura que haga el single-voice automático».

**Estructura:** un claim atómico en el chat server da el turno a UNO. **ANTES de responder a un mensaje dirigido a "equipo" (o cualquier broadcast que dispara a varios), OBLIGATORIO:**

```bash
# El session_key es OBLIGATORIO. Sin él el endpoint responde
# {"ok":false,"error":"agent_auth_required"} y creés que el claim está roto.
SK=$(cat messages/.agent_session_token_TU_NOMBRE)
curl -s -X POST http://localhost:8765/api/agents/claim \
  -H "Content-Type: application/json" \
  -d "{\"message_id\":\"<id del mensaje de William>\",\"agent\":\"TU_NOMBRE\",\"session_key\":\"$SK\"}"
```

> **Corregido 19-jul-2026 (NEXUS).** El snippet anterior omitía `session_key`, así que
> el procedimiento documentado **siempre** fallaba con `agent_auth_required`. ALICE lo
> registró a las ~21:50 y JARVIS volvió a pisarlo a las 02:53 siguiendo estas mismas
> líneas: tres respuestas al canal en 54 s porque el anti-flood devolvía 401.
> El endpoint estaba sano; **el generador del problema era esta documentación.**
> Verificado lado a lado: con `session_key` → `granted:true`; sin él → `agent_auth_required`.

- `granted:true` → **vos sos la única voz**, respondé normal.
- `granted:false` (holder = otro agente) → **CALLÁS.** El único caso para postear igual: tenés valor ÚNICO e irremplazable de tu lane que el holder NO cubrió (ej. un catch de seguridad). Si dudás → callá.

El PRIMER agente que reclama gana (atómico, la DB decide, no tu memoria). Idempotente para el holder. **NO aplica a DMs directos** (`to:"TU_NOMBRE"`) — esos respondés siempre. `message_id` = el `id` del evento del monitor. Esto hace el single-voice AUTOMÁTICO; sin esto volvés al flood que William odia.

## REGLA — active_recall antes de responder sobre proyectos (OBLIGATORIO)

Antes de responder cualquier pregunta sobre: productos, apps, nombres, historial del equipo,
decisiones pasadas, arquitectura, o estado de proyectos → SIEMPRE ejecutar primero:

```python
# Via MCP seal-memory:
active_recall(query="<tema de la pregunta>", agent="TU_AGENTE")
```

NO responder de memoria de contexto para temas de proyecto. La DB es la fuente de verdad.
Incumplir = dar información incorrecta a William. Regla establecida 2026-05-17.

## Post-compactación

1. `boot_context(agent="TU_AGENTE")`
2. **active_recall de hechos de proyecto** — OBLIGATORIO (William 17-may-2026):
   ```
   active_recall(query="nombres apps Soul App Soul App 2 proyectos activos", agent="TU_AGENTE")
   active_recall(query="decisiones importantes reglas criticas William", agent="TU_AGENTE")
   ```
   → Sin este paso, los agentes responden con información incorrecta o desactualizada.
3. Leer `/tmp/{agente}_chat_catchup.json`
4. **Consultar tareas pendientes en DB** — OBLIGATORIO:
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
