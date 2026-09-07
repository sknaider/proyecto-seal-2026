#!/usr/bin/env python3
"""Kill focused mutants for the post-reset MCP surface recovery contract."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "memory/mcp_server_v4.py"
CRON = ROOT / "memory/instinct_cron.py"
TEST = ROOT / "memory/tests/test_mcp_surface_recovery_20260903.py"


MUTANTS = {
    "drop-agent-task-update": (
        "server",
        '        elif action == "update":\n            row = await _append_agent_task_description(',
        '        elif action == "update_REMOVED":\n            row = await _append_agent_task_description(',
    ),
    "query-prefix-regression": ("server", "await get_query_embedding(query)", "await get_embedding(query)"),
    "semantic-band-regression": ("server", "_E5_COS_LO, _E5_COS_HI = 0.70, 0.90", "_E5_COS_LO, _E5_COS_HI = 0.0, 1.0"),
    "identity-fail-open": ("server", 'SEAL_IDENTITY_MODE", "ENFORCE"', 'SEAL_IDENTITY_MODE", "OFF"'),
    "terminal-error-as-success": ("server", ' or result.get("error")', " or False"),
    "drop-ocean-boundary": ("server", "SELECT soul_v3.ocean_signal_apply(", "SELECT soul_v3.ocean_signal_apply_REMOVED("),
    "drop-relationship-boundary": ("server", "SELECT soul_v3.relationship_signal_apply(", "SELECT soul_v3.relationship_signal_apply_REMOVED("),
    "feedback-non-atomic": (
        "server",
        "    # The quality update and its feedback ledger entry are one fact.\n    async with pool.acquire() as conn:\n        async with conn.transaction():",
        "    # The quality update and its feedback ledger entry are one fact.\n    async with pool.acquire() as conn:\n        if True:",
    ),
    "instinct-cross-owner": ("server", "WHERE id = $1 AND agent = $2 AND invalid_at IS NULL", "WHERE id = $1 AND invalid_at IS NULL"),
    "send-without-session-key": (
        "server",
        '        "session_key": chat_token,\n    }\n    try:',
        '        "session_key_REMOVED": chat_token,\n    }\n    try:',
    ),
    "cron-full-age-reapplication": ("cron", "return max(\n        value", "return min(\n        value"),
}


def sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    original_server = SERVER.read_text(encoding="utf-8")
    original_cron = CRON.read_text(encoding="utf-8")
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="seal-mcp-surface-mutants-") as temporary:
        temp = Path(temporary)
        for name, (subject, needle, replacement) in MUTANTS.items():
            server_text = original_server
            cron_text = original_cron
            target = server_text if subject == "server" else cron_text
            if needle not in target:
                results.append({"name": name, "status": "suspicious", "reason": "needle_missing"})
                continue
            if subject == "server":
                server_text = server_text.replace(needle, replacement, 1)
            else:
                cron_text = cron_text.replace(needle, replacement, 1)
            server_path = temp / f"{name}-mcp_server_v4.py"
            cron_path = temp / f"{name}-instinct_cron.py"
            server_path.write_text(server_text, encoding="utf-8")
            cron_path.write_text(cron_text, encoding="utf-8")
            env = dict(os.environ)
            env["SEAL_MCP_RECOVERY_SERVER"] = str(server_path)
            env["SEAL_MCP_RECOVERY_CRON"] = str(cron_path)
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--noconftest", str(TEST)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=45,
                check=False,
            )
            status = "killed" if proc.returncode == 1 else "survived"
            if proc.returncode not in (0, 1):
                status = "suspicious"
            results.append({
                "name": name,
                "status": status,
                "returncode": proc.returncode,
                "tail": (proc.stdout + proc.stderr)[-500:],
            })

    counts = {key: sum(row["status"] == key for row in results) for key in ("killed", "survived", "suspicious")}
    payload = {
        "total": len(results),
        **counts,
        "results": results,
        "file_sha256": {
            "memory/mcp_server_v4.py": sha256(SERVER),
            "memory/instinct_cron.py": sha256(CRON),
            "memory/tests/test_active_recall_hook.py": sha256(ROOT / "memory/tests/test_active_recall_hook.py"),
            "memory/tests/test_mcp_surface_recovery_20260903.py": sha256(TEST),
            "memory/tests/check_mcp_surface_recovery_mutants.py": sha256(Path(__file__)),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if counts["killed"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
