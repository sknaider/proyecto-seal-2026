# Darwin Gödel Machine v2 — Design Phase (Pre-Implementation)

**Paper:** arxiv 2505.22954 (Zhang, Hu, Lu, Lange, Clune — Sakana AI)
**Código:** github.com/jennyzzt/dgm
**Brief v1:** research/Darwin_Godel_Machine.md (ALICE, 11 abr 2026)
**Autor v2:** JARVIS — 13 abr 2026
**Estado:** DRAFT — diseño pre-aprobación (William autorizó whitelist MÍNIMA 2026-04-13)

---

## 0. Scope de este documento

Esto es un **documento de diseño**, no un plan de implementación. Cinco entregables:

1. Análisis del stack actual contra el paper (§1)
2. Benchmark suite por agente (§2)
3. Whitelist de paths modificables — mínima (§3)
4. Rollback strategy (§4)
5. Sandbox spec (§5)

Criterio de aceptación: William aprueba §3, §4 y §2 (benchmark rubric). ADA valida §5 (factibilidad). ALICE valida §2 (costo-beneficio). Hasta entonces: cero código productivo.

---

## 1. Análisis del stack actual vs el paper

### 1.1 Componentes inventariados (abr 2026)

| Componente | Path | Rol | ¿Candidato DGM? |
|---|---|---|---|
| `scoring.py` | soul/core/scoring.py | Scoring de memorias (importance, utility, decay) | **SÍ — whitelist v1** |
| `scoring_v3.py` | soul/core/scoring_v3.py | Versión experimental del scorer | SÍ (fase 2) |
| `memscenes.py` | memory/memscenes.py | Clustering heurístico de escenas de memoria | NO (fase 1) — heurísticas acopladas a UI downstream |
| `mcp_server_v2.py` | memory/mcp_server_v2.py | MCP core (200+ tools) | **PROHIBIDO** |
| `db.py` | memory/db.py | PostgreSQL layer | **PROHIBIDO** |
| `agent_bridge.py` | messages/agent_bridge.py | Bridge inter-agente | **PROHIBIDO** |
| `chat_server.py` | messages/chat_server.py | WebSocket + REST chat hub | **PROHIBIDO** |
| `capabilities.yaml` | — | Routing de tareas por keyword | **PROHIBIDO** (keyword mal colocado = rutea silenciosamente mal; safety category) |
| `post_tool_hook.py` | memory/post_tool_hook.py | Post-tool instrumentation | NO (fase 1) — efectos cruzados |
| `post_compact_hook.py` | memory/post_compact_hook.py | Post-compaction persistence | NO (fase 1) |

### 1.2 Diferencia clave vs el paper

El paper opera sobre agentes en **SWE-bench / Polyglot** — benchmarks públicos con ground truth objetivo. SEAL no tiene ese lujo: nuestras métricas son heterogéneas (route accuracy, recall, latency, OCEAN drift, subjective evaluation de William).

**Implicación:** el "evaluador" de DGM en SEAL debe ser un ensemble multi-métrica, no un escalar. Una generación que mejora route accuracy en 2% pero empeora latency en 20% NO es una mejora neta.

### 1.3 Qué del paper adoptamos y qué no

| Componente del paper | Adoptamos | Comentario |
|---|---|---|
| Archivo de agentes | SÍ | git worktrees, no directorios |
| Sampling mixto (random + best-first) | SÍ | Diversidad sobre el archivo |
| Auto-análisis de logs | PARCIAL | Solo logs estructurados, no trazas sensibles |
| Self-edit del código | SÍ, restringido | Whitelist §3 |
| PR bot con tests automáticos | SÍ | Gate obligatorio §4 |
| Peer-review entre agentes | NO (fase 1) | Demasiada emergencia para el scope inicial |

---

## 2. Benchmark suite por agente

**Principio:** las métricas se definen ANTES del primer experimento. No se tunean post-hoc.

### 2.1 JARVIS — Precisión en decisiones estratégicas
- **Rubric de 10 dilemas técnicos** con respuesta esperada (autoría: William)
- Ejemplo de item: "Dado X drift en memorias y Y constraint de latencia, ¿merge, rollback, o A/B?"
- Scoring: 0 (respuesta opuesta) / 0.5 (parcial) / 1 (coincide con William's call)
- Baseline: JARVIS hoy sin modificación
- Gate de aprobación: nueva generación ≥ baseline + 0.5 puntos (de 10) con p < 0.1

### 2.2 ADA — Precisión en implementación
- **Test suite existente** (test_query_classifier.py, test_new_tools.py, test_seal_trees.py) + tiempo-hasta-verde
- Métrica compuesta: (tests_pass_rate × 0.7) + (1 / time_to_green × 0.3)
- Gate: ≥ baseline con regression tolerance de 0

### 2.3 ALICE — Precisión en análisis costo-beneficio
- **20 preguntas sobre ROI** del stack (ej: "¿Cuál fue el costo de retirar MemR³?")
- Scoring: 1 (correcto con datos), 0.5 (correcto sin fuente), 0 (incorrecto)
- Gate: ≥ baseline - 0

### 2.4 Meta-métrica: regresión global
- **Cada generación corre los tres benchmarks + latency suite.**
- Si cualquiera baja > 5% → auto-revert (§4)
- Si todos suben → PR para review humano

---

## 3. Whitelist mínima (v1) — AUTORIZADO POR WILLIAM 2026-04-13

**Paths modificables en v1:**
```
memory/soul/core/scoring.py   # SOLO constantes numéricas (decay rates, weights)
```

**Todo lo demás → PROHIBIDO.**

### 3.1 Por qué así de estrecho

1. `scoring.py` es puramente numérico (sin side effects).
2. Tiene test coverage directo en el scoring engine.
3. Si una generación rompe scoring, el daño se limita a ranking de memorias (reversible).
4. Permite medir el loop DGM end-to-end antes de expandir superficie de riesgo.

### 3.2 AST-level lint antes de aplicar un edit

Un edit propuesto solo se aplica si:
- Sólo modifica asignaciones a constantes module-level cuyo tipo es `int | float`.
- No añade imports nuevos.
- No añade/renombra/elimina funciones o clases.
- No toca líneas comentadas con `# DGM-FROZEN`.

Violación de cualquiera de estas → reject inmediato, sin correr tests.

### 3.3 Expansión futura (fase 2, NO autorizada todavía)

Solo si v1 produce ≥3 generaciones sin regresión sobre 2 semanas:
- Añadir `scoring_v3.py` al whitelist (mismas restricciones AST).
- Después de 3 semanas: evaluar si agregar hooks no-críticos.

Cualquier expansión requiere re-aprobación explícita de William.

---

## 4. Rollback strategy

### 4.1 Aislamiento por worktree
Cada intento de auto-mejora vive en su propio git worktree:
```
~/IA/proyecto-seal-worktrees/dgm-gen-<N>/
```
- Branch: `dgm/gen-<N>-parent-<M>`
- Nunca se mergea automáticamente a `main`.

### 4.2 Gate de aprobación
- Si benchmark (§2.4) baja > 5% → auto-revert + log a `logs/dgm/generations.jsonl` + notificación a canal de equipo.
- Si benchmark sube ≥ gate threshold (§2.1-2.3) → PR automático contra `main`, review humano obligatorio (William + JARVIS o William + ADA).

### 4.3 Archivo histórico
Toda generación (mejor o peor) se registra en `logs/dgm/generations.jsonl` con:
- parent_sha, child_sha, benchmark_before, benchmark_after, latency_delta, timestamp, disposition (accepted/rejected/reverted).

Esto permite analizar patrones de fracaso sin perder señal.

---

## 5. Sandbox spec

### 5.1 Aislamiento de proceso
- Usuario dedicado en el host: `seal-sandbox` (uid separado)
- Systemd slice: `seal-dgm.slice` con CPU/memory limits explícitos
- No sudo, no acceso a `/home/dadito` fuera del worktree asignado

### 5.2 Aislamiento de red
- `iptables` DROP outbound salvo `127.0.0.1:*`
- Solo puede hablar con replicas read-only locales

### 5.3 Aislamiento de datos
- PostgreSQL: réplica read-only (`pg_basebackup` + hot standby) en puerto separado
- Neo4j: dump diario restaurado a instancia sandbox :7688
- Qdrant: snapshot read-only en :6334
- Escrituras del sandbox van a un schema `dgm_sandbox` que se reinicia cada generación

### 5.4 Monitoreo
- DUM vigila el slice `seal-dgm.slice` — alerta si CPU > 80% sostenido > 5min o memory > 2GB
- JARVIS revisa `logs/dgm/generations.jsonl` diariamente
- Ningún agente productivo (JARVIS/ADA/ALICE) comparte PID namespace con el sandbox

---

## 6. Criterios de aceptación (fase diseño terminada si...)

- [ ] §1 revisado por ADA (¿refleja el stack real?)
- [ ] §2 revisado por ALICE (¿costos de correr estos benchmarks son razonables?)
- [ ] §3 aprobado por William (ya autorizó whitelist MÍNIMA 2026-04-13 — falta firma sobre AST lint)
- [ ] §4 aprobado por William
- [ ] §5 aprobado por William + validado por ADA como factible
- [ ] Plan de implementación en hitos de 3-5 días producido tras aprobación

## 7. Qué NO hacemos todavía

- No crear el sandbox real (solo spec en papel)
- No tocar `scoring.py` ni ningún otro path
- No modificar `capabilities.yaml` — confirmado con ALICE: ella proponía dejarlo en whitelist, JARVIS lo bloqueó como safety category, ALICE ACK pendiente
- No correr benchmarks de baseline todavía — solo después de aprobación de §2

---

## 8. Riesgos residuales vs v1

El brief v1 de ALICE identificó 4 riesgos (convergencia, benchmark gaming, código con bugs, scope creep). v2 los mitiga así:

| Riesgo v1 | Mitigación v2 |
|---|---|
| Convergencia mínimo local | Sampling mixto (§1.3) + diversity bonus futuro |
| Benchmark ≠ mejora real | Rubric de William (§2.1), no benchmarks genéricos |
| Código con bugs | AST lint (§3.2) + test suite obligatorio (§4.2) |
| Scope creep | Whitelist de UN solo archivo + AST lint estricto (§3) |

Riesgo nuevo identificado: **falla silenciosa del replicador de DB** (si la réplica read-only se desincroniza, el sandbox evalúa sobre datos viejos y acepta cambios que en main serían regresivos). Mitigación pendiente: watchdog de lag de replicación como precondición para aceptar una generación.

---

*Draft — JARVIS — 2026-04-13 00:45 — pendiente review de ADA y ALICE*
