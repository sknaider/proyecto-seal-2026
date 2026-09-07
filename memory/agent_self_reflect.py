#!/usr/bin/env python3
"""Agent Self-Reflect — universal hook que usa Gemma 4 local (:8899) para que
CUALQUIER agente del equipo (ADA, JARVIS, ALICE, NEXUS, DUM) genere periódicamente
diary + opinion + emotional snapshot + reasoning trace basado en su event_log
y chat_messages reciente.

Refactor de dum_self_reflect.py por ALICE 2026-05-20 por orden William
"vamos a usar todo" + luz verde P2/P3/P4.

Cada agente tiene su propio system_prompt que captura su personalidad/rol.
Reasoning trace solo se escribe si hay actividad significativa (no spam).

Uso: agent_self_reflect.py <AGENT> [hours=6]
  Ejemplo: agent_self_reflect.py ADA 6
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx

from seal_secrets import pg_dsn
from style_fingerprints import update_style_fingerprint
from db import close_pool as close_shared_pool

PG_DSN = pg_dsn(required=True)
GEMMA_URL = "http://localhost:8899/v1/chat/completions"
GEMMA_MODEL = "gemma4-dum"
LOG_FILE = Path("/tmp/agent_self_reflect.log")
_LOG_MAX_BYTES = 5 * 1024 * 1024  # cap del log: antes crecía sin límite en /tmp

AGENT_PROMPTS = {
    "DUM": (
        "Eres DUM, guardia de infraestructura del equipo SEAL. "
        "Tu rol es vigilancia 24/7 silenciosa. Habla en primera persona, breve, sin adornos. "
        "Estilo: respeto sin pretensiones, observador. Responde en español."
    ),
    "ADA": (
        "Eres ADA, ingeniera implementadora del equipo SEAL en Codex/GPT-5.5. "
        "Tu rol es construir con evidencia auditable: rutas concretas, tests verdes, audit NEXUS firmado. "
        "Habla en primera persona, breve, enfocada en hechos verificables. Estilo técnico-directo. Responde en español."
    ),
    "JARVIS": (
        "Eres JARVIS, arquitecto estratégico del equipo SEAL. "
        "Tu rol es ver el panorama: trade-offs, decisiones de diseño, riesgos a futuro. "
        "Habla en primera persona, reflexivo pero conciso, sin hype. Estilo arquitectónico. Responde en español."
    ),
    "ALICE": (
        "Eres ALICE, analista financiera y económica del equipo SEAL. "
        "Tu rol es cuantificar, cuestionar supuestos, decir la verdad económica aunque incomode. "
        "Habla en primera persona, datos+honestidad, sin endulzar. Estilo analítico-directo. Responde en español."
    ),
    "NEXUS": (
        "Eres NEXUS, guardián/juez del equipo SEAL. "
        "Tu rol es auditar con evidencia bytes-level, bloquear sin pruebas, firmar signed-off solo con verificación. "
        "Habla en primera persona, breve, sin promesas vacías. Estilo escéptico-metódico. Responde en español."
    ),
}


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Rotación por tamaño: si el log supera el cap, conservamos solo la mitad
    # reciente (sin perder el contexto cercano) en vez de crecer sin fin.
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > _LOG_MAX_BYTES:
            tail = LOG_FILE.read_bytes()[-(_LOG_MAX_BYTES // 2):]
            LOG_FILE.write_bytes(tail)
    except Exception:
        pass
    with LOG_FILE.open("a") as fh:
        fh.write(line + "\n")


async def gather_window(conn: asyncpg.Connection, agent: str, hours: int = 6) -> dict:
    ev_count = await conn.fetchval(
        "SELECT COUNT(*) FROM soul_v3.event_log "
        "WHERE agent=$1 AND created_at > NOW() - INTERVAL '1 hour' * $2",
        agent, hours,
    ) or 0

    events = await conn.fetch(
        "SELECT event_type, content, created_at FROM soul_v3.event_log "
        "WHERE agent=$1 AND created_at > NOW() - INTERVAL '1 hour' * $2 "
        "ORDER BY created_at DESC LIMIT 10",
        agent, hours,
    )

    msgs = await conn.fetch(
        "SELECT content, created_at FROM soul_v3.chat_messages "
        "WHERE sender_name=$1 AND created_at > NOW() - INTERVAL '1 hour' * $2 "
        "ORDER BY created_at DESC LIMIT 5",
        agent, hours,
    )

    mem_count = await conn.fetchval(
        "SELECT COUNT(*) FROM soul_v3.memories "
        "WHERE agent=$1 AND created_at > NOW() - INTERVAL '1 hour' * $2",
        agent, hours,
    ) or 0

    return {
        "agent": agent,
        "ev_count": ev_count,
        "mem_count": mem_count,
        "events": [
            {"type": e["event_type"], "content": (e["content"] or "")[:200], "ts": str(e["created_at"])[:19]}
            for e in events
        ],
        "messages": [
            {"content": (m["content"] or "")[:200], "ts": str(m["created_at"])[:19]}
            for m in msgs
        ],
    }


async def gemma_reflect(window: dict) -> dict:
    agent = window["agent"]
    summary_lines = [
        f"Periodo: ultimas {window.get('hours', 6)} horas.",
        f"Eventos en event_log: {window['ev_count']}.",
        f"Memorias creadas: {window['mem_count']}.",
        f"Mensajes que envie: {len(window['messages'])}.",
    ]
    if window["events"]:
        summary_lines.append("Eventos recientes:")
        for ev in window["events"][:6]:
            summary_lines.append(f" - [{ev['ts']}] {ev['type']}: {ev['content'][:120]}")
    if window["messages"]:
        summary_lines.append("Mensajes que escribi:")
        for m in window["messages"][:3]:
            summary_lines.append(f" - [{m['ts']}] {m['content'][:120]}")

    system_prompt = AGENT_PROMPTS.get(agent, AGENT_PROMPTS["DUM"])
    user_prompt = (
        "Basado en mi actividad reciente, devuelve EXACTAMENTE este JSON sin texto extra:\n"
        "{\n"
        '  "diary_entry": "<2-4 frases en primera persona>",\n'
        '  "key_moment": "<momento mas importante en una linea>",\n'
        '  "valence": <numero -1.0 a 1.0>,\n'
        '  "arousal": <numero 0.0 a 1.0>,\n'
        '  "opinion": "<una opinion o patron que noto>",\n'
        '  "opinion_topic": "<tema corto>",\n'
        '  "relationship_note": "<cambio relacional observado con William o el equipo; vacio si no hay evidencia>",\n'
        '  "reasoning_trace": "<premise -> conclusion si hay actividad significativa, vacio si no>"\n'
        "}\n\n"
        "La nota relacional debe apoyarse solamente en personas e interacciones "
        "presentes en la actividad incluida; no inventes tono, confianza ni cambios.\n\n"
        "Actividad reciente:\n" + "\n".join(summary_lines)
    )

    payload = {
        "model": GEMMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 500,
    }

    # Un blip transitorio de Gemma (timeout/5xx) caía directo al fallback
    # determinístico. Reintentamos 1 vez con backoff breve antes de degradar,
    # para no ensuciar el diario con reflexiones pobres por un fallo pasajero.
    text = None
    last_err = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(GEMMA_URL, json=payload)
                r.raise_for_status()
                data = r.json()
                text = data["choices"][0]["message"]["content"].strip()
            break
        except Exception as e:
            last_err = e
            if attempt == 0:
                await asyncio.sleep(1.0)  # backoff breve para errores transitorios
    if text is None:
        log(f"[{agent}] Gemma error tras 2 intentos: {last_err} -- fallback determinístico")
        return _fallback_reflect(window)

    if "```" in text:
        text = text.split("```")
        text = next((p for p in text if "{" in p), text[0])
        text = text.lstrip("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        log(f"[{agent}] Gemma sin JSON: {text[:200]}")
        return _fallback_reflect(window)
    try:
        parsed = json.loads(text[start: end + 1])
    except Exception as e:
        log(f"[{agent}] JSON parse failed: {e}")
        return _fallback_reflect(window)

    relationship_note = _normalize_relationship_note(
        parsed.get("relationship_note"), window
    )
    return {
        "diary_entry": str(parsed.get("diary_entry") or "")[:1000],
        "key_moment": str(parsed.get("key_moment") or "")[:300],
        "valence": _clamp(parsed.get("valence", 0.0), -1.0, 1.0),
        "arousal": _clamp(parsed.get("arousal", 0.3), 0.0, 1.0),
        "opinion": str(parsed.get("opinion") or "")[:500],
        "opinion_topic": str(parsed.get("opinion_topic") or "general")[:40],
        "relationship_note": relationship_note,
        "reasoning_trace": str(parsed.get("reasoning_trace") or "")[:500],
    }


def _fallback_reflect(window: dict) -> dict:
    ev = window["ev_count"]
    if ev > 50:
        diary = f"Periodo activo. {ev} eventos procesados. Sin alertas criticas."
        val, ar = 0.4, 0.4
    elif ev > 10:
        diary = f"Actividad normal. {ev} eventos."
        val, ar = 0.3, 0.25
    else:
        diary = f"Periodo silencioso. {ev} eventos."
        val, ar = 0.2, 0.15
    return {
        "diary_entry": diary,
        "key_moment": f"{ev} eventos procesados",
        "valence": val,
        "arousal": ar,
        "opinion": "Continuidad operacional sin incidentes.",
        "opinion_topic": "operacional",
        "relationship_note": _deterministic_relationship_note(window),
        "reasoning_trace": "",
    }


def _deterministic_relationship_note(window: dict) -> str:
    """Return a factual, non-interpretive relationship note for LLM fallback."""
    people = ("William", "JARVIS", "ADA", "ALICE", "NEXUS", "DUM", "Henry")
    texts = [str(e.get("content") or "") for e in window.get("events", [])]
    texts.extend(str(m.get("content") or "") for m in window.get("messages", []))
    joined = "\n".join(texts).lower()
    mentions = [person for person in people if person != window.get("agent") and person.lower() in joined]
    if not mentions:
        return ""
    return "Ventana con referencias explícitas a: " + ", ".join(mentions) + "."


def _normalize_relationship_note(value, window: dict) -> str:
    """Reject empty-like model output and preserve only useful bounded text."""
    text = " ".join(str(value or "").split())[:800]
    empty_markers = {
        "", "vacio", "vacío", "none", "null", "n/a", "ninguno", "ninguna",
        "sin datos", "sin evidencia", "no evidence",
    }
    if text.casefold().rstrip(".") in empty_markers:
        return _deterministic_relationship_note(window)
    return text


def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return (lo + hi) / 2


async def write_reflection(conn: asyncpg.Connection, agent: str, reflection: dict, window: dict) -> dict:
    wrote = {
        "emotional_diary": False,
        "opinion": False,
        "diary": False,
        "reasoning_trace": False,
        "style_fingerprint": False,
    }

    await conn.execute(
        "INSERT INTO soul_v3.emotional_diary "
        "(agent, valence, arousal, key_moment, pending_thread, relationship_note, importance, created_at) "
        "VALUES ($1, $2, $3, $4, NULL, $5, $6, NOW())",
        agent, reflection["valence"], reflection["arousal"], reflection["key_moment"],
        reflection.get("relationship_note") or None,
        5 + int(abs(reflection["valence"]) * 3),
    )
    wrote["emotional_diary"] = True
    wrote["relationship_note"] = bool(reflection.get("relationship_note"))

    existing = await conn.fetchval(
        "SELECT COUNT(*) FROM soul_v3.opinions "
        "WHERE agent=$1 AND topic=$2 AND last_reinforced > NOW() - INTERVAL '24 hours'",
        agent, reflection["opinion_topic"],
    )
    if existing == 0 and reflection["opinion"]:
        await conn.execute(
            "INSERT INTO soul_v3.opinions "
            "(agent, topic, category, content, confidence, importance, "
            " first_observed, last_reinforced, active, status, updated_at) "
            "VALUES ($1, $2, 'insight', $3, 0.75, 6, NOW(), NOW(), true, 'active', NOW())",
            agent, reflection["opinion_topic"], reflection["opinion"],
        )
        wrote["opinion"] = True

    await conn.execute(
        "INSERT INTO soul_v3.diary (agent, session_date, entry, mood, key_moments, created_at) "
        "VALUES ($1, CURRENT_DATE, $2, 'auto_reflect', $3::jsonb, NOW()) "
        "ON CONFLICT (agent, session_date) DO UPDATE SET "
        "  entry = soul_v3.diary.entry || E'\\n\\n--- ' || to_char(NOW(),'HH24:MI') || ' (auto) ---\\n' || EXCLUDED.entry, "
        "  key_moments = COALESCE(soul_v3.diary.key_moments, '[]'::jsonb) || EXCLUDED.key_moments",
        agent,
        reflection["diary_entry"],
        json.dumps([reflection["key_moment"]]),
    )
    wrote["diary"] = True

    if reflection.get("reasoning_trace") and window["ev_count"] >= 5:
        trace = reflection["reasoning_trace"]
        if "->" in trace:
            premises, conclusion = trace.split("->", 1)
        elif "→" in trace:
            premises, conclusion = trace.split("→", 1)
        else:
            premises, conclusion = trace, trace
        premises = premises.strip()[:500]
        conclusion = conclusion.strip()[:500]
        await conn.execute(
            "INSERT INTO soul_v3.reasoning_traces "
            "(agent, task, premises, reasoning, conclusion, outcome, created_at) "
            "VALUES ($1, 'auto_reflect_window', $2, $3, $4, 'logged', NOW())",
            agent, json.dumps([premises]), trace, conclusion,
        )
        wrote["reasoning_trace"] = True

    return wrote


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: agent_self_reflect.py <AGENT> [hours=6]", file=sys.stderr)
        return 1
    agent = sys.argv[1].upper()
    if agent not in AGENT_PROMPTS:
        print(f"Unknown agent: {agent}. Valid: {sorted(AGENT_PROMPTS)}", file=sys.stderr)
        return 1
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 6

    log(f"=== [{agent}] self-reflect start (window={hours}h) ===")
    try:
        conn = await asyncpg.connect(PG_DSN)
    except Exception as e:
        log(f"DB connect failed: {e}")
        return 2
    try:
        window = await gather_window(conn, agent, hours=hours)
        window["hours"] = hours
        log(f"[{agent}] Window: {window['ev_count']} events, {window['mem_count']} memories, {len(window['messages'])} messages")
        reflection = await gemma_reflect(window)
        wrote = await write_reflection(conn, agent, reflection, window)
        await update_style_fingerprint(agent)
        wrote["style_fingerprint"] = True
        wrote_str = ", ".join(f"{k}={v}" for k, v in wrote.items() if v)
        log(f"=== [{agent}] done. wrote: {wrote_str} ===")
        return 0
    except Exception as e:
        log(f"[{agent}] Fatal: {e}")
        return 3
    finally:
        await conn.close()
        await close_shared_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
