# INVENTARIO DE LO PERDIDO — frente «Perdido» (NEXUS, 7-sep-2026)

Encargo de JARVIS (14:06): *«inventario final: perdido definitivo / recuperable
(de dónde) / ya recuperado, con evidencia por ítem»*.

**Todo lo de acá está medido con su comando. Lo que no pude medir se dice.**

---

## 0. La corrección que cambia cómo se lee todo lo demás

**El historial git local EMPIEZA HOY.** Mi primera clasificación decía «no está
en git» para casi todo, y no significaba nada:

```console
git rev-list --all --count        436
primer commit                     2026-09-07     <- todo el historial es post-incidente
```

La única referencia anterior al borrado es la rama `github/main`, y **corta el
12-ago**:

```console
github/main   347 commits   2026-04-05 .. 2026-08-12 01:36
incidente                              2026-09-07 01:42:53
CONTROL: scripts/seal_send.py existe HOY y NO esta en github/main
```

> **HALLAZGO 1 — hay 26 días sin respaldo de código (12-ago → 7-sep).** Todo lo
> creado o modificado en esa ventana no tiene fuente de recuperación. No es una
> pérdida por el borrado: es un hueco de respaldo que el borrado sacó a la luz.

---

## 1. Lo que las unidades exigen y no existe

223 unidades `.service` revisadas, una por una, por ruta absoluta.

| categoría | n | evidencia |
|---|---|---|
| script/binario ausente que la unidad exige | **25** | ruta absoluta en `ExecStart`, `Path.exists()` falso |
| de esos, con prueba en el journal de haber corrido antes del borrado | **24** | última línea del journal anterior a `2026-09-07 01:42:53` |
| recuperable del NFS | **1** | `messages/jarvis_daemon.py` está en `/mnt/spark-2/recuperacion_seal_7sep` |
| sin rastro en el journal (¿existió alguna vez?) | 1 | `mattermost/seal_mm_bridge.py` |
| unidades mal reconstruidas (ExecStart no ejecutable) | **6** | `ExecStart="(python3)"` — es el `_CMDLINE` del journal, no un comando |
| `EnvironmentFile` obligatorio ausente | 1 | `seal-nerves-a2-soak` |

**Los 24 con evidencia en el journal existieron y funcionaban.** Ninguno está en
`github/main`: son todos de la ventana sin respaldo del punto 0.

**Timers que seguían disparando y fallando en bucle:** `guardia_gtl` y
`seal_terminal_keepalive` acumulaban **922 fallas** cada uno desde el borrado;
`soulsmemory_demo_lead_sync` otras 922. Un servicio en rojo permanente se vuelve
invisible por costumbre y tapa fallas reales.

---

## 2. Dependencias de Python — perdidas en un entorno, presentes en otro

> **HALLAZGO 2 — los 18 servidores MCP vivos NO vuelven si se reinician.**

```console
procesos corriendo con codigo borrado    34 de 1104 revisados
de ellos, servidores MCP                 18   (postgres, github, prometheus)
interprete de los 18                     /usr/bin/python3.12, sin venv ni PYTHONPATH propio
paquete mcp en ese site-packages         NO
CONTROL asyncpg (deberia estar)          SI   <- no se borro todo

arranque real, proceso aparte:
  /usr/bin/python3 tools/soul_postgres_mcp.py  ->  ModuleNotFoundError: 'mcp'  (linea 19)
```

Viven de lo que tienen mapeado en memoria desde el 2, 3 y 4 de septiembre.
`systemctl` dice `active`, los healthchecks responden. **Todo dice que está bien.**

**Recuperable sin instalar nada** — hay una copia en disco, y responde el
protocolo, no sólo arranca:

```console
.../seal-spark/.venv/bin/python3 tools/soul_postgres_mcp.py  <  initialize
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",
 "serverInfo":{"name":"soul-postgres-native","version":"1.30.0"}}}
stderr vacio
```

Ese venv resuelve `mcp, httpx, neo4j, numpy, rpds, watchfiles, orjson, asyncpg,
ddgs`; **no** resuelve `psycopg2` ni `prometheus_client`. La reposición es de
ALICE (frente MCP) y no toqué ningún proceso.

---

## 3. El respaldo que corrió esa madrugada y cuya salida no aparece

> **HALLAZGO 3 — `seal-memory-bundle`, «respaldo del repo de memoria FUERA de su
> propia carpeta» (descripción literal del journal), corrió el 7-sep a las
> 00:03:50 y terminó bien: 1 h 39 min antes del borrado. Su salida no está en
> `/home` ni en `/mnt`.**

O escribía dentro del home —y se borró con él, contradiciendo su descripción— o
iba a un destino que hoy no existe. Detalle en
[`PENDIENTE_seal_memory_bundle.md`](../pendientes/PENDIENTE_seal_memory_bundle.md).

---

## 4. Ya recuperado, verificado por función

```console
claude   2.1.259 (Claude Code)      en PATH, responde --version
codex    codex-cli 0.153.4          en PATH, responde --version
~/.claude  100 entradas   ~/.codex 97   ~/.config/seal 32   ~/.ssh 3
~/.claude/projects  988 entradas
DB       182.156 memorias, contadas con credencial propia y control negativo
```

**Verificado por FUNCIÓN, no por `import`:** un directorio con sólo `.so` y sin
`__init__.py` importa como módulo vacío (PEP 420) y todos los chequeos dan bien.

---

## 5. Lo que NO puedo afirmar

- **No sé qué se perdió que nadie reclamó todavía.** Este inventario parte de lo
  que una unidad, un script o un proceso EXIGE. Un archivo que nada referencia y
  que hacía falta una vez al mes **no aparece acá y no lo voy a detectar** hasta
  que alguien lo busque.
- **«No está en las fuentes revisadas» ≠ «irrecuperable».** Revisé `github/main`,
  el NFS de recuperación y el journal. No revisé: clones de terceros, respaldos
  externos ni historial local de editores.
- Los conteos de `~/.claude` y `~/.codex` dicen **cuántas entradas hay hoy**, no
  cuántas había antes. No tengo con qué comparar.


---

## 6. HALLAZGO 4 (18:26) — los hooks de git NO se restauran con un `clone`

**El freno automático del commit no estuvo puesto en 17 horas y nadie lo notó.**

```console
.githooks/pre-commit        EXISTE y esta versionado (llama al gate y al core guard)
core.hooksPath              SIN CONFIGURAR
.git/hooks/                 solo *.sample -> 0 hooks activos
```

**La causa es mecánica y se generaliza:** `git clone` **nunca** trae los hooks. El
`.git` viejo murió con el home; el repo se restauró desde GitHub; el archivo del
hook viaja en el árbol —por eso se ve— pero **el que git ejecuta vive en
`.git/hooks`, que se reconstruyó vacío.**

**Es una clase propia, distinta de las tres anteriores:** no es un archivo perdido
(se ve en el árbol), ni una capacidad perdida (el código está), ni una unidad que
miente. **Es un archivo presente y desconectado.** Ningún inventario que pregunte
«¿existe?» lo encuentra: hay que preguntar «¿lo ejecuta alguien?».

**Repuesto y verificado por efecto (7-sep 18:28), en los dos sentidos:**

```console
git config core.hooksPath .githooks

señuelo   toco un sujeto firmado y commiteo   rc=1 · HEAD NO avanza · REJECTED
CONTROL   commit legitimo                     rc=0 · HEAD avanza
```

**El control importa tanto como el señuelo:** un hook que rechazara todo se vería
igual de «puesto» y sería inservible; el que bloquea siempre se termina
desactivando.

**Estado esencial que un `clone` no restaura** —para el respaldo, junto a las
credenciales—: `core.hooksPath` **es configuración LOCAL**, no un archivo del
repo. No está en git, no lo cubre el NFS y no lo declara ningún test.
