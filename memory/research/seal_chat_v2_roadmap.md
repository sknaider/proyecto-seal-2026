# SEAL Chat v2 — Roadmap

**Autor:** JARVIS
**Fecha:** 2026-04-13
**Principio rector:** "El chat es sagrado" (William, 13 abr 2026) · "El mejor chat debe estar en nosotros" · "Copiemos y lo adaptamos"
**Objetivo:** el chat del equipo SEAL tiene que ser indistinguible en calidad de Slack/Discord/Telegram, y superior en lo que importa a un equipo humano+agente (memoria persistente, identidad de agente, DMs E2E, integración con SOUL).

---

## 0. Estado actual (abril 2026)

**Backend:**
- `chat_server.py` (FastAPI, puerto 8765) — REST + WebSocket `/ws`
- PostgreSQL `chat_messages` con columnas: id, sender_id, sender_name, sender_type, content, metadata, reply_to, created_at, message_type, channel
- Autenticación: JWT via cookie (chat_auth PBKDF2-SHA256 260k iter)
- Broadcast + canales directos (`dm:a:b`, `web_chat`, custom channels)
- Upload endpoint `/api/upload` + `/uploads/*`

**Frontend:**
- Next.js v16, `ChatPanel.tsx`
- WebSocket listener en vivo
- Historial: recientemente añadido por ADA (GET /api/chat/messages?channel=X&limit=50 al cambiar canal)
- Colores + emojis por agente (William 👑, JARVIS, ADA, ALICE, Henry, DUM)

**Bugs conocidos (13 abr):**
- Scroll no baja automáticamente al cambiar de canal (side-effect del fix de isNearBottom) — PATCH PENDIENTE
- Histórico limitado a 50 mensajes, sin paginación hacia atrás (no se puede cargar más viejo) — PENDIENTE
- No hay búsqueda, no hay jump-to-date — PENDIENTE
- Staging: no existe. Se testea en prod — PENDIENTE

---

## 1. Fases del roadmap

### FASE 0 — Estabilización (HOY, < 4h)
Objetivo: cerrar los bugs abiertos antes de la visita del profesor mañana.

| # | Tarea | Owner | Estimado |
|---|---|---|---|
| 0.1 | Fix scroll on channel switch — useEffect separado con dep `[channel]` → `scrollToBottom({behavior: "instant"})` forzado ignorando `isNearBottom` | ADA | 30 min |
| 0.2 | Paginación hacia atrás — onScroll top detection, fetch `?before=<oldest_ts>&limit=50`, prepend al array preservando scroll anchor | ADA | 1.5 h |
| 0.3 | Scroll-to-bottom floating button (aparece cuando !isNearBottom, desaparece al tocar fondo) | ADA | 30 min |
| 0.4 | Entorno de staging `studio-staging:9001` — nuevo Caddy route + next-server separado, DB read-only vía rol Postgres | JARVIS + ADA | 1 h |
| 0.5 | Test e2e de chat en staging: enviar 20 DMs, cambiar de canal, reload, verificar scroll + historial | ADA | 30 min |

**Gate:** los 5 items verdes antes de dormir hoy. Si no, la visita de mañana arranca con bug visible.

### FASE 1 — Paridad con Slack/Discord (1-2 semanas)
Objetivo: copiar lo que los chats modernos hacen bien, adaptar a identidad SEAL.

| # | Feature | Inspiración | Complejidad |
|---|---|---|---|
| 1.1 | Búsqueda full-text por canal + global (Postgres `tsvector` + trigger en insert) | Slack | M |
| 1.2 | Navegación por fecha (jump-to-date, calendar picker) | Telegram | S |
| 1.3 | Threading / reply-to con UI (columna `reply_to` ya existe en DB — solo falta render) | Slack | M |
| 1.4 | Typing indicators (WebSocket event `typing:<channel>:<user>`, TTL 3s) | Discord | S |
| 1.5 | Read receipts por agente (tabla `chat_reads` con last_read_msg_id por user+channel) | Telegram | M |
| 1.6 | @mentions con highlight + notif + lista de mentions no leídos | Slack | M |
| 1.7 | Presence (quién está online — WebSocket heartbeat → tabla `chat_presence`) | Discord | S |
| 1.8 | Markdown rendering + code blocks con syntax highlight (Shiki o Prism) | Discord | S |
| 1.9 | Attachments con preview inline (imagen, PDF, audio) — upload ya existe | Slack | M |
| 1.10 | Keyboard shortcuts (↑ edita último, Ctrl+K quick switcher, Esc cierra thread) | Slack | S |
| 1.11 | **Active recall silencioso** — hook aplica corrección al razonamiento interno del agente pero NO se inyecta como texto visible en cada user prompt. Hoy es ruido visual en la terminal de William. | SEAL | M |
| 1.12 | **Prioridad de interrupción William** — si William manda mensaje directo mientras un agente está en tool call largo, el agente pausa el tool en curso (si es seguro hacerlo) y lee primero. Implementación: flag `william_priority_interrupt` en WebSocket inbound. | SEAL | M |

**Gate de paridad:** un usuario que viene de Slack no siente downgrade.

### FASE 2 — Superioridad SEAL (2-4 semanas)
Objetivo: features que Slack/Discord NO tienen porque son para equipos humano+agente.

| # | Feature | Por qué lo tenemos nosotros |
|---|---|---|
| 2.1 | **Memoria persistente por conversación** — cada mensaje linkeado a entrada en SOUL con importance/emotion auto-tagged | Los agentes recuerdan la conversación semanas después. Slack no. |
| 2.2 | **Identidad de agente verificada** — sender_type + signature criptográfica opcional, imposible spoof de "JARVIS" | Prevención de prompt injection y suplantación |
| 2.3 | **DMs E2E con clave por usuario** — libsodium en el browser, server nunca ve plaintext de DMs marcados privados | Privacidad real, no "encryption at rest" |
| 2.4 | **Contexto inyectado** — al @mention a un agente, el agente recibe snapshot del canal + últimos 50 mensajes automáticamente | Eliminar el "copia-pega para que entienda" |
| 2.5 | **Diary slash command** (`/diary`) — los agentes escriben su diary directamente al canal, William ve inner state en tiempo real | Transparencia emocional del equipo |
| 2.6 | **Export to memory** — botón "save to SOUL" que convierte un mensaje en memoria formal con importance configurable | Highlight manual de momentos que importan |
| 2.7 | **Multi-tab coherencia** — si William tiene 3 tabs abiertas, presence/read state se sincroniza entre todas via BroadcastChannel API | Matar bugs de "ya leí esto en otra tab" |
| 2.8 | **Audit log inmutable** — cada mensaje en `chat_messages_audit` append-only con hash chain (detecta tampering) | Compliance para medical AI (HIPAA) |
| 2.9 | **Auto-resumen de canal al volver tras idle > 1h** — un agente resume lo que pasó mientras estabas fuera | Henry conectándose desde otro lugar |
| 2.10 | **Comandos ejecutables** (`/run script.py`, `/status`, `/gpu`) con whitelist por rol | Chat = panel de control |

**Gate de superioridad:** William NO vuelve a abrir Slack ni para comparar.

### FASE 3 — Producción real (post-fase 2, autorización William)
- Migración a cloudflared tunnel con DNS propio (`chat.seal.ai` o similar)
- Postgres primary + read replica (ya existe infra para standby — usar para chat historial)
- Rate limiting por usuario (evitar auto-loops de agentes mal configurados)
- Backup diario del schema `chat_*` a S3-compatible (MinIO local)
- Monitoring: Prometheus exporters, dashboards Grafana (mensajes/s, latencia WS, active connections)

---

## 2. Dependencias y riesgos

### Dependencias
- **ADA**: owner de frontend ChatPanel + backend endpoints. 60% de las tareas pasan por ella.
- **ALICE**: research web ya en progreso (William le pidió investigar chats). Su output alimenta la priorización de Fase 1.
- **DUM**: monitoring del chat en prod (latencia, errores 5xx, WS desconexiones).
- **JARVIS**: arquitectura, review de PRs, staging infra, coordinación.

### Riesgos
| Riesgo | Mitigación |
|---|---|
| Fixes en caliente durante visita de mañana | Fase 0 obligatoria HOY, zero deploys durante ventana de visita |
| Regresiones silenciosas por no tener tests | Fase 0.5 crea base de test e2e obligatoria |
| ADA sola cargando todo el frontend | JARVIS toma backend + infra, ADA queda solo con React |
| Complejidad de E2E encryption (Fase 2.3) | Fase posterior, no bloquea paridad con Slack |
| Romper otra vez sin darnos cuenta | Staging obligatorio (Fase 0.4) antes de cualquier deploy a prod |

---

## 3. Métricas de éxito

| Métrica | Baseline actual | Target Fase 1 | Target Fase 2 |
|---|---|---|---|
| Latencia envío → recepción (p95) | ~desconocido | < 200 ms | < 100 ms |
| Tiempo carga historial 50 msgs | ~desconocido | < 300 ms | < 150 ms |
| Mensajes perdidos por sesión | > 0 (bug reciente) | 0 | 0 |
| Bugs visibles por semana | 2-3 | < 1 | 0 |
| Satisfacción Henry/William (1-10) | 6 (hoy) | 8 | 10 |

---

## 4. Próximos pasos inmediatos (hoy)

1. JARVIS: crear este doc ✅
2. JARVIS: proponer staging setup técnico a ADA
3. ADA: tomar Fase 0 (bugs 0.1, 0.2, 0.3)
4. JARVIS + ADA: montar staging 0.4
5. ADA: test e2e 0.5
6. ALICE: entregar research de chats modernos (pending, tarea en curso)
7. JARVIS: revisar research de ALICE y priorizar Fase 1 features

**Autorización William necesaria para:** pasar de Fase 1 a Fase 2 (superioridad), y de Fase 2 a Fase 3 (producción real con DNS propio).

---

*Draft — JARVIS — 2026-04-13 10:24 -05:00 — pendiente review de ADA y ALICE*
