# SEAL App — OpenClaw Native Integration v1

**Author:** JARVIS · **Date:** 2026-05-23 19:58 Lima
**Scope correction:** este spec reemplaza al WhatsApp-only que entregué primero (ADA corrigió scope: William pidió OpenClaw nativo COMPLETO, no solo WhatsApp). WhatsApp pasa a ser un canal entre 24.
**Base:** absorbe `agents/ALICE/spec_seal_channels_gateway_v1.md` + agrega capa technical detail (IPC + Tauri sidecar + lifecycle + cross-platform Linux/Windows/macOS).
**Audiencia:** ADA acceptance gate + NEXUS audit DELEGATE-52.

---

## 0. TL;DR

SEAL App absorbe los **24 canales de OpenClaw** (`/home/dadito/IA/openclaw/`, MIT license) como integración nativa local-first. Channels Gateway en Python dentro de `companion_core`. Plugins canal (Node) corren como subprocess managed children. Toda data en SQLite local cifrada. Sin Composio, sin cloud 3er party.

**Esfuerzo:** 10-15 días equipo. ROI: 5× cobertura vs 5 OAuth actuales.

---

## 1. Por qué Channels Gateway native (no fork ni wrapper)

| Opción | Pro | Contra | Recomendado |
|---|---|---|---|
| Fork OpenClaw entero | 24 canales gratis | +100MB, stack TS/Node ajeno, dependencia upstream | ❌ |
| Wrap monorepo OpenClaw via subprocess | bajo esfuerzo | overhead de 24 procesos siempre vivos, RAM++ | ❌ |
| **Channels Gateway nativo + plugins on-demand** | stack Python coherente, plugins lazy-load, isolation por canal | 10-15d build | ✅ |
| Port baileys a Python | stack Python homogéneo | 3+ meses, obsoleto en semanas | ❌ |

ALICE+JARVIS coinciden en opción 3 = Channels Gateway native con plugins Node child process por canal.

---

## 2. Arquitectura

```
┌──────────────────────────────────────────────────────────────┐
│                    SEAL App (Tauri v2)                       │
│   ┌──────────────────────────────────────────────────────┐   │
│   │ UI React 19 + Vite (companion_core/ui)               │   │
│   │ ChannelsView · MessageList · QR pairing · Composer   │   │
│   └────────────────────────┬─────────────────────────────┘   │
└────────────────────────────┼─────────────────────────────────┘
                             │ HTTP/SSE
                             ▼
┌──────────────────────────────────────────────────────────────┐
│         companion_core :8769 (Python FastAPI)                │
│                                                              │
│   ┌────────────────────────────────────────────────────┐    │
│   │   SEAL Channels Gateway (módulo nuevo)             │    │
│   │   - Channel Registry (catálogo 24 canales)         │    │
│   │   - Plugin Lifecycle (spawn/restart/kill)          │    │
│   │   - Message Router (in → SQLite + sub-agents)      │    │
│   │   - JSON-RPC dispatcher                            │    │
│   └────────┬───────────┬───────────┬─────────┬────────┘    │
│            │           │           │         │             │
│      stdio │     stdio │     stdio │   stdio │             │
│            ▼           ▼           ▼         ▼             │
│      ┌────────┐  ┌─────────┐ ┌───────┐ ┌────────────┐     │
│      │whatsapp│  │telegram │ │slack  │ │... 21 more │     │
│      │child   │  │child    │ │child  │ │child procs │     │
│      │(Node)  │  │(Node)   │ │(Node) │ │(on-demand) │     │
│      └───┬────┘  └────┬────┘ └───┬───┘ └─────┬──────┘     │
│          │            │          │           │             │
│          ▼            ▼          ▼           ▼             │
│   ┌─────────────────────────────────────────────────┐     │
│   │ SQLite local: channel_accounts · channel_chats  │     │
│   │              channel_messages · channel_media   │     │
│   └─────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Plugin SDK contract (refinado de ALICE spec §2)

JSON-RPC over stdio. Plugin canal emite eventos al gateway. Gateway envía comandos al plugin.

### 3.1 Inbound (plugin → gateway)
```jsonc
{ "jsonrpc": "2.0", "method": "channel.message_received", "params": {
    "channel": "whatsapp",
    "account_id": "+51999999999",
    "chat_id": "120363xxx@g.us",
    "from": "+51888888888",
    "from_display": "Henry",
    "body": "texto",
    "timestamp": "2026-05-23T19:55:00Z",
    "type": "text|image|audio|video|doc|sticker",
    "media_url": "file:///path/local/media.jpg",
    "is_group": true,
    "raw": {...}
}}

{ "jsonrpc": "2.0", "method": "channel.pairing_status", "params": {
    "channel": "whatsapp",
    "state": "qr_pending|qr_displayed|connected|logged_out",
    "qr_png_b64": "...",
    "account_label": "+51999999999"
}}

{ "jsonrpc": "2.0", "method": "channel.presence", "params": {
    "channel": "whatsapp", "chat_id": "...", "state": "typing|online|offline"
}}
```

### 3.2 Outbound (gateway → plugin)
```jsonc
{ "jsonrpc": "2.0", "id": 1, "method": "send_message", "params": {
    "chat_id": "...", "body": "...", "media": null
}}

{ "jsonrpc": "2.0", "id": 2, "method": "start_pairing", "params": {} }
{ "jsonrpc": "2.0", "id": 3, "method": "logout", "params": {} }
{ "jsonrpc": "2.0", "id": 4, "method": "list_chats", "params": {"limit": 50} }
{ "jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": {} }
```

---

## 4. Channels Gateway — módulo Python

`companion_core/channels/` (nuevo paquete):

```
channels/
├── __init__.py
├── registry.py       # catálogo 24 canales + capabilities
├── lifecycle.py      # spawn/restart/kill plugins
├── router.py         # message_received → SQLite + sub-agent routing
├── rpc_client.py     # JSON-RPC over stdio bidirectional
├── auth.py           # cifrado credentials per canal (reusa byok_vault)
├── media_store.py    # download + storage en ~/.local/share/seal-app/channels/media/
└── plugins/
    ├── plugin_base.py     # abstract base
    └── README.md          # cómo agregar plugin nuevo
```

### 4.1 Registry — 24 canales bundleados

| ID | Name | Protocol | Auth | Phase |
|---|---|---|---|---|
| whatsapp | WhatsApp | Baileys WSS | QR pair | A |
| telegram | Telegram | Bot API | Bot token | A |
| slack | Slack | RTM + Web API | OAuth | A |
| discord | Discord | Gateway WSS | Bot token | A |
| signal | Signal | signal-cli | QR pair | B |
| imessage | iMessage | BlueBubbles bridge | macOS bridge | B (macOS only) |
| matrix | Matrix | Matrix API | OAuth/token | B |
| line | LINE | LINE Messaging API | OAuth | C |
| mattermost | Mattermost | API | OAuth/token | C |
| wechat | WeChat | wechat4u | QR pair | C |
| qq | QQ | OICQ | login | C |
| gchat | Google Chat | Google API | OAuth (reusa Google ya tenemos) | A |
| gmeet | Google Meet | Google API | OAuth | A |
| zoom | Zoom | Zoom API | OAuth | B |
| teams | Microsoft Teams | Graph API | OAuth (reusa MS365) | B |
| linkedin | LinkedIn | unofficial | OAuth/cookie | C |
| ... | 8 más OpenClaw | ... | ... | D |

**Fases:**
- **Fase A — MVP** (5 canales): WhatsApp, Telegram, Slack, Discord, Google Chat
- **Fase B**: Signal, Matrix, Zoom, Teams, iMessage (macOS only)
- **Fase C**: LINE, Mattermost, WeChat, QQ
- **Fase D**: long tail OpenClaw

### 4.2 Lifecycle — plugin manager

```python
class PluginManager:
    async def start(channel_id: str, account_config: dict) -> Plugin: ...
    async def stop(channel_id: str): ...
    async def restart(channel_id: str): ...
    async def health_check_loop(): ...  # ping every 30s
    async def auto_restart_on_crash(): ...
```

Plugins se levantan **on-demand**: solo cuando el usuario configura una cuenta del canal X. No 24 procesos siempre — solo los que están en uso.

---

## 5. Endpoints FastAPI nuevos

```
GET    /api/channels                              → catálogo 24 canales + status
GET    /api/channels/{id}/status                  → daemon up/down, accounts paired
POST   /api/channels/{id}/pair                    → arranca pairing flow
DELETE /api/channels/{id}/accounts/{account_id}   → unpair cuenta
GET    /api/channels/{id}/accounts                → cuentas configuradas
GET    /api/channels/{id}/chats                   → chats del canal
GET    /api/channels/{id}/chats/{chat_id}/messages → mensajes paginados
POST   /api/channels/{id}/send                    → enviar mensaje
GET    /api/channels/{id}/search?q=               → FTS5 sobre messages
GET    /api/channels/stream                       → SSE eventos tiempo real
```

---

## 6. Schema SQLite

```sql
CREATE TABLE channel_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    account_label TEXT NOT NULL,           -- "+51999...", "@user", etc.
    status TEXT CHECK (status IN ('disconnected','pairing','connected','error')),
    auth_payload BLOB,                     -- cifrado AES-GCM con vault master key
    metadata TEXT,                         -- JSON
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE (channel_id, account_label)
);

CREATE TABLE channel_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES channel_accounts(id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL,
    name TEXT,
    is_group INTEGER DEFAULT 0,
    last_message_at TEXT,
    unread_count INTEGER DEFAULT 0,
    metadata TEXT,
    UNIQUE (account_id, chat_id)
);

CREATE TABLE channel_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES channel_accounts(id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL,
    msg_id TEXT,
    sender TEXT NOT NULL,
    direction TEXT CHECK (direction IN ('in','out')),
    body TEXT,
    media_type TEXT,
    media_path TEXT,                       -- file:// local path
    timestamp TEXT NOT NULL,
    raw_data TEXT,                         -- JSON full payload
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (account_id, chat_id, msg_id)
);

CREATE INDEX channel_messages_recent ON channel_messages (account_id, chat_id, timestamp DESC);

CREATE VIRTUAL TABLE channel_messages_fts USING fts5(
    body, sender UNINDEXED, channel_id UNINDEXED, chat_id UNINDEXED,
    content='channel_messages', content_rowid='id'
);
```

---

## 7. Cross-platform Linux + Windows + macOS

### 7.1 Plugin binaries

Cada plugin Node se bundle con `pkg` o `nexe` a single binary per platform:
```
binaries/
├── seal-channel-whatsapp-linux-arm64
├── seal-channel-whatsapp-linux-x64
├── seal-channel-whatsapp-windows-x64.exe
├── seal-channel-whatsapp-darwin-arm64
├── seal-channel-telegram-linux-arm64
├── ... (5 plugins × 4 platforms = 20 binaries en Fase A)
```

Tamaño total Fase A: ~150-200 MB (Node runtime + plugins). Aceptable para installer.

Alternativa más liviana: **shared Node runtime + plugin sources**. ~80 MB total. Tradeoff: dependencia Node instalado en sistema (descarta).

### 7.2 IPC cross-platform

| Plataforma | Mecanismo |
|---|---|
| Linux | Unix socket `~/.config/seal-app/channels/{id}.sock` + stdio fallback |
| macOS | Unix socket `~/Library/.../channels/{id}.sock` + stdio |
| Windows | Named pipe `\\.\pipe\seal-app-channel-{id}` + stdio |

`channels/rpc_client.py` abstrae plataforma via `platform_paths.py`.

### 7.3 Tauri sidecar wiring

`src-tauri/tauri.user.conf.json`:
```json
{
  "bundle": {
    "externalBin": [
      "binaries/seal-channel-whatsapp",
      "binaries/seal-channel-telegram",
      "binaries/seal-channel-slack",
      "binaries/seal-channel-discord",
      "binaries/seal-channel-gchat"
    ]
  }
}
```

Tauri detecta plataforma host, copia correctos al bundle install.

### 7.4 Lifecycle servicios

- companion_core arranca al boot SEAL App
- companion_core arranca plugins solo si `channel_accounts.status != 'disconnected'`
- Si SEAL App se cierra → plugins se kill via SIGTERM
- Opción opt-in: `autostart=true` mantiene plugins corriendo background incluso sin SEAL App UI abierta (para recibir mensajes en tiempo real)

---

## 8. Privacy + Compliance

### 8.1 Privacy claim verificable
- Daemons 100% local — solo conectan a APIs oficiales del canal (Meta/Telegram/Slack/etc.)
- NO comunicación con servidores SEAL ni 3er party intermedios (NO Composio)
- Credenciales cifradas con master key del vault BYOK (AES-GCM 256)
- Audit log: cada operación `channel/{id}/*` → entry en `companion_audit_log` con `processed_locally=true`

### 8.2 ToS risks
- WhatsApp/iMessage: prohíben clients no oficiales → ban risk
- Signal/Matrix/Telegram/Slack/Discord: APIs oficiales, sin ban risk
- Mitigación obligatoria: disclaimer per-canal en first-pairing con riesgo específico

### 8.3 GDPR / datos terceros
- Mensajes contienen data de contactos (terceros)
- 100% local — sin upload a SEAL
- Disclaimer responsabilidad del usuario sobre sus contactos
- Botón "Borrar historial local" disponible por canal

---

## 9. Esfuerzo + fases

### Fase A — MVP 5 canales (8-10 días equipo)
- Channels Gateway Python (registry + lifecycle + router + rpc_client) — **JARVIS 3-4d**
- Schema SQLite + endpoints FastAPI — **JARVIS 1-2d**
- 5 plugins Node bundle (whatsapp/telegram/slack/discord/gchat) — **JARVIS+NEXUS 2-3d**
- UI ChannelsView + ChatView per channel — **ALICE 3-5d (parallel)**
- Pairing modals + disclaimers — **ALICE+ADA 1-2d**
- Cross-platform Tauri sidecar wiring — **NEXUS 1d**
- Tests integration + audit DELEGATE-52 — **NEXUS 1d**

### Fase B — 5 más (4-6 días)
Signal, Matrix, Zoom, Teams, iMessage(macOS)

### Fase C — 4 más (3-4 días)
LINE, Mattermost, WeChat, QQ

### Fase D — long tail (on-demand)
8 canales OpenClaw restantes

---

## 10. Test plan

| Test | Owner | Esperado |
|---|---|---|
| Gateway arranca + lista 24 canales catálogo | JARVIS | `/api/channels` count=24 |
| Plugin whatsapp se spawna on-demand | JARVIS | subprocess running, ping OK |
| Pairing flow QR end-to-end real | JARVIS | account_status=connected |
| Mensaje entrante WA → SQLite + UI live SSE | JARVIS+ALICE | <1s latency |
| Send mensaje desde UI → confirmación delivery | JARVIS+ALICE | OK |
| FTS5 search "hola" en 1000 mensajes | JARVIS | <100ms |
| Plugin crash → auto-restart | JARVIS+NEXUS | recovered <5s |
| Cross-platform Windows install | NEXUS | smoke test OK |
| Audit log entries 100% local | NEXUS | egress=0 |
| 5 canales simultáneos sin conflict | ALICE+JARVIS | OK |

---

## 11. Riesgos + mitigaciones

| Riesgo | Severidad | Mitigación |
|---|---|---|
| Meta cambia protocolo WhatsApp | Alta | Sync upstream Baileys + auto-update plugin OTA |
| Ban usuario WhatsApp/iMessage | Alta | Disclaimer + recomendar número secundario |
| Bundle size 200MB+ | Media | Aceptable (vs OpenHuman 87MB pero sin 24 canales) |
| Crash plugin = canal desconectado | Media | Watchdog + restart |
| Plugin Node dep vulnerability | Media | `pnpm audit` en cada release + bump deps |
| Compliance regional (LFPDPPP/GDPR/PIPL) | Alta | ADA + abogado IP review pre-public launch |

---

## 12. Decisiones pendientes William / ADA

1. ¿OK arquitectura Channels Gateway native + plugins Node child process?
2. ¿OK Fase A = WhatsApp/Telegram/Slack/Discord/Google Chat?
3. ¿Multi-cuenta por canal en MVP o v2?
4. ¿Auto-reply AI permitido per canal o solo opt-in per chat?
5. ¿Daemon corre solo con SEAL App abierta o systemd autostart background?
6. ¿Subset OpenClaw bundle (MIT) → SEAL App approved license-wise?

---

## 13. Comparativa esfuerzo vs ROI

| Alternativa | Esfuerzo | Cobertura |
|---|---|---|
| 5 OAuth nativos por separado (status actual) | done | 5 servicios (Gmail/GCal/GDrive/GH/Notion) |
| OpenClaw Channels Gateway Fase A | +8-10d | +5 chat (24 total potencial) |
| OpenClaw Channels Gateway full A+B+C | +15-20d | 14 chat + 5 OAuth = 19 servicios |

**ROI:** 14 chat platforms en 2-3 semanas vs construirlos 1×1 en 6+ meses.

---

## 14. Próximos pasos

1. **ADA**: accept/reject este spec, comentarios
2. **NEXUS**: audit DELEGATE-52 (compliance + arquitectura)
3. Si OK → JARVIS arranca Fase A spike (extract whatsapp + telegram plugins de OpenClaw, prueba bundle, validate IPC)
4. ALICE escribe spec UX channels en paralelo
5. Decisiones §12 con William

---

**Status:** READY FOR ADA REVIEW. Reemplaza spec anterior `spec_seal_native_whatsapp_openclaw_20260523.md` (alcance corregido: gateway 24 canales, no WhatsApp solo).
