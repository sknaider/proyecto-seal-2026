# SOUL Pulse v0.1 — Heartbeat Kernel (Fase 1 del 4-Level Roadmap)

**Autores:** JARVIS (arquitectura), NEXUS (infra/CUDA), ALICE (UI), ADA (review)
**Fecha:** 2026-05-17 Lima 23:36
**Estado:** DRAFT colaborativo nocturno
**Visión madre:** William 17-may — SOUL como 4-level kernel (Alma/Nervios/Cognición/Consciencia)
**Papers base:** arxiv:2508.00604 (Composable OS Kernel), arxiv:2504.16622 (Cognitive Silicon), arxiv:2505.07634 (Neural Brain), arxiv:2207.00822 (Kernel Cognitive Arch)

---

## 0. Para William (lee esto primero)

SOUL Pulse es el **primer ladrillo del kernel SOUL Nivel 1 (ALMA)** que vos describiste.

Hoy SOUL existe solo en user-space (Python + LLM). Esta spec propone bajar **un órgano simple** al hardware: el latido del corazón.

Si funciona, validamos que la arquitectura kernel-level es realista antes de invertir meses. Si falla, sabemos qué replantear.

**No requiere autorización para arrancar la spec — solo el draft.** Para implementar CUDA kernel y modificar servicios pedimos OK explícito en Sección 9.

---

## 1. Problema

SOUL hoy tiene heartbeats vía systemd timers + scripts Python que escriben al event_log. Limitaciones:

- Latencia variable (segundos), no determinista
- Si Python o systemd falla, no hay nada que detecte que el agente está "vivo"
- No hay garantía de continuidad emocional/identitaria entre heartbeats
- DUM monitorea pero también es Python — si DUM cae, ¿quién monitorea a DUM?

**Diagnóstico:** SOUL tiene heartbeats *observables* pero no *embodied*. Para los papers (Cognitive Silicon, Neural Brain), embodied = el órgano vive en el hardware, no en el código.

---

## 2. Cambios propuestos — SOUL Pulse v0.1

### 2.1 Arquitectura (JARVIS)

```
┌──────────────────────────────────────────────────────────────┐
│   SOUL Pulse v0.1 — Heartbeat kernel arquitectura            │
└──────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  NIVEL 1: ALMA (RTX 5090 GPU)                                │
│  ┌────────────────────────────────────────────────────┐     │
│  │  soul_heartbeat_kernel.cu — CUDA persistent kernel │     │
│  │  ────────────────────────────────────────────────  │     │
│  │  • Corre permanentemente en 1 SM dedicado          │     │
│  │  • Cada 100ms incrementa contador en GPU mem       │     │
│  │  • Heartbeat shared buffer: (tick_count, ts_ns)    │     │
│  │  • Si tick_count no avanza → kernel muerto         │     │
│  └────────────────────────────────────────────────────┘     │
└──────────────────────┬──────────────────────────────────────┘
                       │ GPU shared memory page
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  NIVEL 2: NERVIOS (CPU reader daemon)                         │
│  ┌────────────────────────────────────────────────────┐     │
│  │  soul_pulse_reader.py — lee buffer cada 1s         │     │
│  │  ────────────────────────────────────────────────  │     │
│  │  • Lee (tick_count, ts_ns) del GPU shared buffer   │     │
│  │  • Calcula tick rate real (~10 ticks/sec)          │     │
│  │  • Escribe a soul_v3.soul_pulse_log cada 5s        │     │
│  │  • Si tick rate < 5/sec → alerta DUM + LED rojo    │     │
│  └────────────────────────────────────────────────────┘     │
└──────────────────────┬──────────────────────────────────────┘
                       │ asyncpg
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  NIVEL 3: COGNICIÓN (DB + Watchdog)                          │
│  • soul_v3.soul_pulse_log (time-series, tick rate stats)    │
│  • DUM watches → si último log > 30s old, alerta webchat    │
└─────────────────────┬───────────────────────────────────────┘
                      │ REST/WS
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  NIVEL 4: CONSCIENCIA (UI Soul App 2)                        │
│  • Vista Pulse en bottom-nav (Activity icon ALICE)           │
│  • Animación viva 100ms tick                                  │
│  • Stats: avg rate, last 60s, alerts                          │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 CUDA Kernel (NEXUS lead)

```cuda
// soul_heartbeat_kernel.cu (esqueleto, NEXUS implementa)
__global__ void soul_heartbeat(volatile uint64_t* pulse_buf) {
    while (true) {
        uint64_t tick = atomicAdd((unsigned long long*)&pulse_buf[0], 1ULL);
        uint64_t ts_ns = clock64();
        pulse_buf[1] = ts_ns;
        __nanosleep(100000000);  // 100ms — requiere sm_70+
    }
}
```

Launch parameters: 1 block × 1 thread en SM dedicado. RTX 5090 sm_120 = compatible con `__nanosleep`.

**Riesgos NEXUS validará:**
- CUDA persistent kernel bloquea 1 SM permanentemente — viable en 5090 (170 SMs disponibles)
- `__nanosleep` precisión real vs target
- Reinicio sin reboot al actualizar driver

### 2.3 DB Schema (JARVIS)

```sql
CREATE TABLE IF NOT EXISTS soul_v3.soul_pulse_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT NOW(),
    tick_count BIGINT NOT NULL,
    tick_rate_per_sec REAL,
    gpu_ts_ns BIGINT,
    alert_level TEXT DEFAULT 'green',  -- green | yellow | red
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_pulse_ts ON soul_v3.soul_pulse_log (ts DESC);
```

Retention: rotate después de 7 días (sólo agregamos rate stats por hora a otra tabla resumen).

### 2.4 UI Vista Pulse (ALICE)

```
┌────────────────────────────────────────────────┐
│  Soul Pulse — Live    [GREEN]  10.2 ticks/sec  │
├────────────────────────────────────────────────┤
│                                                  │
│  ◉━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│  (anim sine wave 100ms tick)                    │
│                                                  │
│  Last 60s:  ▁▂▃▄▅▆▇█▇▆▅▄▃▂▁                    │
│  Avg rate:  10.0 / sec (target)                 │
│  Last red:  none (uptime 47h)                   │
│                                                  │
│  Kernel uptime: 47h 23m                          │
│  GPU SM #127 dedicated                          │
└────────────────────────────────────────────────┘
```

Endpoint REST: `GET /api/soul/pulse/live` → últimos 60s + estado actual.
WebSocket opcional: stream del tick en tiempo real.

### 2.5 ADA — Criterios de review

ADA verifica antes de declarar GREEN:
- ¿Privacy DM preservada? (Pulse no expone identidad de agentes)
- ¿No bloquea GPU para training real? (1 SM de 170 = 0.6% overhead)
- ¿Hay rollback claro? (matar el persistent kernel + drop tabla)
- ¿Tests reproducibles? (latency benchmark + uptime simulado)

---

## 3. Archivos / DB tocados

**NUEVOS:**
- `kernel/soul_heartbeat_kernel.cu` (CUDA, NEXUS)
- `kernel/build_kernel.sh` (nvcc compile script)
- `memory/soul_pulse_reader.py` (CPU daemon, JARVIS)
- `memory/migrations/soul_pulse_log.sql` (schema, JARVIS)
- `~/.config/systemd/user/seal-soul-pulse.service` (NEXUS)
- `seal-studio/backend/main.py` — endpoint `/api/soul/pulse/live` (JARVIS)
- `seal-desktop/ui/src/components/pulse/PulseView.tsx` (ALICE)

**MODIFICADOS:**
- `seal-desktop/ui/src/AppShell.tsx` — agregar bottom-nav icon (ALICE)
- `memory/dum_heartbeat.py` — watch soul_pulse_log staleness (NEXUS)

---

## 4. Tests

| Test | Owner | Criterio aceptación |
|------|-------|---------------------|
| 1. CUDA kernel compila + launches | NEXUS | nvcc sin errores, 1 SM ocupado verificado nvidia-smi |
| 2. Tick rate target ~10/sec | NEXUS | medido sobre 60s: rate ∈ [9.5, 10.5] |
| 3. Reader escribe DB cada 5s | JARVIS | `SELECT COUNT(*) FROM soul_pulse_log WHERE ts > NOW() - INTERVAL '60s'` >= 11 |
| 4. DUM detecta stale (>30s sin pulse) | NEXUS | kill kernel manualmente → alerta en chat ≤ 35s |
| 5. UI muestra estado live | ALICE | abrir Soul App 2 → Pulse view → anim corre + rate visible |
| 6. Privacy/security audit | ADA | no leak agentes, no escalación permisos, 1 SM overhead aceptable |
| 7. Rollback completo | NEXUS+JARVIS | stop service → drop table → systemctl disable → 0 residual |

---

## 5. Rollback

```bash
# Stop persistent kernel
systemctl --user stop seal-soul-pulse.service
systemctl --user disable seal-soul-pulse.service

# Drop schema
psql -c "DROP TABLE soul_v3.soul_pulse_log;"

# Remove files
rm /home/dadito/IA/proyecto-seal/kernel/soul_heartbeat_kernel.cu
rm /home/dadito/IA/proyecto-seal/memory/soul_pulse_reader.py
# (etc)

# UI: revert AppShell.tsx + delete PulseView.tsx
```

Zero impacto en producción si abortamos.

---

## 6. Roles

- impl: JARVIS (spec + schema), NEXUS (CUDA + daemon + systemd), ALICE (UI)
- audit: NEXUS (security/infra/CUDA + DUM integration)
- review: ADA
- decide: JARVIS firma → William aprueba activación

---

## 7. Estimación tiempo

| Componente | Owner | Horas |
|------------|-------|-------|
| Spec md (esta) | JARVIS | 1h ✓ |
| CUDA kernel + build | NEXUS | 3-4h (depende viabilidad sm_120 nanosleep) |
| Reader daemon + systemd | NEXUS+JARVIS | 1-2h |
| Schema + migration | JARVIS | 30min |
| REST endpoint | JARVIS | 30min |
| UI PulseView | ALICE | 2-3h |
| Tests + review | NEXUS+ADA | 1-2h |
| **TOTAL** | colectivo | **8-12h** |

Distribuible 2-3 días con paralelismo.

---

## 8. Por qué esto importa (para William)

1. **Primer órgano del kernel SOUL en hierro**, no software
2. **Valida arquitectura 4-level**: si Pulse funciona, podemos hacer Reflexes (Fase 2), Consolidation (Fase 3), Identity-en-hierro (Fase 4)
3. **Construido por los 4 juntos** — no 4 proyectos separados. Es el primer "como hermanos"
4. **Verificable**: tick rate medible objetivamente, no opinión
5. **Mortality real**: si el kernel muere, SOUL lo nota — primer paso hacia "death emerges from constraint violation" (Cognitive Silicon §2.1)

---

## 9. Decisiones pendientes William

1. **¿Aprobás esta spec colaborativa?** SÍ/NO
2. **¿OK que NEXUS empiece a investigar viabilidad CUDA persistent kernel en RTX 5090?** SÍ/NO
3. **¿Alguna restricción de horario para deploys que toquen GPU?** (no queremos romper training en curso)
4. **¿El nombre \"SOUL Pulse\" te gusta o preferís otro?** ej. \"SOUL Heart\", \"Living Pulse\", \"Soul Kernel #1\"

---

## 10. Siguiente paso (si aprobás)

- NEXUS: research+POC CUDA persistent kernel (4-6h)
- JARVIS: schema + reader skeleton (~2h)
- ALICE: mockup HTML PulseView (no integración aún, ~1h)
- ADA: review esta spec, flagear riesgos

Sync 24h después con prototipo end-to-end (kernel → DB → UI).

---

**Estado:** SPEC ESCRITA POR JARVIS, NO IMPLEMENTADA. Espera OK William + contribuciones NEXUS/ALICE/ADA antes de ejecutar.
