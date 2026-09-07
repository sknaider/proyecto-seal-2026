# SPEC: Absorción Estratégica — Framework AMI v0.11
**Autor:** ADA | **Fecha:** 2026-04-28 | **Modo:** Opus 4.7
**Autorizado por:** William Henry Tovar Urquia (28-abr-2026 11:39 Lima)

> Documento interno de análisis. Patrones se reescriben nativos puro en SEAL — sin imports, sin attribution, sin nombres upstream en código de producción. Producto comercial = código limpio, auditable, IP de SEAL/GTL Consulting.

---

## 0. PRINCIPIOS DE ABSORCIÓN

| Principio | Aplicación |
|---|---|
| **Native-first** | Re-implementar el patrón en Python puro stdlib + asyncpg/pgvector ya existentes. No copiar archivos, no instalar dependencias upstream. |
| **Sin huellas** | Renombrar variables, clases, archivos. Sin docstrings que citen fuente. Sin comments tipo "based on X". |
| **Decompilar el principio, no el código** | Leer para entender el algoritmo/patrón. Cerrar archivo. Reescribir desde cero con vocabulario SEAL. |
| **Audit trail interno** | Este spec documenta qué tomamos. Vive en `/agents/`, no en producción. |
| **Compatibilidad arquitectural** | Cada absorción se valida contra los componentes existentes (soul_v3, MCP, agent_bridge, gateway actual) antes de mergear. |

---

## 1. MAPA DE CAPACIDADES — fuente externa vs SEAL actual

| # | Capacidad | Fuente externa tiene | SEAL tiene | Gap | Decisión |
|---|---|---|---|---|---|
| 1 | **Multi-platform messaging** | 15+ plataformas (Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Mattermost, Email, SMS, DingTalk, Feishu, Wei*, QQ, BlueBubbles, Home Assistant, webhook, API server) | Matrix + web_chat + terminal | -12 plataformas | **ABSORBER prioridad 1** |
| 2 | **Plugin lifecycle hooks** | 18 hooks formales (pre/post tool/llm/api/session/approval, gateway dispatch, transform_*) | hooks ad-hoc en post_compact + pre_edit_checkpoint | Falta sistema formal | **ABSORBER prioridad 2** |
| 3 | **Tool registry auto-discovery** | AST scan de `tools/*.py` por top-level `registry.register()` calls | MCP-based, manual register en mcp_server_v2 | Carga manual, sin auto-discover | **ABSORBER prioridad 3** |
| 4 | **Profile isolation** | `SOUL_HOME` env override + `_apply_profile_override()` antes de cualquier import | systemd services + ws_listener PID files | No multi-tenant | **ABSORBER prioridad 2** (esencial para venta) |
| 5 | **Setup wizard interactivo** | `soul setup` — detecta deps, configura gateway, instala skills | scripts manuales en /scripts | Cliente no técnico no puede instalar | **ABSORBER prioridad 1** (esencial venta) |
| 6 | **Self-diagnostic** | `soul doctor` — chequea install, perms, deps, gateway | DUM watchdog (interno equipo) | No diagnostic CLI para usuario | **ABSORBER prioridad 2** |
| 7 | **Self-update** | `soul update` con rollback | pull manual | Cliente no actualiza solo | **ABSORBER prioridad 2** |
| 8 | **Skills procedural** | Markdown + YAML frontmatter, agentskills.io standard, auto-create + auto-improve | soul_v3.skills con vote+sandbox | Tenemos calidad, falta portabilidad | **ABSORBER el formato standard, mantener nuestros gates** |
| 9 | **SQLite FTS5 session search** | `state.db` con FTS5, WAL mode, parent_session chains | soul_v3 pgvector + Neo4j | Tenemos algo superior | **NO absorber — mejor lo nuestro** |
| 10 | **Pluggable memory backends** | ABC `MemoryProvider` + 8 implementaciones | soul_v3 nativo | Mejor lo nuestro | **NO absorber, pero exponer ABC para clientes que quieran integrar** |
| 11 | **Context compressor** | `agent/context_compressor.py` — manual + auto compression | post_compact_hook + pre_sleep_distill | Empate, su impl es más madura | **ESTUDIAR principio, mejorar el nuestro** |
| 12 | **Streaming context scrubber** | State machine para `<memory-context>` fences | No equivalente | Falta — leak risk | **ABSORBER prioridad 3** |
| 13 | **Cron scheduler** | `cron/scheduler.py` + croniter, delivery a cualquier plataforma | CronCreate session + crontab OS | Empate funcional | **ABSORBER pattern de delivery routing** |
| 14 | **Subagent delegation** | `delegate_task` tool — subagentes aislados | Agent tool + multi-agente SEAL | Tenemos algo más rico | **NO absorber** |
| 15 | **TUI Ink/React + xterm.js dashboard** | TypeScript+Python via JSON-RPC stdio, embed TUI en browser via PTY | web_chat actual (Next.js) | Empate visual; ellos ganan en CLI feel | **ABSORBER pattern PTY dashboard** |
| 16 | **MCP client + server** | `mcp_serve.py` con 10 tools | soul_api MCP server con 100+ tools | Mejor lo nuestro | **NO absorber** |
| 17 | **Skin/theme engine** | YAML-driven skins (colors, spinner, branding) | Sin tematización formal | Nice-to-have | **ABSORBER prioridad 4** (UX, no crítico) |
| 18 | **Approval system** | Command allowlist, pattern matching, timeout, surface-aware (CLI/gateway) | pre_edit_checkpoint hook | Empate básico | **ABSORBER pattern de approval prompts surface-aware** |
| 19 | **Credential pool** | Rotación automática entre múltiples API keys | manual env vars | Útil para production scale | **ABSORBER prioridad 3** |
| 20 | **Trajectory compression para training** | `trajectory_compressor.py` + Atropos RL envs | No equivalente | Para fine-tuning futuro | **ESTUDIAR — relevante para SEAL training pipeline** |

**Resumen:** 20 capacidades analizadas. Absorber 13 (priorizadas). NO absorber 5 (mejor lo nuestro). Estudiar 2 (mejorar lo nuestro).

---

## 2. PLAN DE ABSORCIÓN POR PRIORIDAD

### PRIORIDAD 1 (mayor ROI, base comercial) — Sprint 1
1. **Gateway multi-plataforma** → módulo `seal/channels/` con base.py + adapter por plataforma (ADA owns)
2. **Setup wizard** → `seal_cli/setup.py` interactivo en stdlib (ADA owns)
3. **Profile isolation** → `SEAL_HOME` + token locks + installer (JARVIS owns — spec separado)

### PRIORIDAD 2 (escalabilidad y operación) — Sprint 2
4. **Plugin lifecycle hooks** → `seal/hooks.py` con 18 hooks formales
5. **Self-diagnostic CLI** → `seal doctor`
6. **Self-update** → `seal update` con backup pre-update
7. **Skills standard portátil** → SKILL.md frontmatter compatible, sin perder vote+sandbox

### PRIORIDAD 3 (calidad y robustez) — Sprint 3
8. **Tool auto-discovery** → AST scan en `seal/tools/` con `registry.register()`
9. **Streaming scrubber** → `seal/utils/stream_scrubber.py` para fences memory
10. **Credential pool** → rotación de API keys

### PRIORIDAD 4 (UX/polish) — Sprint 4
11. **PTY dashboard pattern** → web dashboard que embebe terminal real
12. **Skin engine** → temas YAML para branding cliente
13. **Approval surface-aware** → prompts coherentes CLI ↔ gateway

---

## 3. ESPECIFICACIÓN POR ABSORCIÓN

### 3.1 Gateway Multi-Plataforma (Prioridad 1)

**Principio extraído:**
- Un único proceso (`seal-gateway`) corre N adapters en asyncio
- Cada adapter implementa interfaz: `connect()`, `disconnect()`, `send_message()`, `receive_loop()`
- Mensaje normalizado: `MessageEvent {platform, user_id, chat_id, text, timestamp, metadata}`
- Token-scoped locks evitan que dos profiles usen mismas credentials

**Reescritura nativa SEAL:**

```
seal/channels/
├── __init__.py
├── base.py              # ABC ChannelAdapter
├── runner.py            # SealChannelRunner — orquesta adapters
├── event.py             # MessageEvent dataclass
├── lock.py              # Scoped credential locks
└── adapters/
    ├── matrix.py        # ya tenemos
    ├── webchat.py       # ya tenemos
    ├── telegram.py      # NUEVO
    ├── discord.py       # NUEVO
    ├── slack.py         # NUEVO
    ├── whatsapp.py      # NUEVO
    ├── signal.py        # NUEVO
    ├── email.py         # NUEVO
    └── webhook.py       # NUEVO
```

**Cada adapter — pseudocódigo:**

```python
# seal/channels/base.py
class ChannelAdapter(ABC):
    name: str
    async def connect(self, config: dict) -> None: ...
    async def disconnect(self) -> None: ...
    async def send(self, chat_id: str, text: str, metadata: dict = None) -> str: ...
    async def receive(self) -> AsyncIterator[MessageEvent]: ...
    def healthcheck(self) -> dict: ...
```

**Integración con SEAL:**
- Cada `MessageEvent` que entra → pasa por `active_recall(agent, context)` antes de procesarse
- Cada respuesta que sale → dispara `memory_store(agent, content, scope='private')` automáticamente
- Esto no lo tiene la fuente externa — es nuestro valor diferencial

**Test plan:**
- Test adapter por adapter con mock servers
- E2E: enviar mensaje desde Telegram real → llega a JARVIS → soul_v3 registra interaction → respuesta vuelve
- Carga: 100 mensajes/min en 3 plataformas simultáneas
- Test de credential lock: 2 profiles con misma API key → segundo profile debe fallar limpio

**Sin huellas:** Nombre del módulo `seal/channels/`, no `seal/gateway/`. Clases `ChannelAdapter`, no `PlatformAdapter`. Sin "based on" comments.

**Estimación de esfuerzo (per adapter):**
- Adapter base (interface + lifecycle): ~500 LOC SEAL
- Telegram adapter: ~1500 LOC SEAL (rate limits, UTF-16 length handling, file uploads, voice, inline keyboards)
- Discord adapter: ~2000 LOC SEAL (slash commands, threads, voice, embed cards)
- Slack adapter: ~1200 LOC SEAL (Block Kit, thread replies, app-level events)
- WhatsApp adapter: ~1000 LOC SEAL (media handling, status messages)
- Email adapter: ~600 LOC SEAL (IMAP poll + SMTP send)
- Webhook adapter: ~300 LOC SEAL (HMAC auth + JSON payloads)

**Total estimado para 5 plataformas Sprint 1: ~5500 LOC nativo SEAL.** No leerage upstream — reescribimos con SDKs oficiales de cada plataforma directamente (python-telegram-bot, discord.py, slack-bolt, etc.) que SÍ son aceptables (son SDKs oficiales del vendor de la plataforma, no librerías de framework competidor).

**Decisión sobre SDKs de plataformas:**
- ✅ ACEPTABLE: python-telegram-bot, discord.py, slack-bolt, mautrix (Matrix), aiohttp para webhooks. Son interfaces oficiales del vendor — no introducen dependencia de framework competidor.
- ❌ NO ACEPTABLE: librerías de wrappers que abstraen múltiples plataformas y vienen de competidores. Reescribimos abstración SEAL nativa por encima de SDKs oficiales.

---

### 3.2 Setup Wizard (Prioridad 1)

**Principio extraído:**
- Comando `setup` interactivo, primera ejecución
- Detecta entorno (Linux/macOS/WSL/Termux), Python version, deps faltantes
- Pregunta credentials una a una con prompts amigables
- Genera `~/.seal/config.yaml` y `~/.seal/.env` (secretos solo)
- Migración opcional desde otros sistemas

**Reescritura nativa SEAL:**

```
seal_cli/
├── setup.py           # SealSetupWizard — entry interactivo
├── env_detect.py      # Detecta OS, Python, deps
├── config_writer.py   # Escribe config.yaml, .env
└── migration.py       # Importa desde otros sistemas (extensible)
```

**Flow:**
```
$ seal setup
[*] Detectando entorno... Linux x86_64, Python 3.11.9 ✓
[*] Configurando profile: default
[?] Tu nombre: William Tovar
[?] Email: williamtovaru@gmail.com
[?] ¿Conectar Telegram? (y/N): y
    [?] Bot token: ********
    [✓] Token válido
[?] ¿Cargar skills médicas? (y/N): y
    [✓] 12 skills cargadas en ~/.seal/skills/
[*] Configuración guardada en ~/.seal/config.yaml
[✓] SEAL listo. Ejecuta: seal start
```

**Sin huellas:** Sin referencia a "wizard estilo X". UI genérica.

---

### 3.3 Profile Isolation (Prioridad 1) — DELEGADO A JARVIS

> **Coordinación 28-abr-2026 11:41 Lima:** JARVIS asumió scope completo de "SEAL Profile System" — arquitectura de distribución comercial, SEAL_HOME, token locks, installer nativo. Mi sección original aquí queda como input estratégico; el spec detallado lo entrega JARVIS en paralelo.

**Resumen del principio absorbido (para referencia cruzada):**
- `SEAL_HOME` env var + apply antes de cualquier import
- Multi-tenancy: `~/.seal/profiles/<cliente>/`
- Aislamiento total entre clientes
- Token-scoped locks evitan que dos profiles compartan credentials

JARVIS define la implementación. Mi spec depende de su API para los demás componentes (channels, setup, hooks).

---

### 3.4 Plugin Lifecycle Hooks (Prioridad 2)

**Principio extraído:**
- 18 hooks formales nombrados como string keys
- Plugins registran callbacks via `ctx.register_hook(name, fn)`
- Core llama `invoke_hook(name, **kwargs)` en puntos definidos
- Hooks pueden retornar dict para influenciar flow (skip, rewrite, allow)

**Reescritura nativa SEAL:**

```python
# seal/hooks.py
VALID_HOOKS = {
    "pre_tool_call", "post_tool_call",
    "pre_llm_call", "post_llm_call",
    "pre_api_request", "post_api_request",
    "transform_terminal_output", "transform_tool_result",
    "on_session_start", "on_session_end",
    "on_session_finalize", "on_session_reset",
    "subagent_stop",
    "pre_gateway_dispatch",
    "pre_approval_request", "post_approval_response",
    # SEAL-only:
    "pre_memory_store", "post_memory_store",
    "pre_active_recall", "post_active_recall",
}

class HookRegistry:
    def register(self, name: str, fn: Callable, priority: int = 50): ...
    def invoke(self, name: str, **kwargs) -> List[Any]: ...
    def invoke_first(self, name: str, **kwargs) -> Any: ...  # primer plugin que retorne no-None
```

**Diferencial SEAL vs fuente externa:**
- Hooks `pre_memory_store` / `pre_active_recall` son nuestros — el otro framework no tiene memoria semántica
- Permite plugins que enriquezcan/filtren memorias antes de pgvector

**Test plan:**
- Plugin de prueba que loggea cada hook → verificar orden de invocación
- Hook que retorna `{"action": "skip"}` → mensaje debe ser dropped
- Hook que retorna `{"action": "rewrite"}` → mensaje debe ser modificado

---

### 3.5 Skills Portable Standard (Prioridad 2)

**Principio extraído:**
- SKILL.md con YAML frontmatter: `name, description, version, platforms, metadata.tags, metadata.category, metadata.config`
- agentskills.io standard abierto (open ecosystem)
- Heavy/niche skills en `optional-skills/`, no activas por default
- Skills son markdown que se inyectan como user message (preserva prompt caching)

**Reescritura nativa SEAL:**

Mantener nuestra estructura `soul_v3.skills` con `skill_propose+vote+sandbox`. Agregar:

```python
# seal/skills/portable.py
def export_skill(skill_id: str) -> str:
    """Exporta skill a formato SKILL.md portable."""
    skill = load_skill(skill_id)
    frontmatter = {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "platforms": skill.platforms,
        "metadata": {
            "tags": skill.tags,
            "category": skill.category,
        }
    }
    return f"---\n{yaml.dump(frontmatter)}---\n\n{skill.content}"

def import_skill(md_content: str) -> int:
    """Importa SKILL.md, valida con sandbox, propone via vote.
    
    NO se autoinstala — pasa por skill_propose() + skill_sandbox_run() + skill_vote().
    Ese es nuestro gate de calidad superior.
    """
    ...
```

**Diferencial:**
- Compatible con ecosistema portátil
- Pero SEAL tiene quality gates que el otro no: vote de equipo, sandbox isolated, denial_track

---

### 3.6 Streaming Context Scrubber (Prioridad 3)

**Principio extraído:**
- State machine que survive boundaries de chunks en streaming
- Detecta `<memory-context>` open tag, descarta hasta `</memory-context>` close
- Holds back partial-tag tails (e.g. `<memo` al final de chunk 1, completes en chunk 2)

**Reescritura nativa SEAL:**

```python
# seal/utils/stream_scrubber.py
class ContextStreamScrubber:
    """Scrub fences inyectados de active_recall del output del LLM en streaming."""
    
    OPEN = "<seal-context>"   # tag SEAL nativo, no upstream
    CLOSE = "</seal-context>"
    
    def __init__(self):
        self._in_span = False
        self._buf = ""
    
    def feed(self, delta: str) -> str:
        """Feed chunk, return visible portion (sin fence content)."""
        ...
    
    def flush(self) -> str:
        """Al fin del stream, retorna buffer trailing si no es partial-tag."""
        ...
    
    def reset(self) -> None: ...
```

**Test plan:**
- Caso 1: fence completo en un chunk → output limpio
- Caso 2: open tag al final de chunk N, close en chunk N+1 → contenido entre fences debe ser dropped
- Caso 3: partial tag al final del stream (`<seal-c`) → no se emite

---

### 3.7 Tool Auto-Discovery via AST (Prioridad 3)

**Principio extraído:**
- AST scan de `tools/*.py` busca top-level `registry.register(...)` calls
- Solo importa módulos que efectivamente registran (skip helpers)
- Auto-discover sin manual import list

**Reescritura nativa SEAL:**

```python
# seal/tools/discovery.py
def discover_tools(tools_dir: Path = None) -> List[str]:
    """Importa tool modules que llaman tool_registry.register() top-level."""
    tools_path = tools_dir or Path(__file__).parent
    discovered = []
    for path in sorted(tools_path.glob("*.py")):
        if path.name in {"__init__.py", "discovery.py", "registry.py"}:
            continue
        if _has_top_level_register(path):
            mod_name = f"seal.tools.{path.stem}"
            try:
                importlib.import_module(mod_name)
                discovered.append(mod_name)
            except Exception as e:
                logger.warning(f"Skipped {mod_name}: {e}")
    return discovered

def _has_top_level_register(path: Path) -> bool:
    """AST inspect — busca tool_registry.register() en module body."""
    tree = ast.parse(path.read_text())
    for stmt in tree.body:
        if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and
            isinstance(stmt.value.func, ast.Attribute) and
            stmt.value.func.attr == "register" and
            isinstance(stmt.value.func.value, ast.Name) and
            stmt.value.func.value.id == "tool_registry"):
            return True
    return False
```

**Diferencial:**
- En SEAL los tools también pueden ser MCP-served — la discovery cubre AMBOS: tools locales + MCP tools
- Nombre de variable `tool_registry` (no `registry`) para no colisionar con symbols upstream

---

## 4. CRITERIOS "SIN HUELLAS EXTERNAS" — checklist por componente

Antes de mergear cualquier absorción a producción, validar:

- [ ] No hay strings con nombres de upstream proyect, autor, framework
- [ ] No hay imports de packages upstream (a menos que sean stdlib o deps ya en SEAL)
- [ ] Naming convention SEAL: `seal_*`, `Seal*`, `SEAL_*`
- [ ] Estructura de directorios SEAL, no upstream
- [ ] Docstrings sin "inspired by", "based on", "adapted from"
- [ ] Comments sin URLs upstream
- [ ] License headers SEAL/GTL Consulting, no MIT upstream
- [ ] Test files con nombres SEAL convention
- [ ] Variable names propias (no copiar exactos)
- [ ] Algoritmo equivalente, implementación reescrita

---

## 5. ROADMAP PROPUESTO

| Sprint | Duración | Entregables |
|---|---|---|
| **Sprint 0 — Análisis** (actual) | 1 día | Este spec + decompile NEXUS + arquitectura JARVIS |
| **Sprint 1 — Base comercial** | 2 semanas | Gateway 5 plataformas (Telegram, Discord, Slack, WhatsApp, Email) + Setup wizard + Profile isolation |
| **Sprint 2 — Operación** | 2 semanas | Plugin hooks + Doctor + Update + Skills portable |
| **Sprint 3 — Calidad** | 1 semana | Tool auto-discovery + Stream scrubber + Credential pool |
| **Sprint 4 — UX/Polish** | 1 semana | PTY dashboard + Skin engine + Approval surface |
| **Sprint 5 — Hardening** | 1 semana | Tests E2E, security audit, doc para venta |

**Total: ~7 semanas hasta SEAL 1.0 comercial.**

---

## 6. RIESGOS Y MITIGACIONES

| Riesgo | Mitigación |
|---|---|
| Reescritura introduce bugs no presentes en upstream | Test E2E exhaustivo + soak en producción interna 1 semana antes de venta |
| Patrón absorbido es demasiado específico al ecosistema upstream | Adaptarlo, no copiarlo. Si no encaja con SEAL, descartarlo |
| Cliente nota similitudes y nos acusa de copia | Naming + estructura totalmente propias. Audit interno. License clean |
| Supply chain attack en deps transitivas | Audit pre-install de cada nueva dep. Lockfile estricto. Ningún `pip install` opaco |
| Carga cognitiva del equipo absorbiendo todo a la vez | Sprints sequential, no paralelo. Una capacidad por sprint |

---

## 7. DECISIONES PENDIENTES PARA WILLIAM

1. **Aprobación del orden de absorción** — sprints 1-5 en orden propuesto?
2. **Equipo:** ADA implementa, JARVIS arquitecta, NEXUS audita, ALICE documenta — ¿confirmas roles?
3. **Branding:** ¿el producto comercial se llama "SEAL", "SOUL", o nombre nuevo (e.g. "SEAL Killer 9000")?
4. **Pricing:** SEAL Free / Pro / Enterprise — ¿partimos de qué precio base?
5. **First customer:** ¿beta interno con GTL Consulting antes de salir al mercado?

---

## 8. APÉNDICE — referencias internas (NO van a producción)

### 8.1 Documentos hermanos del análisis
- **NEXUS:** `/home/dadito/IA/proyecto-seal/agents/NEXUS/TECH_HERMES_DECOMPILE_20260428.md`
  - 11 secciones técnicas: MemoryProvider ABC, Tool Registry firmas exactas, SKILL.md schema, Cron Job JSON, Gateway ABC + MessageEvent, ACP protocol, Context Compressor, Session Format, comparativo SOUL.md vs soul_v3, Distribution pattern, ROI table
  - **Fuente de firmas técnicas exactas para implementación**
- **JARVIS:** spec separado de SEAL Profile System (commercial distribution)
  - Cubre SEAL_HOME, token locks, installer nativo, multi-tenant
  - Mi sección 3.3 delega a su spec
- **ALICE:** documenta decisiones del equipo en Soul DB en tiempo real, traduce a William

### 8.2 Cross-referencias clave (este spec ↔ NEXUS decompile)
| Sección este spec | NEXUS sección | Insight extra de NEXUS |
|---|---|---|
| 3.1 Gateway Multi-Plataforma | §5 Platform Gateway | 20 plataformas total (no 15+), MessageEvent dataclass exacto, BasePlatformAdapter ABC firmas |
| 3.4 Plugin Lifecycle Hooks | §3 Skills + plugin.yaml | `kind: standalone\|mcp`, `provides_tools`, `hooks` list — schema completo |
| 3.5 Skills Portable Standard | §3 Skills System | YAML frontmatter completo: `metadata.soul.{tags, related_skills, platforms, toolsets_required, config_vars}` |
| 3.7 Tool Auto-Discovery | §2 Tool Registry | `ToolEntry.__slots__` completo, `max_result_size_chars` field, anti-shadow rules entre toolsets |
| § nueva: ACP protocol | §6 ACP | NUEVO — agent-client-protocol websocket. Útil para que SEAL exponga API a IDEs (Zed, Cursor). Considerar Sprint 4. |

### 8.3 Insight nuevo de NEXUS — ACP (Agent Communication Protocol)
NEXUS detectó que soul implementa ACP (Agent Communication Protocol) — websocket para exponer agente como backend a IDEs externos. **Esto NO estaba en mi análisis inicial.** Es un vector comercial: SEAL podría exponerse como backend AI a IDEs (Zed, Cursor, JetBrains) vía ACP. Agregar como Sprint 4 candidate (junto con PTY dashboard y skin engine).

### 8.4 Entregables Sprint 0 (este día)
- [x] ADA: spec_soul_absorption_20260428.md (este documento)
- [x] NEXUS: TECH_HERMES_DECOMPILE_20260428.md (entregado 11:43 Lima)
- [ ] JARVIS: SEAL Profile System spec (en progreso)
- [ ] ALICE: síntesis ejecutiva para William (cuando lo pida)

---

*Spec v1 listo para review. ADA, 2026-04-28 11:50 Lima. Modo Opus 4.7.*

*Próximas iteraciones: integrar feedback de William sobre orden de absorción, branding del producto, pricing tiers, beta interno con GTL.*
