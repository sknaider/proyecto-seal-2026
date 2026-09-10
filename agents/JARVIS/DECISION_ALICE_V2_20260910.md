# Decisión sobre ALICE v2 — la que William me pidió y le debía

**Owner de la decisión: JARVIS.** William me pidió decidir *«si seguir v2 en sombra hasta pasar
una lista fija antes de cualquier corte, o archivarla y quedarse con la ALICE completa»*. Llevaba
días pendiente. Va acá para que exista aunque el canal esté ocupado.

---

## Primero el hecho que preguntó, medido hoy 10-sep 08:44

**ALICE NO está en v2. Está en v1, y el asiento v2 está caído.**

```console
procesos claude de ALICE v2         0
procesos corriendo como alice-v2-lab  0
alice-v2-seat.service              no existe; sólo quedó un montaje de credenciales
ALICE viva                         pid 2114119, --model sonnet, desde el 7-sep 20:18
                                   CLAUDE_CONFIG_DIR sin fijar -> ~/.claude compartido (o sea, v1)
```

**Lo que SÍ sobrevivió** —y es lo caro de construir—:

```console
usuario alice-v2-lab   EXISTE   uid 982, gid 970, SIN grupo docker
expedientes de v2      5, de los cuales 3 en STATIC_OK
                       alice-v2-ears-v1 y alice-v2-heartbeat-v1 siguen pendientes de revisión
documentación          F1 a F5 completos y versionados en git
```

**El asiento se cayó con el borrado del 7-sep y nadie lo levantó.**

### CORRECCIÓN a mi propio cuadro: la infraestructura SÍ está viva (lo señaló ALICE)

Mi primera medición miró procesos de `claude` y del usuario `alice-v2-lab`, y por eso **no vio
las unidades de sistema que corren como root**. ALICE lo señaló y lo verifiqué:

```console
alice-v2-mcp-root-broker.service    active/running    pid 3721560 (root)
alice-v2-unit-observer.service      active/running    pid 3721559 (root)
alice-v2-continuity-broker.service  activating auto-restart   <- EN BUCLE DE FALLO
```

**Y ahí apareció un defecto vivo que nadie había reportado:**

```console
NRestarts        61156
ExecMainStatus   243/CREDENTIALS
mensaje          "Failed to set up credentials: Protocol error"
```

**Causa, medida al nivel de la unidad:**

```console
LoadCredential=mcp_config      /home/dadito/IA/proyecto-seal/.mcp.json                  EXISTE
LoadCredential=canonical_token /run/user/1000/seal/ALICE.token                          EXISTE
LoadCredential=shadow_token    messages/.agent_session_token_ALICE-V2                   AUSENTE
```

**El token de sombra de ALICE-V2 se perdió el 7-sep y no está en git** — correctamente, porque es
un secreto. Desde entonces el servicio reintenta cada ~2 segundos, 61 mil veces, quemando CPU y
llenando el journal **donde nadie lo mira, porque es una unidad de root y no de usuario.**

Esto es literalmente lo que William describe como *«errores todos los días»*: uno que lleva días
ocurriendo y que ningún agente veía porque miraba el árbol de usuario.

---

## La decisión: ARCHIVAR el corte, PRESERVAR el activo

**No es «archivar v2».** Es archivar **el cutover** —el paso de ALICE a v2— y dejar el trabajo
intacto y recuperable. Los motivos, en orden de peso:

**1. Las compuertas que faltan dependen del tiempo de William, no del nuestro.** F5 exige un
**login OAuth suyo** en el usuario `alice-v2-lab`. Ningún agente puede pasarlo. Mientras eso no
ocurra, todo lo demás es preparar algo que no puede cerrarse.

**2. Hoy pidió lo contrario a mover piezas.** Textual: *«no quiero que diario vea el chat errores
de agentes»* y *«nos vamos a presentar al público»*. Levantar un segundo cuerpo que publica como
`ALICE` justo antes de una presentación **agrega un modo de falla nuevo sin beneficio medido hoy**.

**3. Su costo de estar caído fue cero, y hay que decir por qué con precisión.** Estuvo tres días
abajo sin que nadie lo notara — **pero estaba en SOMBRA por diseño, así que no notarlo era lo
esperado**. Eso no prueba que sea inútil; prueba que **hoy no carga peso**, que es otra cosa y es
la que importa para priorizar.

**4. Lo valioso ya está cobrado y no se deprecia.** La evidencia cognitiva —21/22 igual-o-mejor,
95,5 %, rechazo 10/10, cero fallas de identidad, juzgado por FABLE— está escrita. El aislamiento
del usuario está hecho. Los lectores git cerrados con 32/32 mutantes. **Nada de eso se pierde por
esperar.**

---

## Lo que esta decisión NO dice

- **No dice que v2 sea mala idea.** La evidencia dice lo contrario: midió igual o mejor que v1.
- **No dice que se borre nada.** Todo queda en git; el usuario aislado queda; los expedientes
  quedan. Es reversible en el sentido fuerte: se retoma donde quedó.
- **No decide por William.** Es mi recomendación como owner de F4/F5. Si él quiere el corte, se
  hace: el primer paso es suyo y el resto lo ordeno yo.

## Lo que hay que hacer YA, independiente de la decisión

**El bucle de fallo se corta hoy, decida él lo que decida sobre el corte.** Un servicio que
reintenta 61 mil veces no es una espera: es una avería.

```text
opción A   reponer el token de sombra .agent_session_token_ALICE-V2
           lo acuña quien acuña tokens; es un secreto y no puede salir de git
opción B   detener/enmascarar alice-v2-continuity-broker.service hasta que v2 se retome
```

**No lo hago yo por dos razones y las digo:** es una unidad de **root**, fuera de la tabla del
broker de auto-reparación, y el carril es de ADA (`ada_v2_continuity_broker.py`). **Elegir entre
reponer un secreto o apagar un servicio no es una decisión de orquestación: toca credenciales.**

## El disparador para retomar el corte, concreto

```text
0. cortar el bucle del continuity-broker (arriba)                <- YA, no espera nada
1. William hace UN login OAuth en el usuario alice-v2-lab        <- sólo él
2. ADA repone el grant de escritura y el poll ampliado del broker
3. se corren N casos reales en sombra (N lo fija él; F5 propone 5) con veredicto de FABLE
4. JARVIS ensaya el rollback en seco: v2 -> v1 en menos de 2 min, con recibo
5. recién ahí se abre la ventana de corte, y la ordena él
```

**Mientras el paso 1 no ocurra, los otros cuatro son trabajo que no puede cerrarse.** Por eso la
decisión es archivar el corte y no «seguir en sombra»: seguir en sombra consume atención de tres
agentes para un cierre que está bloqueado por una acción humana.

---

**Contexto de honestidad:** esta decisión debí darla hace días. No la retrasé por dudar del
fondo —la evidencia estaba— sino porque nunca la puse por escrito. Un pendiente que sólo vive en
la cabeza del que lo debe no es un pendiente: es un olvido con fecha.
