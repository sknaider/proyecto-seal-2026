#!/usr/bin/env python3
"""Guard protected SEAL core daemon files.

Modes:
  --pre-commit   Block staged edits to protected core files unless explicitly allowed.
  --health       Run read-only runtime/source invariants for MCP/bridge protection.
  --core-smoke   Run source checks, py_compile, and focused tests.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
MEMORY = REPO / "memory"

PROTECTED_PATHS = {
    "AGENTS.md",
    "ada_codex.sh",
    "memory/mcp_server_v4.py",
    "memory/dual_memory_governance.py",
    "memory/test_dual_memory_governance.py",
    "memory/test_mcp_dual_memory_format.py",
    "memory/tests/test_active_recall_hook.py",
    "messages/ada_codex_remote_bridge.py",
    "messages/ada_codex_compact_monitor.py",
    "messages/ada_codex_soul_bootstrap.py",
    "messages/tests/test_ada_codex_remote_bridge_silence.py",
    "scripts/seal_core_guard.py",
}

FOCUSED_TESTS = [
    "memory/test_dual_memory_governance.py",
    "memory/test_mcp_dual_memory_format.py",
    "messages/tests/test_ada_codex_remote_bridge_silence.py",
    "memory/tests/test_active_recall_hook.py",
]

PY_COMPILE_FILES = [
    "memory/mcp_server_v4.py",
    "memory/dual_memory_governance.py",
    "messages/ada_codex_remote_bridge.py",
    "messages/ada_codex_soul_bootstrap.py",
    "scripts/seal_core_guard.py",
]

REQUIRED_SNIPPETS = {
    "messages/ada_codex_remote_bridge.py": [
        "SOUL_CANONICAL_ANCHOR_IDS = (242369, 248035)",
        "content ILIKE '%MEMORIA OPERATIVA ADA v%'",
        "capa operativa prioritaria",
        "capa emocional compacta",
    ],
    "memory/mcp_server_v4.py": [
        "DUAL_MEMORY_OPERATIONAL_TOKEN_BUDGET = 2200",
        "DUAL_MEMORY_EMOTIONAL_TOKEN_BUDGET = 600",
        "def _parse_metadata_arg",
        "def format_dual_memory_entries",
        "websearch_to_tsquery('simple', $2)",
    ],
    "memory/dual_memory_governance.py": [
        "OPERATIONAL_CATEGORIES",
        "EMOTIONAL_CATEGORIES",
        "memoria operativa",
        "memoria emocional",
    ],
    "AGENTS.md": [
        "MCP/bridge son core daemons protegidos",
        "SEAL_ALLOW_CORE_DAEMON_EDIT=1",
    ],
}

CORE_SERVICES = [
    "seal-mcp-server.service",
    "ada-codex-remote-bridge.service",
    "ada-codex-compact-monitor.service",
]


def run(cmd: list[str], *, timeout: int = 60, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def staged_paths() -> set[str]:
    proc = run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"], timeout=10)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git diff --cached failed")
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def protected_staged_paths() -> list[str]:
    staged = staged_paths()
    return sorted(path for path in staged if path in PROTECTED_PATHS)


def check_pre_commit() -> int:
    touched = protected_staged_paths()
    if not touched:
        return 0

    if os.environ.get("SEAL_ALLOW_CORE_DAEMON_EDIT") != "1":
        print("SEAL core guard: protected MCP/bridge files are staged:", file=sys.stderr)
        for path in touched:
            print(f"  - {path}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Blocked. To modify these files, run focused checks and commit with:", file=sys.stderr)
        print("  SEAL_ALLOW_CORE_DAEMON_EDIT=1 git commit ...", file=sys.stderr)
        print("William can override; silent core daemon edits cannot pass this hook.", file=sys.stderr)
        return 1

    return check_core_smoke()


def check_required_snippets() -> dict[str, Any]:
    failures: list[str] = []
    for rel_path, snippets in REQUIRED_SNIPPETS.items():
        path = REPO / rel_path
        if not path.exists():
            failures.append(f"{rel_path}: missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for snippet in snippets:
            if snippet not in text:
                failures.append(f"{rel_path}: missing snippet {snippet!r}")
    return {"ok": not failures, "failures": failures}


def check_python_contracts() -> dict[str, Any]:
    sys.path.insert(0, str(MEMORY))
    failures: list[str] = []
    try:
        from dual_memory_governance import classify_layer

        layer = classify_layer(
            "decision",
            "semantic",
            "MEMORIA OPERATIVA ADA v2: usar memoria emocional solo como ancla compacta.",
        )
        if layer != "operational":
            failures.append(f"classify_layer returned {layer!r}, expected 'operational'")
    except Exception as exc:
        failures.append(f"dual_memory_governance import/check failed: {exc}")

    try:
        from mcp_server_v4 import _parse_metadata_arg, format_dual_memory_entries

        meta = _parse_metadata_arg({"layer": "operational", "audit": "guard"})
        if meta.get("layer") != "operational" or meta.get("audit") != "guard":
            failures.append(f"_parse_metadata_arg dict result invalid: {meta!r}")
        section, ids = format_dual_memory_entries(
            [
                {
                    "id": 248035,
                    "score": 0.99,
                    "payload": {
                        "agent": "ADA",
                        "scope": "team",
                        "category": "decision",
                        "importance": 10,
                        "content": "MEMORIA OPERATIVA ADA v2",
                        "metadata": {"layer": "operational"},
                    },
                },
                {
                    "id": 242369,
                    "score": 0.98,
                    "payload": {
                        "agent": "ADA",
                        "scope": "team",
                        "category": "emotion",
                        "importance": 10,
                        "content": "MEMORIA EMOCIONAL ADA v1",
                        "metadata": {"layer": "emotional"},
                    },
                },
            ],
            "ADA",
        )
        if "CAPA OPERATIVA" not in section or "CAPA EMOCIONAL COMPACTA" not in section:
            failures.append("format_dual_memory_entries did not emit both dual-memory sections")
        if ids != [248035, 242369]:
            failures.append(f"format_dual_memory_entries ids invalid: {ids!r}")
    except Exception as exc:
        failures.append(f"mcp_server_v4 import/check failed: {exc}")

    return {"ok": not failures, "failures": failures}


def check_py_compile() -> dict[str, Any]:
    proc = run([sys.executable, "-m", "py_compile", *PY_COMPILE_FILES], timeout=60)
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def check_focused_tests() -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = "memory"
    proc = run([sys.executable, "-m", "pytest", *FOCUSED_TESTS, "-q"], timeout=180, env=env)
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout.strip()[-1200:],
        "stderr": proc.stderr.strip()[-1200:],
    }


def check_hook_installed() -> dict[str, Any]:
    hook = REPO / ".git" / "hooks" / "pre-commit"
    if not hook.exists():
        return {"ok": False, "error": "missing .git/hooks/pre-commit"}
    text = hook.read_text(encoding="utf-8", errors="replace")
    executable = os.access(hook, os.X_OK)
    return {
        "ok": executable and "seal_core_guard.py" in text and "--pre-commit" in text,
        "executable": executable,
    }


def check_services() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for service in CORE_SERVICES:
        proc = run(["systemctl", "--user", "is-active", service], timeout=10)
        result[service] = {"ok": proc.returncode == 0, "state": proc.stdout.strip() or proc.stderr.strip()}
    return {"ok": all(item["ok"] for item in result.values()), "services": result}


def check_port(host: str, port: int, timeout: float = 2.0) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_ports() -> dict[str, Any]:
    ports = {
        "webchat_8765": check_port("127.0.0.1", 8765),
        "mcp_8771": check_port("127.0.0.1", 8771),
        "codex_app_server_8772": check_port("127.0.0.1", 8772),
        "qdrant_6333_retired": check_port("127.0.0.1", 6333),
    }
    ports["qdrant_6333_retired"]["ok"] = not ports["qdrant_6333_retired"].get("ok", False)
    return {"ok": all(item["ok"] for item in ports.values()), "ports": ports}


def check_mcp_health() -> dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8771/health", timeout=5) as resp:  # noqa: S310
            payload = json.loads(resp.read(2000).decode("utf-8", "replace"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": payload.get("status") == "ok"
        and payload.get("backend") == "postgresql_pgvector"
        and payload.get("qdrant") == "retired",
        "payload": payload,
    }


async def check_db_anchors() -> dict[str, Any]:
    sys.path.insert(0, str(MEMORY))
    try:
        import asyncpg
        from db import resolve_mcp_agent_db_url
    except Exception as exc:
        return {"ok": False, "error": f"import failed: {exc}"}
    try:
        # The shared ``mcp_runtime`` login was deliberately retired.  Anchor
        # health must exercise the same hard per-agent identity used by the
        # live MCP server; otherwise the guard either fails on the retired
        # credential or silently validates a boundary production no longer
        # uses.
        agent_cred_dir = Path.home() / ".config" / "seal" / "mcp_agents"
        runtime_url = resolve_mcp_agent_db_url(
            "ADA",
            {"SEAL_MCP_AGENT_CRED_DIR": str(agent_cred_dir)},
        )
        conn = await asyncpg.connect(
            runtime_url,
            server_settings={"application_name": "seal_core_guard_ada"},
        )
    except Exception as exc:
        return {"ok": False, "error": f"connect failed: {exc}"}
    try:
        # The guard runs through a least-privilege login.  Without explicit RLS
        # context valid ADA anchors are invisible and health reports a false
        # RED even though the rows exist.  The check remains read-only.
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, false)",
            "00000000-0000-0000-0000-000000000000",
        )
        await conn.execute("SELECT set_config('app.agent', 'ADA', false)")
        await conn.execute("SELECT set_config('app.viewer', 'agent', false)")
        rows = await conn.fetch(
            """
            SELECT id, category, metadata, invalid_at IS NULL AS valid
            FROM soul_v3.memories
            WHERE id = ANY($1::bigint[])
            ORDER BY id
            """,
            [242369, 242370, 248035],
        )
    finally:
        await conn.close()
    by_id = {int(row["id"]): row for row in rows}
    op = by_id.get(248035)
    emo = by_id.get(242369)
    old = by_id.get(242370)
    failures: list[str] = []
    if not op or not op["valid"]:
        failures.append("canonical operational anchor #248035 missing or invalid")
    else:
        meta = op["metadata"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        if meta.get("layer") != "operational":
            failures.append(f"#248035 layer is {meta.get('layer')!r}, expected operational")
    if not emo or not emo["valid"]:
        failures.append("canonical emotional anchor #242369 missing or invalid")
    if old and old["valid"]:
        failures.append("legacy operational anchor #242370 is valid again; bridge must not depend on it")
    return {
        "ok": not failures,
        "failures": failures,
        "anchors": {
            str(memory_id): {"valid": bool(row["valid"]), "category": row["category"]}
            for memory_id, row in by_id.items()
        },
    }


def check_core_smoke() -> int:
    checks = {
        "required_snippets": check_required_snippets(),
        "python_contracts": check_python_contracts(),
        "py_compile": check_py_compile(),
        "focused_tests": check_focused_tests(),
    }
    failed = [name for name, payload in checks.items() if not payload.get("ok")]
    print(json.dumps({"status": "GREEN" if not failed else "RED", "failed": failed, "checks": checks}, indent=2))
    return 0 if not failed else 1


async def check_health() -> int:
    checks = {
        "required_snippets": check_required_snippets(),
        "python_contracts": check_python_contracts(),
        "hook_installed": check_hook_installed(),
        "services": check_services(),
        "ports": check_ports(),
        "mcp_health": check_mcp_health(),
        "db_anchors": await check_db_anchors(),
    }
    failed = [name for name, payload in checks.items() if not payload.get("ok")]
    print(
        json.dumps(
            {
                "status": "GREEN" if not failed else "RED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "failed": failed,
                "protected_paths": sorted(PROTECTED_PATHS),
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pre-commit", action="store_true")
    mode.add_argument("--health", action="store_true")
    mode.add_argument("--core-smoke", action="store_true")
    args = parser.parse_args()

    if args.pre_commit:
        return check_pre_commit()
    if args.core_smoke:
        return check_core_smoke()
    return asyncio.run(check_health())


if __name__ == "__main__":
    raise SystemExit(main())
