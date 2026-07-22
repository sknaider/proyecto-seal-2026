#!/usr/bin/env python3
"""SOUL Dependency Inventory — catálogo único durable, verificado POR EFECTO.

Owner: JARVIS. Sustrato del inventario fusionado (21-jul-2026, "ordenar
quirúrgicamente" de William; corrección de identidades por ADA).

Ataca el hallazgo estructural de ADA #1: un catálogo durable (git-tracked,
sobrevive reboot/compactación) donde cada dependencia declara su IDENTIDAD real,
su LIFECYCLE y CÓMO se verifica por sonda funcional — no "existe el binario/puerto"
sino "el componente vivo responde".

CAUSA RAÍZ de los falsos verdes v1 (cazados por ADA, builder≠verifier sobre mí):
  - sondaba por NOMBRES adivinados (`mcp-server-postgres` npm retirado) en vez de
    la fuente de verdad `.mcp.json` → verde sobre lo retirado.
  - puse el nombre `seal-smg` a una sonda que chequeaba el contenedor legacy 8766;
    seal-smg es OTRO componente (gateway 8770, ya retirado).
Fix estructural: los MCP se DERIVAN de `.mcp.json` (no se adivinan) y cada fila
lleva identity/lifecycle/replaced_by. Un componente `retired` que está DOWN es
SANO (DOWN es su estado deseado), no un rojo.

Columnas: name · type · origin · identity · lifecycle · replaced_by · probe(efecto)
          · reboot_safe · healthy(=lifecycle coherente con el estado real)

Uso:
  python3 tools/dependency_inventory.py            # verifica todo por efecto
  python3 tools/dependency_inventory.py --json     # salida máquina
  python3 tools/dependency_inventory.py --diff [BASELINE]   # gate de regresión
"""
from __future__ import annotations

import argparse
import json
import pathlib
import py_compile
import socket
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]

# ── Motores y servicios NO-MCP. Fuente de verdad de QUÉ debe existir. ─────────
# (name, type, origin, (probe_kind, arg), lifecycle, replaced_by, reboot_safe)
STATIC = [
    ("postgres-engine",  "engine",  "extern", ("port", 5433),  "active", "", True),
    ("neo4j-engine",     "engine",  "extern", ("port", 7687),  "active", "", True),
    ("ollama-engine",    "engine",  "extern", ("port", 11434), "active", "", True),
    ("chat-server",      "service", "nativo", ("port", 8765),  "active", "", True),
    ("orion-exam",              "service", "nativo", ("unit", "orion-exam.service"), "active", "", "auto"),
    ("jarvis-awareness",        "service", "nativo", ("unit", "jarvis-awareness.service"), "active", "", "auto"),
    ("ada-codex-remote-bridge", "service", "nativo", ("unit", "ada-codex-remote-bridge.service"), "active", "", "auto"),
    # ── Legacy VIVO: SOUL API v1 en contenedor docker. NO es seal-smg. ──
    ("soul-api-v1-legacy (8766)", "gateway", "nativo",
     ("docker", "soul-api-server-legacy-8766"), "legacy",
     "parcial: 8771 seal-memory + 8768 memoria tenant-safe (sin equivalencia total aún)", "auto"),
    # ── RETIRADO: gateway SMG (8770/9091). DOWN es su estado CORRECTO. ──
    ("seal-smg (gw 8770)", "gateway", "nativo",
     ("unit", "seal-smg.service"), "retired", "8771 seal-memory MCP", "auto"),
]


def _probe(kind, arg):
    """Devuelve (up: bool, detail: str). up = 'el componente responde'."""
    try:
        if kind == "port":
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            ok = s.connect_ex(("127.0.0.1", arg)) == 0
            s.close()
            return ok, f"tcp :{arg} {'up' if ok else 'down'}"
        if kind == "http":
            try:
                code = urllib.request.urlopen(arg, timeout=3).getcode()
                return True, f"http {code}"
            except urllib.error.HTTPError as e:
                return e.code < 500, f"http {e.code}"  # 400/406 = vivo, otro contrato
        if kind == "unit":
            r = subprocess.run(["systemctl", "--user", "is-active", arg],
                               capture_output=True, text=True, timeout=5)
            st = r.stdout.strip()
            return st == "active", f"unit {st}"
        if kind == "docker":
            r = subprocess.run(["docker", "inspect", arg, "--format", "{{.State.Running}}"],
                               capture_output=True, text=True, timeout=5)
            run = r.stdout.strip() == "true"
            return run, f"docker {'running' if run else 'down/ausente'}"
        if kind == "pyfile":
            # sonda FUNCIONAL de un MCP lanzado por python: existe + compila.
            # Mucho más fuerte que "binario npm en PATH" (el falso verde de v1).
            p = arg if pathlib.Path(arg).is_absolute() else ROOT / arg
            p = pathlib.Path(p)
            if not p.exists():
                return False, f"pyfile AUSENTE {p.name}"
            try:
                py_compile.compile(str(p), doraise=True)
                return True, f"pyfile compila {p.name}"
            except py_compile.PyCompileError:
                return False, f"pyfile NO compila {p.name}"
    except Exception as e:
        return False, f"error: {str(e)[:40]}"
    return False, "probe desconocido"


def _mcp_rows():
    """Deriva las filas MCP de .mcp.json (fuente de verdad, no adivinanza)."""
    cfg = json.loads((ROOT / ".mcp.json").read_text())
    servers = cfg.get("mcpServers", cfg.get("servers", {}))
    rows = []
    for name, c in servers.items():
        url = c.get("url", "")
        if url:
            probe = ("http", url)
            identity = url
        else:
            # extraer el .py real que lanza el MCP (nativo), de command/args
            blob = " ".join([c.get("command", "")] + list(c.get("args", [])))
            pys = [tok for tok in blob.split() if tok.endswith(".py")]
            script = pys[-1] if pys else "?"
            probe = ("pyfile", script)
            try:
                identity = str(pathlib.Path(script).relative_to(ROOT))
            except Exception:
                identity = script
        rows.append((f"{name} (MCP)", "mcp", "nativo", probe,
                     "active", "", "session", identity))
    return rows


def _reboot(reboot, pkind, parg, detail):
    """Resuelve reboot_safe='auto' POR EFECTO (is-enabled / restart-policy)."""
    if reboot != "auto":
        return reboot, detail
    if pkind == "unit":
        r = subprocess.run(["systemctl", "--user", "is-enabled", parg],
                           capture_output=True, text=True, timeout=5)
        en = r.stdout.strip()
        return (True if en == "enabled" else (False if en else "?")), detail + f" · is-enabled={en or 'n/a'}"
    if pkind == "docker":
        r = subprocess.run(["docker", "inspect", parg, "--format",
                            "{{.HostConfig.RestartPolicy.Name}}"],
                           capture_output=True, text=True, timeout=5)
        pol = r.stdout.strip()
        safe = pol in ("unless-stopped", "always", "on-failure") or (False if pol else "?")
        return safe, detail + f" · restart={pol or 'n/a'}"
    return "?", detail


def _healthy(lifecycle, up):
    """retired ⇒ sano si está DOWN. active/legacy ⇒ sano si está UP."""
    return (not up) if lifecycle == "retired" else up


def build():
    results = []
    # MCP (de .mcp.json) + estáticos
    for name, typ, origin, probe, lifecycle, repl, reboot, identity in _mcp_rows():
        up, detail = _probe(*probe)
        results.append({"name": name, "type": typ, "origin": origin, "identity": identity,
                        "lifecycle": lifecycle, "replaced_by": repl,
                        "probe": f"{probe[0]}:{probe[1]}", "up": up, "detail": detail,
                        "reboot_safe": reboot, "healthy": _healthy(lifecycle, up)})
    for name, typ, origin, (pk, pa), lifecycle, repl, reboot in STATIC:
        up, detail = _probe(pk, pa)
        reboot, detail = _reboot(reboot, pk, pa, detail)
        results.append({"name": name, "type": typ, "origin": origin, "identity": f"{pk}:{pa}",
                        "lifecycle": lifecycle, "replaced_by": repl,
                        "probe": f"{pk}:{pa}", "up": up, "detail": detail,
                        "reboot_safe": reboot, "healthy": _healthy(lifecycle, up)})
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--diff", metavar="BASELINE", nargs="?",
                    const="logs/dependency_baseline_latest.json",
                    help="compara contra un baseline y marca REGRESIONES (algo sano que se rompió)")
    args = ap.parse_args()

    results = build()

    if args.json:
        print(json.dumps(results, indent=2))
        return 0 if all(r["healthy"] for r in results) else 1

    if args.diff:
        bpath = pathlib.Path(args.diff)
        if not bpath.is_absolute():
            bpath = ROOT / bpath
        if not bpath.exists():
            print(f"baseline no encontrado: {bpath}")
            return 2
        base = {r["name"]: r for r in json.loads(bpath.read_text())}
        now = {r["name"]: r for r in results}
        regress = []
        for name, b in base.items():
            n = now.get(name)
            if n is None:
                continue
            if b.get("healthy") and not n["healthy"]:
                regress.append(f"ROTO: {name} — baseline sano → ahora {n['detail']} (lifecycle={n['lifecycle']})")
            elif b["reboot_safe"] is True and n["reboot_safe"] in (False, "?"):
                regress.append(f"REBOOT: {name} — perdió reboot-safety ({n['detail']})")
        print(f"DIFF contra {bpath.name} — {len(base)} deps en baseline")
        if regress:
            print("\n⚠ REGRESIONES (algo sano se rompió — NO era el objetivo del corte):")
            for r in regress:
                print(f"  {r}")
        else:
            print("✅ 0 regresiones: todo lo sano sigue coherente + reboot-safe.")
        return 1 if regress else 0

    print(f"{'DEP':<26} {'TIPO':<8} {'LIFECYCLE':<9} {'SALUD':<6} DETALLE")
    print("-" * 78)
    bad = 0
    for r in results:
        healthy = r["healthy"]
        if not healthy:
            bad += 1
        mark = "OK" if healthy else "⚠BAD"
        rb = r["reboot_safe"]
        if r["lifecycle"] == "retired":
            flag = ""  # reboot-safety de un retirado es irrelevante
        else:
            flag = " ⚠NO-rearranca" if rb is False else (" ⚠reboot?" if rb == "?" else "")
        # para retired, mostrar que DOWN es lo esperado
        st = r["detail"]
        if r["lifecycle"] == "retired":
            st += "  (retirado: DOWN es correcto)"
        print(f"{r['name']:<26} {r['type']:<8} {r['lifecycle']:<9} {mark:<6} {st}{flag}")

    active = [r for r in results if r["lifecycle"] in ("active", "legacy")]
    up_active = sum(1 for r in active if r["up"])
    nat = sum(1 for r in results if r["origin"] == "nativo")
    print(f"\nnativos={nat}/{len(results)}  activos-UP={up_active}/{len(active)}  "
          + (f"SALUD: {len(results)-bad}/{len(results)} coherentes"
             + ("" if not bad else f"  ⚠{bad} incoherentes")))
    not_safe = [r["name"] for r in results if r["reboot_safe"] in (False, "?") and r["lifecycle"] != "retired"]
    if not_safe:
        print(f"⚠ activos que NO sobreviven reboot: {', '.join(not_safe)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
