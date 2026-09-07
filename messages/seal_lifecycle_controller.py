#!/usr/bin/env python3
"""
SEAL Lifecycle Controller — Desired State reconciler for Team SEAL agents.

Replaces seal_agent_resurrect.sh + dum_watchdog auto-restart with a single
declarative loop. Source of truth: agent_lifecycle table in seal_memory DB.

Usage:
  seal_lifecycle_controller.py --once            # single reconcile pass
  seal_lifecycle_controller.py --daemon          # loop forever (30s interval)
  seal_lifecycle_controller.py --once --dry-run  # show what would happen

"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

# DSN por seal_secrets, fail-closed (ADA 3-sep-2026). POR QUE: el literal que
# estaba aca tenia la contrasena vieja del rol seal; la unidad fallaba con
# InvalidPasswordError desde 13:27 y nadie resucitaba daemons caidos. La
# correccion vivia sin commitear y un reset --hard la piso. Mismo patron que
# continuity_snapshot.py (1581ebd19). Sin credencial, no arranca y lo dice.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "memory"))
try:
    from seal_secrets import pg_dsn as _seal_pg_dsn
    DB_DSN = _seal_pg_dsn()
except Exception as _exc:  # pragma: no cover - fail-closed
    raise SystemExit(f"seal_lifecycle: sin DSN en seal_secrets/SEAL_PG_DSN ({_exc}); no arranco")
RECONCILE_INTERVAL_SEC = int(os.environ.get("LIFECYCLE_INTERVAL_SEC", "30"))
COOLDOWN_MIN = int(os.environ.get("LIFECYCLE_COOLDOWN_MIN", "5"))
SEAL_RESTART = Path("/home/dadito/IA/proyecto-seal/seal_restart.sh")
LOG_FILE = Path("/home/dadito/IA/proyecto-seal/messages/seal_lifecycle.log")

VALID_AGENTS = {"ADA", "JARVIS", "ALICE", "NEXUS"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("lifecycle")


def is_process_alive(agent_name: str) -> int | None:
    """Return PID of a live claude process with SEAL_AGENT=<agent_name>, or None."""
    try:
        out = subprocess.run(
            ["pgrep", "-x", "claude"], capture_output=True, text=True, timeout=5
        )
    except Exception as e:
        log.warning("pgrep failed: %s", e)
        return None
    for pid in out.stdout.split():
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read().decode("utf-8", errors="ignore")
        except (FileNotFoundError, PermissionError):
            continue
        for pair in env.split("\x00"):
            if pair == f"SEAL_AGENT={agent_name}":
                return int(pid)
    return None


def launch_agent(agent_name: str, dry_run: bool = False) -> tuple[bool, str]:
    """Call seal_restart.sh <agent> to launch/resume the agent."""
    if not SEAL_RESTART.exists():
        return False, f"{SEAL_RESTART} not found"
    cmd = ["bash", str(SEAL_RESTART), agent_name.lower()]
    if dry_run:
        return True, f"DRY-RUN would exec: {' '.join(cmd)}"
    try:
        env = os.environ.copy()
        # Always inject display env so kitty window is visible (fix headless bug — ADA fix was a no-op)
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        return r.returncode == 0, f"exit={r.returncode} stdout={r.stdout[-200:]} stderr={r.stderr[-200:]}"
    except Exception as e:
        return False, f"launch exception: {e}"


def kill_agent(agent_name: str, pid: int, dry_run: bool = False) -> tuple[bool, str]:
    """Kill the agent process owned by <agent_name> with the given PID."""
    if dry_run:
        return True, f"DRY-RUN would kill PID {pid} (SEAL_AGENT={agent_name})"
    try:
        os.kill(pid, signal.SIGKILL)
        return True, f"killed PID {pid} via SIGKILL"
    except ProcessLookupError:
        return True, f"PID {pid} already gone"
    except PermissionError as e:
        return False, f"kill permission denied: {e}"
    except Exception as e:
        return False, f"kill exception: {e}"


async def get_last_action_age(pool: asyncpg.Pool, agent_name: str, event_types: list[str]) -> float | None:
    """Return minutes since last event of given type for agent, or None if no event."""
    row = await pool.fetchrow(
        """
        SELECT created_at FROM lifecycle_events
        WHERE agent_name = $1 AND event_type = ANY($2::varchar[])
        ORDER BY created_at DESC LIMIT 1
        """,
        agent_name, event_types,
    )
    if row is None:
        return None
    delta = datetime.now(timezone.utc) - row["created_at"]
    return delta.total_seconds() / 60.0


async def log_event(pool: asyncpg.Pool, agent_name: str, event_type: str,
                    desired: str, actual: str, detail: str, dry_run: bool = False):
    prefix = "[DRY-RUN] " if dry_run else ""
    log.info("%sevent agent=%s type=%s desired=%s actual=%s detail=%s",
             prefix, agent_name, event_type, desired, actual, detail)
    if dry_run:
        return
    await pool.execute(
        """
        INSERT INTO lifecycle_events (agent_name, event_type, desired_state, actual_state, detail)
        VALUES ($1, $2, $3, $4, $5)
        """,
        agent_name, event_type, desired, actual, detail,
    )


async def reconcile_agent(pool: asyncpg.Pool, row: asyncpg.Record, dry_run: bool = False):
    agent = row["agent_name"]
    desired = row["desired_state"]

    if agent not in VALID_AGENTS:
        log.debug("skip agent %s (not in VALID_AGENTS)", agent)
        return

    pid = is_process_alive(agent)
    alive = pid is not None
    actual = "alive" if alive else "dead"

    # Equilibrium cases → no-op
    # "manual": William gestiona ese cuerpo a mano (ADA Claude, 3-sep-2026:
    # "solo te usare de momentos y no quiero 2 adas"). Ni matar ni relanzar.
    if desired == "manual":
        return
    if desired == "running" and alive:
        return
    if desired in ("paused", "stopped") and not alive:
        return

    # Respect manual pause flag (same as seal_agent_resurrect.sh)
    # touch /tmp/seal_pause_resurrect_<agent_lower> to prevent auto-relaunch
    pause_flag = Path(f"/tmp/seal_pause_resurrect_{agent.lower()}")
    if pause_flag.exists():
        log.info("skip agent=%s — pause_resurrect flag active (%s)", agent, pause_flag)
        return

    # Need action — check cooldown first
    if desired == "running" and not alive:
        age_min = await get_last_action_age(pool, agent, ["auto_launched", "launch_failed"])
        if age_min is not None and age_min < COOLDOWN_MIN:
            log.info("cooldown agent=%s age=%.1fmin < %dmin — skip launch", agent, age_min, COOLDOWN_MIN)
            return
        ok, detail = launch_agent(agent, dry_run=dry_run)
        event = "auto_launched" if ok else "launch_failed"
        await log_event(pool, agent, event, desired, actual, detail, dry_run=dry_run)
        return

    if desired in ("paused", "stopped") and alive:
        age_min = await get_last_action_age(pool, agent, ["auto_killed", "kill_failed"])
        if age_min is not None and age_min < COOLDOWN_MIN:
            log.info("cooldown agent=%s age=%.1fmin < %dmin — skip kill", agent, age_min, COOLDOWN_MIN)
            return
        ok, detail = kill_agent(agent, pid, dry_run=dry_run)
        event = "auto_killed" if ok else "kill_failed"
        await log_event(pool, agent, event, desired, actual, f"pid={pid} {detail}", dry_run=dry_run)
        return


async def reconcile_once(pool: asyncpg.Pool, dry_run: bool = False):
    rows = await pool.fetch(
        "SELECT agent_name, desired_state, changed_by, changed_at, reason FROM agent_lifecycle"
    )
    log.info("tick — %d agents (dry_run=%s)", len(rows), dry_run)
    for row in rows:
        try:
            await reconcile_agent(pool, row, dry_run=dry_run)
        except Exception:
            log.exception("reconcile_agent failed for %s", row["agent_name"])


async def main_async(args):
    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=2)
    try:
        if args.once:
            await reconcile_once(pool, dry_run=args.dry_run)
            return
        log.info("daemon start interval=%ds dry_run=%s", RECONCILE_INTERVAL_SEC, args.dry_run)
        while True:
            try:
                await reconcile_once(pool, dry_run=args.dry_run)
            except Exception:
                log.exception("reconcile_once crashed")
            await asyncio.sleep(RECONCILE_INTERVAL_SEC)
    finally:
        await pool.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single reconcile pass then exit")
    ap.add_argument("--daemon", action="store_true", help="loop forever (default)")
    ap.add_argument("--dry-run", action="store_true", help="log actions but don't execute")
    args = ap.parse_args()
    if not args.once and not args.daemon:
        args.daemon = True
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
