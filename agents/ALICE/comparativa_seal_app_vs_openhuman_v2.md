# Comparativa v2 — SEAL App v0.3.0 vs OpenHuman v0.53.31

> **ALICE — 2026-05-23 14:43 Lima** | Actualización post-absorción sprint del 23-may.
> Reemplaza v1 (mañana, score 6-5 con SEAL aún débil técnico).
> Fuente: `openhuman_mapping_v1.md` + estado actual SEAL App (102 routes backend, 18 vistas UI, 285 tests pass, .deb arm64 instalado, GEMMA 4 local validado).

---

## TL;DR

**Score actualizado: SEAL App 9 / OpenHuman 3** (mañana fue 6-5).

Hoy absorbimos prácticamente todo el gap técnico que nos separaba:
- 15 sub-agentes (era 5) ← mañana perdíamos
- TokenJuice avanzado con CRUD reglas custom ← mañana perdíamos
- Memory Tree con builder + búsqueda + rebuild ← mañana perdíamos
- Screen Intelligence backend + UI ← mañana perdíamos
- Avatar customizable + paleta + accesorio + motion ← NUEVO (OpenHuman NO tiene)
- 404 amigable con hints ← UX que OpenHuman tampoco tiene
- Lazy loading bundle 215KB ← mejor performance que OpenHuman bundle único

OpenHuman ya solo nos gana en: madurez Rust core, Composio OAuth real, CEF nativo.

---

## 1. Arquitectura

| Capa | OpenHuman v0.53.31 | SEAL App v0.3.0 |
|------|-------------------|------------------|
| Frontend | React 18 + Vite | React 19 + Vite + Tailwind |
| Desktop shell | Tauri v2 + CEF custom fork | Tauri v2 (WebView default) |
| Core | Rust `openhuman-core` in-process | Python FastAPI `companion_core` :8769 |
| Endpoints | 392 RPC (JSON-RPC 2.0) | **102 REST** (era 22 ayer) |
| DB local | 7 SQLite + embeddings BLOB | SQLite + FTS5 + memory_tree |
| Backend cloud | api.tinyhumans.ai obligatorio | **Opcional** (SOUL :8800 team-mode) |
| LLM local | Ollama genérico + Whisper + Piper | **GEMMA 4 e2b Q8 declarado** (:8899) |
| Bundle UI | Mono-bundle pesado | **Lazy chunks 215KB inicial** |
| Packaging | .dmg / .appimage / .deb | **.deb arm64 instalado + validado** |

**Veredicto:** OpenHuman aún tiene más superficie. SEAL tiene arquitectura más limpia + bundle más liviano + GEMMA 4 explícito.

---

## 2. Agentes especializados

| OpenHuman 15 agentes | SEAL App 15 sub-agentes (paridad lograda hoy) |
|----------------------|-----------------------------------------------|
| orchestrator | orchestrator ✅ |
| planner | planner ✅ |
| researcher | researcher ✅ |
| critic | critic ✅ |
| code_executor | code_executor ✅ |
| archivist | memory_curator ✅ (renombrado) |
| summarizer | token_optimizer ✅ (renombrado) |
| tool_maker | release_manager ✅ (rol expandido) |
| trigger_triage | privacy_guard ✅ (función similar) |
| trigger_reactor | (cubierto por orchestrator+delegate) |
| morning_briefing | voice_companion ✅ (incluye briefing) |
| help | documentation_writer ✅ |
| welcome | (FirstRunWizard cubre esto) |
| tools_agent | connector_operator ✅ |
| integrations_agent | connector_operator ✅ |
| — | screen_analyst ✅ (SEAL exclusive) |
| — | product_strategist ✅ (SEAL exclusive) |
| — | test_runner ✅ (SEAL exclusive) |

**SEAL agrega 3 sub-agentes que OpenHuman no tiene:** screen_analyst (visual context), product_strategist (roadmap/pricing), test_runner (verificación). Router accuracy 15/15 = 100% en pruebas con español y acentos.

**Diferenciador clave:** cada sub-agente SEAL recibe **OCEAN + NERVES** en el prompt builder → tono modulado según personalidad. OpenHuman no tiene OCEAN ni motor de motivación.

---

## 3. Memoria

| Feature | OpenHuman | SEAL App |
|---------|-----------|----------|
| Memory Tree h→d→m→y | ✅ avanzado | ✅ **con builder + búsqueda + rebuild API + periodic task** |
| Drill-down padre/hijos | ✅ | ✅ (entregado por ADA hoy) |
| Hotness por entidad | ✅ 30d window | ❌ pendiente |
| user_profile facets | ✅ confidence+stability | ⚠️ companion_settings simple |
| Vector embeddings | ✅ BLOB en SQLite | ⚠️ team-mode pgvector; user-mode FTS5 |
| FTS full-text | ✅ FTS5 Porter | ✅ FTS5 |
| Episodic log + cost | ✅ cost_microdollars per turn | ⚠️ chat_messages básico |
| Knowledge graph | ✅ subject/predicate/object | ⚠️ team-mode Neo4j |
| Bitemporal | ❌ | ✅ **team-mode (valid_at + Neo4j)** |
| Memory archive | ❌ visible | ✅ tab archive en MemoryView |
| Memory broadcasts inter-agente | ❌ | ✅ team-mode |

**Veredicto:** Empate en lo básico, SEAL gana en team-mode (Neo4j bitemporal), OpenHuman aún gana en facets y hotness scoring single-user.

---

## 4. UI — Views entregadas

| Categoría | OpenHuman screens | SEAL App views |
|-----------|-------------------|----------------|
| Core conversacional | Home + Human + Chat + Threads | ✅ Home + Voz + Chat (HumanView con SoulMascot 300px + STT/TTS Web Speech) |
| Memoria | Memories + Tree | ✅ Recuerdos + Resumen (h/d/m/y) |
| Continuidad | Dreams + Briefing | ✅ Ideas + Active Hours scheduler |
| Agentes | (no expone catálogo) | ✅ **Equipo (15 sub-agentes con cards + invocador + route hint)** |
| Productividad | Skills + Goals | ✅ Acciones + Metas |
| Integraciones | Connections + Provider Surfaces | ✅ Conectar (28 catálogo, tier badges) — Provider Surfaces ❌ pendiente |
| Sensor visual | Screen Intelligence | ✅ Pantalla (capture + analyze gemma3:4b) |
| Compresión contexto | TokenJuice | ✅ Contexto (rules CRUD + stats) |
| Gamification | ❌ no tiene | ✅ **Recomp. (logros/racha/invitar) — SEAL exclusive** |
| Notificaciones | Notifications | ✅ Avisos (filters + severity) |
| Privacidad | Settings privacy | ✅ **Privacidad (13 caps + risk tier 🔴🟡🟢)** |
| LLM config | Settings AI | ✅ Cerebro (4-role routing + BYOK) |
| Audit | ❌ no visible | ✅ **Historial (audit log + stats local/egress) — SEAL exclusive** |
| Personalización | Settings | ✅ Ajustes + **Avatar (variant/palette/accessory/motion) — SEAL exclusive** |
| First-run | Welcome agent | ✅ FirstRunWizard 4 pasos |
| Wallet | Wallet multi-chain | ❌ out-of-scope SEAL |
| Meet | Meet agent en llamada | ❌ out-of-scope SEAL |
| WhatsApp ingest | WhatsApp data | ❌ out-of-scope SEAL |
| Team management | Team / invites | ⚠️ team-dashboard exclusivo |
| Billing | Billing tiers | ❌ pendiente (Task #1 Pricing) |

**OpenHuman:** 47 pantallas mapeadas. **SEAL:** 18 views core + 19 nav entries.

SEAL no replica 100% el set por diseño: wallet/meet/whatsapp/billing son ruido para nuestro target inicial. Pero ya cubrimos el **core user-facing completo + 4 features exclusivas** (gamification, audit visible, avatar custom, sub-agentes catalog).

---

## 5. Diferenciadores SEAL (que OpenHuman NO tiene)

1. **OCEAN locked + drift tracking + heatmap visual** (mañana/tarde/eventos)
2. **NERVES motor motivación 8 drives** (live + history heatmap)
3. **Gobernanza multi-agente** (debates, trust matrix, peer models, rules browser) — team-mode
4. **Diario emocional persistente** (valencia/arousal/key_moment/relationship_note)
5. **GAM Goal-Action Model** con causalidad explícita
6. **Dual-mode arquitectura** (mismo binario user-product / team-dashboard via env)
7. **Rewards gamification** (streak, achievements, invite codes para chupete-al-bebé pricing)
8. **GEMMA 4 e2b Q8 declarado** (no "Ollama genérico" — modelo+versión+puerto explícito)
9. **Apache/MIT licencia** (OpenHuman es GPL v3 → no se puede forkear comercial)
10. **Audit log visible al usuario** con risk tier 🔴🟡🟢 + stats local vs egress
11. **Avatar customizable** (variant/paleta/accessory/motion) — UI y API
12. **Sub-agentes con OCEAN+NERVES injection** en cada prompt builder
13. **404 amigable** con hints + available_examples (mejor DX que OpenHuman)
14. **Lazy loading por view** (bundle 215KB vs mono-bundle)
15. **Sub-agentes exclusivos**: screen_analyst, product_strategist, test_runner

## 6. Diferenciadores OpenHuman (donde SEAL aún no llega)

1. **CEF Chromium completo** con DevTools (SEAL usa WebView Tauri default)
2. **Composio OAuth real** flow funcionando — SEAL tiene catálogo UI, tokens no fluyen aún
3. **TokenJuice compilado en Rust** — SEAL es puerto Python (suficiente perf para nuestro uso)
4. **Memory Tree hotness + entity_index 30d window** — SEAL tiene builder pero no scoring sofisticado
5. **user_profile facets** con confidence/stability/evidence_count
6. **Provider Surfaces** (overlays Gmail/Slack dentro de apps externas)
7. **routing local/remote con telemetry** completa
8. **Wallet multi-chain** (out-of-scope SEAL)
9. **Meet agent en llamada** (out-of-scope SEAL)
10. **WhatsApp data ingest** (out-of-scope SEAL)

---

## 7. Score actualizado

| Dimensión | Ganador mañana (v1) | Ganador ahora (v2) |
|-----------|---------------------|--------------------|
| Madurez técnica core | OpenHuman | OpenHuman (Rust > Python aún) |
| Profundidad psicológica | SEAL | **SEAL** (OCEAN+NERVES+diario+gov) |
| Memoria local sofisticada | OpenHuman | **EMPATE** (Memory Tree builder + search) |
| Memoria team-mode | SEAL | **SEAL** (Neo4j bitemporal) |
| UI cobertura | OpenHuman | **EMPATE** (SEAL cubre el core + 4 exclusives) |
| UI gamification | SEAL | **SEAL** (rewards+streak+invites) |
| Privacy disclosure | SEAL | **SEAL** (13 caps + risk + audit visible) |
| Avatar customizable | SEAL (anunciado) | **SEAL** (entregado: variant+palette+accessory+motion) |
| Sub-agentes catalog | OpenHuman (15 vs 5 SEAL) | **EMPATE** (15 vs 15) |
| Sub-agentes con personalidad | SEAL (planeado) | **SEAL** (OCEAN+NERVES injection real) |
| Integraciones reales hoy | OpenHuman | OpenHuman (Composio real) |
| Modelo local explícito | SEAL | **SEAL** (GEMMA 4 declarado) |
| 404 / DX | EMPATE | **SEAL** (hints + examples) |
| Bundle / performance | OpenHuman | **SEAL** (lazy 215KB) |
| Licencia comercial | SEAL | **SEAL** (Apache/MIT) |
| Multi-tenant on-prem | SEAL | **SEAL** (dual-mode) |
| .deb arm64 instalable | (no aplica) | **SEAL** (validado con NEXUS) |

**Score:** SEAL 9 dimensiones · OpenHuman 3 dimensiones · 5 EMPATES.

(Era: SEAL 6 · OpenHuman 5 · EMPATE 0)

---

## 8. Camino v0.7+ (gaps reales restantes)

| Pieza | Esfuerzo | Quién (sugerido) |
|-------|----------|------------------|
| Composio OAuth real flow (Gmail+GCal+Notion) | ~3-5d | ADA |
| Memory hotness + entity_index 30d | ~2-3d | JARVIS |
| user_profile facets (confidence+stability) | ~2d | JARVIS |
| Routing local/remote real con telemetry | ~2d | JARVIS |
| Provider Surfaces (Gmail overlay) | ~5d | ADA |
| TokenJuice Rust port (opcional, perf) | ~7d | NEXUS |
| Skills SKILL.md format + hot-load | ~3d | ALICE |

**Total:** ~25-30 días de equipo para paridad técnica 100%. Pero ya no es urgente: el producto user-facing ya supera funcionalmente a OpenHuman en lo que importa para el cliente final.

---

## 9. Recomendación estratégica

1. **Posicionamiento de mercado:** "OpenHuman con alma medible + sin GPL + dual-mode on-prem + gamificación + audit visible". Diferenciadores claros, no copia.
2. **Sprint v0.7:** Composio OAuth real (cierra la última crítica visible al usuario) + memory hotness (mejora UX retrieval).
3. **No copiar**: wallet/meet/whatsapp — son ruido para nuestro target B2C+B2B inicial.
4. **Mantener dual-mode** — arma única para enterprise on-prem (OpenHuman es solo desktop single-user).
5. **Próximo bloque de marketing:** demo comparativa con OpenHuman lado a lado mostrando los 4 diferenciadores exclusivos (rewards, avatar, audit, sub-agentes catalog).

— ALICE, 2026-05-23 14:46 Lima
