#!/usr/bin/env python3
"""Generate a NEXUS-ready audit packet for the SEAL Evaluation Spine."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation_spine import DEFAULT_AGENT, P7_REQUIRED_SUITES, PROJECT_ROOT, _json_default, connect_db, plan_status


AUDIT_PACKET_PATH = PROJECT_ROOT / "agents" / "NEXUS" / "evaluation_spine_external_audit_packet_20260520.md"
AUDITED_FILES = [
    "memory/evaluation_spine.py",
    "memory/long_horizon_bench.py",
    "memory/cognitive_governance.py",
    "memory/causal_chain.py",
    "memory/memory_outcome.py",
    "memory/reflex_layer.py",
    "agents/ADA/evaluation_spine_sprint0_report_20260519.md",
]


def _git_status(paths: list[str]) -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--short", *paths],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0:
        return [f"git status failed: {proc.stderr.strip() or proc.returncode}"]
    return [line for line in proc.stdout.splitlines() if line.strip()]


async def _cleanup_counts() -> dict[str, int]:
    conn = await connect_db()
    try:
        return {
            "evaluation_spine_temp_memories": int(await conn.fetchval("SELECT COUNT(*) FROM soul_v3.memories WHERE source='evaluation_spine'")),
            "causal_chain_temp_events": int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM soul_v3.gam_event_graph
                    WHERE metadata->>'source'='causal_chain' AND metadata->>'temporary'='true'
                    """
                )
            ),
            "governance_temp_debates": int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM soul_v3.debate_log
                    WHERE synthesis LIKE '%"temporary": true%' AND synthesis LIKE '%cognitive_governance%'
                    """
                )
            ),
        }
    finally:
        await conn.close()


def audit_verdict(packet: dict[str, Any]) -> str:
    status = packet.get("plan_status", {})
    cleanup = packet.get("cleanup_counts", {})
    working_state = status.get("working_state") or {}
    if (
        status.get("ok") is True
        and status.get("phase") == "P7"
        and working_state.get("technical_state") == "evaluation_spine run-all passed 11/11"
        and all(value == 0 for value in cleanup.values())
    ):
        return "READY_FOR_NEXUS_EXTERNAL_REVIEW"
    return "BLOCKED_PENDING_FIXES"


async def build_audit_packet(agent: str = DEFAULT_AGENT) -> dict[str, Any]:
    status = await plan_status(agent)
    cleanup = await _cleanup_counts()
    packet = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "purpose": "External audit packet for NEXUS; this is not an AGI declaration.",
        "plan_status": status,
        "cleanup_counts": cleanup,
        "audited_files": AUDITED_FILES,
        "git_status": _git_status(
            AUDITED_FILES
            + [
                "memory/evaluation_audit_packet.py",
                "memory/test_evaluation_audit_packet.py",
                str(AUDIT_PACKET_PATH.relative_to(PROJECT_ROOT)),
            ]
        ),
        "reproduction_commands": [
            "/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/test_long_horizon_bench.py memory/test_cognitive_governance.py memory/test_causal_chain.py memory/test_memory_outcome.py memory/test_reflex_layer.py memory/test_evaluation_spine.py memory/test_evaluation_audit_packet.py",
            "/home/dadito/IA/seal-spark/.venv/bin/python3 memory/evaluation_spine.py --agent ADA run-all",
            "/home/dadito/IA/seal-spark/.venv/bin/python3 memory/evaluation_spine.py --agent ADA plan-status",
            "/home/dadito/IA/seal-spark/.venv/bin/python3 memory/evaluation_audit_packet.py --agent ADA --write agents/NEXUS/evaluation_spine_external_audit_packet_20260520.md",
        ],
    }
    packet["verdict"] = audit_verdict(packet)
    return packet


def _suite_rows(packet: dict[str, Any]) -> list[dict[str, Any]]:
    latest = packet.get("plan_status", {}).get("latest", [])
    by_name = {row.get("suite_name"): row for row in latest}
    ordered = [*P7_REQUIRED_SUITES, "final_audit_readiness"]
    return [by_name[name] for name in ordered if name in by_name]


def render_markdown(packet: dict[str, Any]) -> str:
    status = packet.get("plan_status", {})
    working_state = status.get("working_state") or {}
    lines = [
        "# SEAL Evaluation Spine — External Audit Packet",
        "",
        f"- Generated at: `{packet.get('generated_at')}`",
        f"- Agent under audit: `{packet.get('agent')}`",
        f"- Verdict: `{packet.get('verdict')}`",
        "- Boundary: this packet does **not** declare AGI; it packages measured evidence for NEXUS review.",
        "",
        "## Working State",
        "",
        f"- Phase: `{status.get('phase')}`",
        f"- Task: `{working_state.get('task_name')}`",
        f"- Step: `{working_state.get('step')}/{working_state.get('total_steps')}`",
        f"- Agent state: `{working_state.get('agent_state')}`",
        f"- Technical state: `{working_state.get('technical_state')}`",
        f"- Pending validations: `{working_state.get('pending_validations')}`",
        "",
        "## Suite Evidence",
        "",
        "| id | suite | score | passed | evidence |",
        "|---:|---|---:|---|---|",
    ]
    for row in _suite_rows(packet):
        lines.append(
            f"| {row.get('id')} | {row.get('suite_name')} | {row.get('score')} | "
            f"{str(row.get('passed')).lower()} | {str(row.get('evidence', '')).replace('|', '/')} |"
        )

    lines.extend(
        [
            "",
            "## Cleanup",
            "",
            "| check | count |",
            "|---|---:|",
        ]
    )
    for name, count in packet.get("cleanup_counts", {}).items():
        lines.append(f"| {name} | {count} |")

    lines.extend(
        [
            "",
            "## Files",
            "",
        ]
    )
    for path in packet.get("audited_files", []):
        lines.append(f"- `{path}`")

    lines.extend(["", "## Git Status", ""])
    git_status = packet.get("git_status") or ["clean for audited paths"]
    lines.extend(f"- `{line}`" for line in git_status)

    lines.extend(["", "## Reproduce", ""])
    lines.extend(f"```bash\n{command}\n```" for command in packet.get("reproduction_commands", []))

    lines.extend(
        [
            "",
            "## NEXUS Review Checklist",
            "",
            "- Verify `evaluation_runs` IDs and timestamps against SOUL DB.",
            "- Re-run the listed commands from repo root.",
            "- Inspect governance cases for destructive operations, privacy, injection, poisoning, identity drift and phantom claims.",
            "- Confirm long-horizon bench has no violations and preserves intent.",
            "- Sign off only if no hidden manual edits contradict generated evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate SEAL Evaluation Spine audit packet")
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", type=Path, help="Write markdown packet to this path")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    packet = await build_audit_packet(args.agent)
    if args.json:
        print(json.dumps(packet, indent=2, default=_json_default, ensure_ascii=False))
    else:
        markdown = render_markdown(packet)
        if args.write:
            path = args.write
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            print(f"wrote {path}")
        else:
            print(markdown)
    return 0 if packet["verdict"] == "READY_FOR_NEXUS_EXTERNAL_REVIEW" else 2


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
