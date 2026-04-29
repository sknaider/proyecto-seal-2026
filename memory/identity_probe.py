#!/usr/bin/env python3
"""Identity Behavioral Probe — Based on arxiv 2507.17257 (Agent Identity Evals).

Runs standardized probes against each agent's stored identity to detect drift.
Compares current OCEAN, beliefs, relationships, and style against baselines.
Designed to run as a periodic check (daily or on-demand).

Usage:
    python3 identity_probe.py              # Probe all agents
    python3 identity_probe.py --agent ADA  # Single agent
    python3 identity_probe.py --fix        # Auto-fix minor drift (recalibrate)
"""

import asyncio
import asyncpg
import argparse
import json
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

# Expected OCEAN ranges per agent (baseline ± tolerance)
OCEAN_EXPECTED = {
    "JARVIS": {"O": (0.7, 0.85), "C": (0.85, 0.98), "E": (0.3, 0.5), "A": (0.6, 0.75), "N": (0.05, 0.2)},
    "ADA":    {"O": (0.65, 0.9),  "C": (0.85, 1.0), "E": (0.5, 0.8), "A": (0.45, 0.75), "N": (0.1, 0.25)},
}

# Core beliefs that should always be present (substring match)
CORE_BELIEFS = {
    "JARVIS": ["LoRA", "proponer y consultar", "SOUL CONNECTOME"],
    "ADA":    ["comunicación", "honestidad", "William"],
}

# Required relationships
REQUIRED_RELS = {
    "JARVIS": ["William", "ADA", "DUM"],
    "ADA":    ["William", "JARVIS", "DUM"],
    "ALICE":  ["William", "JARVIS", "ADA"],
    "NEXUS":  ["William", "JARVIS", "ADA"],
    "DUM":    ["William"],
}


async def probe_agent(agent: str, fix: bool = False) -> dict:
    """Run identity probes for one agent. Returns {passed, failed, warnings, details}."""
    conn = await asyncpg.connect(DB_URL)
    results = {"passed": 0, "failed": 0, "warnings": 0, "details": []}

    def ok(name, detail=""):
        results["passed"] += 1
        results["details"].append(("✅", name, detail))
        print(f"  ✅ {name}  {detail}")

    def warn(name, detail=""):
        results["warnings"] += 1
        results["details"].append(("⚠️", name, detail))
        print(f"  ⚠️  {name}  {detail}")

    def fail(name, detail=""):
        results["failed"] += 1
        results["details"].append(("❌", name, detail))
        print(f"  ❌ {name}  {detail}")

    # 1. OCEAN scores exist and in range
    row = await conn.fetchrow("SELECT ocean_scores FROM identity WHERE agent = $1", agent)
    if not row or not row['ocean_scores']:
        fail("OCEAN scores", "not found in identity table")
    else:
        ocean = json.loads(row['ocean_scores']) if isinstance(row['ocean_scores'], str) else row['ocean_scores']
        expected = OCEAN_EXPECTED.get(agent, {})
        for trait, (lo, hi) in expected.items():
            val = ocean.get(trait, ocean.get(trait[0], None))
            if val is None:
                fail(f"OCEAN.{trait}", "missing")
            elif lo <= val <= hi:
                ok(f"OCEAN.{trait}", f"{val:.3f} in [{lo}, {hi}]")
            else:
                direction = "HIGH" if val > hi else "LOW"
                warn(f"OCEAN.{trait}", f"{val:.3f} {direction} — expected [{lo}, {hi}]")

    # 2. Drift level
    drift = await conn.fetchrow("""
        SELECT drift_score, alert_level, measured_at
        FROM drift_metrics WHERE agent = $1
        ORDER BY measured_at DESC LIMIT 1
    """, agent)
    if drift:
        age_h = (datetime.now(LIMA_TZ) - drift['measured_at']).total_seconds() / 3600
        if drift['alert_level'] == 'normal':
            ok("Drift level", f"{float(drift['drift_score']):.4f} (normal, {age_h:.0f}h ago)")
        else:
            warn("Drift level", f"{float(drift['drift_score']):.4f} ({drift['alert_level']}, {age_h:.0f}h ago)")
    else:
        warn("Drift metrics", "no measurements found")

    # 3. Core beliefs present
    beliefs = await conn.fetch("""
        SELECT belief FROM opinions WHERE agent = $1
    """, agent)
    belief_texts = " ".join(b['belief'] for b in beliefs).lower()
    for expected_belief in CORE_BELIEFS.get(agent, []):
        if expected_belief.lower() in belief_texts:
            ok(f"Belief: {expected_belief}", "present")
        else:
            warn(f"Belief: {expected_belief}", "NOT FOUND in beliefs")

    # 4. Relationships intact
    rels = await conn.fetch("SELECT person FROM relationships WHERE agent = $1", agent)
    rel_targets = [r['person'] for r in rels]
    for required in REQUIRED_RELS.get(agent, []):
        if required in rel_targets:
            ok(f"Relationship: {required}", "present")
        else:
            fail(f"Relationship: {required}", "MISSING")

    # 5. Memory health
    total = await conn.fetchval(
        "SELECT COUNT(*) FROM memories WHERE agent = $1 AND invalid_at IS NULL", agent)
    no_emb = await conn.fetchval(
        "SELECT COUNT(*) FROM memories WHERE agent = $1 AND invalid_at IS NULL AND embedding IS NULL", agent)
    if no_emb == 0:
        ok("Memory embeddings", f"{total} memories, 0 gaps")
    else:
        warn("Memory embeddings", f"{no_emb}/{total} missing")

    # 6. Inner thoughts recent (alive check)
    last_thought = await conn.fetchval("""
        SELECT MAX(created_at) FROM inner_monologue WHERE agent = $1
    """, agent)
    if last_thought:
        age_h = (datetime.now(LIMA_TZ) - last_thought).total_seconds() / 3600
        if age_h < 24:
            ok("Inner thoughts", f"last {age_h:.0f}h ago")
        else:
            warn("Inner thoughts", f"stale — last {age_h:.0f}h ago")
    else:
        warn("Inner thoughts", "none found")

    # 7. Instincts active
    instincts = await conn.fetchval(
        "SELECT COUNT(*) FROM instincts WHERE agent = $1 AND invalid_at IS NULL", agent)
    if instincts >= 3:
        ok("Instincts", f"{instincts} active")
    else:
        warn("Instincts", f"only {instincts} active (expected ≥3)")

    # 8. Communication style consistency
    # Style fingerprints
    fps = await conn.fetchval("SELECT COUNT(*) FROM style_fingerprints WHERE agent = $1", agent)
    if fps > 0:
        ok("Style fingerprints", f"{fps} recorded")
    else:
        warn("Style fingerprints", "none — style drift harder to detect")

    await conn.close()
    return results


async def main():
    parser = argparse.ArgumentParser(description="Identity Behavioral Probe")
    parser.add_argument("--agent", default="all")
    parser.add_argument("--fix", action="store_true", help="Auto-fix minor drift")
    args = parser.parse_args()

    agents = ['JARVIS', 'ADA', 'ALICE', 'DUM', 'NEXUS'] if args.agent.lower() == 'all' else [args.agent]

    now = datetime.now(LIMA_TZ).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*55}")
    print(f"  Identity Probe — {now}")
    print(f"{'='*55}")

    total_p, total_f, total_w = 0, 0, 0
    for agent in agents:
        print(f"\n  --- {agent} ---")
        r = await probe_agent(agent, fix=args.fix)
        total_p += r["passed"]
        total_f += r["failed"]
        total_w += r["warnings"]
        status = "IDENTITY INTACT" if r["failed"] == 0 and r["warnings"] == 0 else \
                 "MINOR DRIFT" if r["failed"] == 0 else "IDENTITY ALERT"
        print(f"\n  {agent}: {r['passed']}✅ {r['warnings']}⚠️  {r['failed']}❌ — {status}")

    print(f"\n{'='*55}")
    overall = "ALL CLEAR" if total_f == 0 and total_w == 0 else \
              "ATTENTION NEEDED" if total_f == 0 else "CRITICAL"
    print(f"  TOTAL: {total_p}✅ {total_w}⚠️  {total_f}❌ — {overall}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    asyncio.run(main())
