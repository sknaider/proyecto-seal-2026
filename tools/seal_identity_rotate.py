#!/usr/bin/env python3
"""Rotate Store-A identity tokens by CALENDAR, before their cap.

Wraps ``seal_identity_lifecycle`` so a timer can own the schedule.  Rotation
happens only when the live token enters its renewal window; every other run is
a no-op that exits 0, so the timer can fire often without churning creds.

Fails closed: any agent whose metadata is missing, unreadable or inconsistent
aborts that agent with a non-zero exit instead of minting over a broken state.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIFECYCLE = Path(__file__).with_name("seal_identity_lifecycle.py")
DEFAULT_DIR = Path("/run/user/1000/seal")
DEFAULT_AGENTS = ("ADA", "ALICE", "JARVIS", "NEXUS")
DEFAULT_RENEW_WITHIN_HOURS = 72


def _meta(token_dir: Path, agent: str) -> dict:
    return json.loads((token_dir / f"{agent}.token.meta.json").read_text(encoding="utf-8"))


def _needs_rotation(meta: dict, within: timedelta, now: datetime) -> bool:
    expires = datetime.fromisoformat(meta["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires - now <= within


def _lifecycle(token_dir: Path, agent: str, command: str, *extra: str) -> int:
    argv = [sys.executable, str(LIFECYCLE), "--token-dir", str(token_dir),
            "--agent", agent, command, *extra]
    return subprocess.run(argv, check=False, capture_output=True, text=True).returncode


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _retire_expired_grace(
    token_dir: Path,
    agent: str,
    now: datetime,
    *,
    dry_run: bool = False,
) -> dict | None:
    """Archive and clear an expired promoted generation, if one is present."""
    token_path = token_dir / f"{agent}.token.next"
    meta_path = token_dir / f"{agent}.token.next.meta.json"
    if not token_path.exists() and not meta_path.exists():
        return None
    if not token_path.exists() or not meta_path.exists():
        raise RuntimeError("incomplete_grace_generation")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if (
        meta.get("agent") != agent
        or meta.get("mode") != "NEXT"
        or meta.get("enforced") is not True
        or meta.get("grace_after_promotion") is not True
    ):
        # An unpromoted NEXT is a recoverable interrupted rotation.  Leave it
        # in place so stage-next remains idempotent and promote-next can finish.
        return None
    expires_at = _parse_time(meta.get("expires_at"))
    if expires_at > now:
        return {
            "agent": agent,
            "action": "grace_active",
            "expires_at": expires_at.isoformat(),
        }
    if dry_run:
        return {
            "agent": agent,
            "action": "would_retire_expired_grace",
            "generation": int(meta.get("generation", 0)),
            "expires_at": expires_at.isoformat(),
        }
    generation = int(meta.get("generation", 0))
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    archive_dir = token_dir / ".retired" / f"{agent}-g{generation}-{stamp}"
    rc = _lifecycle(
        token_dir,
        agent,
        "retire-previous",
        "--archive-dir",
        str(archive_dir),
    )
    if rc != 0:
        raise RuntimeError(f"retire_expired_grace_failed rc={rc}")
    return {
        "agent": agent,
        "action": "expired_grace_retired",
        "generation": generation,
        "archive_dir": str(archive_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--agent", action="append", dest="agents")
    parser.add_argument("--renew-within-hours", type=int, default=DEFAULT_RENEW_WITHIN_HOURS)
    parser.add_argument("--ttl-hours", type=int, default=168)
    parser.add_argument("--archive-root", type=Path,
                        default=Path("/run/user/1000/seal/retired"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    agents = tuple(a.strip().upper() for a in (args.agents or DEFAULT_AGENTS))
    within = timedelta(hours=args.renew_within_hours)
    now = datetime.now(timezone.utc)
    rows, failed = [], False

    for agent in agents:
        try:
            grace_row = _retire_expired_grace(
                args.token_dir,
                agent,
                now,
                dry_run=args.dry_run,
            )
            if grace_row is not None:
                rows.append(grace_row)
            meta = _meta(args.token_dir, agent)
        except (OSError, ValueError, RuntimeError) as exc:
            rows.append({"agent": agent, "action": "unreadable_metadata", "detail": str(exc)[:200]})
            failed = True
            continue
        if not _needs_rotation(meta, within, now):
            rows.append({"agent": agent, "action": "not_due", "expires_at": meta.get("expires_at")})
            continue
        if args.dry_run:
            rows.append({"agent": agent, "action": "would_rotate", "expires_at": meta.get("expires_at")})
            continue
        rc = _lifecycle(args.token_dir, agent, "stage-next", "--ttl-hours", str(args.ttl_hours))
        if rc != 0:
            rows.append({"agent": agent, "action": "stage_next_failed", "rc": rc})
            failed = True
            continue
        rc = _lifecycle(args.token_dir, agent, "promote-next")
        if rc != 0:
            rows.append({"agent": agent, "action": "promote_failed", "rc": rc})
            failed = True
            continue
        # `promote-next` conserva deliberadamente la generacion anterior en
        # `.next` como gracia: en cada frontera debe quedar al menos un slot
        # valido. La siguiente corrida retira esa gracia SOLO cuando expire,
        # antes de preparar la siguiente generacion. Retirarla aqui haria la
        # rotacion recurrente, pero rompería la continuidad que promete el
        # lifecycle precisamente durante el cambio de credencial.
        rows.append({"agent": agent, "action": "rotated", "grace_retained": True})

    print(json.dumps({"observed_at": now.isoformat(), "token_dir": str(args.token_dir),
                      "renew_within_hours": args.renew_within_hours, "rows": rows}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
