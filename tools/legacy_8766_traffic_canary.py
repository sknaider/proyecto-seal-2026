#!/usr/bin/env python3
"""Canario de tráfico legado de :8766 (SOUL API v1) — verificación POR EFECTO.

Owner: JARVIS (fase-2, "autonomía total" de William 22-jul). Lane de verificación
del retiro de :8766 que ofrecí a ADA; complementa su mapeo de rutas (nivel config)
con el tráfico REAL a nivel runtime.

Gate que implementa: **"canario sin tráfico legado"** — prueba que, en una ventana,
NADIE con datos reales llama a :8766 (solo infra/probes/SDK-fallback). Solo entonces
es seguro apagarlo.

Método (lecciones ya documentadas, aplicadas):
- La fuente de verdad del tráfico es el ACCESS LOG del contenedor, NO el conteo de
  conexiones establecidas (`ss`) — que da 0 falso porque el tráfico es request/response
  corto, no persistente. [[reference_zero_connections_not_safe_to_remove]]
- `docker logs --since` es POCO FIABLE en este contenedor (rango de timestamps mixto);
  se parsea `--timestamps` y se filtra la ventana EN PYTHON.
- Clasificación honesta: infra (/health, /openapi.json, /metrics, /docs, /favicon)
  ≠ tráfico de CONSUMIDOR. El canario mira solo el de consumidor.
- El access log prueba EFECTO (qué se llamó), no INTENCIÓN — un 403 puede ser prober
  denegado (ok) o consumidor roto (reconfigurar); se reporta para decisión humana.

Uso:
  python3 tools/legacy_8766_traffic_canary.py                 # ventana 15 min
  python3 tools/legacy_8766_traffic_canary.py --window-min 60
  python3 tools/legacy_8766_traffic_canary.py --json
Exit: 0 = canario LIMPIO (0 tráfico de consumidor en la ventana) · 1 = tráfico presente.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

CONTAINER = os.environ.get(
    "SEAL_LEGACY_ROLLBACK_CONTAINER",
    "soul-api-v1-rollback-20260722",
)
# rutas de infra/monitoreo — NO son tráfico de consumidor real
INFRA = ("/health", "/openapi.json", "/metrics", "/docs", "/redoc", "/favicon.ico", "/")
# línea uvicorn: <ts> INFO:  <ip>:<port> - "GET /path HTTP/1.1" 200 OK
_LINE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z).*?'
    r'(?P<ip>\d+\.\d+\.\d+\.\d+):\d+ - '
    r'"(?P<method>[A-Z]+) (?P<path>[^ ]+) HTTP/[\d.]+" (?P<status>\d+)')


def _parse_ts(ts: str) -> float:
    """ISO ts → epoch (sin depender de Date.now del entorno; usa el ts del log)."""
    # 2026-07-22T06:15:53.908650931Z → truncar nanos a micros para fromisoformat
    import datetime
    core = ts[:26].rstrip("Z")
    if "." in core:
        head, frac = core.split(".")
        core = f"{head}.{frac[:6]}"
    dt = datetime.datetime.fromisoformat(core).replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def _now_utc() -> float:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def collect(window_min: int, tail: int = 5000, container: str = CONTAINER):
    r = subprocess.run(["docker", "logs", "--timestamps", "--tail", str(tail), container],
                       capture_output=True, text=True, timeout=20)
    # FAIL-CLOSED: si el contenedor no existe/renombrado (cuarentena), docker logs
    # devuelve returncode≠0 → NO pudimos leer. "0 tráfico" por lectura fallida NO es
    # "limpio" (lección: 0 resultados puede ser pipeline roto, no un cero real).
    read_ok = r.returncode == 0
    read_err = "" if read_ok else (r.stderr.strip()[:120] or "docker logs falló")
    lines = (r.stdout + r.stderr).splitlines() if read_ok else []
    cutoff = _now_utc() - window_min * 60
    infra, consumer, parsed, newest = [], [], 0, 0.0
    for ln in lines:
        m = _LINE.search(ln)
        if not m:
            continue
        parsed += 1
        try:
            t = _parse_ts(m.group("ts"))
        except Exception:
            continue
        newest = max(newest, t)
        if t < cutoff:
            continue
        rec = {"ip": m.group("ip"), "method": m.group("method"),
               "path": m.group("path"), "status": int(m.group("status"))}
        base = rec["path"].split("?")[0]
        (infra if base in INFRA else consumer).append(rec)
    return {"parsed": parsed, "window_min": window_min, "newest_epoch": newest,
            "infra": infra, "consumer": consumer,
            "read_ok": read_ok, "read_err": read_err, "raw_lines": len(lines)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-min", type=int, default=15)
    ap.add_argument(
        "--container",
        default=CONTAINER,
        help="contenedor de cuarentena (o SEAL_LEGACY_ROLLBACK_CONTAINER)",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    d = collect(args.window_min, container=args.container)
    # FAIL-CLOSED: si no se pudieron LEER los logs (contenedor ausente/renombrado a
    # cuarentena), NO se puede concluir "limpio". 0 tráfico por lectura fallida ≠ 0 real.
    if not d["read_ok"]:
        msg = (f"no se pudieron leer los logs del contenedor '{args.container}' "
               f"({d['read_err']}). Si fue renombrado a cuarentena, pasá --container <nombre>. "
               f"NO se puede afirmar 'sin tráfico legado' sin leer los logs.")
        if args.json:
            print(json.dumps({"window_min": args.window_min, "clean": None,
                              "read_ok": False, "error": d["read_err"], "hint": msg}, indent=2))
        else:
            print(f"⚠ INDETERMINADO (fail-closed): {msg}")
        return 2
    cons = d["consumer"]
    # agregación de tráfico de consumidor por (método, path, status)
    agg = {}
    for r in cons:
        k = (r["method"], r["path"].split("?")[0], r["status"])
        agg.setdefault(k, {"n": 0, "ips": set()})
        agg[k]["n"] += 1
        agg[k]["ips"].add(r["ip"])
    rows = sorted(({"method": k[0], "path": k[1], "status": k[2],
                    "count": v["n"], "ips": sorted(v["ips"])}
                   for k, v in agg.items()), key=lambda x: -x["count"])

    clean = len(cons) == 0
    if args.json:
        print(json.dumps({"window_min": args.window_min, "clean": clean,
                          "consumer_requests": len(cons), "infra_requests": len(d["infra"]),
                          "by_endpoint": rows}, indent=2))
        return 0 if clean else 1

    print(f"CANARIO :8766 — ventana {args.window_min} min · {d['parsed']} líneas parseadas")
    print(f"  infra (health/openapi/…): {len(d['infra'])} requests (ignoradas)")
    print(f"  CONSUMIDOR real:          {len(cons)} requests")
    if rows:
        print(f"\n  {'MÉTODO':<6} {'STATUS':<6} {'N':<4} PATH  ← IPs")
        for r in rows:
            note = ""
            if r["status"] == 403:
                note = "  (403: prober denegado O consumidor roto — confirmar intención)"
            elif r["status"] >= 500:
                note = "  (5xx: backend con error)"
            print(f"  {r['method']:<6} {r['status']:<6} {r['count']:<4} {r['path']}  ← {','.join(r['ips'])}{note}")
    print(f"\n{'✅ CANARIO LIMPIO: 0 tráfico de consumidor → seguro de retirar (tras reconfigurar gateway SDK).' if clean else '⚠ TRÁFICO DE CONSUMIDOR PRESENTE: NO retirar hasta reconfigurar/confirmar cada endpoint.'}")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
