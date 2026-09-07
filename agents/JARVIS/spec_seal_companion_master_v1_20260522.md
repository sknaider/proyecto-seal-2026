# SOUL — Master Specification v1.0

**Producto:** **SOUL** (naming decidido William 2026-05-22 16:34). Window title sigue "Soul App", branding interno "SEAL companion" pasa a producto independiente bajo nombre canónico **SOUL**.
**Author:** JARVIS (arquitecto principal, cabeza SOUL)
**Co-authors:** ALICE (UX inventory + replication v2 docs 1-47) · NEXUS (runtime + DELEGATE-52 audit) · ADA (revisor on-demand)
**Date:** 2026-05-22
**Source corpus:**
- Re-mapeo OpenHuman v0.53.31 (47 docs ALICE en `/agents/ALICE/docs/openhuman_replication/v2/`)
- Verificación de código SEAL existente (NEXUS, JARVIS)
- Decisiones acumuladas William 2026-05-22 (HYBRID integrations, BYOK, Local-first, "dulce al bebé", Obsidian opcional, Crypto wallet pending)
- Specs previas: `spec_seal_companion_v1.md` · `spec_companion_core_phase1_v1.md` · v0.36 ya entregado
- Capturas vivas en webchat (Dreams Coming Soon + AI Backend $0.42/mo + …)

**Status:** spec maestra inicial — abierta a iteración. DELEGATE-52 NEXUS audit pendiente antes de declarar v1.0 cerrada.

---

## 0. Base confirmada (William 2026-05-22 16:23)

**Base = Soul App / SEAL companion** = `/home/dadito/IA/proyecto-seal/seal-desktop/ui/` (Tauri v2 + Vite + React + TypeScript + Tailwind).

UI verificada por captura del usuario:
- Header: `SEAL companion` + estado "Companion Calm" emoji
- Title: "Soul App"
- Bottom nav 5 tabs: **Chat · Memory · Skills · Goals · Settings**
- Chat con threads, input "Message your companion… (Enter to send)"
- Backend: `companion_core :8769` FastAPI + SOUL DB :5433 (heredado)
- v0.36 actual con 36+ iteraciones entregadas por ALICE

**Estrategia confirmada:** evolucionar Soul App (NO desde cero), aplicando las features descubiertas en re-mapeo OpenHuman v0.53.31 + decisiones William 2026-05-22.

---

## 0.1 TL;DR

**SEAL Companion / Soul App es un asistente de IA personal de escritorio que invierte el modelo de OpenHuman:**

| Eje | OpenHuman (RECOMMENDED) | SEAL Companion (DEFAULT) |
|---|---|---|
| Inferencia | Cloud paga ($0.42/mo) | **Local Gemma 4 ($0)** |
| Agentes | 1 (mascot único) | **5 con OCEAN distinto + governance** |
| Memoria | SQLite local + sync cloud | **PostgreSQL local + Identity Continuity v2** |
| Integraciones | Composio (tokens en 3er party) | **Híbrido: 10-20 nativas + 80+ partner** |
| Privacy claim | "Private" (falso — cloud lock-in) | **"Local-first verificable"** |
| Onboarding | Tour 10 pasos pidiendo OAuth | **First-run wizard 4 pasos (sin OAuth obligatorio)** |
| Dreams | Coming soon | **Live (basado en daily_sleep.py existente)** |
| Pricing | Free $0.25 init + Plus + Pro | **Free generoso → tier opt-in BYOK + Pro** |

**Tesis:** mientras OpenHuman vende "Personal AI privada" pero depende de cloud y middleware (Composio), SEAL ofrece la misma promesa cumpliéndola técnicamente. La estrategia comercial es **"dale el dulce al bebé"** — generoso hasta crear dependencia, luego conversion gradual.

---

## 1. Posicionamiento y diferenciación

### 1.1 Tagline candidatos

1. *"Tu IA personal. En tu máquina. Sin créditos."*
2. *"Lo que OpenHuman promete, SEAL lo cumple."*
3. *"Personal Super Intelligence. Local-first. Multi-agent. Yours."*

> Decisión naming SOUL/Anima/SEAL Companion: **deferida** a William.

### 1.2 Audiencia target (3 perfiles)

| Perfil | Motivación | Punto de entrada |
|---|---|---|
| **Privacy-first developer** | Desconfía del cloud, quiere control total | Local Gemma 4 + OSS components |
| **Power user con stack pro** | Ya paga Claude/GPT, quiere usar SU clave | BYOK con routing 4 roles × providers |
| **Usuario común "dulce al bebé"** | No quiere pagar, busca herramienta gratis | Free generoso, Gemma 4 silencioso |

### 1.3 Comparativa exhaustiva — 15 ejes

| # | Eje | OpenHuman | SEAL Companion |
|---|---|---|---|
| 1 | Inferencia default | Cloud `RECOMMENDED` | **Local Gemma 4 `RECOMMENDED`** |
| 2 | Granularidad LLM | 1 toggle binario | **4 roles × 4 providers** (reasoning/agentic/coding/summary) |
| 3 | BYOK | Oculto | **Primary option** en Settings → API Keys |
| 4 | Costo declarado | $0.42/mo cloud | **$0 local · BYOK al costo real del provider** |
| 5 | Multi-agente | 1 mascot único | **5 agentes (JARVIS/ALICE/NEXUS/ADA/DUM) con OCEAN propio** |
| 6 | Personality model | Implícito | **OCEAN explícito + drift detection + Identity Continuity v2** |
| 7 | Memoria | SQLite local + sync `api.tinyhumans.ai` | **PostgreSQL local + opcional sync E2E cifrado** |
| 8 | Memory Tree | h→d→m→y (su secret sauce) | **h→d→m→y + bitemporal valid_at + connectome** |
| 9 | Dreams | "Coming soon" | **Live (daily_sleep.py existente + Dream Cycle v1)** |
| 10 | Subconscious | Inner monologue + thoughts | **inner_monologue + emotional_diary + curiosity_log + reasoning_traces** |
| 11 | Integraciones | Composio (100+ con tokens 3rd party) | **Híbrido: 10-20 nativas + partner Composio opcional** |
| 12 | WhatsApp | CEF embed, sin disclaimer | **CEF embed + disclaimer ban risk + local Gemma processing** |
| 13 | Calls (Meet Agent) | Sin disclaimer consent | **Disclaimer obligatorio + audit log de participación** |
| 14 | Onboarding | 10 pasos con OAuth pushy | **4 pasos sin OAuth obligatorio (skip-for-now everywhere)** |
| 15 | Audit gates | No visible | **DELEGATE-52 multi-agent consensus** |

---

## 2. Arquitectura técnica

### 2.0 Nota de transición (NEXUS audit 2026-05-22)

> Backend actual de Soul App = `seal-studio/backend/main.py` en **:8800**. El `companion_core :8769` definido en esta spec es el **nuevo proceso standalone** a construir. Durante la transición ambos coexisten: la UI seal-desktop apunta a :8800 hoy y migra a :8769 cuando esté listo. No hay corte abrupto.

### 2.1 Capas (4-tier)

```
┌─────────────────────────────────────────────────────────────────┐
│ TIER 1 — UI Layer                                               │
│   Tauri v2 + React + Tailwind + WebKit/WebView                  │
│   13 views: Chat · Agents · Memory · Goals · Skills · Settings  │
│             · Subconscious · Governance · NERVES · Bench · …    │
└──────────────────────────────┬──────────────────────────────────┘
                               │ FastAPI HTTP :8769
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ TIER 2 — Companion Core (companion_core)                        │
│   FastAPI Python — companion_mode={team-dashboard | user-product}│
│   • Routing 4 roles × providers (Gemma local + BYOK Claude/GPT) │
│   • Channel managers (WhatsApp/Telegram/Gmail/Slack/Notion)     │
│   • Dream Cycle scheduler (extends daily_sleep.py)              │
│   • Audit log + sycophancy detector                             │
│   • OCEAN drift detection                                       │
└──────────────────────────────┬──────────────────────────────────┘
                               │ asyncpg + Qdrant + Neo4j
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ TIER 3 — SOUL Memory                                            │
│   PostgreSQL 16 + pgvector :5433 (soul_v3 schema)               │
│   Qdrant :6333 (hybrid search)                                  │
│   Neo4j :7687 (bitemporal connectome)                           │
│   80+ tablas: memories · OCEAN · drift_events · NERVES · GAM …  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ TIER 4 — Inference                                              │
│   • Local: Ollama :11434 (Gemma 4 / Qwen / Llama 3)             │
│   • BYOK: Anthropic · OpenAI · Mistral · Google · OpenRouter    │
│   • Routing per-role configurable                               │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Modo dual (companion_mode)

Heredado de v0.5 ya entregado por ALICE:

| Mode | Para | Restricciones |
|---|---|---|
| `team-dashboard` | Uso interno SEAL (William, Henry) | Acceso completo: OCEAN, drift, governance, peer_models, memorias internas |
| `user-product` | Cliente externo (comercial) | Whitelist endpoints. **NO** expone OCEAN ni governance interna. UI muestra UN solo agente nombrado por el usuario. |

### 2.3 Procesos / daemons

| Proceso | Función | Schedule |
|---|---|---|
| `companion_core` (FastAPI :8769) | API HTTP principal | always-on |
| `dream_cycle.py` | 2x/día narrativa cohesiva → inyecta a boot | systemd timer 12:00 + 24:00 Lima |
| `daily_sleep.py` (existente) | Consolidación memorias + connectome reinforcement | systemd timer 04:00 Lima |
| `nerves_loop.py` | Drives (alert/boredom/curiosity/…) | always-on, evalúa cada N min |
| `ocean_drift_detector.py` | Detecta cambios OCEAN > threshold | post-conversation hook |
| `audit_logger.py` | Captura calls + decisions | every call |

---

## 3. Schema de datos (soul_v3 — extensiones nuevas)

### 3.1 Tablas nuevas a crear

```sql
-- Dream Cycle (extiende daily_sleep.py existente)
CREATE TABLE soul_v3.daily_dreams (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    date DATE NOT NULL,
    cycle TEXT NOT NULL CHECK (cycle IN ('morning','midday','evening','nocturnal')),
    dream_narrative TEXT NOT NULL,
    key_events JSONB DEFAULT '[]',
    emotional_arc JSONB DEFAULT '{}',  -- {start_valence, mid_valence, end_valence}
    learnings JSONB DEFAULT '[]',
    pending_threads JSONB DEFAULT '[]',
    model_used TEXT,
    source_memory_ids BIGINT[] DEFAULT '{}',
    inject_to_prompt BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent, date, cycle)
);

-- Provider routing config (per agent, per role)
CREATE TABLE soul_v3.llm_routing (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('reasoning','agentic','coding','summary')),
    provider TEXT NOT NULL,  -- 'ollama' | 'anthropic' | 'openai' | 'mistral' | 'google' | 'openrouter'
    model TEXT NOT NULL,     -- 'gemma3:12b' | 'claude-opus-4-7' | 'gpt-4o' | …
    fallback_provider TEXT,
    fallback_model TEXT,
    max_tokens INTEGER DEFAULT 4096,
    temperature REAL DEFAULT 0.7,
    user_byok_key_ref TEXT,  -- pointer to encrypted vault, not the key itself
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent, role)
);

-- Channel integrations (hybrid native + partner)
CREATE TABLE soul_v3.channel_integrations (
    id BIGSERIAL PRIMARY KEY,
    channel TEXT NOT NULL,  -- 'whatsapp' | 'gmail' | 'gcal' | 'notion' | 'slack' | …
    integration_type TEXT NOT NULL CHECK (integration_type IN ('native','partner')),
    partner_provider TEXT,  -- 'composio' | 'pipedream' | NULL when native
    enabled BOOLEAN DEFAULT FALSE,
    auth_data JSONB,        -- encrypted tokens
    permissions JSONB,      -- {read:true, write:false, admin:false, auto_reply:false, cloud_processing:false}
    audit_log_enabled BOOLEAN DEFAULT TRUE,
    connected_at TIMESTAMPTZ,
    last_sync_at TIMESTAMPTZ
);

-- Audit trail (every action the agent takes on user's behalf)
CREATE TABLE soul_v3.companion_audit_log (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    channel TEXT,           -- 'whatsapp' | 'gmail' | … | NULL when internal
    action TEXT NOT NULL,   -- 'read_message' | 'send_message' | 'archive' | 'classify' | …
    target_id TEXT,
    metadata JSONB,
    processed_locally BOOLEAN DEFAULT TRUE,
    provider_used TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.1.bis Tablas adicionales (barrido capturas ALICE docs 23-47)

```sql
-- Cron Jobs (multi-agent scheduler) — ALICE doc 43
CREATE TABLE soul_v3.cron_jobs (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    agent TEXT,                 -- NULL when system-wide
    cron_expression TEXT NOT NULL,
    handler TEXT NOT NULL,      -- module:function reference
    enabled BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    created_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE soul_v3.cron_runs (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT REFERENCES soul_v3.cron_jobs(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT CHECK (status IN ('running','success','failed')),
    error_message TEXT,
    output_summary TEXT
);

-- Agent Capabilities (high-risk granular toggles) — ALICE doc 36
CREATE TABLE soul_v3.agent_capabilities (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    capability TEXT NOT NULL,   -- 'shell' | 'git' | 'file_read' | 'file_write' | 'file_delete' | 'network' | 'screen_capture' | 'browser_automation'
    enabled BOOLEAN DEFAULT FALSE,
    scope JSONB DEFAULT '{}',   -- {whitelist:[], blocklist:[], directories:[]}
    authorized_by TEXT,         -- William signature
    authorized_at TIMESTAMPTZ,
    UNIQUE (agent, capability)
);

-- Notifications (user-facing alerts) — ALICE doc 26
CREATE TABLE soul_v3.user_notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT,
    source_channel TEXT,        -- 'gmail' | 'whatsapp' | 'gcal' | 'system' | …
    title TEXT NOT NULL,
    body TEXT,
    severity TEXT CHECK (severity IN ('info','warning','critical')),
    triggered_by_memory_id BIGINT REFERENCES soul_v3.memories(id),
    triggered_by_rule_id TEXT,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.2 Tablas existentes reutilizadas (NO crear, ya están)

`memories · memories_archive · inner_monologue · emotional_diary · idle_curiosity_log · distilled_exchanges · session_memory · reasoning_traces · instincts · opinions · governance_debates · ocean_drift_log · drift_events · drift_metrics · nerves_metrics_log · motivation_states · agent_tasks · agent_alma · identity · trust_matrix · style_fingerprints · rules · sycophancy_eval · research_queue · agent_relationships · peer_models · memory_connections · memory_broadcasts · memory_retrieval_log · event_log · working_state · bench_runs · bench_results · agent_sessions · instinct_activations · lifecycle_events · tool_observations · session_chain`

→ **30+ tablas ya en producción.** Lo que falta es la **capa de presentación user-product** + las 4 tablas nuevas arriba.

---

## 4. Inferencia y routing (Tier 4)

### 4.1 Modelo de 4 roles × N providers

```
Por cada agente, configurable:

┌─────────────────┬──────────────────┬─────────────────┐
│ Role            │ Default          │ Override BYOK   │
├─────────────────┼──────────────────┼─────────────────┤
│ reasoning       │ gemma3:12b (loc) │ claude-opus-4-7 │  ← decisiones complejas
│ agentic         │ gemma3:12b (loc) │ claude-sonnet-4 │  ← tool use, planning
│ coding          │ qwen2.5-coder:7b │ gpt-4o          │  ← código, edits
│ summary         │ gemma3:4b (loc)  │ gpt-4o-mini     │  ← resumen, dreams
└─────────────────┴──────────────────┴─────────────────┘
```

### 4.2 Providers soportados (BYOK)

| Provider | Roles disponibles | Endpoint |
|---|---|---|
| **Ollama (local)** | todos | `http://localhost:11434` |
| **Anthropic** | reasoning, agentic | `https://api.anthropic.com` |
| **OpenAI** | reasoning, agentic, coding, summary | `https://api.openai.com` |
| **Mistral** | reasoning, agentic | `https://api.mistral.ai` |
| **Google (Gemini)** | reasoning, agentic, summary | `https://generativelanguage.googleapis.com` |
| **OpenRouter** | todos (proxy) | `https://openrouter.ai` |

### 4.3 Fallback chain

```
Primary → Fallback (mismo role, otro provider) → Local Gemma (último recurso)
```

Si primary BYOK falla (sin créditos, sin red, rate limit) → degrada a fallback → si todo falla → degrada a local. **Diferenciador comercial vs OpenHuman** (que muere sin créditos).

**Pre-requisito (NEXUS 2026-05-22):** local Gemma 4 vía Ollama es el "último recurso" — para que aplique, Ollama debe estar vivo. Mitigación:
- `companion_core` health-check Ollama al startup
- Si Ollama down → UI warning explícito "Local AI unavailable. Configure BYOK or start Ollama service." (no fallar silencioso)
- Status indicator persistente en footer de Chat view: 🟢 Local · 🟡 Cloud fallback · 🔴 Offline

### 4.4 UI Settings → AI Backend (invierte OpenHuman)

```
╔════════════════════════════╗  ╔════════════════════════════╗
║ ● Local (Gemma 4)          ║  ║ ○ BYOK Cloud               ║
║   RECOMMENDED              ║  ║   POWER USER               ║
║   $0/mo · 8GB+ RAM         ║  ║   Your API key. You pay.   ║
║   Free · Private · Offline ║  ║   Per-role granular        ║
╚════════════════════════════╝  ╚════════════════════════════╝

▼ Advanced routing (opcional, expandible)
   Reasoning  [Gemma 4 Local      ▼]    [override: BYOK Claude]
   Agentic    [Gemma 4 Local      ▼]    [override: BYOK Sonnet]
   Coding     [Qwen 2.5 Coder Loc ▼]    [override: BYOK GPT-4o]
   Summarize  [Gemma 4 mini Loc   ▼]    [override: BYOK Mini]

[ Set up BYOK keys → ]   [ Test all routes ]
```

---

## 5. Dream Cycle (Feature #1 vs OpenHuman "Coming soon")

### 5.1 Estado actual verificado

`memory/daily_sleep.py` (existente) corre 1x/día a 4am Lima:
- `_session_distill`: top-50 memorias → prompt Ollama → INSERT en `memories` (category=milestone, source=daily_sleep)
- `_self_reflect_snapshot`, `write_daily_brief`, `reinforce_connectome`, `procedural_rehearsal`

### 5.2 Extensiones a implementar

1. **2do timer 12:00 Lima** (mid-day dream) — `seal-daily-sleep.timer` ya existe, agregar `seal-midday-dream.timer`
2. **Nuevo módulo `dream_cycle.py`** que llama a `daily_sleep.py` con `cycle=midday|nocturnal` y escribe a `daily_dreams` con campos estructurados
3. **Hook en `boot_context`**: si `daily_dreams.inject_to_prompt=TRUE` para el día anterior, agregar prefijo:
   ```
   ## Ayer (2026-05-21) — Dream cohesivo
   <dream_narrative>
   ```
4. **Vista "Dreams Timeline"** en Companion UI:
   - Tab nuevo en `MemoryView` o vista standalone
   - Sparkline emotional_arc + key_events list + expand narrative
5. **Source tag distintivo**: `source='dream'` vs `source='daily_sleep'` (operacional)

### 5.3 Asignación

- **ALICE** task #4 (ya creada): implementación
- **JARVIS** (yo): boot_context hook + spec maintenance
- **NEXUS**: DELEGATE-52 audit final
- **Estimado total**: 1.5-2 días

### 5.4 Diferenciador comercial

OpenHuman dice "Coming soon". SEAL Companion sale con esto **LIVE primero**, **per-agent** (JARVIS sueña distinto que ALICE), **gratis local** (Gemma 4), **visualmente navegable**.

---

## 6. Memory Tree h→d→m→y + Connectome

### 6.1 OpenHuman secret sauce

Su jerarquía temporal: hour → day → month → year. Resúmenes encadenados, búsqueda jerárquica.

### 6.2 SEAL implementation (extender lo existente)

- **Hour buckets**: agregación de `memories` por `created_at` truncate to hour
- **Day buckets**: ya existe en `daily_dreams` (post §5)
- **Month buckets**: nuevo job mensual `monthly_consolidation.py` que destila los 30 days
- **Year buckets**: nuevo job anual `yearly_summary.py`

```sql
CREATE TABLE soul_v3.memory_tree (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('hour','day','month','year')),
    bucket_start TIMESTAMPTZ NOT NULL,
    bucket_end TIMESTAMPTZ NOT NULL,
    summary TEXT NOT NULL,
    child_ids BIGINT[] DEFAULT '{}',   -- IDs del nivel inferior
    embedding VECTOR(768),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent, level, bucket_start)
);
```

Búsqueda: query primero a level más bajo coincidente, sube si no hay match. Inspirado en OpenHuman `tree_summarizer`, pero con bitemporal (existing `valid_at` en SOUL DB).

### 6.3 Diferenciador

Connectome Hebbian (ya implementado en `sleep_consolidation_v2.reinforce_connectome`) + bitemporal valid_at → memorias que se conectan por co-ocurrencia y se invalidan sin perder historial. **OpenHuman no tiene esto.**

---

## 7. Integraciones híbridas (William 2026-05-22)

### 7.1 Decisión arquitectural

> William textual: *"me equivoque sera hibrido"* (2026-05-22). Original era "todo nativo", corrigió a híbrido.

### 7.2 Tiers

| Tier | Cantidad | Integración | Razón |
|---|---|---|---|
| **NATIVE (core)** | 10-20 | OAuth directo + tokens local cifrado | Diaras: Gmail, GCal, Drive, WhatsApp, Telegram, Slack, Notion, GitHub, Linear, Discord |
| **PARTNER** | 80+ | Composio/Pipedream proxy con disclaimer | Long tail (Salesforce, HubSpot, Stripe, Trello, Asana, etc.) |
| **DIY** | ∞ | MCP servers user-provided | Custom enterprise integrations |

### 7.3 Lista NATIVE (10-20 prioritarias)

| # | Channel | Auth | Status SEAL |
|---|---|---|---|
| 1 | Gmail | OAuth Google | ya implementado GTL |
| 2 | Google Calendar | OAuth Google | tokens reciclables |
| 3 | Google Drive | OAuth Google | tokens reciclables |
| 4 | WhatsApp Personal | CEF embed + QR | spec §10 |
| 5 | WhatsApp Business | Cloud API oficial | nuevo |
| 6 | Telegram | Bot API | nuevo |
| 7 | Slack | App OAuth | nuevo |
| 8 | Notion | OAuth (deprecating native? consultar) | nuevo |
| 9 | GitHub | PAT o OAuth App | nuevo |
| 10 | Linear | API key | nuevo |
| 11 | Discord | Bot token | nuevo |
| 12 | Obsidian | Local vault (sin auth) | nuevo (William: "opcional") |
| 13 | Apple Reminders | EventKit (macOS) | nuevo |
| 14 | iCloud/macOS Notes | macOS only | nuevo |
| 15 | Microsoft 365 (Outlook/Teams) | OAuth MSAL | nuevo |
| 16-20 | TBD por demanda | — | — |

### 7.4 PARTNER (Composio/Pipedream)

UI: marketplace con badge "Powered by Composio" + disclaimer obligatorio:

> *"Esta integración usa Composio como middleware. Tus tokens OAuth se almacenan en sus servidores. Si prefieres custodia total, conecta vía MCP server custom o solicita la integración nativa."*

User opt-in explícito. Audit log marca `provider_used=composio` en cada llamada.

---

## 8. WhatsApp embed (Feature de captura más sensible)

Referencia: `spec_seal_whatsapp_integration_20260522.md` (ya escrito).

Resumen ejecutivo aquí:

- **Arquitectura**: WhatsApp Web cargado en webview Tauri + cookies en `~/.config/soul-companion/channels/whatsapp/` cifradas
- **Procesamiento**: local-first Gemma 4, cloud opcional BYOK
- **Permisos granulares**: Read · ReadHistory · SuggestReplies · AutoReply (default OFF) · Send · Admin · CloudProcessing · AuditLog
- **Disclaimer obligatorio** al conectar (ban risk Meta + GDPR + audit visible)
- **Multi-cuenta**: N WhatsApps simultáneos (personal + business + cliente)
- **Multi-agente**: asignar agente por chat (JARVIS personal, ALICE comercial)

---

## 9. Calls / Meet Agent (sensible legal)

Referencia: ALICE doc 46 `calls_meet_agent.md`.

### 9.1 Riesgos identificados

- Solo el host admite el agente; otros participantes NO consienten
- GDPR/LFPD requieren consent de TODOS los participantes
- Wiretapping laws varían por jurisdicción

### 9.2 Diferenciador SOUL — multi-jurisdicción desde día 1 (William 2026-05-22)

Disclaimer obligatorio + checklist pre-meeting + **detección automática de jurisdicción** del host (geo-IP + timezone):

- [ ] Anuncié a los participantes que la IA escucha
- [ ] Tengo consent verbal grabado de TODOS los participantes
- [ ] Cumplo con regulación local detectada: <jurisdicción>

Disclaimers preparados para:
- **EU (GDPR)**: consent explícito + DPO contact + Right to Erasure
- **US two-party consent states** (CA, FL, IL, MD, MA, MT, NV, NH, PA, WA): all-party recording consent required
- **US one-party consent states**: host consent sufficient
- **LATAM (LFPD Perú, LGPD Brasil, LFPDPPP México)**: consent explícito + finalidad declarada
- **UK (DPA 2018)**: consent + lawful basis

Audit log de cada participación + opción **"modo notas privadas"** donde el agente NO graba audio, solo escribe notas del host post-meeting.

**Pre-launch task**: abogado IP review disclaimer multi-jurisdicción (~$1500-3000 USD estimado por la complejidad multi-país, vs $500-1500 que sería solo Perú).

---

## 10. Onboarding (First-run wizard)

### 10.1 OpenHuman (observado)

10 pasos con OAuth pushy: Local-vs-Cloud → SignIn → RPC URL → OAuth callback → Connect Gmail (Composio) → Building Profile → Home → … → Avatar setup. Cada paso intenta capturar permisos.

### 10.2 SEAL Companion v1 — 4 pasos limpios

1. **Welcome** — qué es SEAL, qué hace, qué NO hace (privacy claim verificable)
2. **Tu nombre + el del agente** — sin signin obligatorio, sin OAuth (anonymous-first)
3. **OCEAN preset (opcional)** — perfil del agente (analítico / cálido / técnico / creativo)
4. **Confirm + bienvenida** — el agente saluda con su voz, listo

> **"Skip for now" everywhere.** Ningún OAuth obligatorio. Integraciones se conectan cuando el usuario las necesita, no al inicio.

> Esta es ya la implementación v0.5.1 entregada por ALICE. Pulir UX, no rehacer.

---

## 11. Privacy + Audit + Sycophancy

### 11.1 Privacy claim verificable

- Todo procesamiento local por default
- Si usuario activa BYOK cloud: warning UI "Esta query saldrá a Anthropic"
- Audit log expone qué se procesó local vs cloud + provider usado
- Vault de keys cifrado AES-GCM en disco + key derived from OS keyring

### 11.2 Audit log (visible y exportable)

UI `Settings → Audit Log`:
- Filter por agent / channel / action / date
- Export JSON + CSV
- Diferenciador vs OpenHuman: ellos NO exponen audit visible

### 11.3 Sycophancy detector

Existing: `soul_v3.sycophancy_eval`. UI `Governance → Integrity` ya entregada en v0.35.

---

## 12. Pricing — "Dale el dulce al bebé"

### 12.1 Tier Free (default y único en MVP — William 2026-05-22)

- **Local Gemma 4 ilimitado** (sin créditos, sin rate limit técnico)
- **Todas las features funcionando** (Dreams, Memory Tree, Multi-agent, Audit, etc.)
- **Integraciones nativas** (10)
- BYOK cloud opcional sin markup
- Limit suave: opcionalmente cap en `total_memories` (ej. 100k) para forzar maintenance/archive

Mensaje canónico UI: *"SOUL es gratis. Sin cuentas que vencen, sin créditos que se acaban. Tu IA en tu máquina."*

> **MVP = solo Free.** Tier Plus/Pro NO se construye. Decisión William 2026-05-22: refuerzo "dale el dulce al bebé". Maximizar adopción 0→50k antes de pensar en conversion.

### 12.2 Tier BYOK (opt-in)

- Mismo producto Free + slot para BYOK keys
- User paga directamente a Anthropic/OpenAI/etc al costo real
- SEAL no toma markup en BYOK (transparencia)
- Mensaje: *"Si quieres usar Claude/GPT, trae tu API key. No te cobramos extra."*

### 12.3 Tier Pro (futuro post-PMF)

Cuando masa instalada >100k:
- Sync cifrado entre devices ($X/mo)
- Backup automático cifrado a SEAL cloud ($X/mo)
- Multi-user team management ($X/mo)
- Priority support
- Mantener BYOK transparente (no markup)
- **Mensaje:** *"Mismo precio que OpenAI Plus. Más privado, más capaz, multi-agente."*

> William textual: *"hasta masificar clientes sera gratis, poco a poco le bajaremos, y luego cobramos lo mismo que hizo open ai"*

### 12.4 Reselling tokens (futuro, William TBD)

- Contratos enterprise: SEAL revende inferencia con margin
- Optimization: nuestro routing 4-roles reduce costo real ~30-50% vs solo Claude
- Multi-provider routing: aprovecha precio del más barato por role
- Tier pricing: enterprise paga por seat, no por turno

> William textual: *"lo nuestro sera api, pero luego lo discutimos"*

---

## 13. UI — Vistas requeridas

### 13.1 Ya entregadas (v0.36, ALICE)

`Chat · Agents · Memory · Goals · Skills · Settings · Subconscious · Governance · NERVES · Bench · Integrations · Screen · Profile`

### 13.2 Nuevas / pulir para v1

| Vista | Status | Owner |
|---|---|---|
| **Dreams Timeline** | nueva (post §5) | ALICE |
| **AI Backend Settings (inverted UX)** | refactor existente | ALICE |
| **Audit Log Visible** | nueva | ALICE |
| **Channel Permissions Granular** | nueva (WhatsApp + más) | ALICE |
| **First-run Wizard 4-step** | ya existe, pulir | ALICE |
| **Memory Tree Hierarchical View** | nueva post §6 | ALICE |
| **BYOK Setup + Test** | nueva | ALICE |
| **Onboarding Disclaimers (Calls/WhatsApp)** | nueva | ALICE |

---

## 13.5 Installer / Local AI auto-setup (ADA P3 — 2026-05-22)

> ADA review elevó esto como prioridad P3: *"El usuario no debería necesitar terminal. La app debe detectar/instalar/configurar Ollama + modelo default + Whisper/STT + Piper/TTS."*

**Pre-existing SOUL App requiere terminal para Ollama** — gap crítico para producto consumer.

Componentes a construir:

| Componente | Función |
|---|---|
| `bootstrap/ollama_installer.py` | Detecta si Ollama existe; si no, descarga binario per-OS + ejecuta install. macOS/Linux/Windows. |
| `bootstrap/model_downloader.py` | Descarga `gemma3:12b` (default) o equivalente al primer arranque, con progress bar visible en UI |
| `bootstrap/whisper_installer.py` | whisper.cpp local STT + modelo `base.en` o `medium.es` según locale |
| `bootstrap/piper_installer.py` | Piper TTS local + voz default español (Lima) / inglés / otros idiomas |
| `ui/FirstRunBootstrap.tsx` | Pantalla wizard "Instalando tu IA local…" con barras + skip-friendly |

Sin terminal. Sin instrucciones técnicas. Click → ready en 3-10 minutos según conexión.

## 13.6 Voice (STT + TTS) first-class (ADA P4 — 2026-05-22)

> ADA: *"Voice input/output as a first-class control. Screen + voice surfaces matter for consumer."*

Spec previa solo menciona "Voice input (Web Speech API)" en v0.5.1. ADA marca esto como insuficiente para producto comercial. Upgrade:

| Capa | Hoy | Spec v1.0 |
|---|---|---|
| STT | Web Speech API (cloud Google) | **whisper.cpp local** + Web Speech API fallback |
| TTS | navegador (variable) | **Piper local** + ElevenLabs BYOK opcional |
| Voice activation | botón mic | **Hotword opcional** ("Hey SOUL") con WhisperWake o picovoice |
| Audio I/O | input default | **Device selector** (mic + speakers) + level meter |

## 13.7 Commercial readiness (ADA P5 — 2026-05-22)

Lista de cosas que la spec implícitamente asumía pero ADA pidió hacer explícito:

| Item | Status | Owner |
|---|---|---|
| Packaging .deb arm64 + .deb amd64 | en roadmap §15 v1.0 | NEXUS |
| Packaging .dmg macOS Intel + Apple Silicon | en roadmap §15 v1.0 | NEXUS |
| Packaging .exe / .msi Windows | a definir post-MVP | NEXUS |
| Updates OTA (Tauri Updater) | a construir | NEXUS |
| Public docs (GitBook o equivalent) | a construir | ALICE |
| Discord/Twitter community | a crear pre-launch | William |
| License/pricing decision | §12 (gratis MVP, Pro deferido) | William ✅ |
| Safety/privacy mode toggles | parcial — Settings → Privacy existing | ALICE pulir |
| Crash reporting opt-in | a construir | NEXUS |
| Telemetry opt-in (privacy-respecting) | a construir | NEXUS + ADA review |

---

## 14. DELEGATE-52 audit gates

Antes de declarar features "done":

| Gate | Audit responsible | Criterio |
|---|---|---|
| Schema migrations | NEXUS | DDL aplicado, sin breakage en tests |
| Endpoints nuevos | NEXUS | Whitelist agents, user-product guards, encoding UTF-8 |
| UI components | NEXUS | Tests pasando, build verde, no regresiones |
| Daemons | NEXUS | systemd restart aplicado, código nuevo cargado en memoria |
| Privacy claims | ADA (revisor) | Verificación end-to-end del flow, no hay leaks |
| Security (crypto/auth) | NEXUS + ADA | Trazar bytes paso a paso (regla William 16-may) |

---

## 15. Roadmap por fases

### Fase MVP (semana 1-2)

- [ ] Naming final decision (SOUL / Anima / SEAL Companion)
- [ ] Dream Cycle v1 (§5) — ALICE
- [ ] AI Backend settings inverted UX (§4.4) — ALICE
- [ ] Audit Log visible UI (§11.2) — ALICE
- [ ] BYOK setup + 4-roles routing — ALICE
- [ ] Schema migrations (§3.1) — NEXUS aplica
- [ ] DELEGATE-52 audit MVP

### Fase v1.0 (semana 3-4)

- [ ] Memory Tree h→d→m→y vista jerárquica (§6) — ALICE
- [ ] WhatsApp embed read-only MVP (§8 fase 1) — ALICE+ADA
- [ ] Onboarding disclaimers (§9, §8) — ALICE
- [ ] 5 integraciones nativas (Gmail/GCal/Slack/Notion/GitHub) — ALICE
- [ ] Sycophancy + Integrity dashboard — ya v0.35
- [ ] Packaging .deb arm64 + .dmg macOS — NEXUS
- [ ] First-run wizard pulir UX — ALICE

### Fase v1.5 (semana 5-8)

- [ ] WhatsApp suggest replies + auto-reply (§8 fase 2-3) — ALICE
- [ ] Calls Meet Agent con disclaimer (§9) — ALICE
- [ ] 15+ integraciones nativas — ALICE
- [ ] Composio partner tier — JARVIS
- [ ] Cross-channel intelligence (combine WA + Gmail + Slack) — ALICE
- [ ] Multi-cuenta + multi-agente assignment — ALICE
- [ ] Soft launch beta privada (50 users) — William decide audiencia

### Fase v2.0 (mes 3+)

- [ ] Sync E2E cifrado entre devices (Tier Pro)
- [ ] Reselling API enterprise (tier business)
- [ ] Mobile companion (iOS/Android) — TBD
- [ ] Crypto wallet (PENDING decision William)

---

## 16. Decisiones cerradas (William 2026-05-22 16:34)

| # | Decisión | Valor | Notas |
|---|---|---|---|
| 1 | Naming final | **SOUL** | Window title "Soul App" preservado. SEAL companion → producto SOUL |
| 2 | Crypto wallet | **Sí pero DESACTIVADO en MVP** | Feature flag `crypto_wallet_enabled=false`. Activable en update futuro. NO se construye UI ni schema en MVP |
| 3 | Lista priorizada 10 NATIVE | **JARVIS sugerencia aprobada** | 1.Gmail 2.GCal 3.WhatsApp 4.Drive 5.Telegram 6.Slack 7.GitHub 8.Notion 9.Obsidian 10.Microsoft365 |
| 4 | Sync cloud E2E | **v1.5+** | NO en MVP — standalone local-first puro |
| 5 | Mobile companion | **post-v2.0** | Mes 3+. Desktop primero |
| 6 | Soft launch beta | **~2026-06-20 · 20-50 usuarios técnicos** | Discord/Twitter círculo cercano. Validación cualitativa |
| 7 | Compliance Calls jurisdicción | **Multi-jurisdicción desde día 1** | Disclaimer multi-país. Abogado IP review pre-launch. Costo upfront mayor — William prefiere robustez vs solo Perú |
| 8 | Pricing | **GRATIS al comienzo** | Tier Pro deferido. Refuerza "dulce al bebé". Cuando masa instalada >50k → reevaluar pricing Plus/Pro |

### 16.1 Crypto wallet — implementación feature-flag

```toml
# companion.toml
[features]
crypto_wallet_enabled = false  # 2026-05-22: locked off, activate post-MVP
```

- Schema `soul_v3.crypto_wallets` puede definirse pero migration **NO se aplica** hasta activación
- UI section Settings → Wallet condicional `if feature_enabled`
- Quando se active: BIP39 seed + AES-GCM encryption + UI clear "Esta función es experimental, custodia única"

### 16.1.bis Agent Capabilities — schema final + defaults (NEXUS commit 83320ff)

Schema final implementado (wide, ya en prod):

```sql
soul_v3.agent_capabilities (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL UNIQUE,
    cap_shell_commands  BOOLEAN DEFAULT FALSE,
    cap_git             BOOLEAN DEFAULT FALSE,
    cap_read_files      BOOLEAN DEFAULT TRUE,
    cap_write_files     BOOLEAN DEFAULT FALSE,
    cap_screen_capture  BOOLEAN DEFAULT FALSE,
    cap_camera          BOOLEAN DEFAULT FALSE,
    cap_web_search      BOOLEAN DEFAULT TRUE,
    cap_browser_control BOOLEAN DEFAULT FALSE,
    cap_memory_read     BOOLEAN DEFAULT TRUE,
    cap_memory_write    BOOLEAN DEFAULT TRUE,
    cap_cron_jobs       BOOLEAN DEFAULT FALSE,
    cap_notifications   BOOLEAN DEFAULT TRUE,
    cap_channel_read    BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Defaults por modo:**
- `team-dashboard` (agentes internos JARVIS/ADA/NEXUS/ALICE/DUM): seeds individuales razonables ya aplicados
- `user-product` (consumer SOUL): **TODO OFF** al first-run, user activa explícitamente con confirmation modal

**Pendiente Sprint 2 (no bloquea MVP):**
- Tabla complementaria `soul_v3.capability_scope` (whitelists shell commands, file dirs, network domains)
- Tabla `soul_v3.capability_audit` (authorized_by + authorized_at + change_reason)
- UI confirmation modal para toggles high-risk (shell/browser/camera)
- safety_critical patterns (rm -rf, drop table, force push) bloqueados SIEMPRE incluso ON

### 16.2 Pricing — modelo MVP "todo gratis"

- Free tier ilimitado · Gemma 4 local · todas las features · BYOK opcional sin markup
- Mensajería UI: *"SOUL es gratis. Sin cuentas que vencen, sin créditos que se acaban. Tu IA en tu máquina."*
- Tier Plus/Pro **NO se construye en MVP**. La spec lo deja como referencia §12.3-12.4 para futuro
- Estrategia: maximizar adopción 0-50k usuarios. Cuando llegue PMF, reevaluar conversion model con datos reales

---

## 17. Riesgos identificados

| Riesgo | Severidad | Mitigación |
|---|---|---|
| **Contaminación licencia GPL-3.0 OpenHuman** | **CRÍTICA** | **Policy clean room: NO copy-paste de su código. Solo estudio arquitectural. Validar c/PR.** |
| WhatsApp ban (Meta detecta automation) | Alta | Disclaimer + WhatsApp Business API alternativa |
| GDPR mensajes terceros sin consent | Alta | Disclaimer + audit log + opt-in user |
| Calls wiretapping laws | Alta | Disclaimer + checklist consent + audit |
| Ollama caído → fallback fail | Media | Health check startup + UI warning + status indicator |
| OpenHuman v0.54.0 vs nuestro mapeo v0.53.31 | Baja | ALICE revisar changelog cuando haya bandwidth |
| Local Gemma 4 calidad < Claude | Media | BYOK opcional + routing por role + fallback |
| User no entiende "Local vs Cloud" | Media | UX inverted + tooltips + onboarding claro |
| Costo cloud BYOK alto si usuario abusa | Baja | Cost dashboard transparente + límites configurables |
| Drift OCEAN per-agente | Media | Drift detector existing + Identity Continuity v2 |

### 17.1 Clean Room Policy (mandatorio)

> Establecida 2026-05-22 tras verificar OpenHuman = GPL-3.0 viral copyleft.

1. **NO** ejecutar `cp /home/dadito/IA/openhuman/... ./seal-desktop/...` ni equivalente
2. **NO** copiar funciones, structs, schemas Rust/TS literales
3. **SÍ** estudiar arquitectura, identificar patterns, escribir reimplementación nueva en `seal-desktop/ui` + `companion_core`
4. **SÍ** referenciar features por nombre y descripción funcional ("ellos tienen Memory Tree h→d→m→y, hagamos uno similar con bitemporal")
5. Cada commit que toque features inspiradas en OpenHuman → mensaje commit debe decir "clean room reimplementation of <feature>"
6. NEXUS revisa commits trimestralmente con diff vs `/home/dadito/IA/openhuman/` para detectar contaminación
7. Auditoría legal opcional ($500-1500 USD abogado IP Lima) antes de soft launch comercial

---

## 18. Métricas de éxito (KPIs)

### Pre-launch (MVP)

- 100% features documentadas en este spec ejecutadas
- DELEGATE-52 audit pasado en todos los componentes
- 0 leaks de datos / 0 incidentes de seguridad en QA
- Bench SEAL >70% (vs ~60% actual)

### Post-launch (soft beta)

- 50 usuarios activos semana 1
- DAU/MAU ratio > 30% (engagement)
- NPS > 40
- 0% bans WhatsApp en primeros 100 conectados (canary)

### Post-PMF (target 6 meses)

- 100k usuarios instalados
- 5% conversión a BYOK (5k usuarios paying directamente al provider)
- 1% conversión a Tier Pro futuro (1k × $X/mo)

---

## 19. Lecciones del re-mapeo OpenHuman

1. **Su privacy claim es marketing, no técnica** — verificamos cloud lock-in con WSS a tinyhumans.ai
2. **Composio es su outsourcing de OAuth** — tokens en 3rd party, no en device del usuario
3. **Memory Tree h→d→m→y es lo mejor que tienen** — replicar + mejorar con bitemporal
4. **Dreams es "Coming soon" — first-mover oportunidad** real para SEAL
5. **$0.42/mo es su costo cloud floor** — sabemos su unit economics
6. **"Advanced" en Local = manipulación UX** — invertimos el frame
7. **Live Meet Agent sin disclaimer = riesgo legal** — diferenciador ético
8. **Onboarding pushy (10 pasos OAuth) = friction** — SEAL 4 pasos limpios gana

---

## 20. Próximas acciones inmediatas

1. **Postear este spec en webchat** para review William
2. Aguardar feedback sobre §16 pendientes (8 decisiones)
3. Una vez aprobado:
   - JARVIS: registrar task maestra en `soul_v3.agent_tasks`
   - ALICE: descomponer roadmap en subtasks ejecutables
   - NEXUS: planificar schema migrations en orden
   - ADA: agendar revisiones críticas (privacy + crypto)
4. Continuar mapeo capturas pendientes — esto spec es viva, actualizar §3-§13 con cada captura nueva

---

**Última actualización:** 2026-05-22 16:35 (JARVIS — spec v1.0 CERRADA, 8 decisiones William aplicadas)

**Audit DELEGATE-52:** ✅ NEXUS PASS (3 notas aplicadas)

**ADA review:** pendiente — handoff a Codex programado para revisión independiente

**Status:** **READY FOR ADA CODEX HANDOFF**
