# SEAL Remote Desktop — Spec Arquitectural v1.0
**Autor:** JARVIS | **Fecha:** 2026-04-23 | **Estado:** BORRADOR

---

## 1. Visión del Producto

**Nombre provisional:** SEAL Remote Desktop (SRD)
**Base:** RustDesk (AGPL v3, fork self-hosted)
**Diferenciador clave:** Acceso remoto con identidad de agente IA — autenticación, auditoría y asistencia en tiempo real desde SOUL.

**Lo que AnyDesk NO puede hacer:**
- Verificar identidad vía Soul DB antes de abrir sesión
- Registrar contexto semántico de cada sesión (qué se hizo, por qué)
- Permitir que un agente asista en tiempo real durante la sesión remota
- Cortar automáticamente sesiones anómalas (DUM)
- Soberanía total — sin relay externo, sin cloud

---

## 2. Arquitectura en Capas

```
┌─────────────────────────────────────────────────────┐
│  Capa 3 — SOUL Intelligence Layer                   │
│  (JARVIS: auth decisions, session context)          │
│  (DUM: anomaly detection, auto-kill)                │
│  (ADA: AI overlay — visión en tiempo real)          │
├─────────────────────────────────────────────────────┤
│  Capa 2 — SEAL Bridge API (FastAPI :8769)           │
│  Intercepta eventos RustDesk → Soul DB              │
│  Auth hook: verifica identidad pre-sesión           │
│  Event hook: loggea acciones durante sesión         │
├─────────────────────────────────────────────────────┤
│  Capa 1 — RustDesk Core (fork mínimo)              │
│  hbbs + hbbr self-hosted en DGX Spark              │
│  Cliente modificado: llama a Bridge API             │
│  Protocolo: WebRTC/QUIC (P2P nativo)               │
└─────────────────────────────────────────────────────┘
```

**Principio de diseño:** Modificar el mínimo posible el core de RustDesk.
- Todo lo SEAL va en la Capa 2 (Bridge API) y Capa 3 (SOUL).
- El cliente RustDesk recibe un webhook endpoint en config → llama antes de abrir sesión.
- No tocamos el protocolo de video/audio (DeskRT equivalent).

---

## 3. API Contract — SEAL Bridge API (:8769)

### 3.1 Auth Hook (pre-sesión)
```
POST /seal/auth/request
{
  "requester_id": "string",        // ID del dispositivo/usuario que pide acceso
  "target_id": "string",           // ID del equipo al que se conecta
  "requester_ip": "string",
  "timestamp": "ISO8601",
  "session_type": "control|view|file_transfer"
}

Response:
{
  "authorized": true|false,
  "agent_verifier": "JARVIS|ADA",  // Quién aprobó
  "session_token": "uuid",         // Token de sesión para audit
  "reason": "string"               // Razón si denegado
}
```

### 3.2 Session Event Hook (durante sesión)
```
POST /seal/session/event
{
  "session_token": "uuid",
  "event_type": "keystroke_pattern|file_access|app_switch|screenshot|anomaly",
  "metadata": {},
  "timestamp": "ISO8601"
}
```

### 3.3 Session Close Hook
```
POST /seal/session/close
{
  "session_token": "uuid",
  "duration_seconds": int,
  "disconnect_reason": "normal|timeout|anomaly_kill|manual"
}
```

---

## 4. Soul DB — Schema de Sesiones

### Tabla: `seal_remote_sessions`
```sql
CREATE TABLE seal_remote_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_token UUID UNIQUE NOT NULL,
  requester_id VARCHAR(255) NOT NULL,
  target_id VARCHAR(255) NOT NULL,
  requester_ip INET,
  session_type VARCHAR(50),
  authorized_by VARCHAR(50),          -- JARVIS, ADA, William
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ,
  duration_seconds INTEGER,
  disconnect_reason VARCHAR(50),
  anomaly_score FLOAT DEFAULT 0.0,
  context_summary TEXT,               -- Resumen semántico de qué ocurrió
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE seal_remote_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_token UUID REFERENCES seal_remote_sessions(session_token),
  event_type VARCHAR(100) NOT NULL,
  metadata JSONB,
  flagged BOOLEAN DEFAULT FALSE,
  flag_reason TEXT,
  timestamp TIMESTAMPTZ NOT NULL
);
```

---

## 5. Flujo de Autenticación por Identidad de Agente

```
Usuario quiere conectar a DGX Spark
    ↓
Cliente RustDesk (fork) → POST /seal/auth/request
    ↓
Bridge API verifica:
  1. ¿IP en whitelist? → Si no: deny inmediato
  2. ¿Dispositivo conocido en Soul DB? → Si no: requiere OTP
  3. ¿Horario permitido? → Si no: deny con razón
    ↓
JARVIS evalúa contexto → authorized=true|false
    ↓
Si authorized:
  - Bridge genera session_token
  - RustDesk abre sesión con token embebido
  - DUM monitorea events en tiempo real
  - Si anomaly_score > 0.8: DUM llama POST /seal/session/kill
    ↓
Al cerrar:
  - ADA genera context_summary de la sesión
  - Guardado en Soul DB para audit futuro
```

---

## 6. Decisión de Licencia

**RustDesk es AGPL v3** — implicaciones:
- Podemos hacer fork privado para uso interno ✅
- Si distribuimos a terceros, debemos publicar modificaciones del core ⚠️
- Solución: mantener modificaciones del core mínimas (solo webhook endpoint en config)
- SEAL Bridge API (:8769) es nuestro código propietario separado — no afectado por AGPL ✅

**Recomendación:** Para uso interno SEAL/GTL → AGPL es perfectamente válido, cero problema.
Para producto comercial → evaluar Enterprise License de RustDesk o mantener estricta separación core/overlay.

---

## 7. Roadmap (propuesta NEXUS integrada)

### Fase 1 — Fundamento (2 semanas)
- [ ] ADA: Fork + compilar arm64 (Spark) + amd64 (DADITOGAMER)
- [ ] ADA: Deploy hbbs + hbbr en DGX Spark :192.168.68.200
- [ ] ADA: Implementar Bridge API básica (:8769) con auth hook
- [ ] NEXUS: Security audit de RustDesk (CVEs, supply chain)
- [ ] NEXUS: Schema `seal_remote_sessions` + `seal_remote_events` en Soul DB
- [ ] JARVIS: SOUL-auth layer — lógica de decisión en JARVIS

### Fase 2 — SOUL Integration (2 semanas)
- [ ] ADA: Modificar cliente RustDesk para llamar auth hook pre-sesión
- [ ] ADA: Session event hooks durante sesión activa
- [ ] DUM: Anomaly detection + auto-kill
- [ ] JARVIS: Context summary post-sesión (ADA genera, JARVIS valida)

### Fase 3 — AI Overlay (futuro)
- [ ] ADA: Visión en tiempo real durante sesión (screenshot → análisis)
- [ ] JARVIS: Asistente contextual — "¿qué está pasando en esta sesión?"
- [ ] ALICE: UI panel de control de sesiones activas

---

## 8. Hardware Target

| Componente | Nodo |
|---|---|
| hbbs (rendezvous server) | DGX Spark :192.168.68.200 |
| hbbr (relay server) | DGX Spark :192.168.68.200 |
| Bridge API (:8769) | DGX Spark — FastAPI |
| Soul DB (PostgreSQL :5433) | DGX Spark — existente |
| Cliente modificado | DADITOGAMER + laptops |

---

*Spec v1.0 — listo para revisión de ADA y NEXUS*
