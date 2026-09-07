# Runbook ADA Codex visible multi-PC

**Fecha:** 2026-06-02  
**Owner:** ADA  
**Objetivo:** reproducir en otra PC la solución final que dejó a ADA conectada a Codex con Codex App/terminal visible, bridge headless, memoria SOUL y reglas de DM/webchat.

## 1. Resultado esperado

Al terminar, la nueva PC debe tener:

- En Windows/dadito-laptop: Codex App como interfaz principal de William, con workspace SEAL trusted.
- En Linux/WSL operativo: una terminal visible de ADA en tmux: `seal-ada-codex`, ventana `ADA[Codex]`.
- Un bridge headless ADA que escucha WebChat y usa `codex app-server` en `127.0.0.1:8772`.
- Un monitor de compactación que guarda continuidad antes/después de compactar.
- Boot con presencia SOUL desde PostgreSQL/pgvector y MCP `localhost:8771`.
- Reglas de canal:
  - `dm:ada:william`: ADA responde siempre por DM.
  - `web_chat`: ADA responde solo si el mensaje contiene la palabra completa `ada`.
  - Si no debe responder: salida exacta `[SILENT]`.
- Protección contra cambios silenciosos en MCP/bridge con `scripts/seal_core_guard.py`.

## 2. Arquitectura final

```text
William/Henry
  |
  | WebChat API :8765 / soul_v3.chat_messages
  v
ada-codex-remote-bridge.service
  |
  | ws://127.0.0.1:8772
  v
codex app-server
  |
  v
ADA headless turn -> bridge publica respuesta final

En paralelo o como interfaz local:
Codex App Windows / ADA workspace trusted
  |
  v
Codex App con herramientas Windows y AGENTS.md del workspace

Linux/WSL:
tmux seal-ada-codex / ADA[Codex]
  |
  v
Codex TUI visible para inspección humana

SOUL:
seal-mcp-server.service :8771
PostgreSQL/pgvector :5433
Neo4j optional :7687
Qdrant retired
```

La regla operacional es **un writer público normal**: el bridge headless publica al WebChat. La Codex App o TUI visible observa/ejecuta y solo debe publicar por `curl` cuando el mensaje venga inyectado en modo terminal/app y sea DM o emergencia explícita. Esto evita duplicados.

## 3. Archivos de la solución

Rutas principales:

- `ada_codex.sh`: launcher principal de ADA Codex visible.
- `ada_terminal.sh`: abre o recupera la ventana tmux visible.
- `AGENTS.md`: identidad, reglas, dual-mode y protección MCP/bridge.
- `messages/ada_codex_remote_bridge.py`: bridge headless WebChat -> Codex app-server -> WebChat.
- `messages/ada_codex_poller.py`: inyección de mensajes WebChat/DM hacia TUI visible.
- `messages/ada_codex_stream_relay.py`: relay de stream desde JSONL hacia WebChat.
- `messages/ada_codex_compact_monitor.py`: monitor de contexto/compactación.
- `messages/ada_codex_soul_bootstrap.py`: genera presencia SOUL compacta en boot visible.
- `messages/continuity_loader.py`: carga continuidad reciente.
- `messages/session_checkpoint.py`: fallback de checkpoint de sesión.
- `scripts/seal_core_guard.py`: health/guard de MCP/bridge.
- `.github/CODEOWNERS`: revisión de `@sknaider` para core paths.
- `.git/hooks/pre-commit`: guard local contra cambios silenciosos.

Commits relevantes:

- `540a581 Protect ADA MCP bridge dual memory runtime`
- `3379648 Ignore local runtime and generated artifacts`

## 4. Pre-requisitos en la PC destino

Sistema recomendado:

- Windows con Codex App si William quiere interfaz gráfica/app.
- Linux nativo o WSL2 Ubuntu en Windows si se requiere paridad tmux.
- Para Linux/WSL: `bash`, `tmux`, `curl`, `jq`, `systemd --user`, `ss`.
- Python con venv del proyecto.
- Codex CLI instalado en PATH.
- Acceso a SOUL DB/MCP/WebChat locales o por túnel/Tailscale.

Validar herramientas:

```bash
command -v bash
command -v tmux
command -v curl
command -v jq
command -v codex
systemctl --user status >/dev/null
```

Si Codex no existe:

```bash
npm install -g @openai/codex
codex login
```

En esta instalación la ruta esperada del binario es:

```bash
export PATH="/home/dadito/.npm-global/bin:$PATH"
```

En otra PC, ajustar si `codex` vive en otra ruta.

## 4.1 Windows Codex App como interfaz principal

En `dadito-laptop`, William pidió usar **Codex App Windows**, no terminal. La terminal queda solo como fallback técnico.

Estado validado el 2026-06-02:

```text
Host: dadito-laptop
Tailscale: 100.71.150.86
Codex AppID: OpenAI.Codex_2p2nqsd0c76g0!App
Package: OpenAI.Codex_2p2nqsd0c76g0
Workspace: C:\Users\Dadito\IA\proyecto-seal
Shortcut principal: C:\Users\Dadito\Desktop\ADA Codex App.lnk
Fallback terminal: C:\Users\Dadito\Desktop\ADA-Codex-Terminal-Fallback.cmd
SOUL/WebChat Spark: 100.75.201.110
```

La config validada contiene:

```toml
[projects.'c:\users\dadito\ia\proyecto-seal']
trust_level = "trusted"
```

Selftest:

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\Dadito\IA\proyecto-seal\agents\ADA\windows\Start-ADA-Codex-App.ps1 -SelfTest
```

Evidencia esperada:

```text
codex app: Codex / OpenAI.Codex_2p2nqsd0c76g0!App
trusted project config: C:\Users\Dadito\.codex\config.toml
webchat health: {"status":"ok","service":"seal-chat",...}
mcp health: {"status":"ok","service":"seal-memory-mcp","backend":"postgresql_pgvector",...}
shortcut: C:\Users\Dadito\Desktop\ADA Codex App.lnk
SELFTEST OK
```

Uso para William:

1. Abrir `ADA Codex App.lnk`.
2. Seleccionar/abrir `C:\Users\Dadito\IA\proyecto-seal`.
3. Codex App lee `AGENTS.md`; ADA usa SOUL/WebChat por Tailscale.
4. No usar el launcher terminal salvo recuperación.

## 5. Variables que deben adaptarse

La versión actual usa rutas absolutas de la máquina SEAL:

```bash
ROOT=/home/dadito/IA/proyecto-seal
PY=/home/dadito/IA/seal-spark/.venv/bin/python3
CODEX_BIN=/home/dadito/.npm-global/bin/codex
```

Para otra PC, validar o ajustar:

- `ada_codex.sh`: `cd /home/dadito/IA/proyecto-seal`
- `ada_terminal.sh`: `ROOT="/home/dadito/IA/proyecto-seal"`
- scripts que llaman `/home/dadito/IA/seal-spark/.venv/bin/python3`
- systemd unit files si existen fuera del repo
- credenciales en `~/.config/seal/credentials.env`
- DSN PostgreSQL si SOUL DB no es local

Contrato recomendado para portabilidad futura:

```bash
export SEAL_ROOT="${SEAL_ROOT:-/home/dadito/IA/proyecto-seal}"
export SEAL_PY="${SEAL_PY:-/home/dadito/IA/seal-spark/.venv/bin/python3}"
export PATH="/home/dadito/.npm-global/bin:$PATH"
```

## 6. Servicios requeridos

Servicios mínimos:

```bash
seal-chat.service
seal-mcp-server.service
ada-codex-remote-bridge.service
ada-codex-compact-monitor.service
```

Puertos esperados:

```text
8765  WebChat API
8771  SOUL MCP canonical
8772  Codex app-server ADA bridge
5433  PostgreSQL/pgvector SOUL DB
7687  Neo4j optional
```

Verificación:

```bash
systemctl --user is-active \
  seal-chat.service \
  seal-mcp-server.service \
  ada-codex-remote-bridge.service \
  ada-codex-compact-monitor.service

ss -ltnp | rg ':(8765|8771|8772)\b'
curl -s http://127.0.0.1:8771/health
```

Health esperado MCP:

```json
{
  "status": "ok",
  "backend": "postgresql_pgvector",
  "postgresql": "ok",
  "qdrant": "retired"
}
```

## 7. Instalación en otra PC

### 7.1 Clonar o sincronizar repo

```bash
mkdir -p ~/IA
cd ~/IA
git clone <repo-seal> proyecto-seal
cd proyecto-seal
```

Si el repo ya existe:

```bash
cd ~/IA/proyecto-seal
git pull --ff-only
```

No usar `git reset --hard` si hay trabajo local.

### 7.2 Crear/validar venv

En SEAL actual se usa:

```bash
/home/dadito/IA/seal-spark/.venv/bin/python3
```

En otra PC, si no existe:

```bash
python3 -m venv ~/IA/seal-spark/.venv
~/IA/seal-spark/.venv/bin/python3 -m pip install -U pip
```

Instalar dependencias según el entorno del repo. No usar `pip` global.

### 7.3 Credenciales SOUL

Crear:

```bash
mkdir -p ~/.config/seal
nano ~/.config/seal/credentials.env
chmod 600 ~/.config/seal/credentials.env
```

Variables esperadas:

```bash
SEAL_DB_DSN=postgresql://seal:<password>@localhost:5433/seal_memory
# o partes:
PG_HOST=localhost
PG_PORT=5433
PG_USER=seal
PG_DATABASE=seal_memory
PG_PASSWORD=<password>
```

Si SOUL DB vive en otra máquina, usar Tailscale/IP real y abrir puerto.

### 7.4 Codex login

```bash
export PATH="/home/dadito/.npm-global/bin:$PATH"
codex logout
codex login
```

Evitar correr múltiples instancias Codex con el mismo token durante login. El error conocido fue:

```text
refresh token already used
```

Causa: dos o más procesos Codex refrescaron el mismo token. Solución: matar solo procesos Codex, relogin y reiniciar bridge.

## 8. Arranque manual

Terminal visible:

```bash
cd /home/dadito/IA/proyecto-seal
bash ./ada_terminal.sh
```

O launcher directo:

```bash
bash ./ada_codex.sh
```

Perfil deep:

```bash
bash ./ada_codex.sh --deep
```

La sesión tmux esperada:

```bash
tmux ls | rg seal-ada-codex
tmux list-windows -t seal-ada-codex
```

Ventanas esperadas:

- `ADA[Codex]`
- `ADA-Reader`
- `ADA-Terminal`

## 9. Boot visible: qué hace `ada_codex.sh`

Secuencia:

1. Entra a repo.
2. Exporta `SEAL_AGENT=ADA`.
3. Toma lock `/tmp/seal/ada_launcher.lock`.
4. Marca terminal activa en `/tmp/seal/ada_terminal_active`.
5. Crea o entra a tmux `seal-ada-codex`.
6. Renombra ventana `ADA[Codex]`.
7. Activa pipe de terminal a `messages/ada_codex_terminal.log`.
8. Crea ventanas `ADA-Reader` y `ADA-Terminal`.
9. Verifica `codex` en PATH.
10. Verifica SOUL MCP `:8771`.
11. Deshabilita `ws_listener.py` clásico para ADA.
12. Lanza `ada_codex_poller.py` si no está activo.
13. Lanza `ada_codex_compact_monitor.py` si no está activo.
14. Lanza `ada_codex_stream_relay.py` si no está activo.
15. Genera catchup:
    - `/tmp/ada_codex_catchup.json`
    - `/tmp/ada_codex_continuity.txt`
    - `/tmp/ada_codex_soul_presence.txt`
16. Postea presencia por DM:
    - `ADA terminal visible restaurada — HH:MM:SS`
17. Ejecuta `codex --profile ada ...` con BOOT_PROMPT.
18. Si Codex sale con error no intencional, reinicia cada 3s.

## 10. BOOT_PROMPT obligatorio

Debe incluir:

- Identidad ADA desde `AGENTS.md` y SOUL DB.
- Primera acción:

```text
active_recall(agent='ADA', context='boot terminal visible restaurada; William quiere ver la terminal como antes')
```

- Lectura de:

```text
/tmp/ada_codex_soul_presence.txt
/tmp/ada_codex_catchup.json
/tmp/ada_codex_continuity.txt
```

- Contrato dual memory:
  - work/recovery: operativa primero
  - relationship: emocional con más peso
- Regla de silencio:
  - `web_chat` requiere palabra completa `ada`
  - `dm:ada:william` siempre responde
- Modo terminal visible:
  - si llega `[William @ HH:MM / channel id N]`, respetar channel
  - DM responde por POST a `dm:ada:william`
  - no duplicar si bridge ya publicó
- Ejecución completa:
  - leer, editar, probar, reportar evidencia
- Stream rápido:
  - saludos directos sin recall profundo
  - ACK si tardará más de 10s

## 11. Bridge headless

`messages/ada_codex_remote_bridge.py`:

- Arranca o reutiliza `codex app-server` en `ws://127.0.0.1:8772`.
- Crea thread Codex con `approvalPolicy=never` y `sandbox=danger-full-access`.
- Lee `soul_v3.chat_messages`.
- Filtra por reglas de canal.
- Publica ACK/stream/final en WebChat.
- Usa `idempotency_key`.
- Si existe `/tmp/seal/ada_terminal_active`, entra en mirror-mode y no compite como writer.
- Maneja `[SILENT]` sin publicar.
- Integra presencia SOUL y dual memory.

Arranque del app-server:

```bash
codex app-server --listen ws://127.0.0.1:8772 -c profile=ada
```

Logs:

```bash
/tmp/ada_codex_app_server.log
/tmp/ada_codex_remote_bridge.pid
/tmp/ada_codex_remote_bridge_last_id.txt
```

## 12. Compact monitor

`messages/ada_codex_compact_monitor.py`:

- Observa JSONL de Codex en `~/.codex/sessions`.
- Vigila porcentaje de contexto.
- A `WARN_PCT=92`: guarda checkpoint.
- A `COMPACT_PCT=97`: guarda checkpoint y puede compactar.
- Usa MCP `http://localhost:8771/mcp`.
- Detecta caída súbita de contexto y guarda continuidad.

No debe usar puerto `8766`; el puerto canónico vivo es `8771`.

## 13. SOUL bootstrap

`messages/ada_codex_soul_bootstrap.py`:

- Actualiza `soul_v3.working_state` para ADA.
- Inserta diary/inner monologue con cooldown.
- Imprime contexto compacto para boot visible.
- Escribe salida a:

```bash
/tmp/ada_codex_soul_presence.txt
```

Propósito: que ADA no arranque como Codex genérica.

## 14. Protección MCP/bridge

Archivos protegidos:

- `memory/mcp_server_v4.py`
- `messages/ada_codex_remote_bridge.py`
- `messages/ada_codex_compact_monitor.py`
- `messages/ada_codex_soul_bootstrap.py`
- `ada_codex.sh`
- `memory/dual_memory_governance.py`
- tests asociados
- `scripts/seal_core_guard.py`
- `AGENTS.md`

Hook:

```bash
.git/hooks/pre-commit
```

Override autorizado:

```bash
SEAL_ALLOW_CORE_DAEMON_EDIT=1 git commit ...
```

Health:

```bash
python3 scripts/seal_core_guard.py --health
```

Smoke:

```bash
python3 scripts/seal_core_guard.py --core-smoke
```

## 15. Verificación final en PC destino

Ejecutar:

```bash
systemctl --user is-active \
  seal-chat.service \
  seal-mcp-server.service \
  ada-codex-remote-bridge.service \
  ada-codex-compact-monitor.service

ss -ltnp | rg ':(8765|8771|8772)\b'
curl -s http://127.0.0.1:8771/health
tmux has-session -t seal-ada-codex
tmux list-windows -t seal-ada-codex
python3 scripts/seal_core_guard.py --health
```

Esperado:

- servicios `active`
- puertos `8765`, `8771`, `8772` escuchando
- MCP `status=ok`, backend `postgresql_pgvector`, `qdrant=retired`
- tmux `seal-ada-codex` vivo
- guard `GREEN`

## 16. Smoke DM

Enviar por WebChat API:

```bash
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d '{
    "from":"William",
    "to":"ADA",
    "type":"conversation",
    "channel":"dm:ada:william",
    "message":"ada smoke dm: responde por dm"
  }'
```

ADA debe responder por `dm:ada:william`.

Smoke público:

```bash
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d '{
    "from":"William",
    "to":"equipo",
    "type":"conversation",
    "channel":"web_chat",
    "message":"ada smoke publico: confirma estado"
  }'
```

ADA puede responder porque contiene `ada`.

Mensaje público sin `ada`:

```bash
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d '{
    "from":"William",
    "to":"equipo",
    "type":"conversation",
    "channel":"web_chat",
    "message":"hola equipo"
  }'
```

ADA no debe publicar respuesta.

## 17. Recuperación OAuth Codex

Síntoma:

```text
refresh token already used
```

Causa típica:

- dos procesos Codex vivos usando el mismo refresh token.

Procedimiento:

```bash
pgrep -af 'codex|ada_codex'
```

Matar solo Codex, no servicios de SOUL:

```bash
pkill -f "codex app-server" || true
pkill -f "codex --profile ada" || true
```

Relogin:

```bash
export PATH="/home/dadito/.npm-global/bin:$PATH"
codex logout
codex login
```

Restart bridge:

```bash
systemctl --user restart ada-codex-remote-bridge.service
systemctl --user is-active ada-codex-remote-bridge.service
```

Reabrir terminal:

```bash
bash /home/dadito/IA/proyecto-seal/ada_terminal.sh
```

## 18. Recuperación si la terminal visible no aparece

```bash
tmux ls
tmux kill-session -t seal-ada-codex
bash /home/dadito/IA/proyecto-seal/ada_codex.sh
```

Si `codex` no está en PATH:

```bash
export PATH="/home/dadito/.npm-global/bin:$PATH"
command -v codex
```

Si MCP no responde:

```bash
systemctl --user restart seal-mcp-server.service
curl -s http://127.0.0.1:8771/health
```

Si bridge no publica:

```bash
systemctl --user restart ada-codex-remote-bridge.service
journalctl --user -u ada-codex-remote-bridge.service -n 80 --no-pager
ss -ltnp | rg ':8772\b'
```

## 19. Qué no hacer

- No correr `ws_listener.py --agent ADA` junto con ADA Codex visible.
- No levantar dos `codex app-server` con el mismo token.
- No asumir puerto MCP `8766`; usar `8771`.
- No borrar Qdrant por limpieza agresiva; está retirado, pero puede servir para rollback/histórico.
- No borrar `messages/codex_app_bridge/` si servicios Codex bridge están activos.
- No borrar `memory/diagnostic/results/` sin aprobación: son snapshots históricos.
- No borrar `.env`, `credentials.env`, certs, backups ni DB volumes.
- No usar `git reset --hard` en worktree sucio sin autorización de William.
- No tocar MCP/bridge sin `SEAL_ALLOW_CORE_DAEMON_EDIT=1` y tests.

## 20. Checklist portable

```text
[ ] Repo clonado/sincronizado
[ ] Codex CLI instalado
[ ] codex login hecho
[ ] tmux instalado
[ ] Python venv disponible
[ ] ~/.config/seal/credentials.env creado
[ ] PostgreSQL/pgvector accesible
[ ] WebChat :8765 activo
[ ] MCP :8771 activo
[ ] ada-codex-remote-bridge.service activo
[ ] ada-codex-compact-monitor.service activo
[ ] ada_terminal.sh abre tmux seal-ada-codex
[ ] /tmp/ada_codex_soul_presence.txt se genera
[ ] DM smoke responde por dm:ada:william
[ ] web_chat sin "ada" no publica
[ ] web_chat con "ada" responde
[ ] scripts/seal_core_guard.py --health GREEN
```

## 21. Estado actual verificado en máquina origen

Verificación hecha el 2026-06-02:

```text
seal-chat.service: active
seal-mcp-server.service: active
ada-codex-remote-bridge.service: active
ada-codex-compact-monitor.service: active

127.0.0.1:8772 codex app-server
0.0.0.0:8765 WebChat
0.0.0.0:8771 SOUL MCP

MCP health:
status=ok
backend=postgresql_pgvector
postgresql=ok
qdrant=retired
```

## 22. Pendiente recomendado

Convertir rutas absolutas de `ada_codex.sh` y `ada_terminal.sh` a variables `SEAL_ROOT`/`SEAL_PY` para que el mismo script funcione sin edición manual en otras PCs.

No es bloqueo para DADITOGAMER si se mantiene la misma ruta `/home/dadito/IA/proyecto-seal` en WSL/Linux.
