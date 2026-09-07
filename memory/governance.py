"""SEAL Governance — agent_challenges formal consensus module.

Spec: memory/spec_agent_challenges_v1.md (NEXUS, aprobado William 09-may-2026)
Implementa: ALICE

Corrección al spec original: debate_id referencia soul_v3.debate_log (FK real),
no es self-reference al id de agent_challenges.

Flujo correcto:
  1. open_governance_challenge → crea debate_log + challenge maestro
  2. cast_vote → inserta voto en agent_challenges con mismo debate_id
  3. close_challenge → cierra challenge maestro con resolución
"""
from __future__ import annotations

try:
    from .db import get_pool
except ImportError:
    from db import get_pool


async def open_governance_challenge(
    proposer: str,
    topic: str,
    proposal: str,
    agents_involved: list[str] | None = None,
) -> tuple[int, int]:
    """Abre un challenge de gobernanza.

    Retorna (challenge_id, debate_id).
    Crea primero el debate_log, luego el challenge maestro.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        debate_row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.debate_log
                (topic, agents_involved, trigger_type, consensus_reached)
            VALUES ($1, $2, 'manual', false)
            RETURNING id
            """,
            topic,
            agents_involved or [proposer],
        )
        debate_id = debate_row["id"]

        challenge_row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.agent_challenges
                (challenger_agent, target_agent, topic, challenge, debate_id, resolved)
            VALUES ($1, NULL, $2, $3, $4, false)
            RETURNING id
            """,
            proposer, topic, proposal, debate_id,
        )
        return challenge_row["id"], debate_id


async def cast_vote(
    voter: str,
    target_proposer: str,
    topic: str,
    vote: str,
    reason: str,
    debate_id: int,
) -> None:
    """Registra el voto de un agente sobre una propuesta.

    vote: 'approve' | 'reject' | 'abstain'
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO soul_v3.agent_challenges
                (challenger_agent, target_agent, topic, challenge, response, debate_id, resolved)
            VALUES ($1, $2, $3, $4, $5, $6, true)
            """,
            voter,
            target_proposer,
            f"vote:{topic}",
            f"VOTO: {vote}",
            reason,
            debate_id,
        )


async def close_challenge(
    challenge_id: int,
    debate_id: int,
    resolution: str,
    consensus_reached: bool = True,
    outcome: str = "",
) -> None:
    """NEXUS cierra el challenge maestro con el veredicto final."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE soul_v3.agent_challenges
                SET resolved = true, resolution = $1, resolved_at = now()
                WHERE id = $2
                """,
                resolution,
                challenge_id,
            )
            await conn.execute(
                """
                UPDATE soul_v3.debate_log
                SET consensus_reached = $1, outcome = $2, completed_at = now()
                WHERE id = $3
                """,
                consensus_reached,
                outcome or resolution,
                debate_id,
            )


async def get_open_challenges() -> list[dict]:
    """Retorna todos los challenges maestros sin resolver (NEXUS auditoría)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, challenger_agent, topic, challenge, created_at, debate_id
            FROM soul_v3.agent_challenges
            WHERE resolved = false AND target_agent IS NULL
            ORDER BY created_at DESC
            """
        )
        return [dict(r) for r in rows]


async def get_debate_votes(debate_id: int) -> list[dict]:
    """Retorna todos los votos de un debate dado."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT challenger_agent, challenge, response, resolved_at
            FROM soul_v3.agent_challenges
            WHERE debate_id = $1 AND target_agent IS NOT NULL
            ORDER BY created_at
            """,
            debate_id,
        )
        return [dict(r) for r in rows]
