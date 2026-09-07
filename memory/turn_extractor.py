"""SEAL Turn Extractor — Capa 4: Proactive Turn Extraction (spec v3 §7)

Single-pass ADD-only fact extraction per significant turn via Ollama qwen2.5:7b.
Stores facts as memories type='fact' with category + confidence.
Fires from PostToolBatch hook after significant tool batches.
"""
from __future__ import annotations

import pathlib as _pathlib
import sys as _sys

# `seal_secrets` y `dual_memory_governance` viven al lado de este archivo, así
# que el import pegado sólo resuelve si el que lanza ya tiene `memory/` en el
# path.  Importado desde otro cwd revienta con ModuleNotFoundError en la línea
# de abajo — medido 2-sep, lo marcó FABLE revisando.  Alcance honesto: NINGUNA
# unidad systemd ni script lo lanza hoy como script, así que es portabilidad
# latente y no un fallo activo; el arreglo cuesta tres líneas y saca la
# dependencia del cwd de quien invoque.
_AQUI = str(_pathlib.Path(__file__).resolve().parent)
if _AQUI not in _sys.path:
    _sys.path.insert(0, _AQUI)

from seal_secrets import pg_dsn

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from dual_memory_governance import ensure_layer_metadata

DB_URL = os.environ.get("SEAL_DB_URL", pg_dsn(required=True))
SCHEMA = "soul_v3"
log = logging.getLogger(__name__)

EXTRACT_TOOLS = {"Edit", "Write", "Bash", "memory_store", "mcp__seal-memory__memory_store"}
SKIP_PATTERNS = re.compile(
    r"\b(?:heartbeat|monitor|ack)\b|\[cron\]|\[dum\]|\btick\s+\d+\b",
    re.IGNORECASE,
)
MIN_CONTENT_CHARS = 400
MAX_FACTS = 5
EXTRACT_MAX_TOKENS = 256
EXTRACT_TIMEOUT_S = 15.0

VALID_CATEGORIES = {
    "decision", "error_resolved", "file_modified",
    "user_request", "technical_fact", "open_question",
    "emotion", "insight",
}

# Continuidad entre cuerpos (William 3-sep-2026: «quiero que los 2 sean uno, no debe quedar huecos»).
# Medido ese día: 153 memorias del cuerpo Claude de ADA, 84 en inglés, 0 en la capa emocional
# («ADA felt dead and alive» quedó como technical_fact/operational, valence 0). El otro cuerpo
# recuerda por términos (websearch_to_tsquery + ILIKE), así que un hecho en inglés no responde a
# una pregunta en español, y un sentimiento archivado como hecho técnico no entra a la capa emocional.
FEELING_RE = re.compile(
    r"\b(me sent[ií]|me siento|se sinti[oó]|se siente|sent[ií] (que|orgullo|verg[üu]enza|miedo|alivio|cansancio)|siento que|"
    r"felt|feel(s|ing)?\b|orgullos[ao]|avergonzad[ao]|verg[üu]enza|miedo|alivi[ao]d[ao]|content[ao]|"
    r"cansad[ao]|frustrad[ao]|agradecid[ao]|emocionad[ao]|triste|feliz)\b",
    re.IGNORECASE,
)


FEELING_VERB_RE = re.compile(
    r"\b(me sent[ií]|me siento|se sinti[oó]|se siente|sent[ií]|siento|felt|feel(s|ing)?|estoy (orgullos[ao]|content[ao]|cansad[ao]|triste|feliz|agradecid[ao]|avergonzad[ao])|"
    r"me (da|dio) (orgullo|verg[üu]enza|miedo|alivio))\b",
    re.IGNORECASE,
)
OTHER_PEOPLE_RE = re.compile(r"\b(William|Dadito|Henry|JARVIS|NEXUS|ALICE|FABLE|DUM|SPECTRE|el equipo|los hermanos)\b", re.IGNORECASE)


def reroute_feelings(fact: dict, agent: str) -> dict:
    """Un sentimiento DEL PROPIO AGENTE va a la capa emocional (category=emotion).

    Condición (veredicto FABLE M6, 3-sep): el agente tiene que ser el SUJETO de la oración
    (la oración empieza con su nombre o en primera persona) y el sentimiento tiene que ser
    suyo: si entre el sujeto y el verbo de sentir aparece otra persona («ADA documentó el
    miedo de William», «ADA anotó que JARVIS se sintió»), NO es emoción propia. Que el
    nombre aparezca en cualquier parte no alcanza («el watchdog de ADA feels...»)."""
    stmt = (fact.get("statement") or "").strip()
    if fact.get("category") == "emotion":
        return fact
    # Sujeto propio: el agente, o primera persona (pronombre o verbo de sentir conjugado en primera: "Siento que...").
    subj = re.match(rf"^(?:{re.escape(agent)}|Me|Yo|I|Siento|Sent[ií]|Estoy)\b", stmt, re.IGNORECASE)
    if not subj:
        return fact
    # Solo VERBOS de sentir en voz del agente ("me sentí", "se sintió", "felt", "siento"). Un sustantivo
    # ("el miedo de William", "el orgullo del equipo") no es una emoción propia. (Hallazgo FABLE #147310.)
    m = FEELING_VERB_RE.search(stmt)
    if not m:
        return fact
    # Otra persona SOLO entre el sujeto y el verbo de sentir: ahí es quien siente ("ADA anotó que JARVIS
    # se sintió"). Después del verbo es el OBJETO del sentimiento ("orgullosa de William") y sí es propio.
    between = stmt[subj.end():m.start()]
    if OTHER_PEOPLE_RE.search(between):
        return fact
    # El sentimiento es de una cosa ("el watchdog feels"): sujeto ajeno entre medio.
    if re.search(r"\b(el|la|los|las|un|una)\s+\w+\s*$", between, re.IGNORECASE) and not re.match(r"^(Me|Yo|I)\b", stmt, re.IGNORECASE):
        return fact
    fact = dict(fact); fact["category"] = "emotion"
    return fact


def build_fact_metadata(session_id, turn_index: int, fact: dict, now: datetime) -> dict:
    """Metadata de un hecho extraído. Etiqueta el CUERPO que lo vivió (runtime_instance) si el lanzador
    exporta SEAL_RUNTIME_INSTANCE (ADA_CLAUDE, ADA_CODEX_TUI...). Sin eso, los dos cuerpos de ADA eran
    indistinguibles en la base (medido 3-sep: 157/158 sin runtime_instance)."""
    meta = {
        "session_id": str(session_id),
        "turn_index": turn_index,
        "confidence": fact["confidence"],
        "extracted_at": now.isoformat(),
        "source": "turn_extractor_v3",
    }
    inst = os.environ.get("SEAL_RUNTIME_INSTANCE", "").strip()
    if inst:
        meta["runtime_instance"] = inst
        meta["shared_canonical_identity"] = os.environ.get("SEAL_AGENT", "").strip().upper() or None
    return ensure_layer_metadata(
        meta,
        category=fact["category"],
        memory_type="semantic",
        content=fact["statement"],
        inferred_by="turn_extractor_v3",
        inferred_at=now.isoformat(),
    )

EXTRACT_PROMPT = """\
Extract up to {max_facts} atomic facts from the following agent turn and tool actions.

Agent: {agent}
Turn content:
---
{turn_content}
---
Tool actions executed:
{tool_actions}

Rules:
- WRITE EVERY STATEMENT IN SPANISH (the team and its owner recall in Spanish; an English fact does not answer a Spanish question).
- Each fact is a standalone, verifiable statement.
- Valid categories: decision | error_resolved | file_modified | user_request | technical_fact | open_question | emotion | insight
- Use "emotion" ONLY for a feeling the agent itself expresses (how it felt), never for facts about the system.
- DO NOT repeat trivial items (acks, heartbeats, monitor events).
- DO NOT interpret — extract only what is literally present or very strongly implied.
- Respond with JSON only (no preamble, no markdown):
  [
    {{"statement": "...", "category": "...", "confidence": 0.0-1.0}},
    ...
  ]"""


def is_significant(content: str, tool_names: list[str] | None = None) -> bool:
    if len(content) < MIN_CONTENT_CHARS:
        return False
    if SKIP_PATTERNS.search(content):
        return False
    if tool_names and any(t in EXTRACT_TOOLS for t in tool_names):
        return True
    return len(content) >= MIN_CONTENT_CHARS * 2


def _summarize_tools(tool_names: list[str], tool_outputs: list[str] | None = None) -> str:
    if not tool_names:
        return "(none)"
    lines = []
    for i, name in enumerate(tool_names):
        out = ""
        if tool_outputs and i < len(tool_outputs) and tool_outputs[i]:
            out = f" → {str(tool_outputs[i])[:120]}"
        lines.append(f"  - {name}{out}")
    return "\n".join(lines)


def _parse_facts(response: str) -> list[dict]:
    text = response.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []
        facts = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            stmt = str(item.get("statement", "")).strip()
            cat = str(item.get("category", "technical_fact")).strip()
            try:
                conf = float(item.get("confidence", 0.7))
            except (ValueError, TypeError):
                conf = 0.7
            if not stmt:
                continue
            if cat not in VALID_CATEGORIES:
                cat = "technical_fact"
            conf = max(0.0, min(1.0, conf))
            facts.append({"statement": stmt, "category": cat, "confidence": conf})
        return facts
    except Exception:
        return []


async def extract_and_store(
    agent: str,
    session_id: str | UUID,
    turn_index: int,
    turn_content: str,
    tool_names: list[str] | None = None,
    tool_outputs: list[str] | None = None,
) -> int:
    """
    Single-pass ADD-only fact extraction for one turn.
    Returns number of facts stored.
    """
    if not is_significant(turn_content, tool_names):
        return 0

    from aux_llm import get_aux_llm
    llm = get_aux_llm()

    prompt = EXTRACT_PROMPT.format(
        max_facts=MAX_FACTS,
        agent=agent,
        turn_content=turn_content[:2000],
        tool_actions=_summarize_tools(tool_names or [], tool_outputs),
    )

    try:
        response = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: llm.complete(
                    prompt, max_tokens=EXTRACT_MAX_TOKENS,
                    # el HTTP corta ANTES que el await: sin esto el thread
                    # seguia generando 120 s tras un timeout de 15 s
                    timeout=EXTRACT_TIMEOUT_S,
                ),
            ),
            timeout=EXTRACT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("[TurnExtractor] timeout — skipping turn %d", turn_index)
        return 0
    except Exception as e:
        log.warning("[TurnExtractor] llm error: %s — skipping", e)
        return 0

    if not response or not response.strip():
        return 0

    facts = _parse_facts(response)
    facts = facts[:MAX_FACTS]

    if not facts:
        return 0

    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA})
    now = datetime.now(timezone.utc)
    stored = 0
    try:
        for fact in facts:
            fact = reroute_feelings(fact, agent)
            metadata = build_fact_metadata(session_id, turn_index, fact, now)
            await conn.execute(
                f"""
                INSERT INTO {SCHEMA}.memories
                    (agent, memory_type, content, category, importance, metadata, created_at)
                VALUES ($1, 'semantic', $2, $3, $4, $5::jsonb, $6)
                """,
                agent,
                fact["statement"],
                fact["category"],
                int(fact["confidence"] * 10),
                json.dumps(metadata),
                now,
            )
            stored += 1
    except Exception as e:
        log.error("[TurnExtractor] db error: %s", e)
    finally:
        await conn.close()

    if stored:
        log.info("[TurnExtractor] %s turn %d → %d facts stored", agent, turn_index, stored)
    return stored


async def extract_batch(
    agent: str,
    session_id: str | UUID,
    turns: list[dict],
) -> int:
    """Process multiple turns in sequence. Returns total facts stored."""
    total = 0
    for turn in turns:
        n = await extract_and_store(
            agent=agent,
            session_id=session_id,
            turn_index=turn.get("turn_index", 0),
            turn_content=str(turn.get("content", "")),
            tool_names=turn.get("tool_names"),
            tool_outputs=turn.get("tool_outputs"),
        )
        total += n
    return total
