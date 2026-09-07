#!/usr/bin/env python3
"""Guarantee that a Store-A identity token always carries valid lifecycle metadata.

Why this exists
---------------
``seal_identity_env.sh`` seeds ``<AGENT>.token`` but knew nothing about the
``<AGENT>.token.meta.json`` that ``seal_identity_tokens._metadata_allows``
requires under ``SEAL_TOKEN_LIFECYCLE_MODE=ENFORCE``.  A token without that
sidecar is invalid, so every MCP session fell back to ``external`` and every
private tool was denied — the whole team booted mute (measured 2026-08-27:
``valid_tokens()`` returned 0 for ADA, ALICE, JARVIS and NEXUS at once).

The writer of a secret must also write the contract the validator reads.  This
script closes that gap in the same act that creates the token.

Properties
----------
* **Idempotent** — a coherent, unexpired meta is left untouched.
* **Non-destructive** — a stale meta is *overwritten atomically*, never removed.
  There is no unlink and no path built from an unvalidated variable.
* **Fail-loud tool, fail-soft launcher** — this command exits non-zero when it
  cannot repair; the launcher deliberately catches that result so boot is not
  bricked while operators still receive a truthful standalone exit status.
* **Silent about secrets** — never prints token bytes; only a short digest
  prefix that cannot reconstruct the token.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_identity_lifecycle import (  # noqa: E402
    SCHEMA,
    DEFAULT_TTL_HOURS,
    _atomic_private_json,
    _digest,
    _lifecycle_lock,
    _meta_path,
    _read_private_regular,
)


def ensure(token_dir: Path, agent: str, *, ttl_hours: int = DEFAULT_TTL_HOURS) -> dict:
    """Return a row describing the meta state for ``agent`` after reconciliation."""
    now = datetime.now(timezone.utc)
    token_path = token_dir / f"{agent}.token"
    meta_path = _meta_path(token_path)
    token = _read_private_regular(token_path)
    digest = _digest(token)

    previous: dict | None = None
    try:
        previous = json.loads(_read_private_regular(meta_path))
    except Exception:
        previous = None

    if (
        isinstance(previous, dict)
        and previous.get("schema") == SCHEMA
        and previous.get("agent") == agent
        and previous.get("mode") == "CURRENT"
        and previous.get("enforced") is True
        and previous.get("token_sha256") == digest
        and not previous.get("revoked_at")
        and datetime.fromisoformat(
            str(previous.get("expires_at", "")).replace("Z", "+00:00")
        )
        > now
    ):
        return {
            "agent": agent,
            "action": "kept",
            "generation": previous.get("generation"),
            "expires_at": previous.get("expires_at"),
            "token_fingerprint": digest[:12],
        }

    # The token was reseeded (or the sidecar is stale/absent): rewrite the meta
    # in place so it describes the token that actually exists on disk.
    generation = 1
    if isinstance(previous, dict):
        try:
            generation = int(previous.get("generation", 0)) + 1
        except (TypeError, ValueError):
            generation = 1

    meta = {
        "schema": SCHEMA,
        "agent": agent,
        "mode": "CURRENT",
        "generation": generation,
        "token_sha256": digest,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=ttl_hours)).isoformat(),
        "issued_at_basis": "launcher_seeded_token",
        "enforced": True,
        "promoted_at": now.isoformat(),
    }
    _atomic_private_json(meta_path, meta)
    return {
        "agent": agent,
        "action": "rewritten" if previous else "created",
        "generation": generation,
        "expires_at": meta["expires_at"],
        "token_fingerprint": digest[:12],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--token-dir", type=Path, required=True)
    parser.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    agent = args.agent.strip().upper()
    try:
        with _lifecycle_lock(args.token_dir):
            row = ensure(args.token_dir, agent, ttl_hours=args.ttl_hours)
    except Exception as exc:
        # Exit NON-ZERO when nothing was repaired.  This runs as a rescue during an
        # outage — agents mute, someone in a hurry — and that is the worst possible
        # moment for a failed repair to report success.  Caught by JARVIS: the
        # original `return 0` said "done" whether or not a meta was written.
        #
        # The launcher's boot is still never blocked: it calls this with `|| true`,
        # so the fail-soft lives in the CALLER that needs it instead of being baked
        # into a tool whose other caller is a human fixing a broken system.
        print(f"[seal_identity_ensure_meta] {agent}: NOT REPAIRED — "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not args.quiet:
        print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
