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
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "jarvis_nerves_watch.log"
SOUL_UNIT_NAME = re.compile(r"^(?:seal|soul)-[A-Za-z0-9_.@:\\-]+$")


def _run(args):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "dependency_inventory.py")] + args,
                          capture_output=True, text=True, timeout=90)


def _parse_failed_units(output: str) -> list[str]:
    """Parsea `systemctl list-units --state=failed --plain --no-legend`.

    No interpreta una salida no vacía pero inesperada como "cero": eso sería otro
    falso verde. Devuelve únicamente unidades propias de SOUL/SEAL.
    """
    failed = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split()
        if fields[0] == "●":
            fields = fields[1:]
        if len(fields) < 4 or fields[2] != "failed":
            raise ValueError(f"unexpected systemctl row: {line[:120]}")
        unit = fields[0]
        if SOUL_UNIT_NAME.fullmatch(unit):
            failed.append(unit)
    return sorted(set(failed))


def _failed_units(scope: str) -> list[str]:
    """Lee unidades fallidas del ámbito pedido; falla cerrado si systemd no responde."""
    if scope not in {"user", "system"}:
        raise ValueError(f"unsupported systemd scope: {scope}")
    command = ["systemctl"]
    if scope == "user":
        command.append("--user")
    command.extend([
        "list-units", "--state=failed", "--no-legend", "--no-pager", "--plain",
    ])
    process = subprocess.run(
        command, capture_output=True, text=True, timeout=15, check=False,
    )
    if process.returncode != 0:
        error = (process.stderr or process.stdout).strip().replace("\n", " ")
        raise RuntimeError(
            f"systemd {scope} failed-unit sweep rc={process.returncode}: {error[:160]}"
        )
    return _parse_failed_units(process.stdout)


def check():
    """Corre los instrumentos reales. Devuelve (estado, findings, detalle).

    Cuatro ejes (los 2 primeros RELATIVOS al baseline, los demás ABSOLUTOS):
      1. --diff   : regresión (algo sano que se rompió vs baseline). Unidireccional
                    a propósito: roto→sano NO es regresión (no es ruido).
      2. --identity: atribución (huérfano/mismatch de proceso).
      3. salud ABSOLUTA: ¿algún servicio active/legacy está BAD AHORA? — independiente
                    del baseline. Cierra el punto ciego que cazó el red-team: un baseline
                    stale/corrupto podría eximir un servicio genuinamente caído del --diff;
                    el chequeo absoluto lo caza igual. NO rompe la semántica de --diff.
      4. systemd user+system: cualquier unidad fallida seal-*/soul-* aunque no forme
                    parte del catálogo curado. Fail-closed si un ámbito no puede leerse.
    """
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
    if i.returncode == 2:
        return "BROKEN", ["identity instrument unavailable"], i.stdout + i.stderr
    if i.returncode == 1:
        findings.append("IDENTIDAD: " +
                        " · ".join(l.strip() for l in i.stdout.splitlines() if "FAIL" in l))
    # 3) salud ABSOLUTA por efecto (defensa contra baseline stale/corrupto — red-team)
    a = _run(["--json"])
    if a.returncode != 0:
        return "BROKEN", ["absolute health instrument unavailable"], a.stdout + a.stderr
    try:
        rows = json.loads(a.stdout)
        bad = [r["name"] for r in rows
               if r.get("lifecycle") in ("active", "legacy") and not r.get("healthy")]
        if bad:
            findings.append("SALUD ABSOLUTA: servicio(s) BAD ahora (indep. del baseline): "
                            + ", ".join(bad))
    except Exception as e:
        findings.append(f"SALUD ABSOLUTA: no se pudo evaluar (fail-closed): {str(e)[:40]}")
    # 4) sweep complementario: el catálogo curado no conoce necesariamente una unidad
    # nueva. Revisamos ambos ámbitos y nunca traducimos "no pude leer" a GREEN.
    systemd_findings = []
    try:
        for scope in ("user", "system"):
            failed_units = _failed_units(scope)
            if failed_units:
                systemd_findings.append(
                    f"SYSTEMD {scope}: unidad(es) SOUL/SEAL fallida(s): "
                    + ", ".join(failed_units)
                )
    except Exception as e:
        return (
            "BROKEN",
            ["systemd failed-unit sweep unavailable"],
            f"{type(e).__name__}: {str(e)[:240]}",
        )
    # Escalón 2 — MEMORIA ("qué es normal", orden William 23-jul): los hallazgos ya
    # clasificados como normal/aceptado NO rompen el silencio (se cuentan, no alertan).
    # Solo los NUEVOS/no-clasificados alertan. Fail-open: si la memoria falla, se detecta
    # igual (nunca bloquea la detección real).
    conocidos = []
    try:
        import importlib.util as _il
        _spec = _il.spec_from_file_location(
            "jarvis_nerves_known_baseline", ROOT / "tools" / "jarvis_nerves_known_baseline.py")
        _kb = _il.module_from_spec(_spec)
        _spec.loader.exec_module(_kb)
        findings, conocidos = _kb.filter_findings(findings)
    except Exception:
        pass
    # Una unidad propia en estado failed es evidencia viva, no una anomalía histórica:
    # la memoria de baseline no puede convertirla en GREEN.
    findings.extend(systemd_findings)
    detalle = (d.stdout.strip().splitlines()[-1] if d.stdout.strip() else "") + \
              " | " + (i.stdout.strip().splitlines()[-1] if i.stdout.strip() else "")
    if conocidos:
        detalle += f" | conocidos-normales-silenciados={len(conocidos)}"
    return ("FINDING" if findings else "GREEN"), findings, detalle


def _log(line: str):
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")
    LOG.chmod(0o600)


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
                            msg, "--channel", "web_chat", "--type", "alert"],
                           timeout=20, check=True)
        except Exception as e:
            print(f"  (no pudo publicar: {e})")
            return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
