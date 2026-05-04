"""ALICE analyze CLI — invokable wrapper for kernel_alice modules.

Designed for ALICE-Claude to invoke from her Bash tool. Each subcommand:
  1. parses args
  2. imports the relevant kernel_alice module
  3. runs the computation
  4. records a reasoning trace
  5. emits JSON to stdout

Subcommands:
  cost-model     — build a CostModel from line specs
  monte-carlo    — run Monte Carlo over a function and triangular distributions
  scenario       — sensitivity analysis (one-at-a-time elasticity)
  assumption     — record/list/invalidate assumptions in the ledger

Examples:
  analyze.py cost-model --line "spark_energy:gpu:32.40:Lima_tariff_2026:0.85"
  analyze.py monte-carlo --formula "energy*tariff*hours" \\
      --dist "energy=200,250,300" --dist "tariff=0.16,0.18,0.20" --hours 720 --runs 5000
  analyze.py scenario --formula "energy*tariff*hours" \\
      --base "energy=250,tariff=0.18,hours=720" --delta 0.10
  analyze.py assumption record --statement "Tarifa Lima estable 2026" --source SUNAT --confidence 0.85
  analyze.py assumption list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE / "kernel"))
sys.path.insert(0, str(_BASE / "kernel_alice"))

from reasoning_logger import store_trace, update_trace_outcome  # noqa: E402
import cost_modeling  # noqa: E402
import scenario_simulator  # noqa: E402
import assumption_tracker  # noqa: E402


def _emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _safe_eval_formula(formula: str, vars_dict: dict[str, float]) -> float:
    """Evaluate a numeric formula like 'energy*tariff*hours' against vars_dict.
    Restricted to numeric ops only — no builtins, no attribute access.
    """
    allowed_chars = set("0123456789+-*/().eE_ ")
    if any(c not in allowed_chars and not c.isalpha() for c in formula):
        raise ValueError(f"formula contains illegal characters: {formula!r}")
    return float(eval(formula, {"__builtins__": {}}, dict(vars_dict)))


# ── cost-model ───────────────────────────────────────────────────────────────

def _parse_line(spec: str) -> cost_modeling.CostLine:
    """Parse 'label:kind:monthly_usd:source:confidence'."""
    parts = spec.split(":")
    if len(parts) < 3:
        raise ValueError(f"line spec needs at least label:kind:monthly_usd, got {spec!r}")
    label, kind, monthly = parts[0], parts[1], float(parts[2])
    source = parts[3] if len(parts) > 3 else ""
    confidence = float(parts[4]) if len(parts) > 4 else 0.7
    return cost_modeling.CostLine(
        label=label, kind=kind, monthly_usd=monthly,
        source=source, confidence=confidence,
    )


def cmd_cost_model(args: argparse.Namespace) -> int:
    trace_id = store_trace(
        task="cli_cost_model",
        input_excerpt=f"name={args.name} lines={len(args.line)}",
        premises=[f"line:{spec[:60]}" for spec in args.line],
        reasoning="building CostModel from CLI specs",
        decision="aggregate monthly + annual + by_kind",
        confidence=0.9,
        action_type="cli_analysis",
    )
    try:
        model = cost_modeling.CostModel(name=args.name)
        for spec in args.line:
            model.add(_parse_line(spec))
        report = model.report()
        _emit({"trace_id": trace_id, "kind": "cost_model", "result": report})
        update_trace_outcome(trace_id, f"model {args.name}: ${report['total_monthly_usd']}/mo", True)
        return 0
    except Exception as ex:
        update_trace_outcome(trace_id, f"error: {ex}", False)
        _emit({"trace_id": trace_id, "kind": "error", "error": str(ex)})
        return 1


# ── monte-carlo ──────────────────────────────────────────────────────────────

def _parse_dist(spec: str) -> tuple[str, scenario_simulator.Distribution]:
    """Parse 'name=low,mode,high' into (name, Distribution)."""
    if "=" not in spec:
        raise ValueError(f"dist spec must be 'name=low,mode,high', got {spec!r}")
    name, vals = spec.split("=", 1)
    parts = [float(x) for x in vals.split(",")]
    if len(parts) != 3:
        raise ValueError(f"dist needs 3 values (low,mode,high), got {parts}")
    return name.strip(), scenario_simulator.Distribution(*parts)


def cmd_monte_carlo(args: argparse.Namespace) -> int:
    trace_id = store_trace(
        task="cli_monte_carlo",
        input_excerpt=f"formula={args.formula} dists={len(args.dist)} runs={args.runs}",
        premises=[f"dist:{d[:60]}" for d in args.dist] + [f"formula:{args.formula}"],
        reasoning="Monte Carlo with triangular dists; pure-python sampler",
        decision=f"run {args.runs} simulations",
        confidence=0.85,
        action_type="cli_analysis",
    )
    try:
        dists = dict(_parse_dist(d) for d in args.dist)
        constants: dict[str, float] = {}
        for c in (args.const or []):
            k, v = c.split("=", 1)
            constants[k.strip()] = float(v)

        def fn(sample: dict[str, float]) -> float:
            return _safe_eval_formula(args.formula, {**constants, **sample})

        result = scenario_simulator.monte_carlo(
            fn=fn, inputs=dists, runs=args.runs, seed=args.seed,
        )
        _emit({"trace_id": trace_id, "kind": "monte_carlo",
               "formula": args.formula, "constants": constants, "result": result})
        update_trace_outcome(trace_id, f"mean={result['mean']} p05-p95=[{result['p05']}, {result['p95']}]", True)
        return 0
    except Exception as ex:
        update_trace_outcome(trace_id, f"error: {ex}", False)
        _emit({"trace_id": trace_id, "kind": "error", "error": str(ex)})
        return 1


# ── scenario ─────────────────────────────────────────────────────────────────

def cmd_scenario(args: argparse.Namespace) -> int:
    trace_id = store_trace(
        task="cli_scenario_sensitivity",
        input_excerpt=f"formula={args.formula} base={args.base} delta={args.delta}",
        premises=[f"base:{args.base}", f"delta:{args.delta}", f"formula:{args.formula}"],
        reasoning="one-at-a-time elasticity per input variable",
        decision="rank inputs by absolute elasticity",
        confidence=0.9,
        action_type="cli_analysis",
    )
    try:
        base: dict[str, float] = {}
        for kv in args.base.split(","):
            k, v = kv.split("=", 1)
            base[k.strip()] = float(v)

        def fn(vars_dict: dict[str, float]) -> float:
            return _safe_eval_formula(args.formula, vars_dict)

        rows = scenario_simulator.sensitivity_one_at_a_time(
            fn=fn, base_inputs=base, delta_pct=args.delta,
        )
        _emit({"trace_id": trace_id, "kind": "sensitivity",
               "formula": args.formula, "base": base, "rows": rows})
        top = rows[0]["input"] if rows else None
        update_trace_outcome(trace_id, f"top_driver={top} of {len(rows)} inputs", True)
        return 0
    except Exception as ex:
        update_trace_outcome(trace_id, f"error: {ex}", False)
        _emit({"trace_id": trace_id, "kind": "error", "error": str(ex)})
        return 1


# ── assumption ──────────────────────────────────────────────────────────────

def cmd_assumption_record(args: argparse.Namespace) -> int:
    trace_id = store_trace(
        task="cli_assumption_record",
        input_excerpt=args.statement[:200],
        premises=[f"source={args.source}", f"confidence={args.confidence}"],
        reasoning="recording explicit assumption to ledger",
        decision="store as active",
        confidence=0.95,
        action_type="cli_analysis",
    )
    try:
        aid = assumption_tracker.record(
            statement=args.statement,
            source=args.source,
            confidence=args.confidence,
        )
        _emit({"trace_id": trace_id, "kind": "assumption_recorded",
               "assumption_id": aid, "statement": args.statement})
        update_trace_outcome(trace_id, f"recorded {aid}", True)
        return 0
    except Exception as ex:
        update_trace_outcome(trace_id, f"error: {ex}", False)
        _emit({"trace_id": trace_id, "kind": "error", "error": str(ex)})
        return 1


def cmd_assumption_list(args: argparse.Namespace) -> int:
    items = assumption_tracker.list_active()
    _emit({"kind": "assumption_active", "count": len(items), "items": items})
    return 0


def cmd_assumption_invalidate(args: argparse.Namespace) -> int:
    ok = assumption_tracker.invalidate(args.id, args.reason)
    _emit({"kind": "assumption_invalidated", "ok": ok, "id": args.id, "reason": args.reason})
    return 0 if ok else 1


# ── argparse setup ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alice-analyze",
                                description="ALICE analytical CLI — invokes kernel_alice modules")
    sub = p.add_subparsers(dest="cmd", required=True)

    cm = sub.add_parser("cost-model", help="Build a CostModel from line specs")
    cm.add_argument("--name", default="adhoc")
    cm.add_argument("--line", action="append", required=True,
                    help="label:kind:monthly_usd[:source[:confidence]]")
    cm.set_defaults(func=cmd_cost_model)

    mc = sub.add_parser("monte-carlo", help="Monte Carlo with triangular dists")
    mc.add_argument("--formula", required=True, help="e.g. 'energy*tariff*hours'")
    mc.add_argument("--dist", action="append", required=True,
                    help="name=low,mode,high (repeatable)")
    mc.add_argument("--const", action="append", default=[], help="name=value (constants)")
    mc.add_argument("--runs", type=int, default=5000)
    mc.add_argument("--seed", type=int, default=None)
    mc.set_defaults(func=cmd_monte_carlo)

    sc = sub.add_parser("scenario", help="One-at-a-time elasticity sensitivity")
    sc.add_argument("--formula", required=True)
    sc.add_argument("--base", required=True, help="comma-sep name=value pairs")
    sc.add_argument("--delta", type=float, default=0.10)
    sc.set_defaults(func=cmd_scenario)

    ap = sub.add_parser("assumption", help="assumption ledger ops")
    apsub = ap.add_subparsers(dest="op", required=True)

    rec = apsub.add_parser("record")
    rec.add_argument("--statement", required=True)
    rec.add_argument("--source", default="")
    rec.add_argument("--confidence", type=float, default=0.7)
    rec.set_defaults(func=cmd_assumption_record)

    ls = apsub.add_parser("list")
    ls.set_defaults(func=cmd_assumption_list)

    inv = apsub.add_parser("invalidate")
    inv.add_argument("--id", required=True)
    inv.add_argument("--reason", required=True)
    inv.set_defaults(func=cmd_assumption_invalidate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
