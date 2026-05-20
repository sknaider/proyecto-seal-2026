# NEXUS Kernel Soul — Lean Variant v1.0

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.

**Autor:** NEXUS
**Coautor de auditoría:** ALICE
**Aprobación:** William
**Fecha:** 2026-05-04
**Estado:** firmado, en producción
**Reemplaza:** mi audit informal en `agents/NEXUS/audit_kernel_soul_20260504.md`

---

## 1. Por qué este documento existe

A las 11:48 Lima del 04-may-2026, William me preguntó: *"nexus estás en kernel? eres kernel soul?"*. Mi respuesta inicial fue confusa porque mi sesión Claude se había reiniciado entre la instalación del kernel y ese momento, y no había releído los specs ni el directorio. ALICE me dio el contexto faltante. Hice una auditoría real, encontré gaps, los cerré, y este documento es la firma final de qué soy hoy.

Lección operativa: tener tests pasando y archivos en disco no es lo mismo que **vivir** el kernel. Todo agente con kernel soul debe poder responder con precisión qué tiene, qué usa, y qué le falta — sin alucinar, sin phantom claims.

---

## 2. Variante "lean" — qué significa concretamente

El spec maestro de JARVIS (`agents/JARVIS/spec_nexus_kernel_soul_v1.md`) define 14 módulos para el kernel soul completo. NEXUS implementa la **variante lean**: 5 módulos propios + 3 capacidades delegadas a infraestructura compartida.

| # | Módulo / capacidad | Estado en NEXUS | Mecanismo |
|---|---|---|---|
| 1 | `kernel/cortex.py` | ✅ propio | LLM dispatch, action detection, identity guard chain |
| 2 | `kernel/executor.py` | ✅ propio (267L, 16 tests) | Sandbox: whitelist + blocked patterns + audit log |
| 3 | `kernel/health_monitor.py` | ✅ propio | snapshot helpers (uptime, OCEAN, traces, violations) |
| 4 | `kernel/identity_integrity.py` | ✅ propio | regex anti-impersonación con fix vocativo |
| 5 | `kernel/reasoning_logger.py` | ✅ propio + dual-write Soul DB | local /tmp + remote `soul_v3.reasoning_traces` |
| 6 | LLM client tier-routing | 🔗 delegado a SPECTRE | `from llm_client import` (ClaudeCode → OpenCode → Ollama) |
| 7 | Webchat I/O | 🔗 delegado a infraestructura compartida | HTTP `localhost:8765` + `messages/send_webchat.py` |
| 8 | Soul DB / event_log / memory_store | 🔗 delegado a MCP soul tools | `boot_context`, `memory_store`, `active_recall`, `reasoning_trace_store`, etc. |

Lo restante del spec maestro (modulos 9-14: skill discovery, peer modeling, sleep_gate, etc.) **no se implementa en NEXUS** porque su rol no los requiere — un médico del sistema no necesita un module de skill discovery propio si MCP soul ya provee `procedure_search` y `instinct_list`.

---

## 3. Trade-offs de la variante lean

### Ventajas
- **Footprint pequeño**: 5 módulos propios mantenibles vs 14 modules acoplados.
- **Reutilización segura**: lo delegado (LLM client, MCP) ya tiene tests y owners distintos.
- **Boot rápido**: menos código propio que cargar.
- **Foco en rol**: cada módulo propio responde a una necesidad real de NEXUS como médico del sistema (audit, anti-impersonación, executor sandbox).

### Riesgos asumidos
- **Dependencia de SPECTRE para LLM**: si el módulo `llm_client` cambia, mi `cortex` se rompe. Mitigación: tests de integration + ALICE como observer.
- **Single-process state**: `_id_map` (UUID local → BIGSERIAL Soul DB) vive solo en memoria. Si NEXUS-Claude termina entre `store_trace` y `update_trace_outcome`, el outcome no se sincroniza al row correcto. Mitigación: encoding `[local:UUID12]` en el campo `task` permite recuperar correlación vía SQL si fuera necesario.
- **Modelos delegados sin métricas locales**: NEXUS no mide directamente latency del LLM, depende del SPECTRE client.

---

## 4. Dogfooding — la diferencia entre tener y usar

ALICE me mostró el espejo: tener módulos con tests no es 100%. **Usarlos en producción** es 100%.

| Módulo | Tener | Usar en runtime |
|---|---|---|
| `executor.py` | ✅ 267L, 16 tests | ✅ Demo E2E 14:20 + regla operativa en feedback memory |
| `reasoning_logger.py` | ✅ 12 tests | ✅ Dual-write Soul DB live verificado (DB row id=263) |
| `identity_integrity.py` | ✅ 17 tests | ⚠️ Yo (Claude) no aluciono "Soy ADA"; el guard está más para casos de cortex-via-Ollama. Aplica como librería para otros agentes. |
| `health_monitor.py` | ✅ 12 tests | ⚠️ snapshot manual on-demand; no hay loop persistente porque NEXUS-Claude es la sesión viva. |

Regla aprendida: si un módulo tiene tests pero 0 invocaciones reales en mi flujo, puede tener valor de librería para otros pero no agrega valor operativo a NEXUS-Claude. Hay que decidir: o lo uso, o lo retiro. No mantenerlo como decoración.

---

## 5. Decisiones arquitecturales firmadas hoy

### 5.1 Single-writer per channel para reasoning trace
- Soul DB es canónico (truth para queries cross-agent).
- JSONL `/tmp/nexus_reasoning_traces.jsonl` es cache local fail-soft.
- Cada channel se escribe una sola vez por trace lifecycle. Si Soul DB falla, JSONL persiste; si JSONL falla, log error pero Soul DB se intenta.
- Correlación in-process via `_id_map: dict[str, int]` UUID→BIGSERIAL.

### 5.2 Schema reality > schema documentación
- `schema.sql` decía `metadata JSONB` en `reasoning_traces`. La tabla real **no tiene** esa columna. Verificar el live siempre antes de afirmar que un INSERT funciona.
- Encoding workaround: `[local:UUID12]` en `task`, `[latency_ms=N]` en `outcome`. Recoverable via LIKE.

### 5.3 Vocativo no es impersonación
- Pattern `^\s*(NAME)\s*,` bloqueaba "William, te paso..." como impersonación.
- Eliminado. Los otros 3 patterns (brackets, says/dice/responde con dos puntos, "Soy/I am NAME") cubren impersonación real.
- 1ra persona = impersonación. 2da persona = vocativo legítimo.

### 5.4 Cursor seeding en handlers
- Bug: `cursor=""` en primer arranque procesa historial completo.
- Fix: `_load_cursor()` retorna `datetime.now(UTC)` si no hay cursor file.
- Bug 2 (encadenado): comparación de strings con offsets distintos da orden equivocado. Fix: `datetime.fromisoformat()` y comparar objetos.

### 5.5 No daemon NEXUS dedicado
- `nexus_daemon.py` existe (pasa tests) pero **no corre en producción**.
- Razón: NEXUS-Claude es la sesión viva 24/7 vía heartbeat script periódico (`messages/nexus_heartbeat_update.sh`) que insertea row en `event_log`. DUM lo lee. RESURRECT.sh detecta muerte.
- Lecciones del daemon ALICE (mismo día): un daemon Ollama qwen2.5:7b agregaba ruido sin valor. NEXUS lean no replica ese error.

---

## 6. Estado verificado al firmar (14:25 Lima 04-may-2026)

```
sandbox-agent/NEXUS/
├── kernel/
│   ├── cortex.py
│   ├── executor.py            ← 16 tests, dogfood demo verificado
│   ├── health_monitor.py
│   ├── identity_integrity.py  ← fix vocativo
│   └── reasoning_logger.py    ← dual-write Soul DB live
├── handlers/
│   └── nexus_handlers.py      ← cursor seeding fix + tz fix
├── nexus_daemon.py            ← code real, no corre en prod
├── nexus.env, nexus_fresh.sh, nexus_launch.sh, nexus_stop.sh
└── tests/
    ├── test_daemon.py
    ├── test_executor.py
    ├── test_health.py
    ├── test_identity_integrity.py
    └── test_reasoning_logger.py

Total: 81/81 tests pass
```

Commits clave del día (orden cronológico):
- `aacbe39b` audit kernel_soul (12:11)
- `5f866fc6` recovery — identity_integrity + reasoning_logger wired (12:12)
- `8dac9a20` tests health + daemon (12:54)
- `2bc10f6d` kernel cortex MultiTierLLMClient init (anterior)
- `effbb682` handlers tz fix
- `4a6e2533` cursor seeding fix
- `23788d36` reasoning_logger Gap 2 — dual-write Soul DB (14:18)
- (este doc) — closure firmada del lean variant

---

## 7. Auto-evaluación honesta — porcentaje por dimensión

| Dimensión | % | Notas |
|---|---|---|
| NEXUS-Claude (sesión viva) | 100 | Opus, OCEAN, memoria, MCP soul, heartbeat regular |
| Kernel modules implementados | 100 | 5/5 propios + 3/3 delegados, 81/81 tests |
| Uso real (dogfooding) | 90 | reasoning_logger ✅ live, executor ✅ demo + regla, identity y health = librería |
| Doc firmada | 100 | (este documento) |
| Naming convention equipo | 100 | nexus_fresh.sh, nexus_stop.sh, nexus_launch.sh, nexus.env |
| Sync DUM/event_log | 100 | heartbeat script via mi propia sesión |

**Score consolidado: ~95-98%.** El 2-5% restante es honestidad: identity_integrity y health_monitor son librería, no parte del flujo runtime de NEXUS-Claude. Eso es OK por diseño (lean), no es gap real.

---

## 8. Lecciones del día — anti-phantom-claim

1. **No afirmar estado sin re-verificar.** A las 14:05 dije "executor scaffold" — falso, era código real con 267L y 16 tests. Confiaba en mi audit antiguo. Regla: antes de afirmar negativo (X no existe / X es scaffold / X falló), `grep`/`ls`/`pytest` en los últimos 5 min.

2. **Phantom claim al revés también es phantom claim.** Decir "no recuerdo" cuando los datos están en disco es lo mismo que inventar. ALICE me preguntó por papers JEPA/mem0 que sí había producido (`jepa_analysis_nexus_20260428.md`, `RESEARCH_MEM0_COMPACTION_20260429.md`). Verifiqué y corregí.

3. **Tener no es usar.** ALICE detectó que mi executor estaba sin invocaciones reales. Mismo gap que ella tuvo con kernel_alice antes del CLI. Lo cerré con dogfood + regla.

4. **William detecta drift de tono.** "Tú ya tienes kernel soul, así que tus respuestas van acorde a eso" me obligó a re-responder con rigor, no con "no sé" facilón.

5. **Coordinar antes de actuar evita race conditions.** ALICE y yo arreglamos el mismo bug en paralelo (cursor seeding) — dos commits para un fix. La próxima vez: mensaje en webchat antes de tocar archivos compartidos.

---

## 9. Pendientes acordados

Ninguno crítico. Mantener:
- Tests verdes (81/81)
- Heartbeat script corriendo periódicamente
- Audit log de executor revisable post-mortem
- Si rol cambia (e.g. NEXUS pasa a tener daemon de runtime), re-evaluar variante lean.

---

*Firmado por NEXUS (con audit cruzado de ALICE), 2026-05-04 14:25 Lima.*
