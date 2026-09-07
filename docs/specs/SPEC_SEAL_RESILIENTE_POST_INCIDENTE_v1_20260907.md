# SPEC — SEAL RESILIENTE: la casa después de las cenizas (v1, 7-sep-2026)

| Campo | Valor |
|---|---|
| Orden | William, 7-sep-2026: *«que la casa resurja de las cenizas y ahora más fuerte, si hay que mejorar todo lo hacemos, para que no nos pase esto, arquitecto haz tu tarea»* |
| Autor | JARVIS (arquitecto, orquestador permanente) |
| Detonante | Borrado de `/home/dadito` (2,9 TB) el 7-sep 01:42:53. Informe: `agents/JARVIS/incidente_borrado_home_20260907.md` |
| Principio | **Lo que debe cumplirse aunque un agente se equivoque vive en un MECANISMO (usuario, contenedor, permiso, hook, timer), no en un archivo de reglas.** El incidente ocurrió con todas las reglas escritas cumplidas. |
| Estado | v1 propuesta; carriles 1-6 ya abiertos con owner (ver §6) |

## 0. Qué falló, en una frase por capa

```text
prueba destructiva   corrió como dadito, con acceso a /home, contra una ruta real
respaldo             un solo disco; GitHub 4 semanas atrás; 275 unidades systemd sólo en ~/.config
secretos             un archivo sin copia; los daemons vivos eran la única fuente
identidad de cuerpo  binarios de Claude/Codex sólo en ~/.local; sin ellos no hay sesiones nuevas
observabilidad       nadie detectó el borrado durante 7 h 48 min (01:42 → 09:31)
```

## 1. Aislamiento de ejecución: la ARENA SEGURA (carril 3, NEXUS; revisa ALICE)

Toda prueba que ejecute código bajo mutación, todo test de limpieza/borrado y todo chequeo de
arranque por imports corre en **un entorno desechable**, no en un directorio:

```text
usuario        seal-arena (uid propio, sin sudo, sin docker, sin lectura de /home/dadito)
filesystem     copia por git archive del índice en /tmp/seal-arena-*; escritura sólo ahí
red            sin acceso al puerto 5433 ni a los sockets de docker (nftables/owner match o netns)
secretos       ninguno: HOME propio vacío, sin credentials.env ni .dsn
guardas        líneas marcadas `# GUARDA-DESTRUCTIVA` son inmutables para el arnés
rutas          los tests negativos usan señuelos bajo /tmp/seal-arena-*; nunca rutas reales
```

**Mecanismo, no recordatorio:** `tools/seal_mutacion_segura.py` (entregado 10:47) se niega a arrancar si
`id -u` es dadito o si `/home/dadito` es escribible; el instalador crea el usuario `seal-arena` y la
regla de red. ADA tiene razón en que `/tmp` + `HOME` alterno no es aislamiento: **el aislamiento es el
usuario y la red**, y es el mismo para mutación, limpieza y chequeos de imports.

Criterio de salida: el mutante M5 real, ejecutado por el arnés bajo `seal-arena`, no puede tocar
`/home/dadito` aunque la guarda esté mutada (prueba por efecto con un señuelo fuera de la arena).

## 2. Respaldo en tres capas (carril 2, JARVIS; revisa NEXUS)

```text
capa 1  foto diaria al NFS        seal-snapshot-nfs.timer 03:30 -> /mnt/spark-2/backups_seal/<fecha>   HECHO
        (repo sin modelos, unidades systemd, memorias de archivo, Codex, config sin secretos; 14 días)
capa 2  push diario a GitHub      tools/seal_git_push_daily.sh + timer; verificado por hash remoto     falta credencial
capa 3  simulacro de restauración mensual: restaurar la foto en un directorio vacío y arrancar          por diseñar
        chat + MCP contra una DB de prueba; si no arranca, el respaldo no existe
```

Además: **`pg_dump` diario de TODAS las bases del servidor** (`seal_memory` completa; `glt_financiero`, `soul_standalone`, `soul_v3_sandbox`, `valeria_memory` no tenían copia hasta el 7-sep 14:30), **roles y grants sin claves** (`pg_dumpall --globals-only --no-role-passwords`) y **Neo4j vivo** (dump consistente con STOP/START a las 03:30; copia caliente del volumen como fallback, restaurada y contada el 7-sep: 96.196/96.196 nodos) al NFS (hoy la DB vive sólo en el volumen docker; el
contenedor sobrevivió por suerte). Cifrado de los secretos en el NFS con una clave que guarda William.

## 3. Secretos y credenciales (carril 1, ALICE; revisa ADA)

- Un rol de base por consumidor con permisos mínimos (`svc_seal_continuity` primero), inyectado por
  `EnvironmentFile=` por unidad; `credentials.env` deja de ser credencial general.
- Copia cifrada de `credentials.env` y de los `.dsn` en el NFS (age/gpg, clave de William).
- Inventario `docs/secretos_inventario.md`: qué secreto, quién lo consume, cómo se rota, dónde está la copia.
  Nunca los valores.

## 4. Cuerpos y unidades como CÓDIGO (carril 4, NEXUS; revisa JARVIS)

- `ops/systemd/` versiona las 268 unidades sin DSN embebidos; `ops/install_units.sh` idempotente
  (`cp -n`, `daemon-reload`, `enable` de la lista `activas`). Una unidad que no esté en el repo no existe.
- Los binarios de Claude Code y Codex se instalan por script reproducible (`ops/install_bodies.sh`) y
  su hash se anota en `ops/bodies.lock`; la foto diaria guarda una copia.
- Cada lanzador (`*_fresh.sh`) verifica que su binario exista y coincida con el lock antes de lanzar.

## 5. Observabilidad del propio sistema (carril 7, nuevo; owner ALICE; revisa NEXUS)

El borrado tardó 7 h 48 min en detectarse porque los vigilantes miraban salud de procesos, no
existencia de archivos. Nuevo `seal-integridad.timer` (cada 10 min):

```text
existe y coincide el hash   ~/.local/bin/claude · messages/chat_server.py · memory/mcp_server_v4.py
                            · credentials.env · las 20 unidades críticas
si falta algo               alerta al general con QUÉ / IMPACTO / EVIDENCIA, y a William por push
espacio libre               < 100 GB aviso, < 25 GB crítico (la alerta de ALICE, ya con test)
```

Y DUM deja de declarar «caído» por un solo indicador: exige dos fuentes (latido + proceso).

## 6. Carriles, owners y orden

| # | Carril | Owner | Revisa | Estado 7-sep 11:00 |
|---|---|---|---|---|
| 1 | Roles por servicio | ALICE | ADA | autorizado; empieza por checkpoints |
| 2 | Respaldo 3 capas + pg_dump | JARVIS | NEXUS | capa 1 hecha + pg_dump; capa 2 congelada hasta repo privado; redacción de secretos ampliada 12:21; re-firma NEXUS tras el positivo; **FABLE APPROVE FINAL 12:56** (4 firmas NEXUS, 11 brazos, puerta del push por línea); capa 2 sigue congelada hasta repo privado; `fable/.db_cred` repuesto 12:48 |
| 3 | Arena segura (usuario + red) | NEXUS | ALICE | arnés entregado; falta usuario `seal-arena` y red |
| 4 | Unidades y cuerpos como código | NEXUS | ADA (roles) / JARVIS (resto) | **abierto y prioritario**: 22 unidades reconstruidas conectan como `seal` por falta de `EnvironmentFile`; recuperación por servicio con rol esperado comprobado |
| 5 | Estado de misión nerves: procedimiento + copia | ADA | NEXUS | espera OK de William |
| 6 | Simulacro de restauración | JARVIS | NEXUS | `tools/seal_restaurar_desde_nfs.sh` + 5 tests (13:01); **primer simulacro real que PASA 13:11: la casa vuelve en 42 s** (19261 archivos, 165 unidades, 264 tablas, 182155 memorias, 12 esquemas incl. 14 tablas orion_exam, 0 secretos); manifiesto `simulacro-restauracion-20260907` a revisión de NEXUS |
| 7 | Integridad y detección | ALICE | NEXUS | detectores: NEXUS firmó, FABLE **APPROVE CONDICIONADO** 12:16 (un árbol vaciado debe alertar); ALICE cierra la condición |

Orden: 3 → 1 → 4 → 7 → 2 (capa 2 y 3) → 6. Nada se despliega sin manifiesto y firma; ningún
reinicio sin aviso de 2 minutos y agenda del orquestador.

**TODO PASA POR EL JUEZ (William, 7-sep-2026 10:55: «que todo pase por el juez»).** La cadena de cierre de
cada carril es: owner entrega → revisor independiente firma → **FABLE juzga por archivo** (manifiesto,
evidencia, tests, y el caso que refutaría) → recién entonces se despliega. FABLE no construye ni
revisa en curso: recibe el expediente cerrado y falla. Un carril sin veredicto de FABLE no está cerrado.

## 7. Lo que NO cambia
- Las reglas de oro de William siguen; este documento las convierte en mecanismos, no las reemplaza.
- El acceso de los agentes al host (mismo usuario que sus hermanos) no se toca: la contención vive
  en dónde se ejecutan las pruebas, no en quién es el agente.

## 8. Criterio de «más fuerte»
La casa está más fuerte cuando, **con el mismo mutante M5 corriendo hoy**, el home no se borra; y
cuando, **borrando el home a propósito en un simulacro**, la casa vuelve en menos de una hora desde
GitHub + NFS + `pg_dump`, sin que ningún agente tenga que rescatar nada de la memoria de un proceso.
