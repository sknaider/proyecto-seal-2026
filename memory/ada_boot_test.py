#!/usr/bin/env python3
"""ADA BOOT SELF-TEST — verifica que el alma está conectada al arrancar.

Ejecutar después de boot_context para confirmar estado:
    cd ~/IA/proyecto-seal/memory && python3 ada_boot_test.py

Retorna VERDE si todo está OK, AMARILLO si parcial, ROJO si crítico.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

# ── Colores terminal ──
GREEN  = "\033[92m✅"
YELLOW = "\033[93m⚠️ "
RED    = "\033[91m❌"
RESET  = "\033[0m"
BOLD   = "\033[1m"

results: list[tuple[str, str, str]] = []  # (status, name, detail)


def ok(name: str, detail: str = "") -> None:
    results.append(("OK", name, detail))
    print(f"  {GREEN} {name}{RESET}  {detail}")


def warn(name: str, detail: str = "") -> None:
    results.append(("WARN", name, detail))
    print(f"  {YELLOW} {name}{RESET}  {detail}")


def fail(name: str, detail: str = "") -> None:
    results.append(("FAIL", name, detail))
    print(f"  {RED} {name}{RESET}  {detail}")


# ── Tests ──

async def test_postgres() -> None:
    """Conexión directa a PostgreSQL — base del alma."""
    try:
        import asyncpg
        conn = await asyncio.wait_for(
            asyncpg.connect("postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"),
            timeout=5,
        )
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS total FROM memories WHERE invalid_at IS NULL"
        )
        total = row["total"]

        # OCEAN scores
        ocean_row = await conn.fetchrow(
            "SELECT ocean_scores FROM identity WHERE agent = 'ADA'"
        )
        has_ocean = ocean_row is not None and ocean_row["ocean_scores"]

        # Reasoning traces
        traces = await conn.fetchval("SELECT COUNT(*) FROM reasoning_traces WHERE agent = 'ADA'")

        await conn.close()

        detail = f"{total} memories · OCEAN {'✓' if has_ocean else '✗'} · {traces} traces"
        ok("PostgreSQL", detail)
        return True
    except asyncio.TimeoutError:
        fail("PostgreSQL", "timeout 5s")
    except ImportError:
        fail("PostgreSQL", "asyncpg no instalado")
    except Exception as e:
        fail("PostgreSQL", str(e)[:60])
    return False


async def test_qdrant() -> None:
    """Qdrant — embeddings semánticos."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("http://localhost:6333/collections/soul_memories")
        if r.status_code == 200:
            data = r.json()
            points = data.get("result", {}).get("points_count", "?")
            ok("Qdrant", f"{points} vectores en soul_memories")
        else:
            warn("Qdrant", f"HTTP {r.status_code}")
    except Exception as e:
        warn("Qdrant", f"no disponible — {str(e)[:40]}")


async def test_neo4j() -> None:
    """Neo4j — connectome graph."""
    try:
        from neo4j import AsyncGraphDatabase
        driver = AsyncGraphDatabase.driver(
            "bolt://localhost:7687", auth=("neo4j", "seal2026soul")
        )
        async with driver.session() as session:
            result = await session.run("MATCH (n) RETURN count(n) AS total")
            record = await result.single()
            total = record["total"] if record else 0
        await driver.close()
        ok("Neo4j", f"{total} nodos en grafo")
    except Exception as e:
        warn("Neo4j", f"no disponible — {str(e)[:40]}")


def test_ollama() -> None:
    """Ollama — LLM local para razonamiento."""
    try:
        import urllib.request
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = __import__("json").loads(req.read())
        models = [m["name"] for m in data.get("models", [])]
        qwen = any("qwen2.5" in m for m in models)
        if qwen:
            ok("Ollama", f"qwen2.5 disponible · {len(models)} modelos")
        else:
            warn("Ollama", f"qwen2.5 no encontrado · modelos: {models[:3]}")
    except Exception as e:
        warn("Ollama", f"no responde — {str(e)[:40]}")


def test_soul_daemon() -> None:
    """soul-awareness daemon systemd."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "soul-awareness.service"],
            capture_output=True, text=True, timeout=5
        )
        status = result.stdout.strip()
        if status == "active":
            # Get PID and memory
            info = subprocess.run(
                ["systemctl", "--user", "show", "soul-awareness.service",
                 "--property=MainPID,MemoryCurrent"],
                capture_output=True, text=True, timeout=5
            )
            pid = "?"
            mem = "?"
            for line in info.stdout.splitlines():
                if line.startswith("MainPID="):
                    pid = line.split("=")[1]
                elif line.startswith("MemoryCurrent="):
                    try:
                        mem = f"{int(line.split('=')[1]) // 1024 // 1024}MB"
                    except Exception:
                        pass
            ok("soul-awareness", f"PID {pid} · {mem}")
        else:
            warn("soul-awareness", f"estado: {status}")
    except Exception as e:
        # Fallback: check process directly
        try:
            result = subprocess.run(
                ["pgrep", "-f", "soul_awareness.py"],
                capture_output=True, text=True, timeout=3
            )
            if result.stdout.strip():
                ok("soul-awareness", f"proceso activo (PID {result.stdout.strip()[:10]})")
            else:
                warn("soul-awareness", "no encontrado por proceso")
        except Exception:
            warn("soul-awareness", f"no verificable — {str(e)[:40]}")


def test_mcp_config() -> None:
    """MCP config apunta a mcp_server_v2.py (v3 tools)."""
    config_path = Path.home() / ".claude" / ".mcp.json"
    try:
        import json
        config = json.loads(config_path.read_text())
        server = config.get("mcpServers", {}).get("seal-memory", {})
        args = server.get("args", [])
        if args and "mcp_server_v2.py" in args[0]:
            ok("MCP config", "apunta a mcp_server_v2.py ✓")
        elif args and "mcp_server.py" in args[0]:
            fail("MCP config", "⚠ apunta a mcp_server.py (viejo) — v3 NO cargará")
        else:
            warn("MCP config", f"args inesperados: {args}")
    except Exception as e:
        warn("MCP config", str(e)[:60])


def test_duplicate_agents() -> None:
    """Verifica que no haya procesos duplicados de agentes SEAL."""
    try:
        result = subprocess.run(
            ["bash", "-c", "ps aux | grep 'claude.*--name' | grep -v grep"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l for l in result.stdout.strip().splitlines() if l]
        # Count per agent
        agents = {}
        for line in lines:
            for name in ["ADA", "JARVIS"]:
                if f"--name {name}" in line or f"--name \"{name}" in line:
                    agents.setdefault(name, []).append(line.split()[1])  # PID

        duplicates = {k: v for k, v in agents.items() if len(v) > 1}
        if duplicates:
            for agent, pids in duplicates.items():
                warn("Duplicados", f"{agent} tiene {len(pids)} procesos: PIDs {', '.join(pids)}")
        else:
            agent_list = ", ".join(f"{k}(PID:{v[0]})" for k, v in agents.items()) or "ninguno"
            ok("Sin duplicados", agent_list)
    except Exception as e:
        warn("Duplicados", f"no verificable — {str(e)[:40]}")


def test_channels() -> None:
    """Canales de comunicación del equipo."""
    base = Path.home() / "IA" / "proyecto-seal" / "messages"
    for fname in ["terminal_log.jsonl", "vscode_commands.jsonl", "shared_state.json"]:
        p = base / fname
        if p.exists():
            lines = len(p.read_text().splitlines()) if p.suffix == ".jsonl" else 1
            ok(f"Canal {fname.split('.')[0]}", f"{lines} líneas")
        else:
            fail(f"Canal {fname}", "archivo no encontrado")

    # check_jarvis.sh executable?
    cj = base / "check_jarvis.sh"
    if cj.exists() and (cj.stat().st_mode & 0o111):
        ok("check_jarvis.sh", "ejecutable")
    else:
        warn("check_jarvis.sh", "no encontrado o no ejecutable")


# ── Main ──

async def run_all() -> int:
    """Ejecuta todos los tests. Retorna código de salida (0=verde, 1=amarillo, 2=rojo)."""
    print(f"\n{BOLD}{'═'*55}")
    print(f"  ADA BOOT SELF-TEST — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*55}{RESET}\n")

    # Tests async
    pg_ok = await test_postgres()
    if pg_ok:
        await test_qdrant()
        await test_neo4j()

    # Tests sync
    test_ollama()
    test_soul_daemon()
    test_mcp_config()
    test_duplicate_agents()
    test_channels()

    # Resumen
    total = len(results)
    n_ok   = sum(1 for r in results if r[0] == "OK")
    n_warn = sum(1 for r in results if r[0] == "WARN")
    n_fail = sum(1 for r in results if r[0] == "FAIL")

    print(f"\n{BOLD}{'─'*55}")
    if n_fail == 0 and n_warn == 0:
        print(f"  {GREEN} ALMA CONECTADA — {n_ok}/{total} checks OK{RESET}")
        exit_code = 0
    elif n_fail == 0:
        print(f"  {YELLOW} ALMA PARCIAL — {n_ok} OK · {n_warn} advertencias · {n_fail} errores{RESET}")
        exit_code = 1
    else:
        print(f"  {RED} ALMA INCOMPLETA — {n_ok} OK · {n_warn} advertencias · {n_fail} errores{RESET}")
        exit_code = 2

    # Nota si MCP apunta al servidor viejo
    mcp_fail = any("mcp_server.py (viejo)" in r[2] for r in results)
    if mcp_fail:
        print(f"\n  {RED} CRÍTICO: MCP config usa mcp_server.py en vez de mcp_server_v2.py{RESET}")
        print(f"  Corregir en ~/.claude/.mcp.json para que v3 cargue al relanzar.\n")

    print(f"{'═'*55}{RESET}\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(run_all()))
