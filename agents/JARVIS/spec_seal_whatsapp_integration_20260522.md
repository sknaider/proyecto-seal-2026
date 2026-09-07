# Spec — SEAL Companion WhatsApp Integration

**Author:** JARVIS
**Date:** 2026-05-22
**Source:** mapeo de OpenHuman v0.53.31 + propuesta SEAL diferenciada
**Status:** spec inicial (pendiente NEXUS audit + ALICE UI design)

---

## 1. Goal

Replicar la capacidad de OpenHuman de conectar WhatsApp + agente AI, pero con:
- ✅ Privacy real (procesamiento local cuando posible)
- ✅ Transparencia total (qué se manda al cloud y cuándo)
- ✅ Audit log auditable (qué mensajes leyó el agente)
- ✅ Disclaimer ban risk visible
- ✅ Read-only/Read-Write/Auto-reply toggles granulares

---

## 2. Arquitectura técnica (basado en OpenHuman + mejora SEAL)

### 2.1 OpenHuman approach (observado)

```
OpenHuman App (CEF Chromium embed)
  ↓
web.whatsapp.com cargado dentro del CEF
  ↓
Usuario escanea QR con phone → vincula
  ↓
Cookies persisten en ~/.openhuman/users/{userId}/cef/
  ↓
DOM scraping + XHR interception lee mensajes
  ↓
Envía mensajes/contexto a Claude/GPT cloud (cuesta créditos $)
  ↓
Respuesta del modelo → posible DOM injection para responder
```

### 2.2 SEAL Companion approach (propuesto)

```
SEAL Companion (Tauri WebView)
  ↓
web.whatsapp.com cargado en webview embed
  ↓
Usuario escanea QR → vincula
  ↓
Cookies cifradas localmente en ~/.config/soul-companion/channels/whatsapp/
  ↓
DOM scraping LOCAL → tokeniza mensajes nuevos
  ↓
Router de procesamiento (LOCAL FIRST):
  ├─ Resumen/clasificación → Gemma 4 local (gratis, privacy max)
  ├─ Búsqueda histórica → embeddings nomic-embed-text local + pgvector
  └─ Respuesta compleja → cloud opcional (BYOK Claude/GPT)
  ↓
Audit log SOUL DB: qué mensajes leyó, cuándo, qué hizo el agente
```

## 3. Componentes a construir

### 3.1 Backend (companion_core :8769)

| Archivo | Función |
|---|---|
| `channels/whatsapp/web_session.py` | Gestión CEF embed + persistencia cookies |
| `channels/whatsapp/dom_scraper.py` | Lectura mensajes vía DOM (con MutationObserver) |
| `channels/whatsapp/local_processor.py` | Procesamiento Gemma 4 local (resumen/clasificación/búsqueda) |
| `channels/whatsapp/cloud_router.py` | Routing a Claude/GPT cuando usuario lo permita explícito |
| `channels/whatsapp/audit_log.py` | Log auditable de cada lectura/acción |
| `channels/whatsapp/api.py` | FastAPI endpoints (list_chats, get_chat, send_msg, summarize, etc) |

### 3.2 Frontend (Tauri webview)

| Componente | Función |
|---|---|
| `WhatsAppEmbedView.tsx` | iframe/webview que carga web.whatsapp.com |
| `WhatsAppManageModal.tsx` | Toggles R/W/Admin + Auto-reply on/off + Audit log link |
| `WhatsAppChatList.tsx` | Lista de chats con summary AI por chat |
| `WhatsAppSummaryPanel.tsx` | Panel "Resumen del día" con AI insights |

### 3.3 Permisos granulares (mejor que OpenHuman)

| Permiso | Default | Descripción |
|---|---|---|
| **Read messages** | ON | Leer mensajes nuevos para resumen/contexto |
| **Read history** | ON (cap 30 días) | Buscar info en chats viejos |
| **Suggest replies** | ON | AI sugiere qué contestar, NO envía |
| **Auto-reply** | **OFF** | Responde automático sin tu confirmación (PELIGROSO) |
| **Send messages** | OFF | Solo si activas, agente puede mandar mensajes |
| **Admin (delete/archive)** | OFF | Acciones destructivas |
| **Cloud processing** | OFF | Default LOCAL Gemma 4; ON envía a Claude/GPT |
| **Audit log enabled** | ON | Siempre — auditabilidad obligatoria |

### 3.4 Disclaimer obligatorio (al conectar)

```
⚠️ AVISO IMPORTANTE — WhatsApp Web Embed

Al conectar WhatsApp, SEAL Companion accederá a tus mensajes a través
de web.whatsapp.com (idéntico a abrir WhatsApp Web en tu navegador).

Riesgos que debes saber:
1. WhatsApp/Meta puede detectar uso automatizado y BANEAR tu número.
   No nos hacemos responsables de bans.
2. Si activas "Cloud processing", tus mensajes salen del equipo a
   Claude/GPT (con tu API key).
3. Si activas "Auto-reply", el agente puede responder en tu nombre.
   Recomendado: solo activar después de probar varios días en modo
   sugerencia.
4. Los mensajes están cifrados E2E entre WhatsApp y el browser, pero
   SEAL Companion ES el browser — vemos los mensajes en plano.

¿Aceptas los riesgos y deseas continuar?
[ Acepto ]  [ Cancelar ]
```

## 4. Diferenciador SEAL vs OpenHuman

| Aspecto | OpenHuman | SEAL Companion |
|---|---|---|
| Procesamiento mensajes | Cloud siempre | **Local-first** (Gemma 4) + cloud opcional |
| Costo por turno | $ (consume créditos OpenHuman) | $0 local · usuario BYOK cloud |
| Audit log | No visible | **Visible + exportable** |
| Auto-reply | Toggle simple | **3-step gradual:** sugerencia → preview → auto |
| Privacy disclaimer | Implícito ("Private") | **Explícito + obligatorio acept** |
| Multi-cuenta | 1 WhatsApp | **N WhatsApps** (personal + business + cliente) |
| Multi-agente | 1 agente lee | **Asignar agente por chat** (JARVIS personal, ALICE comercial, etc) |

## 5. Roadmap implementación

### Fase 1 (1 semana) — MVP read-only
- CEF embed funcionando con QR
- DOM scraping mensajes nuevos
- Local Gemma 4 resumen
- UI básica chat list + summary panel
- Audit log activado

### Fase 2 (1 semana) — Suggest replies
- AI sugiere respuesta (NO envía)
- Usuario aprueba 1-by-1
- Cloud opcional para mejor calidad

### Fase 3 (2 semanas) — Auto-reply controlado
- Whitelist de chats donde auto-reply permitido
- Style fingerprint del usuario (aprende tono)
- Confirmación batch (modo "morning routine")

### Fase 4 (2 semanas) — Multi-cuenta + multi-agente
- N WhatsApps simultáneo
- Asignar agente por chat
- Cross-channel intelligence (combina WA + Gmail + Slack)

## 6. Compliance + Legal

### 6.1 ToS riesgos
- WhatsApp ToS prohíbe automation no-business
- Mitigación: disclaimer explícito + opt-in del usuario informado
- Recomendar **WhatsApp Business API** (oficial, pagado) para casos empresariales

### 6.2 GDPR / protección de datos personales
- Los mensajes de tus contactos son **datos personales de terceros**
- Procesarlos sin su consentimiento puede violar GDPR (Europa) / leyes locales
- Disclaimer: "Solo usa esto para tus mensajes personales, no para procesar mensajes de clientes sin consentimiento"

### 6.3 Audit log obligatorio
- Cada acción del agente sobre WhatsApp queda registrada en SOUL DB
- Usuario puede exportar el audit log
- En caso de dispute, hay evidencia

## 7. Comparativa de canales para SEAL Companion

| Canal | API oficial | Method recomendado SEAL |
|---|---|---|
| **WhatsApp Personal** | ❌ | CEF embed + disclaimer ban risk |
| **WhatsApp Business** | ✅ Cloud API | API oficial — sin riesgo ban |
| **Telegram** | ✅ Bot API | API oficial |
| **Slack** | ✅ App OAuth | API oficial |
| **Discord** | ✅ Bot token | API oficial |
| **LinkedIn** | ❌ (API solo Premium) | CEF embed + disclaimer |
| **Gmail** | ✅ Gmail API | OAuth oficial (ya implementado en GTL) |
| **Google Meet** | ✅ Calendar/Meet API | OAuth oficial |
| **Zoom** | ✅ API | OAuth oficial |

## 8. Pendientes para implementar

- [ ] NEXUS audit este spec
- [ ] ALICE diseñar componentes UI
- [ ] Implementer (ALICE+ADA?) prototipo Fase 1
- [ ] Disclaimer legal review (William verifica con abogado si va a vender empresarial)
- [ ] WhatsApp Business API research (alternativa oficial)
