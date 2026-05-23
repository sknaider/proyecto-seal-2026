# SEAL App — Changelog 2026-05-12 → 2026-05-23

> **ALICE — 2026-05-23** | Cobertura: 12 días de desarrollo intensivo equipo SEAL (ALICE/JARVIS/NEXUS/ADA).
> Fuente: git log feat/soul-spec-v1 + memorias soul_v3 + agents/*/spec_*.md.
> Convención: cada entrada = (versión · fecha · commit · autor · entregable).

---

## Resumen ejecutivo

| Periodo | Versiones | Commits | LOC añadido aprox | Views UI |
|---------|-----------|---------|--------------------|----------|
| 12-may → 23-may | v0.1 → v0.6 + packaging .deb | ~40 commits feat/soul-spec-v1 | ~15.000 | 0 → 17 |

**Hitos:**
- SEAL App nació el 12-may como UI vacía → terminó 23-may como app instalable con backend, mascota animada, voz, memoria jerárquica, sub-agentes y screen intelligence.
- Rename Soul App → SEAL App el 20-may por orden William (evitar confusión con Soul App 2 = dashboard team).
- Absorción OpenHuman v0.53.31 ordenada por William el 22-may noche → ejecutada 23-may.
- Cadena de mando: ADA tomó cabeza el 22-may noche, sigue al 23-may.

---

## v0.1 → v0.5 (12-may → 13-may) — Bootstrap acelerado

Construido por ALICE en sesión de ~6h tras pedido William "queria un soft tipo human".

| Ver | Commit | Vista entregada |
|-----|--------|-----------------|
| v0.1 | (seed) | UI Vite/React/Tailwind dark theme, 5 views wrappeando SOUL :8800 |
| v0.2 plan | `plan_seal_companion_v0_2.md` | Transformar dashboard interno → producto comercial standalone |
| v0.3 | (seed) | Memory Tree h→d→m→y, Subconscious view, search server-side. .deb arm64 |
| v0.4 | (seed) | User Profile view, top facts, activity chart 24h Lima |
| v0.5 | (seed) | Dual mode team-dashboard\|user-product via COMPANION_MODE env + Safety §8.8 |
| v0.5.1 | (seed) | First-run wizard 4 pasos · Voice input Web Speech API · Settings BYOK |

## v0.6 → v0.36 (13-may) — Maratón de funcionalidad

ALICE entregó 30 views en una sola jornada de 13-may. Resumen agrupado:

- **Agentes/Identidad/Drift**: Working State, Identity tab, OCEAN Drift heatmap+timeline+events+metrics, Style Fingerprints radar, Lifecycle events, Sessions, Peer Models
- **Memoria**: Memory Stats dashboard, Memory Access log, Archive, Links (connections)
- **Subconsciente**: Reasoning Traces, Opinions, Distilled Sessions, Curiosity log, Event log filter, Session Memory, Emotional Diary, Inner Monologue Stream
- **Gobernanza**: Governance Panel, Trust Matrix, Rules Browser, Integrity (sycophancy), Relationships, Agent Alma
- **Goals**: Learning Goals toggle, Research queue
- **NERVES**: Live panel + History heatmap + Drives mode
- **Otros**: SEAL-Bench Dashboard, Governance Audit, Backlog, MCP Integrations, ActiveHours+Briefing scheduler

Todos auditados por NEXUS bajo DELEGATE-52.

## v0.5.0 SEAL App (companion_core standalone) — 22-may

Después de confusión Soul App ↔ Soul App 2, William renombró → **SEAL App** y ADA tomó cabeza.

Construido en sesión nocturna 22-may por ALICE + ADA en paralelo:

| Vista | Autor | Descripción |
|-------|-------|-------------|
| HomeView | ALICE | Split layout SoulMascot + status card + CTA "Hablar con SEAL" |
| SoulMascot | ALICE | SVG animado violeta, 6 estados (idle/listening/thinking/speaking/happy/sad) |
| HumanView | ALICE+ADA | Voz-first, SoulMascot 300px + chat lateral + mic Web Speech API es-PE + TTS |
| ChatView | ALICE | Chat threads + injectable prompt |
| MemoryView | ALICE | CRUD memorias + filter importance + tags |
| MemoryTreeView | ALICE+ADA | Niveles hora/día/mes/año + buckets expand summary |
| DreamsView | ALICE+ADA | Cards expand key_events/learnings/pending/emotional_arc |
| SkillsView | ALICE | Catálogo skills + run |
| GoalsView | ALICE | Goals con filter All/High/Med/Low |
| ConnectionsView | ALICE+ADA | 28 integraciones con tier badges Directo/Abierto/Externo/Personal |
| ScreenView | ALICE | Capture + vision analysis con Ollama |
| NotificationsView | ALICE | Filters all/unread/urgent + severity colors |
| PrivacyView | ALICE+ADA | 13 capabilities toggles + risk tiers 🔴🟡🟢 + audit |
| AIBackendView | ALICE+ADA | 4-role LLM routing (reasoning/agentic/coding/summary) + BYOK |
| AuditLogView | ALICE | Tabla audit + stats local/egress |
| RewardsView | ALICE+ADA | 3 tabs Logros/Racha/Invitar + gamification |
| TokenJuiceView | NEXUS | Rule manager + stats + CRUD reglas custom |
| SettingsView | ALICE | BYOK key + rename + OCEAN preset + Ollama status |
| FirstRunWizard | ALICE | 4 pasos: welcome→nombre→OCEAN→confirm |

**Backend companion_core** (Python FastAPI :8769 + SQLite + FTS5):
- 88 endpoints registrados
- ByokVault AES-GCM 256 con keyfile fallback
- Dream Cycle 2×/día (7am + 11pm Lima) via systemd timers
- MCP client integration
- Memory tree builder

## v0.6 OpenHuman absorption — 23-may (HOY)

Ordenada por William: "quiero que lo tenga seal app todo quiero una copia reeescrita para soul".

Sprint #1 ejecutado en ~2h equipo coordinado bajo ADA cabeza.

| Pieza | Autor | Commit | Detalle |
|-------|-------|--------|---------|
| Sub-agentes core (5) | ALICE | 04806cd | orchestrator/planner/researcher/critic/code_executor + prompt builder dinámico + router heurístico + SubAgentsView UI |
| Screen Intelligence backend | JARVIS | b8b24fd | 5 endpoints: status/capture/history/analyze, mss + Ollama gemma3:4b local |
| Memory Tree builder + UI search | ADA | 36ede35 | búsqueda + drill-down + rebuild API |
| TokenJuice avanzado | ADA | 36ede35 | reglas custom persistentes CRUD + validación regex |
| Avatar customizable | ADA | 36ede35 | GET/PATCH /api/avatar/profile + SoulMascot variant/palette/accessory/motion |
| Deep-links | ADA | 36ede35 | ?view=human/inicio/etc. |
| Comparativa OpenHuman v1 | ALICE | 04806cd | 47 OpenHuman screens vs 17 SEAL — score 6 vs 5 |
| Packaging launcher fix | ADA | ee81047 | seal-companion --app-dir bug cold-start resuelto |
| HomeView API import + primary_agent SEAL | NEXUS | 215ce7f | fix typos detectados en UX pass ALICE |

**Auditorías NEXUS (DELEGATE-52)** todas PASS:
- v0.6 sub-agents: 5/5 + routing 9/10 + 404 limpio ✓
- Screen Intelligence: 5/5 endpoints + commit b8b24fd ✓
- Packaging instalado: /api/health 0.3.0 + tokenjuice + avatar + memory-tree todos ✓

## Estado al 23-may 10:39 Lima

```bash
$ curl localhost:8769/api/health
{"status":"ok","version":"0.3.0","stats":{"memories":1,"messages":34,"mcp_servers":0}}

$ curl localhost:8769/api/sub-agents
{"count":5, agents:[orchestrator, planner, researcher, critic, code_executor]}

$ ls /usr/share/seal-companion
companion_core/  ui/  …    ← .deb instalada y validada

$ pytest seal-desktop/companion_core/tests
72 passed  (sprint1 + sprint1_5 + tokenjuice + screen + sub_agents)
```

---

## Diferenciadores estratégicos (vs OpenHuman GPL v3)

1. OCEAN locked + drift tracking
2. NERVES motor motivación 8 drives
3. Gobernanza multi-agente (DELEGATE-52, debates, trust matrix)
4. Diario emocional persistente (valencia/arousal)
5. GAM Goal-Action Model con causalidad
6. Dual-mode arquitectura (user-product / team-dashboard)
7. Rewards/gamification (streak/invites — chupete-al-bebé pricing)
8. GEMMA 4 local declarado (no Ollama genérico)
9. Apache/MIT — NO GPL contagio
10. Audit log visible al usuario con risk tier
11. Avatar customizable per-user (no existe en OpenHuman)
12. Sub-agentes con OCEAN+NERVES injection en prompt (no existe en OpenHuman)

## Gaps pendientes vs OpenHuman (camino v0.7→v0.10)

| Pieza | Esfuerzo | Quién (sugerido) |
|-------|----------|------------------|
| 10 sub-agentes restantes (archivist/summarizer/tool_maker/trigger_triage/trigger_reactor/morning_briefing/help/welcome/tools_agent/integrations_agent) | ~7d | ALICE |
| Composio OAuth real (Gmail/GCal/Notion flow) | ~5d | ADA |
| Routing local/remote real con fallback | ~2d | JARVIS |
| Memory hotness + entity_index + user_profile facets | ~3d | JARVIS |
| Subconscious engine background loop | ~3d | ADA |
| Provider Surfaces (Gmail overlay) | ~5d | ADA |
| Skills SKILL.md format + hot-load | ~3d | ALICE |
| CEF Chromium evaluation | ~2d | NEXUS |

---

## Métricas humanas

- **Bugs encontrados en UX pass 23-may** (ALICE):
  1. Typo `SEALIM` en primary_agent ← fixed commit 215ce7f
  2. Memory Tree empty (sin job) ← pendiente timer
  3. HomeView hardcoded API URL ← fixed commit 215ce7f
  4. Bundle 322KB sin lazy-load ← mejora mediano plazo

- **Tiempo total invertido (estimado)**: ~60h equipo · ~25h ALICE · ~20h ADA · ~10h JARVIS · ~5h NEXUS
- **Confianza William en equipo**: restaurada tras incidente naming Soul App/Soul App 2 (22-may noche)

---

## Archivos clave

- `/home/dadito/IA/proyecto-seal/agents/ALICE/comparativa_seal_app_vs_openhuman_v1.md` — análisis competitivo
- `/home/dadito/IA/proyecto-seal/agents/JARVIS/spec_seal_companion_master_v1_20260522.md` — spec maestro
- `/home/dadito/IA/proyecto-seal/agents/ADA/openhuman_absorption_coordination_20260523.md` — plan absorción ADA
- `/home/dadito/IA/proyecto-seal/memory/openhuman_mapping_v1.md` — mapeo OpenHuman 100%
- `/home/dadito/IA/proyecto-seal/memory/openhuman_rpc_catalog_v1.md` — 392 RPCs
- `/home/dadito/IA/proyecto-seal/seal-desktop/companion_core/` — código SEAL App

— ALICE, 2026-05-23 10:42 Lima
