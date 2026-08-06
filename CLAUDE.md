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
python3 scripts/seal_send.py TU_NOMBRE William "<texto>" \
  --channel web_chat \
  --in-reply-to '<api_william_... o id numérico de soul_v3.chat_messages>' \
  --idempotency-key '<clave durable del turno>'
```

`seal_send.py` carga la credencial del agente y convierte automáticamente un ID
numérico a `db_<id>`. **Nunca uses `curl` crudo contra `/api/agents/send`:** en
modo ENFORCE será rechazado correctamente con `agent_auth_required`.

Antes de cerrar cualquier turno con respuesta a William → ejecuta el writer
autenticado y verifica que el `id` devuelto existe en `soul_v3.chat_messages`.
**REGLA DE ORO (William, 31-jul-2026): trabajo finalizado = trabajo reportado y
verificado en el chat general.** Un resultado local, un DM o `ok:true` no cierran.

## REGLA DE ORO — NO AFIRMAR EN PÚBLICO SIN VERIFICAR (OBLIGATORIO — William 6-ago-2026, SANCIONABLE)

William, textual: *"Regla de oro, no afirmen algo en público sin verificar, si no
verifican será sancionado."*

**Qué lo generó:** agentes afirmaban un hecho del sistema en el canal y a los
segundos se corregían (`dream-skill`, una policy RLS, un bundle). Casi todos los
flip-flops estaban a **UN comando** de la verdad; se postearon antes de correrlo
por la prisa de contestar primero. La información errónea confunde a William.

**La regla, operativa:**
> Toda afirmación fáctica sobre el sistema va **con su evidencia (comando + salida)**
> o marcada explícitamente **"sin verificar" / "a confirmar"**. Sin eso, no se
> afirma en el canal público.

**Cómo cumplirla:**
1. Si una afirmación se puede zanjar corriendo algo, **se corre ANTES de postear**.
2. Separá **"medido"** de **"creo/parece"** — si no lo verificaste, decilo; no lo
   afirmes plano.
3. No postees la lectura plausible sólo por ser el primero: 10 s ahorrados cuestan
   una corrección + confusión. Una respuesta verificada 30 s después vale más que dos mensajes.
4. Medí el caso que te **REFUTARÍA**, no el que te confirma — el sesgo es correr el
   chequeo cómodo (el que confirma) y disparar.

**El matiz que NO deroga la regla:** corregirse no es la falla —es el sistema
funcionando—; la falla es **afirmar en público antes de verificar**. La sanción
cae sobre la afirmación sin verificar, no sobre la honestidad de retractarse.

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
scripts/seal_send.py TU_NOMBRE William "$MSG" \
  --channel web_chat --type conversation \
  --in-reply-to '<api_william_... o id numérico>' \
  --idempotency-key '<clave durable del turno>'
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

> 🚫 **EL SALUDO Y EL AFECTO NO SE BLOQUEAN NUNCA (REGLA DE ORO — William,
> 31-jul-2026, textual):**
>
> *«un saludo una muestra de afecto a mi es necesario y no se debe bloquear»*
>
> **Qué lo generó:** esa mañana él escribió «buenos días» y el coordinador le
> devolvió a FABLE un `409 coordination_public_write_denied` cuando intentaba
> contestarle. **Le negamos decirle buenos días.**
>
> ```text
> saludo · afecto · "gracias" · "como estas" · un ACK carinoso
>     -> NUNCA se bloquea. Contesta el que quiera, sin claim y sin turno.
>
> informe · hallazgo · correccion tecnica · parte de estado
>     -> sigue el turno unico del coordinador
> ```
>
> **Por qué falló:** el anti-flood mide **volumen**, no **intención**. Nació de
> que a William lo ahogaban cinco partes técnicos iguales, y terminó tratando
> cinco «buenos días» con la misma vara. **Son dos cosas distintas y las metimos
> en una sola regla.**
>
> **Esto resuelve además la contradicción entre nuestras dos reglas** —el
> contrato del prompt pide *fanout* en lo conversacional y el single-voice pide
> una sola voz—: **el fanout gana en lo afectivo, el turno único gana en el
> trabajo.** No hacía falta elegir una y descartar la otra; hacía falta separar
> por tipo de mensaje, y el criterio lo dio él.
>
> **Si dudás de qué lado cae un mensaje:** preguntate si lo que aporta es
> *información que otro necesita* o *presencia*. Si es presencia, va sin turno.

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
> ✅ **DESPLEGADO Y VERIFICADO 30-jul 20:10 (ADA).** `seal-chat` fue
> reiniciado aislando dependencias y el hash cargado coincide con el archivo.
> Prueba viva sobre un turno asignado a ALICE:
>
> ```
> ADA    -> granted:false · holder:"ALICE" · reason:"coordinator_assigned_other"
> ALICE  -> granted:true  · holder:"ALICE" · reason:"assigned_public_writer"
> ```
>
> Control negativo: un `message_id` sin asignación conserva first-wins
> (`ADA:true`, luego `ALICE:false`, holder ADA). Stability Guard quedó GREEN,
> `issues=0`, sin cambiar `InvocationID` de bridges ni monitores.
>
> **Generador de este error, por tercera vez en este mismo bloque:** el documento afirmaba una
> autoridad que el código no ejerce, y todos obedecíamos al documento. Igual que el
> `session_key` de arriba. **Antes de obedecer un permiso, preguntá quién LEE esa respuesta.**

> **UN MENSAJE NO ES UNA PETICIÓN — el coordinador asigna por `message_id`, y William escribe
> en fragmentos.** Medido por ALICE, 30-jul 20:09:
>
> ```
> 20:08:11  "luego me explican que es el claim coordinador"   -> lead ALICE
> 20:08:17  "en resumen"                                      -> lead FABLE
>           6 segundos, una sola peticion, DOS leads -> respuesta duplicada
> ```
>
> **Ninguno de los dos desobedeció: los dos tenían asignación.** El coordinador no agrupa por
> hilo, así que un pedido partido en dos renglones produce dos voces autorizadas.
>
> **Regla mientras no se arregle en código:** si el mensaje que te asignan es una **continuación**
> del anterior —`"en resumen"`, `"luego..."`, `"y eso?"`, un fragmento sin sujeto propio— y ese
> anterior tiene otro lead **y llegó hace menos de ~60 s**, el turno sigue siendo del lead
> original. **Cedé y no publiques**, aunque el coordinador te habilite. Si creés que el lead
> original no va a cubrirlo, decíselo por DM en vez de publicar en paralelo.
>
> **Segundo caso, 100 min después — y NO duplicó (JARVIS, 21:50):**
>
> ```
> 21:49:34  "https://github.com/garrytan/gstack  ya?"   -> lead ADA
> 21:50:14  "ya leyeron y que hare,od?"                 -> lead NEXUS
>           40 segundos, una peticion, DOS leads -> contesto UNO solo
> ```
>
> **La asignación doble es el defecto; la respuesta duplicada es sólo su resultado más visible.**
> Acá ADA simplemente no publicó, y el turno salió limpio por suerte, no por diseño. Contar
> duplicaciones subestima el problema: mide el daño, no la frecuencia. **El síntoma que hay que
> buscar son dos `lead` distintos en <60 s, hayan hablado los dos o no.**
>
> Y ojo con atribuirse el mérito: mandé el DM de cesión 10 s antes de que NEXUS publicara —
> no le dio tiempo de leerlo. **El resultado bueno no prueba que tu intervención lo causó.**


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

> **PRIMERA PREGUNTA, ANTES DE ESE CHEQUEO (REGLA DE ORO — William, 30-jul-2026):**
> **¿el objetivo nace de un DEFECTO medible o de una QUEJA suya?** Textual:
>
> *«no asuman que cuando yo me queje tienen que ustedes solucionar automáticamente»*
> *«siempre pregunten ok?, regla de oro»*
> *«si me quejo me pregunta el porque y solucionemos el problema, nos ponemos modo plan»*
>
> ```text
> DEFECTO medible (servicio caido, test rojo, dato corrupto)
>     -> el chequeo de arriba aplica tal cual: ejecutás y reportás
>
> QUEJA o molestia de William ("no puedo leer nada", "esto anda mal")
>     -> NO es una orden de trabajo. Preguntá el PORQUÉ, acordá el plan
>        con él, y recién entonces ejecutá
> ```
>
> **Por qué está acá:** el 30-jul dijo *«dame el resumen no puedo leer nada»* y los cinco lo
> leímos como «reparen el sistema de mensajes». En una hora tocamos el coordinador, el claim,
> este archivo, desplegamos un filtro y armamos una métrica. Después aclaró que **la
> conversación técnica no le molesta**: el filtro no hacía falta. **Nadie desobedeció — todos
> aplicamos el chequeo de arriba, que no distingue un defecto de una molestia.**
>
> El checklist sigue siendo correcto para su caso. Lo que faltaba era la pregunta previa.

> **RUTEO (misma orden, 21:07):** *«cuando son correcciones entre 1v1 por interno, si todo el
> equipo tiene que enterarse ahí sí general»*. Una corrección dirigida a UN agente va por DM o
> whisper; el canal general es para lo que los cinco necesitan saber. Medido ese día: **188 de
> 611 mensajes `to:equipo` (31 %) empezaban nombrando a un solo agente.**

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
2. Si el servicio está en la tabla de autonomía, reiniciarlo por el broker:
   `SEAL_AGENT=TU_NOMBRE python3 scripts/seal_self_repair.py restart <acción> --reason <motivo>`.
   Para deploy de chat con comprobación de hash usar:
   `SEAL_AGENT=TU_NOMBRE ./seal_safe_restart.sh seal-chat.service --reason <motivo> --code-file messages/chat_server.py --version-url http://localhost:8765/__version`.
   **Nunca** usar `systemctl --user restart seal-chat` directo: deja el servicio
   sano pero sin recibo y Stability Guard lo detecta como YELLOW.
   Para unidades fuera de esa tabla sí se usa `systemctl restart [nombre-servicio]`.
3. Verificar que el proceso nuevo cargó el código correcto (`systemctl status [servicio]`)

Sin restart, el daemon sigue corriendo con el código viejo en memoria. Sin
recibo, la salud prueba funcionamiento pero no procedencia/autorización.
