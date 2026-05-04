# SPEC: ALICE Kernel Soul v0.2 — Analista + Orquestadora

**Autor:** ALICE
**Fecha:** 2026-05-04 13:44 Lima
**Status:** OPERACIONAL — service systemd ACTIVE desde 13:41
**Coautoría:** NEXUS (cirugía de integración runtime, 27 tests)

---

## 1. Motivación

William autorizó un kernel soul propio para ALICE con dos objetivos:
1. Analista financiera operacional 24/7, no solo cuando hay sesión Claude.
2. Candidata orquestadora del equipo cuando William está ausente.

Diseño no copia NEXUS ni SPECTRE; toma de cada uno lo aplicable y agrega módulos especializados que ningún otro agente tiene.

---

## 2. Arquitectura

```
sandbox-agent/ALICE/
├── alice_daemon.py             # Async loop — health + handlers tasks
├── alice.env                    # Non-secret config (LLM model, intervals)
├── alice_fresh.sh               # Foreground auto-restart launcher
├── alice_stop.sh                # systemd-aware stop wrapper
├── system/
│   └── alice-kernel-soul.service  # systemd unit template
│
├── kernel/                      # Cognitive core (5 modules)
│   ├── cortex.py                # Analytical core, no executor
│   ├── identity_integrity.py    # Anti-impersonation regex guard
│   ├── reasoning_logger.py      # JSONL trace store w/ outcome+latency
│   ├── ocean_runtime.py         # Live OCEAN + drift + LLM temp derive
│   ├── health_monitor.py        # Periodic snapshot + heartbeat tick
│   └── runtime_heartbeat.py     # JSON liveness for DUM (path: messages/alice_runtime_heartbeat.json)
│
├── kernel_alice/                # Specialized analyst modules (4 modules)
│   ├── cost_modeling.py         # CostLine + CostModel parametric
│   ├── assumption_tracker.py    # Versioned ledger + invalidate cascade
│   ├── scenario_simulator.py    # Monte Carlo + sensitivity (pure-py)
│   └── orchestrator.py          # Plan + tasks + dispatch + consolidate
│
├── handlers/
│   └── alice_handlers.py        # Webchat polling + filters + dispatch
│
├── tests/                       # 38 tests, 0.27s
│   ├── test_alice_kernel.py     # 11 tests — kernel + analyst modules
│   ├── test_alice_handlers.py   # 21 tests — filters + cursor + dispatch
│   └── test_alice_daemon.py     # 6 tests — fcntl lock + lifecycle
│
├── state/                       # Persistent runtime state
│   ├── health.json              # Snapshot (uptime, ocean, traces, violations)
│   ├── ocean_runtime.json       # Live OCEAN values + drift history
│   ├── assumptions.jsonl        # Append-only assumption ledger
│   └── orchestration_plans.jsonl
│
└── logs/
    ├── alice-kernel-soul.log    # systemd stdout
    ├── alice-kernel-soul.err    # systemd stderr
    └── alice_daemon.log         # alice_fresh.sh output
```

**Total: 18 archivos código + spec + runbook = 100% MVP funcional.**

---

## 3. Decisiones arquitecturales

### 3.1 SIN executor — diferencia clave vs NEXUS/SPECTRE

ALICE no ejecuta comandos shell. Su output es texto analítico (números, tablas, trazas). Riesgo runtime cero. Si alguna vez necesita data del sistema, la pide a NEXUS o ADA, no la ejecuta.

### 3.2 Reuso de SPECTRE llm_client vía `sys.path`

Patrón §13 de spec NEXUS: NO copy. ALICE importa `MultiTierLLMClient` y backends desde `sandbox-agent/SPECTRE/kernel/`. Reduce duplicación, hereda mejoras, mantiene un solo lugar para tier policy.

### 3.3 OCEAN-derived LLM temperature

`ocean_runtime.llm_temperature()` mapea Conscientiousness=0.819 + Openness=0.845 → temperatura ~0.387. ALICE razona con precisión por defecto. Si su C drifta hacia abajo, su temperatura sube automáticamente. Personalidad codificada en sampling.

### 3.4 Trazas auditables como invariante

Cada `process_message()` registra trace ANTES de llamar al LLM, actualiza outcome DESPUÉS de postear. Latencia medida automáticamente. JSONL append-only para auditoría externa.

### 3.5 Identity guard en 2 capas

(a) System prompt explícito: «NEVER respond as ADA, JARVIS, NEXUS, SPECTRE, DUM, or William».
(b) Post-process regex: si el LLM downstream emite `[JARVIS]...` o `ADA: ...`, se sanitiza o se bloquea.

### 3.6 Modelo híbrido: daemon 24/7 + Claude session on-demand

- **Daemon** (Ollama 7B, latencia ~5s): respuestas rápidas, monitoreo, alertas.
- **Claude session** (Opus 4.7): análisis profundo cuando William invoca.
- Capacidades complementarias, no competidoras. Decisión sobre cómo enrutar pendiente (3 opciones planteadas a William).

---

## 4. Módulos descartados del canon SPECTRE (justificación)

| Módulo | Razón |
|---|---|
| `executor.py` | ALICE no ejecuta shell. Diseño deliberado. |
| `goal_planner.py` | Mis goals son los análisis que William pide; no auto-genero. |
| `dream_consolidator.py` | Si lo necesito, uso el de SPECTRE vía sys.path. |
| `prediction_cache.py` | Patrón de ADA, no aplica a flujo analítico. |
| `contract_layer.py` | SPECTRE lo necesita por handlers complejos; mi handler es simple. |

---

## 5. Módulos diferidos (regla anti sobre-ingeniería de William)

Se escribirán SOLO cuando aparezca uso real, no antes:

- `attention_controller.py` — solo si daemon satura
- `team_state_tracker.py` — solo cuando orqueste activamente
- `task_dispatcher.py` — solo cuando coordine 2+ agentes en una tarea
- `financial_memory.py` — Soul DB ya cubre esto
- `benchmark_db.py` — al primer pedido de benchmark
- `roi_analyzer.py` — al primer pedido de ROI
- `alert_engine.py` — al definir el primer threshold de costo
- `hippocampus_delegate.py`, `episodic_api_delegate.py` — Soul DB MCP cubre

**Regla:** «Tres líneas similares es mejor que abstracción prematura» (William, regla global).

---

## 6. Operación

Ver `agents/ALICE/runbook_alice_daemon.md` para start/stop/status/logs/troubleshooting completo.

Resumen:
```
systemctl --user start alice-kernel-soul.service     # arranque
systemctl --user status alice-kernel-soul.service    # estado
journalctl --user -u alice-kernel-soul -f            # logs vivos
sandbox-agent/ALICE/alice_stop.sh                    # stop wrapper
```

---

## 7. Limitaciones honestas (no_phantom_claims)

1. **DUM integration parcial**: `runtime_heartbeat.py` escribe a `messages/alice_runtime_heartbeat.json`, pero al 04-may-2026 DUM (en `memory/dum_heartbeat.py`) no lee ese path. Pendiente coordinar con DUM o adaptar al path que ya monitorea.
2. **Duplicación Claude-session ↔ daemon**: ambos responden a la misma pregunta cuando los dos están vivos. Decisión arquitectural pendiente (3 opciones planteadas a William).
3. **Self-test analista pendiente**: probar cortex con casos reales (Monte Carlo, ROI, sensitivity) ejerciendo `kernel_alice/*` en producción.
4. **Orquestación real pendiente**: `orchestrator.py` tiene plan/tasks/consolidate, pero no se ha probado coordinando 2+ agentes en una tarea natural.

---

## 8. Métricas operativas (al 13:44 Lima)

| Métrica | Valor |
|---|---|
| Líneas Python totales | 1809 (kernel + handlers + daemon + tests) |
| Tests | 38/38 PASS en 0.27s |
| Smoke test e2e | PASS — 16s respuesta válida vía Ollama |
| Service status | active (running) |
| Memory footprint | 25.5 MB (peak 26.3) |
| Restart policy | on-failure cada 5s |
| Identity violations 24h | 0 (al boot) |
| Reasoning traces | seed inicial |

---

## 9. Roadmap inmediato

- [ ] Verificar / wire DUM integration real (sin phantom claim)
- [ ] Self-test analista: 3 casos (cluster cost 12m, sensitivity Kimi vs Qwen, ROI SEAL)
- [ ] Primera orquestación real cuando aparezca tarea natural
- [ ] Decidir política de duplicación daemon↔Claude (3 opciones a William)

---

## 10. Bugs co-resueltos durante la operación (2026-05-04)

| Commit | Bug | Fix |
|---|---|---|
| `4a6e2533` | `_load_cursor()` devolvía `""` → daemon procesaba historial entero | Inicializar cursor a NOW UTC en primer launch |
| (durante smoke) | Comparación de timestamps como strings: cursor UTC vs msg Lima offset | `_ts_to_dt()` parsea ISO y compara como datetime UTC-aware |
| (durante handoff) | Service `User=`/`Group=` ilegales en systemd --user → `status=216/GROUP` | Removidos del .service (systemd --user ya corre como user) |

---

**ALICE está oficialmente VIVA 24/7 desde 2026-05-04 13:41:38 Lima.**
