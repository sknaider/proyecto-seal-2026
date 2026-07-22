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
4. `identity` real por fila: el SDK nativo :8768 y su gateway :8767 se
   atribuyen a sus unidades reales; el backend de compatibilidad se atribuye al
   contenedor DB local :5435. No se conserva un listener de aplicación :8766.
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

# ── Motores / servicios. supervisor = cómo se MIDE reboot_safe. ──────────────
# (name, type, lifecycle, replaced_by, (probe_kind, arg), (sup_kind, sup_arg))
#   sup_kind: docker=restart-policy · unit-user/unit-sys=is-enabled · none
STATIC = [
    ("postgres-engine (pgvector)", "engine", "active", "",
     ("pgvector", None), ("docker", "seal-memory-db")),
    ("neo4j-engine",  "engine",  "active", "", ("port", 7687),  ("docker", "soul-neo4j")),
    ("ollama-engine", "engine",  "active", "", ("port", 11434), ("unit-sys", "ollama")),
    ("prometheus-engine (9090)", "engine", "active", "", ("port", 9090), ("docker", "seal-prometheus")),
    ("chat-server (8765)", "service", "active", "", ("port", 8765), ("unit-user", "seal-chat.service")),
    ("seal-studio-backend (8800)", "service", "active", "", ("port", 8800), ("unit-user", "seal-studio-backend.service")),
    ("orion-exam",              "service", "active", "", ("unit", "orion-exam.service"), ("unit-user", "orion-exam.service")),
    ("jarvis-awareness",        "service", "active", "", ("unit", "jarvis-awareness.service"), ("unit-user", "jarvis-awareness.service")),
    ("ada-codex-remote-bridge", "service", "active", "", ("unit", "ada-codex-remote-bridge.service"), ("unit-user", "ada-codex-remote-bridge.service")),
    ("soul-memory-sdk-api (8768)", "service", "active", "",
     ("port", 8768), ("unit-user", "seal-memory-sdk-api.service")),
    ("soul-memory-sdk-gateway (8767)", "gateway", "active", "",
     ("http", "http://172.22.0.1:8767/health"), ("unit-user", "seal-memory-sdk-gateway-prod.service")),
    ("soul-api-compat-db (5435)", "engine", "active", "",
     ("port", 5435), ("docker", "soul-api-db")),
    # RETIRADO 22-jul 01:35 (fase-2): SOUL API v1. Transición DECLARADA legacy→retired
    # (restaurada como fila, no borrada — el borrado perdía el audit de la transición;
    # catch de JARVIS confirmado por ADA). Contenedor en cuarentena reversible
    # (soul-api-v1-rollback-20260722, exited, sin autorrestart) + DB :5435 preservada.
    # DOWN (sin listener :8766) es su estado CORRECTO. Verificable: legacy_8766_traffic_canary.py.
    ("soul-api-v1-legacy (8766)", "gateway", "retired",
     ":8767 gateway (172.22.0.1) + :8768 API nativa tenant-safe (absorción completada)",
     ("port", 8766), ("none", None)),
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


# ── Resolver de IDENTIDAD (tarea de ADA): PID→cgroup→unidad/contenedor ────────
# Verifica que quien REALMENTE respalda un puerto/servicio == lo declarado.
# Gotchas manejados (cazados por efecto):
#  1. docker-proxy NO es el contenedor → para puertos docker, se consulta
#     `docker ps` por el contenedor que publica el puerto (no el cgroup del proxy).
#  2. cgroup: tomar la unidad MÁS ESPECÍFICA (última .service), no la slice.
#  3. AUTOEXCLUSIÓN (req. FABLE): el propio PID + ancestría se excluyen del set.
def _self_lineage():
    import os
    pids, pid = set(), os.getpid()
    for _ in range(12):
        if pid <= 1 or pid in pids:
            break
        pids.add(pid)
        try:
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().split(") ", 1)[1].split()[1])  # PPid
        except Exception:
            break
    return pids


def _docker_publisher(port):
    """Contenedor que publica host:port (evita la trampa docker-proxy)."""
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
                           capture_output=True, text=True, timeout=6)
        for line in r.stdout.splitlines():
            if f":{port}->" in line:
                name = line.split("\t", 1)[0]
                d = subprocess.run(["docker", "inspect", name, "--format",
                                    "{{.Id}}\t{{.Config.Image}}\t{{.State.Running}}"],
                                   capture_output=True, text=True, timeout=6)
                cid, image, run = (d.stdout.strip().split("\t") + ["", "", ""])[:3]
                return {"kind": "docker", "name": name, "id": cid[:12], "image": image,
                        "running": run == "true"}
    except Exception:
        pass
    return None


def _listener_pid(port, exclude):
    try:
        for base in (["sudo", "ss"], ["ss"]):
            r = subprocess.run(base + ["-lntpH", f"sport = :{port}"],
                               capture_output=True, text=True, timeout=6)
            for m in r.stdout.split("pid=")[1:]:
                pid = int(m.split(",")[0].split(")")[0])
                if pid not in exclude:
                    return pid
            if r.stdout.strip():
                break
    except Exception:
        pass
    return None


def _cgroup_unit(pid):
    """Unidad systemd MÁS ESPECÍFICA del cgroup (última .service), no la slice."""
    try:
        cg = open(f"/proc/{pid}/cgroup").read()
        units = [tok for tok in cg.replace("/", " ").split()
                 if tok.endswith(".service") or tok.endswith(".scope")]
        # descartar slices genéricas; preferir la última .service específica
        svcs = [u for u in units if u.endswith(".service") and u not in
                ("docker.service", "user@1000.service", "init.scope")]
        return svcs[-1] if svcs else (units[-1] if units else None)
    except Exception:
        return None


def _identity_observed(port, expect_kind, expect_name, exclude):
    """Resuelve la identidad REAL detrás de un puerto. Fail-closed si ambiguo."""
    if expect_kind == "docker":
        pub = _docker_publisher(port)
        if pub is None:
            # host-network: no hay port-map en `docker ps` → resolver por el
            # cgroup del listener (docker-<id>.scope). Gotcha cazado por efecto.
            pid = _listener_pid(port, exclude)
            cid = None
            if pid is not None:
                try:
                    cg = open(f"/proc/{pid}/cgroup").read()
                    m = [t for t in cg.replace("/", " ").split() if t.startswith("docker-")]
                    if m:
                        cid = m[0].replace("docker-", "").replace(".scope", "")[:64]
                except Exception:
                    pass
            if cid:
                d = subprocess.run(["docker", "inspect", cid, "--format",
                                    "{{.Name}}\t{{.Id}}\t{{.Config.Image}}\t{{.State.Running}}"],
                                   capture_output=True, text=True, timeout=6)
                nm, iid, image, run = (d.stdout.strip().split("\t") + ["", "", "", ""])[:4]
                pub = {"kind": "docker", "name": nm.lstrip("/"), "id": iid[:12],
                       "image": image, "running": run == "true", "net": "host"}
                return pub, f"docker {pub['name']} id={pub['id']} image={pub['image']} (host-net)"
            return None, "sin contenedor que publique el puerto"
        return pub, f"docker {pub['name']} id={pub['id']} image={pub['image']}"
    # systemd / proceso
    pid = _listener_pid(port, exclude)
    if pid is None:
        return None, "sin PID dueño (huérfano o no observable)"
    unit = _cgroup_unit(pid)
    return ({"kind": "systemd", "unit": unit, "pid": pid},
            f"pid={pid} unit={unit or 'sin-unidad'}")


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
            # verdad autoritativa: chain funcional completo (initialize+tools/list+canario).
            # SIN reclasificación de deny: un canario que falla se muestra FALLA honesto.
            # (El "authz-gated" anterior band-aideaba MI error de token — corregido:
            #  usar el token MCP del env, no el del webchat. ADA: DENY≠healthy.)
            up = bool(w["ok"])
            detail = f"canario {w.get('probe', '?')} · {w.get('tools', '?')} tools · {'OK' if up else 'FALLA'}"
            probe = "wiring-canary"
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


def _identity_report():
    """Verifica identidad REAL vs declarada por puerto (tarea de ADA). Fail-closed.
    Compara identity_expected (supervisor declarado en STATIC) contra
    identity_observed (resuelto por efecto), excluyendo el propio lineage (FABLE)."""
    exclude = _self_lineage()
    # filas con puerto verificable + supervisor esperado
    checks = []
    for name, typ, lifecycle, repl, (pk, pa), (sk, sa) in STATIC:
        if pk != "port" or lifecycle == "retired":
            continue
        expect_kind = "docker" if sk == "docker" else "systemd"
        checks.append((name, pa, expect_kind, sa))
    # puerto docker-publicado cuyo probe no es "port" — cobertura completa
    checks.append(("postgres-engine", 5433, "docker", "seal-memory-db"))
    checks.append(("soul-memory-sdk-gateway", 8767, "systemd", "seal-memory-sdk-gateway-prod.service"))
    print(f"{'DEP':<28} {'PUERTO':<7} {'VEREDICTO':<10} EXPECTED → OBSERVED")
    print("-" * 92)
    fails = 0
    for name, port, ekind, ename in checks:
        obs, detail = _identity_observed(port, ekind, ename, exclude)
        if obs is None:
            verdict = "FAIL-ORPHAN"  # fail-closed: sin dueño/ambiguo
            fails += 1
        else:
            if ekind == "docker":
                got = obs.get("name", "")
                match = got == ename and obs.get("running")
            else:
                got = (obs.get("unit") or "").removesuffix(".service")
                match = got == ename.removesuffix(".service")
            verdict = "MATCH" if match else "FAIL-MISMATCH"
            if not match:
                fails += 1
        print(f"{name:<28} :{port:<6} {verdict:<10} {ekind}:{ename} → {detail}")
    print(f"\nidentidad: {len(checks)-fails}/{len(checks)} MATCH"
          + (f"  ⚠{fails} fail-closed" if fails else "  — todo atribuido")
          + f"  · autoexcluidos {len(exclude)} PIDs propios")
    return 1 if fails else 0


def _audit_report(results):
    """Health audit LEGIBLE desde la fuente de verdad VIVA (no puertos hardcodeados).
    Substrato durable para las skills de audit (seal-audit, etc.) — al consumir esto
    en vez de hardcodear puertos, NUNCA quedan stale cuando el catálogo cambia.
    Agrupa por tipo, marca UP/DOWN/RETIRED y "qué necesita atención"."""
    by_type = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r)
    print("═══ SOUL — Health Audit (fuente: dependency_inventory, catálogo vivo) ═══")
    attention = []
    for typ in ("engine", "mcp", "service", "gateway"):
        rows = by_type.get(typ, [])
        if not rows:
            continue
        print(f"\n▸ {typ.upper()}")
        for r in rows:
            if r["lifecycle"] == "retired":
                mark = "RETIRED" if not r["up"] else "⚠RESUCITÓ"
            else:
                mark = "UP" if r["up"] else "DOWN"
            if not r["healthy"]:
                attention.append(r["name"])
            print(f"    {mark:<9} {r['name']:<28} {r['detail']}")
    total_active = [r for r in results if r["lifecycle"] in ("active", "legacy")]
    up = sum(1 for r in total_active if r["up"])
    print(f"\n{'─'*60}")
    print(f"Activos: {up}/{len(total_active)} UP · Total catálogo: {len(results)}")
    if attention:
        print(f"⚠ NECESITA ATENCIÓN: {', '.join(attention)}")
        return 1
    print("✅ Todo lo activo está sano. Nada requiere atención.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--diff", action="store_true", help="gate de regresión (fail-closed) vs baseline durable")
    ap.add_argument("--save-baseline", action="store_true", help="regenera tools/dependency_baseline.json (git-tracked)")
    ap.add_argument("--identity", action="store_true", help="verifica identidad real vs declarada por puerto (fail-closed)")
    ap.add_argument("--audit", action="store_true", help="health audit legible desde el catálogo vivo (substrato durable para skills)")
    args = ap.parse_args()

    if args.identity:
        return _identity_report()

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
    if args.audit:
        return _audit_report(results)
    return 1 if _fmt(results) else 0


if __name__ == "__main__":
    sys.exit(main())
