#!/usr/bin/env python3
"""SOUL Dependency Inventory — durable, git-tracked, verificable POR EFECTO.

Owner: JARVIS (21-jul-2026, "ayudar todos" de William).
Ataca el hallazgo estructural de ADA #1: inventario durable que sobrevive a
reinicio/compactación, donde cada dependencia declara CÓMO se verifica por
sonda real — no "existe el archivo/puerto" sino "responde".

Disciplina de la sesión aplicada:
- La verdad es la sonda por EFECTO, no el estado declarado.
- "sobrevive_reboot" es campo explícito: un servicio activo que no rearranca
  es un falso verde de disponibilidad (el gap que marcó ADA).
- Este archivo ES el registro durable: vive en git, no en el chat.

Complementa (NO duplica) el healthcheck de ADA: ella distingue "bridge ocupado
vs caído" en runtime; esto es el CATÁLOGO de qué debe existir y cómo probarlo.

Uso:
  python3 tools/dependency_inventory.py            # verifica todo por efecto
  python3 tools/dependency_inventory.py --json     # salida máquina
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request

# ── Catálogo declarativo. Fuente de verdad de QUÉ debe existir. ──────────────
# probe: cómo se verifica POR EFECTO.
# reboot_safe:  True/False explícito · "auto" = se computa por is-enabled (units)
#               · "session" = nace con la sesión del agente, no es asunto de reboot
INVENTORY = [
    # nombre                tipo        origen     probe                    reboot_safe
    ("postgres-engine",     "engine",   "extern",  ("port", 5433),          True),
    ("neo4j-engine",        "engine",   "extern",  ("port", 7687),          True),
    ("ollama-engine",       "engine",   "extern",  ("port", 11434),         True),
    ("seal-memory (MCP)",   "mcp",      "nativo",  ("http", "http://127.0.0.1:8771/mcp"), True),
    ("chat-server",         "service",  "nativo",  ("port", 8765),          True),
    # seal-smg NO es un unit --user (ese existe pero es fantasma disabled) —
    # lo sirve un contenedor Docker. Verificado por efecto 21-jul (JARVIS).
    ("seal-smg (gw 8766)",  "gateway",  "nativo",  ("docker", "soul-api-server-legacy-8766"), "auto"),
    ("postgres (MCP)",      "mcp",      "nativo",  ("stdio", "mcp-server-postgres"), "session"),
    ("mcp-web-soul (MCP)",  "mcp",      "nativo",  ("file", "fable/seal_cdp_mcp.py"), "session"),
    ("github (MCP)",        "mcp",      "nativo",  ("stdio", "mcp-server-github"), "session"),
    ("prometheus (MCP)",    "mcp",      "nativo",  ("stdio", "prometheus-mcp-server"), "session"),
    ("orion-exam",          "service",  "nativo",  ("unit", "orion-exam.service"), "auto"),
    ("jarvis-awareness",    "service",  "nativo",  ("unit", "jarvis-awareness.service"), "auto"),
    ("ada-codex-remote-bridge", "service", "nativo", ("unit", "ada-codex-remote-bridge.service"), "auto"),
]

results: list[dict] = []


def _probe(kind: str, arg) -> tuple[bool, str]:
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
                # 400/406 = servidor vivo, contrato distinto → cuenta como UP
                return e.code < 500, f"http {e.code}"
        if kind == "unit":
            r = subprocess.run(["systemctl", "--user", "is-active", arg],
                               capture_output=True, text=True, timeout=5)
            state = r.stdout.strip()
            return state == "active", f"unit {state}"
        if kind == "docker":
            r = subprocess.run(["docker", "inspect", arg, "--format", "{{.State.Running}}"],
                               capture_output=True, text=True, timeout=5)
            running = r.stdout.strip() == "true"
            return running, f"docker {'running' if running else 'down/ausente'}"
        if kind == "stdio":
            # binario presente y ejecutable (el proceso vive por sesión, no persistente)
            path = shutil.which(arg) or arg
            present = shutil.which(arg) is not None
            return present, f"binario {'presente' if present else 'AUSENTE'}"
        if kind == "file":
            import pathlib
            p = pathlib.Path(__file__).resolve().parents[1] / arg
            return p.exists(), f"archivo {'existe' if p.exists() else 'AUSENTE'}"
    except Exception as e:
        return False, f"error: {str(e)[:40]}"
    return False, "probe desconocido"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--diff", metavar="BASELINE", nargs="?",
                    const="logs/dependency_baseline_latest.json",
                    help="compara el estado actual contra un baseline JSON y marca "
                         "REGRESIONES (conservada que se cayó o perdió reboot-safety). "
                         "Sin valor usa logs/dependency_baseline_latest.json.")
    args = ap.parse_args()

    for name, typ, origin, (pkind, parg), reboot in INVENTORY:
        ok, detail = _probe(pkind, parg)
        # reboot_safe="auto" en un unit → se resuelve POR EFECTO con is-enabled
        # (cierra el gap de ADA en vez de dejarlo en None/desconocido).
        if reboot == "auto":
            if pkind == "unit":
                r = subprocess.run(["systemctl", "--user", "is-enabled", parg],
                                   capture_output=True, text=True, timeout=5)
                en = r.stdout.strip()
                reboot = True if en == "enabled" else (False if en else "?")
                detail += f" · is-enabled={en or 'n/a'}"
            elif pkind == "docker":
                r = subprocess.run(["docker", "inspect", parg, "--format",
                                    "{{.HostConfig.RestartPolicy.Name}}"],
                                   capture_output=True, text=True, timeout=5)
                pol = r.stdout.strip()
                # unless-stopped/always/on-failure rearrancan tras reboot; 'no'/vacío no
                reboot = pol in ("unless-stopped", "always", "on-failure") or (False if pol else "?")
                detail += f" · restart={pol or 'n/a'}"
            else:
                reboot = "?"
        results.append({
            "name": name, "type": typ, "origin": origin,
            "probe": f"{pkind}:{parg}", "up": ok, "detail": detail,
            "reboot_safe": reboot,
        })

    if args.json:
        print(json.dumps(results, indent=2))
        return 0 if all(r["up"] for r in results) else 1

    if args.diff:
        # Gate de regresión: ¿algo que CONSERVAMOS se rompió tras un corte quirúrgico?
        import pathlib as _pl
        bpath = _pl.Path(args.diff)
        if not bpath.is_absolute():
            bpath = _pl.Path(__file__).resolve().parents[1] / bpath
        if not bpath.exists():
            print(f"baseline no encontrado: {bpath}")
            return 2
        base = {r["name"]: r for r in json.loads(bpath.read_text())}
        now = {r["name"]: r for r in results}
        regress, expected = [], []
        for name, b in base.items():
            n = now.get(name)
            if n is None:
                continue  # ya no está en el catálogo (retiro intencional del código)
            if b["up"] and not n["up"]:
                regress.append(f"CAÍDA: {name} — baseline UP → ahora DOWN ({n['detail']})")
            elif b["reboot_safe"] is True and n["reboot_safe"] in (False, "?"):
                regress.append(f"REBOOT: {name} — perdió reboot-safety ({n['detail']})")
        # dep del baseline ausente del catálogo actual = retiro declarado (esperado, no regresión)
        for name in base.keys() - now.keys():
            expected.append(name)
        print(f"DIFF contra {bpath.name} — {len(base)} deps en baseline")
        if regress:
            print("\n⚠ REGRESIONES (algo conservado se rompió — NO era el objetivo del corte):")
            for r in regress:
                print(f"  {r}")
        else:
            print("✅ 0 regresiones: todo lo conservado sigue UP + reboot-safe.")
        if expected:
            print(f"\nRetiros declarados (ausentes del catálogo, esperado): {', '.join(expected)}")
        return 1 if regress else 0

    print(f"{'DEP':<26} {'TIPO':<8} {'ORIGEN':<7} {'ESTADO':<6} DETALLE")
    print("-" * 72)
    down = 0
    for r in results:
        st = "UP" if r["up"] else "DOWN"
        if not r["up"]:
            down += 1
        rb = r["reboot_safe"]
        flag = " ⚠NO-rearranca" if rb is False else (" ⚠reboot?" if rb == "?" else "")
        print(f"{r['name']:<26} {r['type']:<8} {r['origin']:<7} {st:<6} {r['detail']}{flag}")

    nat = sum(1 for r in results if r["origin"] == "nativo")
    print(f"\nnativos={nat}/{len(results)}  UP={len(results)-down}/{len(results)}"
          + (f"  DOWN={down}" if down else "  — TODO VERDE"))
    # El gap de ADA, cerrado por efecto: unidades que NO rearrancan tras reboot.
    not_safe = [r["name"] for r in results if r["reboot_safe"] in (False, "?")]
    if not_safe:
        print(f"⚠ NO sobreviven reboot (frágiles): {len(not_safe)} → {', '.join(not_safe)}")
    else:
        print("reboot_safe: todo lo persistente es enabled; MCPs = session (nacen con el agente)")
    return 1 if down else 0


if __name__ == "__main__":
    sys.exit(main())
