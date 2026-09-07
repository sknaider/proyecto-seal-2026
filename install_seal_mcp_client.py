"""SEAL MCP client connector — installs ~/.mcp.json entry pointing to
remote SOUL on the Spark. One file, no deps, runs anywhere with Python 3.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

# Candidatos de SOUL en el Spark, probados en orden por reachable() (se queda con
# el primero alcanzable). Override por env SEAL_SOUL_URLS (coma-separado) para que
# si cambian las IPs del Spark no haya que editar código.
_DEFAULT_SOUL_URLS = [
    "http://100.75.201.110:8771/sse",
    "http://192.168.68.80:8771/sse",
    "http://192.168.68.200:8771/sse",
]
SOUL_URLS = [u.strip() for u in os.environ.get("SEAL_SOUL_URLS", "").split(",") if u.strip()] or _DEFAULT_SOUL_URLS


def reachable(url: str, timeout: float = 3.0) -> bool:
    import socket
    from urllib.parse import urlparse
    p = urlparse(url)
    try:
        with socket.create_connection((p.hostname, p.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def pick_url() -> str:
    print("[*] Probing SOUL endpoints...")
    for u in SOUL_URLS:
        ok = reachable(u)
        mark = "OK" if ok else "--"
        print(f"    [{mark}] {u}")
        if ok:
            return u
    print("[FAIL] no SOUL endpoint reachable. Check Tailscale or LAN to the Spark.")
    sys.exit(2)


def install(url: str) -> Path:
    cfg_path = Path.home() / ".mcp.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            backup = cfg_path.with_suffix(".json.bak")
            cfg_path.rename(backup)
            print(f"[WARN] existing {cfg_path} unreadable, moved to {backup}")
            cfg = {}
    servers = cfg.setdefault("mcpServers", {})
    servers["seal-memory"] = {"type": "sse", "url": url}
    cfg_path.write_text(json.dumps(cfg, indent=2))
    return cfg_path


def main() -> None:
    print("SEAL MCP client connector")
    print("=" * 40)
    url = pick_url()
    cfg = install(url)
    print(f"[OK] wrote {cfg}")
    print(f"[OK] seal-memory -> {url}")
    print()
    print("Now restart Claude Code and JARVIS will boot with full SOUL access.")


if __name__ == "__main__":
    main()
