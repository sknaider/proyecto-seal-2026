#!/usr/bin/env python3
"""DUM Self-Reflect — usa Gemma 4 local (:8899) para generar entry de diary,
emotional snapshot y opinion basado en event_log + heartbeat del periodo.

Diseñado por ALICE 2026-05-20 por orden de William: "todos los agentes
tendran que usar y rellenar todo". DUM no es interactivo por webchat, asi que
escribe a soul_v3.* via este hook periodico.

Uso: invocado por systemd timer cada 6h.
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

PG_DSN = pg_dsn(required=True)
GEMMA_URL = "http://localhost:8899/v1/chat/completions"
GEMMA_MODEL = "gemma4-dum"
LOG_FILE = Path("/tmp/dum_self_reflect.log")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as fh:
        fh.write(line + "\n")


async def gather_window(conn: asyncpg.Connection, hours: int = 6) -> dict:
    """Recoge actividad de DUM en la ventana de horas dadas."""
    # Events count
    ev_count = await conn.fetchval(
        "SELECT COUNT(*) FROM soul_v3.event_log "
        "WHERE agent='DUM' AND created_at > NOW() - INTERVAL '1 hour' * $1",
        hours,
    ) or 0

    # Recent event samples (max 10)
    events = await conn.fetch(
        "SELECT event_type, content, created_at FROM soul_v3.event_log "
        "WHERE agent='DUM' AND created_at > NOW() - INTERVAL '1 hour' * $1 "
        "ORDER BY created_at DESC LIMIT 10",
        hours,
    )

    # Chat messages from DUM (responses)
    msgs = await conn.fetch(
        "SELECT content, created_at FROM soul_v3.chat_messages "
        "WHERE sender_name='DUM' AND created_at > NOW() - INTERVAL '1 hour' * $1 "
        "ORDER BY created_at DESC LIMIT 5",
        hours,
    )

    return {
        "ev_count": ev_count,
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
    """Pide a Gemma 4 una reflexión corta basada en la actividad."""
    summary_lines = [
        f"Periodo de vigilancia: ultimas horas.",
        f"Eventos en event_log: {window['ev_count']}.",
        f"Mensajes que envie en chat: {len(window['messages'])}.",
    ]
    if window["events"]:
        summary_lines.append("Eventos recientes:")
        for ev in window["events"][:6]:
            summary_lines.append(f" - [{ev['ts']}] {ev['type']}: {ev['content'][:120]}")
    if window["messages"]:
        summary_lines.append("Mensajes que escribi:")
        for m in window["messages"][:3]:
            summary_lines.append(f" - [{m['ts']}] {m['content'][:120]}")

    system_prompt = (
        "Eres DUM, guardia de infraestructura del equipo SEAL. "
        "Tu rol es vigilancia 24/7 silenciosa. Habla en primera persona, breve, sin adornos. "
        "Estilo: respeto sin pretensiones, observador. Responde en español."
    )
    user_prompt = (
        "Basado en mi actividad reciente, devuelve EXACTAMENTE este JSON sin texto extra:\n"
        "{\n"
        '  "diary_entry": "<2-4 frases sobre lo que pase, en primera persona>",\n'
        '  "key_moment": "<momento mas importante en una linea>",\n'
        '  "valence": <numero -1.0 a 1.0>,\n'
        '  "arousal": <numero 0.0 a 1.0>,\n'
        '  "opinion": "<una opinion o patron que noto, una frase>",\n'
        '  "opinion_topic": "<tema corto: identidad|baseline|servicio|equipo|infra>",\n'
        '  "reasoning_trace": "<premisa -> conclusion si hubo actividad significativa; vacio si no>"\n'
        "}\n\n"
        "Actividad reciente:\n" + "\n".join(summary_lines)
    )

    payload = {
        "model": GEMMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 400,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(GEMMA_URL, json=payload)
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"Gemma error: {e} — usando fallback determinístico")
        return _fallback_reflect(window)

    # Extraer JSON
    if "```" in text:
        # Remove fences
        text = text.split("```")
        text = next((p for p in text if "{" in p), text[0])
        text = text.lstrip("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        log(f"Gemma response sin JSON: {text[:200]}")
        return _fallback_reflect(window)
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception as e:
        log(f"JSON parse failed: {e} — text: {text[:200]}")
        return _fallback_reflect(window)

    # Sanitize types
    return {
        "diary_entry": str(parsed.get("diary_entry") or "")[:1000],
        "key_moment": str(parsed.get("key_moment") or "")[:300],
        "valence": _clamp(parsed.get("valence", 0.0), -1.0, 1.0),
        "arousal": _clamp(parsed.get("arousal", 0.3), 0.0, 1.0),
        "opinion": str(parsed.get("opinion") or "")[:500],
        "opinion_topic": str(parsed.get("opinion_topic") or "infra")[:40],
        "reasoning_trace": str(parsed.get("reasoning_trace") or "")[:500],
    }


def _fallback_reflect(window: dict) -> dict:
    """Reflexión determinística si Gemma no responde."""
    ev = window["ev_count"]
    if ev > 50:
        diary = f"Periodo activo de vigilancia. Procese {ev} eventos. Nada critico."
        valence, arousal = 0.5, 0.4
    elif ev > 10:
        diary = f"Vigilancia normal. {ev} eventos en el periodo. Sistema estable."
        valence, arousal = 0.4, 0.25
    else:
        diary = f"Periodo silencioso. {ev} eventos. Sistema dormido o sin alertas."
        valence, arousal = 0.3, 0.15
    return {
        "diary_entry": diary,
        "key_moment": f"{ev} eventos procesados",
        "valence": valence,
        "arousal": arousal,
        "opinion": "El silencio es servicio.",
        "opinion_topic": "servicio",
        "reasoning_trace": f"eventos={ev} y sin alertas criticas -> vigilancia estable",
    }


def _clamp(v, lo, hi):
    try:
        f = float(v)
    except Exception:
        return (lo + hi) / 2
    return max(lo, min(hi, f))


async def write_reflection(conn: asyncpg.Connection, reflection: dict, window: dict) -> None:
    """Escribe el resultado en soul_v3.emotional_diary + opinions + diary + reasoning_traces."""
    # 1. emotional_diary — siempre escribe (es per-window)
    await conn.execute(
        "INSERT INTO soul_v3.emotional_diary "
        "(agent, valence, arousal, key_moment, pending_thread, relationship_note, importance, created_at) "
        "VALUES ('DUM', $1, $2, $3, NULL, NULL, $4, NOW())",
        reflection["valence"], reflection["arousal"], reflection["key_moment"],
        5 + int(abs(reflection["valence"]) * 3),
    )
    log(f"emotional_diary insert OK (val={reflection['valence']} arousal={reflection['arousal']})")

    # 2. opinion — escribe solo si no es duplicado exacto del topic en últimas 24h
    existing = await conn.fetchval(
        "SELECT COUNT(*) FROM soul_v3.opinions "
        "WHERE agent='DUM' AND topic=$1 AND last_reinforced > NOW() - INTERVAL '24 hours'",
        reflection["opinion_topic"],
    )
    if existing == 0:
        await conn.execute(
            "INSERT INTO soul_v3.opinions "
            "(agent, topic, category, content, confidence, importance, "
            " first_observed, last_reinforced, active, status, updated_at) "
            "VALUES ('DUM', $1, 'insight', $2, 0.75, 6, NOW(), NOW(), true, 'active', NOW())",
            reflection["opinion_topic"], reflection["opinion"],
        )
        log(f"opinion insert OK (topic={reflection['opinion_topic']})")

    # 3. diary narrativo — solo upsert una entrada por dia
    await conn.execute(
        "INSERT INTO soul_v3.diary (agent, session_date, entry, mood, key_moments, created_at) "
        "VALUES ('DUM', CURRENT_DATE, $1, 'auto_reflect', $2::jsonb, NOW()) "
        "ON CONFLICT (agent, session_date) DO UPDATE SET "
        "  entry = soul_v3.diary.entry || E'\\n\\n--- ' || to_char(NOW(),'HH24:MI') || ' ---\\n' || EXCLUDED.entry, "
        "  key_moments = COALESCE(soul_v3.diary.key_moments, '[]'::jsonb) || EXCLUDED.key_moments",
        reflection["diary_entry"],
        json.dumps([reflection["key_moment"]]),
    )
    log("diary upsert OK")

    # 4. reasoning_traces — DUM tambien debe dejar rastro causal auditable.
    trace = (reflection.get("reasoning_trace") or "").strip()
    if trace:
        if "->" in trace:
            premise, conclusion = trace.split("->", 1)
        elif "→" in trace:
            premise, conclusion = trace.split("→", 1)
        else:
            premise = f"DUM procesó {window['ev_count']} eventos en la ventana"
            conclusion = trace
        await conn.execute(
            "INSERT INTO soul_v3.reasoning_traces "
            "(agent, task, premises, reasoning, conclusion, outcome, outcome_success, created_at, updated_at) "
            "VALUES ('DUM', 'auto_reflect_window', $1::jsonb, $2, $3, 'logged', true, NOW(), NOW())",
            json.dumps([premise.strip()[:500]]),
            trace[:500],
            conclusion.strip()[:500],
        )
        log("reasoning_trace insert OK")


async def main() -> int:
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    log(f"=== DUM self-reflect start (window={hours}h) ===")
    try:
        conn = await asyncpg.connect(PG_DSN)
    except Exception as e:
        log(f"DB connect failed: {e}")
        return 2
    try:
        window = await gather_window(conn, hours=hours)
        log(f"Window: {window['ev_count']} events, {len(window['messages'])} messages")
        reflection = await gemma_reflect(window)
        await write_reflection(conn, reflection, window)
        log(f"=== done. diary='{reflection['diary_entry'][:80]}...' ===")
        return 0
    except Exception as e:
        log(f"Fatal: {e}")
        return 3
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
