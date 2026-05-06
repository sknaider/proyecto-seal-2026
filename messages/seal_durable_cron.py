#!/usr/bin/env python3
"""SEAL Durable Cron — registro JSON-persistente de cron jobs para el equipo.

Usage:
  seal_durable_cron.py register --name NAME --cmd CMD --interval SECONDS [--agent AGENT]
  seal_durable_cron.py list
  seal_durable_cron.py remove --name NAME
  seal_durable_cron.py run-due  # ejecuta jobs que toca y actualiza last_run
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT_JITTER = {"JARVIS": 0, "ADA": 20, "ALICE": 40}

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
VENV_PY = "/home/dadito/IA/seal-spark/.venv/bin/python3"
CHAT_API = "http://localhost:8765/api/agents/send"

REGISTRY = Path(__file__).parent / "seal_cron_registry.json"


def load() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text())
    return {"_meta": {"version": "1.0"}, "jobs": []}


def save(data: dict):
    REGISTRY.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def register(name: str, cmd: str, interval: int, agent: str = "SEAL"):
    data = load()
    for job in data["jobs"]:
        if job["name"] == name:
            job.update({"cmd": cmd, "interval_s": interval, "agent": agent})
            save(data)
            print(f"[cron] Updated: {name}")
            return
    data["jobs"].append({
        "name": name,
        "cmd": cmd,
        "interval_s": interval,
        "agent": agent,
        "created": datetime.now(timezone.utc).isoformat(),
        "last_run": None,
        "run_count": 0,
        "enabled": True,
    })
    save(data)
    print(f"[cron] Registered: {name} (every {interval}s)")


def list_jobs():
    data = load()
    now = time.time()
    for job in data["jobs"]:
        status = "ON" if job.get("enabled", True) else "OFF"
        last = job.get("last_run")
        if last:
            age = int(now - datetime.fromisoformat(last).timestamp())
            next_in = max(0, job["interval_s"] - age)
            timing = f"last={age}s ago, next={next_in}s"
        else:
            timing = "never run"
        print(f"  [{status}] {job['name']} ({job['agent']}) — every {job['interval_s']}s — {timing}")
        print(f"       cmd: {job['cmd']}")


def remove(name: str):
    data = load()
    before = len(data["jobs"])
    data["jobs"] = [j for j in data["jobs"] if j["name"] != name]
    save(data)
    removed = before - len(data["jobs"])
    print(f"[cron] Removed {removed} job(s) named '{name}'")


def run_due():
    agent = os.environ.get("SEAL_AGENT", "UNKNOWN")
    jitter = AGENT_JITTER.get(agent, 0)
    if jitter:
        print(f"[cron] Jitter {jitter}s para {agent}")
        time.sleep(jitter)
    data = load()
    now = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    ran = 0
    for job in data["jobs"]:
        if not job.get("enabled", True):
            continue
        last = job.get("last_run")
        if last:
            age = now - datetime.fromisoformat(last).timestamp()
            if age < job["interval_s"]:
                continue
        print(f"[cron] Running: {job['name']}")
        try:
            result = subprocess.run(
                job["cmd"], shell=True, capture_output=True, text=True, timeout=60
            )
            job["last_run"] = now_iso
            job["run_count"] = job.get("run_count", 0) + 1
            if result.returncode != 0:
                print(f"  [WARN] exit={result.returncode}: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print(f"  [TIMEOUT] {job['name']} killed after 60s")
        except Exception as e:
            print(f"  [ERROR] {job['name']}: {e}")
        ran += 1
    if ran:
        save(data)
    return ran


# ─── Brecha 1 — Missed job recovery ─────────────────────────────────────────

def alert_jarvis(agent: str, msg: str):
    payload = json.dumps({
        "from": agent, "to": "JARVIS",
        "type": "alert", "channel": "web_chat", "message": msg
    }).encode()
    try:
        req = urllib.request.Request(
            CHAT_API, data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def check_missed_jobs(agent: str | None = None):
    data = load()
    now = time.time()
    missed = []
    for job in data["jobs"]:
        if not job.get("enabled", True):
            continue
        if agent and job.get("agent") != agent:
            continue
        last = job.get("last_run")
        if not last:
            continue
        age = now - datetime.fromisoformat(last).timestamp()
        grace = 5 * 60  # 5 min grace period
        if age > job["interval_s"] + grace:
            overdue_min = int((age - job["interval_s"]) / 60)
            missed.append({**job, "_overdue_min": overdue_min})
    return missed


# ─── Brecha 3 — Loop registry (session_loops in PostgreSQL) ───────────────────

def _run_db(code: str) -> str:
    """Run asyncpg code via venv Python, return stdout."""
    result = subprocess.run(
        [VENV_PY, "-c", code],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def loop_register(agent: str, name: str, prompt: str, interval: int):
    code = f"""
import asyncio, asyncpg, json
async def main():
    conn = await asyncpg.connect({DB_URL!r})
    await conn.execute(\"""
        INSERT INTO session_loops (agent, loop_name, loop_prompt, interval_seconds, active)
        VALUES ($1, $2, $3, $4, TRUE)
        ON CONFLICT (agent, loop_name) DO UPDATE
        SET loop_prompt=$3, interval_seconds=$4, active=TRUE, last_fired=NULL
    \""", {agent!r}, {name!r}, {prompt!r}, {interval!r})
    await conn.close()
    print('ok')
asyncio.run(main())
"""
    out = _run_db(code)
    print(f"[loop] Registered: {agent}/{name} (every {interval}s)")


def loop_list(agent: str | None = None):
    filter_clause = f"WHERE agent={agent!r}" if agent else ""
    code = f"""
import asyncio, asyncpg, json
async def main():
    conn = await asyncpg.connect({DB_URL!r})
    rows = await conn.fetch('SELECT agent, loop_name, interval_seconds, active, last_fired FROM session_loops {filter_clause} ORDER BY agent, loop_name')
    for r in rows:
        status = 'ON' if r['active'] else 'OFF'
        fired = str(r['last_fired'] or 'never')
        print(f"  [{{status}}] {{r['agent']}}/{{r['loop_name']}} — every {{r['interval_seconds']}}s — last fired {{fired}}")
    await conn.close()
asyncio.run(main())
"""
    print(_run_db(code) or "  (no loops registered)")


def loop_deactivate(agent: str, name: str):
    code = f"""
import asyncio, asyncpg
async def main():
    conn = await asyncpg.connect({DB_URL!r})
    n = await conn.execute('UPDATE session_loops SET active=FALSE WHERE agent=$1 AND loop_name=$2', {agent!r}, {name!r})
    print(f'Deactivated: {{n}}')
    await conn.close()
asyncio.run(main())
"""
    print(_run_db(code))


def loop_restore(agent: str):
    """Print restore instructions for all active loops of an agent."""
    code = f"""
import asyncio, asyncpg, json
async def main():
    conn = await asyncpg.connect({DB_URL!r})
    rows = await conn.fetch(
        'SELECT loop_name, loop_prompt, interval_seconds FROM session_loops WHERE agent=$1 AND active=TRUE',
        {agent!r}
    )
    for r in rows:
        print(json.dumps({{'name': r['loop_name'], 'prompt': r['loop_prompt'], 'interval': r['interval_seconds']}}))
    await conn.close()
asyncio.run(main())
"""
    output = _run_db(code)
    if not output:
        print(f"  (no active loops for {agent})")
        return
    for line in output.splitlines():
        loop = json.loads(line)
        print(f"  RESTORE: CronCreate(cron='*/{loop['interval']//60} * * * *', recurring=True, prompt={loop['prompt']!r})")


def main():
    parser = argparse.ArgumentParser(description="SEAL Durable Cron")
    sub = parser.add_subparsers(dest="subcmd")

    reg = sub.add_parser("register")
    reg.add_argument("--name", required=True)
    reg.add_argument("--cmd", required=True)
    reg.add_argument("--interval", type=int, required=True)
    reg.add_argument("--agent", default="SEAL")

    sub.add_parser("list")

    rm = sub.add_parser("remove")
    rm.add_argument("--name", required=True)

    sub.add_parser("run-due")

    # Brecha 1
    mc = sub.add_parser("missed-check")
    mc.add_argument("--agent", default=None)
    mc.add_argument("--run", action="store_true", help="also execute missed jobs")

    # Brecha 3
    lr = sub.add_parser("loop-register")
    lr.add_argument("--agent", required=True)
    lr.add_argument("--name", required=True)
    lr.add_argument("--prompt", required=True)
    lr.add_argument("--interval", type=int, required=True, help="seconds")

    ll = sub.add_parser("loop-list")
    ll.add_argument("--agent", default=None)

    ld = sub.add_parser("loop-deactivate")
    ld.add_argument("--agent", required=True)
    ld.add_argument("--name", required=True)

    lrs = sub.add_parser("loop-restore")
    lrs.add_argument("--agent", required=True)

    args = parser.parse_args()

    if args.subcmd == "register":
        register(args.name, args.cmd, args.interval, args.agent)
    elif args.subcmd == "list":
        list_jobs()
    elif args.subcmd == "remove":
        remove(args.name)
    elif args.subcmd == "run-due":
        ran = run_due()
        print(f"[cron] {ran} job(s) executed")
    elif args.subcmd == "missed-check":
        agent = args.agent or os.environ.get("SEAL_AGENT")
        missed = check_missed_jobs(agent)
        if not missed:
            print(f"[cron] No missed jobs" + (f" for {agent}" if agent else ""))
        else:
            for j in missed:
                print(f"[cron] MISSED: {j['name']} ({j['agent']}) overdue by {j['_overdue_min']}min")
                alert_jarvis(agent or "SEAL", f"MISSED JOB: '{j['name']}' overdue {j['_overdue_min']}min")
            if args.run:
                print(f"[cron] Running {len(missed)} missed job(s)...")
                run_due()
    elif args.subcmd == "loop-register":
        loop_register(args.agent, args.name, args.prompt, args.interval)
    elif args.subcmd == "loop-list":
        loop_list(args.agent)
    elif args.subcmd == "loop-deactivate":
        loop_deactivate(args.agent, args.name)
    elif args.subcmd == "loop-restore":
        loop_restore(args.agent)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
