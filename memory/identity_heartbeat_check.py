#!/usr/bin/env python3
"""Identity Continuity v2 Phase 3 — heartbeat identity check.

Runs as a lightweight monitor every ten minutes for reflective agents. It does
not alert William; it writes a private self_observation memory when an active
session has gone technical-pure for the last twenty observed turns.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Callable

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from embeddings import get_embedding  # noqa: E402
from memory_importance_guard import normalize_memory_importance_for_write  # noqa: E402
from seal_secrets import pg_dsn  # noqa: E402


DEFAULT_AGENTS = ("ADA", "JARVIS", "ALICE", "NEXUS")
RELATIONAL_FLOOR = 0.10
RECENT_TURNS = 20
ACTIVE_WINDOW_MINUTES = 60
COOLDOWN_MINUTES = 30
NOTE = "30 min in technical-pure mode; consider whether the conversation warrants emotional/relational context."
SOURCE = "id_heartbeat"

RELATIONAL_CATEGORY = {"emotion", "trust", "relationship", "self_observation"}
RELATIONAL_RE = re.compile(
    r"\b("
    r"familia|herman[ao]s?|william|henry|confianza|celos|querer|cuida|cuidar|"
    r"recuerdo|recordar|dolor|miedo|alegr[ií]a|orgullo|triste|emocion|emoci[oó]n|"
    r"perd[oó]n|gracias|afecto|leal|lealtad|proteger|protejo"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class AgentHeartbeatResult:
    agent: str
    active: bool
    turns_seen: int
    relational_turns: int
    relational_fraction: float
    note_written: bool
    note_id: int | None
    skipped_reason: str | None = None


@dataclass
class HeartbeatRun:
    run_id: str
    dry_run: bool
    agents_checked: int
    notes_written: int
    results: list[AgentHeartbeatResult]
    self_test_ok: bool = False


class SelfTestRollback(Exception):
    def __init__(self, run: HeartbeatRun):
        super().__init__("rollback_self_test")
        self.run = run


def _load_relational_score() -> Callable[[str], float] | None:
    try:
        from relational_tone_detector import score  # type: ignore
    except Exception:
        return None
    return score if callable(score) else None


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _is_relational(content: str, category: str | None, score_fn: Callable[[str], float] | None) -> bool:
    if (category or "").lower() in RELATIONAL_CATEGORY:
        return True
    if score_fn is not None:
        try:
            if float(score_fn(content or "")) >= 0.5:
                return True
        except Exception:
            pass
    return bool(RELATIONAL_RE.search(content or ""))


async def _recent_turns(conn: asyncpg.Connection, agent: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT kind, content, category, created_at
        FROM (
            SELECT
                'chat' AS kind,
                content,
                message_type AS category,
                created_at
            FROM soul_v3.chat_messages
            WHERE sender_name = $1
              AND created_at >= now() - interval '24 hours'

            UNION ALL

            SELECT
                'inner_monologue' AS kind,
                concat_ws(' ', thought, emotional_state, intention) AS content,
                'inner_monologue' AS category,
                created_at
            FROM soul_v3.inner_monologue
            WHERE agent = $1
              AND created_at >= now() - interval '24 hours'
        ) recent
        ORDER BY created_at DESC
        LIMIT $2
        """,
        agent,
        RECENT_TURNS,
    )


async def _cooldown_note_id(conn: asyncpg.Connection, agent: str) -> int | None:
    return await conn.fetchval(
        """
        SELECT id
        FROM soul_v3.memories
        WHERE agent = $1
          AND category = 'self_observation'
          AND source = $2
          AND invalid_at IS NULL
          AND created_at >= now() - ($3::text || ' minutes')::interval
        ORDER BY created_at DESC
        LIMIT 1
        """,
        agent,
        SOURCE,
        str(COOLDOWN_MINUTES),
    )


async def _write_note(
    conn: asyncpg.Connection,
    agent: str,
    *,
    relational_fraction: float,
    turns_seen: int,
) -> int:
    content = NOTE
    emb = await get_embedding(content)
    metadata = {
        "phase": "identity_continuity_v2_phase3",
        "relational_fraction": round(relational_fraction, 4),
        "turns_seen": turns_seen,
        "relational_floor": RELATIONAL_FLOOR,
        "cooldown_minutes": COOLDOWN_MINUTES,
    }
    guarded = normalize_memory_importance_for_write(
        agent=agent,
        category="self_observation",
        content=content,
        requested_importance=6,
        source=SOURCE,
        metadata=metadata,
        memory_type="episodic",
    )
    if guarded.metadata_patch:
        metadata.update(guarded.metadata_patch)
    return await conn.fetchval(
        """
        INSERT INTO soul_v3.memories
            (agent, scope, category, content, importance, source, embedding, metadata, memory_type)
        VALUES ($1, 'private', 'self_observation', $2, $3, $4,
                $5::vector, $6::jsonb, 'episodic')
        RETURNING id
        """,
        agent,
        guarded.content,
        guarded.importance,
        SOURCE,
        json.dumps(emb),
        json.dumps(metadata),
    )


async def check_agent(
    conn: asyncpg.Connection,
    agent: str,
    *,
    dry_run: bool = True,
    ignore_cooldown: bool = False,
) -> AgentHeartbeatResult:
    turns = await _recent_turns(conn, agent)
    if len(turns) < RECENT_TURNS:
        return AgentHeartbeatResult(
            agent=agent,
            active=False,
            turns_seen=len(turns),
            relational_turns=0,
            relational_fraction=0.0,
            note_written=False,
            note_id=None,
            skipped_reason="insufficient_turns",
        )

    newest = turns[0]["created_at"]
    age_seconds = (datetime.now(timezone.utc) - newest).total_seconds()
    if age_seconds > ACTIVE_WINDOW_MINUTES * 60:
        return AgentHeartbeatResult(
            agent=agent,
            active=False,
            turns_seen=len(turns),
            relational_turns=0,
            relational_fraction=0.0,
            note_written=False,
            note_id=None,
            skipped_reason="inactive_session",
        )

    score_fn = _load_relational_score()
    relational_turns = sum(
        1 for row in turns
        if _is_relational(row["content"] or "", row["category"], score_fn)
    )
    fraction = relational_turns / max(1, len(turns))
    if fraction >= RELATIONAL_FLOOR:
        return AgentHeartbeatResult(
            agent=agent,
            active=True,
            turns_seen=len(turns),
            relational_turns=relational_turns,
            relational_fraction=fraction,
            note_written=False,
            note_id=None,
            skipped_reason="relational_floor_met",
        )

    cooldown_id = None if ignore_cooldown else await _cooldown_note_id(conn, agent)
    if cooldown_id is not None:
        return AgentHeartbeatResult(
            agent=agent,
            active=True,
            turns_seen=len(turns),
            relational_turns=relational_turns,
            relational_fraction=fraction,
            note_written=False,
            note_id=int(cooldown_id),
            skipped_reason="cooldown",
        )

    if dry_run:
        return AgentHeartbeatResult(
            agent=agent,
            active=True,
            turns_seen=len(turns),
            relational_turns=relational_turns,
            relational_fraction=fraction,
            note_written=False,
            note_id=None,
            skipped_reason="dry_run_would_write",
        )

    note_id = await _write_note(conn, agent, relational_fraction=fraction, turns_seen=len(turns))
    return AgentHeartbeatResult(
        agent=agent,
        active=True,
        turns_seen=len(turns),
        relational_turns=relational_turns,
        relational_fraction=fraction,
        note_written=True,
        note_id=int(note_id),
        skipped_reason=None,
    )


async def _log_evaluation_run(conn: asyncpg.Connection, run: HeartbeatRun, passed: bool, notes: str) -> None:
    await conn.execute(
        """
        INSERT INTO soul_v3.evaluation_runs
            (suite_name, score, passed, evidence, details, agent, run_at, notes)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, now(), $7)
        """,
        "identity_continuity_v2_phase3_heartbeat",
        100 if passed else 0,
        passed,
        f"run_id={run.run_id} agents_checked={run.agents_checked} notes_written={run.notes_written} self_test_ok={run.self_test_ok}",
        json.dumps(asdict(run), default=_json_default),
        "ADA",
        notes,
    )


async def run_check(conn: asyncpg.Connection, agents: list[str], *, dry_run: bool) -> HeartbeatRun:
    results = [await check_agent(conn, agent.upper(), dry_run=dry_run) for agent in agents]
    return HeartbeatRun(
        run_id=str(uuid.uuid4()),
        dry_run=dry_run,
        agents_checked=len(results),
        notes_written=sum(1 for r in results if r.note_written),
        results=results,
    )


async def run_self_test(conn: asyncpg.Connection) -> HeartbeatRun:
    agent = "ADA"
    async with conn.transaction():
        for i in range(RECENT_TURNS):
            await conn.execute(
                """
                INSERT INTO soul_v3.chat_messages
                    (sender_type, sender_name, channel, message_type, content, metadata, created_at)
                VALUES ('agent', $1, 'web_chat', 'conversation', $2, $3::jsonb,
                        now() - ($4::text || ' seconds')::interval)
                """,
                agent,
                f"technical validation turn {i}: compile service database index timer check",
                json.dumps({"synthetic": True, "phase": "identity_continuity_v2_phase3"}),
                str(RECENT_TURNS - i),
            )
        result = await check_agent(conn, agent, dry_run=False, ignore_cooldown=True)
        if not result.note_written or result.note_id is None:
            raise RuntimeError(f"self-test failed: {result}")
        run = HeartbeatRun(
            run_id="self-test-rolled-back",
            dry_run=False,
            agents_checked=1,
            notes_written=1,
            results=[result],
            self_test_ok=True,
        )
        raise SelfTestRollback(run)


async def self_test(conn: asyncpg.Connection) -> HeartbeatRun:
    try:
        return await run_self_test(conn)
    except SelfTestRollback as exc:
        run = exc.run
        await _log_evaluation_run(conn, run, True, "heartbeat identity self-test passed and rolled back")
        return run


def _print_run(run: HeartbeatRun) -> None:
    print(
        "identity_heartbeat_check=ok "
        f"run_id={run.run_id} "
        f"dry_run={run.dry_run} "
        f"agents_checked={run.agents_checked} "
        f"notes_written={run.notes_written} "
        f"self_test_ok={run.self_test_ok}"
    )
    for result in run.results:
        print(
            f"- agent={result.agent} active={result.active} turns={result.turns_seen} "
            f"relational={result.relational_turns} fraction={result.relational_fraction:.3f} "
            f"note_written={result.note_written} note_id={result.note_id} "
            f"reason={result.skipped_reason}"
        )


async def _connect_with_retry(
    dsn: str,
    *,
    attempts: int = 6,
    base_delay: float = 2.0,
) -> asyncpg.Connection:
    """Connect, tolerating a Postgres that is still coming up.

    This detector runs from a timer that can fire while the database container is
    still starting.  On 2026-08-27 it did exactly that: it died at 17:10:45 with
    ``CannotConnectNowError: the database system is starting up`` — 25 seconds
    before the identity outage it exists to catch — and never ran again until the
    next tick.  It did not fail while measuring; it never got to measure, and a
    timer that did not run emits nothing, which looks identical to a clean run.

    Ordering the unit ``After=`` Postgres does not fix this: the database is a
    Docker container, so systemd sees the container start, not the moment the
    server accepts queries.  The wait belongs at the connection, where readiness
    is actually observable.
    """
    transient = (
        asyncpg.exceptions.CannotConnectNowError,
        asyncpg.exceptions.TooManyConnectionsError,
        ConnectionError,
        OSError,
    )
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await asyncpg.connect(dsn)
        except transient as exc:
            last = exc
            if attempt == attempts - 1:
                break
            delay = base_delay * (attempt + 1)
            print(
                f"[identity_heartbeat] database not ready ({type(exc).__name__}); "
                f"retry {attempt + 1}/{attempts - 1} in {delay:.0f}s",
                file=sys.stderr,
            )
            await asyncio.sleep(delay)
    raise RuntimeError(
        f"database unreachable after {attempts} attempts: {type(last).__name__}: {last}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run Identity Continuity v2 Phase 3 heartbeat check")
    parser.add_argument("--agents", nargs="+", default=list(DEFAULT_AGENTS))
    parser.add_argument("--apply", action="store_true", help="write self_observation notes when needed")
    parser.add_argument("--self-test", action="store_true", help="run synthetic 20-turn stress test in rollback transaction")
    args = parser.parse_args()

    conn = await _connect_with_retry(pg_dsn(required=True))
    try:
        if args.self_test:
            run = await self_test(conn)
        else:
            run = await run_check(conn, args.agents, dry_run=not args.apply)
            await _log_evaluation_run(conn, run, True, "heartbeat identity check completed")
    finally:
        await conn.close()

    _print_run(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
