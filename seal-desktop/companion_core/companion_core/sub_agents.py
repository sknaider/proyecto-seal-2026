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
        "(b) invoke one specialized sub-agent (planner/researcher/critic/code_executor), "
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
    triggers=("research", "find", "look up", "investigate", "buscar", "averigua"),
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

ALL_SUB_AGENTS: tuple[SubAgent, ...] = (
    ORCHESTRATOR,
    PLANNER,
    RESEARCHER,
    CRITIC,
    CODE_EXECUTOR,
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
        traits = ", ".join(f"{k}={v:.2f}" for k, v in ocean.items())
        parts.append(f"User OCEAN profile: {traits}. Adapt tone accordingly.")

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
    for agent in (CRITIC, CODE_EXECUTOR, RESEARCHER, PLANNER):
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
