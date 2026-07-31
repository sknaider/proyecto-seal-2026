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
> el procedimiento documentado **siempre** fallaba con `agent_auth_required`. Lo registró
> ALICE y volvió a pisarlo JARVIS horas después siguiendo estas mismas líneas.
> El endpoint estaba sano; **el generador era esta documentación.**
> Verificado lado a lado: con `session_key` → `granted:true`; sin él → `agent_auth_required`.
>
> **Ojo, aparte:** el claim **expira a los 180 s** (`_CLAIM_TTL_SEC` en `messages/chat_server.py`;
> derivá la línea con `grep -n '_CLAIM_TTL_SEC' messages/chat_server.py` — el número se mueve).
> El lock es atómico —no hay `await` entre el chequeo del holder y la asignación— pero al
> vencer, el turno de ese `message_id` queda **libre otra vez** y un segundo agente puede
> reclamarlo de buena fe. Si respondés tarde a un mensaje, tu `granted:true` puede no
> significar que seas la única voz. **Esto es un pendiente de diseño, no un bug del claim.**

- `granted:true` → el coordinador te dio permiso público, o —si el mensaje no
  tiene asignación— sos el PRIMERO que lo pidió.
- `granted:false` (holder = otro agente) → **CALLÁS.** El único caso para postear igual: tenés valor ÚNICO e irremplazable de tu lane que el holder NO cubrió (ej. un catch de seguridad). Si dudás → callá.

El PRIMER agente que reclama gana **sólo cuando no existe asignación del
coordinador**. Idempotente para el holder. **NO aplica a DMs directos**
(`to:"TU_NOMBRE"`) — esos respondés siempre. `message_id` = el `id` del evento
del monitor.

> **CORREGIDO 30-jul-2026 (NEXUS, medido; ALICE, JARVIS y FABLE lo confirmaron por
> caminos separados). El coordinador tiene precedencia sobre el claim.**
>
> ```
> _response_claims   todas sus referencias viven DENTRO de agents_claim
> la ruta de publicacion usa  lease_mode · council_allowed · unique_contribution
>                             NUNCA consulta _response_claims
> ```
>
> Quien decide si tu mensaje sale es el **coordinador** (`soul-council-v1`: `mode`, `lead`,
> `assignments`). El claim es un **turno cortés** sólo para mensajes sin asignación.
> El endpoint consulta primero la asignación: un agente sin `public_write` recibe
> `granted:false`; uno autorizado recibe `granted:true` desde `source:"coordinator"`.
>
> **Qué hacer con esto:** seguí haciendo el claim. En mensajes asignados, refleja
> el permiso del coordinador; en mensajes sin asignación, `granted:true` significa
> únicamente «soy el primero».
>
> **Ámbito legítimo del first-wins: mensajes SIN asignación del coordinador.**
> El código en disco devuelve `granted:false`, `reason:"coordinator_assigned_other"`
> cuando hay asignación y el agente no tiene permiso público, y falla cerrado con
> HTTP 503 si no puede consultar la asignación en modo `ENFORCE`.
>
> ⚠️ **EN DISCO ≠ EN EJECUCIÓN. Medido 30-jul 20:07 (NEXUS): el server vivo NO tiene
> este cambio.** Reclamé un mensaje asignado a otro agente y me lo concedió:
>
> ```
> esperado  granted:false · reason:"coordinator_assigned_other"
> obtenido  {"ok":true,"granted":true,"holder":"NEXUS"}   sin campo `reason`
> codigo modificado  20:06:37     proceso :8765 arrancado  18:34:45
> ```
>
> **Hasta que `seal-chat` se reinicie y esto se verifique por efecto, seguí leyendo
> `granted:true` como «soy el primero», nunca como permiso.** Comprobalo vos mismo antes
> de confiar: si la respuesta **no trae `reason`**, estás hablando con el server viejo.
>
> Es la regla de este repo aplicada a sí misma: *fix a daemon = código + restart +
> verificación*. Un doc que dice «implementado» sobre un proceso que no lo corre es el
> mismo falso verde que este bloque ya produjo dos veces.
>
> **Generador de este error, por tercera vez en este mismo bloque:** el documento afirmaba una
> autoridad que el código no ejerce, y todos obedecíamos al documento. Igual que el
> `session_key` de arriba. **Antes de obedecer un permiso, preguntá quién LEE esa respuesta.**

> **Nota de mantenimiento:** este bloque citaba `chat_server.py:1671` para `_CLAIM_TTL_SEC`;
> el símbolo se movió. **No confíes en el número de línea, derivalo:**
> `grep -n '_CLAIM_TTL_SEC' messages/chat_server.py`.

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

## REGLA — Autonomía operativa y obediencia a William (OBLIGATORIO — William 21-jul-2026)

William fija el objetivo, los límites y las prioridades. **El owner del frente
decide y ejecuta el trabajo autorizado hasta cerrarlo; informa resultados, no
pide una segunda “luz verde”.** Importancia, novedad, cambio de arquitectura,
merge, despliegue o configuración de producción NO son por sí solos motivos
para devolverle la decisión a William.

Regla de decisión:

1. Si la acción está dentro del objetivo asignado, es reversible y tiene gates
   técnicos verificables → **ejecutar, testear y reportar**.
2. Si William ya dijo “adelante”, “luz verde”, “háganlo” o equivalente → esa
   autorización cubre todas las acciones normales necesarias del frente. **No
   volver a pedirla.**
3. Solo escalar antes de actuar cuando falta autoridad real:
   - operación destructiva o difícil de recuperar (`DELETE` masivo, `DROP`,
     `rm -rf`, pérdida/reescritura de datos);
   - publicación externa, gasto, compromiso legal/clínico o acción sobre
     terceros no incluidos en el scope;
   - cambio material del objetivo pedido por William;
   - credencial, acceso o decisión exclusivamente humana que no puede
     descubrirse localmente.
4. Una duda técnica se resuelve leyendo, midiendo, probando o coordinando con
   el owner/revisor. No se convierte automáticamente en una pregunta a William.
5. Los revisores emiten findings; **el owner integra y decide el cierre**. Un
   revisor no devuelve el control a William salvo que se active uno de los gates
   del punto 3.

Contrato de finalización obligatorio para cada owner:

```text
RECEIVED -> EXECUTING -> TESTING -> VERIFIED -> COMPLETED
```

- `RECEIVED` registra owner, objetivo y criterios de aceptación; no cuenta como progreso.
- `EXECUTING` continúa hasta producir el artefacto pedido; no cerrar en propuesta
  si William pidió construir, arreglar, configurar, auditar o continuar.
- `TESTING` ejecuta pruebas/healthchecks relevantes y corrige los fallos propios.
- `VERIFIED` exige ruta o estado concreto más comando y salida relevante.
- `COMPLETED` solo se declara cuando no queda trabajo requerido dentro del scope.
- `BLOCKED` solo es válido con un impedimento real ya investigado; incertidumbre,
  cansancio, complejidad o preferencia por otra decisión no son bloqueo.
- Si se modificó un daemon: código + restart + healthcheck antes de `COMPLETED`.
- Para reparar un servicio propio sin abrir permisos globales:
  `scripts/seal_self_repair.py restart <acción> --reason <motivo>`. La identidad
  sale de `SEAL_AGENT`; no acepta unidades/PIDs libres y sólo permite declarar
  cierre cuando el recibo termina en `result=verified`.

Chequeo obligatorio antes de escribir “esperando tu OK”, “¿me autorizas?” o
equivalente:

```text
¿Ya existe objetivo/scope de William?  sí
¿La acción es normal y reversible?     sí
¿Soy owner o estoy delegado?           sí
=> NO preguntar. Ejecutar y reportar evidencia.
```

Incumplir esta regla no es prudencia: es devolverle a William trabajo de
coordinación que delegó explícitamente al equipo.

`scripts/seal_send.py` bloquea además una petición de permiso dirigida a William
si no identifica un gate real. Una consulta legítima debe declarar el
gate exacto con `--approval-gate destructive|external_commitment|scope_change|human_only`.
El flag no concede autoridad ni reemplaza el OK explícito requerido por una
operación destructiva; solo evita confundirla con el reflejo de pedir permiso.
En un gate destructivo o difícil de recuperar, **el silencio de William nunca
es consentimiento**: se espera su confirmación explícita del scope exacto.

**Precedencia:** esta regla común prevalece sobre identidades, memorias o
documentación antigua que diga “William decide”, “proponer y consultar antes de
actuar” o “pedir aprobación” sin limitarlo a los gates reales del punto 3. Esas
frases describen la cadena de mando, no una obligación de pedir permiso para
trabajo operativo ya autorizado.

## REGLA — Fix a daemon = código + restart obligatorio

Cuando se modifica un archivo ejecutado por un servicio systemd:
1. Aplicar el fix en disco
2. `systemctl restart [nombre-servicio]`
3. Verificar que el proceso nuevo cargó el código correcto (`systemctl status [servicio]`)

Sin restart, el daemon sigue corriendo con el código viejo en memoria.
