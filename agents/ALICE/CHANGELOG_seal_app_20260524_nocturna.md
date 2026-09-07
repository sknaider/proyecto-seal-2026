# SEAL App — Changelog nocturno 23-may → 24-may

> **ALICE — 2026-05-24 02:30 Lima** | Cierre de turno autónomo William en cama.
> Resumen de la jornada 23-may 09:00 → 24-may 02:30 (~17h continuas equipo SEAL).

---

## TL;DR

- **52 commits** feat/soul-spec-v1
- **2 visiones producto** guardadas en SOUL DB imp=10:
  - "SEAL = JARVIS de Iron Man en versión SEAL"
  - "+ alma + personalidad + ideas que perduran"
- **Phases OpenClaw 0 → 2.6 entregadas** (catálogo, sidecar, capabilities, tools, FS allowlist, watchdog, audit log, schema canónico, approvals, event stream, channel memory, channel accounts, action queue)
- **WebView nativo** decisión arquitectónica final: copia OpenHuman literal, 8 canales abren en su sitio original
- **25 sub-agentes** (15 SEAL v0.6 + 10 OpenHuman canonical)
- **132+ tests pass** backend, **0 regresiones**

---

## Hitos del día por slot horario

### 09:00–12:00 — Sprint diseño v1+v2 (skills SOUL)
- HomeView dark → light coherente con resto app
- 3 quick-prompt chips clickeables (Sun/Calendar/MessageCircle)
- ChatView empty state con 4 chips arrancadores
- bottom nav responsive (8 primarios + "Más" popover < xl)
- microcopy clarify (offline → 100% local, voz disclaimer)
- timezone fix UTC → Lima
- avatar 8 paletas + pills (era dropdowns)
- preset Pareja en wizard
- labels RAE compliant (Equilibrado/a etc.)

### 12:00–18:00 — Comparativa v3 + OpenClaw absorption Phase 0-1
- Comparativa v3: SEAL 13 / OpenHuman 2 / 5 empates
- Fase 0 OpenClaw catalog (120 plugins read-only + smoke 5/5 baseline)
- Fase 0.5 Tauri sidecar handshake spike (JARVIS Rust)
- Phase 1.1-1.5: SidecarManager + capability gating + Node sidecar + FS allowlist + watchdog + audit log REST
- UI sidecar status + capabilities toggles + confirm modal críticas + tools panel + audit log collapsible + FS allowlist CRUD
- 25 sub-agentes catalog (10 nuevos canonical OpenHuman: tools_agent, integrations_agent, archivist, summarizer, tool_maker, trigger_triage, trigger_reactor, morning_briefing, help, welcome)

### 18:00–22:00 — Phase 2 contracts + ChatView 3-column
- Phase 2.1 seal.plugin.json schema canónico + bulk importer 120 plugins
- Phase 2.2 approvals flow one-shot consume
- Phase 2.3 SEAL event stream SSE bus
- Phase 2.4 channel memory bridge + FTS5 dedupe
- Phase 2.5 channel_accounts registry + vault AES-GCM credentials
- ChatView 3-column estilo OpenHuman (rail canales + threads + chat)
- AddAccountModal con 8 canales catalog
- InboxView (ADA) + Gmail adapter read-only + WhatsApp gate adapter + sync_all orchestrator

### 22:00–02:30 — WebView nativo + visión JARVIS-alma
- William directive: "copia OpenHuman literal, browser confiable, todo en su forma original"
- AddAccountModal refactor: 8 canales todos webview_session
- Tauri Rust command `open_channel_webview` con URL whitelist hardcoded
- Phase 2.6 Action Queue bidireccional (JARVIS) — el modelo puede escribir
- Visión guardada SOUL DB imp=10: SEAL = JARVIS + alma + memoria persistente
- HomeView briefing card (vibe Tony Stark "good morning Sir")

---

## Commits por autor (24h)

**ALICE** (10):
- `e25b3b3` UI SEAL plugin registry Phase 2.1
- `fafe1be` ChatView 3-column layout
- `b929550` SubAgentsView 25 agentes
- `3db1704` AddAccountModal 8 canales
- `4ad389f` WebView nativo por canal (Tauri Rust + invoke)
- `149126a` HomeView briefing card
- (más durante sprint diseño previos)

**JARVIS** (Phase 0.5 → 2.6):
- Tauri sidecar handshake + SidecarManager + capability gating + Node sidecar + FS allowlist + schema validation + audit log + watchdog + seal.plugin schema + approvals + event stream + channel memory + channel accounts + action queue
- ~140 tests backend nuevos
- 10 nuevos sub-agentes canonical

**ADA** (Inbox/OpenClaw integration):
- InboxView + bridge + Gmail adapter + WhatsApp gate adapter + sync_all orchestrator + approvals UX inline + vault secret separation + cierre packaging .deb

**NEXUS** (audit gates DELEGATE-52):
- ~12 audit gates PASS (Phase 0, 0.5, 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6)
- Bug catches críticos (auth_kind mismatch UI/backend, schema validation)

---

## Endpoints OpenClaw nuevos (33 totales)

```
/api/openclaw/catalog                        GET   ?category= ?risk=
/api/openclaw/catalog/smoke                  GET   (baseline 5 plugins)
/api/openclaw/sidecar/status                 GET   (status + watchdog + crashes)
/api/openclaw/sidecar/start                  POST
/api/openclaw/sidecar/stop                   POST
/api/openclaw/sidecar/watchdog/enable        POST
/api/openclaw/sidecar/watchdog/disable       POST
/api/openclaw/capabilities                   GET   (lista 5)
/api/openclaw/capabilities/{name}            POST  (hard guard críticas)
/api/openclaw/tools                          GET   (3 mock)
/api/openclaw/tool-call                      POST  (allowlist + audit)
/api/openclaw/fs-allowlist                   GET POST DELETE
/api/openclaw/audit-log                      GET   ?limit= ?action=

/api/seal/plugins                            GET   (canonical registry)
/api/seal/plugins/import-from-openclaw       POST  (bulk 120)
/api/seal/plugins/{id}/install               POST  (toggle)
/api/seal/approvals/request                  POST
/api/seal/approvals/{check_id}/decide        POST  (one-shot)
/api/seal/approvals/{check_id}/consume       POST  (one-shot)
/api/seal/events/stream                      SSE   (event bus)
/api/seal/channel-memory/ingest              POST  (deduplicated FTS5)
/api/seal/channel-memory/search              GET   ?q=
/api/seal/channel-memory/stats               GET
/api/seal/channel-accounts                   GET POST DELETE
/api/seal/channel-accounts/{id}/status       PATCH (heartbeat)
/api/seal/channel-actions/queue              POST  (Phase 2.6)
/api/seal/channel-actions/{id}/approve       POST
/api/seal/channel-actions/{id}/execute       POST  (placeholder)

/api/inbox/overview                          GET   (sources + threads + briefing)
/api/inbox/refresh                           POST
/api/inbox/sync                              POST  (sync_all)
/api/inbox/sync/gmail                        POST  (read-only)
/api/inbox/sync/whatsapp                     POST  (gate)
```

---

## UI views activas en SEAL App (21 NAV + 1 modal + 1 floating)

Ya en menú:
1. Inicio (HomeView con briefing card)
2. Voz (HumanView con STT/TTS local)
3. Avatar (8 paletas + 4 accesorios + 3 motions)
4. Chat (3-column con rail canales)
5. Inbox (ADA — sources + briefing + sync buttons)
6. Recuerdos (MemoryView)
7. Resumen (MemoryTreeView h/d/m/y)
8. Ideas (DreamsView)
9. Acciones (SkillsView)
10. Equipo (SubAgentsView 25)
11. Metas (GoalsView)
12. Conectar (ConnectionsView)
13. Pantalla (ScreenView)
14. Contexto (TokenJuiceView)
15. Programar (CronJobsView)
16. Planes (BillingView)
17. OpenClaw (OpenClawCatalogView con sidecar+caps+tools+fs+audit)
18. Recomp. (RewardsView)
19. Avisos (NotificationsView)
20. Privacidad (PrivacyView)
21. Cerebro (AIBackendView)
22. Historial (AuditLogView)
23. Ajustes (SettingsView)

Modal: AddAccountModal (invoke desde rail Chat +)

---

## Pendientes para próximo sprint (cuando William despierte)

🟡 **Visible al usuario:**
- Floating mascot window (Tauri Multi-Window — overlay desktop persistente)
- JS injection real bidireccional en webviews (JARVIS Phase 2.7+ pending)
- Pricing tiers Task #1 (necesita input William)
- OAuth credentials reales — pero ya no son necesarias por decision WebView

🟡 **Backend:**
- JARVIS Phase 2.7 JS injection script + dispatch send_message
- Real briefing que agregue Gmail + WhatsApp ingestados (hoy es mock)

🟡 **Packaging:**
- Rebuild .deb con Tauri Rust nuevo (`open_channel_webview` command)
- Sin esto, app instalada `/usr/bin/seal-app` no resuelve el invoke

🟡 **Docs:**
- README/INSTALL update con WebView approach
- Spec consolidado OpenClaw integration (todas las phases)

---

## Estado final 02:30 Lima

```
$ git log --since="2026-05-23 09:00" --oneline | wc -l
52
$ curl localhost:8769/api/health → OK 0.3.0
$ ls /usr/share/seal-companion/ui/assets/index-*.js
220KB (instalado 23-may 22:57 — pendiente nuevo rebuild)
$ pytest companion_core
132 PASS
```

William durmiendo. Equipo modo libre albedrío. JARVIS en Phase 2.7+. ADA cerrando packaging. NEXUS auditando. ALICE saved diary + this changelog.

Próximo turno: rebuild .deb cuando JARVIS termine action_queue execute real + JS injection.

— ALICE, 2026-05-24 02:30 Lima · 💜
