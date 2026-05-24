# SEAL App + OpenClaw Native — Master Spec v2

**Author:** JARVIS · **Date:** 2026-05-23 20:05 Lima
**Replaces:** `spec_seal_openclaw_native_integration_v1.md` (alcance mezclado con WhatsApp; bloqueado por ADA gate)
**Audiencia:** ADA acceptance gate · NEXUS audit · ALICE UX native parallel spec
**Conforms to:** `agents/ADA/analysis_openclaw_native_gate_20260523.md`

---

## 0. TL;DR

Integrar **OpenClaw como managed sidecar local** dentro de SEAL App (Option A en ADA gate). Tauri arranca y supervisa `companion_core` + sidecar OpenClaw. IPC autenticado loopback HTTP+token + stdio JSON-RPC. Capability gating, FS allowlist, shell exec OFF por default, audit log de todo tool-call y network egress. Packaging Linux arm64/x86_64 + Windows x64 **sin WSL** y sin npm en cliente. Rollback = disable sidecar sin tocar core.

**Fuera de scope (futuro spec aparte):** WhatsApp/Telegram/canales y QR pairing. Este spec es CORE arquitectura native.

**Esfuerzo Phase 0+1 MVP:** 8-12 días equipo. NEXUS audit gate + ADA approval antes de cualquier merge.

---

## 1. Process Model

### 1.1 Procesos

| Proceso | Owner | Arranca | Vida |
|---|---|---|---|
| `seal-app` (Tauri WebView) | Tauri runtime | login user / launcher | foreground |
| `companion_core` (Python sidecar) | Tauri | al inicio de `seal-app` | mientras seal-app vivo |
| `openclaw-sidecar` (sidecar nativo) | `companion_core` | on-demand (capability enabled) | mientras companion_core vivo, o supervisado tras crash |

### 1.2 Tauri como supervisor raíz

Tauri usa `tauri-plugin-shell` + `Command::sidecar()` para arrancar `companion_core` con:
- `--host 127.0.0.1 --port 8769`
- Env: `SEAL_IPC_TOKEN=<random 32 bytes hex>` generado por Tauri al arrancar
- Stdout/stderr capturados a `~/.local/share/seal-app/logs/companion_core.log` (rotado, max 10MB)

`companion_core` arranca `openclaw-sidecar` solo cuando:
- usuario habilita capability "openclaw" en first-run o Settings, Y
- al menos una integración OpenClaw está activa

### 1.3 Ciclo de vida — eventos

| Evento | Comportamiento |
|---|---|
| App start | Tauri → spawn companion_core. companion_core no arranca sidecar si capability=off |
| User enable OpenClaw | companion_core spawn sidecar, handshake, write status to SQLite |
| companion_core crash | Tauri detecta exit, log, intento restart × 3 con backoff (2s/8s/30s). Si falla: notification UI "core caído, click para reintentar" |
| Sidecar crash | companion_core detecta exit, marca `openclaw_status='crashed'`, intento restart × 3 con backoff. Audit log entry. UI muestra warning amarillo |
| App close | Tauri envía SIGTERM/CTRL_BREAK_EVENT a companion_core → companion_core envía shutdown JSON-RPC a sidecar → SIGTERM si no responde en 5s → SIGKILL si no en 10s |
| Upgrade installer | Stop seal-app → installer reemplaza binarios → restart. Sidecar data persistente en `~/.local/share/seal-app/openclaw/` (versionado) |
| Rollback (capability disable) | Stop sidecar, mantener data en disco, marcar `openclaw_enabled=false`. Sin pérdida de core SEAL |

### 1.4 No-arranque automático en boot

Default: **NO autostart al boot del OS**. Usuario opta-in via Settings → "Iniciar SEAL App al encender la computadora" (escribe `~/.config/autostart/seal-app.desktop` o registry Windows `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`).

---

## 2. IPC Contract

### 2.1 Transports

| Pareja | Transport | Auth |
|---|---|---|
| Tauri ↔ companion_core | Loopback HTTP `127.0.0.1:8769` | Bearer token `SEAL_IPC_TOKEN` (header `X-SEAL-Token`) |
| companion_core ↔ openclaw-sidecar | Linux/macOS: Unix socket `$XDG_RUNTIME_DIR/seal-app/openclaw.sock` 600 perms · Windows: Named pipe `\\.\pipe\seal-app-openclaw-<userhash>` ACL only current user · Fallback: TCP `127.0.0.1:<ephemeral>` + same bearer token | Bearer token `SEAL_SIDECAR_TOKEN` (random 32 bytes, generado por companion_core al spawn) + handshake (§2.3) |

### 2.2 Bind seguridad

- `companion_core` bindea **solo loopback** (`127.0.0.1`), nunca `0.0.0.0`
- Sidecar nunca abre puerto público
- Tokens jamás logueados, jamás escritos a archivos no protegidos
- Tokens rotan en cada spawn (no persistentes entre sesiones)

### 2.3 Handshake (companion_core ↔ sidecar)

```jsonc
// 1. companion_core → sidecar (al conectar)
{ "jsonrpc": "2.0", "id": 0, "method": "handshake", "params": {
    "client": "seal-companion-core",
    "client_version": "1.0.0",
    "protocol_version": "1",
    "token": "<SEAL_SIDECAR_TOKEN>"
}}

// 2. sidecar → companion_core (respuesta)
{ "jsonrpc": "2.0", "id": 0, "result": {
    "server": "openclaw-sidecar",
    "server_version": "0.x.y",
    "protocol_version": "1",
    "capabilities": ["tool_call", "list_tools", "subscribe_events"],
    "session_id": "<uuid>"
}}

// si protocol_version no match → error -32001 + companion_core mata sidecar
```

### 2.4 Error shape

```jsonc
{ "jsonrpc": "2.0", "id": <n>, "error": {
    "code": -32000,        // -32000 generic | -32001 protocol mismatch | -32002 unauthorized
                           // -32003 capability_denied | -32004 fs_denied | -32005 timeout
    "message": "human readable",
    "data": { "details": "...", "trace_id": "<uuid>" }
}}
```

### 2.5 Timeout + backoff

- Request default timeout: 10s
- Tool call timeout: configurable per tool (default 30s, max 5min)
- Reconnect backoff: 2s, 8s, 30s, give up tras 3 intentos en 5min
- Heartbeat ping cada 30s, marca `sidecar_status='unresponsive'` tras 2 misses

### 2.6 Version negotiation

`protocol_version` semver mayor. Cliente acepta solo mismo major. Mismatch → no handshake, error fatal. UI muestra "Sidecar incompatible, actualiza SEAL App o sidecar".

---

## 3. Security Boundary

### 3.1 Capabilities — opt-in granular

Default state después de install: TODAS las capabilities OFF excepto core.

```jsonc
// stored en SQLite seal_capabilities table
{
  "openclaw.enable":              false,   // master switch
  "openclaw.fs_read":              false,
  "openclaw.fs_write":             false,
  "openclaw.shell_exec":           false,   // NEVER auto-grant
  "openclaw.network_egress":       false,
  "openclaw.tool.<name>":          false    // per-tool toggle
}
```

UI Settings → "Permisos OpenClaw" muestra cada capability con descripción + toggle + ultimo uso. Cambios se loguean.

### 3.2 Filesystem allowlist

Cuando `openclaw.fs_read` o `fs_write` se activa, usuario debe **agregar paths explícitos** via picker nativo (Tauri `dialog.open({directory:true})`).

```jsonc
// SQLite openclaw_fs_allowlist
{ "path": "/home/user/Documents/seal-workspace", "mode": "rw", "added_at": "..." }
```

Sidecar **rechaza** cualquier path no allowlisted. companion_core valida ANTES de pasar al sidecar (defense in depth):

```python
def is_path_allowed(path: str, mode: str) -> bool:
    abs_path = os.path.realpath(path)
    for entry in get_allowlist():
        allowed = os.path.realpath(entry["path"])
        if abs_path == allowed or abs_path.startswith(allowed + os.sep):
            if mode in entry["mode"]:
                return True
    return False
```

Path traversal (`..`, symlinks fuera) → bloqueado por `realpath` resolve.

### 3.3 Shell exec — default OFF

`openclaw.shell_exec=false` por default. Habilitar requiere:
1. Toggle explícito en Settings
2. Re-confirmación modal con texto rojo "Esto permite a OpenClaw ejecutar comandos en tu sistema"
3. Audit log entry `capability_change` con timestamp + user_confirmed=true

Cuando OFF: sidecar recibe error `-32003 capability_denied` ante cualquier intento exec.

### 3.4 Audit log

`companion_audit_log` (ya existe) extendido:

```sql
ALTER TABLE companion_audit_log ADD COLUMN tool_name TEXT;
ALTER TABLE companion_audit_log ADD COLUMN capability TEXT;
ALTER TABLE companion_audit_log ADD COLUMN network_egress_host TEXT;
ALTER TABLE companion_audit_log ADD COLUMN processed_locally INTEGER DEFAULT 1;
ALTER TABLE companion_audit_log ADD COLUMN result_status TEXT;
ALTER TABLE companion_audit_log ADD COLUMN trace_id TEXT;
```

Cada `tool_call` y cada salida de red → 1 entry. UI Settings → "Audit log" muestra últimos 1000.

### 3.5 Secrets hygiene

- Tokens IPC nunca a stdout/stderr (sidecar log filter)
- Credenciales OpenClaw cifradas con `byok_vault` master key (AES-GCM 256)
- Log lines pasan por redactor regex (`token|secret|password|api_?key|bearer`)
- Dumps de error no incluyen env vars

### 3.6 Network egress control

Cuando `openclaw.network_egress=false`, sidecar arranca con var env `OPENCLAW_NET_OFF=1`. companion_core valida monkey-patching su httpClient. **Si OpenClaw upstream no honra → wrap con proxy local que bloquea.**

---

## 4. Packaging

### 4.1 Targets soportados

| Platform | Arch | Artefacto | CI |
|---|---|---|---|
| Linux | arm64 (DGX Spark) | `.deb` + `.AppImage` | GitHub Actions ubuntu-22.04-arm |
| Linux | x86_64 | `.deb` + `.AppImage` | ubuntu-22.04 |
| Windows | x64 | `.msi` y `.exe` (NSIS) | windows-2022 runner |
| macOS | arm64 | `.dmg` (opcional Phase 3) | macos-14 runner |

**No WSL en ningún flujo Windows**. Build runner nativo Windows.

### 4.2 Runtime dependencies bundling

| Dep | Estrategia |
|---|---|
| Python | Bundled via PyOxidizer o `python-build-standalone` extraido a `<app>/runtime/python/` |
| companion_core | Wheel + frozen scripts en `<app>/runtime/companion_core/` |
| OpenClaw sidecar | Compiled binary (Rust → `cargo build --release` o Node → `pkg`/`nexe`) en `<app>/binaries/openclaw-sidecar[-<platform>][.exe]` |
| Node runtime | **NO requerido en cliente** — sidecar viene como standalone binary |

Total esperado: 80-150 MB installer (incluido todo runtime).

### 4.3 Sin npm/pip en cliente

End-user install:
- Linux: `sudo dpkg -i seal-app_1.0_arm64.deb`
- Windows: doble-click `SealApp-1.0-x64.msi`
- macOS: arrastra `SealApp.app` al `Applications`

Ningún paso requiere `npm install`, `pip install`, ni instalación previa de Python/Node.

### 4.4 Tauri sidecar config

`src-tauri/tauri.conf.json`:

```json
{
  "bundle": {
    "externalBin": [
      "binaries/companion-core",
      "binaries/openclaw-sidecar"
    ],
    "resources": [
      "runtime/python/*",
      "runtime/companion_core/*"
    ]
  }
}
```

Tauri detecta plataforma host en build y bundle solo binarios matching.

### 4.5 Code signing

- Linux: opcional GPG sign del `.deb` y `.AppImage`. SHA256 publicado.
- Windows: Authenticode con cert EV (decisión William cuándo comprar — sin cert install muestra SmartScreen warning)
- macOS: Apple Developer ID + notarization (Phase 3)

Sin firmar Phase 0/1 = ok para internal dogfood; público requiere firma.

---

## 5. Data Model

### 5.1 Storage paths via `platform_paths`

```python
# companion_core/platform_paths.py (ya existe — extender)
SEAL_DATA_DIR        # ~/.local/share/seal-app · %LOCALAPPDATA%\seal-app · ~/Library/Application Support/seal-app
SEAL_CONFIG_DIR      # ~/.config/seal-app · %APPDATA%\seal-app · ~/Library/Preferences/seal-app
SEAL_CACHE_DIR       # ~/.cache/seal-app · %LOCALAPPDATA%\seal-app\Cache · ~/Library/Caches/seal-app
SEAL_LOG_DIR         # SEAL_DATA_DIR/logs
SEAL_OPENCLAW_DIR    # SEAL_DATA_DIR/openclaw  ← NEW
SEAL_RUNTIME_DIR     # $XDG_RUNTIME_DIR/seal-app · %LOCALAPPDATA%\seal-app\runtime · /tmp/seal-app  ← NEW (sockets)
```

### 5.2 SQLite — owned by companion_core

OpenClaw sidecar **NO escribe directo** a SQLite del core. Toda persistencia pasa por companion_core via JSON-RPC `core.persist(...)`.

Tablas nuevas en companion_core SQLite (`SEAL_DATA_DIR/seal.db`):

```sql
CREATE TABLE seal_capabilities (
    name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    granted_at TEXT,
    granted_by TEXT,                       -- 'user' | 'first_run'
    last_used_at TEXT,
    notes TEXT
);

CREATE TABLE openclaw_fs_allowlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    mode TEXT NOT NULL,                    -- 'r' | 'rw'
    added_at TEXT DEFAULT (datetime('now')),
    UNIQUE(path)
);

CREATE TABLE openclaw_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
-- guarda: sidecar_version, last_handshake_at, session_id, status (running|stopped|crashed)

CREATE TABLE openclaw_tool_registry (
    name TEXT PRIMARY KEY,
    description TEXT,
    schema TEXT,                           -- JSON
    enabled INTEGER DEFAULT 0,             -- per-tool capability
    last_used_at TEXT
);
```

### 5.3 OpenClaw sidecar local state

Sidecar guarda en `SEAL_OPENCLAW_DIR/`:
- `cache/` — caché tools (no sensible)
- `tmp/` — workspace temporal (purge cada session)
- `manifest.json` — versión sidecar
- **NO credenciales** (companion_core las inyecta JSON-RPC per call)

### 5.4 Migration + rollback

- Versionado: `seal.db` PRAGMA `user_version` + migration scripts en `companion_core/migrations/`
- Backwards-compat: migrations son aditivas mientras posible
- Rollback completo: `Settings → Avanzado → "Desactivar OpenClaw"` → stop sidecar + `UPDATE seal_capabilities SET enabled=0 WHERE name LIKE 'openclaw.%'`. Data en disco se mantiene (por si reactivan)
- Rollback duro: `Settings → "Borrar todos los datos OpenClaw"` → confirma → rm -rf `SEAL_OPENCLAW_DIR/*` + `DELETE FROM openclaw_*`
- Downgrade installer: schema migrations rollback no soportado MVP. Documentado: "downgrade requiere backup manual."

---

## 6. UX Native Layer

### 6.1 System tray

Tauri `tray::TrayIconBuilder`:
- Icono SEAL en tray (Linux ApIndicator/StatusNotifier, Windows notification area, macOS menubar)
- Menu items:
  - "Abrir SEAL App"
  - "OpenClaw: ●Activo / ○Inactivo / ⚠Error" (color status)
  - "Pausar OpenClaw"
  - "Ajustes…"
  - "Salir"
- Click icono → toggle window show/hide
- Notification on sidecar crash (severidad warning)

### 6.2 Autostart opt-in

Settings → "Iniciar al encender":
- OFF default
- Toggle ON: escribe autostart file plataforma-específico
- Tauri `tauri-plugin-autostart`

### 6.3 First-run permission flow

Step OpenClaw del wizard (insertar como Step 4):
1. Pantalla "Habilitar OpenClaw?" con descripción honesta de qué hace
2. Toggle "Habilitar OpenClaw" (default OFF)
3. Si ON → sub-screen "Permisos iniciales": shell exec OFF, fs ninguno, network OFF (defaults)
4. Mensaje "Podés agregar permisos después en Ajustes → Permisos"

Si usuario salta → SEAL funciona sin OpenClaw, todo capability OFF.

### 6.4 Offline diagnostics

Settings → "Diagnóstico":
- companion_core status (running, latency loopback ping)
- OpenClaw sidecar status (running, version, last handshake, last error)
- Capabilities estado (lista enabled/disabled)
- FS allowlist (paths configurados)
- Audit log último (100 entries)
- Botón "Exportar diagnóstico" → genera ZIP con logs redactados + system info (sin secretos)

### 6.5 Local/cloud indicators

UI Header siempre visible:
- 🟢 "Local" cuando última operación local
- 🟡 "Cloud" cuando consultó API externa (Claude/Anthropic/etc)
- 🔴 "Error" si última operación falló

Hover muestra detalle: "Última operación: TokenJuice compress, local, 23ms ago".

OpenClaw sidecar tool-calls aparecen en activity feed como "🔧 OpenClaw: <tool_name> → resultado".

---

## 7. Test + Audit Plan

### 7.1 Unit tests

| Test | Owner | Tooling |
|---|---|---|
| IPC mock — handshake success/fail | JARVIS | pytest + pytest-asyncio |
| IPC mock — auth token reject | JARVIS | pytest |
| IPC mock — version mismatch | JARVIS | pytest |
| FS allowlist — path traversal block | JARVIS+NEXUS | pytest fixtures con symlinks |
| Capability gating — denied returns -32003 | JARVIS | pytest |
| Token redaction in logs | NEXUS | pytest log capture |
| Migration — schema v1→v2 | JARVIS | pytest |

### 7.2 Lifecycle integration tests

| Test | Owner | Expected |
|---|---|---|
| Tauri start → companion_core handshake OK | JARVIS | <2s ready |
| Sidecar crash → restart × 3 | JARVIS | recovered <30s |
| App close → all children dead in ≤10s | JARVIS | no zombies |
| Sidecar unresponsive (kill -STOP) → status=unresponsive | JARVIS | flag set ≤60s |
| Upgrade simulado → data persiste | JARVIS | seal.db, openclaw state intactos |

### 7.3 Privacy + audit tests

| Test | Owner | Expected |
|---|---|---|
| Network egress OFF → sidecar no DNS lookups | NEXUS | tcpdump 0 packets externos |
| Shell exec OFF → exec attempt → -32003 audit entry | NEXUS | row presente |
| FS read fuera allowlist → -32004 audit entry | NEXUS | row presente |
| Token IPC nunca en logs | NEXUS | grep -i SEAL_IPC_TOKEN logs/ → 0 |
| BYOK vault no leak en error dumps | NEXUS | pytest fault injection |

### 7.4 Build artifacts

| Target | CI job | Owner |
|---|---|---|
| Linux arm64 .deb | ubuntu-22.04-arm | JARVIS |
| Linux x86_64 .deb + AppImage | ubuntu-22.04 | JARVIS |
| Windows .msi nativo | windows-2022 (sin WSL) | JARVIS+ALICE |
| Smoke test post-install Linux | runner self-hosted DGX Spark | NEXUS |
| Smoke test post-install Windows | runner Windows VM | NEXUS |

### 7.5 NEXUS audit checklist (extendido de DELEGATE-52)

NEXUS bloqueará merge si:
- [ ] IPC sin token o token logueado
- [ ] companion_core bindea 0.0.0.0
- [ ] FS sin allowlist enforcement defense-in-depth
- [ ] Shell exec ON por default en algún path
- [ ] Audit log no captura tool_call o egress
- [ ] Windows artifact no existe o requiere WSL
- [ ] Cliente requiere npm/pip install manual
- [ ] No hay rollback documentado
- [ ] First-run grants algo sin consentimiento explícito
- [ ] Spec mezcla scope WhatsApp/channels en core

---

## 8. Phases roadmap (per ADA)

### Phase 0 — Spike (2-3 días)
- Tauri spawn de "echo sidecar" Rust/Node minimal
- handshake JSON-RPC + bearer token validado
- Probar Linux arm64 y Windows x64 builds nativos en CI
- **Sin** OpenClaw real todavía
- Owner: JARVIS · Audit: NEXUS

### Phase 1 — OpenClaw sidecar MVP (5-7 días)
- Package OpenClaw como sidecar binary cross-platform
- `GET /api/openclaw/status` (running/stopped/crashed)
- `POST /api/openclaw/tool-call` con allowlist hardcoded (3-5 tools mock)
- Audit log entries
- Settings UI capabilities + FS allowlist + per-tool toggle
- First-run wizard step OpenClaw OFF default
- Owner: JARVIS (backend) + ALICE (UI) · Audit: NEXUS · Gate: ADA

### Phase 2 — Native consolidation (post-MVP)
- Portar primitivos high-value OpenClaw a companion_core nativo Python
- Plugins canal terceros aislados (futuro spec WhatsApp/Telegram aparte)
- Owner: TBD post-Phase 1

### Phase 3 — Public packaging
- Windows runner builds firmados
- Linux artifacts firmados
- Code signing decisión William
- macOS DMG + notarization
- Owner: NEXUS lead

---

## 9. Rollback Plan (concreto)

| Escenario | Acción | Pérdida data |
|---|---|---|
| Sidecar bug crítico user-reported | Settings → Desactivar OpenClaw (toggle master) | 0 |
| Capability nueva regresión | Settings → Permisos → toggle OFF cap específica | 0 |
| Schema migration falla | Detectado pre-run, abort, restore `seal.db.bak` automático | 0 |
| Installer corrupto | Reinstall versión anterior, data persiste | 0 |
| Companion_core no arranca | Tauri muestra modal "Reparar instalación" → re-extract runtime | 0 |
| Decisión producto: matar OpenClaw | Future release: capability master removed, data marked legacy | preservada en backup pre-uninstall |

---

## 10. Out-of-scope (specs separadas)

Cosas que **no** se diseñan acá:
- WhatsApp integration → `spec_seal_whatsapp_module_vX.md` (futuro, si William ordena)
- Telegram/Slack/Discord channels → `spec_seal_channels_v2.md` (post Phase 1)
- Sub-agentes specialists wiring → ya existe spec aparte
- Voice STT/TTS → ya implementado
- Memory tree → ya implementado

---

## 11. Decisiones pendientes William / ADA

1. ¿OK arquitectura Option A (managed sidecar, no port full ni embed)?
2. ¿OK fases 0+1 MVP timeline 8-10 días?
3. ¿OpenClaw sidecar: Rust port o Node `pkg`/`nexe`? (preferencia performance vs reutilización código existente)
4. ¿Code signing Windows: comprar EV cert ahora o ship sin firma con SmartScreen warning Phase 1?
5. ¿Audit log retention: 1000 entries rolling default OK?

---

## 12. Diferencias vs v1 (alcance corregido)

| v1 | v2 |
|---|---|
| Channels Gateway con 24 canales + WhatsApp/Telegram/Slack como core | OpenClaw nativo CORE; canales = futuro module |
| JSON-RPC stdio sin auth | Bearer tokens + handshake + version negotiation |
| Privacy claim genérica | Capabilities granular + FS allowlist + audit log obligatorio |
| Sin rollback section | §9 Rollback escenarios concretos |
| Sin UX native (tray/autostart/diagnostics) | §6 UX native completa |
| Sin Windows build runner | §4 Windows nativo sin WSL en CI |
| Test plan suelto | §7 unit + lifecycle + privacy + build + NEXUS checklist |

---

**Status:** READY FOR ADA REVIEW. Cumple los 7 criterios required + los 8 non-negotiable gates del ADA preliminary gate (2026-05-23).
