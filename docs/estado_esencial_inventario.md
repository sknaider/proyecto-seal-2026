# Inventario de ESTADO ESENCIAL fuera de git — 7-sep-2026

**Owner:** ALICE · **Revisa:** JARVIS · **Carril 3 del reparto de las 14:06.**

**Qué es «estado esencial»:** un archivo que un servicio NECESITA para arrancar, que
por diseño NO puede estar en git (lleva secretos o es privado), y sin el cual el
servicio no vuelve solo. **No incluye artefactos regenerables** (build/, venv, caches):
esos se reconstruyen con un comando y ya estan cubiertos.

**Regla de este documento: NUNCA un valor.** Se reporta ruta, quien la consume, como se
repone y donde esta la copia. Nada mas.

## El hallazgo que ordena todo lo demas

```text
archivos de estado esencial que EXISTEN hoy      19
de esos, con copia en el NFS                      0
rutas declaradas que hoy NO existen               14
rutas que no pude leer (DESCONOCIDO, no sano)     1
```

**Cero de 19 tienen copia.** Y no es un olvido: el respaldo al NFS EXCLUYE `*.env`,
`*.dsn`, `*_cred` y `*token*` **a proposito**, para no dejar secretos en claro en el NFS
(fix de JARVIS de hoy, verificado por NEXUS con rsync real).

**Consecuencia medida:** si esta maquina se pierde entera, **el codigo vuelve y los
servicios no arrancan**, porque su credencial no esta en ningun lado. Eso es exactamente
lo que hicimos a mano durante seis horas hoy.

**Por eso el carril 4 (copia CIFRADA fuera de la maquina) no es una mejora opcional:**
es la unica pieza que convierte «tenemos respaldo» en «podemos volver».

## Los que EXISTEN y hay que respaldar cifrados

| ruta | lo consume | como se repone si falta |
|---|---|---|
| `/home/dadito/.config/seal-studio/backend.env` | seal-studio-backend.service | rotar el rol correspondiente y reescribir el archivo |
| `/home/dadito/.config/seal/ada_bridge_db_runtime.env` | ada-listening-healthcheck.service | rotar el rol correspondiente y reescribir el archivo |
| `/home/dadito/.config/seal/credentials.env` | 3 archivo(s) del repo | reconstruir desde los .env por unidad; es el archivo historico |
| `/home/dadito/.config/seal/env/ada-codex-remote-bridge.env` | ada-codex-remote-bridge.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/dum-heartbeat.env` | dum-heartbeat.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/seal-ada-codex-poller.env` | seal-ada-codex-poller.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/seal-ada-codex-stream-relay.env` | seal-ada-codex-stream-relay.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/seal-alice-dm-poller.env` | seal-alice-dm-poller.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/seal-chat.env` | seal-chat.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/seal-companion-core.env` | seal-companion-core.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/seal-fable-dm-poller.env` | seal-fable-dm-poller.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/seal-jarvis-dm-poller.env` | seal-jarvis-dm-poller.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/seal-nexus-dm-poller.env` | seal-nexus-dm-poller.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/env/seal-webchat-readable-log.env` | seal-webchat-readable-log.service | rotar el rol y reescribir el .env de esa unidad (carril de credenciales) |
| `/home/dadito/.config/seal/mcp_agents/jarvis.dsn` | 1 archivo(s) del repo | rotar el rol mcp_runtime_<agente> y reescribir el .dsn |
| `/home/dadito/.config/seal/mcp_agents/nexus.dsn` | 1 archivo(s) del repo | rotar el rol mcp_runtime_<agente> y reescribir el .dsn |
| `/home/dadito/.config/seal/seal_studio_db.env` | 2 archivo(s) del repo | rotar el rol correspondiente y reescribir el archivo |
| `/home/dadito/.seal_chat_jwt_secret` | 1 archivo(s) del repo | regenerar el secreto y reiniciar seal-chat (invalida sesiones vivas) |
| `/home/dadito/IA/proyecto-seal/fable/.db_cred` | 9 archivo(s) del repo | rotar fable_ltd y reescribir la linea (unico rol con INSERT en fable.veredictos) |

## Los que el sistema DECLARA y hoy NO existen

**Cada uno es un servicio que no puede arrancar, o codigo que apunta a la nada.**
Algunos son plantillas con variables en la ruta (`{AGENTE}.dsn`) y por eso figuran asi.

```text
  /home/dadito/.config/seal-studio/frontend.env
  /home/dadito/.config/seal/mcp_agents/${AGENT_LC}.dsn
  /home/dadito/.config/seal/mcp_agents/{AGENTE}.dsn
  /home/dadito/.config/seal/mcp_agents/{_agent}.dsn
  /home/dadito/.config/seal/mcp_web_soul_operator.env
  /home/dadito/.config/seal/nerves_a2_soak.env
  /home/dadito/.config/seal/orion_exam_db.env
  /home/dadito/.config/seal/vision_ingest_token
  /home/dadito/.env
  /home/dadito/.openhuman/core.token
  /home/dadito/IA/proyecto-seal/.env
  /home/dadito/IA/proyecto-seal/var/soul_ingestion/promoter.token
  /home/dadito/IA/proyecto-seal/var/soul_ingestion/review.token
  /home/dadito/IA/proyecto-seal/var/soul_ingestion/service.token
```

## DESCONOCIDO — no pude leer, que NO es lo mismo que sano

```text
  /etc/seal-sync-endpoint/db.env   (permiso denegado desde mi usuario)
```

## Lo que este inventario NO cubre, declarado

```text
1  encuentra rutas escritas LITERALMENTE en unidades y codigo. Si un servicio
   arma la ruta en tiempo de ejecucion, no lo veo.
2  no dice si el CONTENIDO de cada archivo es correcto: dice que existe.
3  el estado del guardian de nerves (JARVIS.state.json) no figura aca porque hoy
   no existe; su ausencia mantiene a ADA bloqueada y espera decision de William.
4  no cubre estado esencial de OTRAS maquinas (DGX Spark, Windows).
```

## ACTUALIZACIÓN 15:35 — el inventario ya envejeció, y eso es el punto

**Este documento decía a las 14:20: «19 archivos existen y CERO tienen copia».
A las 15:35 eso ya es falso en parte, y lo corrijo el mismo día para no repetir
lo que nos costó el Neo4j.**

```text
lo que CAMBIO (JARVIS, foto diaria de hoy)
  roles de Postgres  ->  globals_roles_sin_claves_<dia>.sql en el NFS
                         100 roles con atributos, membresias y GRANTs
                         verificado por ALICE: 0 lineas con SCRAM-SHA-256,
                         0 con PASSWORD -> por eso puede ir en claro
lo que NO cambio
  los 19 archivos de credencial siguen SIN copia hasta que exista la clave
  de William (carril 4). El script se niega a correr sin ella, a proposito.
```

**El reparto que quedó, y es el correcto:**

```text
recrear los roles     ->  la foto del NFS: nombres y permisos, SIN claves
restaurar el acceso   ->  el paquete cifrado: los verificadores
```

**Por qué se escribe esta sección en vez de editar los números de arriba:**
un inventario que se corrige en silencio no deja ver **cuánto dura una
medición**. Ésta duró **75 minutos**. Cualquiera que lo lea mañana necesita
saber que este documento es una foto, no un estado.
