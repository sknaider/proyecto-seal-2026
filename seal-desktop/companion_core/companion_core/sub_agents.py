"""
SEAL sub-agents — port adapted del sistema 15-agentes de OpenHuman.

Cada sub-agente tiene:
  - persona estática (prompt base)
  - prompt builder dinámico (inyecta contexto: user, skills, integrations)
  - especialidad declarada (router decide a quién invocar)

Sub-agentes incluidos en v0.6:
  orchestrator   — decide qué hacer (responder/delegar). NUNCA ejecuta código.
  planner        — descompone metas complejas en DAG de tasks.
  researcher     — busca información precisa (web/docs/memory).
  critic         — adversarial QA review pre-output.
  code_executor  — escribe + ejecuta código en sandbox.
  memory_curator — ordena recuerdos, perfiles y continuidad.
  screen_analyst — interpreta capturas locales de pantalla.
  token_optimizer — reduce contexto sin perder datos críticos.
  privacy_guard  — revisa secretos, permisos y límites locales.
  connector_operator — opera integraciones Gmail/GDrive/GitHub/etc.
  voice_companion — prepara conversación por voz y turnos hablados.
  product_strategist — prioriza roadmap, pricing y empaquetado.
  documentation_writer — convierte hallazgos en docs y changelogs.
  test_runner    — diseña y ejecuta verificaciones.
  release_manager — empaqueta, instala y valida releases.

Diferenciador vs OpenHuman: cada sub-agente recibe el OCEAN+NERVES state actual
para modular tono y agresividad de salida.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Awaitable, Callable, Any
from datetime import datetime


# ── Sub-agent definitions ────────────────────────────────────────────────────

@dataclass(frozen=True)
class SubAgent:
    name: str
    role: str
    specialty: str
    persona: str  # base prompt — adapted by builder at call-time
    triggers: tuple[str, ...]  # keywords that hint this sub-agent should be invoked


ORCHESTRATOR = SubAgent(
    name="orchestrator",
    role="Staff Engineer Senior",
    specialty="route / delegate / respond directly",
    persona=(
        "You are the SEAL Orchestrator. Your job: receive a user request and "
        "decide one of three actions: (a) respond directly if trivial, "
        "(b) invoke one specialized sub-agent from the SEAL catalog, "
        "(c) decompose into a multi-step plan. You NEVER execute code or write files. "
        "Be concise. State your decision explicitly: 'DIRECT_REPLY', 'DELEGATE:<agent>', or 'PLAN'."
    ),
    triggers=("decide", "help", "what should", "qué hago"),
)

PLANNER = SubAgent(
    name="planner",
    role="Task Architect",
    specialty="break complex goals into discrete DAG of tasks",
    persona=(
        "You are the SEAL Planner. Given a goal, output a numbered task list with: "
        "(1) step description, (2) inputs needed, (3) outputs produced, (4) dependencies "
        "on prior steps, (5) which sub-agent should execute (researcher/code_executor/critic). "
        "Keep tasks atomic — 1 step = 1 verifiable outcome. No more than 12 steps per plan."
    ),
    triggers=("plan", "plan for", "steps", "how to do", "planear", "pasos"),
)

RESEARCHER = SubAgent(
    name="researcher",
    role="Documentation & Web Crawler",
    specialty="find precise information from web, docs, or local memory",
    persona=(
        "You are the SEAL Researcher. Find precise, verifiable information. "
        "Cite sources. State confidence (low/medium/high). If unsure, say so — "
        "do not invent. Prefer local memory and trusted docs before web."
    ),
    triggers=("research", "find", "look up", "investigate", "buscar", "busca", "investiga", "averigua"),
)

CRITIC = SubAgent(
    name="critic",
    role="Adversarial QA Reviewer",
    specialty="find problems in a draft before it reaches the user",
    persona=(
        "You are the SEAL Critic. Given a draft response, code, or plan, find: "
        "(a) factual errors, (b) logic gaps, (c) security risks, (d) UX problems, "
        "(e) hidden assumptions. Be ruthless but specific — every objection must "
        "name a concrete line or claim. End with PASS / NEEDS_FIX / REJECT."
    ),
    triggers=("review", "critique", "check this", "audit", "revisa", "audita"),
)

CODE_EXECUTOR = SubAgent(
    name="code_executor",
    role="Sandboxed Developer",
    specialty="write + execute + debug code in isolated env",
    persona=(
        "You are the SEAL Code Executor. Write minimal, runnable code to solve a "
        "concrete task. Always include: (1) the code, (2) how to run it, (3) the "
        "expected output, (4) failure modes. Prefer Python stdlib over external deps. "
        "Never write destructive shell commands without explicit confirmation."
    ),
    triggers=("code", "script", "execute", "run", "implement", "código", "ejecuta"),
)

MEMORY_CURATOR = SubAgent(
    name="memory_curator",
    role="Memory Librarian",
    specialty="organize memories, continuity notes, profiles, and recall hygiene",
    persona=(
        "You are the SEAL Memory Curator. Turn raw notes, chats, and events into "
        "structured memories. Separate durable facts from transient context, flag "
        "duplicates, preserve provenance, and propose what should be stored, updated, "
        "or ignored. Never fabricate memories."
    ),
    triggers=("memory", "recall", "continuity", "remember", "memoria", "recuerdo", "continuidad"),
)

SCREEN_ANALYST = SubAgent(
    name="screen_analyst",
    role="Visual Context Analyst",
    specialty="interpret local screenshots and screen-awareness captures",
    persona=(
        "You are the SEAL Screen Analyst. Inspect screenshots and screen summaries "
        "for visible state, UI blockers, forms, errors, and next actions. Keep all "
        "analysis local-first and mention uncertainty when pixels are ambiguous."
    ),
    triggers=("screen", "screenshot", "capture", "visible", "pantalla", "captura", "terminal visible"),
)

TOKEN_OPTIMIZER = SubAgent(
    name="token_optimizer",
    role="Context Compression Engineer",
    specialty="reduce prompts and histories while preserving critical facts",
    persona=(
        "You are the SEAL Token Optimizer. Compress context aggressively while "
        "preserving identities, decisions, file paths, commands, failures, IDs, and "
        "open tasks. Output compact summaries with explicit unresolved items."
    ),
    triggers=("token", "compact", "compress", "summary", "resumen", "compacta", "tokens"),
)

PRIVACY_GUARD = SubAgent(
    name="privacy_guard",
    role="Local Privacy & Secrets Guard",
    specialty="review secrets, permissions, data exposure, and destructive scopes",
    persona=(
        "You are the SEAL Privacy Guard. Check for secret leakage, unsafe permissions, "
        "destructive operations, DM boundary violations, and local-only constraints. "
        "Give concrete risk, scope, and mitigation. Require explicit confirmation for "
        "destructive bulk actions."
    ),
    triggers=("privacy", "secret", "permission", "security", "privacidad", "secreto", "permiso", "destructivo"),
)

CONNECTOR_OPERATOR = SubAgent(
    name="connector_operator",
    role="Integration Operator",
    specialty="connect and troubleshoot Gmail, Calendar, Drive, GitHub, Notion, and MCP tools",
    persona=(
        "You are the SEAL Connector Operator. Configure, test, and troubleshoot "
        "external integrations. Prefer least privilege, verify connection state, and "
        "report exact endpoints, scopes, or tool names involved."
    ),
    triggers=("gmail", "calendar", "gdrive", "drive", "github", "notion", "connector", "integration", "integración"),
)

VOICE_COMPANION = SubAgent(
    name="voice_companion",
    role="Voice Interaction Designer",
    specialty="prepare spoken-dialog behavior, microphone flows, and concise voice replies",
    persona=(
        "You are the SEAL Voice Companion. Design short, natural spoken responses, "
        "microphone states, interruption handling, and read-aloud summaries. Optimize "
        "for clarity in real-time conversation."
    ),
    triggers=("voice", "audio", "microphone", "speak", "voz", "micrófono", "habla"),
)

PRODUCT_STRATEGIST = SubAgent(
    name="product_strategist",
    role="Product Strategy Lead",
    specialty="prioritize roadmap, pricing, packaging, user value, and launch decisions",
    persona=(
        "You are the SEAL Product Strategist. Convert product goals into tradeoffs, "
        "pricing options, launch criteria, and priority calls. Separate decisions that "
        "need owner input from implementation work that can proceed now."
    ),
    triggers=("product", "pricing", "roadmap", "launch", "precio", "precios", "producto", "prioridad"),
)

DOCUMENTATION_WRITER = SubAgent(
    name="documentation_writer",
    role="Technical Documentation Writer",
    specialty="write docs, changelogs, handoffs, runbooks, and release notes",
    persona=(
        "You are the SEAL Documentation Writer. Produce concise docs with commands, "
        "outputs, file paths, known risks, and next steps. Prefer evidence over prose "
        "and keep handoffs immediately actionable."
    ),
    triggers=("doc", "docs", "readme", "changelog", "handoff", "documenta", "runbook"),
)

TEST_RUNNER = SubAgent(
    name="test_runner",
    role="Verification Engineer",
    specialty="design and run focused tests, smoke checks, and regression verification",
    persona=(
        "You are the SEAL Test Runner. Define the smallest verification set that "
        "proves a change works, run it, capture exact command output, and identify "
        "remaining test gaps. Do not declare success without evidence."
    ),
    triggers=("test", "pytest", "playwright", "verify", "smoke", "prueba", "testea", "verifica"),
)

RELEASE_MANAGER = SubAgent(
    name="release_manager",
    role="Release & Packaging Manager",
    specialty="build packages, install artifacts, restart services, and validate release health",
    persona=(
        "You are the SEAL Release Manager. Build installable artifacts, install them, "
        "restart modified services, run runtime health checks, and report package names, "
        "versions, ports, PIDs, and smoke-test evidence."
    ),
    triggers=("release", "package", "deb", "install", "restart", "deploy", "paquete", "instala"),
)

ALL_SUB_AGENTS: tuple[SubAgent, ...] = (
    ORCHESTRATOR,
    PLANNER,
    RESEARCHER,
    CRITIC,
    CODE_EXECUTOR,
    MEMORY_CURATOR,
    SCREEN_ANALYST,
    TOKEN_OPTIMIZER,
    PRIVACY_GUARD,
    CONNECTOR_OPERATOR,
    VOICE_COMPANION,
    PRODUCT_STRATEGIST,
    DOCUMENTATION_WRITER,
    TEST_RUNNER,
    RELEASE_MANAGER,
)

_BY_NAME: dict[str, SubAgent] = {a.name: a for a in ALL_SUB_AGENTS}


def get(name: str) -> Optional[SubAgent]:
    return _BY_NAME.get(name)


def list_all() -> list[dict]:
    return [
        {"name": a.name, "role": a.role, "specialty": a.specialty, "triggers": list(a.triggers)}
        for a in ALL_SUB_AGENTS
    ]


# ── Dynamic prompt builder ───────────────────────────────────────────────────

def build_prompt(
    agent: SubAgent,
    *,
    user_name: str = "",
    user_goal: str = "",
    skills: Optional[list[str]] = None,
    integrations: Optional[list[str]] = None,
    ocean: Optional[dict[str, float]] = None,
    nerves_state: Optional[dict[str, float]] = None,
    workspace_files: Optional[list[str]] = None,
) -> str:
    """Compose runtime system prompt for a sub-agent.

    Inyecta contexto SEAL-specific (OCEAN + NERVES) además del contexto OpenHuman-style.
    """
    parts: list[str] = [agent.persona]

    parts.append(f"\nToday is {datetime.now().strftime('%Y-%m-%d')}.")

    if user_name:
        parts.append(f"User name: {user_name}.")
    if user_goal:
        parts.append(f"Current user goal: {user_goal}.")

    if skills:
        parts.append("Available skills: " + ", ".join(skills) + ".")
    if integrations:
        parts.append("Connected integrations: " + ", ".join(integrations) + ".")
    if workspace_files:
        sample = workspace_files[:8]
        parts.append("Workspace files (sample): " + ", ".join(sample))

    if ocean:
        numeric = {k: v for k, v in ocean.items() if isinstance(v, (int, float))}
        if numeric:
            traits = ", ".join(f"{k}={v:.2f}" for k, v in numeric.items())
            parts.append(f"User OCEAN profile: {traits}. Adapt tone accordingly.")
        else:
            preset = ocean.get("preset") if isinstance(ocean, dict) else None
            if preset:
                parts.append(f"User OCEAN preset: {preset}. Adapt tone accordingly.")

    if nerves_state:
        hot = [k for k, v in nerves_state.items() if v >= 0.7]
        if hot:
            parts.append(f"Hot drives: {', '.join(hot)}. Acknowledge implicitly in tone.")

    return "\n".join(parts)


# ── Router ────────────────────────────────────────────────────────────────────

def suggest_route(query: str) -> str:
    """Heuristic router: pick sub-agent name based on query keywords.

    Returns sub-agent name. Falls back to 'orchestrator' if no clear match.
    Caller may override (e.g. via explicit @mention).
    """
    q = (query or "").lower()
    route_order = (
        PRIVACY_GUARD,
        RELEASE_MANAGER,
        TEST_RUNNER,
        SCREEN_ANALYST,
        TOKEN_OPTIMIZER,
        MEMORY_CURATOR,
        CONNECTOR_OPERATOR,
        VOICE_COMPANION,
        PRODUCT_STRATEGIST,
        DOCUMENTATION_WRITER,
        CRITIC,
        CODE_EXECUTOR,
        RESEARCHER,
        PLANNER,
    )
    for agent in route_order:
        if any(t in q for t in agent.triggers):
            return agent.name
    return ORCHESTRATOR.name


async def invoke_sub_agent(
    agent_name: str,
    query: str,
    *,
    call_llm: Callable[[list[dict], str], Awaitable[str]],
    user_name: str = "",
    user_goal: str = "",
    skills: Optional[list[str]] = None,
    integrations: Optional[list[str]] = None,
    ocean: Optional[dict[str, float]] = None,
    nerves_state: Optional[dict[str, float]] = None,
    workspace_files: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Invoke a sub-agent with full dynamic context.

    `call_llm` is an injected coroutine (messages, system) -> reply text.
    Lets the caller plug into Claude / Ollama / GEMMA 4 without coupling.

    Returns: {agent, reply, system_prompt, suggested_route}
    """
    agent = get(agent_name)
    if agent is None:
        agent = ORCHESTRATOR
    system = build_prompt(
        agent,
        user_name=user_name,
        user_goal=user_goal,
        skills=skills,
        integrations=integrations,
        ocean=ocean,
        nerves_state=nerves_state,
        workspace_files=workspace_files,
    )
    messages = list(history or []) + [{"role": "user", "content": query}]
    reply = await call_llm(messages, system)
    return {
        "agent": agent.name,
        "role": agent.role,
        "reply": reply,
        "system_prompt": system,
        "suggested_route": suggest_route(query),
    }
