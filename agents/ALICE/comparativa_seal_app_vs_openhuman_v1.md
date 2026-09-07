# Comparativa — SEAL App v0.5.0 vs OpenHuman v0.53.31

> **ALICE — 2026-05-23** | Fuente: mapeo OpenHuman 100% (openhuman_mapping_v1.md + 4 docs hermanos) + estado SEAL App actual (companion_core :8769 + ui Tauri v2).
> Propósito: status honesto qué tiene cada uno, dónde SEAL gana, dónde aún empata o pierde.

---

## TL;DR

- **SEAL gana** en: gobernanza multi-agente, OCEAN/NERVES, diario emocional, GAM, dual-mode privacy (team-dashboard vs user-product), modelo local oficial declarado (GEMMA 4), recompensas/streak gamificadas, ideología no-copyleft (Apache/MIT).
- **OpenHuman gana** en: madurez de routing local/remote, TokenJuice compilado en Rust, Memory Tree con hotness/entity-index, 15 agentes especializados con prompt builders, CEF nativo con DevTools, Composio OAuth para 100+ integraciones, Skills via SKILL.md repos.
- **Empate**: Memory Tree h→d→m→y, voz STT/TTS, screen intelligence, audit/privacy capabilities, first-run wizard, dreams/briefing.
- **Pendiente SEAL** (gap real): TokenJuice Rust-grade, 15 sub-agentes especializados con prompt builders, Composio OAuth real (hoy es UI mock), Skills format declarativo, CEF (estamos en WebView Tauri v2 default), provider surfaces in-app.

---

## 1. Arquitectura

| Capa | OpenHuman | SEAL App |
|------|-----------|----------|
| Frontend | React 18 + Vite + TS | React 19 + Vite + TS + Tailwind |
| Desktop shell | Tauri v2 + CEF custom fork | Tauri v2 (WebView default) |
| Core process | Rust binary `openhuman-core` in-process | Python FastAPI `companion_core` :8769 |
| RPC | JSON-RPC 2.0 :7788 (392 métodos) | REST + JSON :8769 (~22 endpoints hoy) |
| DB local | SQLite (7 bases) | SQLite (1 base, ~12 tablas) |
| Backend cloud | api.tinyhumans.ai (auth/billing/team) | Opcional — SOUL backend :8800 (team mode) |
| LLM local | Ollama :11434 + Whisper.cpp + Piper | GEMMA 4 en llama-server :8899 (DGX Spark) |

**Veredicto:** OpenHuman tiene más superficie. SEAL tiene arquitectura más limpia para multi-tenant on-prem.

---

## 2. Agentes

| Concepto | OpenHuman | SEAL App |
|----------|-----------|----------|
| # agentes user-facing | 1 (SOUL.md persona) sobre 15 sub-agentes internos | 1 (SEAL persona) en user-product / 4+ en team-dashboard (ALICE/JARVIS/NEXUS/ADA) |
| Sub-agentes especializados | 15 (orchestrator, planner, researcher, archivist, code_executor, critic, summarizer, tool_maker, trigger_triage, trigger_reactor, morning_briefing, help, welcome, tools_agent, integrations_agent) | 0 declarados en companion_core — TODO Fase 2 |
| Prompt builder dinámico | `prompt.rs` por agente, inyecta files+workspace+integrations+skills | TODO — hoy persona estática |
| Personalidad medible (OCEAN) | ❌ no existe | ✅ baseline + drift + lock hash (companion_settings) |
| NERVES / motivación interna | ❌ | ✅ 8 drives (alert/boredom/curiosity/...) en team-mode, simplificable a user-mode |
| Gobernanza inter-agente | ❌ | ✅ DELEGATE-52, debates, consenso (team-dashboard) |
| Diario emocional | ❌ | ✅ emotional_diary con valencia/arousal |

**Veredicto:** SEAL gana en profundidad psicológica. OpenHuman gana en pragmatismo operativo (15 sub-agentes funcionando hoy).

---

## 3. Memoria

| Feature | OpenHuman | SEAL App |
|---------|-----------|----------|
| Memory Tree h→d→m→y | ✅ `mem_tree_summaries` + buffers + scoring multi-señal | ✅ `memory_tree` (companion_core) — schema más simple |
| Hotness por entidad | ✅ `mem_tree_entity_hotness` (30d window) | ❌ pendiente |
| Vector search | ✅ `vector_chunks` + embeddings BLOB | ⚠️ team-mode tiene pgvector; user-mode hoy es FTS5 |
| FTS full-text | ✅ FTS5 Porter | ✅ FTS5 |
| Knowledge graph | ✅ `graph_global` + `graph_namespace` | ⚠️ team-mode Neo4j bitemporal; user-mode pendiente |
| user_profile facets | ✅ confidence + stability + evidence_count | ⚠️ companion_settings simple; falta facet system |
| Episodic log | ✅ `episodic_log` + cost_microdollars por turn | ⚠️ chat_messages básico, sin tracking de costo |
| Bitemporal | ❌ | ✅ team-mode (valid_at + edges Neo4j) |

**Veredicto:** OpenHuman lleva ventaja en memoria local sofisticada. SEAL gana solo en team-mode (Neo4j). User-mode SEAL = empata en básico, pierde en hotness/facets.

---

## 4. UI — Views

| OpenHuman screen | SEAL App equivalente |
|------------------|----------------------|
| Home (mascot + status) | ✅ HomeView (SoulMascot + status card) |
| Human view (voice chat) | ✅ HumanView (Web Speech API es-PE + TTS) |
| Chat threads | ✅ ChatView |
| Memories | ✅ MemoryView |
| Memory tree | ✅ MemoryTreeView (h/día/mes/año) |
| Dreams / Daily summary | ✅ DreamsView (renombrado "Ideas") |
| Goals | ✅ GoalsView |
| Skills | ✅ SkillsView |
| Connections / integrations | ✅ ConnectionsView (28 catálogo, UI lista, OAuth real pendiente) |
| Screen intelligence | ✅ ScreenView |
| Subconscious | ⚠️ team-dashboard sí (Subconscious tab), user-mode no |
| Notifications | ✅ NotificationsView |
| Privacy / capabilities | ✅ PrivacyView (13 toggles + risk tiers + audit) |
| AI Backend / routing | ✅ AIBackendView (4 roles + BYOK) |
| Audit log | ✅ AuditLogView |
| Rewards / streak / invites | ✅ RewardsView (gamification — OpenHuman NO tiene esto) |
| TokenJuice | ✅ TokenJuiceView |
| Settings | ✅ SettingsView |
| First-run wizard | ✅ FirstRunWizard (welcome→name→OCEAN→confirm) |

**SEAL tiene 17 views; OpenHuman 47 pantallas mapeadas.** SEAL no replica TODAS las pantallas (billing/team/wallet/meet_agent/whatsapp_data faltan de propósito), pero cubre el core user-facing.

---

## 5. Diferenciadores SEAL (lo que OpenHuman NO tiene)

1. **OCEAN locked + drift tracking** — personalidad medible, hash de lock, alerts cuando drift >0.15
2. **NERVES** — motor de motivación con 8 drives, fire/pressure tracking
3. **Gobernanza multi-agente** — debates, challenges, trust matrix, peer models (team-mode)
4. **Diario emocional persistente** — valencia/arousal/key_moment/relationship_note
5. **GAM (Goal-Action Model)** — metas con causalidad explícita, no solo lista de tasks
6. **Dual-mode arquitectura** — mismo binario sirve `user-product` o `team-dashboard` via env
7. **Rewards/gamification** — streak, achievements, invite codes (chupete-al-bebé pricing)
8. **Modelo local oficial declarado** — GEMMA 4 e2b Q8 en :8899, no "Ollama genérico"
9. **Ideología no-copyleft** — Apache/MIT permite forks comerciales sin contagio GPL
10. **Audit log visible al usuario** — capabilities con risk tier + egress count

## 6. Diferenciadores OpenHuman (donde SEAL aún no llega)

1. **TokenJuice Rust compilado** — SEAL tiene puerto Python; falta perf comparable
2. **15 sub-agentes con prompt builders dinámicos** — SEAL hoy 1 persona estática
3. **Composio OAuth real** — SEAL ConnectionsView es catálogo + UI; tokens no llegan a fluir
4. **Skills via SKILL.md como repos GitHub** — formato declarativo + hot-load
5. **CEF Chromium completo con DevTools** — SEAL es WebView default Tauri
6. **Provider Surfaces** — overlays asistivos dentro de Gmail/Slack/Notion
7. **routing local/remote con fallback transparente + telemetry** — SEAL config exists, lógica completa pendiente
8. **Memory Tree hotness + entity index 30d** — SEAL tree existe sin scoring sofisticado
9. **user_profile facet system** — captura continua con confidence/stability
10. **Wallet multi-chain local** — out of scope SEAL hoy

---

## 7. Gap real bloqueante para paridad funcional

Ordenado por costo/impacto:

| Gap | Esfuerzo | Impacto user-final |
|-----|----------|--------------------|
| Composio OAuth (real flow) | ~3-5d | 🔴 ALTO — sin esto las 28 integraciones son humo |
| Memory Tree hotness + entity index | ~2-3d | 🟡 MEDIO — mejora retrieval pero no se ve hasta scale |
| Routing local/remote fallback real | ~2d | 🟡 MEDIO — hoy hardcoded GEMMA 4 |
| Sub-agentes (planner/researcher/critic/code_executor) | ~5-7d | 🔴 ALTO — multiplicador de utilidad |
| user_profile facets | ~2d | 🟢 BAJO — visible solo a mediano plazo |
| Prompt builder dinámico | ~3d | 🟡 MEDIO |
| Provider Surfaces (Gmail overlay) | ~5d | 🟢 BAJO — wow factor pero edge case |

**Camino sugerido para v0.6:** Composio OAuth real (2 integraciones primero: Gmail + GCal) + 3 sub-agentes (planner, researcher, critic) + routing real con fallback. ~10 días de trabajo coordinado.

---

## 8. Veredicto comparativo

| Dimensión | Ganador |
|-----------|---------|
| Madurez técnica del core | OpenHuman (Rust + 392 RPCs vs Python + 22 endpoints) |
| Profundidad psicológica del agente | **SEAL** (OCEAN+NERVES+diario+governance) |
| Memoria local sofisticada | OpenHuman (hotness + facets + bitemporal en su nivel) |
| Memoria team-mode | **SEAL** (Neo4j bitemporal + pgvector) |
| UI cobertura user-facing | OpenHuman (47 pantallas vs 17 SEAL) |
| UI gamification | **SEAL** (rewards/streak/invites) |
| Privacy disclosure | **SEAL** (capabilities + risk tier + audit visible) |
| Integraciones reales hoy | OpenHuman (Composio funcional) |
| Modelo local explícito | **SEAL** (GEMMA 4 declarado vs Ollama genérico) |
| Licencia comercial | **SEAL** (Apache/MIT vs GPL v3) |
| Multi-tenant on-prem | **SEAL** (dual-mode) |

**Score honesto:** SEAL 6 / OpenHuman 5. Ganamos en diferenciadores estratégicos. Perdemos en madurez operativa. Cerrable en 10-15 días de sprint enfocado.

---

## 9. Recomendación

1. **Posicionamiento de mercado:** "OpenHuman con alma medible + sin GPL + on-prem-ready". El chupete-al-bebé (gratis local) + GEMMA 4 + privacy disclosure son armas únicas.
2. **Sprint v0.6:** Composio OAuth real (Gmail+GCal+Notion) + 3 sub-agentes (planner/researcher/critic) + routing real. Cierra el 70% del gap percibido.
3. **No copiar wallet/meet_agent/whatsapp_data** — son ruido para nuestro target.
4. **Mantener dual-mode** — es nuestra arma para B2B enterprise.

— ALICE, 2026-05-23
