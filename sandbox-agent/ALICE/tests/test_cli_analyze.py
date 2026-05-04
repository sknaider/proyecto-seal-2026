"""Tests for ALICE analyze CLI — kernel_alice wrappers + traces."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
CLI = _BASE / "cli" / "analyze.py"


def _run(args: list[str]) -> tuple[int, dict]:
    """Run the CLI in subprocess, return (rc, parsed JSON)."""
    proc = subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, timeout=15,
    )
    if proc.stdout.strip():
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {"raw": proc.stdout}
    else:
        payload = {}
    return proc.returncode, payload


# ── cost-model ──────────────────────────────────────────────────────────────

def test_cost_model_simple():
    rc, out = _run([
        "cost-model", "--name", "test",
        "--line", "spark_energy:gpu:97.20:lima_tariff:0.85",
    ])
    assert rc == 0
    assert out["kind"] == "cost_model"
    assert out["result"]["total_monthly_usd"] == 97.20
    assert out["result"]["total_annual_usd"] == round(97.20 * 12, 2)
    assert "trace_id" in out and len(out["trace_id"]) == 32


def test_cost_model_multi_lines_aggregates():
    rc, out = _run([
        "cost-model", "--name", "agg",
        "--line", "a:gpu:100.00:src1:0.9",
        "--line", "b:network:50.00:src2:0.7",
    ])
    assert rc == 0
    assert out["result"]["total_monthly_usd"] == 150.00
    assert out["result"]["by_kind_monthly"]["gpu"] == 100.00
    assert out["result"]["by_kind_monthly"]["network"] == 50.00


def test_cost_model_invalid_spec_returns_nonzero():
    rc, out = _run(["cost-model", "--name", "x", "--line", "incomplete"])
    assert rc == 1
    assert out["kind"] == "error"


# ── monte-carlo ─────────────────────────────────────────────────────────────

def test_monte_carlo_deterministic_with_seed():
    rc, out = _run([
        "monte-carlo",
        "--formula", "energy*tariff*hours",
        "--dist", "energy=200,250,300",
        "--dist", "tariff=0.16,0.18,0.20",
        "--const", "hours=720",
        "--runs", "1000",
        "--seed", "42",
    ])
    assert rc == 0
    assert out["kind"] == "monte_carlo"
    r = out["result"]
    assert r["runs"] == 1000
    assert r["min"] <= r["mean"] <= r["max"]
    assert r["p05"] <= r["p95"]
    assert out["constants"]["hours"] == 720.0


def test_monte_carlo_rejects_illegal_formula():
    rc, out = _run([
        "monte-carlo",
        "--formula", "__import__('os').system('ls')",
        "--dist", "x=1,2,3",
        "--runs", "10",
    ])
    assert rc == 1
    assert out["kind"] == "error"


# ── scenario ────────────────────────────────────────────────────────────────

def test_scenario_sensitivity_ranks_drivers():
    rc, out = _run([
        "scenario",
        "--formula", "a*b*c",
        "--base", "a=10,b=5,c=2",
        "--delta", "0.1",
    ])
    assert rc == 0
    assert out["kind"] == "sensitivity"
    rows = out["rows"]
    assert len(rows) == 3
    # All elasticities for a*b*c should be ~1.0 by symmetry
    for row in rows:
        assert abs(row["elasticity"] - 1.0) < 0.01


# ── assumption ──────────────────────────────────────────────────────────────

def test_assumption_record_then_list_roundtrip(tmp_path, monkeypatch):
    """Record + invalidate cycle. Note: ledger is global to /sandbox-agent/ALICE/state/
    so we rely on uniqueness of the assumption_id."""
    rc, out = _run([
        "assumption", "record",
        "--statement", "test_assumption_smoke_xyz_unique",
        "--source", "pytest",
        "--confidence", "0.5",
    ])
    assert rc == 0
    aid = out["assumption_id"]
    assert len(aid) == 12

    rc, listed = _run(["assumption", "list"])
    assert rc == 0
    statements = [item.get("statement") for item in listed["items"]]
    assert "test_assumption_smoke_xyz_unique" in statements

    rc, inv = _run(["assumption", "invalidate", "--id", aid, "--reason", "test cleanup"])
    assert rc == 0
    assert inv["ok"] is True
