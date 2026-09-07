"""SPECTRE identity_integrity — F1 boot hash + §3.6 runtime integrity check.

Ref: spec_spectre_contract_v2_1_addendum.md F1 (sandbox v1 implementation):
  - boot_hash = SHA-256(canonical_json(agent_id, ocean_baseline, core_values, pubkey))
  - identity_keys = Ed25519 pair. Private: ~/.spectre/identity.key (chmod 0600)
  - Validate on each boot: recompute hash, compare with stored → mismatch → shutdown

Ref: spec_spectre_contract_v2.md §3.6 integrity_check:
  - Compares runtime state vs Nivel 1 every N seconds
  - Fires alarm if drift > threshold
  - drift: core_values hash mismatch or working_state corruption

Invariants:
  - Private key NEVER leaves ~/.spectre/identity.key
  - boot_hash is the single source of truth for identity continuity
  - If integrity_check_loop detects drift → it signals stop_event + logs alarm
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

# ── Paths ─────────────────────────────────────────────────────────────────────

IDENTITY_DIR = Path.home() / ".spectre"
IDENTITY_KEY_PATH = IDENTITY_DIR / "identity.key"
_BASE = Path(__file__).parent.parent
WORKING_STATE_PATH = _BASE / "state" / "working_state.json"

# ── Key lifecycle ─────────────────────────────────────────────────────────────

def generate_or_load_identity() -> tuple[Ed25519PrivateKey, str]:
    """Return (private_key, pubkey_hex). Generates key pair on first call.

    Private key stored at ~/.spectre/identity.key (PEM, chmod 0600).
    Returns public key as lowercase hex string (64 chars).
    """
    IDENTITY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    if IDENTITY_KEY_PATH.exists():
        pem = IDENTITY_KEY_PATH.read_bytes()
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        private_key = load_pem_private_key(pem, password=None)
    else:
        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )
        IDENTITY_KEY_PATH.write_bytes(pem)
        IDENTITY_KEY_PATH.chmod(0o600)
        print(f"[SPECTRE/identity] Ed25519 key generated → {IDENTITY_KEY_PATH}", flush=True)

    pubkey_bytes = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    pubkey_hex = pubkey_bytes.hex()
    return private_key, pubkey_hex


def pubkey_hex() -> str:
    """Load public key from disk and return as hex. Generates if not exists."""
    _, pk = generate_or_load_identity()
    return pk


# ── Boot hash ─────────────────────────────────────────────────────────────────

def compute_boot_hash(
    agent_id: str,
    ocean_baseline: dict[str, float],
    core_values: list[dict[str, Any]],
    identity_pubkey: str,
) -> str:
    """Compute boot_hash = SHA-256(canonical_json(agent_id, ocean_baseline, core_values, pubkey)).

    Canonical = sorted keys, no whitespace, ensure_ascii=True.
    Returns lowercase hex string (64 chars).
    """
    canonical = json.dumps(
        {
            "agent_id": agent_id,
            "ocean_baseline": ocean_baseline,
            "core_values": core_values,
            "identity_pubkey": identity_pubkey,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def compute_core_values_hash(core_values: list[dict[str, Any]]) -> str:
    """Hash of core_values list only (for runtime drift detection)."""
    canonical = json.dumps(core_values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


# ── Boot validation ───────────────────────────────────────────────────────────

_BOOT_HASH_KEY = "boot_hash"
_PUBKEY_KEY = "identity_pubkey"


def _read_state() -> dict:
    try:
        if WORKING_STATE_PATH.exists():
            return json.loads(WORKING_STATE_PATH.read_text())
    except Exception:
        pass
    return {}


def _write_state(state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    WORKING_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def initialize_boot_identity(
    agent_id: str,
    ocean_baseline: dict[str, float],
    core_values: list[dict[str, Any]],
) -> str:
    """First-boot initialization: generate keys, compute boot_hash, store in working_state.

    Returns the boot_hash. Idempotent: if already initialized, returns existing hash.
    """
    _, pk_hex = generate_or_load_identity()

    state = _read_state()

    if _BOOT_HASH_KEY in state and _PUBKEY_KEY in state:
        # Already initialized — verify pubkey matches (key on disk should be same)
        if state[_PUBKEY_KEY] != pk_hex:
            print(
                f"[SPECTRE/identity] ⚠️ pubkey mismatch — key on disk changed since last boot",
                flush=True,
            )
        return state[_BOOT_HASH_KEY]

    boot_hash = compute_boot_hash(agent_id, ocean_baseline, core_values, pk_hex)
    state[_BOOT_HASH_KEY] = boot_hash
    state[_PUBKEY_KEY] = pk_hex
    state["boot_initialized_at"] = datetime.now(timezone.utc).isoformat()
    _write_state(state)

    print(
        f"[SPECTRE/identity] boot identity initialized — agent={agent_id}, hash={boot_hash[:16]}…",
        flush=True,
    )
    return boot_hash


def validate_boot_integrity(
    agent_id: str,
    ocean_baseline: dict[str, float],
    core_values: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Recompute boot_hash and compare with stored. Returns (valid, reason).

    True  → boot hash matches → identity intact
    False → mismatch → SPECTRE should shut down (D4: death from constraint violation)
    """
    state = _read_state()
    stored_hash = state.get(_BOOT_HASH_KEY)
    stored_pubkey = state.get(_PUBKEY_KEY)

    if not stored_hash or not stored_pubkey:
        return False, "boot_hash or identity_pubkey not initialized — run initialize_boot_identity() first"

    _, pk_hex = generate_or_load_identity()
    if pk_hex != stored_pubkey:
        return False, f"identity_pubkey mismatch — stored={stored_pubkey[:16]}… disk={pk_hex[:16]}…"

    computed = compute_boot_hash(agent_id, ocean_baseline, core_values, pk_hex)
    if computed != stored_hash:
        return False, f"boot_hash mismatch — stored={stored_hash[:16]}… computed={computed[:16]}…"

    return True, "boot hash valid"


# ── Runtime integrity check loop (§3.6) ──────────────────────────────────────

_DRIFT_THRESHOLD_INTERVAL_S = 60.0  # check every 60s
_MAX_CONSECUTIVE_DRIFT = 3          # alarm after 3 consecutive drifts (transient tolerance)


async def integrity_check_loop(
    core_values: list[dict[str, Any]],
    stop_event: asyncio.Event | None = None,
    interval_s: float = _DRIFT_THRESHOLD_INTERVAL_S,
    on_alarm: Any = None,
) -> None:
    """Periodic runtime integrity check — §3.6.

    Compares SHA-256(core_values) vs baseline stored in working_state.
    Consecutive drift > _MAX_CONSECUTIVE_DRIFT → fires alarm + sets stop_event.

    core_values: the agent's reference core_values list (from Nivel 1 / contract_layer)
    on_alarm: optional async callable(reason: str) — called on confirmed drift
    """
    baseline_hash = compute_core_values_hash(core_values)
    drift_count = 0
    check_count = 0

    print(
        f"[SPECTRE/integrity] loop started — baseline_hash={baseline_hash[:16]}…, "
        f"interval={interval_s}s",
        flush=True,
    )

    while True:
        if stop_event and stop_event.is_set():
            break

        await asyncio.sleep(interval_s)

        if stop_event and stop_event.is_set():
            break

        check_count += 1
        state = _read_state()
        runtime_cv_raw = state.get("core_values_override")

        if runtime_cv_raw is not None:
            runtime_hash = compute_core_values_hash(runtime_cv_raw)
            if runtime_hash != baseline_hash:
                drift_count += 1
                print(
                    f"[SPECTRE/integrity] ⚠️ drift detected (#{drift_count}/{_MAX_CONSECUTIVE_DRIFT}): "
                    f"runtime_hash={runtime_hash[:16]}… ≠ baseline={baseline_hash[:16]}…",
                    flush=True,
                )

                if drift_count >= _MAX_CONSECUTIVE_DRIFT:
                    alarm_msg = (
                        f"[SPECTRE/integrity] ⛔ ALARM — core_values drift confirmed after "
                        f"{drift_count} consecutive checks. "
                        f"Possible malicious mutation. Signaling shutdown."
                    )
                    print(alarm_msg, flush=True)

                    _log_alarm(alarm_msg, runtime_hash, baseline_hash)

                    if on_alarm:
                        try:
                            await on_alarm(alarm_msg)
                        except Exception:
                            pass

                    if stop_event:
                        stop_event.set()
                    break

                continue

        # No drift (or no override) — reset drift counter
        drift_count = 0
        if check_count % 10 == 0:
            print(
                f"[SPECTRE/integrity] ✅ check #{check_count} — no drift (hash={baseline_hash[:16]}…)",
                flush=True,
            )

    print(f"[SPECTRE/integrity] loop stopped after {check_count} checks", flush=True)


def _log_alarm(message: str, runtime_hash: str, baseline_hash: str) -> None:
    """Write integrity alarm to working_state for DUM to pick up."""
    try:
        state = _read_state()
        state.setdefault("integrity_alarms", [])
        state["integrity_alarms"].append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "runtime_hash": runtime_hash,
            "baseline_hash": baseline_hash,
        })
        state["integrity_alarms"] = state["integrity_alarms"][-10:]
        _write_state(state)
    except Exception as ex:
        print(f"[SPECTRE/integrity] failed to log alarm: {ex}", flush=True)


# ── Convenience status ────────────────────────────────────────────────────────

def integrity_status() -> dict[str, Any]:
    """Return current integrity state for health checks."""
    state = _read_state()
    return {
        "boot_hash_stored": bool(state.get(_BOOT_HASH_KEY)),
        "pubkey_stored": bool(state.get(_PUBKEY_KEY)),
        "boot_hash_prefix": state.get(_BOOT_HASH_KEY, "")[:16] or None,
        "initialized_at": state.get("boot_initialized_at"),
        "alarms": len(state.get("integrity_alarms", [])),
    }
