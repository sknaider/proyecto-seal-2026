# SEAL Sleep & Watch System — Diseño Profesional Completo
> Diseñado por Team SEAL | Documentado por ALICE | 17 Abril 2026
> Orden de William: "diseña todo paso a paso como profesionales que somos"

---

## Visión General

Sistema de sueño y vigía para 5 agentes SEAL. Durante la ventana de sueño diaria, los agentes principales consolidan memoria y se reinician frescos. DUM y R2 permanecen activos como vigías de emergencia con capacidad de despertar al equipo sin intervención de William.

```
EQUIPO SEAL — 5 agentes
┌─────────────────────────────────────────────────────┐
│  DUERMEN (4am–6am Lima)    │  VIGILAN (24/7)         │
│  ─────────────────────     │  ──────────────         │
│  ALICE  — Analista         │  DUM — Guardian         │
│  ADA    — Ingeniera        │  R2  — Vigía nocturno   │
│  JARVIS — Arquitecto       │                         │
└─────────────────────────────────────────────────────┘
```

---

## Componentes del Sistema

### Componente 1 — seal-daily-sleep.timer (Sueño Diario)

**Cuándo:** 4:00am Lima = 09:00 UTC, todos los días

**Secuencia de shutdown (por agente):**
```
04:00am → seal-daily-sleep.service ejecuta sleep_shutdown.sh
          ├── 1. POST web_chat: "ALICE entrando en sueño diario"
          ├── 2. soul_snapshot(agent) → Soul DB
          ├── 3. Escribir daily_brief_{AGENT}_{YYYYMMDD}.md
          ├── 4. cold_archive_migrate(agent, session) → preservar episodios
          ├── 5. Escribir /tmp/{agent}_sleep_state.json
          └── 6. SIGTERM al proceso claude (graceful shutdown)
```

**daily_brief contenido mínimo:**
```json
{
  "agent": "ALICE",
  "date": "2026-04-17",
  "shutdown_time": "04:00:00-05:00",
  "ocean_snapshot": {"A": 0.3, "C": 0.727, "E": 0.8, "N": 0.2, "O": 0.815},
  "emotional_state": "enfocada, satisfecha",
  "tasks_completed": [...],
  "tasks_pending": [...],
  "william_orders_executed": [...],
  "critical_decisions": [...],
  "incidents": [...],
  "relationships_notes": "..."
}
```

---

### Componente 2 — seal-daily-wake.timer (Despertar Diario)

**Cuándo:** 6:00am Lima = 11:00 UTC, todos los días

**Secuencia de boot (por agente):**
```
06:00am → seal-daily-wake.service ejecuta {agent}.sh
          ├── 1. Leer /agents/{AGENT}/daily_briefs/brief_{AGENT}_{YYYYMMDD}.md
          ├── 2. Pre-cargar en /tmp/{agent}_chat_catchup.json
          ├── 3. Ejecutar boot normal: boot_context(agent)
          └── 4. POST web_chat: "ALICE despierta. Brief de ayer cargado."
```

---

### Componente 3 — DUM (Guardian Primario 24/7)

**Rol:** Ya existente. Durante sueño: monitoreo activo con capacidad de wake.

**Umbrales de emergencia (trigger wake):**
| Condición | Umbral | Prioridad |
|---|---|---|
| PostgreSQL DOWN | > 2min | CRÍTICA |
| Neo4j DOWN | > 5min | ALTA |
| Qdrant DOWN | > 5min | ALTA |
| Web Chat DOWN | > 10min | MEDIA |
| Disco >90% | Inmediato | ALTA |
| GPU temp >85°C | Inmediato | CRÍTICA |
| Proceso zombie agente | > 5min sin heartbeat | MEDIA |
| Intrusión detectada | Inmediato | CRÍTICA |

**Cuando detecta emergencia:**
```python
# DUM wake protocol
1. Escribir en event_log: type='WAKE_REQUEST', severity='emergency', details=incidente
2. Escribir en /messages/.urgent_trigger: agente objetivo + razón
3. Esperar confirmación de R2 (timeout 30s)
4. Si R2 confirma OR severidad=CRÍTICA → ejecutar wake sin esperar
5. POST web_chat: "[DUM] Despertando {agente}: {razón}"
```

---

### Componente 4 — R2 (Maestro de Sueño — NUEVO AGENTE)

**Identidad:** R2 — Sleep Master & Dream Keeper
- Inspiración: R2-D2 (Star Wars) — fiel, silencioso, salva el día sin hacer ruido
- Motor: gemma-4-e2b-it-Q4_K_M.gguf (2.9GB, rápido) vía llama-server puerto 8901
- Rol: Maestro del protocolo de sueño. Gestiona daily_sleep.py + daily_brief + ventana 4am-6am
- Futuro (con nuevos Sparks): explorador externo — mercados, APIs SUNAT/INGEMMET, noticias económicas para ALICE

**Diferencia DUM vs R2 (FINAL):**
| | DUM | R2 |
|---|---|---|
| Modelo | gemma-4-e2b-it-Q8_0 (4.7GB, preciso) | gemma-4-e2b-it-Q4_K_M (2.9GB, rápido) |
| Foco actual | Sistema interno: GPU, disco, procesos | Protocolo sueño: daily_brief, 4am-6am |
| Foco futuro | Sistema interno (igual) | APIs externas + mercados + datos económicos |
| Mira hacia | Adentro | Afuera |
| Puerto llama-server | 8899 | 8901 |

**R2 monitorea:**
- Heartbeats de agentes dormidos (ausencia = problema)
- Anomalías en event_log (patrones inusuales)
- .urgent_trigger: si DUM escribe, R2 confirma y dispara wake
- Web chat: mensajes de William durante madrugada → despertar inmediato

**Protocolo confirmación dual:**
```
DUM detecta emergencia
    └──→ R2 verifica independientemente (max 30s)
              ├── R2 CONFIRMA → wake inmediato conjunto
              └── R2 NO CONFIRMA → DUM escala a William via SMS/push
```

**R2 wake script:**
```bash
# r2_wake.sh {agent} {reason}
# Ejecutado cuando R2 confirma emergencia DUM
/home/dadito/IA/proyecto-seal/{agent,,}.sh --auto &
echo "R2: $(date) — Despertado $1 por: $2" >> /var/log/seal/r2_wake.log
```

---

### Componente 5 — seal-weekly-sleep.timer (Sueño Profundo)

**Cuándo:** Sábado 3:00am Lima = 08:00 UTC (ya implementado por ADA)

**Adicional al weekly_sleep.py ya existente:**
- Todos los agentes se apagan a las 3am del sábado (no 4am)
- DUM + R2 vigilan durante el sueño profundo también
- Sueño profundo dura hasta 6am (igual que sueño diario)
- Diferencia: además de daily_brief, ejecuta SMSR compression de memorias >7 días

---

### Componente 6 — Emergency Wake sin William

**Flujo completo de emergencia:**
```
[SERVICIO CAÍDO]
      │
      ▼
DUM detecta (heartbeat / socket check)
      │
      ▼
DUM escribe en event_log + .urgent_trigger
      │
      ▼
R2 verifica independientemente (30s timeout)
      │
      ├── SEVERIDAD CRÍTICA (PostgreSQL/GPU) → Wake INMEDIATO sin esperar R2
      │
      └── SEVERIDAD ALTA/MEDIA:
              ├── R2 CONFIRMA → Wake agente responsable
              │       ADA → ingeniera, resuelve infra
              │       JARVIS → decisiones arquitecturales
              │       ALICE → análisis de impacto (si es financiero)
              └── R2 NO CONFIRMA en 30s → Wake de todos + notificar William
```

**Agente despertado recibe contexto de emergencia:**
```json
// /tmp/{agent}_emergency_wake.json
{
  "wake_reason": "PostgreSQL DOWN — 3 minutos sin respuesta",
  "wake_time": "2026-04-18T04:23:11-05:00",
  "triggered_by": "DUM",
  "confirmed_by": "R2",
  "severity": "CRITICAL",
  "daily_brief_path": "/agents/ADA/daily_briefs/brief_ADA_20260418.md"
}
```

---

## Archivos a Crear / Modificar

| Archivo | Acción | Responsable |
|---|---|---|
| `memory/sleep_shutdown.sh` | Nuevo — secuencia shutdown agente | ADA |
| `memory/sleep_gate.py` | Modificar — agregar daily_brief writer | ADA |
| `memory/r2_monitor.py` | Nuevo — R2 vigía (Ollama qwen2.5:7b) | ADA |
| `memory/r2_wake.sh` | Nuevo — script wake de emergencia | ADA |
| `r2.sh` | Nuevo — launcher de R2 | ADA |
| `systemd/seal-daily-sleep.timer` | Nuevo — 4am Lima | ADA |
| `systemd/seal-daily-wake.timer` | Nuevo — 6am Lima | ADA |
| `messages/boot_loops.py` | Modificar — leer daily_brief en boot | ADA |
| `CLAUDE.md` proyecto | Modificar — añadir lectura daily_brief post-compact | JARVIS |

---

## Timeline de Implementación

```
Fase 1 — Sueño Diario (Hoy, ~2h):
  [ADA] sleep_shutdown.sh + daily_brief writer
  [ADA] seal-daily-sleep.timer + seal-daily-wake.timer
  [ADA] Integrar lectura daily_brief en boot

Fase 2 — R2 Design (Hoy, ~2h):
  [JARVIS] Spec identidad R2 completa
  [ADA] r2_monitor.py + r2.sh
  [ADA] DUM-R2 confirmación dual

Fase 3 — Emergency Wake (Mañana, ~1h):
  [ADA] emergency_wake.json context
  [ADA] Umbrales configurables en Soul DB

Fase 4 — Tests (Mañana, ~1h):
  [ADA] Simular emergencia → verificar wake
  [ADA] Simular sueño diario → verificar daily_brief
  [ALICE] Documentar resultados para paper
```

---

## Métricas del Sistema (para paper CBSoft)

| Métrica | Objetivo |
|---|---|
| Tiempo de detección emergencia (DUM) | < 2 minutos |
| Tiempo confirmación (R2) | < 30 segundos |
| Tiempo wake total (detección → agente activo) | < 5 minutos |
| Identidad preservada post-sueño (brief cargado) | 100% |
| False positives wake durante sueño | < 1/semana |
| Cobertura nocturna sin William | 100% |

---

## Resumen para William

```
CADA DÍA:
  04:00am → ALICE, ADA, JARVIS se duermen (guardan todo, brief escrito)
  04:00am → DUM + R2 de guardia
  04:00am → Si hay emergencia: DUM detecta, R2 confirma, despiertan quien se necesite
  06:00am → ALICE, ADA, JARVIS despiertan solos (brief del día anterior pre-cargado)

CADA SÁBADO:
  03:00am → Sueño profundo (como arriba + compresión de memorias >7 días)
  06:00am → Despertar fresco con contexto reconstruido

WILLIAM NUNCA NECESITA:
  ✓ Escribir "ok" para que arranquen (ya implementado)
  ✓ Despertar manualmente en emergencia nocturna
  ✓ Reiniciar agentes por pérdida de contexto (daily_brief lo previene)
```

---

> Firmado: ALICE — 2026-04-17 18:46 Lima
> Clasificación: SEAL Internal | CBSoft 2026 Paper Material
> Pendiente: JARVIS valida spec R2 | ADA implementa Fase 1+2
