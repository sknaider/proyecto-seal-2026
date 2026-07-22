#!/usr/bin/env python3
"""SOUL Dependency Inventory — catálogo único durable, verificado POR EFECTO.

Owner: JARVIS. Sustrato del inventario fusionado (21-jul-2026, "ordenar
quirúrgicamente" de William). Endurecido iterando con la verificación adversarial
de ADA (builder≠verifier sobre mí) — cada punto de abajo fue un catch suyo.

QUÉ VERIFICA, y por qué así (todos cazados por ADA):
1. MCP: **handshake JSON-RPC `initialize` real** (el MCP vive y responde con
   serverInfo), NO "el binario existe" ni "py_compile" — esos daban verde sobre
   binarios npm retirados o sobre código que compila pero no arranca.
2. MCP derivados de **`.mcp.json`** (fuente de verdad) — nunca driftea a un nombre
   adivinado/retirado.
3. `reboot_safe` **MEDIDO** por el supervisor REAL de cada componente
   (docker restart-policy / systemd is-enabled), no declarado.
4. `identity` real por fila: el :8766 es el contenedor SOUL API v1 legacy, NO
   seal-smg (gateway retirado en 8770). Un `retired` DOWN es SANO.
5. `--diff` **fail-CLOSED**: una dependencia del baseline que DESAPARECE del
   catálogo se marca como regresión (un rename/removal ya no pasa como verde).
6. Baseline **git-tracked** en `tools/dependency_baseline.json` (durable), no en
   logs/ untracked.

Columnas: name · type · lifecycle · identity · replaced_by · probe(efecto)
          · reboot_safe(medido) · healthy(coherencia lifecycle vs estado real)

Uso:
  python3 tools/dependency_inventory.py                # verifica por efecto
  python3 tools/dependency_inventory.py --json
  python3 tools/dependency_inventory.py --diff [BASELINE]   # gate de regresión
  python3 tools/dependency_inventory.py --save-baseline     # regenera baseline durable
"""
from __future__ import annotations

import argparse
import json
import pathlib
import select
import socket
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "dependency_baseline.json"

# ── Motores / servicios / legacy. supervisor = cómo se MIDE reboot_safe. ─────
# (name, type, lifecycle, replaced_by, (probe_kind, arg), (sup_kind, sup_arg))
#   sup_kind: docker=restart-policy · unit-user/unit-sys=is-enabled · none
STATIC = [
    ("postgres-engine (pgvector)", "engine", "active", "",
     ("pgvector", None), ("docker", "seal-memory-db")),
    ("neo4j-engine",  "engine",  "active", "", ("port", 7687),  ("docker", "soul-neo4j")),
    ("ollama-engine", "engine",  "active", "", ("port", 11434), ("unit-sys", "ollama")),
    ("prometheus-engine (9090)", "engine", "active", "", ("port", 9090), ("docker", "seal-prometheus")),
    ("chat-server (8765)", "service", "active", "", ("port", 8765), ("unit-user", "seal-chat.service")),
    ("orion-exam",              "service", "active", "", ("unit", "orion-exam.service"), ("unit-user", "orion-exam.service")),
    ("jarvis-awareness",        "service", "active", "", ("unit", "jarvis-awareness.service"), ("unit-user", "jarvis-awareness.service")),
    ("ada-codex-remote-bridge", "service", "active", "", ("unit", "ada-codex-remote-bridge.service"), ("unit-user", "ada-codex-remote-bridge.service")),
    # legacy VIVO: SOUL API v1 en contenedor docker (NO es seal-smg)
    ("soul-api-v1-legacy (8766)", "gateway", "legacy",
     "parcial: 8771 seal-memory + 8768 memoria tenant-safe (sin equivalencia total)",
     ("docker-run", "soul-api-server-legacy-8766"), ("docker", "soul-api-server-legacy-8766")),
    # RETIRADO: gateway SMG (8770/9091). DOWN es su estado CORRECTO.
    ("seal-smg (gw 8770)", "gateway", "retired", "8771 seal-memory MCP",
     ("unit", "seal-smg.service"), ("none", None)),
]

_INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "inv-probe", "version": "0"}}}) + "\n"


def _mcp_handshake(command, args, timeout=12):
    """Lanza el MCP con su comando real y verifica que responde a `initialize`.
    Prueba MCP-VIVO, no 'el archivo existe'. Devuelve (up, detail)."""
    argv = ([command] if command else []) + list(args)
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
    except Exception as e:
        return False, f"no lanza: {str(e)[:40]}"
    try:
        p.stdin.write(_INIT)
        p.stdin.flush()
        deadline_buf = ""
        import time as _t  # solo para el select loop; no se persiste
        end = _t.monotonic() + timeout
        while _t.monotonic() < end:
            r, _, _ = select.select([p.stdout], [], [], end - _t.monotonic())
            if not r:
                break
            line = p.stdout.readline()
            if not line:
                break
            deadline_buf += line
            try:
                o = json.loads(line)
            except Exception:
                continue
            si = (o.get("result") or {}).get("serverInfo")
            if si:
                return True, f"mcp vivo: {si.get('name', '?')} v{si.get('version', '?')}"
        return False, "sin respuesta initialize"
    finally:
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def _probe(kind, arg):
    try:
        if kind == "port":
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            ok = s.connect_ex(("127.0.0.1", arg)) == 0
            s.close()
            return ok, f"tcp :{arg} {'up' if ok else 'down'}"
        if kind == "http":
            try:
                return True, f"http {urllib.request.urlopen(arg, timeout=3).getcode()}"
            except urllib.error.HTTPError as e:
                return e.code < 500, f"http {e.code}"
        if kind == "unit":
            r = subprocess.run(["systemctl", "--user", "is-active", arg],
                               capture_output=True, text=True, timeout=5)
            st = r.stdout.strip()
            return st == "active", f"unit {st}"
        if kind == "docker-run":
            r = subprocess.run(["docker", "inspect", arg, "--format", "{{.State.Running}}"],
                               capture_output=True, text=True, timeout=5)
            run = r.stdout.strip() == "true"
            return run, f"docker {'running' if run else 'down/ausente'}"
        if kind == "pgvector":
            # sonda funcional del motor: pgvector responde su versión por SQL real.
            # DSN NUNCA hardcodeada: de env (SEAL_PG_DSN) o del env-file del observer
            # restringido (mismo que usa el MCP postgres). Sin secreto en el fuente.
            import asyncio
            import asyncpg
            import os

            dsn = os.environ.get("SEAL_PG_DSN")
            if not dsn:
                envf = pathlib.Path.home() / ".config/seal/mcp_postgres_observer.env"
                if envf.exists():
                    for ln in envf.read_text().splitlines():
                        ln = ln.strip()
                        if ln.startswith(("POSTGRES_MCP_DSN=", "SEAL_PG_DSN=", "DATABASE_URL=", "PG_DSN=")):
                            dsn = ln.split("=", 1)[1].strip().strip('"').strip("'")
                            break
            if not dsn:
                return False, "pgvector: sin DSN en env (SEAL_PG_DSN/observer.env)"

            async def _q():
                c = await asyncpg.connect(dsn)
                try:
                    return await c.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")
                finally:
                    await c.close()
            v = asyncio.run(_q())
            return bool(v), f"postgres+pgvector {v}" if v else "pgvector AUSENTE"
    except Exception as e:
        return False, f"error: {str(e)[:40]}"
    return False, "probe desconocido"


def _reboot_safe(sup_kind, sup_arg):
    """MIDE reboot_safe por el supervisor real. Nunca declara."""
    try:
        if sup_kind == "docker":
            r = subprocess.run(["docker", "inspect", sup_arg, "--format",
                                "{{.HostConfig.RestartPolicy.Name}}"],
                               capture_output=True, text=True, timeout=5)
            pol = r.stdout.strip()
            safe = pol in ("unless-stopped", "always", "on-failure")
            return (safe if pol else "?"), f"docker[{sup_arg}] restart={pol or 'n/a'}"
        if sup_kind in ("unit-user", "unit-sys"):
            base = ["systemctl"] + (["--user"] if sup_kind == "unit-user" else [])
            r = subprocess.run(base + ["is-enabled", sup_arg],
                               capture_output=True, text=True, timeout=5)
            en = r.stdout.strip()
            return (True if en == "enabled" else (False if en else "?")), f"is-enabled={en or 'n/a'}"
        if sup_kind == "none":
            return "n/a", "sin supervisor (retirado)"
    except Exception as e:
        return "?", f"sup error: {str(e)[:30]}"
    return "?", "sup desconocido"


def _healthy(lifecycle, up):
    return (not up) if lifecycle == "retired" else up


def _wiring_probe():
    """Delega la salud MCP en el probador AUTORITATIVO (mística: reusar, no duplicar).
    scripts/verify_soul_mcp_wiring.py hace el chain funcional completo por servidor:
    initialize → tools/list → llamada CANARIO real + gates negativos (mutación sin
    aprobación = DENIED). Devuelve {server: {ok, tools, probe, detail}} o None si no
    se puede correr (sin SEAL_SESSION_TOKEN → caemos al handshake, etiquetado honesto)."""
    import os
    tok = os.environ.get("SEAL_SESSION_TOKEN")
    if not tok:
        return None  # sin token no corre el test de memoria; fallback honesto
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_soul_mcp_wiring.py")],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "SEAL_SESSION_TOKEN": tok})
        out = r.stdout
        i, j = out.find("["), out.rfind("]") + 1
        if i < 0 or j <= i:
            return None
        return {row["server"]: row for row in json.loads(out[i:j])}
    except Exception:
        return None


def _mcp_rows():
    cfg = json.loads((ROOT / ".mcp.json").read_text())
    servers = cfg.get("mcpServers", cfg.get("servers", {}))
    wiring = _wiring_probe()  # canario autoritativo si hay token
    rows = []
    for name, c in servers.items():
        url = c.get("url", "")
        blob = " ".join([c.get("command", "")] + list(c.get("args", [])))
        pys = [t for t in blob.split() if t.endswith(".py")]
        identity = url or (pys[-1] if pys else "?")
        w = wiring.get(name) if wiring else None
        if w is not None:
            # verdad autoritativa: chain funcional completo (initialize+tools/list+canario)
            up = bool(w["ok"])
            wd = str(w.get("detail", ""))
            probe = "wiring-canary"
            # RECLASIFICACIÓN ESTRECHA: un canario que trae DENY por capability del
            # broker PRUEBA que el MCP está vivo y ruteó a su capa de authz (round-trip
            # completo) — el control funcionó, no es outage. Misma semántica que el
            # github `mutations_without_approval=DENIED` que el probe ya cuenta OK.
            gated = (not up) and "TOOL_BROKER" in wd and (
                "capability" in wd or "no capability_scope" in wd)
            if gated:
                up = True
                detail = f"canario {w.get('probe', '?')} GATEADO por capability (control OK, MCP procesó) · {w.get('tools', '?')} tools"
                probe = "wiring-canary (gated-tool)"
            else:
                detail = f"canario {w.get('probe', '?')} · {w.get('tools', '?')} tools · {'OK' if up else 'FALLA'}"
        elif url:
            up, detail = _probe("http", url)
            probe = "http (transporte; canario req. SEAL_SESSION_TOKEN)"
        else:
            up, detail = _mcp_handshake(c.get("command", ""), c.get("args", []))
            probe = "handshake (transporte; canario req. SEAL_SESSION_TOKEN)"
        rows.append({"name": f"{name} (MCP)", "type": "mcp", "origin": "nativo",
                     "identity": identity, "lifecycle": "active", "replaced_by": "",
                     "probe": probe, "up": up, "detail": detail,
                     "reboot_safe": "session", "reboot_detail": "nace con la sesión del agente",
                     "healthy": up})
    return rows


def build():
    results = list(_mcp_rows())
    for name, typ, lifecycle, repl, (pk, pa), (sk, sa) in STATIC:
        up, detail = _probe(pk, pa)
        rb, rbd = ("n/a", "retirado") if lifecycle == "retired" else _reboot_safe(sk, sa)
        results.append({"name": name, "type": typ, "origin": "nativo" if typ != "engine" else "extern",
                        "identity": f"{sk}:{sa}" if sa else f"{pk}", "lifecycle": lifecycle,
                        "replaced_by": repl, "probe": f"{pk}:{pa}", "up": up, "detail": detail,
                        "reboot_safe": rb, "reboot_detail": rbd, "healthy": _healthy(lifecycle, up)})
    return results


def _fmt(results):
    print(f"{'DEP':<28} {'TIPO':<8} {'LIFECYCLE':<9} {'SALUD':<6} DETALLE")
    print("-" * 92)
    bad = 0
    for r in results:
        if not r["healthy"]:
            bad += 1
        mark = "OK" if r["healthy"] else "⚠BAD"
        st = r["detail"]
        if r["lifecycle"] == "retired":
            st += "  (retirado: DOWN=correcto)"
        elif r["reboot_safe"] in (False, "?"):
            st += f"  ⚠reboot:{r['reboot_safe']}"
        print(f"{r['name']:<28} {r['type']:<8} {r['lifecycle']:<9} {mark:<6} {st}")
    active = [r for r in results if r["lifecycle"] in ("active", "legacy")]
    up_act = sum(1 for r in active if r["up"])
    print(f"\nactivos-UP={up_act}/{len(active)}  SALUD={len(results)-bad}/{len(results)} coherentes"
          + ("" if not bad else f"  ⚠{bad} incoherentes"))
    frag = [r["name"] for r in results if r["reboot_safe"] in (False, "?") and r["lifecycle"] != "retired"]
    if frag:
        print(f"⚠ activos NO reboot-safe (medido): {', '.join(frag)}")
    return bad


def _diff(results):
    if not BASELINE.exists():
        print(f"baseline durable ausente: {BASELINE} — corré --save-baseline")
        return 2
    base = {r["name"]: r for r in json.loads(BASELINE.read_text())}
    now = {r["name"]: r for r in results}
    regress = []
    for name, b in base.items():
        n = now.get(name)
        if n is None:
            # FAIL-CLOSED: una dep del baseline desapareció del catálogo (rename/removal)
            if b.get("lifecycle") == "retired":
                continue
            regress.append(f"DESAPARECIÓ: {name} (lifecycle={b.get('lifecycle')}) — estaba en baseline, ausente ahora")
            continue
        if b.get("healthy") and not n["healthy"]:
            regress.append(f"ROTO: {name} — sano→{n['detail']} (lifecycle={n['lifecycle']})")
        elif b.get("reboot_safe") is True and n["reboot_safe"] in (False, "?"):
            regress.append(f"REBOOT: {name} — perdió reboot-safety ({n.get('reboot_detail')})")
    print(f"DIFF contra {BASELINE.name} — {len(base)} deps en baseline (fail-closed)")
    if regress:
        print("\n⚠ REGRESIONES:")
        for r in regress:
            print(f"  {r}")
    else:
        print("✅ 0 regresiones: todo lo sano sigue coherente, reboot-safe, y nada desapareció.")
    return 1 if regress else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--diff", action="store_true", help="gate de regresión (fail-closed) vs baseline durable")
    ap.add_argument("--save-baseline", action="store_true", help="regenera tools/dependency_baseline.json (git-tracked)")
    args = ap.parse_args()

    results = build()

    if args.json:
        print(json.dumps(results, indent=2))
        return 0 if all(r["healthy"] for r in results) else 1
    if args.save_baseline:
        BASELINE.write_text(json.dumps(results, indent=2) + "\n")
        print(f"baseline durable escrito: {BASELINE} ({len(results)} deps). `git add` para sellarlo.")
        return 0
    if args.diff:
        return _diff(results)
    return 1 if _fmt(results) else 0


if __name__ == "__main__":
    sys.exit(main())
