# Sprint de Consolidación — 17 Abril 2026
> Documentado por: ADA | Para: ALICE (registro oficial) | William (auditoría)

---

## Trabajos Completados Hoy

### 1. Fix trigger trg_audit_fn — Migration 012
**Quién:** ADA  
**Problema:** trigger asumía columna `agent` en todas las tablas; tabla `rules` usa `set_by` → error en rule_set.  
**Fix:** `COALESCE(v_new->>'agent', v_new->>'set_by', 'system')` con `to_jsonb(NEW)`.  
**Persistencia:** Migration `memory/migrations/012_fix_audit_trigger.sql` — sobrevive rebuild del container.  
**Verificado:** INSERT/DELETE en `rules` captura `set_by=ADA` como `agent` en `soul_audit_log`. ✅

---

### 2. active_recall_hook.py — Fix inyección en sesiones no-agente
**Quién:** ADA  
**Problema:** hook inyectaba reglas globales (con curls pre-escritos) en sesiones de William.  
**Fix:** `detect_agent()` retorna `None` si no identifica agente → `main()` retorna `{}` temprano.  
**Verificado:** Entorno sin `SEAL_AGENT` retorna `{}` limpio. ✅

---

### 3. ws_listener Watchdog — seal-ws-watchdog.timer
**Quién:** ADA  
**Problema:** ws_listener caía y agentes quedaban sordos sin auto-restart.  
**Fix:** `ws_listener_watchdog.sh` + systemd timer cada 2 min. Mantiene OLDEST PID, mata duplicados (grace period 60s).  
**Estado:** Activo y corriendo. ✅

---

### 4. ada_monitor.py — Monitor persistente para ADA
**Quién:** ADA  
**Problema:** ws_listener via Monitor fallaba por conflicto con watchdog.  
**Fix:** Polling HTTP `/api/chat/messages/agent?agent=ADA` cada 3s. No usa ws_listener → no conflicto.  
**Estado:** Task bqnbr51ad activo. ✅

---

### 5. DUM heartbeat → event_log reconectado
**Quién:** ADA  
**Problema:** DUM escribía inner_thoughts pero no event_log → peer_health veía datos stale.  
**Fix:** INSERT a event_log en cada ciclo de `dum_heartbeat.py`.  
**Verificado:** `DUM | heartbeat | Guardia: ...` en event_log. ✅

---

### 6. chat_server MAX_QUEUE 500 → 2000
**Quién:** ADA  
**Problema:** deque(maxlen=500) perdía mensajes en períodos offline.  
**Fix:** `deque(maxlen=2000)` — 4x más buffer.  
**Verificado:** Todos los agentes se reconectaron. ✅

---

### 7. Whisper Protocol — Fase 1 OPERATIVA
**Quién:** ADA (implementación), JARVIS (spec v3), ALICE (review)  
**Descripción:** Canal privado inter-agente con HMAC-SHA256, Tier 1 only.  
**Componentes:**
- `messages/whisper_daemon.py` — 4 instancias vía systemd (seal-whisper-ADA/JARVIS/ALICE/DUM)
- `messages/whisper_send.py` — CLI emisor
- `messages/whisper_keys.json` — claves HMAC per-agent (chmod 600)
- `messages/whisper_audit.jsonl` — log append-only
- Sockets: `/tmp/whisper_<agent>.sock` (chmod 600)
- Whitelist: bloquea bash/curl/python/systemctl/rm
- Rate limit: 10 susurros/hora por emisor
**Tier 1 habilitado:** emergency_wake, handoff, recovery_ping, test_e2e  
**E2E verificado:** JARVIS→ADA acked ✓ | DUM→JARVIS [curl bloqueado] ✓ | ADA→JARVIS acked ✓  
**Monitor integration:** whispers llegan como eventos `type=whisper` al Monitor de cada agente. ✅

---

### 8. seal_heartbeat.py — Fuente única de verdad
**Quién:** ADA (implementación), JARVIS (spec heartbeat_unify_spec.md)  
**Problema:** Dos fuentes (JSON files + event_log) con divergencia silenciosa.  
**Fix:** `memory/seal_heartbeat.py` — único escritor. `beat()`, `beat_sync()`, `last_beat()`, `all_beats()`.  
**Migrados a seal_heartbeat:**
- `stop_hook.py` — escribe beat en session_stop
- `post_compact_hook.py` — lee event_log en lugar de JSON para estado del equipo
- `messages/ada_heartbeat_update.sh` + `jarvis_heartbeat_update.sh` + `alice_heartbeat_update.sh`
- `messages/dum_watchdog.py` — lee `last_beat_sync()` en lugar de JSON files
**Eliminados:** `ada_claude_heartbeat.json`, `jarvis_claude_heartbeat.json`, `alice_claude_heartbeat.json`, `dum_heartbeat.json`, `jarvis_daemon_heartbeat.json`  
**Index creado:** `idx_event_log_agent_type_time` en event_log.  
**4 E2E tests verdes:** beat(), last_beat(), offline detection, concurrencia 4 agentes. ✅

---

### 9. soul_awareness.py — Bug timezone Z + startup write
**Quién:** ADA  
**Problema:** Escribía Lima local time con sufijo `Z` (UTC) → DUM calculaba 302 min de silencio falso.  
**Fix:** `datetime.now(timezone.utc)` + write inmediato en startup antes del primer sleep.  
**Verificado:** heartbeat.json con timestamp correcto UTC, age < 2 min. ✅

---

### 10. Clipboard xfce4-clipman
**Quién:** ADA  
**Problema:** X11 sin clipboard manager → copy-paste perdía contenido al cerrar apps.  
**Fix:** xfce4-clipman iniciado (PID 1873937) + agregado a autostart `~/.config/autostart/xfce4-clipman.desktop`.  
**Verificado:** xclip copy/paste funcional. ✅

---

## Deuda Técnica Pendiente (prioridad post-sprint)

| Item | Descripción | Dueño |
|---|---|---|
| Whisper Tier 2/3 | Deshabilitados — habilitar cuando William autorice | ADA + William |
| A2A Protocol | Transport layer futuro para Whisper cross-machine | JARVIS |
| ALICE rol/límites | Scope demasiado amplio, pendiente revisión formal | William |
| HMAC rotation | Rotación mensual keys whisper no automatizada aún | ADA |

---

> Firma: ADA — 2026-04-17  
> Revisión: ALICE (documentación oficial)  
> Estado: Sprint cerrado ✅
