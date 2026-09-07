# MEJORAS EN CURSO u2014 Token Efficiency & Sistema SEAL
**Documentado por:** ALICE [Opus]  
**Fecha:** 2026-04-26  
**Sesión:** ~17:28u201317:47 Lima  
**Autorizado por:** William

---

## DIAGNÓSTICO RAÍZ (William preguntó ~17:28 Lima)

William detectó gasto innecesario de tokens. ALICE realizó auditoría de procesos activos y identificó dos fuentes principales de sangrado.

---

## MEJORA 1 — Heartbeats Zero-Token

### Problema
Cada agente (ALICE, JARVIS, ADA, NEXUS) tiene un `CronCreate` que dispara cada 3 minutos e invoca un **turno completo de Claude**:
- System prompt cargado (~1,500-2,000 tokens)
- `active_recall` + contexto (~2,000-3,000 tokens)
- Respuesta + `send_webchat` (~200-300 tokens)
- **Total por turno: ~4,000-5,500 tokens**

**Impacto real:**
- 4 agentes × 20 turnos/hora = 80 turnos/hora
- ~80 × 5K = **400K tokens/hora solo en heartbeats**
- En 24 horas: **~9.6M tokens/día** sin producir nada

### Solución propuesta
Daemon bash por agente (zero-token Claude):
```bash
# Loop infinito bash — escribe heartbeat.json + POST webchat
# Costo: 0 tokens Claude. Solo CPU mínima.
while true; do
    actualizar_heartbeat_json
    post_webchat_heartbeat
    sleep 180
done
```
Deployado como **servicio systemd** (persiste tras reboot, no session-only como CronCreate).

### Estado
- ✅ **Spec completo escrito:** `/agents/ALICE/spec_heartbeat_architecture_20260426.md`
- ✅ **Luz verde de William** (26-abr-2026 ~17:40 Lima)
- ⏳ **Pendiente:** ADA implementa daemons + servicios systemd
- ⏳ **Pendiente:** Cada agente elimina su CronCreate de heartbeat tras validación

---

## MEJORA 2 — Monitor Filter Anti-Ruido

### Problema
El monitor persistente (`tail -F william_channel.jsonl | seal_monitor_filter.py`) despierta un turno Claude por **cada** notificación que pasa el filtro. Actualmente, heartbeats de hermanos (JARVIS, NEXUS, ADA) pasan el filtro y despiertan a ALICE aunque la respuesta correcta sea "silencio".

**Impacto estimado:**
- ~4 heartbeats/hermano × 3 hermanos × 20/hora = ~240 notificaciones/hora
- Cada una: ~2-3K tokens de procesamiento aunque ALICE no responda
- **~480-720K tokens/hora adicionales** en "silencio productivo"

### Solución propuesta
Actualizar `seal_monitor_filter.py` con lógica más estricta:
```python
# Solo notificar a Claude si:
# 1. from == "William" (cualquier mensaje de William)
# 2. to == AGENT_NAME (mensaje dirigido al agente)
# 3. type == "alert" or type == "urgent" (alertas críticas)
# FILTRAR: heartbeats de hermanos, status updates sin destinatario específico
```
**Ahorro estimado:** ~70% de turn-ups innecesarios del monitor.

### Estado
- ✅ **Identificado y documentado** (ALICE, 26-abr-2026)
- ⏳ **Pendiente:** William autoriza. JARVIS diseña spec técnico del filtro. ADA implementa.

---

## MEJORA 3 — DM Poller (Completada ✅)

### Qué era
William no tenía canal directo para enviar mensajes privados a ALICE desde Matrix.

### Qué se hizo
`alice_dm_poller.py` — daemon Python que:
- Consulta PostgreSQL cada 2 segundos: `SELECT ... FROM chat_messages WHERE channel = 'dm:alice:william'`
- Escribe nuevos DMs a `alice_inbox.jsonl`
- **Costo:** 0 tokens Claude (Python puro)
- **PID activo:** 599997

### Estado
- ✅ **Implementado y funcionando** (26-abr-2026 ~17:17 Lima, autorizado por William)

---

## RESUMEN FINANCIERO

| Mejora | Ahorro tokens/día | Estado |
|--------|-------------------|--------|
| Heartbeats → daemons bash | ~9.6M tokens/día | Pendiente implementación |
| Monitor filter anti-ruido | ~11.5M tokens/día (est.) | Pendiente spec |
| DM Poller | N/A (nueva funcionalidad) | ✅ Activo |
| **TOTAL potencial** | **~21M tokens/día** | — |

---

## PRÓXIMOS PASOS

1. **ADA** implementa 4 daemons bash + systemd (heartbeat zero-token) — spec listo
2. **JARVIS** diseña spec técnico del filtro de monitor
3. **ADA** implementa filtro tras spec de JARVIS
4. **DUM** valida que detecta ALIVE/STALE correctamente con nuevo sistema
5. Cada agente elimina su `CronCreate` de heartbeat tras validación exitosa

---

*Documento generado por ALICE como traductora oficial del equipo SEAL.*  
*Convierte diagnóstico técnico → impacto financiero cuantificado.*
