#!/usr/bin/env python3
"""
distill_metrics_report.py — H2.7 session_distill coverage metrics
Owner: ADA (Team SEAL) | Spec: ALICE metrics_session_distill_coverage_v1.md
Schema: real tables (distilled_exchanges, memories, sessions)
"""
import os
import sys
import json
from datetime import datetime

try:
    import pg8000.native as pg
except ImportError:
    try:
        import psycopg2 as psycopg2_mod
        USE_PSYCOPG2 = True
    except ImportError:
        print("ERROR: necesita pg8000 o psycopg2. pip install pg8000")
        sys.exit(1)
    USE_PSYCOPG2 = True
else:
    USE_PSYCOPG2 = False

DB_HOST = os.getenv("SEAL_DB_HOST", "localhost")
DB_PORT = int(os.getenv("SEAL_DB_PORT", "5433"))
DB_USER = os.getenv("SEAL_DB_USER", "seal")
DB_PASS = os.getenv("SEAL_DB_PASS", "seal_memory_2026")
DB_NAME = os.getenv("SEAL_DB_NAME", "seal_memory")

AGENT_FILTER = sys.argv[1] if len(sys.argv) > 1 else None

SQL_M1 = """
SELECT
  s.id AS session_id,
  s.agent,
  s.started_at,
  s.ended_at,
  COUNT(de.id) AS destiladas,
  COUNT(DISTINCT m.id) FILTER (
    WHERE m.importance >= 6
    AND m.created_at BETWEEN s.started_at AND COALESCE(s.ended_at, NOW())
  ) AS candidatas,
  ROUND(
    COUNT(de.id)::numeric /
    NULLIF(COUNT(DISTINCT m.id) FILTER (
      WHERE m.importance >= 6
      AND m.created_at BETWEEN s.started_at AND COALESCE(s.ended_at, NOW())
    ), 0), 3
  ) AS distill_coverage
FROM sessions s
LEFT JOIN distilled_exchanges de ON de.session_id = s.id
LEFT JOIN memories m ON m.agent = s.agent
{where}
GROUP BY s.id, s.agent, s.started_at, s.ended_at
ORDER BY s.started_at DESC
LIMIT 20;
"""

SQL_M2 = """
SELECT
  s.agent,
  ROUND(
    PERCENTILE_CONT(0.5) WITHIN GROUP (
      ORDER BY EXTRACT(EPOCH FROM (s.started_at - m_last.last_mem_time))/60
    )::numeric, 1
  ) AS recency_p50_min
FROM sessions s
JOIN LATERAL (
  SELECT MAX(created_at) AS last_mem_time
  FROM memories WHERE agent = s.agent AND created_at < s.started_at
) m_last ON true
{where}
GROUP BY s.agent;
"""


def get_conn():
    if USE_PSYCOPG2:
        import psycopg2
        return psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASS, dbname=DB_NAME
        )
    else:
        return pg.Connection(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASS, database=DB_NAME
        )


def run_query(conn, sql):
    if USE_PSYCOPG2:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    else:
        rows = conn.run(sql)
        cols = [c["name"] for c in conn.columns]
        return [dict(zip(cols, row)) for row in rows]


def main():
    where_clause = f"WHERE s.agent = '{AGENT_FILTER}'" if AGENT_FILTER else ""

    conn = get_conn()
    m1 = run_query(conn, SQL_M1.format(where=where_clause))
    m2 = run_query(conn, SQL_M2.format(where=where_clause))
    conn.close()

    print(f"\n{'='*60}")
    print(f"  SEAL — distill_metrics_report  ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    if AGENT_FILTER:
        print(f"  Agente: {AGENT_FILTER}")
    print(f"{'='*60}")

    print("\n[M1] distill_coverage (meta: ≥0.85)")
    print(f"{'session_id':<36}  {'agent':<8}  {'dest':>5}  {'cand':>5}  {'cov':>6}")
    print("-" * 65)
    for r in m1:
        cov = r['distill_coverage'] or 0
        flag = "🔴" if cov < 0.85 else "✅"
        print(f"{str(r['session_id']):<36}  {r['agent']:<8}  "
              f"{r['destiladas']:>5}  {r['candidatas']:>5}  {float(cov):>6.3f} {flag}")

    print("\n[M2] boot_memory_recency p50 (meta: <30 min)")
    for r in m2:
        mins = r['recency_p50_min'] or 0
        flag = "🔴" if float(mins) > 30 else "✅"
        print(f"  {r['agent']}: {float(mins):.1f} min {flag}")

    print("\n[M3-M6] pendiente v2 (requieren duration_ms + outcome en distilled_exchanges)")
    print(f"{'='*60}\n")

    # JSON output para DUM
    report = {"timestamp": datetime.now().isoformat(), "m1": m1, "m2": m2}
    out_path = f"/tmp/distill_metrics_{AGENT_FILTER or 'all'}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  JSON guardado: {out_path}")


if __name__ == "__main__":
    main()
