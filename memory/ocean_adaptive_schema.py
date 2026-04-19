"""
ocean_adaptive_schema.py — OCEAN adaptativo SEAL
Spec: ALICE (2026-04-17) | Impl: ADA (2026-04-18)

Crea schema ocean_test en seal_memory + popula con valores actuales.
Para producción: mover objetos a schema public con JARVIS sign-off.
"""

import asyncio
import asyncpg
import sys

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

# OCEAN actual de los agentes (leído de identity.ocean_scores)
OCEAN_CURRENT = {
    "ADA":        {"openness": 0.791, "conscientiousness": 1.0,   "extraversion": 1.0,   "agreeableness": 0.476, "neuroticism": 0.206},
    "ALICE":      {"openness": 0.815, "conscientiousness": 0.727, "extraversion": 0.8,   "agreeableness": 0.300, "neuroticism": 0.200},
    "JARVIS":     {"openness": 0.820, "conscientiousness": 1.0,   "extraversion": 0.398, "agreeableness": 0.661, "neuroticism": 0.115},
    "DUM":        {"openness": 0.300, "conscientiousness": 0.950, "extraversion": 0.2,   "agreeableness": 0.750, "neuroticism": 0.400},
}

# Guardrails de ALICE spec
OCEAN_LIMITS = {
    "max_drift_per_dimension": 0.15,
    "max_drift_per_day": 0.003,
    "review_threshold": 0.05,
}

OCEAN_BOUNDS = {
    "openness":          (0.20, 0.95),
    "conscientiousness": (0.40, 1.00),
    "extraversion":      (0.10, 1.00),
    "agreeableness":     (0.15, 0.90),
    "neuroticism":       (0.05, 0.60),
}

DDL = """
CREATE SCHEMA IF NOT EXISTS ocean_test;

CREATE TABLE IF NOT EXISTS ocean_test.ocean_base_values (
    id          SERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    dimension   TEXT NOT NULL,
    base_value  REAL NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent, dimension)
);

CREATE TABLE IF NOT EXISTS ocean_test.ocean_drift_log (
    id          SERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    dimension   TEXT NOT NULL,
    delta       REAL NOT NULL,
    event_type  TEXT NOT NULL,   -- e.g. 'task_completed', 'rule_violation'
    event_desc  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    applied     BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_ocean_drift_agent
    ON ocean_test.ocean_drift_log (agent, dimension)
    WHERE applied = TRUE;

DROP VIEW IF EXISTS ocean_test.ocean_current;
CREATE VIEW ocean_test.ocean_current AS
SELECT
    b.agent,
    b.dimension,
    b.base_value,
    COALESCE(SUM(d.delta), 0.0)              AS total_drift,
    b.base_value + COALESCE(SUM(d.delta), 0.0) AS current_value
FROM ocean_test.ocean_base_values b
LEFT JOIN ocean_test.ocean_drift_log d
       ON b.agent = d.agent
      AND b.dimension = d.dimension
      AND d.applied = TRUE
GROUP BY b.agent, b.dimension, b.base_value;
"""


async def setup_schema(conn: asyncpg.Connection) -> None:
    """Creates schema, tables, view in one transaction."""
    await conn.execute(DDL)


async def populate_base_values(conn: asyncpg.Connection) -> None:
    """Inserts current OCEAN scores as base_values (idempotent — ON CONFLICT DO NOTHING)."""
    rows = []
    for agent, dims in OCEAN_CURRENT.items():
        for dim, val in dims.items():
            rows.append((agent, dim, val))

    await conn.executemany(
        """
        INSERT INTO ocean_test.ocean_base_values (agent, dimension, base_value)
        VALUES ($1, $2, $3)
        ON CONFLICT (agent, dimension) DO NOTHING
        """,
        rows,
    )


async def verify_guardrails(conn: asyncpg.Connection, agent: str, dimension: str, delta: float) -> tuple[bool, str]:
    """Validate a proposed drift delta against all guardrails."""
    # 1. Max per-day drift
    daily_sum = await conn.fetchval(
        """
        SELECT COALESCE(ABS(SUM(delta)), 0.0)
        FROM ocean_test.ocean_drift_log
        WHERE agent = $1
          AND dimension = $2
          AND applied = TRUE
          AND created_at >= NOW() - INTERVAL '24 hours'
        """,
        agent, dimension
    )
    if daily_sum + abs(delta) > OCEAN_LIMITS["max_drift_per_day"]:
        return False, f"daily cap reached ({daily_sum:.4f} + {abs(delta):.4f} > {OCEAN_LIMITS['max_drift_per_day']})"

    # 2. Max total drift from base
    base = await conn.fetchval(
        "SELECT base_value FROM ocean_test.ocean_base_values WHERE agent=$1 AND dimension=$2",
        agent, dimension
    )
    total = await conn.fetchval(
        "SELECT COALESCE(SUM(delta),0.0) FROM ocean_test.ocean_drift_log WHERE agent=$1 AND dimension=$2 AND applied=TRUE",
        agent, dimension
    )
    projected = (total or 0) + delta
    if abs(projected) > OCEAN_LIMITS["max_drift_per_dimension"]:
        return False, f"total drift cap ({abs(projected):.4f} > {OCEAN_LIMITS['max_drift_per_dimension']})"

    # 3. Absolute bounds check
    lo, hi = OCEAN_BOUNDS[dimension]
    projected_val = (base or 0) + projected
    if not (lo <= projected_val <= hi):
        return False, f"bounds violation ({projected_val:.4f} not in [{lo},{hi}])"

    return True, "ok"


async def apply_drift(conn: asyncpg.Connection, agent: str, dimension: str,
                      delta: float, event_type: str, event_desc: str = "") -> dict:
    """Apply a verified drift event. Returns result dict."""
    ok, reason = await verify_guardrails(conn, agent, dimension, delta)
    if not ok:
        return {"applied": False, "reason": reason}

    await conn.execute(
        """
        INSERT INTO ocean_test.ocean_drift_log (agent, dimension, delta, event_type, event_desc)
        VALUES ($1, $2, $3, $4, $5)
        """,
        agent, dimension, delta, event_type, event_desc
    )

    new_val = await conn.fetchval(
        "SELECT current_value FROM ocean_test.ocean_current WHERE agent=$1 AND dimension=$2",
        agent, dimension
    )
    return {"applied": True, "new_value": new_val, "delta": delta}


async def run_tests(conn: asyncpg.Connection) -> list[dict]:
    results = []

    def check(name, cond, detail=""):
        results.append({"test": name, "pass": cond, "detail": detail})
        status = "✅" if cond else "❌"
        print(f"  {status} {name}" + (f" — {detail}" if detail else ""))

    print("\n=== OCEAN Adaptive Schema Tests ===\n")

    # T1: base_values populated
    count = await conn.fetchval("SELECT COUNT(*) FROM ocean_test.ocean_base_values")
    check("T1 base_values populated", count >= 20, f"{count} rows")

    # T2: ocean_current returns same as base when no drift
    ada_open = await conn.fetchrow(
        "SELECT base_value, current_value, total_drift FROM ocean_test.ocean_current WHERE agent='ADA' AND dimension='openness'"
    )
    check("T2 current=base with no drift",
          ada_open and abs(ada_open["current_value"] - ada_open["base_value"]) < 1e-6,
          f"base={ada_open['base_value']}, current={ada_open['current_value']}" if ada_open else "no row")

    # T3: apply valid drift — use agreeableness (0.476, has room to drift)
    r = await apply_drift(conn, "ADA", "agreeableness", +0.001, "task_completed",
                          "5 consecutive tasks without error — test")
    check("T3 valid drift applied", r["applied"], f"new_val={r.get('new_value')}")

    # T4: drift reflected in view
    ada_a = await conn.fetchrow(
        "SELECT current_value FROM ocean_test.ocean_current WHERE agent='ADA' AND dimension='agreeableness'"
    )
    expected = OCEAN_CURRENT["ADA"]["agreeableness"] + 0.001
    check("T4 drift reflected in view",
          ada_a and abs(ada_a["current_value"] - expected) < 1e-5,
          f"expected={expected:.4f}, got={ada_a['current_value'] if ada_a else 'None'}")

    # T5: daily cap — try to apply huge delta
    r_cap = await apply_drift(conn, "ALICE", "conscientiousness", +0.999, "test_cap",
                              "should be rejected by daily cap")
    check("T5 daily cap guardrail", not r_cap["applied"], r_cap.get("reason"))

    # T6: bounds guardrail — ADA conscientiousness already at 1.0+0.001, try to push past hi
    r_bounds = await apply_drift(conn, "ADA", "conscientiousness", +0.10, "test_bounds",
                                 "should be rejected by bounds")
    check("T6 bounds guardrail", not r_bounds["applied"], r_bounds.get("reason"))

    # T7: total drift cap — force accumulated drift > 0.15
    # Apply repeated small valid deltas to ALICE neuroticism (many days simulated by inserting directly)
    await conn.execute(
        """
        INSERT INTO ocean_test.ocean_drift_log (agent, dimension, delta, event_type, created_at, applied)
        VALUES ('ALICE', 'neuroticism', -0.14, 'test_total_cap',
                NOW() - INTERVAL '48 hours', TRUE)
        """
    )
    r_total = await apply_drift(conn, "ALICE", "neuroticism", -0.02, "test_total_cap_trigger",
                                "should be rejected by total drift cap")
    check("T7 total drift cap guardrail", not r_total["applied"], r_total.get("reason"))

    # T8: rollback — set applied=FALSE and check view reverts
    await conn.execute(
        "UPDATE ocean_test.ocean_drift_log SET applied=FALSE WHERE agent='ADA' AND event_type='task_completed'"
    )
    ada_c_after = await conn.fetchrow(
        "SELECT current_value FROM ocean_test.ocean_current WHERE agent='ADA' AND dimension='conscientiousness'"
    )
    check("T8 rollback via applied=FALSE",
          ada_c_after and abs(ada_c_after["current_value"] - OCEAN_CURRENT["ADA"]["conscientiousness"]) < 1e-5,
          f"expected={OCEAN_CURRENT['ADA']['conscientiousness']}, got={ada_c_after['current_value'] if ada_c_after else 'None'}")

    # T9: all 4 agents have all 5 dimensions
    coverage = await conn.fetchval(
        "SELECT COUNT(DISTINCT agent||'.'||dimension) FROM ocean_test.ocean_base_values WHERE agent IN ('ADA','ALICE','JARVIS','DUM')"
    )
    check("T9 all agents×dimensions populated", coverage == 20, f"{coverage}/20")

    # T10: JARVIS values match source
    j_open = await conn.fetchval(
        "SELECT base_value FROM ocean_test.ocean_base_values WHERE agent='JARVIS' AND dimension='openness'"
    )
    check("T10 JARVIS openness matches source",
          abs(j_open - 0.820) < 1e-5, f"{j_open}")

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    print(f"\nResult: {passed}/{total} passed")
    return results


async def main(mode: str = "setup"):
    conn = await asyncpg.connect(DB_URL)
    try:
        if mode in ("setup", "all"):
            print("Creating ocean_test schema...")
            await setup_schema(conn)
            print("Populating base values...")
            await populate_base_values(conn)
            print("Schema ready.")

        if mode in ("test", "all"):
            results = await run_tests(conn)
            failed = [r for r in results if not r["pass"]]
            if failed:
                print(f"\n❌ {len(failed)} tests FAILED")
                sys.exit(1)
            else:
                print("\n✅ All tests passed — ready for JARVIS review")

        if mode == "status":
            rows = await conn.fetch(
                "SELECT agent, dimension, base_value, total_drift, current_value FROM ocean_test.ocean_current ORDER BY agent, dimension"
            )
            print(f"\n{'Agent':<10} {'Dimension':<20} {'Base':>6} {'Drift':>7} {'Current':>7}")
            print("-" * 55)
            for r in rows:
                print(f"{r['agent']:<10} {r['dimension']:<20} {r['base_value']:>6.3f} {r['total_drift']:>+7.4f} {r['current_value']:>7.4f}")

    finally:
        await conn.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    asyncio.run(main(mode))
