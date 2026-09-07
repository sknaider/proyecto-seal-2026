# Auditoría profunda — qué tenemos y qué falta conectar (7-sep-2026)

**Pedida por William a las 14:10.** Ejecutada por ALICE con tres subagentes en
paralelo (MCP, servicios, datos) más el frente de código con los detectores
propios. **Todo de sólo lectura.** Cada línea lleva su evidencia o va marcada
como no verificada.

**Regla que se aplicó y que ordena el documento:** «no lo encontré» no es «no
existe», y «no pude mirar» no es «está sano». Lo desconocido tiene su propia
categoría y no se mezcla con lo sano.

---

## 1. Lo que ESTÁ y funciona, verificado

```text
base seal_memory        248 tablas · 2.847.349 filas contadas de verdad
                        copia integra: verificada LEYENDO EL ARCHIVO ENTERO
                        (pg_restore -f /dev/null, exit 0), no por su indice
servicios               119 activos · 99 timers con proxima ejecucion
MCP seal-memory         active, puerto propio, health ok en dos llamadas
MCP web/CDP             active, cadena de auditoria ok
Studio                  backend con credencial rotada hoy, /health 200
arbol de codigo         0 credenciales de base de datos (scrub de las 12:40)
                        0 archivos que un servicio necesite y no esten en git
                        0 paquetes mutilados en el venv de produccion
```

## 2. Lo que FALTA CONECTAR — con causa medida

```text
3 conectores MCP (github, postgres, prometheus)
    corren con el python3 del SISTEMA, que NO tiene el paquete `mcp`.
    Viven de lo que cargaron el 3-sep. Mueren con su sesion y no vuelven.
    ARREGLO MEDIDO: apuntarlos al venv de seal-spark. Probado por handshake
    en dos de ellos: responden `initialize`. NO requiere instalar nada.

alice-v2-mcp-root-broker
    su LoadCredential apunta a 2 archivos que ya no existen. Corre con datos
    cacheados del 4-sep; no sobrevive un reinicio.

token del MCP de GitHub
    HTTP 401. OJO: el token de GIT funciona (push verificado). Son dos.

12 unidades con LOAD=bad-setting
    el reconstructor escribio un "!" como parte de la ruta de WorkingDirectory.
    No pueden (re)iniciar; dos de ellas estan vivas y no sobrevivirian.

servicios ACTIVOS sin archivo de unidad
    claude-proxy, lyra-backend, orion-exam, sunshine, rustdesk-hbbr/hbbs.
    Corren hoy; no hay definicion que los vuelva a lanzar tras un reboot.

mundial-dashboard
    ACTIVO y su codigo NO existe. Restart=always lo dejaria en bucle.
```

## 3. Lo que NO tenía copia — el hallazgo más caro

```text
4 bases enteras          glt_financiero (facturacion GTL.pe: 12 facturas)
                         soul_standalone · soul_v3_sandbox · valeria_memory
                         Todo el pipeline volcaba SOLO seal_memory.
                         -> JARVIS las incorporo hoy al respaldo diario.

roles de PostgreSQL      115 roles, 54 con contraseña. pg_dump NUNCA los trae.
                         -> incorporados a la copia CIFRADA (carril 4).

neo4j vivo               la foto diaria copiaba un directorio CONGELADO desde
                         el 31-marzo; el grafo vivo usa otro volumen.
                         La replica fuera del host NUNCA funciono: todos los
                         logs decian SKIP.
                         -> JARVIS tomo copia caliente y la RESTAURO en un
                            contenedor desechable: 96.196 nodos contados.

19 archivos de credencial  existen y CERO tenian copia. Es por diseño -la foto
                         excluye secretos- y por eso existe el carril 4.
```

## 4. Trampas silenciosas encontradas (parecen sanas y no lo están)

```text
61 timers ACTIVE+ENABLED sin proxima ejecucion, entre ellos OCHO guardias de
   seguridad. Corregido por JARVIS: 61 -> 9. Causa: OnUnitActiveSec sin
   OnCalendar sólo reprograma si el servicio vuelve a activarse.

un dump a medio escribir PASA el chequeo por indice: la tabla de contenidos
   va al principio. Acreditar integridad exige leer el archivo entero.

un respaldo que aborta se ve igual que uno que nunca hizo falta
   (orion-backup, sin credencial, sin correr desde las 09:30).

el gate de calidad hashea el DISCO, no HEAD: un verde puede apoyarse en bytes
   que nadie commiteo.
```

## 5. DESCONOCIDO — declarado, no escondido

```text
- 8 servidores MCP con su interprete borrado: dependencias sin medir.
- causa exacta del 401 del token de GitHub (expiro / revocado / scope).
- si /mnt/spark-2 tiene redundancia fisica propia.
- 5 motores Postgres mas en el host (mattermost, soul-api-db, spectre) sin auditar.
- el estado de las 5 misiones A2: irrecuperable por las fuentes consultadas;
  su desvinculacion espera decision de William.
- alcance exacto de la exposicion de credenciales de las 14:23 (ver
  EXPOSICION_PRUEBA_CIFRADO_ALICE.md): la evidencia se destruyo al contenerla.
```

## 6. Lo que esta auditoría NO cubre

```text
otras maquinas (DGX Spark, Windows), Matrix/Synapse, Dify, y los MCP remotos
de la cuenta de claude.ai. Sólo se auditó este host.
```
