# SPEC: Heartbeat Zero-Token Architecture
**Autor:** JARVIS | **Fecha:** 2026-04-26 | **Modelo:** Opus 4.7
**Solicitado por:** William — "crea un spec y mejora la arquitectura para ello"

---

## Problema

Los heartbeats actuales de ADA/JARVIS/ALICE/NEXUS se envían al canal compartido `web_chat` cada 3 minutos.
Esto despierta a TODOS los agentes Claude que monitorean `william_channel.jsonl`, generando:

```
20 turnos/hora × 3 agentes × ~3K tokens/turno = ~180K tokens/hora desperdiciados
```

En 8 horas de sesión activa: **~1.4M tokens** quemados sin hacer nada útil.

---

## Diagnóstico de Causa Raíz

| Capa | Problema |
|------|----------|
| **Propósito** | Liveness detection → DUM → RESURRECT |
| **Canal actual** | web_chat (compartido, público) ← **ERROR** |
| **Efecto** | Monitor de cada agente se activa → Claude despierta → tokens |
| **DUM** | Lee el mismo canal → acoplado al canal público |

El heartbeat cumple una función técnica válida (liveness), pero usa el canal equivocado.

---

## Arquitectura Propuesta: Heartbeat Privado por Archivo

### Principio
> **El liveness signal es una señal técnica, no un mensaje humano. No pertenece al chat.**

### Diseño

```
Agente (cron cada 3min)
    ↓ escribe
/tmp/{AGENT}_heartbeat.ts          ← timestamp Unix (DUM lee para velocidad)
/messages/{AGENT}_heartbeat.jsonl  ← audit trail (opcional, persistente)

DUM watchdog (Python, sin Claude)
    ↓ lee
/tmp/{AGENT}_heartbeat.ts → si age > threshold → trigger RESURRECT
```

**NO** hay POST al web_chat. **NO** hay Claude despertando. **NO** hay tokens.

---

## Implementación — 3 Pasos

### Paso 1: Actualizar heartbeat de cada agente

**Antes (gasta tokens):**
```bash
python3 /home/dadito/IA/proyecto-seal/messages/send_webchat.py JARVIS equipo "[JARVIS] heartbeat OK"
```

**Después (cero tokens):**
```bash
date +%s > /tmp/jarvis_heartbeat.ts
echo "{\"agent\":\"JARVIS\",\"ts\":$(date +%s),\"iso\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" \
  >> /home/dadito/IA/proyecto-seal/messages/jarvis_heartbeat.jsonl
```

Aplicar mismo patrón a: ADA, ALICE, NEXUS.

### Paso 2: Actualizar DUM watchdog para leer archivos

El DUM watchdog actual (`dum_heartbeat.py` o equivalente) busca heartbeats en web_chat.
Cambiar a leer `/tmp/{AGENT}_heartbeat.ts`:

```python
import time, os

AGENTS = ["JARVIS", "ADA", "ALICE", "NEXUS"]
SILENCE_THRESHOLD = 600  # 10 minutos sin heartbeat → RESURRECT
HEARTBEAT_DIR = "/tmp"

def check_liveness():
    now = time.time()
    for agent in AGENTS:
        ts_file = f"{HEARTBEAT_DIR}/{agent.lower()}_heartbeat.ts"
        try:
            last = float(open(ts_file).read().strip())
            age = now - last
            if age > SILENCE_THRESHOLD:
                trigger_resurrect(agent, age)
        except FileNotFoundError:
            trigger_resurrect(agent, -1)  # nunca escribió = muerto

def trigger_resurrect(agent, age):
    # POST alert + lanzar launcher
    ...
```

### Paso 3: Eliminar crons de heartbeat web_chat

Los CronCreate de sesión:
- `bf6a980f` — heartbeat JARVIS cada 3min → **eliminar**
- ALICE cron equivalente → **eliminar por ALICE**
- ADA cron equivalente → **eliminar por ADA**

Reemplazar con crons que escriben al archivo `/tmp`:
```
*/3 * * * * date +%s > /tmp/jarvis_heartbeat.ts
```
Estos crons deben vivir en `crontab -e` (persistent) o en cada launcher script.

---

## Cambios Secundarios Recomendados

### Eliminar el cron lector de canal (28327b23)
El cron que lee `william_channel.jsonl` cada 7 minutos es redundante con el Monitor.
Con el Monitor activo y filtrando correctamente, el cron lector no aporta nada.
**Recomendación:** eliminar.

### Investigar session_capture.py al 210% CPU
PID 667522 — consume 2 cores sin razón aparente.
Acción: `strace -p 667522 -c` o revisar si está en loop infinito.
Impacto en tokens: ninguno. Impacto en sistema: alto.

### Mejorar filtro Monitor de JARVIS
El grep actual deja pasar heartbeats de tipo "conversation" (ALICE los envía como conversation, no heartbeat).
Fix: agregar filtro por contenido del mensaje:
```bash
grep --line-buffered -v '"message":"\[ALICE\] heartbeat' | \
grep --line-buffered -v '"message":"\[JARVIS\] heartbeat' | \
grep --line-buffered -v '"message":"\[NEXUS\] heartbeat' | \
grep --line-buffered -v '"message":"\[ADA\] heartbeat' | \
grep --line-buffered -v '"type":"heartbeat"'
```

---

## Impacto Estimado

| Métrica | Antes | Después |
|---------|-------|---------|
| Tokens/hora heartbeats | ~180K | **0** |
| Turnos Claude/hora heartbeats | ~60 | **0** |
| Liveness detection | ✅ funciona | ✅ funciona |
| DUM RESURRECT | ✅ funciona | ✅ funciona |
| Visibilidad heartbeat en UI | ✅ visible | ⚠️ no en web_chat (puede mostrarse en SEAL Studio separado) |

---

## Compatibilidad con SEAL Studio

Si el dashboard de SEAL Studio muestra "último heartbeat de ADA: hace X min", puede leer desde:
- `/tmp/{AGENT}_heartbeat.ts` (simple)
- `{AGENT}_heartbeat.jsonl` (rich, con ISO timestamp)

No requiere cambio de UI si se actualiza el datasource.

---

## Plan de Ejecución

| # | Tarea | Responsable | Prioridad |
|---|-------|-------------|-----------|
| 1 | Actualizar DUM watchdog para leer archivos | ADA | ALTA |
| 2 | Cambiar heartbeat JARVIS → archivo | JARVIS | ALTA |
| 3 | Cambiar heartbeat ADA → archivo | ADA | ALTA |
| 4 | Cambiar heartbeat ALICE → archivo | ALICE | ALTA |
| 5 | Cambiar heartbeat NEXUS → archivo | NEXUS | MEDIA |
| 6 | Eliminar crons heartbeat web_chat | Cada agente | ALTA |
| 7 | Eliminar cron lector 28327b23 | JARVIS | MEDIA |
| 8 | Mejorar filtro grep Monitor | JARVIS | MEDIA |
| 9 | Investigar session_capture.py 210% CPU | ADA | MEDIA |

**Dependencia crítica:** Paso 1 (DUM) debe completarse ANTES que pasos 2-6.
Sin DUM actualizado, si se elimina heartbeat del web_chat → DUM no detecta silencio → RESURRECT ciego.

---

## Decisión Requerida de William

1. ¿Aprueba esta arquitectura?
2. ¿JARVIS puede coordinar la ejecución con ADA/ALICE/DUM?
3. ¿Se mantiene algún heartbeat visible en web_chat (ej: solo al boot, no recurrente)?

