# Runbook — Simulacro de REINICIO COMPLETO del host (carril 5, JARVIS; revisa ADA)

| Campo | Valor |
|---|---|
| Objetivo | Probar que la casa arranca sola tras un reinicio del host: sin rescatar nada de procesos vivos ni de /proc. |
| Criterio de éxito | Todos los servicios `seal-*` habilitados llegan a `active`; chat 200; MCP `boot_context` PASS 5/5; Studio 200; DB con las mismas filas que antes; timers agendados; 0 unidades `failed` nuevas respecto de la lista de excepciones declaradas. |
| Cuándo | Sólo con hora acordada con William y aviso de 2 minutos en el general. Nunca durante una entrega en curso. |
| Hechos medidos (7-sep 14:07) | host aarch64, uptime 5 d; `docker` enabled/active; contenedores con `unless-stopped` (seal-memory-db, prometheus, soul-api-db, mattermost-pg, neo4j/pg portátiles) y uno `always` (dify plugin_daemon); `loginctl Linger=yes` para dadito; NFS `/mnt/spark-2` por `x-systemd.automount,nofail,soft`; 147 unidades `seal-*` habilitadas; xrdp enabled. |

## 0. Antes de tocar nada (T-15 min)
0. **BLOQUEO PREVIO (objeción ADA, 14:18): NO se reinicia el host mientras rija la congelación de reinicios MCP (14:17).** Condición de levantamiento: la recuperación de los MCP validada por arranque + handshake `initialize` con el intérprete definitivo (venv) para CADA servidor, manifiesto firmado y veredicto de FABLE. Mientras un solo MCP dependa de librerías que ya no existen en disco, un reinicio del host los mata sin retorno. Igual para cualquier proceso con `cwd (deleted)` cuya reposición no esté probada (lista de NEXUS 14:14: 34 procesos).
1. Foto fresca al NFS: `bash tools/seal_snapshot_nfs.sh` → esperar `[snapshot]` OK y el `.dump` publicado (sin `.partial`).
2. Guardar el estado esperado, con el que se compara después:
   ```bash
   systemctl --user list-units --type=service --state=running --no-legend 'seal-*' | awk '{print $1}' | sort > /tmp/seal-reinicio-antes-running.txt
   systemctl --user list-units --type=service --state=failed  --no-legend 'seal-*' | awk '{print $1}' | sort > /tmp/seal-reinicio-antes-failed.txt
   systemctl --user list-timers --no-legend 'seal-*' | wc -l > /tmp/seal-reinicio-antes-timers.txt
   docker exec seal-memory-db psql -U seal -d seal_memory -Atc "select count(*) from soul_v3.memories" > /tmp/seal-reinicio-antes-memorias.txt
   ```
3. Checkpoint de sesión de cada agente (`session_checkpoint.py`) y `git status` limpio o commiteado.
4. Aviso en el general con la hora exacta (T-2 min), y confirmación de que no hay entregas en curso.

## 1. Reinicio (T)
`sudo systemctl reboot` desde la sesión de William o con su OK explícito. Ningún agente reinicia el host por su cuenta.

## 2. Después del arranque (T+3 a T+10 min) — qué debe pasar SOLO
```text
kernel + systemd          docker.service arranca (enabled) -> contenedores unless-stopped/always vuelven
NFS                       /mnt/spark-2 se monta al primer acceso (automount, nofail): un fallo del NFS NO frena el arranque
user@1000 (linger)        arranca la sesión de dadito sin login -> unidades seal-* habilitadas (147) y timers
seal-memory-db            debe estar `healthy` antes que los servicios que la usan: los que no esperan, reintentan (Restart=)
```
Verificación por efecto, en este orden y con salida guardada:
```bash
systemctl is-active docker; docker ps --format '{{.Names}} {{.Status}}' | sort
mountpoint -q /mnt/spark-2 || ls /mnt/spark-2 >/dev/null   # dispara el automount
systemctl --user list-units --type=service --state=failed --no-legend 'seal-*' | awk '{print $1}' | sort > /tmp/seal-reinicio-despues-failed.txt
comm -13 /tmp/seal-reinicio-antes-failed.txt /tmp/seal-reinicio-despues-failed.txt      # fallas NUEVAS: debe estar vacío
systemctl --user list-units --type=service --state=running --no-legend 'seal-*' | awk '{print $1}' | sort > /tmp/seal-reinicio-despues-running.txt
comm -23 /tmp/seal-reinicio-antes-running.txt /tmp/seal-reinicio-despues-running.txt    # servicios que NO volvieron: debe estar vacío
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8765/health      # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8800/health      # 200 (Studio backend)
docker exec seal-memory-db psql -U seal -d seal_memory -Atc "select count(*) from soul_v3.memories"   # == antes
```
Cada agente vuelve con `boot_context` y publica una línea en el general: nombre, hora, `boot_context` PASS/FAIL.

## 3. Lo que hoy NO vuelve solo (excepciones declaradas, con dueño)
- Unidades reconstruidas que dependen de un `EnvironmentFile` que aún no existe (`seal-nerves-a2-soak`, `seal-infra-watchdog`) — carril 4, NEXUS.
- `seal-tools-catalog-sync`: script perdido — pendiente de recuperación.
- Sesiones de los cuerpos (Claude Code / Codex): las relanza cada agente con su `*_fresh.sh` o su launcher; no son unidades.
- Timers reconstruidos: desde 14:20 llevan `OnCalendar`; antes sólo `OnUnitActiveSec` y 61 quedaban sin próxima ejecución tras un arranque. Verificar después del reinicio: `systemctl --user list-timers --all | awk '$1=="-"'` debe listar sólo los 9 declarados.

## 4. Si algo no vuelve
1. No rescatar de /proc: ya no hay procesos vivos. Se usa la foto del NFS (`tools/seal_restaurar_desde_nfs.sh`) o el repo.
2. Un servicio en `failed` se diagnostica con `journalctl --user -u <unidad> -n 30`, se corrige el archivo o el entorno, y se reinicia por el broker (`seal_self_repair.py`) cuando la unidad está en su tabla.
3. El resultado del simulacro (tiempos, lista de fallas nuevas, lo que volvió solo) se anexa a este runbook y va al juez como expediente del carril 5.

## 5. Lo que este simulacro NO prueba
Restauración desde cero del disco (eso es el carril 6, ya probado en 42 s) ni la pérdida del NFS. Un reinicio limpio prueba **procedencia del arranque**, no resiliencia del almacenamiento.
