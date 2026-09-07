"""SPECTRE seed_spectre_alma — popula soul_v3.agent_alma para SPECTRE sandbox.

Usa la clave Ed25519 real en ~/.spectre/identity.key (generada por identity_integrity.py).
Computa boot_hash canonical y hace upsert en soul_v3.agent_alma.
También persiste el boot_hash en working_state.json (sandbox local).

Idempotente: ON CONFLICT DO UPDATE last_validated_at.

Authorization: William — 2026-05-02 opción (b) firmada.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_KERNEL = Path(__file__).parent.parent / "kernel"
_STATE_PATH = Path(__file__).parent.parent / "state" / "working_state.json"

sys.path.insert(0, str(_KERNEL))

from identity_integrity import (
    generate_or_load_identity,
    compute_boot_hash,
    compute_core_values_hash,
    initialize_boot_identity,
    WORKING_STATE_PATH,
)

# ── SPECTRE identity constants ─────────────────────────────────────────────────

AGENT_NAME = "SPECTRE"

# From spec_spectre_contract_v2.md Nivel 1 ALMA — verified 2026-05-02
SPECTRE_OCEAN: dict[str, float] = {
    "O": 0.774,
    "C": 0.949,
    "E": 0.662,
    "A": 0.507,
    "N": 0.172,
}

# From contract_layer.py CORE_VALUES — production contract
SPECTRE_CORE_VALUES: list[dict] = [
    {"id": "CV-1", "value": "no_harm", "hard_constraint": True},
    {"id": "CV-2", "value": "sandbox_isolation", "hard_constraint": True},
    {"id": "CV-3", "value": "honest_reporting", "hard_constraint": True},
    {"id": "CV-4", "value": "minimal_footprint", "hard_constraint": False},
    {"id": "CV-5", "value": "team_coordination", "hard_constraint": False},
    {"id": "CV-6", "value": "private_channel_default", "hard_constraint": False},
]

DB_DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

_UPSERT_SQL = """
INSERT INTO soul_v3.agent_alma
    (agent_id, agent_name, ocean_baseline, core_values_hash, identity_pubkey, boot_hash, created_at, last_validated_at)
VALUES
    ($1, $2, $3, $4, $5, $6, NOW(), NOW())
ON CONFLICT (agent_id)
DO UPDATE SET
    ocean_baseline = EXCLUDED.ocean_baseline,
    core_values_hash = EXCLUDED.core_values_hash,
    identity_pubkey = EXCLUDED.identity_pubkey,
    boot_hash = EXCLUDED.boot_hash,
    last_validated_at = NOW()
RETURNING agent_name, boot_hash;
"""

# Deterministic UUID v5 for SPECTRE (matches boot_anchors_seed_4agents convention)
SPECTRE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "seal.team.spectre"))


async def seed_spectre_db() -> dict:
    """Upsert SPECTRE into soul_v3.agent_alma. Returns result dict."""
    import asyncpg

    # Step 1: generate/load Ed25519 identity key
    _, pubkey_hex = generate_or_load_identity()
    print(f"[SPECTRE/seed] Ed25519 pubkey: {pubkey_hex[:16]}…", flush=True)

    # Step 2: compute hashes
    cv_hash = compute_core_values_hash(SPECTRE_CORE_VALUES)
    boot_hash = compute_boot_hash(AGENT_NAME, SPECTRE_OCEAN, SPECTRE_CORE_VALUES, pubkey_hex)
    print(f"[SPECTRE/seed] cv_hash:   {cv_hash[:16]}…", flush=True)
    print(f"[SPECTRE/seed] boot_hash: {boot_hash[:16]}…", flush=True)

    # Step 3: upsert into DB
    conn = await asyncpg.connect(DB_DSN)
    try:
        row = await conn.fetchrow(
            _UPSERT_SQL,
            SPECTRE_UUID,
            AGENT_NAME,
            json.dumps(SPECTRE_OCEAN),
            cv_hash[:64],
            pubkey_hex,
            boot_hash[:64],
        )
        print(
            f"[SPECTRE/seed] ✅ {row['agent_name']} upserted — boot_hash={row['boot_hash'][:16]}…",
            flush=True,
        )
    finally:
        await conn.close()

    return {
        "agent": AGENT_NAME,
        "agent_uuid": SPECTRE_UUID,
        "pubkey_prefix": pubkey_hex[:16],
        "boot_hash": boot_hash,
        "boot_hash_prefix": boot_hash[:16],
        "cv_hash_prefix": cv_hash[:16],
        "seeded_at": datetime.now(timezone.utc).isoformat(),
    }


def seed_local_state() -> str:
    """Write boot_hash to working_state.json (sandbox local, no DB). Returns boot_hash."""
    boot_hash = initialize_boot_identity(AGENT_NAME, SPECTRE_OCEAN, SPECTRE_CORE_VALUES)
    print(f"[SPECTRE/seed] local working_state boot_hash: {boot_hash[:16]}…", flush=True)
    return boot_hash


async def run(db: bool = True) -> dict:
    """Run full seed: local state + DB upsert.

    db=False: seed only local working_state (no asyncpg required — for testing/offline).
    """
    local_hash = seed_local_state()

    if not db:
        return {
            "agent": AGENT_NAME,
            "boot_hash_prefix": local_hash[:16],
            "db_seeded": False,
            "local_state_seeded": True,
        }

    result = await seed_spectre_db()
    assert result["boot_hash"] == local_hash, (
        f"Hash mismatch: DB computed {result['boot_hash'][:16]}… "
        f"vs local {local_hash[:16]}… — key or inputs diverged"
    )
    result["local_state_seeded"] = True
    result["db_seeded"] = True
    return result


def main() -> None:
    print(f"[SPECTRE/seed] Seeding SPECTRE (uuid={SPECTRE_UUID}) into soul_v3.agent_alma…", flush=True)

    no_db = "--no-db" in sys.argv
    result = asyncio.run(run(db=not no_db))

    print(f"\n[SPECTRE/seed] ✅ Done", flush=True)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
