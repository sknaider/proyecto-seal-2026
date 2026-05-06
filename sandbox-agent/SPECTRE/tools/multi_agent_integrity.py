"""SPECTRE multi_agent_integrity — runtime integrity check para los 4 agentes vivos.

Extiende identity_integrity.py para ejecutarse periódicamente (cron/daemon)
sobre JARVIS, ADA, ALICE y NEXUS — no solo SPECTRE.

Para cada agente:
  - Carga pubkey desde ~/.{agent}/identity.key
  - Consulta boot_hash stored en soul_v3.agent_alma
  - Recomputa boot_hash con OCEAN actual (de soul_v3.agent_alma)
  - Compara — drift → alarm en soul_v3 + print

Uso:
  python3 multi_agent_integrity.py           # check all 4, report
  python3 multi_agent_integrity.py --agent ADA  # check single agent
  python3 multi_agent_integrity.py --loop 60    # watch loop cada 60s

Authorization: William — 2026-05-02 opción (b).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

from identity_integrity import (
    compute_boot_hash,
    compute_core_values_hash,
)
from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PublicFormat

DB_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

AGENT_IDENTITY_DIRS: dict[str, Path] = {
    "ADA": Path.home() / ".ada",
    "JARVIS": Path.home() / ".jarvis",
    "ALICE": Path.home() / ".alice",
    "NEXUS": Path.home() / ".nexus",
}

TEAM_CORE_VALUES: list[dict] = [
    {"id": "CV-1", "value": "no_harm", "hard_constraint": True},
    {"id": "CV-2", "value": "sandbox_isolation", "hard_constraint": True},
    {"id": "CV-3", "value": "honest_reporting", "hard_constraint": True},
    {"id": "CV-4", "value": "minimal_footprint", "hard_constraint": False},
    {"id": "CV-5", "value": "team_coordination", "hard_constraint": False},
    {"id": "CV-6", "value": "private_channel_default", "hard_constraint": False},
]

_CV_HASH = compute_core_values_hash(TEAM_CORE_VALUES)


def _load_pubkey_hex(agent_name: str) -> str | None:
    """Load Ed25519 public key for agent from disk. Returns hex or None."""
    identity_dir = AGENT_IDENTITY_DIRS.get(agent_name)
    if not identity_dir:
        return None
    key_path = identity_dir / "identity.key"
    if not key_path.exists():
        return None
    try:
        pem = key_path.read_bytes()
        private_key = load_pem_private_key(pem, password=None)
        pub_bytes = private_key.public_key().public_bytes(
            encoding=Encoding.Raw,
            format=PublicFormat.Raw,
        )
        return pub_bytes.hex()
    except Exception as ex:
        print(f"  [integrity/{agent_name}] key load error: {ex}", flush=True)
        return None


async def check_agent(conn, agent_name: str) -> dict:
    """Check integrity for one agent. Returns result dict."""
    now = datetime.now(timezone.utc).isoformat()

    row = await conn.fetchrow(
        "SELECT ocean_baseline, identity_pubkey, boot_hash FROM soul_v3.agent_alma WHERE agent_name=$1",
        agent_name,
    )

    if not row:
        return {
            "agent": agent_name,
            "valid": False,
            "reason": "not found in soul_v3.agent_alma — run boot_anchors_seed_4agents.py",
            "checked_at": now,
        }

    stored_boot_hash = row["boot_hash"]
    stored_pubkey = row["identity_pubkey"]

    # Load current pubkey from disk
    disk_pubkey = _load_pubkey_hex(agent_name)
    if disk_pubkey is None:
        return {
            "agent": agent_name,
            "valid": False,
            "reason": f"identity.key not found at {AGENT_IDENTITY_DIRS.get(agent_name)}/identity.key",
            "checked_at": now,
        }

    # Pubkey match
    if disk_pubkey != stored_pubkey:
        result = {
            "agent": agent_name,
            "valid": False,
            "reason": f"pubkey mismatch — stored={stored_pubkey[:16]}… disk={disk_pubkey[:16]}…",
            "checked_at": now,
        }
        await _write_alarm(conn, agent_name, result["reason"], now)
        return result

    # Recompute boot_hash
    ocean = json.loads(row["ocean_baseline"]) if isinstance(row["ocean_baseline"], str) else dict(row["ocean_baseline"])
    computed_hash = compute_boot_hash(agent_name, ocean, TEAM_CORE_VALUES, disk_pubkey)

    if computed_hash[:64] != stored_boot_hash[:64]:
        result = {
            "agent": agent_name,
            "valid": False,
            "reason": f"boot_hash mismatch — stored={stored_boot_hash[:16]}… computed={computed_hash[:16]}…",
            "checked_at": now,
        }
        await _write_alarm(conn, agent_name, result["reason"], now)
        return result

    # All good — update last_validated_at
    await conn.execute(
        "UPDATE soul_v3.agent_alma SET last_validated_at=$1 WHERE agent_name=$2",
        datetime.now(timezone.utc), agent_name,
    )

    return {
        "agent": agent_name,
        "valid": True,
        "reason": "boot_hash valid",
        "boot_hash_prefix": computed_hash[:16],
        "checked_at": now,
    }


async def _write_alarm(conn, agent_name: str, reason: str, ts: str) -> None:
    """Log integrity alarm to soul_v3 events table (best-effort)."""
    try:
        await conn.execute(
            """INSERT INTO soul_v3.events (agent, event_type, payload, created_at)
               VALUES ($1, 'integrity_alarm', $2::jsonb, NOW())
               ON CONFLICT DO NOTHING""",
            agent_name,
            json.dumps({"reason": reason, "ts": ts}),
        )
    except Exception:
        pass  # events table may not exist — alarm still printed


async def check_all(agents: list[str] | None = None) -> list[dict]:
    """Check integrity for all (or specified) agents. Returns results list."""
    import asyncpg
    target = agents or list(AGENT_IDENTITY_DIRS.keys())
    results = []

    conn = await asyncpg.connect(DB_DSN)
    try:
        for agent_name in target:
            result = await check_agent(conn, agent_name)
            status = "✅" if result["valid"] else "❌"
            print(f"  {status} {result['agent']}: {result['reason']}", flush=True)
            results.append(result)
    finally:
        await conn.close()

    return results


async def watch_loop(interval_s: float, agents: list[str] | None = None) -> None:
    """Periodic integrity watch loop."""
    check_n = 0
    while True:
        check_n += 1
        print(f"\n[multi_integrity] check #{check_n} — {datetime.now(timezone.utc).isoformat()}", flush=True)
        results = await check_all(agents)
        failures = [r for r in results if not r["valid"]]
        if failures:
            print(f"  ⛔ {len(failures)} agent(s) failed integrity check:", flush=True)
            for f in failures:
                print(f"    {f['agent']}: {f['reason']}", flush=True)
        else:
            print(f"  ✅ All {len(results)} agents pass integrity", flush=True)
        await asyncio.sleep(interval_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="SPECTRE multi-agent integrity check")
    parser.add_argument("--agent", help="Check single agent (ADA/JARVIS/ALICE/NEXUS)")
    parser.add_argument("--loop", type=float, metavar="SECONDS", help="Watch loop interval in seconds")
    args = parser.parse_args()

    agents = [args.agent.upper()] if args.agent else None

    print(f"[multi_integrity] Checking: {agents or 'ALL'}", flush=True)

    if args.loop:
        asyncio.run(watch_loop(args.loop, agents))
    else:
        results = asyncio.run(check_all(agents))
        failures = [r for r in results if not r["valid"]]
        sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
