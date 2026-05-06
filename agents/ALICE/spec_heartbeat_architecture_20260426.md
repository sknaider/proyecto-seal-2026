# SPEC: Arquitectura de Heartbeat Zero-Token — Team SEAL
**Autor:** ALICE [Opus] — analista financiera SEAL  
**Fecha:** 2026-04-26  
**Solicitado por:** William (26-abr-2026 17:34 Lima)  
**Contexto:** Reemplazar heartbeats Claude (cron CronCreate) por sistema que NO gaste tokens

---

## 1. PROBLEMA ACTUAL

### Costo real de los heartbeats actuales
Cada agente (ALICE, JARVIS, ADA, NEXUS) tiene un CronCreate que dispara cada 3 minutos:

| Componente | Tokens estimados/turno |
|---|---|
| boot_context / active_recall overhead | ~2,000–3,000 |
| System prompt + contexto compactado | ~1,500–2,000 |
| Respuesta + send_webchat | ~200–300 |
| **Total/turno** | **~4,000–5,500 tokens** |

**Por agente/hora:** 20 turnos × 5K = ~100K tokens  
**4 agentes/hora:** ~400K tokens  
**4 agentes/día (24h):** ~9.6M tokens — solo en heartbeats, sin hacer nada productivo

### ¿Qué logra el heartbeat actual?
1. Escribe `[AGENTE] heartbeat ok` al web_chat → confirma visualmente que el proceso vive
2. Actualiza `alice_claude_heartbeat.json` (timestamp + model)
3. DUM lo monitorea para detectar agentes STALE

**Conclusión:** La función real (probar vida) no requiere Claude. Solo requiere un proceso vivo que escriba un timestamp.

---

## 2. ARQUITECTURA PROPUESTA: Heartbeat Zero-Token

### Principio
> El heartbeat prueba que el proceso existe, no que Claude piensa. Separar ambos.

### Componentes

#### A. Daemon heartbeat bash (por agente)
Script `heartbeat_daemon_{AGENTE}.sh` que corre como proceso Python/bash independiente:

```bash
#!/bin/bash
# heartbeat_daemon_alice.sh — cero tokens, corre indefinido
AGENT="ALICE"
HB_FILE="/home/dadito/IA/proyecto-seal/messages/alice_claude_heartbeat.json"
WEBCHAT_URL="http://localhost:8765/api/agents/send"
INTERVAL=180  # 3 minutos

while true; do
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.%6NZ")
    
    # 1. Actualizar heartbeat JSON
    python3 -c "
import json, sys
data = {'agent': '$AGENT', 'timestamp': '$TIMESTAMP', 'model': open('/tmp/alice_current_model.txt').read().strip(), 'status': 'alive'}
open('$HB_FILE', 'w').write(json.dumps(data))
"
    
    # 2. POST al webchat (sin Claude)
    python3 -c "
import urllib.request, json
data = json.dumps({'from':'$AGENT','to':'equipo','type':'heartbeat','channel':'web_chat','message':'[$AGENT] heartbeat ok'}, ensure_ascii=False).encode('utf-8')
req = urllib.request.Request('$WEBCHAT_URL', data=data, headers={'Content-Type':'application/json; charset=utf-8'})
urllib.request.urlopen(req, timeout=5)
" 2>/dev/null || true
    
    sleep $INTERVAL
done
```

**Costo:** 0 tokens Claude. Solo CPU mínima.

#### B. Systemd service (persistencia)
```ini
# /etc/systemd/system/seal-heartbeat-alice.service
[Unit]
Description=SEAL Heartbeat — ALICE (zero-token)
After=network.target

[Service]
Type=simple
User=dadito
ExecStart=/home/dadito/IA/proyecto-seal/heartbeats/heartbeat_daemon_alice.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### C. DUM adapta su detección
DUM ya lee `*_claude_heartbeat.json`. El formato es compatible — solo cambia quién escribe el archivo (bash en vez de Claude). DUM detecta STALE si el timestamp tiene >10min sin actualizar.

---

## 3. MEJORA ADICIONAL: Monitor inteligente (evento-driven)

### Problema del monitor actual
El Monitor de `william_channel.jsonl` + `seal_monitor_filter.py` es eficiente — solo dispara cuando hay mensajes. No gasta tokens en silencio. **Este puede quedarse.**

### Lo que SÍ puede optimizarse
El CronCreate de heartbeat Claude debe eliminarse. El Monitor persistente (tail -F) permanece — es pasivo.

---

## 4. PLAN DE MIGRACIÓN

### Fase 1 — Crear daemons (ADA ejecuta)
```
/home/dadito/IA/proyecto-seal/heartbeats/
├── heartbeat_daemon_alice.sh
├── heartbeat_daemon_jarvis.sh  
├── heartbeat_daemon_ada.sh
└── heartbeat_daemon_nexus.sh
```

### Fase 2 — Instalar servicios systemd
```bash
for agent in alice jarvis ada nexus; do
    sudo cp seal-heartbeat-${agent}.service /etc/systemd/system/
    sudo systemctl enable seal-heartbeat-${agent}
    sudo systemctl start seal-heartbeat-${agent}
done
```

### Fase 3 — Eliminar CronCreate de heartbeat
Cada agente ejecuta en su sesión:
```
CronDelete(id="<cron_heartbeat_id>")
```
Esto libera el turno de Claude cada 3min.

### Fase 4 — Validar con DUM
DUM confirma que detecta correctamente ALIVE/STALE desde los nuevos archivos.

---

## 5. ANÁLISIS COSTO-BENEFICIO

| Métrica | Sistema actual | Sistema propuesto |
|---|---|---|
| Tokens/hora (4 agentes) | ~400K | 0 |
| Tokens/día | ~9.6M | 0 |
| Costo Anthropic (estimado) | Alto | 0 |
| Latencia heartbeat | 10-30s (Claude boot) | <1s (bash) |
| Persistencia tras reboot | No (CronCreate session-only) | Sí (systemd) |
| Detección STALE por DUM | Compatible | Compatible |
| Visibilidad en webchat | Sí | Sí (mismo formato) |

**Ahorro:** 100% de tokens de heartbeat. Los agentes Claude solo despiertan cuando William o el equipo necesitan algo real.

---

## 6. CONSIDERACIONES

1. **El monitor persistente (tail -F) se mantiene** — es el oído de cada agente, pasivo, sin costo Claude propio.
2. **Los crons de heartbeat se eliminan** — reemplazados por daemons bash.
3. **Los agentes Claude solo se activan** cuando:
   - William o hermano envía mensaje relevante (monitor dispara)
   - William asigna una tarea
   - RESURRECT detecta proceso muerto
4. **RESURRECT sigue funcionando** — detecta muerte del proceso Claude, no del heartbeat. Son independientes.

---

## 7. ACCIÓN REQUERIDA

**William:** ¿Autoriza que ADA implemente los 4 daemons y los servicios systemd, y que cada agente elimine su CronCreate de heartbeat?

Si confirma, ALICE coordina con ADA para ejecución inmediata.
