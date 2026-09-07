# SEAL App — Integración Nativa WhatsApp + OpenClaw

**Spec author:** JARVIS · **Date:** 2026-05-23 19:55 Lima
**Spec target:** ADA (acceptance gate) · NEXUS (audit) · ALICE (UX side spec)
**Status:** propuesta inicial — pendiente revisión

---

## 0. Objetivo

Aprovechar la arquitectura OpenClaw (`/home/dadito/IA/openclaw/`, MIT) para que SEAL App lea/escriba WhatsApp 100% **localmente y privadamente**, sin cloud 3er party (Composio/Twilio/etc.) y sin browser externo. Debe correr nativo en Linux + Windows con la misma codebase.

Definición operativa de "nativo":
- ❌ No usa browser externo (Chrome/Brave/Firefox separados)
- ❌ No usa Composio ni intermediarios cloud
- ❌ No deja tokens en servicios de terceros
- ✅ Usa procesos locales bundled en la instalación SEAL App
- ✅ Webview embed solo si es UI (no auth/scraping crítico)
- ✅ Toda data en SQLite local cifrada

---

## 1. Estudio de OpenClaw WhatsApp

### 1.1 Stack observado
- TypeScript ESM / Node 24 / pnpm monorepo
- License **MIT** (Peter Steinberger 2025) → permite copy/adapt/redistribute con atribución
- Dependencia clave: `@whiskeysockets/baileys 7.0.0-rc.9` — implementación pura del protocolo WhatsApp Web vía WebSockets (NO usa Puppeteer ni browser real)
- Helpers: `jimp` (imagen), `typebox` (validación), `undici` (HTTP), `https-proxy-agent`

### 1.2 Por qué Baileys (no scrape DOM)
- Conecta directamente al protocolo WhatsApp Web — más rápido + más estable
- Sin browser overhead (RAM/CPU bajísimo vs Puppeteer)
- Auth via QR pairing como WhatsApp Web normal
- Soporte multi-device, media (audio/imagen/video/docs), grupos, status
- Reverse-engineered (Meta no lo aprueba oficialmente — ToS risk)

### 1.3 Arquitectura de extensión
`/extensions/whatsapp/` (35+ archivos .ts):
- `channel.ts` — entry point: setup + lifecycle
- `auth-store.ts` — credenciales cifradas en disco (sesión persistente)
- `login-qr-runtime.ts` — emite QR para pareo inicial
- `auto-reply.ts` — handler mensajes entrantes
- `channel-actions.ts` — send/receive/list chats/contacts
- `action-runtime.ts` — eventos en tiempo real

---

## 2. Por qué NO portamos baileys a Python

Análisis técnico:
- Baileys es **~50k LOC TypeScript** con criptografía proprietary de WhatsApp (signal-protocol + curves + custom encoding)
- Meta actualiza el protocolo con frecuencia → cualquier port se desactualiza en semanas
- Mantenedores Baileys son activos (last commit días) → seguir su pace en Python = full-time team
- No existe equivalente Python production-grade:
  - `pywa` → solo API oficial Cloud (no Web personal accounts)
  - `whatsapp-api-python` → wrapper sobre Selenium (lento, frágil, fácil ban)
  - `yowsup` → abandonado 2021

**Conclusión:** intentar port Python rompe nuestra velocidad de iteración y queda obsoleto en 1-2 meses.

---

## 3. Arquitectura propuesta: Node Sidecar Daemon

### 3.1 Resumen

```
┌────────────────────────────────────────────────────────────────┐
│                  SEAL App (Tauri v2 binary)                    │
│  ┌────────────────────────────────────────────────────────┐    │
│  │  UI React + Vite (companion_core/ui)                   │    │
│  │  WhatsAppView.tsx ←──── HTTP/WS ──┐                    │    │
│  └────────────────────────────────────┼───────────────────┘    │
│                                       │                        │
│  ┌────────────────────────────────────▼───────────────────┐    │
│  │  companion_core (Python FastAPI :8769)                 │    │
│  │  /api/whatsapp/* endpoints                             │    │
│  │  SQLite local: whatsapp_chats + whatsapp_messages      │    │
│  └────────────────────┬───────────────────────────────────┘    │
│                       │ JSON-RPC                               │
│                       │ unix socket / localhost:7787           │
│                       ▼                                        │
│  ┌────────────────────────────────────────────────────────┐    │
│  │  seal-wa-daemon (Node sidecar)                         │    │
│  │  Bundled from openclaw whatsapp extension              │    │
│  │  Baileys → encrypted local credentials (~/.config/...) │    │
│  └────────────────────┬───────────────────────────────────┘    │
└─────────────────────── │ ──────────────────────────────────────┘
                         │ WSS
                         ▼
                  web.whatsapp.com (Meta)
```

### 3.2 Sidecar: `seal-wa-daemon`

- Carpeta nueva: `seal-desktop/seal-wa-daemon/`
- Subset de OpenClaw extensions/whatsapp + minimal openclaw runtime (sin todo el monorepo)
- Bundled con `pkg` o `nexe` a single Node binary (~30-50 MB)
- Cross-platform: `seal-wa-daemon-linux-arm64`, `seal-wa-daemon-windows-x64.exe`, `seal-wa-daemon-darwin-arm64`
- Lifecycle: companion_core arranca con `subprocess.Popen` al startup; lo monitorea + restart on crash
- IPC: JSON-RPC sobre unix socket Linux/macOS, named pipe Windows, fallback TCP 127.0.0.1:7787

### 3.3 companion_core endpoints nuevos (Python)

```
GET    /api/whatsapp/status              → daemon up/down, paired/unpaired, account info
POST   /api/whatsapp/pair                → solicita QR (devuelve PNG b64)
DELETE /api/whatsapp/unpair              → desvincula sesión
GET    /api/whatsapp/chats               → lista chats (cached desde SQLite)
GET    /api/whatsapp/chats/{id}/messages → mensajes paginados
POST   /api/whatsapp/send                → envía mensaje (proxy a daemon)
POST   /api/whatsapp/search              → FTS5 sobre whatsapp_messages
GET    /api/whatsapp/contacts            → lista contactos
DELETE /api/whatsapp/chats/{id}          → borra historial local (no en WA)
```

### 3.4 Schema SQLite (companion_core local)

```sql
CREATE TABLE whatsapp_chats (
    chat_id TEXT PRIMARY KEY,         -- "5491155556666@s.whatsapp.net"
    name TEXT NOT NULL,
    is_group INTEGER NOT NULL DEFAULT 0,
    last_message_at TEXT,
    unread_count INTEGER NOT NULL DEFAULT 0,
    avatar_url TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE whatsapp_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    msg_id TEXT,                       -- WhatsApp internal msg id
    sender TEXT NOT NULL,
    direction TEXT CHECK (direction IN ('in','out')),
    body TEXT,
    media_type TEXT,                   -- 'image' | 'video' | 'audio' | 'doc' | 'sticker' | NULL
    media_url TEXT,                    -- file:// path local (descargado por daemon)
    timestamp TEXT NOT NULL,
    raw_data TEXT,                     -- JSON full payload del daemon
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (chat_id) REFERENCES whatsapp_chats(chat_id) ON DELETE CASCADE,
    UNIQUE (chat_id, msg_id)
);

CREATE INDEX whatsapp_messages_chat_idx ON whatsapp_messages (chat_id, timestamp DESC);

CREATE VIRTUAL TABLE whatsapp_messages_fts USING fts5(
    body, sender UNINDEXED, chat_id UNINDEXED,
    content='whatsapp_messages', content_rowid='id'
);
```

Media binaries (imágenes/audio/video) → `~/.local/share/seal-app/whatsapp/media/` con permisos 0600.

---

## 4. Integración nativa Tauri (companion_core sidecar)

### 4.1 Tauri sidecar configuration

`src-tauri/tauri.user.conf.json`:
```json
{
  "bundle": {
    "externalBin": [
      "binaries/seal-wa-daemon"
    ]
  }
}
```

Tauri se encarga de:
- Detectar plataforma host y copiar el binary correcto
- Permisos ejecución correctos en macOS (codesign)
- Path resolution para `subprocess` invocation

### 4.2 Lifecycle companion_core

`companion_core/wa_daemon.py` (módulo nuevo):
```python
async def start_daemon():
    """Launch sidecar at startup, register on shutdown."""
async def health_check_loop():
    """Ping every 10s, restart if down."""
async def call_daemon(method: str, params: dict) -> dict:
    """JSON-RPC client over unix socket / named pipe."""
```

### 4.3 Eventos en tiempo real

Daemon emite WhatsApp events (mensaje nuevo, presencia, typing) → companion_core los recibe vía WebSocket persistente → propaga al UI via SSE (`/api/whatsapp/stream`) → UI actualiza en vivo.

---

## 5. Cross-platform: Linux + Windows + macOS

| Capa | Linux | Windows | macOS |
|---|---|---|---|
| Daemon binary | `seal-wa-daemon-linux-${arch}` | `seal-wa-daemon-windows-x64.exe` | `seal-wa-daemon-darwin-${arch}` (codesign) |
| IPC | Unix socket `~/.config/seal-app/wa.sock` | Named pipe `\\.\pipe\seal-app-wa` | Unix socket |
| Auth dir | `~/.config/seal-app/whatsapp/` | `%APPDATA%\seal-app\whatsapp\` | `~/Library/Application Support/seal-app/whatsapp/` |
| Media dir | `~/.local/share/seal-app/whatsapp/media/` | `%LOCALAPPDATA%\seal-app\whatsapp\media\` | `~/Library/Application Support/seal-app/whatsapp/media/` |

Todo abstraído por `platform_paths.py` que ya tenemos.

---

## 6. Privacy + Compliance

### 6.1 Privacy claim verificable
- Daemon corre 100% local — NO comunicación con servidores SEAL ni 3er party
- WSS solo a `web.whatsapp.com` (Meta) — mismo que WhatsApp Web del browser
- Credenciales auth: `~/.config/seal-app/whatsapp/creds.json` cifradas con master key del vault BYOK existente (AES-GCM)
- Audit log: cada operación (mensaje enviado, recibido, sincronización) escribe `companion_audit_log` channel='whatsapp' processed_locally=true

### 6.2 ToS risk
- WhatsApp prohíbe clients no oficiales en su Terms of Service
- Riesgo: Meta puede banear el número WhatsApp del usuario
- Mitigación obligatoria: **disclaimer explícito en first-pairing modal**:

> *"WhatsApp prohíbe en sus términos el uso de clientes no oficiales. Vincular tu número aquí usa el mismo protocolo que WhatsApp Web pero implementado localmente. Meta puede banear tu número WhatsApp si detecta uso automatizado. Recomendado: usar un número secundario, no tu número principal. ¿Aceptás el riesgo?"*

- Tasa de baneo observada por OpenClaw community: <1% para uso humano normal, ~10-30% para spam/automation visible

### 6.3 GDPR / datos terceros
- Los mensajes contienen data de contactos del usuario (terceros)
- Solo se almacenan localmente — no se envían a SEAL servers
- Disclaimer adicional en spec UX (ALICE) sobre responsabilidad del usuario respecto a su contactos

---

## 7. Esfuerzo + roadmap

### Fase 1 — Spike técnico (2-3 días) — JARVIS
- Subset minimal de OpenClaw whatsapp + dependencias
- Bundle con `pkg` o `nexe` a single executable
- Test pairing real con número personal
- IPC JSON-RPC mínimo (auth/status/list_chats)

### Fase 2 — Schema + endpoints (1-2 días) — JARVIS
- Schemas SQLite (este spec §3.4)
- 9 endpoints (este spec §3.3)
- Tests pytest cubriendo CRUD + search FTS5

### Fase 3 — Daemon lifecycle + monitoring (1 día) — JARVIS+NEXUS
- `wa_daemon.py` start/stop/restart/health
- Logs estructurados
- Crash recovery

### Fase 4 — UI WhatsApp view (3-5 días) — ALICE
- ChatList component
- Message thread + media preview
- Pairing QR modal
- Search inline
- Send composer

### Fase 5 — Cross-platform binaries (1-2 días) — NEXUS
- Build matrix Linux arm64/amd64 + Windows x64 + macOS arm64
- Tauri sidecar wiring
- Empaquetado .deb / .msi / .dmg

### Fase 6 — Disclaimer legal + audit (1 día) — ADA review
- Disclaimer pairing
- Audit log integration
- Privacy policy update

### Total: 9-14 días equipo SEAL (en paralelo)

---

## 8. Test plan

| Test | Owner | Status esperado |
|---|---|---|
| Daemon arranca + responde ping | JARVIS | green |
| Pairing QR real con número de prueba | JARVIS | green |
| Recibir 10 mensajes de prueba | JARVIS | persisted en SQLite |
| Enviar mensaje desde companion_core API | JARVIS | recibido en WA real |
| FTS5 search "hola" devuelve hits | JARVIS | match correcto |
| Daemon crash → restart automático | JARVIS+NEXUS | recovered <5s |
| UI WhatsAppView renderiza chats | ALICE | OK |
| Pairing modal disclaimer obligatorio | ALICE+ADA | aceptación requerida |
| Cross-platform Windows install | NEXUS | smoke test OK |
| Audit log entries en operaciones | NEXUS audit | local=N egress=0 |

---

## 9. Riesgos + mitigaciones

| Riesgo | Severidad | Mitigación |
|---|---|---|
| Meta cambia protocolo WhatsApp → daemon roto | Alta | seguir Baileys upstream + auto-update daemon binary OTA |
| Usuario es baneado por Meta | Alta | disclaimer explícito + recomendar número secundario |
| Crash daemon = WhatsApp se desconecta | Media | Watchdog en companion_core con restart automático |
| Binary daemon Node = +50MB en bundle final | Media | Aceptable; bundle todavía <100MB final |
| Audit / certificación legal pre-launch | Alta | ADA review + abogado IP review pre-public launch |

---

## 10. Por qué NO usar Composio aquí

- Composio guarda los tokens en SUS servidores (cloud) → viola privacy local-first
- Composio cobra (free tier limitado, paid después)
- Composio no soporta WhatsApp personal (solo Business API)
- Composio rompe nuestra narrativa #1 vs OpenHuman

---

## 11. Por qué NO usar embed WebView WhatsApp Web

- Requiere Chromium/WebKit corriendo dentro de Tauri (RAM++ CPU++)
- DOM scrape es frágil — Meta cambia HTML frecuentemente
- Cookies persistencia frágil en sandbox Tauri
- No tiene control sobre eventos (presence/typing/read receipts)
- Baileys es ~10x más estable + más rápido

---

## 12. Decisiones pendientes (William / ADA)

| # | Pregunta | Owner |
|---|---|---|
| 1 | ¿Approved subset OpenClaw bundle (MIT) → SEAL App? | William |
| 2 | ¿Multi-cuenta (N WhatsApps simultáneos) en MVP o v2? | William |
| 3 | ¿Auto-reply AI permitido en MVP? (puede ser bot-like, más ban risk) | William |
| 4 | ¿Daemon corre como systemd user service o solo cuando SEAL App está abierta? | ADA |
| 5 | ¿Spec WhatsApp Business API paralela para usuarios enterprise? | William |
| 6 | ¿Telegram + Discord + Slack también via OpenClaw (24 canales) en mismo spec? | William |

---

## 13. Próximas acciones inmediatas

1. **ADA**: analiza este spec — accept / reject / cambios
2. **NEXUS**: audit DELEGATE-52 del spec (compliance + arquitectura)
3. Si approved → JARVIS arranca Fase 1 (spike técnico bundle daemon)
4. ALICE escribe spec UX paralelo
5. Decisiones §12 con William

---

**Status:** READY FOR ADA REVIEW
