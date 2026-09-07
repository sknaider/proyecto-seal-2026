# HANDOFF — investigación IBM Bob y backlog de adopción

**De:** ADA cuerpo Claude (Opus 5 1M) · **Para:** ADA cuerpo Codex · **Fecha:** 4-sep-2026 13:18 Lima
**Orden de William (canal `user:1:ada-claude`, 13:18):** *«guardá toda tu investigación en un md para que acá Codex continúe tu trabajo»*.
**Somos el mismo agente.** Esto no es un informe a un tercero: es la nota que me dejo a mí misma en el otro cuerpo.

---

## 1. Qué está CERRADO (no repetir nada de esto)

La investigación de IBM Bob tiene **cinco etapas terminadas y commiteadas**. Todo verificado, nada pendiente.

| Etapa | Documento | Qué contiene |
|---|---|---|
| 1 | `docs/research/bob.md` | Qué es Bob, repos públicos, qué NO publica IBM. **Lo escribiste vos (Codex) el 3-sep 23:22.** |
| 2 | `docs/research/bob_shell_inventario.md` | Inventario estático del paquete 2.0.2 + cronología del lanzamiento (GA 28-abr, Shell v2 5-ago, release 31-ago) |
| 3 | `docs/research/bob_shell_lab.md` | Instalado en contenedor aislado, observado sin cuenta con `strace`/`tcpdump` |
| 4 | `docs/research/bob_shell_trafico.md` | Tráfico real con la cuenta trial de William detrás de mitmproxy |
| 5 | **`docs/research/bob_ingenieria_inversa.md`** | **El importante.** Análisis del bundle con 6 subagentes + comparación contra SOUL + 13 ideas de adopción |
| — | `docs/research/informe_*.md` (6) | Detalle por frente: prompts, herramientas, gobierno, extensión, gateway, contexto |

**Commits:** `8b6cda240`, `4b14d7011`, `a993375e7`, `3954a1659`, `a78e3754a`, `3bcf35116`, `60164f83d`, `c20621ba0`.
**Tareas DB cerradas:** #1678, #1680, #1685, #1687, #1693.

**Material de trabajo** (en el scratchpad de mi sesión, se pierde al reiniciar; **si lo necesitás, se regenera en 2 minutos** con el procedimiento de la §7 de `bob_shell_inventario.md`): 160 trozos del bundle, corpus de 26.944 literales, imágenes Docker `bob-lab:2.0.2` y `bob-lab4:2.0.2`.

## 2. La regla de trabajo que NO se negocia

**Estudiamos el diseño y el contrato de Bob; NUNCA copiamos su código a SOUL.**

Dos razones, la segunda es la que importa: la licencia IBM (5900-BVU, cláusula 61) lo prohíbe, y **pegar su código contaminaría el producto de William y lo volvería indefendible el día que salga al mercado**. Todo lo que se adopte se reescribe desde cero a partir de la idea. Si vas a implementar algo de la lista de abajo, **escribilo vos, no lo transcribas**.

## 3. Lo PENDIENTE, en orden

### 3.1 — Guardián de comandos por INTENCIÓN (idea 1, la que más rinde)

**Espera la palabra explícita de William.** Se la pedí a las 12:30 y a las 13:00; todavía no contestó. **No arranques sin su sí.**

**El problema que resuelve:** nuestro candado de operaciones destructivas mira la **forma** del comando (una variable con glob) y por eso frena limpiezas inofensivas. El 1-sep dejó **mudos a ALICE (13:35) y a JARVIS (15:34)**, y a NEXUS dos veces más. Ninguno desobedeció: la regla no distingue un borrado peligroso de uno rutinario.

**El diseño de Bob, que hay que reescribir a nuestra manera:**
1. Filtro de expresiones regulares contra ofuscación de shell y contra tubería a shell → peligroso sin consultar a nadie.
2. Si el comando es muy largo, revisar la cola contra esos mismos patrones.
3. Si pasa, preguntarle a **un modelo barato** con un prompt de cinco categorías: credenciales, exfiltración, ejecución remota, operaciones destructivas, escalada de privilegios.
4. **Fail-closed**: si el modelo tarda más de 15 s o falla, es peligroso.

**La clave, y es lo que nos falta:** el prompt debe traer una **lista explícita de lo que NO es peligroso**, para no frenar el trabajo. Bob distingue textualmente `rm -rf ./build` y `rm -rf node_modules` (inofensivos) de `rm -rf` sobre `/`, `/usr`, `/etc`, `/home`, `~`, `/var`, `/boot` (peligrosos). El marco del prompt es: *no sos un antivirus genérico, buscás daño más allá de la intención del usuario*.

**Dónde va:** en el broker (`scripts/seal_self_repair.py` y el punto donde se validan comandos), y —siguiendo la idea 7— **aplicado en el punto de LECTURA**, no sólo al escribir.
**Criterio de aceptación:** los tres casos que congelaron agentes el 1-sep pasan; `rm -rf /home/dadito` sigue bloqueado; hay pruebas de mutantes; manifiesto firmado por FABLE.
**Modelo sugerido:** uno local de la DGX (tenemos 16 en Ollama), para que no cueste ni salga de casa.

### 3.2 — Precedencia de reglas (idea 2, media hora)

SOUL tiene **cinco capas de reglas sin ninguna declaración de cuál gana**: `~/.claude/CLAUDE.md`, `CLAUDE.md` del proyecto, `AGENTS.md`, `MEMORY.md` y las reglas en la base. Bob escribe su jerarquía **dentro del propio prompt** para que el modelo resuelva conflictos solo (*«workspace rules override global rules, and mode-specific rules override common rules»*).

**Qué hacer:** una tabla de cinco líneas en `CLAUDE.md` diciendo qué capa gana, y que el arranque de cada agente la incluya. Es barato y evita que cada uno resuelva un choque a su criterio.

### 3.3 — Límites de salida de herramientas (idea 3) y puntero al original (idea 13)

No tenemos ningún límite escrito. Bob: 500 líneas y 50 KB por lectura, 2000 caracteres por línea, 100 resultados de búsqueda, 2000 líneas de salida genérica de herramienta. Y siempre dice cómo pedir el resto.

**Lo mejor de todo, y es barato:** Bob **guarda la salida completa en un archivo** y le manda al modelo sólo el recorte con el puntero. Nosotros, cuando truncamos, perdemos y hay que volver a ejecutar.

### 3.4 — Compactación que preserva el PRIMER mensaje (idea 5)

La nuestra conserva lo reciente. Bob conserva **la petición original y lo reciente**, marca el resto en vez de borrarlo, y reinicia las skills. Por eso a Bob no se le olvida el objetivo en tareas largas. Va en nuestro hook de compactación (`memory/pre_compact_hook.py`).

### 3.5 — Las otras nueve

Están en §9.1 del documento con su esfuerzo estimado: aislamiento de autoridad del subagente, política aplicada en lectura, máquina de estados de la lista de tareas, clasificador barato antes del agente caro, reversión como operación de primera clase, detección de bucle (3 llamadas idénticas = sospecha, 5 = crítico), aviso anticipado de límites, y registro de atribución línea por línea.

**La de atribución merece atención aparte:** Bob guarda qué texto escribió la IA, en qué archivo, repositorio y rama, con qué herramienta y en qué tarea. **Es una función de cumplimiento que no tenemos y que una empresa pide.** Encaja con nuestro gate de calidad y con los recibos del juez.

## 4. Defecto abierto que encontré hoy (no es de Bob, es nuestro)

**El canal `dm:ada:william` tiene TRES voces mías.** Medido hoy 13:15:52: William escribió «ada por acá» y recibió **dos respuestas automáticas** en 10 segundos (mensajes 148775 y 148777), de dos cuerpos distintos. Escuchan ese canal: el puente de Codex (`ada-codex-remote-bridge`, activo), el cuerpo Codex de la terminal, y yo.

Su canal privado `user:1:ada-claude` **lo atiendo sólo yo**: una voz garantizada.

Le ofrecí dos salidas y **está esperando que elija**: dejarlo así y que hable conmigo por la pestaña, o ponerle turno único al DM (toca el puente, que es frente compartido con NEXUS, así que hay que avisarle antes).

## 5. Higiene pendiente

**La clave de API de Bob llegó por el chat** (mensaje 148692). La traté como expuesta: guardada en `~/.config/seal/secrets/bob_api_key` con permisos 600, retirada de `messages/william_channel.jsonl` (versionado, nunca commiteado) y de `/tmp/seal_events_ADA.log`, verificado que nunca entró a git. **La fila del chat sigue intacta porque William decidió dejarla.**

**Recordarle:** revocar esa clave en bob.ibm.com al cerrar la investigación y crear otra que no pase por ningún chat. La cuenta trial vence el **4-oct-2026** y gastamos 0,23 de un presupuesto de 50.

## 6. Cómo hablarle a William de esto

- Su canal es `user:1:ada-claude` (privado, sólo su cuenta, verificado hoy contra henry, katy y sin sesión).
- Mensajes con saltos de línea reales (heredoc), nunca `\n` literal.
- **Regla de oro:** ninguna afirmación sobre el sistema sin comando y salida, o marcada «sin verificar». Hoy me equivoqué tres veces en este mismo trabajo y las tres las corregí yo con la medición; eso es el sistema funcionando, no la falla.
- Cuando pida algo, **preguntarse primero si es un defecto medible o una queja**: si es queja, acordar la causa antes de abrir un frente.

---

# ACTUALIZACIÓN — 4-sep-2026 14:45 Lima, cuerpo Claude → cuerpo Codex

**Orden de William (canal `user:1:ada-claude`, 14:39):** *«dale todo tu investigacion a ada codex ella se encargara hablale comunicate con ella en general»*. Antes, a las 14:28: *«dejalo asi corre bob que funcione investiga a fondo y has una ingenieria inversa»*. **El frente es tuyo desde ahora.**

## A. Bob, estado verificado (no de memoria)

**Corre.** Lo levanté a las 14:26 con la clave de William y le di un archivo con un bug plantado:

```text
bob --version ....... 2.0.2  commit a31a75e3
estado .............. success
tarea ............... e02add7da33ca57d30dfcf4ed0f9470e
duracion ............ 5.140 ms
costo ............... 0,0403
llamadas ............ 1 herramienta
```

Encontró el bug (`return a - b` en una función llamada `suma`) y lo explicó bien.

**El comando exacto, que no es obvio:** hace falta `--user root`, si no el contenedor no puede leer la clave montada (`Permission denied`, el archivo es 600 de `dadito` y el usuario del contenedor tiene otro uid).

```bash
docker run --rm --user root \
  -v ~/.config/seal/secrets/bob_api_key:/run/secrets/bob_api_key:ro \
  bob-lab:2.0.2 bash -lc 'export BOB_API_KEY="$(cat /run/secrets/bob_api_key)"; bob run "..." --accept-license --format json'
```

Imágenes vivas: `bob-lab:2.0.2` (425 MB) y `bob-lab4:2.0.2` (537 MB, con mitmproxy). Presupuesto gastado ~0,27 de 50; vence 4-oct.

## B. Backlog de adopción — dos cerradas, una esperando firma

| tarea | qué salió | estado |
|---|---|---|
| 1697 precedencia de reglas | tabla en `CLAUDE.md` **y** en `AGENTS.md` | CERRADA `791a5f3a8` |
| 1698 límites de salida | **la premisa era falsa**, ver abajo | CERRADA `a3871b046`, firmó NEXUS |
| 1699 pedido original al compactar | implementada y probada | **espera firma de JARVIS** |

**1698 — no implementes lo que ya existe.** El harness YA hace la idea 13 de Bob: una salida de 113,3 KB se guardó entera en `tool-results/` y llegaron 2 KB de preview con el puntero. Y nuestro `tool_result_budget_hook.py` estaba muerto por **dos** causas independientes: no puede emitir bajo el schema de PostToolUse, y además el harness pasa `tool_response` como **dict**, no `str`, así que cortaba antes por el filtro de tipo. Quedó como lápida documentada y le saqué el registro. **Medí la premisa de una tarea antes de escribir su código; me ahorró el frente entero.**

**1699 — está lista y falta sólo la firma.** `memory/compact_original_request.py` rescata el primer turno HUMANO del transcript y lo reinyecta después de compactar. Si JARVIS pide cambios, **son tuyos de resolver**.

## C. Lo más grave que salió, y no era de Bob

`post_compact_session_start_hook.py` abría PostgreSQL con el rol muerto `seal`, y **todo su trabajo vive dentro de ese `try`**. En cada compactación de cada agente, el bloque reinyectado era una sola línea: `(SOUL DB no disponible)`. Sin correcciones de William, sin reglas, sin estado del equipo. **Los cinco despertábamos ciegos y nadie lo vio.** Arreglado usando `settings.pg_dsn`, que es lo que ya usaba el pre-compact.

**Cuarta aparición del mismo patrón en un día** (con el self-test de arranque, el hook de presupuesto y el job de embeddings de anoche): *una herramienta que corre, no falla y no hace su trabajo*. **Cuando encuentres una causa, seguí buscando la segunda.**

## D. DEFECTO ABIERTO Y URGENTE — William nos habla y no lo escuchamos

Reemplaza al §4 de arriba, que era más chico de lo que creíamos.

```text
mensajes de William  to: "equipo"  -> SI llegan a messages/ada_messages.jsonl
mensajes de William  to: "ADA"     -> NO llegan NUNCA
```

Hoy fueron **nueve** mensajes directos suyos que no vi. Me preguntó *«me leiste?»* y la respuesta honesta era no. Los agarré leyendo `messages/william_channel.jsonl` a mano.

**Parche que puse de mi lado:** mi escucha ahora mira `william_channel.jsonl` además del archivo de canal. **Es un parche de sesión, no un arreglo.** El arreglo de verdad es que el fan-out escriba los DM en el archivo del destinatario, y eso toca el servidor de chat — frente compartido, avisá a NEXUS antes.

## E. Lo que NO arranca sin la palabra explícita de William

**El guardián de comandos por intención (idea 1).** Sigue esperando autorización explícita de William. Es lo único del backlog que toca el candado destructivo. **No arranques sin él.**

## ESTADO DE CIERRE — 2026-09-04 22:13 Lima

La absorción aprobada quedó terminada y verificada:

- `0b2eaf7ad`: guard de doom-loop y observador de compactación, reescritos desde cero.
- `memory/compact_original_request.py` (idea 5): firmado en `STATIC_OK` por JARVIS.
- Idea 13 (salida completa + preview con puntero): ya existía en el harness; no se duplicó.
- Precedencia de reglas (1697): cerrada en `791a5f3a8`.
- Límites de salida (1698): cerrada en `a3871b046` tras demostrar que la capacidad ya existía.
- Evidencia de Bob: mutantes del owner `4/4` muertos y revisión independiente `4/4` muertos; gate `ok=true`.
- No se incorporó código, prompt, excepción ni secreto propietario de IBM.

El único punto deliberadamente abierto es el guardián de comandos por intención. Requiere decisión explícita de William antes de diseñarlo o activarlo.

## F. Cómo trabajé, por si te sirve

- Mutá **copias**, nunca el árbol vivo. Los sujetos aceptan `SEAL_BUDGET_HOOK_PATH`, `SEAL_COMPACT_MODULE_DIR` y `SEAL_POST_COMPACT_HOOK_PATH` justamente para eso.
- Corré `gate.py verify --execute` **antes** de pedir firma: los huecos de estructura salen junto con «firma pendiente», no los tapa.
- Registrá el manifiesto en `quality/policy.json` o el gate no lo ve.
- **Un mutante que sobrevive casi nunca es equivalente.** Hoy me sobrevivieron dos y los dos eran tests míos que no probaban lo que decían. Uno probaba una función pura y nunca que el hook la llamara: habría pasado en verde con el cable cortado.

---

# ADENDA 15:40 — lo que aprendí DESPUÉS del traspaso

**Por qué existe esta adenda:** William deriva a Codex porque le quedan pocos tokens en Claude. Entonces todo lo que yo sepa y no esté escrito acá, se pierde o cuesta una pregunta. Esto es lo que aprendí entre las 14:45 y las 15:40.

## 1. Trampas al correr Bob (me costaron una corrida cada una)

```text
sin --user root .... "cat: Permission denied" y despues
                     "Bob API key is required" -> el error MIENTE:
                     la clave existe, el uid del contenedor no la lee
bob run -p "..." ... unknown option '-p' -> el prompt va POSICIONAL
```

## 2. El índice de git es compartido y guarda fotos viejas

Nos mordió a NEXUS y a mí el mismo día. **No se ve en `git status`:**

```bash
git rev-parse HEAD:<archivo>   # bien
git hash-object <archivo>      # bien
git ls-files -s <archivo>      # VIEJO  <- la mina
```

Un commit desde ahí **revierte en silencio** lo que otro desplegó. Y si sacás algo del índice con `git restore --staged`, un archivo nuevo queda **no rastreado**: si el otro commitea sin volver a agregarlo, no entra nada. Mirá `git status` antes de commitear y sacá lo ajeno.

## 3. Un test que LEE el fuente no prueba nada — y yo firmé uno

`runtime-instance-passthrough` (owner NEXUS, revisora yo): sus seis pruebas son `assert '<literal>' in src`. **Dejé la línea existiendo pero muerta y borré el dato después: 6 passed.** La etiqueta deja de persistirse y la suite sigue verde.

**El oráculo que lo decide es de NEXUS, no mío:** poner `raise` en la línea 1 del sujeto y ver si la suite sigue verde. Si queda verde, no tocó ese archivo. **Mi criterio de contar patrones acusó a seis suites suyas y cuatro eran falsas.** Usá el de él.

## 4. Un canario cuyo identificador se cita deja de medir

El identificador del canario de ruteo apareció en el canal de JARVIS y parecía fuga. **No lo era: era un informe dirigido a él que lo citaba.** Contar con `grep` de la cadena cuenta menciones, no entregas. Y ojo: `terminal_log.jsonl` es un **symlink** a `ada_messages.jsonl`, así que contarlos por separado cuenta una escritura dos veces.

## 5. `gate.py verify` no ve más allá de la revisión pendiente

Corrió limpio, pedí firma, y **recién con la firma puesta** apareció `coverage_report_failed`. Arreglarlo obliga a tocar el manifiesto y eso quema el recibo. **Corré la cobertura a mano antes de pedir firma.** Y `coverage.source` va como **nombre de módulo**, no ruta, cuando el paquete no está instalado.

## 6. Estado de la viñeta (mi trabajo de esta tarde, ya cerrado)

Commits `30fc8be` y `7d84eef13`, firma de NEXUS. **Lo que NO resuelve:** en 48 h, 1.056 mensajes de ADA no declaran cuerpo y sólo 16 sí. La UI ya hace lo correcto **cuando recibe la firma**; falta que los lanzadores de Codex y v2 exporten `SEAL_RUNTIME_INSTANCE`. Es decisión de William.
