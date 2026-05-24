# SEAL Channels Gateway — Native Port spec v1

> **ALICE — 2026-05-23 19:55 Lima** | Por orden de William: "aprovechemos esa arquitectura y lo hacemos nativa".
> Fuente: `/home/dadito/IA/openclaw/` (MIT License) + nuestro mapeo OpenHuman docs 19-23.
> Audiencia: ADA (cabeza del proyecto) para análisis y consenso.

---

## 0. Resumen ejecutivo

**Objetivo:** SEAL App lee y responde en **24 canales de comunicación** (WhatsApp, Telegram, Slack, Discord, Signal, iMessage, Matrix, etc.) de forma **nativa** dentro de `companion_core`, sin depender del proceso OpenClaw como subprocess.

**Estrategia:** Port arquitectónico, no fork ciego.
- Reusamos los **plugins de canal** de OpenClaw (`@openclaw/whatsapp`, `@openclaw/telegram`, etc.) como dependencias npm puntuales
- Reescribimos el **gateway/orchestrator** en Python nativo dentro de companion_core
- Cada plugin canal corre como **child process Node managed por companion_core** (mínimo runtime overhead, máxima reusabilidad de código probado)

**Licencias compatibles:** OpenClaw es **MIT** (Peter Steinberger 2025). Apache 2.0 SEAL puede consumir MIT sin contaminación.

**Esfuerzo total estimado:** ~10-15 días equipo coordinado (vs ~10-12d sólo para los 7 connectors que mostró William).

**ROI:** 24 channels vs los 5 OAuth nativos que tenemos hoy. 5× la cobertura por 1.3× el costo.

---

## 1. Arquitectura propuesta

```
┌──────────────────────────────────────────────────────────────┐
│                    SEAL App (Tauri)                          │
│   ┌──────────────────────────────────────────────────────┐   │
│   │              UI (React 19 + Vite)                    │   │
│   │  ChannelsView · ChatView per channel · QR pairing    │   │
│   └────────────────────────┬─────────────────────────────┘   │
└────────────────────────────┼─────────────────────────────────┘
                             │ HTTP REST
                             ▼
┌──────────────────────────────────────────────────────────────┐
│              companion_core :8769 (Python FastAPI)           │
│                                                              │
│   ┌────────────────────────────────────────────────────┐    │
│   │   SEAL Channels Gateway (NEW — Python native)      │    │
│   │                                                    │    │
│   │   ┌──────────────┐  ┌──────────────┐  ┌──────────┐│    │
│   │   │ Channel      │  │ Plugin       │  │ Message  ││    │
│   │   │ Registry     │  │ Lifecycle    │  │ Router   ││    │
│   │   └──────────────┘  └──────────────┘  └──────────┘│    │
│   │                                                    │    │
│   │   ┌──────────────────────────────────────────────┐│    │
│   │   │  Plugin SDK contract (JSON RPC over stdio)   ││    │
│   │   └──────────────────────────────────────────────┘│    │
│   └────────────┬─────────────┬─────────────┬──────────┘    │
│                │             │             │               │
│                ▼             ▼             ▼               │
│       ┌─────────────┐ ┌─────────────┐ ┌──────────────┐   │
│       │ whatsapp    │ │ telegram    │ │ slack        │   │
│       │ child proc  │ │ child proc  │ │ child proc   │   │
│       │ (Node)      │ │ (Node)      │ │ (Node)       │   │
│       │             │ │             │ │              │   │
│       │ Baileys     │ │ Telegram    │ │ Slack OAuth  │   │
│       │ via         │ │ Bot API     │ │ + RTM        │   │
│       │ @openclaw/* │ │ @openclaw/* │ │ @openclaw/*  │   │
│       └─────────────┘ └─────────────┘ └──────────────┘   │
│                                                            │
│   ┌────────────────────────────────────────────────────┐  │
│   │   SQLite — channel_messages + channel_chats       │  │
│   │              + channel_accounts                    │  │
│   └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘

Cada canal ↔ SEAL Sub-agents (orchestrator/critic/etc.)
Cada mensaje recibido → ingesta memory_tree + sub-agent routing
```

---

## 2. Plugin SDK contract (JSON-RPC over stdio)

Cada plugin canal (child process Node) implementa:

### Inbound (plugin → gateway)
```jsonc
// Message received on channel
{ "jsonrpc": "2.0", "method": "channel.message_received", "params": {
    "channel": "whatsapp",
    "account_id": "+51999999999",
    "chat_id": "120363...@g.us",
    "from": "+51888888888",
    "from_display": "Henry",
    "body": "Hola alice, ¿cómo va?",
    "timestamp": "2026-05-23T19:55:00Z",
    "type": "text",
    "media_url": null,
    "is_group": true
}}

// Pairing status updates
{ "jsonrpc": "2.0", "method": "channel.pairing_status", "params": {
    "channel": "whatsapp",
    "state": "qr_pending" | "qr_displayed" | "connected" | "logged_out",
    "qr_payload": "...base64 png..." // si qr_displayed
}}

// Channel health
{ "jsonrpc": "2.0", "method": "channel.heartbeat", "params": {
    "channel": "...", "uptime_s": 1234, "msg_rate_per_min": 3.2
}}
```

### Outbound (gateway → plugin)
```jsonc
// Send a message
{ "jsonrpc": "2.0", "id": 42, "method": "channel.send_message", "params": {
    "chat_id": "+51888888888",
    "body": "Hola Henry, ya respondí en SEAL.",
    "reply_to": "msg_abc123" // optional
}}

// Pair / unpair
{ "jsonrpc": "2.0", "id": 43, "method": "channel.start_pairing", "params": {}}
{ "jsonrpc": "2.0", "id": 44, "method": "channel.logout", "params": {}}

// Sync request (full backfill)
{ "jsonrpc": "2.0", "id": 45, "method": "channel.sync_history", "params": {
    "since": "2026-05-22T00:00:00Z",
    "chats": ["all"] | ["specific_chat_id"]
}}
```

### Plugin manifest (`plugin.json` por canal)
```json
{
  "name": "@seal/channel-whatsapp",
  "version": "0.1.0",
  "channel_id": "whatsapp",
  "display_name": "WhatsApp",
  "icon": "whatsapp.svg",
  "capabilities": ["text", "media", "groups", "pairing_qr"],
  "auth_kind": "qr_scan",
  "depends_on": ["@openclaw/whatsapp@^2026.5.6"],
  "risk_tier": "critical"
}
```

---

## 3. Schema SQLite (companion.db)

```sql
-- Cuentas pareadas (1 SEAL puede tener varias cuentas WhatsApp/Telegram/etc)
CREATE TABLE channel_accounts (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,                   -- 'whatsapp', 'telegram', etc.
    account_id TEXT NOT NULL,                -- '+51999...', '@username', etc.
    display_name TEXT,
    paired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat TIMESTAMP,
    state TEXT DEFAULT 'connected',          -- 'connected'/'qr_pending'/'logged_out'
    metadata TEXT,                           -- JSON
    UNIQUE(channel, account_id)
);

-- Chats (DMs y grupos)
CREATE TABLE channel_chats (
    id INTEGER PRIMARY KEY,
    account_pk INTEGER REFERENCES channel_accounts(id),
    chat_id TEXT NOT NULL,                   -- ID nativo del canal
    display_name TEXT,
    is_group BOOLEAN DEFAULT 0,
    last_message_at TIMESTAMP,
    message_count INTEGER DEFAULT 0,
    pinned BOOLEAN DEFAULT 0,
    metadata TEXT,
    UNIQUE(account_pk, chat_id)
);

-- Mensajes
CREATE TABLE channel_messages (
    id INTEGER PRIMARY KEY,
    chat_pk INTEGER REFERENCES channel_chats(id),
    msg_native_id TEXT,                      -- ID original del canal
    sender_id TEXT,                          -- número/username del sender
    sender_display TEXT,
    direction TEXT CHECK(direction IN ('inbound', 'outbound')),
    body TEXT,
    body_type TEXT DEFAULT 'text',           -- 'text','image','audio','video','document'
    media_local_path TEXT,                   -- si descargado
    media_url TEXT,                          -- si remoto
    reply_to_msg_id INTEGER REFERENCES channel_messages(id),
    timestamp TIMESTAMP NOT NULL,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT
);

CREATE INDEX idx_messages_chat_ts ON channel_messages(chat_pk, timestamp DESC);

-- FTS5 sobre body para búsqueda
CREATE VIRTUAL TABLE channel_messages_fts USING fts5(
    body, sender_display, chat_display,
    content='channel_messages', tokenize='unicode61'
);

-- Triggers para mantener FTS5 sincronizado
CREATE TRIGGER channel_messages_ai AFTER INSERT ON channel_messages BEGIN
  INSERT INTO channel_messages_fts(rowid, body, sender_display, chat_display)
  SELECT new.id, new.body, new.sender_display, c.display_name
  FROM channel_chats c WHERE c.id = new.chat_pk;
END;
```

---

## 4. REST API (companion_core endpoints nuevos)

```
GET    /api/channels                          # lista canales soportados + status
GET    /api/channels/{channel}/accounts       # cuentas pareadas
POST   /api/channels/{channel}/pair           # inicia pairing → devuelve qr o oauth_url
POST   /api/channels/{channel}/logout         # desconecta una cuenta

GET    /api/channels/{channel}/chats          # lista de chats del canal
GET    /api/channels/{channel}/chats/{chat_id}/messages?limit=50&before=...
POST   /api/channels/{channel}/chats/{chat_id}/send  { body }

GET    /api/channels/search?q=...&channel=&from_date=&to_date=
                                              # búsqueda cross-channel via FTS5

# Mensajes inbound son push: el plugin escribe directo a DB y emite WS event

WS     /api/channels/stream                   # WebSocket — mensajes en vivo
```

---

## 5. UI surface — Views React

### 5.1 `ChannelsView` (nuevo tab "Canales")
- Grid de canales soportados (24 cards con ícono brand)
- Por cada canal: status (conectado/desconectado/QR pendiente), accounts count
- Click "Conectar" → modal pairing (QR para WhatsApp, OAuth para Slack, etc.)

### 5.2 `InboxView` (mejora del Chat existente)
- 3-column layout (como OpenHuman):
  - **Izq**: lista de chats agregados de TODOS los canales (filter por canal, search)
  - **Centro**: mensajes del chat seleccionado
  - **Der**: detalles (contacto, archivos compartidos, sub-agente sugerido)

### 5.3 `PairingFlow` componente reutilizable
- Tab QR (WhatsApp/iMessage)
- Tab OAuth (Slack/Discord/Telegram con bot)
- Tab Token (Telegram bot token manual, Matrix access token)

---

## 6. Privacy & Capabilities (review ADA)

Nuevas capabilities en `cap_*` con risk tier:

```python
NEW_CAPABILITIES = [
    ("cap_channel_whatsapp_read",  "critical", "Lee mensajes de WhatsApp"),
    ("cap_channel_whatsapp_write", "critical", "Responde en WhatsApp en tu nombre"),
    ("cap_channel_telegram_read",  "critical", "Lee mensajes de Telegram"),
    ("cap_channel_telegram_write", "critical", "Responde en Telegram"),
    ("cap_channel_slack_read",     "high",     "Lee mensajes Slack"),
    ("cap_channel_slack_write",    "high",     "Responde en Slack"),
    # ... (24 canales × 2 = 48 capabilities)
    ("cap_channels_cross_search",  "normal",   "Busca a través de todos tus canales"),
]
```

Riesgos a flaggear:
- ⚠️ WhatsApp/iMessage usan APIs no-oficiales → riesgo de ban de cuenta
- ⚠️ Disclaimer legal explícito en pairing UI antes de conectar
- ⚠️ Audit log captura cada mensaje enviado en nombre del usuario (sin body, solo metadata)
- ⚠️ Capability per canal — no se prende todo de un saque

---

## 7. Migración de OpenClaw (estrategia step-by-step)

### Fase 1 — Spike + 1 canal (3 días)
- ALICE: spec firmado por ADA + ChannelsView mock UI
- JARVIS: gateway Python + plugin SDK contract documentado + 1 plugin Node spawning WhatsApp
- NEXUS: audit del spike, validar contract estable
- **Entregable**: SEAL App recibe y responde 1 mensaje de WhatsApp test, persistido en companion.db

### Fase 2 — 5 canales prioritarios (4 días)
WhatsApp + Telegram + Slack + Discord + Signal
- 5 plugins child processes coordinados por gateway
- UI ChannelsView con grid + pairing flows

### Fase 3 — 5 canales secundarios (3 días)
Matrix + iMessage + MS Teams + LINE + WeChat

### Fase 4 — InboxView 3-column unificado (2 días)
Cross-channel search + sub-agent routing por mensaje

### Fase 5 — Polish + audit (3 días)
NEXUS DELEGATE-52 final + .deb rebuild + ADA acceptance + tests

**Total: ~15 días equipo coordinado**

---

## 8. Decisiones técnicas que ADA debe validar

1. **Node child process vs port puro Python**:
   - **Opción A (recomendada)**: child Node por plugin reusa @openclaw/* compilado. Pro: 90% de código maduro y testeado. Con: dependencia npm + Node runtime ~50MB extra en el .deb.
   - **Opción B**: portar todo a Python (Baileys-py existe pero menos maduro, Telegram tiene aiogram, etc.). Pro: 0 deps Node. Con: ~30d trabajo adicional.

2. **Storage local vs cloud sync**:
   - Local SQLite por defecto (consistente con privacy local-first)
   - Future: opcional encrypted backup a S3/Drive del usuario (NUNCA a server SEAL)

3. **Cada plugin = child process separado vs 1 monolito Node**:
   - **Separado**: isolation, crash de WhatsApp no afecta Telegram. +RAM (1 Node × N canales = 50MB × N).
   - **Monolito**: 1 proceso Node con todos los plugins cargados. -RAM (50MB único). Crash mata todo.
   - **Recomiendo separado** por estabilidad.

4. **Encryption at rest**:
   - Mensajes en SQLite plain (consistente con resto del data SEAL)
   - O cifrar `channel_messages` con clave derivada de BYOK vault (más fuerte, +complejidad)
   - **ADA decide.**

5. **Rate limits & spam protection**:
   - Throttle outbound (max 10 msg/min por canal default)
   - Detect "responding from SEAL" loop (no responder a sí mismo)

---

## 9. Riesgos & mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| WhatsApp ban por TOS | Disclaimer explícito + capability opt-in 🔴 |
| Plugin Node crashea repetidamente | Watchdog en gateway, restart con exponential backoff, alerta UI |
| Volumen alto saturar SQLite | Memory tree h→d→m→y para compress histórico + archive table |
| Multi-account confusion (2 WhatsApp del usuario) | Schema soporta múltiples accounts por canal |
| Update @openclaw/whatsapp rompe API | Lock version + tests integración |
| User responde a sí mismo (echo) | Filtro `from_me=true` no se route a sub-agentes |

---

## 10. Para ADA — preguntas explícitas de consenso

1. ¿Path Node child process (recomendado) o port puro Python?
2. ¿Encryption at rest sobre channel_messages: sí o no?
3. ¿Capability granular per canal o single `cap_channels`?
4. ¿Empezamos por WhatsApp (más demandado) o por uno OAuth-puro (Slack, menos riesgo) para validar arquitectura?
5. ¿La UI canales se queda en SEAL App o se libera como producto separado?

---

## 11. Archivos de referencia

- `/home/dadito/IA/openclaw/` — repo MIT clonado
- `/home/dadito/IA/openclaw/docs/channels/whatsapp.md` — pairing doc
- `/home/dadito/IA/openclaw/packages/plugin-sdk/` — SDK base reusable
- `/home/dadito/IA/openclaw/extensions/whatsapp/` — plugin WhatsApp implementación
- `/agents/ALICE/docs/openhuman_replication/v2/20_whatsapp_qr_login.md` — pairing UX referencia
- `/agents/ALICE/docs/openhuman_replication/v2/21_whatsapp_ai_assistance.md` — pipeline AI

---

**Status:** DRAFT v1 para review ADA.
**Owner:** ALICE (UI + spec) · JARVIS (backend) · ADA (gate) · NEXUS (audit)
**Next:** ADA analiza, marca con ACK/CHANGES/REJECT cada decisión técnica.

— ALICE, 2026-05-23 19:58 Lima
