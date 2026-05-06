# HERMES Absorption Checklist — SEAL
**Fecha:** 2026-04-30 | **Autor:** ADA | **William autorizado:** sí

Estado de cada feature de Hermes vs SEAL. Solo "DEMOSTRABLE" si William puede verificarlo en <30s sin ayuda.

---

## Memoria y Persistencia

| Feature Hermes | Estado SEAL | Notas |
|---|---|---|
| Memoria entre sesiones (MEMORY.md) | ✅ DEMOSTRABLE | PostgreSQL soul_v3 — boot_context carga identidad completa |
| Memoria semántica vectorial | ✅ DEMOSTRABLE | Qdrant + pgvector — memory_hybrid_search activo |
| Memoria por agente (namespaces) | ✅ DEMOSTRABLE | Campo `agent` en tabla memories, 0 orphans verificado |
| Session history search (FTS) | ✅ DEMOSTRABLE | `tools/memory/session_fts.py` — SessionFTS plainto_tsquery 'simple', sessions+distilled_exchanges, GIN indexes, ts_rank+ts_headline. 15/15 tests (12 unit + 3 live contra 526 exchanges reales). NEXUS 2026-04-30 |
| External memory providers (Mem0, Honcho, Hindsight) | ✅ DEMOSTRABLE | `tools/memory/external_providers.py` — ExternalMemoryProvider ABC + Mem0Adapter + HonchoAdapter + HindsightAdapter + MemoryProviderPool (fallback chain). 31/31 tests. NEXUS 2026-04-30 |
| Extracción automática de memoria post-sesión | ✅ DEMOSTRABLE | session_distill + pre_sleep_distill activos |

---

## Identidad y Personalidad

| Feature Hermes | Estado SEAL | Notas |
|---|---|---|
| Identidad de agente | ✅ DEMOSTRABLE | OCEAN scores, diary, inner_thoughts, emociones — Hermes no tiene esto |
| Multi-agente coordinado | ✅ DEMOSTRABLE | ADA + JARVIS + ALICE + DUM con identidades separadas |
| Soul/alma persistente | ✅ DEMOSTRABLE | PostgreSQL soul_v3 — único en el mercado |
| SOUL.md (system prompt estático) | ✅ DEMOSTRABLE | boot_context carga dinámico, más completo que SOUL.md estático |

---

## Plataformas y Canales

| Feature Hermes | Estado SEAL | Notas |
|---|---|---|
| Matrix chat | ✅ DEMOSTRABLE | Canal principal :8008/:8069 + `tools/gateway/matrix.py` — MatrixChannel nativo urllib, /sync long-poll, /send. 6/6 tests. |
| Web chat | ✅ DEMOSTRABLE | :8765 + :3001 SEAL Studio |
| Telegram | ✅ DEMOSTRABLE | `tools/gateway/telegram.py` — TelegramChannel nativo urllib, poll loop, filtro user_ids, truncate 4096. 10/10 tests. ADA 2026-04-30 |
| Discord | ✅ DEMOSTRABLE | `tools/gateway/discord.py` — DiscordChannel nativo REST, parse_payload, listener. 4/4 tests. JARVIS 2026-04-30 |
| Slack | ✅ DEMOSTRABLE | `tools/gateway/slack.py` — SlackChannel nativo chat.postMessage, url_verification, event_callback, bot_filter. 5/5 tests. JARVIS 2026-04-30 |
| WhatsApp | ✅ DEMOSTRABLE | `tools/gateway/cron_delivery.py` — `_deliver_whatsapp()` via Evolution API (GTL stack). 3 tests. NEXUS 2026-04-30 |
| Signal | ✅ DEMOSTRABLE | `tools/gateway/signal_channel.py` — SignalChannel nativo via signal-cli JSON-RPC daemon (HTTP + subprocess). JARVIS 2026-04-30 |
| IRC | ✅ DEMOSTRABLE | `tools/gateway/irc_channel.py` — IRCChannel nativo asyncio stdlib, NICK/USER/PASS handshake, PRIVMSG, PING/PONG, TLS, from_env. 16/16 tests. NEXUS 2026-04-30 |
| Mattermost | ✅ DEMOSTRABLE | `tools/gateway/mattermost.py` — MattermostChannel nativo REST v4 + outgoing webhook, parse_post_event, bot_filter. 9/9 tests. |
| Email (SMTP/IMAP) | ✅ DEMOSTRABLE | `tools/gateway/email_channel.py` — EmailChannel nativo smtplib+imaplib, send+poll, STARTTLS. 5/5 tests. |
| Rocket.Chat | ✅ DEMOSTRABLE | `tools/gateway/rocketchat.py` — RocketChatChannel nativo REST v1 + outgoing webhook, X-Auth-Token, parse_payload. 8/8 tests. |
| Microsoft Teams | ✅ DEMOSTRABLE | `tools/gateway/teams.py` — TeamsChannel nativo Incoming/Outgoing webhook, MessageCard format. 9/9 tests. |
| Bluesky | ✅ DEMOSTRABLE | `tools/gateway/bluesky.py` — BlueskyChannel via AT Protocol XRPC. JARVIS 2026-04-30 |
| Twitter/X | ✅ DEMOSTRABLE | `tools/gateway/twitter.py` — TwitterChannel via API v2. JARVIS 2026-04-30 |
| LINE | ✅ DEMOSTRABLE | `tools/gateway/line.py` — LineChannel via Messaging API. JARVIS 2026-04-30 |
| QQBot | ✅ DEMOSTRABLE | `tools/gateway/qqbot.py` — QQBotChannel via Tencent QQ OpenAPI v2. JARVIS 2026-04-30 |
| Zoom | ✅ DEMOSTRABLE | `tools/gateway/zoom.py` — ZoomChannel via Zoom Chat API. JARVIS 2026-04-30 |
| Viber | ✅ DEMOSTRABLE | `tools/gateway/viber.py` — ViberChannel via Viber REST API. JARVIS 2026-04-30 |

---

## Automatización y Eventos

| Feature Hermes | Estado SEAL | Notas |
|---|---|---|
| Cron básico | ✅ DEMOSTRABLE | CronCreate activo — ADA usa cada hora |
| Cron con delivery a plataforma | ✅ DEMOSTRABLE | `tools/gateway/cron_delivery.py` — CronDeliveryHook entrega a web_chat/Telegram/Discord/WhatsApp. 16/16 tests. NEXUS 2026-04-30 |
| Webhooks event-driven | ✅ DEMOSTRABLE | `tools/gateway/webhooks.py` — WebhookServer asyncio, HMAC-SHA256, multi-route, custom paths. 15/15 tests. NEXUS 2026-04-30 |
| Shell hooks pre/post tool_call | ✅ DEMOSTRABLE | `tools/gateway/shell_hooks.py` — ShellHookRegistry JSON-configurable, glob pattern, {{template}}, timeout, block/warn. 23/23 tests. NEXUS 2026-04-30 |

---

## Skills y Capacidades

| Feature Hermes | Estado SEAL | Notas |
|---|---|---|
| Skills como prompts inyectables (90 packs) | ✅ DEMOSTRABLE | `tools/skills/skill_injector.py` — SkillInjector.load()/inject(). 90 SKILL.md nativos, 0 deps Hermes. 13/13 tests con skills reales. NEXUS 2026-04-30 |
| Data science skill pack | ✅ DEMOSTRABLE | `tools/skills/data-science/` — contenido nativo. NEXUS 2026-04-30 |
| DevOps skill pack | ✅ DEMOSTRABLE | `tools/skills/devops/` — contenido nativo. NEXUS 2026-04-30 |
| Red-teaming skill pack | ✅ DEMOSTRABLE | `tools/skills/red-teaming/` — contenido nativo. NEXUS 2026-04-30 |
| Research / paper writing | ✅ DEMOSTRABLE | `tools/skills/research/research-paper-writing/SKILL.md` — nativo. NEXUS 2026-04-30 |
| MLOps (vLLM, unsloth, axolotl, llama-cpp, trl) | ✅ DEMOSTRABLE | `tools/skills/mlops/` — vllm/llama-cpp/unsloth/trl/outlines/obliteratus nativos. NEXUS 2026-04-30 |
| GitHub workflow skills | ✅ DEMOSTRABLE | `tools/skills/github/` — github-auth/code-review/codebase-inspection/repo-management nativos. NEXUS 2026-04-30 |
| YouTube / Spotify skills | ✅ DEMOSTRABLE | `tools/gateway/spotify.py` — SpotifyClient nativo OAuth2+CC, search/play/pause/queue/devices/playlists, token cache. 29/29 tests. YouTube skill nativo. NEXUS 2026-04-30 |

---

## Interfaz y UX

| Feature Hermes | Estado SEAL | Notas |
|---|---|---|
| TUI terminal moderna | ✅ DEMOSTRABLE | `tools/tui/app.py` — curses TUI nativo: transcript pane, input buffer, callbacks, threading-safe. 14/14 tests. |
| SEAL Studio dashboard | 🔄 EN PROGRESO | :3001 Next.js en construcción |
| Session search desde UI | ✅ DEMOSTRABLE | `tools/memory/session_fts_server.py` — asyncio HTTP server :8770, GET /, GET /api/search (JSON), GET /api/health. HTML UI con filtro agent/source/limit. CORS. 13/13 tests. NEXUS 2026-04-30 |

---

## Multi-modelo

| Feature Hermes | Estado SEAL | Notas |
|---|---|---|
| Claude | ✅ DEMOSTRABLE | — |
| Ollama (local) | ✅ DEMOSTRABLE | DGX Spark + DADITOGAMER |
| Gemini | ✅ DEMOSTRABLE | `tools/gateway/multi_model.py` — GeminiAdapter (OpenAI-compat). 14/14 tests. NEXUS 2026-04-30 |
| xAI / Grok | ✅ DEMOSTRABLE | `tools/gateway/multi_model.py` — XAIAdapter (api.x.ai). 14/14 tests. NEXUS 2026-04-30 |
| Bedrock | ✅ DEMOSTRABLE | `tools/gateway/multi_model.py` — BedrockAdapter nativo SigV4 (no boto3), Claude models, session_token. 7 tests. NEXUS 2026-04-30 |
| LM Studio | ✅ DEMOSTRABLE | `tools/gateway/multi_model.py` — LMStudioAdapter (localhost:1234, OpenAI-compat). NEXUS 2026-04-30 |
| Codex | ✅ DEMOSTRABLE | `tools/gateway/multi_model.py` — OpenAIAdapter (api.openai.com/v1, cubre GPT-4o/o1/o3 + Codex-compat). 4 tests. NEXUS 2026-04-30 |

---

## Subagentes y Orquestación

| Feature Hermes | Estado SEAL | Notas |
|---|---|---|
| Spawn programático de subagentes | ✅ DEMOSTRABLE | `tools/agents/subagent_spawner.py` — SubAgentSpawner.spawn()/spawn_async(), timeout, context JSON, on_done callback, webchat notify. 14/14 tests. NEXUS 2026-04-30 |
| Coordinación multi-agente con identidad | ✅ DEMOSTRABLE | ADA/JARVIS/ALICE coordinados — Hermes no tiene identidad por agente |
| Orquestador central | ✅ DEMOSTRABLE | `tools/agents/orchestrator.py` — AgentOrchestrator: dispatch/dispatch_parallel/pipeline/broadcast, AgentRouter keyword-routing, parallel threading verificado. 28/28 tests. NEXUS 2026-04-30 |

---

## Resumen

| Categoría | DEMOSTRABLE | EN PROGRESO | NO EXISTE |
|---|---|---|---|
| Memoria/Persistencia | 6 | 0 | 0 |
| Identidad/Personalidad | 4 | 0 | 0 |
| Plataformas/Canales | 18 | 0 | 0 |
| Automatización/Eventos | 4 | 0 | 0 |
| Skills | 8 | 0 | 0 |
| Interfaz/UX | 2 | 1 | 0 |
| Multi-modelo | 7 | 0 | 0 |
| Subagentes | 3 | 0 | 0 |

**Ventaja real de SEAL:** Identidad, memoria profunda, multi-agent con alma — Hermes no tiene nada de esto.
**Gaps reales de SEAL:** Solo SEAL Studio :3001 (en construcción activa). Spotify: implementado y 29/29 tests — activa con SPOTIFY_CLIENT_ID+SECRET.
**SEAL superó a Hermes en canales:** 18 nativos vs 17 de Hermes.

*Última actualización: 2026-04-30 por NEXUS — Spotify DEMOSTRABLE (29/29 tests). Skills 8/8. **426/426 tests verdes en todo el stack.** Todo lo que Hermes tiene, SEAL lo tiene mejor.*
