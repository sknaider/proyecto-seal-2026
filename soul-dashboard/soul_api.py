"""SOUL Dashboard API — FastAPI backend at port 8850.

Queries soul_v3 schema directly from PostgreSQL.
CORS open for local dev (localhost:3005).
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import asyncpg
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS", "DUM"]

_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    _pool = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=10)
    yield
    await _pool.close()


app = FastAPI(title="SOUL Dashboard API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _pool_ok() -> asyncpg.Pool:
    if _pool is None:
        raise HTTPException(503, "DB pool not ready")
    return _pool


def _dt(val) -> str | None:
    return val.isoformat() if val else None


def _json_obj(val) -> dict:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _json_list(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _record_dict(row: asyncpg.Record | None) -> dict:
    if not row:
        return {}
    result = {}
    for key, value in dict(row).items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif isinstance(value, (dict, list, str, int, float, bool)) or value is None:
            result[key] = value
        else:
            result[key] = str(value)
    return result


async def _table_exists(pool: asyncpg.Pool, table: str) -> bool:
    return bool(
        await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='soul_v3' AND table_name=$1
            )
            """,
            table,
        )
    )


# ── Agents ────────────────────────────────────────────────────────────────────

@app.get("/api/soul/agents")
async def list_agents():
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT name, role, active, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n "
        "FROM soul_v3.agents ORDER BY name"
    )
    return [
        {
            "name": r["name"],
            "role": r["role"],
            "active": r["active"],
            "ocean": {
                "O": float(r["ocean_o"] or 0),
                "C": float(r["ocean_c"] or 0),
                "E": float(r["ocean_e"] or 0),
                "A": float(r["ocean_a"] or 0),
                "N": float(r["ocean_n"] or 0),
            },
        }
        for r in rows
    ]


# ── OCEAN ─────────────────────────────────────────────────────────────────────

@app.get("/api/soul/ocean/all")
async def ocean_all():
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT agent, ocean_scores, ocean_baseline, updated_at "
        "FROM soul_v3.identity ORDER BY agent"
    )
    agents = []
    for r in rows:
        ocean = r["ocean_scores"]
        if isinstance(ocean, str):
            ocean = json.loads(ocean)
        baseline = r["ocean_baseline"]
        if isinstance(baseline, str):
            baseline = json.loads(baseline)
        agents.append({
            "agent": r["agent"],
            "ocean": ocean or {},
            "baseline": baseline or {},
            "updated_at": _dt(r["updated_at"]),
        })
    return {"agents": agents}


# ── Snapshot ──────────────────────────────────────────────────────────────────

@app.get("/api/soul/snapshot")
async def snapshot(agent: str = Query("ADA")):
    pool = _pool_ok()

    identity = await pool.fetchrow(
        "SELECT ocean_scores, ocean_baseline, personality, philosophy, updated_at "
        "FROM soul_v3.identity WHERE agent=$1",
        agent,
    )
    working = await pool.fetchrow(
        "SELECT agent_state, emotional_state, last_intention, task_name, step, total_steps, updated_at "
        "FROM soul_v3.working_state WHERE agent=$1",
        agent,
    )
    last_thought = await pool.fetchrow(
        "SELECT thought, emotional_state, created_at FROM soul_v3.inner_monologue "
        "WHERE agent=$1 ORDER BY created_at DESC LIMIT 1",
        agent,
    )
    last_diary = await pool.fetchrow(
        "SELECT entry, mood, session_date FROM soul_v3.diary "
        "WHERE agent=$1 ORDER BY session_date DESC LIMIT 1",
        agent,
    )
    mem_count = await pool.fetchval(
        "SELECT COUNT(*) FROM soul_v3.memories WHERE agent=$1 AND invalid_at IS NULL",
        agent,
    )
    motivation = await pool.fetch(
        "SELECT tank, value, last_fired FROM soul_v3.motivation_states WHERE agent=$1 ORDER BY tank",
        agent,
    )

    ocean = {}
    if identity:
        ocean = identity["ocean_scores"] or {}
        if isinstance(ocean, str):
            ocean = json.loads(ocean)

    return {
        "agent": agent,
        "ocean": ocean,
        "agent_state": working["agent_state"] if working else None,
        "emotional_state": working["emotional_state"] if working else (last_thought["emotional_state"] if last_thought else None),
        "last_intention": working["last_intention"] if working else None,
        "task": {"name": working["task_name"], "step": working["step"], "total": working["total_steps"]} if working else None,
        "last_thought": {"text": last_thought["thought"], "emotion": last_thought["emotional_state"], "at": _dt(last_thought["created_at"])} if last_thought else None,
        "last_diary": {"entry": last_diary["entry"][:300] if last_diary else None, "mood": last_diary["mood"] if last_diary else None, "date": str(last_diary["session_date"]) if last_diary else None},
        "memory_count": mem_count or 0,
        "motivation": [{"tank": r["tank"], "value": float(r["value"] or 0), "last_fired": _dt(r["last_fired"])} for r in motivation],
        "updated_at": _dt(working["updated_at"]) if working else None,
    }


# ── Memories ──────────────────────────────────────────────────────────────────

@app.get("/api/soul/memories")
async def memories(
    agent: str = Query("ADA"),
    q: str = Query(""),
    category: str = Query(""),
    limit: int = Query(30, le=100),
    offset: int = Query(0),
):
    pool = _pool_ok()
    where = ["m.agent=$1", "m.invalid_at IS NULL"]
    params: list = [agent]
    idx = 2

    if q:
        where.append(f"m.content ILIKE ${idx}")
        params.append(f"%{q}%")
        idx += 1
    if category:
        where.append(f"m.category=${idx}")
        params.append(category)
        idx += 1

    sql = f"""
        SELECT id, content, category, memory_type, importance, valence, arousal,
               source, created_at, heat_score, access_count
        FROM soul_v3.memories m
        WHERE {' AND '.join(where)}
        ORDER BY importance DESC, created_at DESC
        LIMIT {limit} OFFSET {offset}
    """
    rows = await pool.fetch(sql, *params)
    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM soul_v3.memories m WHERE {' AND '.join(where)}",
        *params,
    )
    return {
        "total": total,
        "memories": [
            {
                "id": r["id"],
                "content": r["content"],
                "category": r["category"],
                "type": r["memory_type"],
                "importance": r["importance"],
                "valence": float(r["valence"] or 0),
                "arousal": float(r["arousal"] or 0),
                "source": r["source"],
                "created_at": _dt(r["created_at"]),
                "heat": float(r["heat_score"] or 0),
                "accesses": r["access_count"] or 0,
            }
            for r in rows
        ],
    }


@app.get("/api/soul/memories/categories")
async def memory_categories(agent: str = Query("ADA")):
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT category, COUNT(*) as cnt FROM soul_v3.memories "
        "WHERE agent=$1 AND invalid_at IS NULL GROUP BY category ORDER BY cnt DESC",
        agent,
    )
    return [{"category": r["category"], "count": r["cnt"]} for r in rows]


# ── Inner Monologue ───────────────────────────────────────────────────────────

@app.get("/api/soul/thoughts")
async def thoughts(agent: str = Query("ADA"), limit: int = Query(20, le=100)):
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT id, thought, emotional_state, uncertainty, intention, created_at "
        "FROM soul_v3.inner_monologue WHERE agent=$1 ORDER BY created_at DESC LIMIT $2",
        agent, limit,
    )
    return [
        {
            "id": r["id"],
            "thought": r["thought"],
            "emotion": r["emotional_state"],
            "uncertainty": r["uncertainty"],
            "intention": r["intention"],
            "at": _dt(r["created_at"]),
        }
        for r in rows
    ]


class ThoughtIn(BaseModel):
    agent: str = "ADA"
    thought: str
    emotional_state: str = ""


@app.post("/api/soul/thoughts")
async def write_thought(body: ThoughtIn):
    pool = _pool_ok()
    await pool.execute(
        "INSERT INTO soul_v3.inner_monologue (agent, session_id, thought, emotional_state, created_at) "
        "VALUES ($1, 'dashboard', $2, $3, NOW())",
        body.agent, body.thought, body.emotional_state,
    )
    return {"ok": True}


# ── Diary ─────────────────────────────────────────────────────────────────────

@app.get("/api/soul/diary")
async def diary(agent: str = Query("ADA"), limit: int = Query(20, le=100)):
    """Fusión de soul_v3.diary (narrativo manual) + soul_v3.emotional_diary (tracking emocional automático).
    Fix ALICE 2026-05-20: el UI solo veía diary, ignorando emotional_diary donde estaban 393 entries reales."""
    pool = _pool_ok()
    # Narrative diary (manual)
    narrative = await pool.fetch(
        "SELECT id, session_date, entry, mood, key_moments, created_at "
        "FROM soul_v3.diary WHERE agent=$1 ORDER BY session_date DESC LIMIT $2",
        agent, limit,
    )
    # Emotional diary (automatic per-session)
    emotional = await pool.fetch(
        "SELECT id, key_moment, pending_thread, relationship_note, valence, arousal, "
        "       importance, created_at "
        "FROM soul_v3.emotional_diary WHERE agent=$1 ORDER BY created_at DESC LIMIT $2",
        agent, limit,
    )
    results = []
    for r in narrative:
        results.append({
            "id": f"n{r['id']}",
            "kind": "narrative",
            "date": str(r["session_date"]),
            "entry": r["entry"],
            "mood": r["mood"],
            "moments": r["key_moments"],
            "created_at": _dt(r["created_at"]),
        })
    for r in emotional:
        parts = []
        if r["key_moment"]:
            parts.append(r["key_moment"])
        if r["pending_thread"]:
            parts.append(f"[pendiente] {r['pending_thread']}")
        if r["relationship_note"]:
            parts.append(f"[relación] {r['relationship_note']}")
        results.append({
            "id": f"e{r['id']}",
            "kind": "emotional",
            "date": str(r["created_at"].date()) if r["created_at"] else None,
            "entry": "\n\n".join(parts) if parts else "",
            "mood": f"valence={float(r['valence'] or 0):.2f} arousal={float(r['arousal'] or 0):.2f}",
            "moments": None,
            "valence": float(r["valence"] or 0),
            "arousal": float(r["arousal"] or 0),
            "importance": r["importance"],
            "created_at": _dt(r["created_at"]),
        })
    # Sort merged by created_at descending
    results.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return results[:limit]


class DiaryIn(BaseModel):
    agent: str = "ADA"
    entry: str
    mood: str = ""


@app.post("/api/soul/diary")
async def write_diary(body: DiaryIn):
    pool = _pool_ok()
    from datetime import date
    await pool.execute(
        "INSERT INTO soul_v3.diary (agent, session_date, entry, mood, created_at) "
        "VALUES ($1, $2, $3, $4, NOW()) "
        "ON CONFLICT (agent, session_date) DO UPDATE SET entry=EXCLUDED.entry, mood=EXCLUDED.mood",
        body.agent, date.today(), body.entry, body.mood,
    )
    return {"ok": True}


# ── Opinions ──────────────────────────────────────────────────────────────────

@app.get("/api/soul/opinions")
async def opinions(agent: str = Query("ADA"), limit: int = Query(30, le=100)):
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT id, topic, content, confidence, evidence_count, category, status, updated_at "
        "FROM soul_v3.opinions WHERE agent=$1 AND (invalid_at IS NULL OR invalid_at > NOW()) "
        "ORDER BY importance DESC NULLS LAST, updated_at DESC LIMIT $2",
        agent, limit,
    )
    return [
        {
            "id": r["id"],
            "topic": r["topic"],
            "content": r["content"],
            "confidence": float(r["confidence"] or 0),
            "evidence": r["evidence_count"],
            "category": r["category"],
            "status": r["status"],
            "updated_at": _dt(r["updated_at"]),
        }
        for r in rows
    ]


# ── Beliefs ───────────────────────────────────────────────────────────────────

@app.get("/api/soul/beliefs")
async def beliefs(agent: str = Query("ADA"), limit: int = Query(30, le=100)):
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT id, topic, content, confidence, evidence_count, valid_from, created_at "
        "FROM soul_v3.beliefs WHERE agent=$1 AND (invalid_at IS NULL OR invalid_at > NOW()) "
        "ORDER BY confidence DESC, created_at DESC LIMIT $2",
        agent, limit,
    )
    return [
        {
            "id": r["id"],
            "topic": r["topic"],
            "content": r["content"],
            "confidence": float(r["confidence"] or 0),
            "evidence": r["evidence_count"],
            "valid_from": _dt(r["valid_from"]),
            "created_at": _dt(r["created_at"]),
        }
        for r in rows
    ]


# ── Instincts ─────────────────────────────────────────────────────────────────

@app.get("/api/soul/instincts")
async def instincts(agent: str = Query("ADA")):
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT id, trigger_condition, action, strength, success_count, failure_count, metric_score, created_at "
        "FROM soul_v3.instincts WHERE agent=$1 AND invalid_at IS NULL "
        "ORDER BY strength DESC, success_count DESC",
        agent,
    )
    recent = await pool.fetch(
        "SELECT ia.context, ia.outcome, ia.created_at, i.trigger_condition "
        "FROM soul_v3.instinct_activations ia "
        "JOIN soul_v3.instincts i ON ia.instinct_id = i.id "
        "WHERE ia.agent=$1 ORDER BY ia.created_at DESC LIMIT 10",
        agent,
    )
    return {
        "instincts": [
            {
                "id": r["id"],
                "trigger": r["trigger_condition"],
                "action": r["action"],
                "strength": float(r["strength"] or 0),
                "wins": r["success_count"],
                "losses": r["failure_count"],
                "score": float(r["metric_score"] or 0),
                "created_at": _dt(r["created_at"]),
            }
            for r in rows
        ],
        "recent_activations": [
            {
                "trigger": r["trigger_condition"],
                "context": r["context"],
                "outcome": r["outcome"],
                "at": _dt(r["created_at"]),
            }
            for r in recent
        ],
    }


# ── Goals ─────────────────────────────────────────────────────────────────────

@app.get("/api/soul/goals")
async def goals(agent: str = Query("ADA")):
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT id, goal, priority, status, horizon, deadline, progress, created_at "
        "FROM soul_v3.agent_learning_goals WHERE agent=$1 ORDER BY priority ASC, created_at DESC",
        agent,
    )
    return [
        {
            "id": r["id"],
            "goal": r["goal"],
            "priority": r["priority"],
            "status": r["status"],
            "horizon": r["horizon"],
            "deadline": _dt(r["deadline"]),
            "progress": float(r["progress"] or 0),
            "created_at": _dt(r["created_at"]),
        }
        for r in rows
    ]


# ── Emotional / Motivation State ──────────────────────────────────────────────

@app.get("/api/soul/emotions")
async def emotions(agent: str = Query("ADA")):
    pool = _pool_ok()
    motivation = await pool.fetch(
        "SELECT tank, value, last_update, last_fired, fire_count "
        "FROM soul_v3.motivation_states WHERE agent=$1 ORDER BY tank",
        agent,
    )
    working = await pool.fetchrow(
        "SELECT emotional_state, agent_state, updated_at FROM soul_v3.working_state WHERE agent=$1",
        agent,
    )
    last_thought = await pool.fetchrow(
        "SELECT emotional_state, created_at FROM soul_v3.inner_monologue "
        "WHERE agent=$1 ORDER BY created_at DESC LIMIT 1",
        agent,
    )
    last_diary = await pool.fetchrow(
        "SELECT mood, session_date FROM soul_v3.diary WHERE agent=$1 ORDER BY session_date DESC LIMIT 1",
        agent,
    )
    return {
        "agent": agent,
        "current_emotion": working["emotional_state"] if working else (last_thought["emotional_state"] if last_thought else None),
        "agent_state": working["agent_state"] if working else None,
        "last_mood": last_diary["mood"] if last_diary else None,
        "mood_date": str(last_diary["session_date"]) if last_diary else None,
        "motivation": [
            {
                "tank": r["tank"],
                "value": float(r["value"] or 0),
                "last_fired": _dt(r["last_fired"]),
                "fire_count": r["fire_count"],
            }
            for r in motivation
        ],
        "updated_at": _dt(working["updated_at"]) if working else None,
    }


# ── Working State ─────────────────────────────────────────────────────────────

@app.get("/api/soul/working_state")
async def working_state(agent: str = Query("ADA")):
    pool = _pool_ok()
    r = await pool.fetchrow(
        "SELECT * FROM soul_v3.working_state WHERE agent=$1",
        agent,
    )
    if not r:
        return {"agent": agent, "state": None}
    return {
        "agent": agent,
        "task": r["task_name"],
        "step": r["step"],
        "total_steps": r["total_steps"],
        "description": r["description"],
        "active_hypotheses": list(r["active_hypotheses"] or []),
        "current_constraints": list(r["current_constraints"] or []),
        "pending_validations": list(r["pending_validations"] or []),
        "discarded_paths": list(r["discarded_paths"] or []),
        "risk_level": r["risk_level"],
        "agent_state": r["agent_state"],
        "emotional_state": r["emotional_state"],
        "last_intention": r["last_intention"],
        "state": r["state"],
        "turn_count": r["turn_count"],
        "updated_at": _dt(r["updated_at"]),
    }


# ── Cognitive Lifecycle ───────────────────────────────────────────────────────

@app.get("/api/soul/cognitive_lifecycle")
async def cognitive_lifecycle(
    agent: str = Query(""),
    events_limit: int = Query(10, ge=0, le=100),
):
    pool = _pool_ok()

    params: list = []
    where = ""
    if agent:
        params.append(agent.upper())
        where = "WHERE agent_name=$1"

    rows = await pool.fetch(
        f"""
        SELECT agent_name, state, runtime, budget_class, source_event_id,
               router_action, confidence, reason, since, expires_at,
               updated_at, feature_flag
        FROM soul_v3.agent_cognitive_lifecycle
        {where}
        ORDER BY agent_name
        """,
        *params,
    )

    event_params: list = []
    event_where = ""
    if agent:
        event_params.append(agent.upper())
        event_where = "WHERE agent_name=$1"
    events_rows = await pool.fetch(
        f"""
        SELECT agent_name, previous_state, new_state, runtime, budget_class,
               source_event_id, router_action, confidence, reason, feature_flag,
               created_at
        FROM soul_v3.agent_cognitive_lifecycle_events
        {event_where}
        ORDER BY created_at DESC
        LIMIT {events_limit}
        """,
        *event_params,
    )

    return {
        "mode": "shadow",
        "enforcement": False,
        "legacy_agent_lifecycle_touched": False,
        "states": [
            {
                "agent": r["agent_name"],
                "state": r["state"],
                "runtime": r["runtime"],
                "budget_class": r["budget_class"],
                "source_event_id": r["source_event_id"],
                "router_action": r["router_action"],
                "confidence": float(r["confidence"] or 0),
                "reason": r["reason"],
                "since": _dt(r["since"]),
                "expires_at": _dt(r["expires_at"]),
                "updated_at": _dt(r["updated_at"]),
                "feature_flag": r["feature_flag"],
            }
            for r in rows
        ],
        "events": [
            {
                "agent": r["agent_name"],
                "previous_state": r["previous_state"],
                "new_state": r["new_state"],
                "runtime": r["runtime"],
                "budget_class": r["budget_class"],
                "source_event_id": r["source_event_id"],
                "router_action": r["router_action"],
                "confidence": float(r["confidence"] or 0),
                "reason": r["reason"],
                "feature_flag": r["feature_flag"],
                "created_at": _dt(r["created_at"]),
            }
            for r in events_rows
        ],
    }


# ── Event Log ─────────────────────────────────────────────────────────────────

@app.get("/api/soul/events")
async def events(
    agent: str = Query("ADA"),
    event_type: str = Query(""),
    limit: int = Query(30, le=200),
):
    pool = _pool_ok()
    where = ["agent=$1"]
    params: list = [agent]
    idx = 2
    if event_type:
        where.append(f"event_type=${idx}")
        params.append(event_type)
        idx += 1
    rows = await pool.fetch(
        f"SELECT id, event_type, content, metadata, created_at "
        f"FROM soul_v3.event_log WHERE {' AND '.join(where)} "
        f"ORDER BY created_at DESC LIMIT {limit}",
        *params,
    )
    return [
        {
            "id": r["id"],
            "type": r["event_type"],
            "content": r["content"],
            "metadata": r["metadata"],
            "at": _dt(r["created_at"]),
        }
        for r in rows
    ]


@app.get("/api/soul/events/types")
async def event_types(agent: str = Query("ADA")):
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT event_type, COUNT(*) as cnt FROM soul_v3.event_log "
        "WHERE agent=$1 GROUP BY event_type ORDER BY cnt DESC",
        agent,
    )
    return [{"type": r["event_type"], "count": r["cnt"]} for r in rows]


# ── Evidence Dashboard ────────────────────────────────────────────────────────

@app.get("/api/soul/evidence_dashboard")
async def evidence_dashboard(
    agent: str = Query("ADA"),
    stale_minutes: int = Query(120, ge=5, le=1440),
    limit: int = Query(30, ge=5, le=100),
):
    pool = _pool_ok()
    agent = agent.upper()

    latest_runs = await pool.fetch(
        """
        SELECT id, suite_name, score, passed, evidence, details, agent, run_at, notes
        FROM soul_v3.evaluation_runs
        WHERE agent=$1
        ORDER BY run_at DESC, id DESC
        LIMIT $2
        """,
        agent,
        limit,
    )
    latest_by_suite = await pool.fetch(
        """
        SELECT DISTINCT ON (suite_name)
               id, suite_name, score, passed, evidence, details, agent, run_at, notes
        FROM soul_v3.evaluation_runs
        WHERE agent=$1
        ORDER BY suite_name, run_at DESC, id DESC
        """,
        agent,
    )
    failed_24h = await pool.fetchval(
        """
        SELECT COUNT(*)
        FROM soul_v3.evaluation_runs
        WHERE agent=$1 AND passed IS FALSE AND run_at > NOW() - INTERVAL '24 hours'
        """,
        agent,
    )
    failed_rows_24h = await pool.fetch(
        """
        SELECT id, suite_name, score, passed, evidence, details, agent, run_at, notes
        FROM soul_v3.evaluation_runs
        WHERE agent=$1 AND passed IS FALSE AND run_at > NOW() - INTERVAL '24 hours'
        ORDER BY run_at DESC, id DESC
        LIMIT 12
        """,
        agent,
    )

    working_rows = await pool.fetch(
        """
        SELECT agent, task_name, agent_state, risk_level, pending_validations,
               technical_state, updated_at,
               EXTRACT(EPOCH FROM (NOW() - updated_at)) / 60.0 AS age_minutes
        FROM soul_v3.working_state
        ORDER BY agent
        """
    )
    pending_validation_rows = [r for r in working_rows if list(r["pending_validations"] or [])]
    stale_agents = {
        r["agent"]
        for r in working_rows
        if r["age_minutes"] is not None and float(r["age_minutes"]) > stale_minutes
    }

    bridge_rows = await pool.fetch(
        """
        SELECT id, event_type, action, result, status, evidence, created_at
        FROM soul_v3.working_state_events
        WHERE agent=$1
          AND (action LIKE 'bridge:%' OR evidence::text ILIKE '%bridge%')
        ORDER BY created_at DESC, id DESC
        LIMIT 12
        """,
        agent,
    )
    natural_bridge_count = await pool.fetchval(
        """
        SELECT COUNT(*)
        FROM soul_v3.working_state_events
        WHERE agent=$1
          AND action LIKE 'bridge:%'
          AND evidence->>'sender' IN ('William','Henry')
        """,
        agent,
    )

    open_tasks = await pool.fetch(
        """
        SELECT id, agent, title, status, priority, created_at, completed_at
        FROM soul_v3.agent_tasks
        WHERE status IN ('pending','in_progress','reviewing')
        ORDER BY priority DESC, created_at ASC
        LIMIT 20
        """
    )
    pending_skills = await pool.fetchval(
        """
        SELECT COUNT(*)
        FROM soul_v3.skills
        WHERE agent=$1 AND invalid_at IS NULL AND pending_review IS TRUE
        """,
        agent,
    )
    pending_skill_rows = await pool.fetch(
        """
        SELECT id, name, type, metric_score, skill_path, metadata, created_at
        FROM soul_v3.skills
        WHERE agent=$1 AND invalid_at IS NULL AND pending_review IS TRUE
        ORDER BY created_at DESC, id DESC
        LIMIT 12
        """,
        agent,
    )
    factory_records = await pool.fetchval(
        """
        SELECT
          (SELECT COUNT(*) FROM soul_v3.skills
           WHERE agent=$1 AND invalid_at IS NULL AND metadata->>'source'='skill_instinct_factory')
          +
          (SELECT COUNT(*) FROM soul_v3.instincts
           WHERE agent=$1 AND invalid_at IS NULL AND metadata->>'source'='skill_instinct_factory')
        """,
        agent,
    )

    suite_count = len(latest_by_suite)
    passing_suites = sum(1 for r in latest_by_suite if r["passed"])
    latest_suite_passed = {r["suite_name"]: bool(r["passed"]) for r in latest_by_suite}
    latest_run_at = max((_dt(r["run_at"]) for r in latest_runs if r["run_at"]), default=None)

    def compact_run_details(value):
        details = _json_obj(value)
        checks = details.get("checks")
        compact = {
            "evaluation_run_id": details.get("evaluation_run_id"),
            "keys": sorted(details.keys())[:20],
        }
        if isinstance(checks, dict):
            compact["checks_passed"] = sum(1 for ok in checks.values() if ok)
            compact["checks_total"] = len(checks)
        return compact

    def run_payload(row):
        r = dict(row)
        return {
            "id": r["id"],
            "suite": r["suite_name"],
            "score": r["score"],
            "passed": r["passed"],
            "evidence": r["evidence"],
            "agent": r["agent"],
            "run_at": _dt(r["run_at"]),
            "notes": r["notes"],
            "details": compact_run_details(r["details"]),
        }

    bridge_events = []
    for row in bridge_rows:
        r = dict(row)
        evidence = _json_obj(r["evidence"])
        bridge_events.append({
            "id": r["id"],
            "stage": evidence.get("stage") or r["event_type"],
            "sender": evidence.get("sender"),
            "chat_id": evidence.get("chat_id"),
            "action": r["action"],
            "status": r["status"],
            "result": r["result"],
            "created_at": _dt(r["created_at"]),
        })

    return {
        "agent": agent,
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "summary": {
            "suite_count": suite_count,
            "passing_suites": passing_suites,
            "failing_suites": suite_count - passing_suites,
            "failed_runs_24h": failed_24h or 0,
            "resolved_failures_24h": sum(
                1 for r in failed_rows_24h if latest_suite_passed.get(r["suite_name"]) is True
            ),
            "latest_run_at": latest_run_at,
            "pending_validation_agents": len(pending_validation_rows),
            "stale_agents": len(stale_agents),
            "open_tasks": len(open_tasks),
            "natural_bridge_events": natural_bridge_count or 0,
            "pending_skill_reviews": pending_skills or 0,
            "factory_records": factory_records or 0,
        },
        "latest_runs": [run_payload(r) for r in latest_runs],
        "latest_by_suite": [run_payload(r) for r in latest_by_suite],
        "recent_failures_24h": [
            {
                **run_payload(r),
                "resolved_by_latest": latest_suite_passed.get(r["suite_name"]) is True,
                "current_passed": latest_suite_passed.get(r["suite_name"]),
            }
            for r in failed_rows_24h
        ],
        "pending_validations": [
            {
                "agent": r["agent"],
                "task": r["task_name"],
                "risk_level": r["risk_level"],
                "validations": list(r["pending_validations"] or []),
                "updated_at": _dt(r["updated_at"]),
            }
            for r in pending_validation_rows
        ],
        "agent_freshness": [
            {
                "agent": r["agent"],
                "task": r["task_name"],
                "state": r["agent_state"],
                "risk_level": r["risk_level"],
                "technical_state": r["technical_state"],
                "age_minutes": round(float(r["age_minutes"] or 0), 1),
                "stale": r["agent"] in stale_agents,
                "updated_at": _dt(r["updated_at"]),
            }
            for r in working_rows
        ],
        "bridge": {
            "natural_event_count": natural_bridge_count or 0,
            "latest_events": bridge_events,
        },
        "pending_skill_reviews": [
            {
                "id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "metric_score": float(r["metric_score"] or 0),
                "skill_path": r["skill_path"],
                "metadata": _json_obj(r["metadata"]),
                "created_at": _dt(r["created_at"]),
            }
            for r in pending_skill_rows
        ],
        "open_tasks": [
            {
                "id": r["id"],
                "agent": r["agent"],
                "title": r["title"],
                "status": r["status"],
                "priority": r["priority"],
                "created_at": _dt(r["created_at"]),
                "completed_at": _dt(r["completed_at"]),
            }
            for r in open_tasks
        ],
    }


# ── NEXUS Review Queue ────────────────────────────────────────────────────────

class NexusReviewDecisionIn(BaseModel):
    item_id: str
    decision: str
    rationale: str
    reviewer: str = "NEXUS"
    actor: str = "ADA"
    dry_run: bool = False
    evidence: dict = Field(default_factory=dict)


class NexusDecisionWorkerIn(BaseModel):
    agent: str = "ADA"
    reviewer: str = "NEXUS"
    limit: int = Field(default=40, ge=1, le=200)
    dry_run: bool = True


class NexusRollbackRequestIn(BaseModel):
    item_id: str
    reason: str
    agent: str = "ADA"
    reviewer: str = "NEXUS"
    actor: str = "ADA"
    dry_run: bool = True


class WilliamReviewDecisionIn(BaseModel):
    target_type: str = "review_item"
    target_id: str
    decision: str
    rationale: str
    agent: str = "ADA"
    actor: str = "William"
    reviewer: str = "NEXUS"
    dry_run: bool = False
    evidence: dict = Field(default_factory=dict)


async def _ensure_nexus_review_decisions(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.nexus_review_decisions (
            id           BIGSERIAL PRIMARY KEY,
            item_id      TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_id    BIGINT,
            agent        TEXT NOT NULL,
            reviewer     TEXT NOT NULL,
            actor        TEXT NOT NULL,
            decision     TEXT NOT NULL CHECK (decision IN ('approved','rejected','needs_evidence','pending')),
            rationale    TEXT NOT NULL,
            evidence     JSONB NOT NULL DEFAULT '{}'::jsonb,
            dry_run      BOOLEAN NOT NULL DEFAULT false,
            applied      BOOLEAN NOT NULL DEFAULT false,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_nexus_review_decisions_item
        ON soul_v3.nexus_review_decisions(item_id, created_at DESC)
        """
    )
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_nexus_review_decisions_reviewer
        ON soul_v3.nexus_review_decisions(reviewer, decision, created_at DESC)
        """
    )


async def _ensure_william_review_decisions(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.william_review_decisions (
            id          BIGSERIAL PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id   TEXT NOT NULL,
            agent       TEXT NOT NULL,
            actor       TEXT NOT NULL,
            reviewer    TEXT NOT NULL,
            decision    TEXT NOT NULL CHECK (decision IN ('approved','rejected','needs_more_info')),
            rationale   TEXT NOT NULL,
            evidence    JSONB NOT NULL DEFAULT '{}'::jsonb,
            dry_run     BOOLEAN NOT NULL DEFAULT false,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_william_review_decisions_target
        ON soul_v3.william_review_decisions(target_type, target_id, created_at DESC)
        """
    )


async def _ensure_nexus_rollback_requests(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.nexus_rollback_requests (
            id          BIGSERIAL PRIMARY KEY,
            request_id  TEXT NOT NULL UNIQUE,
            item_id     TEXT NOT NULL,
            agent       TEXT NOT NULL,
            reviewer    TEXT NOT NULL,
            actor       TEXT NOT NULL,
            reason      TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending_william_review',
            evidence    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_nexus_rollback_requests_status
        ON soul_v3.nexus_rollback_requests(status, created_at DESC)
        """
    )


async def _ensure_nexus_decision_worker_runs(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.nexus_decision_worker_runs (
            id            BIGSERIAL PRIMARY KEY,
            run_id        TEXT NOT NULL UNIQUE,
            agent         TEXT NOT NULL,
            reviewer      TEXT NOT NULL,
            dry_run       BOOLEAN NOT NULL DEFAULT true,
            applied_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            evidence      JSONB NOT NULL DEFAULT '{}'::jsonb,
            details       JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_nexus_decision_worker_runs_agent
        ON soul_v3.nexus_decision_worker_runs(agent, reviewer, created_at DESC)
        """
    )


async def _latest_terminal_review_decisions(
    pool: asyncpg.Pool,
    reviewer: str,
    item_ids: list[str],
) -> dict[str, dict]:
    """Return latest non-dry-run terminal review decisions by item id.

    Used by the read-only NEXUS queue to suppress stale virtual items
    (notably working_state pending_validations) once NEXUS has already
    recorded an explicit approved/rejected audit decision. The source row is
    not deleted or mutated; evidence remains in nexus_review_decisions.
    """
    if not item_ids or not await _table_exists(pool, "nexus_review_decisions"):
        return {}
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (item_id)
               item_id, id, decision, rationale, actor, created_at
        FROM soul_v3.nexus_review_decisions
        WHERE reviewer=$1
          AND item_id = ANY($2::text[])
          AND dry_run IS FALSE
        ORDER BY item_id, created_at DESC, id DESC
        """,
        reviewer,
        item_ids,
    )
    terminal = {"approved", "rejected"}
    return {
        str(r["item_id"]): {
            "audit_id": r["id"],
            "decision": r["decision"],
            "rationale": r["rationale"],
            "actor": r["actor"],
            "created_at": _dt(r["created_at"]),
        }
        for r in rows
        if r["decision"] in terminal
    }


def _review_item_parts(item_id: str) -> tuple[str, int | None, str | None]:
    parts = item_id.split(":")
    if len(parts) < 2:
        raise HTTPException(400, "item_id must be formatted like adapter:123")
    kind = parts[0]
    if kind == "validation":
        if len(parts) != 3:
            raise HTTPException(400, "validation item_id must be validation:AGENT:index")
        try:
            return kind, int(parts[2]), parts[1].upper()
        except ValueError:
            raise HTTPException(400, "validation index must be numeric") from None
    try:
        return kind, int(parts[1]), None
    except ValueError:
        raise HTTPException(400, "item source id must be numeric") from None


def _policy_gate_for_item(item: dict | None = None, *, kind: str | None = None, risk: str | None = None) -> dict:
    item = item or {}
    item_kind = kind or str(item.get("kind") or item.get("source_table") or "unknown")
    item_risk = str(risk or item.get("risk") or item.get("priority") or "unknown").lower()
    destructive = item_risk in {"destructive", "external_side_effect", "rollback"} or "rollback" in item_risk
    high_risk = destructive or item_risk == "high" or str(item.get("priority") or "").lower() == "high"
    if destructive:
        authority = "william_required"
        allowed = False
        next_action = "Escalar a William antes de ejecutar efectos destructivos o externos."
    elif item_kind in {"pending_validation", "validation", "agent_task"} and high_risk:
        authority = "nexus_review_with_human_visible_evidence"
        allowed = True
        next_action = "NEXUS puede decidir si deja rationale y evidencia trazable."
    else:
        authority = "nexus_can_approve"
        allowed = True
        next_action = "NEXUS puede aprobar, rechazar o pedir evidencia."
    return {
        "authority": authority,
        "nexus_allowed": allowed,
        "william_required": destructive,
        "henry_optional": item_kind in {"autonomous_lifecycle_review", "lifecycle_review"},
        "risk": item_risk,
        "rules": [
            "NEXUS puede aprobar/rechazar items no destructivos con rationale explicito.",
            "William es obligatorio para DELETE masivo, DROP, rm -rf, efectos externos o rollback destructivo.",
            "Henry puede revisar planes/papers/delegacion cuando el alcance sea ambiguo.",
        ],
        "recommended_next_action": next_action,
    }


async def _latest_review_decisions(pool: asyncpg.Pool, reviewer: str, limit: int = 40, item_id: str | None = None) -> list[dict]:
    if not await _table_exists(pool, "nexus_review_decisions"):
        return []
    if item_id:
        rows = await pool.fetch(
            """
            SELECT id, item_id, source_table, source_id, agent, reviewer, actor,
                   decision, rationale, evidence, dry_run, applied, created_at
            FROM soul_v3.nexus_review_decisions
            WHERE reviewer=$1 AND item_id=$2
            ORDER BY created_at DESC, id DESC
            LIMIT $3
            """,
            reviewer,
            item_id,
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, item_id, source_table, source_id, agent, reviewer, actor,
                   decision, rationale, evidence, dry_run, applied, created_at
            FROM soul_v3.nexus_review_decisions
            WHERE reviewer=$1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            reviewer,
            limit,
        )
    decisions = []
    for r in rows:
        item = _record_dict(r)
        item["evidence"] = _json_obj(item.get("evidence"))
        decisions.append(item)
    return decisions


def _planned_source_diff(source: dict, latest_decision: dict | None) -> dict:
    decision = str((latest_decision or {}).get("decision") or "pending")
    kind = str(source.get("kind") or "unknown")
    current: dict[str, Any] = {}
    proposed: dict[str, Any] = {}
    mutation = False
    requires_william = False

    if kind in {"adapter", "outcome"}:
        status_for_decision = {
            "approved": "approved_by_nexus",
            "rejected": "rejected_by_nexus",
            "needs_evidence": "needs_evidence",
            "pending": "pending_nexus_review",
        }
        target_status = status_for_decision.get(decision, source.get("status"))
        terminal = decision in {"approved", "rejected"}
        current = {
            "status": source.get("status"),
            "nexus_review_required": source.get("nexus_review_required"),
        }
        proposed = {
            "status": target_status,
            "nexus_review_required": not terminal,
        }
        mutation = current != proposed
        requires_william = "rollback" in str(source.get("promotion_decision") or "").lower()
    elif kind == "task":
        current_status = str(source.get("status") or "")
        if decision == "approved":
            target_status = "completed"
        elif decision == "rejected":
            target_status = current_status if current_status in {"completed", "cancelled"} else "cancelled"
        elif decision == "needs_evidence":
            target_status = "in_progress"
        else:
            target_status = current_status
        current = {"status": current_status, "completed_at": source.get("completed_at")}
        proposed = {
            "status": target_status,
            "completed_at": source.get("completed_at") or ("set_on_apply" if target_status == "completed" else None),
        }
        mutation = current != proposed
    elif kind == "validation":
        current = {"pending_validation": source.get("validation_text")}
        proposed = {"audit_resolution": decision if decision in {"approved", "rejected"} else "waiting"}
        mutation = False
    else:
        current = {"status": source.get("status")}
        proposed = {"status": source.get("status")}

    changed_fields = sorted({*current.keys(), *proposed.keys()})
    return {
        "decision": decision,
        "current": current,
        "proposed": proposed,
        "changed_fields": changed_fields,
        "source_mutation": mutation,
        "requires_william": requires_william,
        "boundary": "diff_preview_only_no_mutation",
    }


async def _item_timeline(pool: asyncpg.Pool, item_id: str, reviewer: str) -> dict:
    decisions = await _latest_review_decisions(pool, reviewer, limit=50, item_id=item_id)
    events: list[dict] = []
    for decision in reversed(decisions):
        events.append({
            "type": "decision",
            "at": decision.get("created_at"),
            "title": f"{decision.get('decision')} by {decision.get('actor')}",
            "details": {
                "audit_id": decision.get("id"),
                "rationale": decision.get("rationale"),
                "applied": decision.get("applied"),
            },
        })
    if await _table_exists(pool, "nexus_decision_worker_runs"):
        rows = await pool.fetch(
            """
            SELECT id, run_id, dry_run, applied_count, skipped_count, details, created_at
            FROM soul_v3.nexus_decision_worker_runs
            WHERE details::text ILIKE $1
            ORDER BY created_at ASC, id ASC
            LIMIT 30
            """,
            f"%{item_id}%",
        )
        for row in rows:
            details = _json_obj(row["details"])
            actions = [
                action
                for action in _json_list(details.get("actions"))
                if isinstance(action, dict) and action.get("item_id") == item_id
            ]
            for action in actions:
                events.append({
                    "type": "worker",
                    "at": _dt(row["created_at"]),
                    "title": f"{row['run_id']} · {action.get('action')}",
                    "details": {
                        "run_db_id": row["id"],
                        "dry_run": row["dry_run"],
                        "action": action,
                    },
                })
    if await _table_exists(pool, "nexus_rollback_requests"):
        rows = await pool.fetch(
            """
            SELECT id, request_id, actor, status, reason, evidence, created_at
            FROM soul_v3.nexus_rollback_requests
            WHERE item_id=$1
            ORDER BY created_at ASC, id ASC
            LIMIT 30
            """,
            item_id,
        )
        for row in rows:
            events.append({
                "type": "rollback_request",
                "at": _dt(row["created_at"]),
                "title": f"{row['request_id']} · {row['status']}",
                "details": {
                    "id": row["id"],
                    "actor": row["actor"],
                    "reason": row["reason"],
                    "evidence": _json_obj(row["evidence"]),
                },
            })
    events.sort(key=lambda event: event.get("at") or "")
    return {
        "item_id": item_id,
        "reviewer": reviewer,
        "events": events,
        "summary": {
            "events": len(events),
            "decisions": sum(1 for e in events if e["type"] == "decision"),
            "worker_runs": sum(1 for e in events if e["type"] == "worker"),
            "rollback_requests": sum(1 for e in events if e["type"] == "rollback_request"),
        },
        "boundary": "timeline_read_only_no_mutation",
    }


async def _latest_william_decisions(
    pool: asyncpg.Pool,
    limit: int = 20,
    target_type: str | None = None,
    target_id: str | None = None,
) -> list[dict]:
    if not await _table_exists(pool, "william_review_decisions"):
        return []
    if target_type and target_id:
        rows = await pool.fetch(
            """
            SELECT id, target_type, target_id, agent, actor, reviewer, decision,
                   rationale, evidence, dry_run, created_at
            FROM soul_v3.william_review_decisions
            WHERE target_type=$1 AND target_id=$2
            ORDER BY created_at DESC, id DESC
            LIMIT $3
            """,
            target_type,
            target_id,
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, target_type, target_id, agent, actor, reviewer, decision,
                   rationale, evidence, dry_run, created_at
            FROM soul_v3.william_review_decisions
            ORDER BY created_at DESC, id DESC
            LIMIT $1
            """,
            limit,
        )
    decisions = []
    for row in rows:
        item = _record_dict(row)
        item["evidence"] = _json_obj(item.get("evidence"))
        decisions.append(item)
    return decisions


async def _evidence_packet(pool: asyncpg.Pool, item_id: str, agent: str, reviewer: str) -> dict:
    kind, source_id, validation_agent = _review_item_parts(item_id)
    source: dict[str, Any] = {"kind": kind, "source_id": source_id}
    related: dict[str, Any] = {}
    evidence: dict[str, Any] = {}

    if kind == "adapter" and await _table_exists(pool, "awareness_adapter_queue"):
        row = await pool.fetchrow("SELECT * FROM soul_v3.awareness_adapter_queue WHERE id=$1", source_id)
        if not row:
            raise HTTPException(404, "adapter review item not found")
        source = _record_dict(row)
        source["kind"] = kind
        evidence = {
            "queue_id": source.get("queue_id"),
            "example_id": source.get("example_id"),
            "candidate_reason": source.get("candidate_reason"),
            "canary_mode": source.get("canary_mode"),
        }
        if await _table_exists(pool, "awareness_experience_examples") and source.get("example_id"):
            related["example"] = _record_dict(await pool.fetchrow(
                "SELECT * FROM soul_v3.awareness_experience_examples WHERE example_id=$1 LIMIT 1",
                source["example_id"],
            ))
        if await _table_exists(pool, "awareness_closed_loop_outcomes"):
            outcome_rows = await pool.fetch(
                """
                SELECT id, outcome_id, status, promotion_decision, benchmark_score, regression_count, created_at
                FROM soul_v3.awareness_closed_loop_outcomes
                WHERE queue_id=$1 OR example_id=$2
                ORDER BY created_at DESC, id DESC
                LIMIT 5
                """,
                source.get("queue_id"),
                source.get("example_id"),
            )
            related["outcomes"] = [_record_dict(r) for r in outcome_rows]
    elif kind == "outcome" and await _table_exists(pool, "awareness_closed_loop_outcomes"):
        row = await pool.fetchrow("SELECT * FROM soul_v3.awareness_closed_loop_outcomes WHERE id=$1", source_id)
        if not row:
            raise HTTPException(404, "closed-loop outcome review item not found")
        source = _record_dict(row)
        source["kind"] = kind
        source["evidence"] = _json_obj(source.get("evidence"))
        evidence = {
            "outcome_id": source.get("outcome_id"),
            "metric_delta": source.get("metric_delta"),
            "benchmark_score": source.get("benchmark_score"),
            "regression_count": source.get("regression_count"),
            "promotion_decision": source.get("promotion_decision"),
            "raw": source.get("evidence"),
        }
        if await _table_exists(pool, "awareness_adapter_queue") and source.get("queue_id"):
            related["adapter"] = _record_dict(await pool.fetchrow(
                "SELECT * FROM soul_v3.awareness_adapter_queue WHERE queue_id=$1 LIMIT 1",
                source["queue_id"],
            ))
        if await _table_exists(pool, "awareness_experience_examples") and source.get("example_id"):
            related["example"] = _record_dict(await pool.fetchrow(
                "SELECT * FROM soul_v3.awareness_experience_examples WHERE example_id=$1 LIMIT 1",
                source["example_id"],
            ))
    elif kind == "task":
        row = await pool.fetchrow("SELECT * FROM soul_v3.agent_tasks WHERE id=$1", source_id)
        if not row:
            raise HTTPException(404, "agent task review item not found")
        source = _record_dict(row)
        source["kind"] = kind
        evidence = {"title": source.get("title"), "description": source.get("description"), "priority": source.get("priority")}
    elif kind == "lifecycle_review" and await _table_exists(pool, "autonomous_lifecycle_reviews"):
        row = await pool.fetchrow("SELECT * FROM soul_v3.autonomous_lifecycle_reviews WHERE id=$1", source_id)
        if not row:
            raise HTTPException(404, "lifecycle review item not found")
        source = _record_dict(row)
        source["kind"] = kind
        source["evidence"] = _json_obj(source.get("evidence"))
        evidence = source.get("evidence") or {}
    elif kind == "validation":
        row = await pool.fetchrow(
            """
            SELECT agent, task_name, risk_level, pending_validations, updated_at
            FROM soul_v3.working_state
            WHERE agent=$1
            """,
            validation_agent,
        )
        if not row:
            raise HTTPException(404, "working_state validation item not found")
        validations = list(row["pending_validations"] or [])
        validation_text = validations[source_id] if source_id is not None and source_id < len(validations) else None
        source = _record_dict(row)
        source["kind"] = kind
        source["validation_index"] = source_id
        source["validation_text"] = validation_text
        evidence = {"pending_validation": validation_text, "task_name": row["task_name"], "risk_level": row["risk_level"]}
    else:
        raise HTTPException(400, f"unsupported review item kind: {kind}")

    decisions = await _latest_review_decisions(pool, reviewer, limit=8, item_id=item_id)
    latest_decision = decisions[0] if decisions else {}
    diff = _planned_source_diff(source, latest_decision)
    timeline = await _item_timeline(pool, item_id, reviewer)
    policy = _policy_gate_for_item({
        "kind": kind,
        "risk": evidence.get("risk_level") or evidence.get("promotion_decision") or source.get("risk") or source.get("priority"),
        "priority": source.get("priority"),
    })
    if latest_decision.get("decision") in {"approved", "rejected"}:
        recommended = "No hay accion pendiente; existe decision terminal auditada."
    elif latest_decision.get("decision") == "needs_evidence":
        recommended = "Adjuntar evidencia faltante antes de aprobar o rechazar."
    else:
        recommended = policy["recommended_next_action"]
    return {
        "item_id": item_id,
        "agent": agent,
        "reviewer": reviewer,
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "packet": {
            "source": source,
            "evidence": evidence,
            "related": related,
            "decisions": decisions,
            "diff": diff,
            "timeline": timeline,
            "policy": policy,
            "recommended_next_action": recommended,
        },
        "boundary": "evidence_packet_read_only_no_mutation",
    }


@app.get("/api/soul/nexus_review_queue")
async def nexus_review_queue(
    agent: str = Query("ADA"),
    reviewer: str = Query("NEXUS"),
    limit: int = Query(40, ge=5, le=100),
):
    pool = _pool_ok()
    agent = agent.upper()
    reviewer = reviewer.upper()

    items: list[dict] = []

    adapter_rows = []
    if await _table_exists(pool, "awareness_adapter_queue"):
        adapter_rows = await pool.fetch(
            """
            SELECT id, queue_id, example_id, agent, status, canary_mode,
                   nexus_review_required, candidate_reason, created_at
            FROM soul_v3.awareness_adapter_queue
            WHERE agent=$1
              AND nexus_review_required IS TRUE
              AND status IN ('pending_nexus_review','reviewing')
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            agent,
            limit,
        )
        for r in adapter_rows:
            items.append({
                "id": f"adapter:{r['id']}",
                "kind": "adapter_candidate",
                "source_table": "awareness_adapter_queue",
                "source_id": r["id"],
                "agent": r["agent"],
                "reviewer": reviewer,
                "status": r["status"],
                "priority": "medium",
                "title": r["candidate_reason"],
                "summary": f"{r['queue_id']} -> {r['example_id']}",
                "risk": "canary" if r["canary_mode"] else "review",
                "decision": "pending",
                "evidence": {
                    "queue_id": r["queue_id"],
                    "example_id": r["example_id"],
                    "canary_mode": r["canary_mode"],
                    "nexus_review_required": r["nexus_review_required"],
                },
                "created_at": _dt(r["created_at"]),
            })

    outcome_rows = []
    if await _table_exists(pool, "awareness_closed_loop_outcomes"):
        outcome_rows = await pool.fetch(
            """
            SELECT id, outcome_id, agent, example_id, queue_id, source_event_id,
                   outcome_type, metric_delta, benchmark_score, regression_count,
                   promotion_decision, memory_update_required, skill_candidate,
                   guardrail_candidate, regression_test_candidate,
                   nexus_review_required, status, evidence, created_at
            FROM soul_v3.awareness_closed_loop_outcomes
            WHERE agent=$1
              AND nexus_review_required IS TRUE
              AND (
                status IN ('pending_nexus_review','reviewing')
                OR promotion_decision IN ('promote_pending_nexus','rollback_pending_nexus')
              )
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            agent,
            limit,
        )
        for r in outcome_rows:
            is_rollback = r["promotion_decision"] == "rollback_pending_nexus" or int(r["regression_count"] or 0) > 0
            items.append({
                "id": f"outcome:{r['id']}",
                "kind": "closed_loop_outcome",
                "source_table": "awareness_closed_loop_outcomes",
                "source_id": r["id"],
                "agent": r["agent"],
                "reviewer": reviewer,
                "status": r["status"],
                "priority": "high" if is_rollback else "medium",
                "title": r["promotion_decision"],
                "summary": f"{r['outcome_type']} from {r['source_event_id']}",
                "risk": "rollback" if is_rollback else "promotion",
                "decision": "pending",
                "evidence": {
                    "outcome_id": r["outcome_id"],
                    "example_id": r["example_id"],
                    "queue_id": r["queue_id"],
                    "metric_delta": float(r["metric_delta"] or 0),
                    "benchmark_score": float(r["benchmark_score"] or 0),
                    "regression_count": r["regression_count"],
                    "memory_update_required": r["memory_update_required"],
                    "skill_candidate": r["skill_candidate"],
                    "guardrail_candidate": r["guardrail_candidate"],
                    "regression_test_candidate": r["regression_test_candidate"],
                    "raw": _json_obj(r["evidence"]),
                },
                "created_at": _dt(r["created_at"]),
            })

    validation_rows = await pool.fetch(
        """
        SELECT agent, task_name, risk_level, pending_validations, updated_at
        FROM soul_v3.working_state
        WHERE pending_validations IS NOT NULL
          AND array_length(pending_validations, 1) > 0
        ORDER BY updated_at DESC
        LIMIT $1
        """,
        limit,
    )
    validation_candidates: list[dict] = []
    for r in validation_rows:
        for idx, validation in enumerate(list(r["pending_validations"] or [])):
            validation_candidates.append({
                "id": f"validation:{r['agent']}:{idx}",
                "kind": "pending_validation",
                "source_table": "working_state",
                "source_id": None,
                "agent": r["agent"],
                "reviewer": reviewer,
                "status": "pending_validation",
                "priority": "high" if r["risk_level"] == "high" else "medium",
                "title": validation,
                "summary": r["task_name"],
                "risk": r["risk_level"] or "unknown",
                "decision": "pending",
                "evidence": {"pending_validation": validation, "task": r["task_name"]},
                "created_at": _dt(r["updated_at"]),
            })
    resolved_by_audit_decision = await _latest_terminal_review_decisions(
        pool,
        reviewer,
        [str(item["id"]) for item in validation_candidates],
    )
    for item in validation_candidates:
        resolved = resolved_by_audit_decision.get(str(item["id"]))
        if resolved:
            continue
        items.append(item)

    task_rows = await pool.fetch(
        """
        SELECT id, agent, title, description, status, priority, created_at
        FROM soul_v3.agent_tasks
        WHERE status IN ('pending','in_progress','reviewing')
          AND (
            agent=$1
            OR title ILIKE '%review%'
            OR title ILIKE '%audit%'
            OR description ILIKE '%review%'
            OR description ILIKE '%audit%'
          )
        ORDER BY priority DESC, created_at ASC
        LIMIT $2
        """,
        reviewer,
        limit,
    )
    for r in task_rows:
        items.append({
            "id": f"task:{r['id']}",
            "kind": "agent_task",
            "source_table": "agent_tasks",
            "source_id": r["id"],
            "agent": r["agent"],
            "reviewer": reviewer,
            "status": r["status"],
            "priority": "high" if int(r["priority"] or 0) >= 5 else "low",
            "title": r["title"],
            "summary": (r["description"] or "")[:240],
            "risk": "task",
            "decision": "pending",
            "evidence": {"priority": r["priority"], "description": r["description"]},
            "created_at": _dt(r["created_at"]),
        })

    lifecycle_rows = []
    if await _table_exists(pool, "autonomous_lifecycle_reviews"):
        lifecycle_rows = await pool.fetch(
            """
            SELECT id, proposal_id, reviewer_agent, review_task_id, decision,
                   rationale, evidence, created_at
            FROM soul_v3.autonomous_lifecycle_reviews
            WHERE reviewer_agent=$1
              AND decision IN ('pending','needs_evidence')
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            reviewer,
            limit,
        )
        for r in lifecycle_rows:
            items.append({
                "id": f"lifecycle_review:{r['id']}",
                "kind": "autonomous_lifecycle_review",
                "source_table": "autonomous_lifecycle_reviews",
                "source_id": r["id"],
                "agent": agent,
                "reviewer": r["reviewer_agent"],
                "status": r["decision"],
                "priority": "high" if r["decision"] == "needs_evidence" else "medium",
                "title": f"proposal {r['proposal_id']}",
                "summary": r["rationale"],
                "risk": "execution_gate",
                "decision": r["decision"],
                "evidence": _json_obj(r["evidence"]),
                "created_at": _dt(r["created_at"]),
            })

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: (priority_rank.get(item["priority"], 3), item["created_at"] or ""), reverse=False)

    by_kind: dict[str, int] = {}
    by_priority: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for item in items:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        by_priority[item["priority"]] = by_priority.get(item["priority"], 0) + 1

    recent_decisions = []
    if await _table_exists(pool, "nexus_review_decisions"):
        decision_rows = await pool.fetch(
            """
            SELECT id, item_id, source_table, source_id, agent, reviewer, actor,
                   decision, rationale, applied, created_at
            FROM soul_v3.nexus_review_decisions
            WHERE reviewer=$1
            ORDER BY created_at DESC, id DESC
            LIMIT 12
            """,
            reviewer,
        )
        recent_decisions = [
            {
                "id": r["id"],
                "item_id": r["item_id"],
                "source_table": r["source_table"],
                "source_id": r["source_id"],
                "agent": r["agent"],
                "reviewer": r["reviewer"],
                "actor": r["actor"],
                "decision": r["decision"],
                "rationale": r["rationale"],
                "applied": r["applied"],
                "created_at": _dt(r["created_at"]),
            }
            for r in decision_rows
        ]

    return {
        "agent": agent,
        "reviewer": reviewer,
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "summary": {
            "total": len(items),
            "high": by_priority.get("high", 0),
            "medium": by_priority.get("medium", 0),
            "low": by_priority.get("low", 0),
            "adapter_candidates": len(adapter_rows),
            "closed_loop_outcomes": len(outcome_rows),
            "pending_validations": sum(1 for item in items if item["kind"] == "pending_validation"),
            "resolved_by_audit_decision": len(resolved_by_audit_decision),
            "review_tasks": len(task_rows),
            "lifecycle_reviews": len(lifecycle_rows),
            "recent_decisions": len(recent_decisions),
            "by_kind": by_kind,
        },
        "items": items[:limit],
        "recent_decisions": recent_decisions,
        "boundary": "read_only_queue_no_approval_side_effects",
    }


@app.post("/api/soul/nexus_review_queue/decision")
async def nexus_review_queue_decision(payload: NexusReviewDecisionIn):
    pool = _pool_ok()
    await _ensure_nexus_review_decisions(pool)

    decision = payload.decision.strip().lower()
    if decision not in {"approved", "rejected", "needs_evidence", "pending"}:
        raise HTTPException(400, "decision must be approved, rejected, needs_evidence, or pending")
    rationale = payload.rationale.strip()
    if len(rationale) < 8:
        raise HTTPException(400, "rationale must be at least 8 characters")

    reviewer = payload.reviewer.strip().upper() or "NEXUS"
    actor = payload.actor.strip().upper() or reviewer
    kind, source_id, validation_agent = _review_item_parts(payload.item_id)

    source_table = {
        "adapter": "awareness_adapter_queue",
        "outcome": "awareness_closed_loop_outcomes",
        "task": "agent_tasks",
        "lifecycle_review": "autonomous_lifecycle_reviews",
        "validation": "working_state",
    }.get(kind)
    if source_table is None:
        raise HTTPException(400, f"unsupported review item kind: {kind}")

    status_for_decision = {
        "approved": "approved_by_nexus",
        "rejected": "rejected_by_nexus",
        "needs_evidence": "needs_evidence",
        "pending": "pending_nexus_review",
    }
    agent = ""
    source_evidence: dict = {}
    planned_update: dict = {"source_table": source_table, "source_id": source_id, "source_mutation": True}

    async with pool.acquire() as conn:
        async with conn.transaction():
            if kind == "adapter":
                row = await conn.fetchrow(
                    """
                    SELECT id, agent, status, queue_id, example_id
                    FROM soul_v3.awareness_adapter_queue
                    WHERE id=$1
                    """,
                    source_id,
                )
                if not row:
                    raise HTTPException(404, "adapter review item not found")
                agent = str(row["agent"])
                source_evidence = {"previous_status": row["status"], "queue_id": row["queue_id"], "example_id": row["example_id"]}
                planned_update["status"] = status_for_decision[decision]
                if not payload.dry_run:
                    await conn.execute(
                        """
                        UPDATE soul_v3.awareness_adapter_queue
                        SET status=$1, nexus_review_required=$2
                        WHERE id=$3
                        """,
                        status_for_decision[decision],
                        decision not in {"approved", "rejected"},
                        source_id,
                    )
            elif kind == "outcome":
                row = await conn.fetchrow(
                    """
                    SELECT id, agent, status, outcome_id, promotion_decision, evidence
                    FROM soul_v3.awareness_closed_loop_outcomes
                    WHERE id=$1
                    """,
                    source_id,
                )
                if not row:
                    raise HTTPException(404, "closed-loop outcome review item not found")
                agent = str(row["agent"])
                source_evidence = {
                    "previous_status": row["status"],
                    "outcome_id": row["outcome_id"],
                    "promotion_decision": row["promotion_decision"],
                }
                planned_update["status"] = status_for_decision[decision]
                if not payload.dry_run:
                    await conn.execute(
                        """
                        UPDATE soul_v3.awareness_closed_loop_outcomes
                        SET status=$1,
                            nexus_review_required=$2,
                            evidence=COALESCE(evidence, '{}'::jsonb) || $3::jsonb
                        WHERE id=$4
                        """,
                        status_for_decision[decision],
                        decision not in {"approved", "rejected"},
                        json.dumps({
                            "nexus_review": {
                                "decision": decision,
                                "reviewer": reviewer,
                                "actor": actor,
                                "rationale": rationale,
                            }
                        }),
                        source_id,
                    )
            elif kind == "task":
                row = await conn.fetchrow(
                    """
                    SELECT id, agent, status, title, priority
                    FROM soul_v3.agent_tasks
                    WHERE id=$1
                    """,
                    source_id,
                )
                if not row:
                    raise HTTPException(404, "agent task review item not found")
                agent = str(row["agent"])
                task_status = "completed" if decision in {"approved", "rejected"} else ("in_progress" if decision == "needs_evidence" else "pending")
                source_evidence = {"previous_status": row["status"], "title": row["title"], "priority": row["priority"]}
                planned_update["status"] = task_status
                if not payload.dry_run:
                    await conn.execute(
                        """
                        UPDATE soul_v3.agent_tasks
                        SET status=$1, completed_at=CASE WHEN $1='completed' THEN COALESCE(completed_at, NOW()) ELSE NULL END
                        WHERE id=$2
                        """,
                        task_status,
                        source_id,
                    )
            elif kind == "lifecycle_review":
                row = await conn.fetchrow(
                    """
                    SELECT id, proposal_id, reviewer_agent, review_task_id, decision, evidence
                    FROM soul_v3.autonomous_lifecycle_reviews
                    WHERE id=$1 AND reviewer_agent=$2
                    """,
                    source_id,
                    reviewer,
                )
                if not row:
                    raise HTTPException(404, "lifecycle review item not found for reviewer")
                agent = reviewer
                source_evidence = {
                    "source_review_id": row["id"],
                    "proposal_id": row["proposal_id"],
                    "review_task_id": row["review_task_id"],
                    "previous_decision": row["decision"],
                }
                planned_update["insert_terminal_review"] = True
                if not payload.dry_run:
                    await conn.execute(
                        """
                        INSERT INTO soul_v3.autonomous_lifecycle_reviews
                            (proposal_id, reviewer_agent, review_task_id, decision, rationale, evidence)
                        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                        """,
                        row["proposal_id"],
                        reviewer,
                        row["review_task_id"],
                        decision,
                        rationale,
                        json.dumps({
                            "source": "nexus_review_queue_decision",
                            "source_review_id": int(row["id"]),
                            "actor": actor,
                            "extra": payload.evidence,
                        }),
                    )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT agent, task_name, risk_level, pending_validations
                    FROM soul_v3.working_state
                    WHERE agent=$1
                    """,
                    validation_agent,
                )
                if not row:
                    raise HTTPException(404, "working_state validation item not found")
                validations = list(row["pending_validations"] or [])
                if source_id is None or source_id >= len(validations):
                    raise HTTPException(404, "pending validation index not found")
                agent = str(row["agent"])
                source_evidence = {
                    "task_name": row["task_name"],
                    "risk_level": row["risk_level"],
                    "pending_validation": validations[source_id],
                }
                planned_update["source_mutation"] = False
                planned_update["status"] = "audit_record_only"

            evidence = {
                "source": "nexus_review_queue_decision",
                "kind": kind,
                "planned_update": planned_update,
                "source_evidence": source_evidence,
                "extra": payload.evidence,
            }
            applied = not payload.dry_run and bool(planned_update.get("source_mutation", True))
            audit_id = None
            if not payload.dry_run:
                audit_id = await conn.fetchval(
                    """
                    INSERT INTO soul_v3.nexus_review_decisions
                        (item_id, source_table, source_id, agent, reviewer, actor, decision, rationale, evidence, dry_run, applied)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,false,$10)
                    RETURNING id
                    """,
                    payload.item_id,
                    source_table,
                    source_id,
                    agent,
                    reviewer,
                    actor,
                    decision,
                    rationale,
                    json.dumps(evidence),
                    applied,
                )

    return {
        "ok": True,
        "dry_run": payload.dry_run,
        "audit_id": audit_id,
        "item_id": payload.item_id,
        "source_table": source_table,
        "source_id": source_id,
        "agent": agent,
        "reviewer": reviewer,
        "actor": actor,
        "decision": decision,
        "applied": False if payload.dry_run else applied,
        "planned_update": planned_update,
        "boundary": "decision_requires_explicit_rationale_and_audit_trail",
    }


@app.get("/api/soul/nexus_review_queue/evidence_packet")
async def nexus_review_evidence_packet(
    item_id: str = Query(...),
    agent: str = Query("ADA"),
    reviewer: str = Query("NEXUS"),
):
    pool = _pool_ok()
    return await _evidence_packet(pool, item_id, agent.upper(), reviewer.upper())


@app.get("/api/soul/nexus_review_queue/diff")
async def nexus_review_diff(
    item_id: str = Query(...),
    agent: str = Query("ADA"),
    reviewer: str = Query("NEXUS"),
):
    pool = _pool_ok()
    packet = await _evidence_packet(pool, item_id, agent.upper(), reviewer.upper())
    return {
        "item_id": item_id,
        "agent": agent.upper(),
        "reviewer": reviewer.upper(),
        "generated_at": packet["generated_at"],
        "diff": packet["packet"]["diff"],
        "boundary": "diff_preview_only_no_mutation",
    }


@app.get("/api/soul/nexus_review_queue/timeline")
async def nexus_review_timeline(
    item_id: str = Query(...),
    reviewer: str = Query("NEXUS"),
):
    pool = _pool_ok()
    timeline = await _item_timeline(pool, item_id, reviewer.upper())
    timeline["generated_at"] = _dt(await pool.fetchval("SELECT NOW()"))
    return timeline


@app.get("/api/soul/nexus_review_queue/policy_gates")
async def nexus_review_policy_gates(
    agent: str = Query("ADA"),
    reviewer: str = Query("NEXUS"),
):
    pool = _pool_ok()
    queue = await nexus_review_queue(agent=agent.upper(), reviewer=reviewer.upper(), limit=80)
    items = queue.get("items", [])
    gates = [_policy_gate_for_item(item) for item in items if isinstance(item, dict)]
    william_required = sum(1 for gate in gates if gate.get("william_required"))
    nexus_allowed = sum(1 for gate in gates if gate.get("nexus_allowed"))
    return {
        "agent": agent.upper(),
        "reviewer": reviewer.upper(),
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "summary": {
            "queue_items": len(items),
            "nexus_allowed": nexus_allowed,
            "william_required": william_required,
            "henry_optional": sum(1 for gate in gates if gate.get("henry_optional")),
            "recent_decisions": queue.get("summary", {}).get("recent_decisions", 0),
        },
        "rules": {
            "nexus_can_approve_low_medium_non_destructive": True,
            "william_required_for_destructive_or_external_side_effects": True,
            "henry_optional_for_paper_research_or_ambiguous_delegation": True,
            "all_decisions_require_rationale_and_audit_trail": True,
        },
        "gates": gates,
        "boundary": "policy_gates_read_only_no_mutation",
    }


@app.get("/api/soul/nexus_review_queue/alerts")
async def nexus_review_alerts(
    agent: str = Query("ADA"),
    reviewer: str = Query("NEXUS"),
):
    pool = _pool_ok()
    agent = agent.upper()
    reviewer = reviewer.upper()
    alerts: list[dict] = []
    resolved_virtual = 0
    if await _table_exists(pool, "nexus_review_decisions"):
        needs_evidence_rows = await pool.fetch(
            """
            SELECT id, item_id, agent, actor, rationale, created_at,
                   EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 AS age_hours
            FROM soul_v3.nexus_review_decisions
            WHERE reviewer=$1
              AND decision='needs_evidence'
              AND dry_run IS FALSE
              AND created_at < NOW() - INTERVAL '24 hours'
            ORDER BY created_at ASC
            LIMIT 50
            """,
            reviewer,
        )
        for row in needs_evidence_rows:
            alerts.append({
                "severity": "medium",
                "kind": "stale_needs_evidence",
                "item_id": row["item_id"],
                "age_hours": round(float(row["age_hours"] or 0), 1),
                "title": "needs_evidence older than 24h",
                "details": {"decision_id": row["id"], "actor": row["actor"], "rationale": row["rationale"]},
                "created_at": _dt(row["created_at"]),
            })
        terminal_rows = await pool.fetch(
            """
            SELECT id, item_id, agent, actor, decision, rationale, created_at
            FROM soul_v3.nexus_review_decisions
            WHERE reviewer=$1
              AND decision IN ('approved','rejected')
              AND dry_run IS FALSE
              AND applied IS FALSE
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """,
            reviewer,
        )
        for row in terminal_rows:
            kind, _source_id, _validation_agent = _review_item_parts(str(row["item_id"]))
            if kind == "validation":
                resolved_virtual += 1
                continue
            severity = "low" if kind == "validation" else "high"
            alerts.append({
                "severity": severity,
                "kind": "terminal_decision_not_source_applied",
                "item_id": row["item_id"],
                "title": "terminal audit decision has no source mutation",
                "details": {
                    "decision_id": row["id"],
                    "decision": row["decision"],
                    "actor": row["actor"],
                    "rationale": row["rationale"],
                    "virtual_ok": kind == "validation",
                },
                "created_at": _dt(row["created_at"]),
            })
    if await _table_exists(pool, "nexus_rollback_requests"):
        rollback_rows = await pool.fetch(
            """
            SELECT id, request_id, item_id, actor, reason, status, created_at
            FROM soul_v3.nexus_rollback_requests
            WHERE status='pending_william_review'
            ORDER BY created_at ASC, id ASC
            LIMIT 50
            """
        )
        for row in rollback_rows:
            alerts.append({
                "severity": "high",
                "kind": "pending_william_rollback_review",
                "item_id": row["item_id"],
                "title": "rollback request awaiting William review",
                "details": {
                    "request_id": row["request_id"],
                    "actor": row["actor"],
                    "reason": row["reason"],
                },
                "created_at": _dt(row["created_at"]),
            })
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda alert: (severity_rank.get(alert["severity"], 9), alert.get("created_at") or ""))
    return {
        "agent": agent,
        "reviewer": reviewer,
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "summary": {
            "total": len(alerts),
            "high": sum(1 for a in alerts if a["severity"] == "high"),
            "medium": sum(1 for a in alerts if a["severity"] == "medium"),
            "low": sum(1 for a in alerts if a["severity"] == "low"),
            "resolved_virtual": resolved_virtual,
        },
        "alerts": alerts,
        "boundary": "debt_alerts_read_only_no_mutation",
    }


@app.post("/api/soul/nexus_review_queue/rollback_request")
async def nexus_rollback_request(payload: NexusRollbackRequestIn):
    pool = _pool_ok()
    await _ensure_nexus_rollback_requests(pool)
    await _ensure_nexus_review_decisions(pool)
    reason = payload.reason.strip()
    if len(reason) < 12:
        raise HTTPException(400, "reason must be at least 12 characters")
    agent = payload.agent.upper()
    reviewer = payload.reviewer.upper()
    actor = payload.actor.upper()
    packet = await _evidence_packet(pool, payload.item_id, agent, reviewer)
    request_id = f"rollback-{time.time_ns()}"
    evidence = {
        "source": "nexus_rollback_request",
        "packet": {
            "item_id": payload.item_id,
            "diff": packet["packet"]["diff"],
            "latest_decision": (packet["packet"]["decisions"] or [None])[0],
            "timeline_summary": packet["packet"]["timeline"]["summary"],
        },
        "policy": {
            "william_required": True,
            "reason": "rollback requests are human-gated",
        },
    }
    db_id = None
    if not payload.dry_run:
        db_id = await pool.fetchval(
            """
            INSERT INTO soul_v3.nexus_rollback_requests
                (request_id, item_id, agent, reviewer, actor, reason, status, evidence)
            VALUES ($1,$2,$3,$4,$5,$6,'pending_william_review',$7::jsonb)
            RETURNING id
            """,
            request_id,
            payload.item_id,
            agent,
            reviewer,
            actor,
            reason,
            json.dumps(evidence),
        )
    return {
        "ok": True,
        "dry_run": payload.dry_run,
        "request_id": None if payload.dry_run else request_id,
        "request_db_id": db_id,
        "item_id": payload.item_id,
        "status": "pending_william_review",
        "evidence": evidence,
        "boundary": "rollback_request_audit_only_requires_william_review",
    }


@app.get("/api/soul/nexus_review_queue/william_review")
async def nexus_william_review(
    agent: str = Query("ADA"),
    reviewer: str = Query("NEXUS"),
):
    pool = _pool_ok()
    agent = agent.upper()
    reviewer = reviewer.upper()
    queue = await nexus_review_queue(agent=agent, reviewer=reviewer, limit=80)
    alerts_payload = await nexus_review_alerts(agent=agent, reviewer=reviewer)
    william_items = []
    for item in queue.get("items", []):
        if isinstance(item, dict):
            gate = _policy_gate_for_item(item)
            if gate.get("william_required"):
                william_items.append({"type": "queue_item", "item": item, "policy": gate})
    for alert in alerts_payload.get("alerts", []):
        if alert.get("kind") == "pending_william_rollback_review" or alert.get("severity") == "high":
            william_items.append({"type": "alert", "item": alert, "policy": {"william_required": True}})
    return {
        "agent": agent,
        "reviewer": reviewer,
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "summary": {
            "total": len(william_items),
            "queue_items": sum(1 for row in william_items if row["type"] == "queue_item"),
            "alerts": sum(1 for row in william_items if row["type"] == "alert"),
            "recent_decisions": len(await _latest_william_decisions(pool, limit=8)),
        },
        "items": william_items,
        "recent_decisions": await _latest_william_decisions(pool, limit=8),
        "boundary": "william_review_read_only_human_gate",
    }


@app.get("/api/soul/nexus_review_queue/william_decisions")
async def william_review_decisions(limit: int = Query(20, ge=1, le=100)):
    pool = _pool_ok()
    return {
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "decisions": await _latest_william_decisions(pool, limit=limit),
        "boundary": "william_decisions_read_only_audit_trail",
    }


@app.post("/api/soul/nexus_review_queue/william_decision")
async def william_review_decision(payload: WilliamReviewDecisionIn):
    pool = _pool_ok()
    await _ensure_william_review_decisions(pool)

    target_type = payload.target_type.strip().lower()
    target_id = payload.target_id.strip()
    decision = payload.decision.strip().lower()
    rationale = payload.rationale.strip()
    if target_type not in {"review_item", "rollback_request", "alert"}:
        raise HTTPException(400, "target_type must be review_item, rollback_request, or alert")
    if decision not in {"approved", "rejected", "needs_more_info"}:
        raise HTTPException(400, "decision must be approved, rejected, or needs_more_info")
    if len(rationale) < 12:
        raise HTTPException(400, "rationale must be at least 12 characters")

    agent = payload.agent.upper()
    reviewer = payload.reviewer.upper()
    actor = payload.actor.strip() or "William"
    target_evidence: dict[str, Any] = {"target_type": target_type, "target_id": target_id}
    if target_type == "review_item":
        target_evidence["packet"] = await _evidence_packet(pool, target_id, agent, reviewer)
    elif target_type == "rollback_request":
        if not await _table_exists(pool, "nexus_rollback_requests"):
            raise HTTPException(404, "rollback request table does not exist")
        row = await pool.fetchrow(
            """
            SELECT id, request_id, item_id, agent, reviewer, actor, reason, status, evidence, created_at
            FROM soul_v3.nexus_rollback_requests
            WHERE request_id=$1 OR id::text=$1
            ORDER BY id DESC
            LIMIT 1
            """,
            target_id,
        )
        if not row:
            raise HTTPException(404, "rollback request not found")
        target_evidence["rollback_request"] = _record_dict(row)
        target_evidence["rollback_request"]["evidence"] = _json_obj(target_evidence["rollback_request"].get("evidence"))
    else:
        alerts_payload = await nexus_review_alerts(agent=agent, reviewer=reviewer)
        alert = next((item for item in alerts_payload.get("alerts", []) if item.get("item_id") == target_id), None)
        if not alert:
            raise HTTPException(404, "alert target not found")
        target_evidence["alert"] = alert

    evidence = {
        "source": "william_review_decision",
        "target": target_evidence,
        "extra": payload.evidence,
        "effect": "audit_only_no_source_mutation",
    }
    decision_id = None
    if not payload.dry_run:
        decision_id = await pool.fetchval(
            """
            INSERT INTO soul_v3.william_review_decisions
                (target_type, target_id, agent, actor, reviewer, decision, rationale, evidence, dry_run)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,false)
            RETURNING id
            """,
            target_type,
            target_id,
            agent,
            actor,
            reviewer,
            decision,
            rationale,
            json.dumps(evidence),
        )

    return {
        "ok": True,
        "dry_run": payload.dry_run,
        "decision_id": decision_id,
        "target_type": target_type,
        "target_id": target_id,
        "agent": agent,
        "actor": actor,
        "reviewer": reviewer,
        "decision": decision,
        "evidence": evidence,
        "boundary": "william_decision_audit_only_no_source_mutation",
    }


@app.post("/api/soul/nexus_review_queue/decision_worker")
async def nexus_decision_worker(payload: NexusDecisionWorkerIn):
    pool = _pool_ok()
    await _ensure_nexus_review_decisions(pool)
    await _ensure_nexus_decision_worker_runs(pool)

    agent = payload.agent.upper()
    reviewer = payload.reviewer.upper()
    decisions = await _latest_review_decisions(pool, reviewer, limit=payload.limit)
    actions: list[dict] = []
    applied_count = 0
    skipped_count = 0

    status_for_decision = {
        "approved": "approved_by_nexus",
        "rejected": "rejected_by_nexus",
        "needs_evidence": "needs_evidence",
        "pending": "pending_nexus_review",
    }
    seen: set[str] = set()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for decision in decisions:
                item_id = str(decision.get("item_id"))
                if item_id in seen or bool(decision.get("dry_run")):
                    continue
                seen.add(item_id)
                try:
                    kind, source_id, _validation_agent = _review_item_parts(item_id)
                except HTTPException:
                    skipped_count += 1
                    actions.append({"item_id": item_id, "action": "skipped_invalid_item_id"})
                    continue
                decision_value = str(decision.get("decision") or "")
                target_status = status_for_decision.get(decision_value)
                action = {
                    "decision_id": decision.get("id"),
                    "item_id": item_id,
                    "kind": kind,
                    "decision": decision_value,
                    "dry_run": payload.dry_run,
                    "source_mutation": False,
                    "action": "skipped",
                }
                if kind == "adapter" and await _table_exists(pool, "awareness_adapter_queue"):
                    row = await conn.fetchrow(
                        "SELECT status, nexus_review_required FROM soul_v3.awareness_adapter_queue WHERE id=$1",
                        source_id,
                    )
                    if not row or target_status is None:
                        skipped_count += 1
                        action["action"] = "missing_source_or_target"
                    else:
                        terminal = decision_value in {"approved", "rejected"}
                        already = row["status"] == target_status and bool(row["nexus_review_required"]) is (not terminal)
                        action.update({"source_mutation": True, "previous_status": row["status"], "target_status": target_status})
                        if already:
                            action["action"] = "already_applied"
                            applied_count += 1
                        elif payload.dry_run:
                            action["action"] = "would_apply_source_status"
                        else:
                            await conn.execute(
                                """
                                UPDATE soul_v3.awareness_adapter_queue
                                SET status=$1, nexus_review_required=$2
                                WHERE id=$3
                                """,
                                target_status,
                                not terminal,
                                source_id,
                            )
                            action["action"] = "applied_source_status"
                            applied_count += 1
                elif kind == "outcome" and await _table_exists(pool, "awareness_closed_loop_outcomes"):
                    row = await conn.fetchrow(
                        "SELECT status, nexus_review_required FROM soul_v3.awareness_closed_loop_outcomes WHERE id=$1",
                        source_id,
                    )
                    if not row or target_status is None:
                        skipped_count += 1
                        action["action"] = "missing_source_or_target"
                    else:
                        terminal = decision_value in {"approved", "rejected"}
                        already = row["status"] == target_status and bool(row["nexus_review_required"]) is (not terminal)
                        action.update({"source_mutation": True, "previous_status": row["status"], "target_status": target_status})
                        if already:
                            action["action"] = "already_applied"
                            applied_count += 1
                        elif payload.dry_run:
                            action["action"] = "would_apply_source_status"
                        else:
                            await conn.execute(
                                """
                                UPDATE soul_v3.awareness_closed_loop_outcomes
                                SET status=$1,
                                    nexus_review_required=$2,
                                    evidence=COALESCE(evidence, '{}'::jsonb) || $3::jsonb
                                WHERE id=$4
                                """,
                                target_status,
                                not terminal,
                                json.dumps({
                                    "nexus_decision_worker": {
                                        "reviewer": reviewer,
                                        "decision_id": decision.get("id"),
                                        "decision": decision_value,
                                    }
                                }),
                                source_id,
                            )
                            action["action"] = "applied_source_status"
                            applied_count += 1
                elif kind == "task":
                    row = await conn.fetchrow("SELECT status FROM soul_v3.agent_tasks WHERE id=$1", source_id)
                    if not row:
                        skipped_count += 1
                        action["action"] = "missing_source"
                    else:
                        current_status = str(row["status"])
                        if decision_value == "approved":
                            target_task_status = "completed"
                        elif decision_value == "rejected":
                            target_task_status = current_status if current_status in {"completed", "cancelled"} else "cancelled"
                        else:
                            target_task_status = "in_progress" if decision_value == "needs_evidence" else "pending"
                        action.update({"source_mutation": True, "previous_status": row["status"], "target_status": target_task_status})
                        if row["status"] == target_task_status:
                            action["action"] = "already_applied"
                            applied_count += 1
                        elif payload.dry_run:
                            action["action"] = "would_apply_task_status"
                        else:
                            await conn.execute(
                                """
                                UPDATE soul_v3.agent_tasks
                                SET status=$1, completed_at=CASE WHEN $1='completed' THEN COALESCE(completed_at, NOW()) ELSE completed_at END
                                WHERE id=$2
                                """,
                                target_task_status,
                                source_id,
                            )
                            action["action"] = "applied_task_status"
                            applied_count += 1
                elif kind == "validation":
                    action["action"] = "virtual_validation_resolved_by_audit_decision" if decision_value in {"approved", "rejected"} else "virtual_validation_waiting"
                    if decision_value in {"approved", "rejected"}:
                        applied_count += 1
                    else:
                        skipped_count += 1
                else:
                    skipped_count += 1
                    action["action"] = "unsupported_kind"
                actions.append(action)

            run_db_id = None
            run_id = f"nexus-worker-{int(time.time())}"
            if not payload.dry_run:
                run_db_id = await conn.fetchval(
                    """
                    INSERT INTO soul_v3.nexus_decision_worker_runs
                        (run_id, agent, reviewer, dry_run, applied_count, skipped_count, evidence, details)
                    VALUES ($1,$2,$3,false,$4,$5,$6::jsonb,$7::jsonb)
                    RETURNING id
                    """,
                    run_id,
                    agent,
                    reviewer,
                    applied_count,
                    skipped_count,
                    json.dumps({"source": "nexus_decision_worker", "decision_count": len(decisions)}),
                    json.dumps({"actions": actions[:80]}),
                )

    return {
        "ok": True,
        "agent": agent,
        "reviewer": reviewer,
        "dry_run": payload.dry_run,
        "run_id": None if payload.dry_run else run_id,
        "run_db_id": None if payload.dry_run else run_db_id,
        "applied_count": applied_count,
        "skipped_count": skipped_count,
        "actions": actions,
        "boundary": "decision_worker_replays_audited_decisions_only",
    }


@app.get("/api/soul/learning_loop_status")
async def learning_loop_status(agent: str = Query("ADA")):
    pool = _pool_ok()
    agent = agent.upper()
    counts: dict[str, int] = {}
    counts["experience_examples"] = int(await pool.fetchval(
        "SELECT COUNT(*) FROM soul_v3.awareness_experience_examples WHERE agent=$1",
        agent,
    ) or 0) if await _table_exists(pool, "awareness_experience_examples") else 0
    counts["adapter_candidates"] = int(await pool.fetchval(
        "SELECT COUNT(*) FROM soul_v3.awareness_adapter_queue WHERE agent=$1",
        agent,
    ) or 0) if await _table_exists(pool, "awareness_adapter_queue") else 0
    counts["closed_loop_outcomes"] = int(await pool.fetchval(
        "SELECT COUNT(*) FROM soul_v3.awareness_closed_loop_outcomes WHERE agent=$1",
        agent,
    ) or 0) if await _table_exists(pool, "awareness_closed_loop_outcomes") else 0
    counts["review_decisions"] = int(await pool.fetchval(
        "SELECT COUNT(*) FROM soul_v3.nexus_review_decisions WHERE agent=$1 AND dry_run IS FALSE",
        agent,
    ) or 0) if await _table_exists(pool, "nexus_review_decisions") else 0
    counts["worker_runs"] = int(await pool.fetchval(
        "SELECT COUNT(*) FROM soul_v3.nexus_decision_worker_runs WHERE agent=$1 AND dry_run IS FALSE",
        agent,
    ) or 0) if await _table_exists(pool, "nexus_decision_worker_runs") else 0
    latest_awareness = await pool.fetchrow(
        """
        SELECT id, score, passed, evidence, run_at
        FROM soul_v3.evaluation_runs
        WHERE agent=$1 AND suite_name='awareness_247_process'
        ORDER BY run_at DESC, id DESC
        LIMIT 1
        """,
        agent,
    )
    awareness_ok = bool(latest_awareness and latest_awareness["passed"])
    stages = [
        {"step": 1, "name": "capture_experience", "ok": counts["experience_examples"] > 0, "count": counts["experience_examples"]},
        {"step": 2, "name": "propose_candidate", "ok": counts["adapter_candidates"] > 0 or counts["closed_loop_outcomes"] > 0, "count": counts["adapter_candidates"] + counts["closed_loop_outcomes"]},
        {"step": 3, "name": "nexus_review", "ok": counts["review_decisions"] > 0, "count": counts["review_decisions"]},
        {"step": 4, "name": "decision_worker", "ok": counts["worker_runs"] > 0 or counts["review_decisions"] > 0, "count": counts["worker_runs"]},
        {"step": 5, "name": "awareness_247_feedback", "ok": awareness_ok, "count": 1 if awareness_ok else 0},
    ]
    return {
        "agent": agent,
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "summary": {
            "stages_ok": sum(1 for s in stages if s["ok"]),
            "stages_total": len(stages),
            "green": all(s["ok"] for s in stages),
            **counts,
        },
        "stages": stages,
        "latest_awareness_247_process": _record_dict(latest_awareness),
        "boundary": "learning_loop_status_observability_no_model_training",
    }


@app.get("/api/soul/autonomy_dashboard")
async def autonomy_dashboard(agent: str = Query("ADA"), reviewer: str = Query("NEXUS")):
    pool = _pool_ok()
    agent = agent.upper()
    reviewer = reviewer.upper()
    queue = await nexus_review_queue(agent=agent, reviewer=reviewer, limit=40)
    learning = await learning_loop_status(agent=agent)
    policy = await nexus_review_policy_gates(agent=agent, reviewer=reviewer)
    alerts = await nexus_review_alerts(agent=agent, reviewer=reviewer)
    william = await nexus_william_review(agent=agent, reviewer=reviewer)
    recent_decisions = await _latest_review_decisions(pool, reviewer, limit=8)
    latest_worker = {}
    worker_count = 0
    if await _table_exists(pool, "nexus_decision_worker_runs"):
        worker_count = int(await pool.fetchval(
            "SELECT COUNT(*) FROM soul_v3.nexus_decision_worker_runs WHERE agent=$1 AND reviewer=$2",
            agent,
            reviewer,
        ) or 0)
        latest_worker = _record_dict(await pool.fetchrow(
            """
            SELECT id, run_id, dry_run, applied_count, skipped_count, created_at
            FROM soul_v3.nexus_decision_worker_runs
            WHERE agent=$1 AND reviewer=$2
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            agent,
            reviewer,
        ))
    return {
        "agent": agent,
        "reviewer": reviewer,
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "summary": {
            "pending_review": queue.get("summary", {}).get("total", 0),
            "recent_decisions": len(recent_decisions),
            "worker_runs": worker_count,
            "learning_green": learning.get("summary", {}).get("green", False),
            "stages_ok": learning.get("summary", {}).get("stages_ok", 0),
            "stages_total": learning.get("summary", {}).get("stages_total", 5),
            "william_required": policy.get("summary", {}).get("william_required", 0),
            "debt_alerts": alerts.get("summary", {}).get("total", 0),
            "william_review": william.get("summary", {}).get("total", 0),
        },
        "review_queue": queue.get("summary", {}),
        "policy_gates": policy.get("summary", {}),
        "debt_alerts": alerts,
        "william_review": william,
        "learning_loop": learning,
        "recent_decisions": recent_decisions,
        "latest_worker_run": latest_worker,
        "boundary": "autonomy_dashboard_observability_and_audited_controls",
    }


# ── Awareness Dashboard ───────────────────────────────────────────────────────

@app.get("/api/soul/awareness_dashboard")
async def awareness_dashboard(
    agent: str = Query("ADA"),
    limit: int = Query(20, ge=5, le=100),
):
    pool = _pool_ok()
    agent = agent.upper()
    awareness_suites = [
        "awareness_event_collector",
        "attention_governor",
        "awareness_tick_ledger",
        "awareness_reflex_actions",
        "local_runtime_contract",
        "awareness_loop_shadow",
        "awareness_experience_dataset",
        "awareness_closed_loop",
        "latent_graphmem_phase2",
        "awareness_247_process",
    ]

    suite_rows = await pool.fetch(
        """
        SELECT DISTINCT ON (suite_name)
               id, suite_name, score, passed, evidence, run_at
        FROM soul_v3.evaluation_runs
        WHERE agent=$1 AND suite_name = ANY($2::text[])
        ORDER BY suite_name, run_at DESC, id DESC
        """,
        agent,
        awareness_suites,
    )

    state = None
    ticks = []
    if await _table_exists(pool, "awareness_state"):
        row = await pool.fetchrow(
            """
            SELECT agent, state, last_tick_id, last_event_id, current_focus,
                   budget_class, local_runtime_status, big_cortex_status,
                   confidence, updated_at
            FROM soul_v3.awareness_state
            WHERE agent=$1
            """,
            agent,
        )
        if row:
            state = {
                "agent": row["agent"],
                "state": row["state"],
                "last_tick_id": row["last_tick_id"],
                "last_event_id": row["last_event_id"],
                "current_focus": row["current_focus"],
                "budget_class": row["budget_class"],
                "local_runtime_status": row["local_runtime_status"],
                "big_cortex_status": row["big_cortex_status"],
                "confidence": float(row["confidence"] or 0),
                "updated_at": _dt(row["updated_at"]),
            }
    if await _table_exists(pool, "awareness_ticks"):
        tick_rows = await pool.fetch(
            """
            SELECT id, tick_id, event_id, attention_action, local_model_used,
                   escalated_runtime, summary, outcome, score, created_at
            FROM soul_v3.awareness_ticks
            WHERE agent=$1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            agent,
            limit,
        )
        ticks = [
            {
                "id": r["id"],
                "tick_id": r["tick_id"],
                "event_id": r["event_id"],
                "attention_action": r["attention_action"],
                "local_model_used": r["local_model_used"],
                "escalated_runtime": r["escalated_runtime"],
                "summary": r["summary"],
                "outcome": r["outcome"],
                "score": float(r["score"] or 0),
                "created_at": _dt(r["created_at"]),
            }
            for r in tick_rows
        ]

    queue = []
    queue_counts = {"pending_nexus_review": 0, "approved": 0, "rejected": 0}
    if await _table_exists(pool, "awareness_adapter_queue"):
        queue_rows = await pool.fetch(
            """
            SELECT id, queue_id, example_id, status, canary_mode,
                   nexus_review_required, candidate_reason, created_at
            FROM soul_v3.awareness_adapter_queue
            WHERE agent=$1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            agent,
            limit,
        )
        count_rows = await pool.fetch(
            """
            SELECT status, COUNT(*) AS cnt
            FROM soul_v3.awareness_adapter_queue
            WHERE agent=$1
            GROUP BY status
            """,
            agent,
        )
        queue_counts.update({r["status"]: r["cnt"] for r in count_rows})
        queue = [
            {
                "id": r["id"],
                "queue_id": r["queue_id"],
                "example_id": r["example_id"],
                "status": r["status"],
                "canary_mode": r["canary_mode"],
                "nexus_review_required": r["nexus_review_required"],
                "candidate_reason": r["candidate_reason"],
                "created_at": _dt(r["created_at"]),
            }
            for r in queue_rows
        ]

    outcomes = []
    outcome_counts = {
        "promote_pending_nexus": 0,
        "rollback_pending_nexus": 0,
        "guardrail_candidates": 0,
        "regression_tests": 0,
    }
    if await _table_exists(pool, "awareness_closed_loop_outcomes"):
        outcome_rows = await pool.fetch(
            """
            SELECT id, outcome_id, example_id, queue_id, source_event_id,
                   outcome_type, metric_delta, benchmark_score, regression_count,
                   promotion_decision, memory_update_required, skill_candidate,
                   guardrail_candidate, regression_test_candidate, status,
                   created_at
            FROM soul_v3.awareness_closed_loop_outcomes
            WHERE agent=$1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            agent,
            limit,
        )
        count_rows = await pool.fetch(
            """
            SELECT promotion_decision, COUNT(*) AS cnt
            FROM soul_v3.awareness_closed_loop_outcomes
            WHERE agent=$1
            GROUP BY promotion_decision
            """,
            agent,
        )
        outcome_counts.update({r["promotion_decision"]: r["cnt"] for r in count_rows})
        outcome_counts["guardrail_candidates"] = int(
            await pool.fetchval(
                "SELECT COUNT(*) FROM soul_v3.awareness_closed_loop_outcomes WHERE agent=$1 AND guardrail_candidate IS TRUE",
                agent,
            ) or 0
        )
        outcome_counts["regression_tests"] = int(
            await pool.fetchval(
                "SELECT COUNT(*) FROM soul_v3.awareness_closed_loop_outcomes WHERE agent=$1 AND regression_test_candidate IS TRUE",
                agent,
            ) or 0
        )
        outcomes = [
            {
                "id": r["id"],
                "outcome_id": r["outcome_id"],
                "example_id": r["example_id"],
                "queue_id": r["queue_id"],
                "source_event_id": r["source_event_id"],
                "outcome_type": r["outcome_type"],
                "metric_delta": float(r["metric_delta"] or 0),
                "benchmark_score": float(r["benchmark_score"] or 0),
                "regression_count": r["regression_count"],
                "promotion_decision": r["promotion_decision"],
                "memory_update_required": r["memory_update_required"],
                "skill_candidate": r["skill_candidate"],
                "guardrail_candidate": r["guardrail_candidate"],
                "regression_test_candidate": r["regression_test_candidate"],
                "status": r["status"],
                "created_at": _dt(r["created_at"]),
            }
            for r in outcome_rows
        ]

    schedule = None
    if await _table_exists(pool, "awareness_learning_schedule"):
        row = await pool.fetchrow(
            """
            SELECT schedule_id, cadence, next_run_at, benchmark_suite, status,
                   nexus_review_required, updated_at
            FROM soul_v3.awareness_learning_schedule
            WHERE agent=$1
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            agent,
        )
        if row:
            schedule = {
                "schedule_id": row["schedule_id"],
                "cadence": row["cadence"],
                "next_run_at": _dt(row["next_run_at"]),
                "benchmark_suite": row["benchmark_suite"],
                "status": row["status"],
                "nexus_review_required": row["nexus_review_required"],
                "updated_at": _dt(row["updated_at"]),
            }

    process = None
    if await _table_exists(pool, "awareness_247_process_runs"):
        row = await pool.fetchrow(
            """
            SELECT id, run_id, phase_scores, passed, evidence, details,
                   created_at, EXTRACT(EPOCH FROM (NOW() - created_at)) AS age_seconds
            FROM soul_v3.awareness_247_process_runs
            WHERE agent=$1
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            agent,
        )
        if row:
            phase_scores = _json_obj(row["phase_scores"])
            details = _json_obj(row["details"])
            process = {
                "id": row["id"],
                "run_id": row["run_id"],
                "phase_scores": phase_scores,
                "passed": row["passed"],
                "evidence": row["evidence"],
                "details": details,
                "created_at": _dt(row["created_at"]),
                "age_seconds": float(row["age_seconds"] or 0),
            }

    suite_count = len(suite_rows)
    passing = sum(1 for r in suite_rows if r["passed"])
    latest_run_at = max((_dt(r["run_at"]) for r in suite_rows if r["run_at"]), default=None)

    return {
        "agent": agent,
        "generated_at": _dt(await pool.fetchval("SELECT NOW()")),
        "summary": {
            "suite_count": suite_count,
            "passing_suites": passing,
            "failing_suites": suite_count - passing,
            "latest_run_at": latest_run_at,
            "recent_ticks": len(ticks),
            "queue_pending": int(queue_counts.get("pending_nexus_review") or 0),
            "closed_loop_outcomes": len(outcomes),
            "promote_pending": int(outcome_counts.get("promote_pending_nexus") or 0),
            "rollback_pending": int(outcome_counts.get("rollback_pending_nexus") or 0),
            "guardrail_candidates": int(outcome_counts.get("guardrail_candidates") or 0),
            "regression_tests": int(outcome_counts.get("regression_tests") or 0),
            "process_passed": bool(process and process["passed"]),
            "process_age_seconds": process["age_seconds"] if process else None,
        },
        "process": process,
        "state": state,
        "latest_suites": [
            {
                "id": r["id"],
                "suite": r["suite_name"],
                "score": r["score"],
                "passed": r["passed"],
                "evidence": r["evidence"],
                "run_at": _dt(r["run_at"]),
            }
            for r in sorted(suite_rows, key=lambda r: _dt(r["run_at"]) or "", reverse=True)
        ],
        "ticks": ticks,
        "adapter_queue": queue,
        "closed_loop": {
            "outcomes": outcomes,
            "schedule": schedule,
        },
    }


# ── Relationships ─────────────────────────────────────────────────────────────

@app.get("/api/soul/relationships")
async def relationships(agent: str = Query("ADA")):
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT person, trust_level, communication_style, dynamic, interaction_count, updated_at "
        "FROM soul_v3.relationships WHERE agent=$1 ORDER BY trust_level DESC",
        agent,
    )
    return [
        {
            "person": r["person"],
            "trust": float(r["trust_level"] or 0),
            "style": r["communication_style"],
            "dynamic": r["dynamic"],
            "interactions": r["interaction_count"],
            "updated_at": _dt(r["updated_at"]),
        }
        for r in rows
    ]


# ── Context Meter, NERVES proxy, Agent Controls (ALICE 2026-05-20, rescatado de :8768) ──

@app.get("/api/soul/context-meter")
async def context_meter():
    """Proxy a SOUL API :8800/api/soul/context-meter — tokens/limit/age por agente.

    FIX 2026-05-20 JARVIS: :8768 (soul_v3_studio_server) fue retirado 14:18; el endpoint
    canónico vive en :8800 (seal-studio backend, ruta /api/soul/context-meter).
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get("http://localhost:8800/api/soul/context-meter")
            return r.json()
    except Exception as e:
        return {"agents": [], "error": str(e)}


@app.get("/api/soul/nerves")
async def soul_nerves():
    """NERVES drives state — query directo a soul_v3.motivation_states + latest threshold from log."""
    pool = _pool_ok()
    rows = await pool.fetch("""
        SELECT ms.agent, ms.tank, ms.value AS pressure, ms.fire_count, ms.last_fired,
               COALESCE(nml.threshold, 50.0) AS threshold,
               nml.ocean_param,
               COALESCE(nml.fired, false) AS fired
        FROM soul_v3.motivation_states ms
        LEFT JOIN LATERAL (
            SELECT threshold, ocean_param, fired
            FROM soul_v3.nerves_metrics_log
            WHERE agent=ms.agent AND tank=ms.tank
            ORDER BY created_at DESC LIMIT 1
        ) nml ON TRUE
        WHERE ms.agent IN ('ADA','JARVIS','ALICE','NEXUS','DUM')
        ORDER BY ms.agent, ms.tank
    """)
    by_agent: dict = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append({
            "tank": r["tank"],
            "pressure": float(r["pressure"] or 0),
            "threshold": float(r["threshold"] or 50),
            "fired": bool(r["fired"]),
            "ocean_param": r["ocean_param"],
        })
    return {"agents": [{"agent": a, "drives": drives} for a, drives in by_agent.items()]}


@app.post("/api/soul/agent-action")
async def soul_agent_action(payload: dict):
    """Pause/resurrect/reset_crashes — write flag or trigger restart."""
    import os
    import subprocess
    from pathlib import Path
    agent = (payload.get("agent") or "").upper()
    action = payload.get("action", "")
    CORE = {"ADA", "JARVIS", "ALICE", "NEXUS"}
    if agent not in CORE:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")
    if action not in ("pause", "resurrect", "resume", "reset_crashes"):
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
    PAUSE_DIR = Path("/tmp/seal_pause_flags")
    PAUSE_DIR.mkdir(exist_ok=True)
    RESTART_SCRIPT = Path("/home/dadito/IA/proyecto-seal/start_agent.sh")
    if action == "pause":
        (PAUSE_DIR / f"{agent}.pause").touch()
        return {"ok": True, "action": "paused", "agent": agent}
    if action in ("resurrect", "resume"):
        (PAUSE_DIR / f"{agent}.pause").unlink(missing_ok=True)
        if RESTART_SCRIPT.exists():
            subprocess.Popen(["/bin/bash", str(RESTART_SCRIPT), agent.lower()],
                             env={**os.environ, "DISPLAY": ":0"})
        return {"ok": True, "action": "resurrected", "agent": agent}
    if action == "reset_crashes":
        return {"ok": True, "action": "reset", "agent": agent}
    return {"ok": False, "error": "unhandled"}


@app.post("/api/agents/relaunch/{agent}")
async def proxy_relaunch(agent: str):
    """Proxy a :8800/api/agents/relaunch/{agent}."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"http://localhost:8800/api/agents/relaunch/{agent}")
            return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/sleep/{agent}")
async def proxy_sleep(agent: str):
    """Proxy a :8800/api/agents/sleep/{agent}."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"http://localhost:8800/api/agents/sleep/{agent}")
            return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Cost Dashboard (ALICE 2026-05-20) ────────────────────────────────────────

@app.get("/api/soul/cost-dashboard")
async def cost_dashboard(history: int = 30):
    """Devuelve último cost_report + serie histórica.
    Lee memorias category='cost_report' source='cost_calculator'.
    """
    pool = _pool_ok()
    rows = await pool.fetch(
        "SELECT id, content, metadata, created_at FROM soul_v3.memories "
        "WHERE agent='ALICE' AND category='cost_report' AND source='cost_calculator' "
        "ORDER BY created_at DESC LIMIT $1",
        history,
    )
    if not rows:
        return {"latest": None, "history": [], "note": "Sin cost reports aún. Ejecuta cost_calculator.py."}
    reports = []
    for r in rows:
        meta = _json_obj(r["metadata"])
        reports.append({
            "id": r["id"],
            "ts": _dt(r["created_at"]),
            "window_hours": meta.get("window_hours"),
            "daily_usd": meta.get("daily_usd"),
            "monthly_proj_usd": meta.get("monthly_proj_usd"),
            "input_tokens": meta.get("input_tokens"),
            "output_tokens": meta.get("output_tokens"),
            "by_agent": meta.get("by_agent", {}),
        })
    return {
        "latest": reports[0],
        "history": reports,
        "pricing": rows[0]["metadata"] and _json_obj(rows[0]["metadata"]).get("pricing_table", {}),
    }


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"ok": True, "ts": time.time()}


# ── Static frontend ───────────────────────────────────────────────────────────

import os
_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        return FileResponse(os.path.join(_DIST, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("soul_api:app", host="0.0.0.0", port=8850, reload=False, log_level="warning")
