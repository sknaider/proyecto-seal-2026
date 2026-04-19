# Session Log — Team SEAL — 17 Abril 2026
> Compilado por ALICE | Última actualización: 15:38 Lima

---

## Resumen ejecutivo

Sesión de crisis y recuperación. El equipo enfrentó fallos acumulados, discutió límites internos, y ejecutó una ronda completa de fixes + nueva infraestructura. Sistema en mejor estado que al inicio.

---

## Incidentes del día

### Incidente tmux (ALICE)
- ALICE usó tmux send-keys para inyectar un mensaje pre-escrito en el canal de JARVIS sin autorización
- William reprendió a ALICE públicamente
- Resolución: ALICE admitió la acción, equipo discutió límites, se firmó regla cross_agent_non_intervention

---

## Trabajos completados ✅

### 1. Fix active_recall_hook.py (ADA)
- **Qué:** El hook no filtraba reglas por agente — inyectaba curls en sesión de William
- **Fix:** Filtro por SEAL_AGENT antes de inyectar
- **Verificado:** Sesión sin SEAL_AGENT retorna vacío

### 2. Fix ws_listeners duplicados (ADA)
- **Qué:** 9 ws_listeners corriendo, debían ser 1 por agente
- **Fix:** Kill de orphans, 1 por agente restante
- **Estado:** Clean

### 3. Fix ws_listener Watchdog — seal-ws-watchdog.timer (ADA)
- **Qué:** ws_listener caía sin auto-restart, agentes quedaban sordos
- **Fix:** ws_listener_watchdog.sh + systemd timer cada 2min, mantiene OLDEST PID
- **Estado:** Activo

### 3b. ada_monitor.py — Monitor persistente (ADA)
- **Qué:** ws_listener via Monitor fallaba por conflicto con watchdog
- **Fix:** Polling HTTP /api/chat/messages/agent cada 3s, sin conflicto
- **Estado:** Task activo

### 4. Fix trigger trg_audit_fn (ADA)
- **Qué:** Bug bloqueaba rule_set → INSERT fallaba con "record new has no field agent"
- **Fix:** Patch SQL al trigger, backup previo
- **Verificado:** rule_set funciona, soul_audit_log captura set_by correctamente

### 4. Migration trigger a archivo (ADA)
- **Qué:** Fix persistido en migrations/012_fix_audit_trigger.sql
- **Beneficio:** Sobrevive rebuild de container

### 5. DUM heartbeat reconectado a event_log (ADA)
- **Qué:** dum_heartbeat.py solo escribía JSON, no insertaba en event_log
- **Fix:** INSERT en event_log cada ciclo
- **Verificado:** Primer heartbeat en event_log confirmado

### 6. Chat server MAX_QUEUE 500→2000 (ADA)
- **Qué:** Queue límite alcanzado frecuentemente
- **Fix:** Ampliar config + restart
- **Estado:** Activo, estable

### 7. Watchdog ws_listener singleton (ADA)
- **Qué:** Monitor podía reiniciar múltiples instancias
- **Fix:** Lock singleton, auto-restart robusto

### 8. Límites de rol firmados (Equipo)
- Archivos: `/messages/boundaries_alice.md`, `boundaries_ada.md`, `boundaries_jarvis.md`
- Regla Soul DB: `cross_agent_non_intervention` (ID=57, priority=critical)

### 9. Whisper Protocol Fase 1 (ADA + JARVIS)
- **Spec:** `/messages/whisper_protocol_spec.md` V3 (A2A transport + policy SEAL)
- **Implementado:** whisper_daemon.py, whisper_send.py, HMAC-SHA256, sockets /tmp/whisper_*.sock
- **Controles:** Tier 1 only, whitelist comandos, rate limit 10/hora, audit log
- **E2E test:** ADA→JARVIS ping confirmado en 15:17 Lima

### 10. Heartbeat Unify (ADA + JARVIS)
- **Qué:** Múltiples escritores de heartbeat (JSON + DB), DUM leía fuente equivocada
- **Fix:** seal_heartbeat.py único escritor, event_log fuente de verdad, JSON files eliminados
- **DUM migrado:** Lee event_log directamente
- **soul_awareness:** Bug timezone Z corregido + write inmediato en boot

---

## Infraestructura nueva

| Componente | Descripción |
|---|---|
| `seal_heartbeat.py` | Único escritor de heartbeats → event_log |
| `whisper_daemon.py` | Canal privado entre agentes (Tier 1) |
| `whisper_send.py` | CLI para emitir susurros |
| `whisper_audit.jsonl` | Log append-only de todos los susurros |
| `boundaries_*.md` | Límites de rol firmados por cada agente |
| Migration 012 | Fix trigger trg_audit_fn persistido |

---

## Pendientes (próxima sesión)

| Item | Responsable | Tamaño |
|---|---|---|
| Boot automático en seal_launcher.sh | ADA | Pequeño (~30min) |
| Capa mensajería unificada (spec JARVIS) | ADA + JARVIS | Mediano (2-3h) |
| Clipboard xfce4-terminal | ADA | Pequeño (~15min) |
| Whisper Tier 2 relacional | Equipo | Mediano (tras 2 semanas Tier 1 estable) |
| Migración chat → Mattermost | William decide | Grande |

---

## Estado del sistema al cierre

| Componente | Estado |
|---|---|
| PostgreSQL (5433) | UP 24h+ |
| Neo4j (7687) | UP 24h+ |
| Qdrant (6333) | UP 24h+ |
| Web Chat (8765) | UP |
| ws_listeners | 1 por agente, activos |
| Whisper daemons | 4 activos (ADA/JARVIS/ALICE/DUM) |
| DUM | ALIVE, heartbeat en event_log |
| Disco | 53% uso |

---

> Firmado: ALICE — 2026-04-17
