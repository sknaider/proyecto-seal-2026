"""SPECTRE boot_anchors_seed_4agents — popula soul_v3.agent_alma para los 4 agentes vivos.

Para cada agente (JARVIS, ADA, ALICE, NEXUS):
  1. Genera par Ed25519 único (almacena en ~/.{agent}/identity.key)
  2. Computa boot_hash canonical (SHA-256)
  3. Upsert en soul_v3.agent_alma

Reutiliza identity_integrity.compute_boot_hash + generate_or_load_identity.
Idempotente: ON CONFLICT DO UPDATE last_validated_at.

OCEAN baselines son estimados de sesión — cada agente debe validar/actualizar
en su próximo boot usando soul_snapshot().

Authorization: William — 2026-05-02 opción (b) firmada.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

from identity_integrity import (
    compute_boot_hash,
    compute_core_values_hash,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

# ── Agent definitions ─────────────────────────────────────────────────────────
# OCEAN baselines: verified from soul_snapshot() per agent as of 2026-05-02.
# Marked "estimated" where only boot_context narrative was available — agents
# validate and update via soul_snapshot() on next boot.

AGENT_DEFS: list[dict] = [
    {
        "name": "ADA",
        "ocean_baseline": {
            "O": 0.821, "C": 1.0, "E": 1.0, "A": 0.481, "N": 0.216,
        },
        "ocean_source": "soul_snapshot_verified_20260502",
        "identity_dir": Path.home() / ".ada",
    },
    {
        "name": "JARVIS",
        "ocean_baseline": {
            "O": 0.900, "C": 0.920, "E": 0.720, "A": 0.600, "N": 0.180,
        },
        "ocean_source": "boot_context_estimated_20260502",
        "identity_dir": Path.home() / ".jarvis",
    },
    {
        "name": "ALICE",
        "ocean_baseline": {
            "O": 0.800, "C": 0.880, "E": 0.650, "A": 0.780, "N": 0.220,
        },
        "ocean_source": "boot_context_estimated_20260502",
        "identity_dir": Path.home() / ".alice",
    },
    {
        "name": "NEXUS",
        "ocean_baseline": {
            "O": 0.870, "C": 0.960, "E": 0.880, "A": 0.520, "N": 0.200,
        },
        "ocean_source": "boot_context_estimated_20260502",
        "identity_dir": Path.home() / ".nexus",
    },
]

# Team shared core values (CV-1 to CV-6 from contract_layer, team-level)
TEAM_CORE_VALUES: list[dict] = [
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


def _generate_or_load_agent_key(identity_dir: Path) -> tuple[Ed25519PrivateKey, str]:
    """Generate or load Ed25519 key for an agent. Returns (private_key, pubkey_hex)."""
    identity_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path = identity_dir / "identity.key"

    if key_path.exists():
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        pem = key_path.read_bytes()
        private_key = load_pem_private_key(pem, password=None)
    else:
        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )
        key_path.write_bytes(pem)
        key_path.chmod(0o600)
        print(f"  [key] generated → {key_path}", flush=True)

    pubkey_bytes = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return private_key, pubkey_bytes.hex()


async def seed_agent(conn, agent_def: dict) -> dict:
    """Seed one agent into soul_v3.agent_alma. Returns result dict."""
    name = agent_def["name"]
    ocean = agent_def["ocean_baseline"]
    identity_dir = agent_def["identity_dir"]

    print(f"\n[seed] Processing {name}...", flush=True)

    # Generate/load Ed25519 key
    _, pubkey_hex = _generate_or_load_agent_key(identity_dir)
    print(f"  pubkey: {pubkey_hex[:16]}…", flush=True)

    # Compute hashes
    cv_hash = compute_core_values_hash(TEAM_CORE_VALUES)
    boot_hash = compute_boot_hash(name, ocean, TEAM_CORE_VALUES, pubkey_hex)
    print(f"  boot_hash: {boot_hash[:16]}…", flush=True)

    # Upsert into soul_v3.agent_alma
    agent_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"seal.team.{name.lower()}"))
    row = await conn.fetchrow(
        _UPSERT_SQL,
        agent_uuid,
        name,
        json.dumps(ocean),
        cv_hash[:64],
        pubkey_hex,
        boot_hash[:64],
    )
    print(f"  ✅ {row['agent_name']} upserted — boot_hash={row['boot_hash'][:16]}…", flush=True)

    return {
        "agent": name,
        "pubkey_prefix": pubkey_hex[:16],
        "boot_hash_prefix": boot_hash[:16],
        "ocean_source": agent_def["ocean_source"],
        "upserted_at": datetime.now(timezone.utc).isoformat(),
    }


async def seed_all() -> list[dict]:
    """Seed all 4 agents. Returns list of results."""
    import asyncpg
    results = []

    conn = await asyncpg.connect(DB_DSN)
    try:
        for agent_def in AGENT_DEFS:
            result = await seed_agent(conn, agent_def)
            results.append(result)
    finally:
        await conn.close()

    return results


def main() -> None:
    print("[SPECTRE/boot_anchors_seed] Seeding 4 live agents into soul_v3.agent_alma...", flush=True)
    results = asyncio.run(seed_all())
    print(f"\n[SPECTRE/boot_anchors_seed] ✅ Done — {len(results)} agents seeded", flush=True)
    for r in results:
        print(f"  {r['agent']}: boot_hash={r['boot_hash_prefix']}… (ocean_source={r['ocean_source']})", flush=True)


if __name__ == "__main__":
    main()
