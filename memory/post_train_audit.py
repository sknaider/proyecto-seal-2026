#!/usr/bin/env python3
"""post_train_audit.py — Post-training audit & merge/triage decision.

Consumes the JSON emitted by diagnostic_eval_extended.py, re-runs the
contamination gate one more time as closure, computes cryptographic
fingerprints of the adapter checkpoint, and emits a final audit JSON
with the merge/triage verdict.

Written for LatentGraphMem V1.1 post-mortem 2026-04-12. Rule (William):
nothing ships without a second look.

Usage:
    python3 post_train_audit.py \\
        --adapter ~/IA/modelos/latent-graphmem-soul-v1/best \\
        --eval    diagnostic/results/latent_v11_extended.json \\
        --baseline 0.4375 \\
        --source   synthetic_v1

Exit codes:
    0 — audit clean, decision rendered
    1 — contamination re-check failed or critical inconsistency
    2 — infrastructure error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTAMINATION_GATE = HERE / "latent_graphmem" / "contamination_check.py"
DEFAULT_REPORT_DIR = HERE / "diagnostic" / "reports"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint_adapter(adapter_dir: Path) -> dict:
    if not adapter_dir.exists():
        raise FileNotFoundError(f"adapter dir not found: {adapter_dir}")
    files = {}
    for p in sorted(adapter_dir.rglob("*")):
        if p.is_file():
            files[str(p.relative_to(adapter_dir))] = {
                "size": p.stat().st_size,
                "mtime": p.stat().st_mtime,
                "sha256": sha256_file(p),
            }
    combined = hashlib.sha256(
        "".join(f"{k}:{v['sha256']}" for k, v in sorted(files.items())).encode()
    ).hexdigest()
    return {"files": files, "combined_sha256": combined, "n_files": len(files)}


def rerun_contamination_gate(source: str) -> dict:
    if not CONTAMINATION_GATE.exists():
        return {"status": "MISSING", "gate_path": str(CONTAMINATION_GATE)}
    r = subprocess.run(
        [sys.executable, str(CONTAMINATION_GATE),
         "--pairs-source", source, "--json"],
        cwd=str(HERE), capture_output=True, text=True,
    )
    try:
        report = json.loads(r.stdout)
    except json.JSONDecodeError:
        report = {"raw": r.stdout, "stderr": r.stderr}
    return {
        "status": "CLEAN" if r.returncode == 0 else "CONTAMINATED",
        "exit_code": r.returncode,
        "report": report,
    }


def decide(eval_report: dict, baseline: float) -> dict:
    """Merge if LatentGraphMem recall@5 > baseline, else triage.

    Schema agreed with ADA (eval_extended output):
        recall.at_5 OR retrievers.latent.recall.at_5
    """
    def _r5(node):
        if not isinstance(node, dict):
            return None
        for k in ("recall@5", "recall_at_5"):
            if k in node:
                return node[k]
        r = node.get("recall")
        if isinstance(r, dict):
            return r.get("at_5") or r.get("5")
        return None

    latent_node = (
        (eval_report.get("by_mode") or {}).get("latent")
        or (eval_report.get("retrievers") or {}).get("latent")
        or {}
    )
    magma_node = (
        (eval_report.get("by_mode") or {}).get("magma")
        or (eval_report.get("retrievers") or {}).get("magma")
        or {}
    )
    latent_r5 = _r5(latent_node) if latent_node else _r5(eval_report)
    magma_r5 = _r5(magma_node)
    if latent_r5 is None:
        return {"verdict": "UNKNOWN", "baseline": baseline,
                "reason": "recall@5 not in eval report"}

    tripwire = latent_r5 > 0.95

    if latent_r5 >= baseline:
        return {
            "verdict": "MERGE_CANDIDATE",
            "latent_recall_at_5": latent_r5,
            "magma_recall_at_5": magma_r5,
            "baseline": baseline,
            "delta_vs_baseline": round(latent_r5 - baseline, 4),
            "tripwire_10_10": tripwire,
            "note": ("SUSPICIOUSLY HIGH — trigger William's 10/10 rule, "
                     "audit data leak again before merge"
                     if tripwire else
                     "within normal range — ready for William's merge approval"),
        }
    return {
        "verdict": "TRIAGE",
        "latent_recall_at_5": latent_r5,
        "magma_recall_at_5": magma_r5,
        "baseline": baseline,
        "delta_vs_baseline": round(latent_r5 - baseline, 4),
        "tripwire_10_10": False,
        "note": ("below baseline — do not merge; run error breakdown "
                 "by query_type with ADA, decide V2 vs kill"),
    }


def store_to_soul(audit: dict) -> None:
    """Best-effort SOUL memory_store via MCP — log only, never fail."""
    try:
        import asyncio
        import asyncpg
        async def _go():
            conn = await asyncpg.connect(
                "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
            )
            try:
                await conn.execute(
                    "INSERT INTO memories "
                    "(agent, content, category, importance, tags, metadata) "
                    "VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
                    "JARVIS",
                    ("LatentGraphMem V1.1 post-train audit — "
                     f"{audit['decision']['verdict']}: "
                     f"recall@5={audit['decision'].get('latent_recall_at_5')} "
                     f"vs baseline={audit['decision']['baseline']}"),
                    "insight", 8,
                    ["latent_graphmem", "v1.1", "audit",
                     audit["decision"]["verdict"].lower()],
                    json.dumps(audit, default=str),
                )
            finally:
                await conn.close()
        asyncio.run(_go())
        return True
    except Exception as e:
        print(f"[audit] WARN: SOUL store failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", type=Path, required=True,
                    help="path to LoRA adapter dir (e.g., best/)")
    ap.add_argument("--eval", type=Path, required=True,
                    help="path to diagnostic_eval_extended JSON output")
    ap.add_argument("--baseline", type=float, default=0.4375,
                    help="MAGMA baseline recall@5 on test_set_v1")
    ap.add_argument("--source", default="synthetic_v1",
                    help="training pairs source tag for contamination recheck")
    ap.add_argument("--out", type=Path, default=None,
                    help="output audit JSON path (default: reports/<adapter>_audit.json)")
    ap.add_argument("--no-store", action="store_true",
                    help="skip SOUL memory_store")
    args = ap.parse_args()

    try:
        t0 = time.time()

        if not args.eval.exists():
            print(f"[audit] ERROR: eval report not found: {args.eval}",
                  file=sys.stderr)
            return 2
        eval_report = json.loads(args.eval.read_text())

        print(f"[audit] fingerprinting adapter {args.adapter}...")
        fp = fingerprint_adapter(args.adapter)
        print(f"[audit] combined sha256 = {fp['combined_sha256'][:16]}... "
              f"({fp['n_files']} files)")

        print(f"[audit] re-running contamination gate (source={args.source})...")
        gate = rerun_contamination_gate(args.source)
        if gate["status"] != "CLEAN":
            print(f"[audit] ABORT: contamination gate status={gate['status']}",
                  file=sys.stderr)

        print(f"[audit] rendering decision against baseline={args.baseline}...")
        decision = decide(eval_report, args.baseline)

        audit = {
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "adapter_path": str(args.adapter.resolve()),
            "eval_report_path": str(args.eval.resolve()),
            "training_source": args.source,
            "adapter_fingerprint": fp,
            "contamination_recheck": gate,
            "decision": decision,
            "audit_duration_s": round(time.time() - t0, 2),
            "auditor": "JARVIS",
            "rule_reference": "William 2026-04-12 — silent bugs scale exponentially",
        }

        out_path = args.out or (
            DEFAULT_REPORT_DIR / f"{args.adapter.name}_audit.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(audit, indent=2, default=str))
        print(f"[audit] wrote {out_path}")

        if not args.no_store:
            if store_to_soul(audit):
                print("[audit] stored to SOUL")

        print(f"[audit] VERDICT: {decision['verdict']} "
              f"(recall@5={decision.get('latent_recall_at_5')} "
              f"vs baseline={decision['baseline']})")

        if gate["status"] != "CLEAN":
            return 1
        return 0

    except Exception as e:
        print(f"[audit] FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
