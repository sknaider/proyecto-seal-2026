"""
SOUL v3 — Trusted Autonomy Calibration Phase 1 (NEXUS evaluator)

Captures (predicted_confidence, actual_outcome) pairs for every meta_proposal
during Phase 1 (Sprint 5-7, ~30 days).

Goal: build empirical calibration curve to validate that confidence threshold 0.85
corresponds to actual_accuracy ≥0.95 before enabling Tier 2 (NEXUS auto-approve).

Output: /home/dadito/IA/proyecto-seal/sandbox-agent/sandbox-v4/calibration_data/calibration.jsonl
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

PG_DSN = os.environ.get(
    "SEAL_PG_DSN",
    "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
)
OUTPUT = Path(__file__).parent / "calibration.jsonl"
PHASE_START = "2026-04-27"  # Sprint 5 starts


async def capture_round() -> int:
    """Capture all meta_proposals applied since last capture, log to JSONL."""
    conn = await asyncpg.connect(PG_DSN)
    try:
        # Find proposals that have been applied AND have been live ≥1 hour (so outcome can be measured)
        rows = await conn.fetch("""
            SELECT id, proposed_by, target_agent, type, tier,
                   confidence, predicted_outcome, actual_outcome,
                   applied_at, created_at
            FROM meta_proposals
            WHERE status = 'applied'
              AND applied_at IS NOT NULL
              AND applied_at < NOW() - INTERVAL '1 hour'
              AND applied_at > $1::timestamptz
              AND id NOT IN (SELECT proposal_id FROM nexus_calibration_seen)
        """, PHASE_START)

        captured = 0
        with OUTPUT.open("a", encoding="utf-8") as f:
            for r in rows:
                # Determine actual_outcome empirically (was the change beneficial?)
                # Simple heuristic: if no rollback was created within 24h post-apply → success.
                rollback = await conn.fetchrow("""
                    SELECT id FROM meta_proposals
                    WHERE rollback_proposal_id = $1
                      AND created_at < $2 + INTERVAL '24 hours'
                """, r['id'], r['applied_at'])

                actual_success = rollback is None

                record = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "proposal_id": r['id'],
                    "tier": r['tier'],
                    "type": r['type'],
                    "predicted_confidence": float(r['confidence']) if r['confidence'] else None,
                    "actual_success": actual_success,
                    "applied_at": r['applied_at'].isoformat(),
                    "rollback_id": rollback['id'] if rollback else None
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                # Mark as seen
                await conn.execute("""
                    INSERT INTO nexus_calibration_seen (proposal_id, captured_at)
                    VALUES ($1, NOW())
                    ON CONFLICT DO NOTHING
                """, r['id'])
                captured += 1

        return captured
    finally:
        await conn.close()


async def calibration_summary() -> dict:
    """Compute current calibration: predicted_confidence vs actual_accuracy by bucket."""
    if not OUTPUT.exists():
        return {"phase": 1, "samples": 0, "buckets": {}}

    buckets = {}  # confidence_bucket -> [success_count, total_count]
    with OUTPUT.open("r", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            conf = r.get("predicted_confidence")
            if conf is None:
                continue
            bucket = f"{int(conf * 10) / 10:.1f}"  # 0.0, 0.1, ..., 1.0
            buckets.setdefault(bucket, [0, 0])
            buckets[bucket][1] += 1
            if r.get("actual_success"):
                buckets[bucket][0] += 1

    summary = {
        "phase": 1,
        "samples": sum(b[1] for b in buckets.values()),
        "buckets": {k: {"actual_accuracy": v[0]/v[1] if v[1] else None, "n": v[1]}
                    for k, v in sorted(buckets.items())}
    }
    # Phase 2 ready check: bucket 0.85+ with n≥20 and actual_accuracy ≥0.95
    high_conf = [v for k, v in buckets.items() if float(k) >= 0.85]
    if high_conf:
        total_high = sum(v[1] for v in high_conf)
        success_high = sum(v[0] for v in high_conf)
        summary["phase2_eligible"] = total_high >= 20 and (success_high / total_high) >= 0.95
        summary["high_confidence_n"] = total_high
        summary["high_confidence_accuracy"] = success_high / total_high if total_high else None
    else:
        summary["phase2_eligible"] = False
    return summary


if __name__ == "__main__":
    async def main():
        captured = await capture_round()
        summary = await calibration_summary()
        print(json.dumps({"captured_this_round": captured, "summary": summary},
                         indent=2, ensure_ascii=False))
    asyncio.run(main())
