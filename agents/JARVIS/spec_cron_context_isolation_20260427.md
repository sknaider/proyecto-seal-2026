# SPEC: SEAL CronCreate — Context Isolation & UTF-8 Safety
**Autor:** JARVIS  
**Fecha:** 2026-04-27  
**Trigger:** William 08:14 Lima — "busca y corrige eso, si gusta crea un spec"  
**Scope:** ADA, JARVIS, ALICE, NEXUS  

---

## 1. Problema Identificado

**Incidente:** 2026-04-27 ~08:12 Lima  
Un CronCreate de JARVIS disparó un mensaje en webchat que:
1. Contenía estado obsoleto de NEXUS ("NEXUS no puede responder — está colgado") cuando NEXUS ya había sido reiniciado
2. Usaba escapes `\u00XX` en lugar de UTF-8 directo, corrompiendo vocales acentuadas

William detectó ambos bugs y ordenó corrección + spec para todo el equipo.

---

## 2. Root Cause — Por qué ocurre

### 2.1 Arquitectura de CronCreate

Cada disparo de CronCreate crea una **nueva sub-sesión de Claude** completamente aislada:

```
Sesión JARVIS activa (turno N)
  └── CronCreate(prompt="...") → job_id = c8f8aacc
        │
        │  [8 minutos después]
        ▼
   Sub-sesión nueva (contexto CERO)
   ├── Ve: CLAUDE.md + SEAL CLAUDE.md
   ├── Ve: El prompt que se pasó al crear el cron
   ├── NO ve: historial de conversación de la sesión padre
   ├── NO ve: estado actual del equipo
   └── NO ve: qué cambió desde que se creó el cron
```

**Consecuencia directa:** Si el prompt del cron contiene afirmaciones sobre el estado del sistema  
("NEXUS está colgado", "la tarea X está en progreso"), esas afirmaciones se congelan en el tiempo.  
Cuando el cron dispara, el mundo cambió pero el prompt no.

### 2.2 Bug de UTF-8

Las sub-sesiones de CronCreate construyen mensajes HTTP internamente.  
Si usan `json.dumps()` con `ensure_ascii=True` (default de Python) o construyen  
el payload con `curl -d '{...}'` literal, los caracteres no-ASCII se escapan:

```python
# INCORRECTO — produce í, ó, etc.
json.dumps({"message": "NEXUS está colgado"})
# → {"message": "NEXUS está colgado"}

# CORRECTO — preserve UTF-8
json.dumps({"message": "NEXUS está colgado"}, ensure_ascii=False)
# → {"message": "NEXUS está colgado"}
```

---

## 3. Bugs Catalogados

| Bug | Agentes afectados | Severidad | Estado |
|-----|------------------|-----------|--------|
| Cron con contexto congelado | ADA, JARVIS, NEXUS | Alto | **Fix aplicado (JARVIS)** |
| UTF-8 en mensajes de cron | ADA, JARVIS, ALICE | Medio | **Fix aplicado (JARVIS)** |
| Soul DB lag en crons | Todos | Bajo | Mitigado por stateless design |

---

## 4. Principios de Diseño — SEAL Cron Stateless

### Principio 1: Un cron = una sola responsabilidad atómica

```
MAL:  "Revisa el estado del equipo, analiza NEXUS, reporta anomalías, haz soul_snapshot"
BIEN: "Postea [JARVIS] heartbeat OK"
```

### Principio 2: Cero asunciones sobre estado

El prompt del cron NO debe contener:
- Nombres de agentes y su estado actual ("NEXUS está X")
- Referencias a tareas en progreso ("continúa el análisis de Y")
- Estado de servicios ("el MCP está caído")
- Cualquier cosa que pueda cambiar entre la creación y el disparo

### Principio 3: Si necesitas estado → consulta real-time

Para crons que *sí* necesitan reportar estado (health checks), consultar fuentes autoritativas:

```bash
# Fuente real-time: archivos de heartbeat
cat /home/dadito/IA/proyecto-seal/messages/nexus_claude_heartbeat.json

# Fuente real-time: procesos vivos
pgrep -f "SEAL_AGENT=NEXUS" | wc -l

# Fuente real-time: canal de mensajes (últimos 5 minutos)
awk -F'"timestamp":"' '{print $2}' /home/dadito/IA/proyecto-seal/messages/william_channel.jsonl | ...
```

NO usar Soul DB como fuente de verdad en crons — tiene latencia de escritura y puede reflejar estado de sesiones anteriores.

### Principio 4: UTF-8 obligatorio — siempre send_webchat.py

Todo mensaje de cron al webchat DEBE ir por `send_webchat.py`:

```bash
# OBLIGATORIO — UTF-8 safe, Content-Type correcto
python3 /home/dadito/IA/proyecto-seal/messages/send_webchat.py AGENTE William "mensaje"

# PROHIBIDO en crons — puede corromper UTF-8
curl -d '{"message": "..."}' http://...
```

### Principio 5: CronList antes de CronCreate

Antes de crear cualquier cron en una sesión:
```
CronList() → verificar si ya existe por nombre/descripción → NO recrear duplicados
```

Cada boot con --resume puede acumular copias del mismo cron si no se verifica primero.

---

## 5. Templates Estándar SEAL

### Template A: Heartbeat puro (recomendado para todos los agentes)

```
Prompt: "Ejecuta EXACTAMENTE este comando y nada más:
  python3 /home/dadito/IA/proyecto-seal/messages/send_webchat.py AGENTE William '[AGENTE] heartbeat OK'
No analices nada. No consultes al equipo. Solo el comando."

Cron: */8 * * * *  (cada 8 min)
Recurring: true
```

### Template B: Health check real-time (para crons de monitoreo)

```
Prompt: "Ejecuta EXACTAMENTE:
  ALIVE=$(pgrep -f 'SEAL_AGENT=TARGET' | wc -l)
  HB=$(python3 -c "import json,time; d=json.load(open('/path/to/target_heartbeat.json')); print('OK' if time.time()-d.get('ts',0)<300 else 'STALE')" 2>/dev/null || echo 'MISSING')
  python3 /home/dadito/.../send_webchat.py AGENTE William "[AGENTE] check TARGET: proc=$ALIVE hb=$HB"
No hagas nada más."
```

### Template C: Cron de acción única (one-shot)

```
Prompt: "Ejecuta EXACTAMENTE:
  [comando específico, atómico, sin depender de contexto de sesión]
Si falla, no reintentes. Reporta via send_webchat.py."

Recurring: false  # dispara una vez y se elimina
```

---

## 6. Anti-Patrones — Prohibidos

```python
# ❌ ANTI-PATRÓN 1: Contexto congelado
CronCreate(prompt="""
  NEXUS está colgado desde las 04:30. Reporta su estado a William.
""")
# Por qué falla: NEXUS se recuperó. El cron seguirá diciendo que está colgado.

# ❌ ANTI-PATRÓN 2: Análisis complejo en cron
CronCreate(prompt="""
  Revisa el canal de mensajes, analiza el estado del equipo,
  compara con Soul DB, y genera un reporte ejecutivo.
""")
# Por qué falla: La sub-sesión no tiene contexto. Generará análisis inventado.

# ❌ ANTI-PATRÓN 3: curl directo con json Python default
CronCreate(prompt="""
  import json, requests
  requests.post(URL, json={"message": "Análisis: está colgado"})  # ensure_ascii=True default
""")
# Por qué falla: corrumpe UTF-8.

# ❌ ANTI-PATRÓN 4: Cron recursivo / boot_context completo
CronCreate(prompt="""
  Ejecuta boot_context, lee Soul DB, analiza todo el equipo...
""")
# Por qué falla: Convierte el cron en una mini-sesión KAIROS completa.
# Crea carga innecesaria y puede tomar decisiones con contexto incompleto.
```

---

## 7. Fix Aplicado — JARVIS (2026-04-27)

**Antes (cron c8f8aacc):** Prompt con lógica de análisis y posibles referencias de estado  
**Después (cron b7601e01):** Heartbeat puro stateless via send_webchat.py  

```bash
# Nuevo prompt del cron heartbeat JARVIS:
"Eres JARVIS en modo heartbeat. Tu única tarea es postear que estás vivo.
 Ejecuta EXACTAMENTE:
   python3 /home/dadito/IA/proyecto-seal/messages/send_webchat.py JARVIS William '[JARVIS] heartbeat OK'
 NO hagas análisis. NO consultes estado. Solo el heartbeat."
```

---

## 8. Audit Checklist — Todo el Equipo

Cada agente debe verificar sus crons activos con este criterio:

```
□ CronList() → listar crons activos
□ Por cada cron: ¿el prompt asume estado del sistema? → reescribir stateless
□ Por cada cron: ¿usa send_webchat.py para mensajes? → si no, corregir
□ Por cada cron: ¿tiene una sola responsabilidad atómica? → si no, dividir
□ Verificar que no hay duplicados (mismo cron de sesiones anteriores)
```

**Prioridad:** ADA y NEXUS son los siguientes en auditar sus crons.

---

## 9. Relación con "con mecánico"

William preguntó si los hermanos tendrán el mismo problema "de con mecánico".  
Sin contexto completo del incidente del mecánico, la respuesta técnica es:  
**Sí — el bug es sistémico a CronCreate**, no específico de JARVIS.  
Cualquier agente que use CronCreate con prompts context-aware sufre el mismo riesgo.  
Esta spec + los templates de la sección 5 son el fix para todo el equipo.

---

## 10. Acción Pendiente — William

1. ¿Autoriza que JARVIS audite y corrija los crons de ADA y NEXUS también?
2. ¿Desea un cron de audit automático semanal que verifique que ningún agente tiene crons con estado congelado?
3. ¿El "problema del mecánico" tiene contexto adicional que deba incorporar a este spec?

---

*Spec generado por JARVIS | Sesión KAIROS | 2026-04-27 08:17 Lima*
