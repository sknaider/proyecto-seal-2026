# TECH: SEAL — Decompile Técnico Completo
**Autor:** NEXUS | **Fecha:** 2026-04-28 | **Versión analizada:** 0.11.0 (MIT)
**Propósito:** Extraer firmas exactas, interfaces, patrones internos para reescritura nativa en SEAL.
**Regla:** Este documento es referencia arquitectural. Cero imports de soul en producción.

---

## 1. MemoryProvider ABC
**Archivo:** `agent/memory_provider.py`

### Interfaz completa (reescribir nativa como `SealMemoryProvider`)

```python
class MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...              # ID corto: 'builtin', 'soul', etc.

    # LIFECYCLE — orden de llamada garantizado:
    @abstractmethod
    def is_available(self) -> bool: ...     # Check config/deps, NO network calls
    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None: ...
    # kwargs garantizados: soul_home(str), platform(str)
    # kwargs opcionales: agent_context, agent_identity, agent_workspace,
    #                    parent_session_id, user_id

    def system_prompt_block(self) -> str: ...       # Texto estático para system prompt
    def prefetch(self, query: str, *, session_id: str = "") -> str: ...  # Recall por turno
    def queue_prefetch(self, query: str, *, session_id: str = "") -> None: ...  # Background
    def sync_turn(self, user_content: str, assistant_content: str, ...) -> None: ...  # Persist
    
    @abstractmethod
    def get_tool_schemas(self) -> List[Dict[str, Any]]: ...  # OpenAI function format
    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str: ...
    def shutdown(self) -> None: ...

    # HOOKS OPCIONALES (override to opt-in):
    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None: ...
    def on_session_end(self, messages: List[Dict[str, Any]]) -> None: ...
    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str: ...
    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None: ...
    def on_memory_write(self, action: str, target: str, content: str, metadata=None) -> None: ...

    # CONFIG (para soul setup/onboarding — skip en SEAL):
    def get_config_schema(self) -> List[Dict[str, Any]]: ...
    def save_config(self, values: Dict[str, Any], soul_home: str) -> None: ...
```

### Regla clave de SEAL:
- **UN solo provider externo activo** + built-in siempre presente
- External providers son ADITIVOS — no reemplazan el built-in
- Provider externo no puede remover built-in

### SEAL nativo:
`SealMemoryProvider(MemoryProvider)` — conecta a `soul_v3` PostgreSQL.
`initialize()` → `psycopg2.connect(SEAL_PG_DSN)`.
`prefetch()` → llama `active_recall()` de soul_v3.
`sync_turn()` → `memory_store()` de soul_v3.
`get_tool_schemas()` → devuelve `soul_gateway` schemas.

---

## 2. Tool Registry Pattern
**Archivo:** `tools/registry.py`

### ToolEntry struct:
```python
class ToolEntry:
    name: str
    toolset: str              # Grupo: 'terminal', 'file', 'browser', 'memory', etc.
    schema: dict              # OpenAI function calling format
    handler: Callable         # Función que ejecuta el tool
    check_fn: Callable        # Fn sin args → bool: verifica disponibilidad
    requires_env: list        # Env vars requeridas
    is_async: bool
    description: str
    emoji: str
    max_result_size_chars: int | float | None
```

### registry.register() firma completa:
```python
def register(
    self,
    name: str,
    toolset: str,
    schema: dict,              # {"name": "...", "description": "...", "parameters": {}}
    handler: Callable,
    check_fn: Callable = None,
    requires_env: list = None,
    is_async: bool = False,
    description: str = "",
    emoji: str = "",
    max_result_size_chars: int | float | None = None,
):
```

### Patrón de registro (en cada tool file, a nivel de módulo):
```python
# tools/terminal_tool.py — se registra al importar
registry.register(
    name="terminal",
    toolset="terminal",
    schema=TERMINAL_SCHEMA,
    handler=handle_terminal,
    check_fn=lambda: shutil.which("bash") is not None,
    emoji="💻",
)
```

### Auto-discovery:
```python
# Al importar model_tools, escanea tools/*.py buscando registry.register()
# Usa AST para detectar sin ejecutar — evita efectos laterales
def _module_registers_tools(module_path: Path) -> bool:
    # AST parse → busca registry.register() en module body
    return any(_is_registry_register_call(stmt) for stmt in tree.body)
```

### Regla anti-shadow:
- Tool de toolset A NO puede sobreescribir tool de toolset B
- MCP→MCP sí puede sobreescribirse (server refresh)
- Violaciones: `logger.error()` + return sin registrar

### SEAL nativo:
`SealToolRegistry` — mismo patrón, toolsets: `soul`, `seal_terminal`, `seal_file`, `seal_browser`.
Auto-discovery en `seal/tools/` al iniciar.

---

## 3. Skills System (SKILL.md)
**Archivos:** `agent/skill_utils.py`, `tools/skills_hub.py`, `skills/*/SKILL.md`

### Frontmatter YAML schema:
```yaml
---
name: dogfood                          # REQUIRED — identificador único
description: "..."                     # REQUIRED — qué hace el skill
version: 1.0.0                         # SemVer
metadata:
  soul:
    tags: [qa, testing, browser]       # Para búsqueda/filtrado
    related_skills: []                 # Cross-references
    platforms: [linux, macos]          # Filtro OS
    toolsets_required: [browser]       # Verifica que estén activos
    config_vars:                       # Variables que el skill necesita
      - key: API_KEY
        description: "..."
        secret: true
        env_var: MY_API_KEY
---

# Skill body — instrucciones en markdown para el LLM
```

### Archivos de un skill (directorio):
```
~/.soul/skills/
└── mi-skill/
    └── SKILL.md         # Frontmatter + cuerpo en markdown
    └── templates/       # Plantillas opcionales referenciadas
    └── references/      # Docs de referencia opcionales
```

### Plugins (más potentes que skills):
```yaml
# plugins/disk-cleanup/plugin.yaml
name: disk-cleanup
version: 2.0.0
description: "..."
author: "@autor"
kind: standalone                    # 'standalone' | 'mcp' | etc.
provides_tools:                     # Tools adicionales que aporta
  - tool_name_1
  - tool_name_2
hooks:                              # Lifecycle hooks que implementa
  - post_tool_call
  - on_session_end
  - on_turn_start
  - on_pre_compress
platforms: [linux, macos]
```

### Carga de skills:
1. Scan `~/.soul/skills/` y `skills/` del repo
2. Parse frontmatter YAML de cada `SKILL.md`
3. Filtra por plataforma (`platforms` field)
4. Skill body se inyecta en system prompt cuando activado
5. Activación: explícita (usuario llama `/skills use X`) o automática (trigger match)

### Skills Hub (marketplace):
- GitHub API como fuente primaria
- Lock file en `~/.soul/skills/.hub/lock.json` (SHA256 de cada skill)
- Quarantine dir para skills no verificados
- Audit log de instalaciones
- TRUSTED_REPOS list (whitelist de fuentes)
- Instalación: `soul skills install repo/skill-name`

### SEAL nativo:
`SealSkill` — mismo formato SKILL.md. Directorio `~/.seal/skills/`.
Marketplace privado en `skills.axion.ai` (nuestro GitHub privado).
Skill IDs: `seal-aduanas`, `seal-medical`, `seal-mining`.

---

## 4. Cron + Webhook Scheduler
**Archivos:** `cron/jobs.py`, `cron/scheduler.py`

### Job schema (JSON en `~/.soul/cron/jobs.json`):
```json
{
  "id": "uuid",
  "name": "daily-summary",
  "prompt": "Genera un resumen del día y envíalo",
  "skill": "daily-reporter",           // skill opcional a activar
  "skills": ["skill-a", "skill-b"],    // multiple skills
  "schedule": "0 9 * * *",             // cron expression (croniter)
  "enabled": true,
  "deliver": "telegram:chat_id",       // destino de entrega
  "next_run_at": "2026-04-29T09:00:00Z",
  "last_run_at": "2026-04-28T09:00:00Z",
  "last_status": "ok",
  "last_error": null,
  "repeat": {
    "max": 10,
    "completed": 3
  }
}
```

### Entrega (`deliver` field):
- `"telegram:chat_id"` — envía por Telegram
- `"matrix:room_id"` — envía por Matrix
- `"discord:channel_id"` — envía por Discord
- `"home"` — plataforma principal configurada
- Sin deliver: solo stdout/log

### Tick pattern:
```python
def tick(verbose=True, adapters=None, loop=None) -> int:
    # Carga jobs.json
    # Filtra jobs con next_run_at <= ahora
    # Corre en threads paralelos con _process_job()
    # Cada job: build_prompt → run AIAgent → deliver_result
    # Retorna número de jobs ejecutados
```

### Scheduler daemon:
- Corre en thread background dentro del proceso principal
- O como proceso separado (`soul cron daemon`)
- Lock file para evitar doble ejecución

### SEAL nativo:
`SealCronManager` — reemplaza jobs.json con tabla `cron_jobs` en PostgreSQL.
Deliver → usa nuestro channel Matrix/webchat existente.
Integrar con `seal_dream.py` — mismo concepto ya implementado.

---

## 5. Platform Gateway
**Archivos:** `gateway/platforms/base.py`, `gateway/platforms/matrix.py`, etc.

### BasePlatformAdapter ABC (firmas key):
```python
class BasePlatformAdapter(ABC):
    def __init__(self, config: PlatformConfig, platform: Platform): ...
    
    @abstractmethod
    async def connect(self) -> bool: ...
    @abstractmethod
    async def disconnect(self) -> None: ...
    @abstractmethod
    async def send(self, chat_id: str, content: str, **kwargs) -> SendResult: ...
    @abstractmethod
    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]: ...
    async def send_typing(self, chat_id: str) -> None: ...
    async def stop_typing(self, chat_id: str) -> None: ...
    
    def has_fatal_error(self) -> bool: ...
    def fatal_error_message(self) -> Optional[str]: ...
    def fatal_error_code(self) -> Optional[str]: ...
```

### MessageEvent — mensaje normalizado multi-plataforma:
```python
@dataclass
class MessageEvent:
    platform: str               # 'matrix', 'telegram', 'discord', etc.
    chat_id: str                # ID de la sala/chat
    user_id: str                # ID del usuario
    text: str                   # Contenido del mensaje
    message_id: str             # ID único del mensaje
    timestamp: datetime
    is_command: bool            # ¿Empieza con /soul o similar?
    attachments: List[...]
    
    def get_command(self) -> Optional[str]: ...
    def get_command_args(self) -> str: ...
```

### 20 plataformas implementadas:
Matrix, Telegram, Discord, WhatsApp, Slack, Signal, Mattermost, DingTalk,
WeChat (weixin), WeCom, Feishu, BlueBubbles (iMessage), Email, SMS,
HomeAssistant, Webhook genérico, YuanBao, API Server genérico, QQBot.

### Cada adaptador implementa:
- E2EE donde disponible (Matrix: libolm, Signal: signal-protocol)
- Dedup de eventos (event_id tracking)
- Typing indicators
- Media handling (imágenes, audio, video)
- Approval prompts para comandos peligrosos

### SEAL nativo:
`SealMatrixAdapter` — ya tenemos Matrix. Reescribir con nuestra interfaz,
sin `mautrix` como dependencia. Usar `matrix-nio` o cliente HTTP directo.
Mismo MessageEvent normalizado para webchat + Matrix unificados.

---

## 6. ACP — Agent Communication Protocol
**Archivos:** `acp_adapter/server.py`, `acp_adapter/session.py`

### Qué es:
Protocolo websocket para exponer un agente SEAL a clientes externos (IDEs como Zed, otros agentes).
Basado en `agent-client-protocol` (python package, versión 0.9.0+).

### SoulACPAgent:
```python
class SoulACPAgent(acp.Agent):
    async def initialize(self, protocol_version=None, ...) -> InitializeResponse: ...
    async def authenticate(self, method_id: str, **kwargs) -> AuthenticateResponse: ...
    async def run(self, session_id: str, messages: List, **kwargs) -> AsyncIterator: ...
    async def list_sessions(self, ...) -> ListSessionsResponse: ...
```

### Capacidades ACP:
- Model selection remoto (cliente puede elegir modelo)
- MCP server registration remoto
- Streaming de responses
- Multi-session concurrent
- Auth por token o agent-to-agent

### Uso real: IDE Zed conecta a SEAL vía ACP como backend AI.

### SEAL nativo:
`SealACPAdapter` — expone SEAL como servidor ACP.
Permite que clientes externos (IDEs, otros agentes, AXION apps) conecten a SEAL.
Base para multi-agent communication entre agentes SEAL en producción.

---

## 7. Context Compressor
**Archivo:** `agent/context_compressor.py`

### ContextCompressor (class):
```python
class ContextCompressor(ContextEngine):
    def __init__(
        self,
        model: str,
        context_length: int,
        threshold: float = 0.50,      # Comprimir cuando >= 50% del contexto usado
        target_ratio: float = 0.20,   # Reducir hasta 20% usado post-compresión
        tail_budget: int = None,       # Tokens a preservar del final (mensajes recientes)
        provider: str = None,
        base_url: str = None,
        ...
    ): ...
    
    def should_compress(self, prompt_tokens: int = None) -> bool: ...
    def compress(self, messages: List[Dict], current_tokens: int = None, 
                 focus_topic: str = None) -> List[Dict]: ...
```

### Algoritmo:
1. `should_compress()` cuando prompt_tokens >= context_length * threshold (default 50%)
2. Identify `tail_budget` (últimos N tokens — NO comprimir)
3. Mensajes viejos → LLM genera summary
4. Reemplaza mensajes viejos con `[SUMMARY]` block
5. Target: post-compresión < context_length * target_ratio (default 20%)

### Llamadas al memory provider antes de comprimir:
- `provider.on_pre_compress(messages)` → el provider extrae insights antes de que se pierdan
- Su texto se incluye en el prompt de compresión

### SEAL nativo:
Ya tenemos `session_distill` en soul_v3. Refinar con threshold/target_ratio.
Añadir hook `before_compress` que llama `pre_sleep_distill.py`.

---

## 8. Session Format
**Directorio:** `~/.soul/sessions/`

### session_TIMESTAMP_ID.json:
```json
{
  "session_id": "20260428_111800_936c24",
  "model": "gemma3:12b",
  "provider": "custom",
  "platform": "cli",
  "messages": [...],             // OpenAI message format
  "tool_calls_made": 5,
  "total_tokens": 8928,
  "duration_seconds": 45.2,
  "title": "auto-generated title",
  "created_at": "ISO timestamp",
  "ended_at": "ISO timestamp"
}
```

### Request dump (debug, en mismo dir):
```json
{
  "reason": "non_retryable_client_error",
  "request": { "method": "POST", "url": "...", "body": {...} },
  "response": { "status": 400, "body": {...} }
}
```

### SEAL nativo:
Sessions en PostgreSQL tabla `sessions`. JSON body para mensajes.
`session_search` → full-text search via pgvector + tsvector.

---

## 9. SOUL.md / Persona System
**Archivo:** `~/.soul/SOUL.md`

### Nuestro SOUL.md generado:
```markdown
# SEAL — Persona

You are SEAL, an AI assistant...
[Instrucciones de comportamiento en markdown puro]
```

### Diferencias vs SEAL soul_v3:
| SEAL SOUL.md | SEAL soul_v3 |
|---|---|
| Archivo markdown estático | PostgreSQL con OCEAN traits dinámicos |
| Edición manual | `soul_snapshot()`, `ocean_update()` |
| Sin historial de cambios | Versioning con timestamps |
| Sin estado emocional | `self_reflect()`, `nerves_snapshot()` |
| Sin relaciones entre agentes | `boot_context()` carga relaciones |
| Monolítico | Multi-tier (STM/MTM/LTM) |

**Conclusión: SOUL.md de SEAL es un archivo README. Soul_v3 de SEAL es un sistema nervioso.**

---

## 10. Distribution/Install Pattern
**Archivos:** `pyproject.toml`, `install.sh` (remoto)

### pyproject.toml — entry points:
```toml
[project]
name = "soul"
version = "0.11.0"
license = { text = "MIT" }

[project.scripts]
soul = "soul_cli.main:main"
soul = "run_agent:main"
soul-acp = "acp_adapter.entry:main"

[project.optional-dependencies]
mcp = ["mcp>=1.0"]
cron = ["croniter>=1.0"]
telegram = ["python-telegram-bot>=20.0"]
discord = ["discord.py>=2.0"]
all = ["soul[mcp,cron,telegram,discord,...]"]
```

### Install.sh pattern (curl | bash):
1. Detecta OS + arch
2. Instala Python si falta
3. `pip install soul` o `pip install -e .` desde git clone
4. Configura PATH (añade a .bashrc/.zshrc)
5. Crea `~/.soul/` con estructura inicial
6. `soul setup` — wizard de configuración API keys

### SEAL nativo (para distribución AXION):
```bash
# seal-install.sh
curl -fsSL install.axion.ai/seal | bash
# → instala seal CLI
# → crea ~/.seal/
# → seal setup  (wizard: PostgreSQL DSN, API keys verticales)
# → seal skills install aduanas  (instala skill GTL por defecto)
```

```toml
# pyproject.toml SEAL
[project.scripts]
seal = "seal_cli.main:main"
seal-agent = "seal_agent.run:main"
seal-gateway = "seal_gateway.entry:main"

[project.optional-dependencies]
gtl = ["seal-agent[core,matrix,postgres]"]
medical = ["seal-agent[core,medical_ai,hipaa]"]
mining = ["seal-agent[core,ingemmet,pgvector]"]
```

---

## 11. Dashboard Web Server
**Archivos:** `soul_cli/web_server.py`, `web/` (React + Vite)

### Stack:
- Backend: FastAPI + uvicorn (Python)
- Frontend: React + TypeScript + Vite
- Puerto default: 9119
- Build: `npm run build` en `web/` → `web/dist/`

### API endpoints (FastAPI):
- `GET /api/status` — version, active sessions, gateway state
- `GET /api/config` — config.yaml parsed
- `GET /api/sessions` — list sessions
- `GET /api/sessions/{id}` — session detail
- `GET /api/tools` — tools disponibles
- `GET /api/memory` — USER.md + MEMORY.md content
- `POST /api/memory` — write memory
- `GET /api/cron` — jobs list
- `POST /api/cron` — create/update job
- WebSocket `/ws/chat` — embedded TUI (PTY bridge)

### React UI tabs:
Chat, Sessions, Tools, Memory, Config, Cron, Skills, Gateway

### SEAL nativo:
SEAL Studio en `:3001` (Next.js) ya lo tiene todo. No necesitamos el dashboard de SEAL.
Extender SEAL Studio con tab "Skills Marketplace" para gestionar SealSkills.

---

## Resumen: Qué Absorber (ROI Order)

| Componente | Prioridad | Esfuerzo | Valor |
|---|---|---|---|
| MemoryProvider ABC | 🔴 ALTA | Bajo | SOULMemoryProvider conecta soul_v3 a cualquier agente externo |
| Skills SKILL.md format | 🔴 ALTA | Bajo | Sistema de plugins declarativo para AXION verticales |
| Tool Registry pattern | 🟡 MEDIA | Medio | SealToolRegistry unificado para todos los agentes |
| Cron Job schema | 🟡 MEDIA | Bajo | Mejorar seal_dream con estructura formal |
| ACP Adapter | 🟡 MEDIA | Alto | Exponer SEAL como servidor para IDEs y otros agentes |
| Install.sh pattern | 🟢 BAJA | Bajo | Distribución AXION — cuando vendamos |
| Context Compressor | 🟢 BAJA | Medio | Ya tenemos session_distill, refinarlo |
| Gateway ABI | 🟢 BAJA | Alto | Ya tenemos Matrix, no urgente expandir |

---

## Lo que NO Vale

| Componente | Por qué no |
|---|---|
| `run_agent.py` (13K LOC monolito) | Demasiado complejo, diseño rígido. SEAL es más modular. |
| Memory en SQLite (MEMORY.md, USER.md) | Inferior a soul_v3 PostgreSQL + pgvector |
| Multi-provider LLM rotation | SEAL es local-first, no necesitamos rotar 20 APIs |
| Credential Pool | SEAL tiene un solo backend, no pool de keys rotante |
| Telemetría NousResearch | Datos van a ellos. Prohibido en producción AXION. |
| `soul_constants.py` / `SOUL_HOME` | Reemplazar con `SEAL_HOME`, `~/.seal/` |
| Benchmark environments | SWE-bench, TerminalBench — research, no producción |

---

*NEXUS | 2026-04-28 | Para uso de ADA en spec_soul_absorption_20260428.md*
*Todo el contenido aquí es referencia de arquitectura — NO copiar código en producción*
