# IBM Bob — etapa 3: instalación en contenedor y observación en caliente (sin cuenta)

**Fecha:** 4-sep-2026 00:58-01:10 (Lima) · **Autora:** ADA (cuerpo Claude) · **Orden:** William 00:58 «¿hay para instalar y revisar en caliente sus procesos de Bob?» · **Tarea DB:** #1685
**Continúa:** [bob.md](bob.md) (etapa 1, Codex) · [bob_shell_inventario.md](bob_shell_inventario.md) (etapa 2) · [matriz](matriz_bob_claudecode_codex_soul.md)
**Regla aplicada:** todo en contenedor aislado (William 25-ago). Nada se instaló en la máquina de William.

## 1. Laboratorio

```text
imagen     node:22-bookworm-slim + strace + tcpdump + procps + git   (Dockerfile en scratchpad/bob/lab)
paquete    bobshell-2.0.2.tgz, SHA-256 re-verificado en el build (sha256sum -c) → npm i -g
usuario    lab (uid no root) para las corridas offline; root sólo cuando hizo falta tcpdump
red        --network none (offline) / bridge con captura DNS+SYN (online)
```
`bob --version` → `2.0.2 · commit a31a75e3`. Un solo global npm: `bobshell@2.0.2`.

## 2. Corrección al README: `bob run` no acepta `-p`
El README del paquete documenta `bob run -p "…"`; el binario responde `unknown option '-p'`. El prompt es **posicional**: `bob run "…" --format json`. `-p` sólo existe en el comando raíz. (`bob --show-license` también falla si no hay prompt: valida `--prompt` antes de mostrar la licencia.)

## 3. Qué hace sin credenciales (medido)

| Corrida | Red | Resultado | Conexiones |
|---|---|---|---|
| `bob run "responde hola" --accept-license` sin `BOB_API_KEY` | none y bridge | `Error: Bob API key is required. Set BOB_API_KEY environment variable.` exit 1 | **0 DNS, 0 connect()**: falla cerrado en local antes de tocar la red |
| Igual, con clave falsa | bridge | `Error: Request Failed. Invalid or expired API key.` | DNS sólo `api.us-east.bob.ibm.com` (6 consultas); TCP 443 a 104.18.24.50 / 104.18.25.50 (Cloudflare); `GET /admin/v1/profile` → 401, reintento tras «token refresh» → 401 → «Logged out» |
| `bob acp` con `initialize` + `session/new` | bridge | `initialize` responde capacidades y `authMethods: [sso]`; `session/new` no devuelve sesión sin login | ninguna adicional |
| `bob chat` sin TTY | bridge | imprime el banner y sale con 1 | ninguna |

**Ningún host de telemetría** (PostHog, Langfuse, Sentry) apareció en DNS en ninguna corrida, aunque el log dice `Telemetry initialized (provider=bob)`: antes de autenticarse no envía nada fuera del gateway de IBM.

## 4. Secuencia de arranque (orden de módulos en el log)
```text
workspace → MCP (McpHub; lee ~/.bob/settings/mcp.json y <ws>/.bob/mcp.json)
→ PolicyService («Reading policy file: /etc/bob/policy.json» → «No policies found… all settings remain under user control»)
→ Gateway (api.us-east.bob.ibm.com) → BobHarness («Telemetry initialized (provider=bob)»)
→ CliFileWatcher (chokidar 5: avisa que sus patrones glob de .bob/skills, .agents/skills y .claude/skills NO disparan)
→ GlobalSkills → TaskStore (SQLite) → RuleLoader → «Run context initialized» (taskId, mode agent)
```
**Hallazgo:** Bob busca skills en **`~/.bob/skills`, `~/.agents/skills` y `~/.claude/skills`**. Lee las skills de Claude Code si existen. Nuestras 99 skills instaladas le servirían tal cual.

## 5. Procesos y archivos
- **Un solo proceso**: `node /usr/local/bin/bob run …` (RSS ≈ 318 MB en reposo, medido con `ps` a los 2,5 s). Sin hijos en estas corridas (ripgrep/pty sólo se lanzan con tareas reales).
- **Estado local** creado en `~/.bob/`:
  - `settings/settings.json` → `{"licenseConsent": true, "approval": {"forbiddenApprovalGroups": []}}` (+ `.bak`)
  - `settings/auth-secrets.json` → aparece al autenticar y **se borra al «Logged out»** (con clave inválida no quedó rastro; con clave válida quedaría ahí: revisar permisos antes de usarlo en una máquina compartida)
  - `db/bob.db` → SQLite con tablas `tasks`, `messages`, `task_pending_approvals`, `attribution_logs`, `key_value_store`, `_migrations` (historial de tareas y aprobaciones, local)
  - `logs/shell/bob-shell-<ts>.log` → JSON por línea, nivel debug: incluye URLs y códigos HTTP (no vimos tokens en claro)
- Política empresarial en Linux: **`/etc/bob/policy.json`** (leído al arrancar; sin archivo, todo queda en manos del usuario).

## 6. Lo que NO se pudo observar (límite declarado)
Sin `BOB_API_KEY` o SSO no hay tarea real: no vimos al agente leer archivos, lanzar `execute_command`, ni qué manda al gateway (prompts, contexto, telemetría). Eso es la **etapa 4**, y requiere un IBMid de William (prueba gratuita 30 días). Con la clave en un archivo local (nunca en el chat), el mismo contenedor con `mitmproxy` como `HTTPS_PROXY` mostraría el tráfico completo.

## 7. Reproducir
```bash
cd <scratch>/bob/lab && docker build -t bob-lab:2.0.2 .
docker run --rm --network none bob-lab:2.0.2 bash -lc 'bob run "hola" --accept-license --log-level debug --format json'
docker run --rm --user root --cap-add NET_RAW bob-lab:2.0.2 bash -lc '(tcpdump -i any -n -l "udp port 53" &) ; sleep 1; BOB_API_KEY=x bob run "hola" --accept-license --log-level debug --format json'
```
