#!/usr/bin/env python3
"""Post-activation monitor for guarded SOUL cognitive graph assist.

This is a read-only closure gate. It verifies that:
- guarded assist telemetry passes the promotion gate;
- raw shadow is not treated as the active production mode;
- the live MCP service is configured for guarded assist;
- optional SEAL core health is GREEN.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analyze_soul_cognitive_graph_shadow import (
    DEFAULT_PATH,
    analyze_rows,
    filter_rows_by_query_tokens,
    load_rows,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = Path(__file__).parent / "diagnostic" / "soul_cognitive_graph_post_activation_latest.json"


def run_command(args: list[str], *, cwd: Path = ROOT, timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def systemd_environment() -> dict[str, Any]:
    code, stdout, stderr = run_command(
        ["systemctl", "--user", "show", "seal-mcp-server.service", "-p", "Environment", "-p", "ActiveState", "-p", "MainPID"],
        timeout=20,
    )
    env_line = ""
    active_state = ""
    main_pid = ""
    for line in stdout.splitlines():
        if line.startswith("Environment="):
            env_line = line.removeprefix("Environment=")
        elif line.startswith("ActiveState="):
            active_state = line.removeprefix("ActiveState=")
        elif line.startswith("MainPID="):
            main_pid = line.removeprefix("MainPID=")
    env = {
        "command_ok": code == 0,
        "stderr": stderr.strip(),
        "active_state": active_state,
        "main_pid": main_pid,
        "environment": env_line,
        "assist": "SOUL_COGNITIVE_GRAPH_MODE=assist" in env_line,
        "preserve_top1": "SOUL_COGNITIVE_GRAPH_ASSIST_PRESERVE_TOP1=true" in env_line,
        "min_tokens_3": "SOUL_COGNITIVE_GRAPH_ASSIST_MIN_QUERY_TOKENS=3" in env_line,
        "shadow": "SOUL_COGNITIVE_GRAPH_SHADOW=true" in env_line,
    }
    env["ok"] = (
        env["command_ok"]
        and active_state == "active"
        and env["assist"]
        and env["preserve_top1"]
        and env["min_tokens_3"]
        and env["shadow"]
    )
    return env


def seal_core_health(*, skip: bool = False) -> dict[str, Any]:
    if skip:
        return {"ok": True, "skipped": True}
    code, stdout, stderr = run_command(["python3", "scripts/seal_core_guard.py", "--health"], timeout=90)
    payload: dict[str, Any] | None = None
    try:
        start = stdout.index("{")
        end = stdout.rindex("}") + 1
        payload = json.loads(stdout[start:end])
    except Exception:
        payload = None
    return {
        "ok": code == 0 and bool(payload) and payload.get("status") == "GREEN",
        "status": payload.get("status") if payload else None,
        "failed": payload.get("failed") if payload else None,
        "stderr": stderr.strip(),
    }


def build_report(
    *,
    telemetry_path: Path = DEFAULT_PATH,
    min_rows: int = 300,
    min_query_tokens: int = 3,
    max_p95_ms: float = 20.0,
    skip_health: bool = False,
) -> dict[str, Any]:
    rows, invalid = load_rows(telemetry_path)
    eligible = filter_rows_by_query_tokens(rows, min_query_tokens)
    assist_report = analyze_rows(
        eligible,
        invalid_lines=invalid,
        min_rows=min_rows,
        max_p95_ms=max_p95_ms,
        preserve_top1=True,
    )
    raw_report = analyze_rows(
        eligible,
        invalid_lines=invalid,
        min_rows=min_rows,
        max_p95_ms=max_p95_ms,
        preserve_top1=False,
    )
    env = systemd_environment()
    health = seal_core_health(skip=skip_health)
    culminated = (
        assist_report["decision"]["ready_for_assist"]
        and env["ok"]
        and health["ok"]
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "telemetry_path": str(telemetry_path),
        "source_rows": len(rows),
        "eligible_rows": len(eligible),
        "min_rows": min_rows,
        "min_query_tokens": min_query_tokens,
        "assist_preserve_top1": assist_report,
        "raw_shadow_reference": raw_report,
        "systemd": env,
        "seal_core_health": health,
        "culminated": culminated,
        "decision": "CULMINATED" if culminated else "KEEP_MONITORING",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--min-rows", type=int, default=300)
    parser.add_argument("--min-query-tokens", type=int, default=3)
    parser.add_argument("--max-p95-ms", type=float, default=20.0)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(
        telemetry_path=args.path,
        min_rows=args.min_rows,
        min_query_tokens=args.min_query_tokens,
        max_p95_ms=args.max_p95_ms,
        skip_health=args.skip_health,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        assist = report["assist_preserve_top1"]
        raw = report["raw_shadow_reference"]
        print(f"SOUL Cognitive Graph Post-Activation Monitor — {report['decision']}")
        print(f"report: {args.report}")
        print(f"rows: source={report['source_rows']} eligible={report['eligible_rows']}")
        print(
            "assist: "
            f"{assist['decision']['status']} "
            f"top1={assist['agreement']['top1_rate']:.3f} "
            f"p95_ms={assist['latency_ms']['p95']:.3f}"
        )
        print(
            "raw_shadow_reference: "
            f"{raw['decision']['status']} "
            f"top1={raw['agreement']['top1_rate']:.3f}"
        )
        print(f"systemd_ok: {report['systemd']['ok']}")
        print(f"health_ok: {report['seal_core_health']['ok']}")
    return 0 if report["culminated"] else 1


if __name__ == "__main__":
    sys.exit(main())
