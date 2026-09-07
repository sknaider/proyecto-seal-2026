"""
SEAL Nerves Daemon — periodic tick for motivation engine
Designed to be called by seal_loops.py / CronCreate every 5 minutes.
Agent is read from SEAL_AGENT env var or sys.argv[1].
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from seal_nerves import run_tick

if __name__ == "__main__":
    # argv takes priority over env var (env var is for systemd services)
    agent = (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get("SEAL_AGENT") or "JARVIS"
    states, fired = asyncio.run(run_tick(agent))
    # Brief summary to stdout for loop logging
    active = {k: f"{v['value']:.0f}/{v['threshold']:.0f}" for k, v in states.items() if v["value"] > 1}
    print(f"[nerves] {agent} active tanks: {active} | fired: {[f['tank'] for f in fired]}")
