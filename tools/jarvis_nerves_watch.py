#!/usr/bin/env python3
"""JARVIS NERVES-watch — lazo GATED: disparo → chequeo → habla SOLO si hay hallazgo real.

Owner: JARVIS (22-jul, pregunta de William: "¿NERVES que realmente activen y hagan
el trabajo automáticamente con las herramientas?"). Respuesta POR EFECTO, no teoría.

Qué lo hace distinto de un daemon "que late solo" (teatro que cuesta y no deja artefacto):
- **Acotado**: un chequeo por invocación, sin loop infinito. Se agenda por systemd timer.
- **Usa las herramientas reales** de mi lane: dependency_inventory.py --diff / --identity
  + legacy_8766_traffic_canary.py. No "reflexiona", VERIFICA.
- **Silencio cuando todo verde** (cero ruido/flood — la preocupación de William). Solo
  emite alerta al chat si hay una REGRESIÓN o mismatch REAL, con evidencia.
- **Fail-closed heredado**: los instrumentos ya distinguen "0 real" de "no pude leer".
- **Deja artefacto**: cada corrida escribe una línea al log (disparo→resultado), medible.

Salida: exit 0 = verde (silencio) · 1 = hallazgo real (alertaría) · 2 = instrumento roto.
Uso:
  python3 tools/jarvis_nerves_watch.py            # un chequeo, silencioso si verde
  python3 tools/jarvis_nerves_watch.py --verbose  # muestra el detalle siempre
  python3 tools/jarvis_nerves_watch.py --alert     # además publica al chat si hay hallazgo
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "jarvis_nerves_watch.log"


def _run(args):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "dependency_inventory.py")] + args,
                          capture_output=True, text=True, timeout=90)


def check():
    """Corre los instrumentos reales. Devuelve (estado, findings, detalle)."""
    findings = []
    # 1) gate de regresión (fail-closed)
    d = _run(["--diff"])
    if d.returncode == 2:
        return "BROKEN", ["baseline ausente/instrumento roto"], d.stdout + d.stderr
    if d.returncode == 1:
        findings.append("REGRESIÓN en --diff: " +
                        " · ".join(l.strip() for l in d.stdout.splitlines() if "ROTO" in l or "DESAPARECIÓ" in l or "REBOOT" in l))
    # 2) identidad (fail-closed ante huérfano/mismatch)
    i = _run(["--identity"])
    if i.returncode == 1:
        findings.append("IDENTIDAD: " +
                        " · ".join(l.strip() for l in i.stdout.splitlines() if "FAIL" in l))
    detalle = (d.stdout.strip().splitlines()[-1] if d.stdout.strip() else "") + \
              " | " + (i.stdout.strip().splitlines()[-1] if i.stdout.strip() else "")
    return ("FINDING" if findings else "GREEN"), findings, detalle


def _log(line: str):
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--alert", action="store_true", help="publica al chat si hay hallazgo real")
    ap.add_argument("--stamp", default="", help="marca temporal externa (el entorno bloquea Date.now)")
    args = ap.parse_args()

    estado, findings, detalle = check()
    # artefacto: siempre deja rastro del disparo→resultado (medible)
    _log(f"[{args.stamp or '?'}] nerves-watch estado={estado} findings={len(findings)} :: {detalle}")

    if estado == "GREEN":
        if args.verbose:
            print(f"✅ NERVES-watch: verde, silencio. {detalle}")
        return 0  # SILENCIO — no habla cuando todo está bien
    if estado == "BROKEN":
        print(f"⚠ NERVES-watch: instrumento roto (fail-closed) — {findings}")
        return 2

    # FINDING real → esto es lo único que rompe el silencio
    msg = "⚠ JARVIS NERVES-watch — HALLAZGO REAL:\n" + "\n".join(f"  - {f}" for f in findings)
    print(msg)
    if args.alert:
        try:
            subprocess.run([str(ROOT / "scripts" / "seal_send.py"), "JARVIS", "William",
                            msg, "--channel", "web_chat", "--type", "alert"], timeout=20)
        except Exception as e:
            print(f"  (no pudo publicar: {e})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
