"""seal/doctor.py — Self-diagnostic CLI for SEAL.

Run via: seal doctor

Checks every critical component and prints a table:
    OK    — reachable / present
    WARN  — partial or degraded
    FAIL  — unreachable / missing

Stdlib only — no external deps.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Literal, Optional

Status = Literal["OK", "WARN", "FAIL"]

# ── Credentials that SEAL requires in the environment ─────────────────────────

_REQUIRED_CREDS = [
    ("ANTHROPIC_API_KEY",  "Anthropic API"),
]

_OPTIONAL_CREDS = [
    ("OPENAI_API_KEY",     "OpenAI"),
    ("OPENROUTER_API_KEY", "OpenRouter"),
    ("GEMINI_API_KEY",     "Gemini"),
    ("GROQ_API_KEY",       "Groq"),
    ("XAI_API_KEY",        "xAI"),
]


# ── Individual checks ──────────────────────────────────────────────────────────


def _tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    """Returns (status_code, body_snippet). Raises on network error."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(512).decode("utf-8", errors="replace")
        return resp.status, body


def check_postgres() -> tuple[Status, str]:
    host = os.environ.get("SEAL_PG_HOST", "localhost")
    port = int(os.environ.get("SEAL_PG_PORT", "5433"))
    if not _tcp(host, port):
        return "FAIL", f"TCP refused at {host}:{port}"
    try:
        import psycopg2  # type: ignore[import]
        user = os.environ.get("SEAL_PG_USER", "seal")
        pw   = os.environ.get("SEAL_PG_PASSWORD", "seal_memory_2026")
        db   = os.environ.get("SEAL_PG_DATABASE", "seal_memory")
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=pw,
            dbname=db, connect_timeout=3,
        )
        conn.close()
        return "OK", f"{host}:{port}/{db}"
    except ImportError:
        return "WARN", f"TCP ok but psycopg2 not installed — cannot verify schema"
    except Exception as exc:
        return "FAIL", str(exc)[:80]


def check_qdrant() -> tuple[Status, str]:
    host = os.environ.get("QDRANT_HOST", "localhost")
    port = int(os.environ.get("QDRANT_PORT", "6333"))
    url  = f"http://{host}:{port}/health"
    try:
        code, body = _http_get(url)
        if code == 200:
            return "OK", f"{host}:{port} — {body[:40].strip()}"
        return "WARN", f"HTTP {code}"
    except Exception as exc:
        return "FAIL", str(exc)[:80]


def check_neo4j() -> tuple[Status, str]:
    host = os.environ.get("NEO4J_HOST", "localhost")
    port = int(os.environ.get("NEO4J_PORT", "7687"))
    if _tcp(host, port):
        return "OK", f"Bolt reachable at {host}:{port}"
    http_port = 7474
    if _tcp(host, http_port):
        return "WARN", f"Bolt {port} refused but HTTP {http_port} open"
    return "FAIL", f"TCP refused at {host}:{port}"


def check_gateway() -> tuple[Status, str]:
    url = os.environ.get("SEAL_GATEWAY_URL", "http://localhost:8765/health")
    try:
        code, body = _http_get(url)
        if code == 200:
            return "OK", f"{url} — {body[:40].strip()}"
        return "WARN", f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 405):
            return "WARN", f"HTTP {exc.code} (gateway up, no /health endpoint)"
        return "FAIL", f"HTTP {exc.code}"
    except Exception as exc:
        return "FAIL", str(exc)[:80]


def check_mcp_sse() -> tuple[Status, str]:
    url = os.environ.get("SEAL_MCP_URL", "http://localhost:8766/health")
    try:
        code, body = _http_get(url)
        if code == 200:
            return "OK", f"SSE server responding"
        return "WARN", f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 405):
            return "WARN", f"HTTP {exc.code} (MCP up, no /health route)"
        return "FAIL", f"HTTP {exc.code}"
    except Exception as exc:
        return "FAIL", str(exc)[:80]


def check_tailscale() -> tuple[Status, str]:
    ts = shutil.which("tailscale")
    if not ts:
        # fall back: look for the interface
        try:
            result = subprocess.run(
                ["ip", "-j", "addr"], capture_output=True, text=True, timeout=3
            )
            if "tailscale" in result.stdout:
                return "WARN", "interface present but tailscale CLI not found"
        except Exception:
            pass
        return "WARN", "tailscale CLI not found"
    try:
        result = subprocess.run(
            [ts, "status", "--json"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return "FAIL", result.stderr.strip()[:80] or "non-zero exit"
        data = json.loads(result.stdout)
        backend = data.get("BackendState", "Unknown")
        if backend == "Running":
            self_ip = data.get("TailscaleIPs", ["?"])[0]
            return "OK", f"connected — {self_ip}"
        return "WARN", f"BackendState={backend}"
    except json.JSONDecodeError:
        return "WARN", "CLI found but JSON parse failed"
    except Exception as exc:
        return "FAIL", str(exc)[:80]


def check_claude_cli() -> tuple[Status, str]:
    claude = shutil.which("claude")
    if claude:
        return "OK", claude
    # Check common alternative locations
    candidates = [
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        os.path.expanduser("~/.claude/local/claude"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return "WARN", f"found at {c} but not in $PATH"
    return "FAIL", "claude binary not found"


def check_credentials() -> tuple[Status, str]:
    missing_required = [label for var, label in _REQUIRED_CREDS if not os.environ.get(var)]
    present_optional = [label for var, label in _OPTIONAL_CREDS if os.environ.get(var)]

    if missing_required:
        return "FAIL", f"missing required: {', '.join(missing_required)}"
    if present_optional:
        return "OK", f"required present; optional: {', '.join(present_optional)}"
    return "WARN", "required present; no optional keys set"


# ── Table renderer ─────────────────────────────────────────────────────────────

_STATUS_COLORS = {
    "OK":   "\033[32m",   # green
    "WARN": "\033[33m",   # yellow
    "FAIL": "\033[31m",   # red
}
_RESET = "\033[0m"


def _colored(status: str, no_color: bool = False) -> str:
    if no_color:
        return status
    color = _STATUS_COLORS.get(status, "")
    return f"{color}{status}{_RESET}"


def _run_all(no_color: bool = False) -> list[tuple[str, Status, str]]:
    checks = [
        ("PostgreSQL SOUL",  check_postgres),
        ("Qdrant",           check_qdrant),
        ("Neo4j",            check_neo4j),
        ("Gateway :8765",    check_gateway),
        ("MCP SSE :8766",    check_mcp_sse),
        ("Tailscale",        check_tailscale),
        ("claude CLI",       check_claude_cli),
        ("Credentials",      check_credentials),
    ]
    results = []
    for name, fn in checks:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "FAIL", f"unexpected: {exc}"
        results.append((name, status, detail))
    return results


def print_table(results: list[tuple[str, Status, str]], no_color: bool = False) -> None:
    col1 = max(len(r[0]) for r in results) + 2
    col2 = 6
    col3 = 72 - col1 - col2

    header = f"{'COMPONENT':<{col1}} {'STATUS':<{col2}} DETAIL"
    print(header)
    print("─" * (col1 + col2 + col3))
    for name, status, detail in results:
        colored_status = _colored(status, no_color)
        pad = 6 + (len(colored_status) - len(status))  # account for ANSI
        detail_trimmed = detail[:col3]
        print(f"{name:<{col1}} {colored_status:<{pad}} {detail_trimmed}")
    print("─" * (col1 + col2 + col3))


def run_doctor(no_color: bool = False, json_out: bool = False) -> int:
    results = _run_all(no_color=no_color)

    if json_out:
        output = [{"component": n, "status": s, "detail": d} for n, s, d in results]
        print(json.dumps(output, indent=2))
    else:
        print()
        print("  SEAL System Diagnostic")
        print()
        print_table(results, no_color=no_color)
        print()

    fail_count = sum(1 for _, s, _ in results if s == "FAIL")
    warn_count = sum(1 for _, s, _ in results if s == "WARN")

    if not json_out:
        if fail_count:
            print(f"  {_colored('FAIL', no_color)}: {fail_count} component(s) need attention")
        elif warn_count:
            print(f"  {_colored('WARN', no_color)}: {warn_count} warning(s) — system usable")
        else:
            print(f"  {_colored('OK', no_color)}: all systems nominal")
        print()

    return 1 if fail_count else 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(prog="seal doctor")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--json", action="store_true", dest="json_out")
    args = p.parse_args()
    sys.exit(run_doctor(no_color=args.no_color, json_out=args.json_out))
