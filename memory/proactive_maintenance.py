#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proactive_maintenance.py — Runner ÚNICO de auto-mantenimiento proactivo del equipo SEAL.
=========================================================================================
Directiva William (22-jun-2026): "si hay problemas menores y detalles técnicos, ser
proactivo y tienen la libertad de solucionar, guarden eso en un cron."

Arquitectura (JARVIS): UN solo runner que el daemon de nervios (seal-nerves-daemon)
invoca cada tick. NO crons nuevos por agente (evita duplicación + recalentar el Spark,
ver el incidente de monitores huérfanos a 92°C). Cada agente APORTA detectores y fixers
al MISMO registry, no su propio cron.

  detector() -> list[Issue]      (read-only: detecta un detalle técnico menor)
  fixer(issue) -> FixResult      (aplica el fix; GATEADO + reversible + minor-only)

Seguridad: por defecto DRY_RUN (solo detecta y reporta). Para que un fixer EJECUTE de
verdad necesita: (a) flag SEAL_PROACTIVE_FIX=on, (b) issue.severity=='minor', (c) el
fixer marcado reversible=True. Lo no-menor / con riesgo / outward-facing => solo alerta.
(El gate de seguridad fino lo aporta NEXUS; aquí está el esqueleto conservador.)
"""
from __future__ import annotations
import os, sys, json, time, subprocess, datetime
from dataclasses import dataclass, field, asdict
from typing import Callable

# Fix sistémico (FABLE generalización 23-jun): garantizar env de sesión para TODO detector
# session-dependent (systemctl --user, dbus). Sin esto, en cron fallan con "connect to bus".
os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")

DRY_RUN = os.environ.get("SEAL_PROACTIVE_FIX", "off").lower() not in ("on", "1", "true")
LOG = "/tmp/seal_proactive_maintenance.jsonl"


@dataclass
class Issue:
    key: str                 # id estable del problema
    detector: str            # quién lo detectó
    summary: str
    severity: str = "minor"  # minor | risky | outward  (solo 'minor' autoejecuta)
    data: dict = field(default_factory=dict)


@dataclass
class FixResult:
    key: str
    applied: bool
    detail: str


# ── Registro {nombre: (detector_fn, fixer_fn|None, reversible)} ──
_REGISTRY: dict[str, tuple[Callable, Callable | None, bool]] = {}


def register(name: str, detector: Callable, fixer: Callable | None = None, reversible: bool = False, cadence: str = 'frequent'):
    """Cada agente registra aquí su detector (+ fixer opcional). No crea cron propio."""
    _REGISTRY[name] = (detector, fixer, reversible, cadence)  # cadence: 'frequent' (*/15) | 'daily'


def _log(rec: dict):
    rec["ts"] = datetime.datetime.now().isoformat()
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def run_once(cadence: str | None = None) -> dict:
    """Un barrido. Si cadence se da, solo corre detectores de esa cadencia; si None, corre TODOS (sweep diario)."""
    detected, fixed, alerted = [], [], []
    for name, (detector, fixer, reversible, det_cad) in _REGISTRY.items():
        if cadence is not None and det_cad != cadence:
            continue
        try:
            issues = detector() or []
        except Exception as e:
            _log({"runner": name, "detector_error": str(e)}); continue
        for iss in issues:
            detected.append(asdict(iss))
            can_autofix = (fixer is not None and reversible and iss.severity == "minor")
            if can_autofix and not DRY_RUN:
                try:
                    res = fixer(iss)
                    (fixed if res.applied else alerted).append(asdict(res) if hasattr(res, '__dataclass_fields__') else res)
                except Exception as e:
                    alerted.append({"key": iss.key, "fixer_error": str(e)})
            else:
                # dry-run, no-fixer, o severity>minor => solo se reporta (no toca)
                alerted.append({"key": iss.key, "summary": iss.summary,
                                "severity": iss.severity, "would_fix": can_autofix and DRY_RUN})
    summary = {"detected": len(detected), "fixed": len(fixed), "alerted": len(alerted),
               "dry_run": DRY_RUN, "issues": detected, "fixes": fixed, "alerts": alerted}
    _log({"sweep": summary})
    return summary


# ── Detector built-in seguro: servicios systemd-user que deberían estar activos y no lo están ──
_WATCH_SERVICES = ["seal-chat.service", "seal-jarvis-dm-poller.service", "mundial-dashboard.service"]

def _systemctl_env():
    """Asegura XDG_RUNTIME_DIR para que `systemctl --user` funcione en contexto cron."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env

def _detect_dead_services() -> list[Issue]:
    out = []
    _env = _systemctl_env()
    for svc in _WATCH_SERVICES:
        try:
            r = subprocess.run(["systemctl", "--user", "is-active", svc],
                               capture_output=True, text=True, timeout=8, env=_env)
            state = r.stdout.strip()
            # GUARD (de NEXUS): si no se alcanza el bus o el estado es desconocido, NO marcar 'down'
            # (mata el falso-positivo de raíz). Solo 'inactive'/'failed' explícito = caído real.
            if "bus" in (r.stderr or "").lower() or state in ("", "unknown"):
                continue
            if state in ("inactive", "failed", "deactivating"):
                out.append(Issue(key=f"svc_down:{svc}", detector="service_watch",
                                 summary=f"{svc} no está activo ({state})",
                                 severity="minor", data={"service": svc}))
        except Exception:
            pass
    return out

def _fix_restart_service(iss: Issue) -> FixResult:
    svc = iss.data.get("service")
    try:
        subprocess.run(["systemctl", "--user", "restart", svc], timeout=20, check=True)
        return FixResult(iss.key, True, f"reiniciado {svc}")
    except Exception as e:
        return FixResult(iss.key, False, f"fallo al reiniciar {svc}: {e}")

register("service_watch", _detect_dead_services, _fix_restart_service, reversible=True)



# ── Detectores externos del equipo (cada agente APORTA aquí; registrados tras definir Issue) ──
def _register_external():
    import importlib.util as _ilu
    # Blindaje doble-import: si corremos como __main__, exponer este módulo bajo su nombre real
    # para que módulos externos (nexus_maintenance) que hacen `from proactive_maintenance import register`
    # registren en ESTE registry, no en una segunda instancia.
    sys.modules.setdefault("proactive_maintenance", sys.modules[__name__])
    # Pieza de NEXUS (auto-registra sus 4 vía register() al importarse)
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import nexus_maintenance  # noqa: F401
    except Exception as _e:
        _log({"register_external_error": f"nexus_maintenance: {_e}"})
    # Ruta RELATIVA al propio archivo: con la absoluta esto fallaba EN SILENCIO
    # en cualquier maquina que no fuera la de William (el except de abajo se
    # traga el error y el detector simplemente no se registra).
    _REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    externs = [
        ("fable_dm_poller", os.path.join(_REPO, "fable", "dm_poller_verifier.py"), "detector", None, False),
    ]
    for name, path, detfn, fixfn, rev in externs:
        try:
            _spec = _ilu.spec_from_file_location(name, path)
            _m = _ilu.module_from_spec(_spec); sys.modules[name] = _m
            _spec.loader.exec_module(_m)
            det = getattr(_m, detfn, None)
            fix = getattr(_m, fixfn) if fixfn else None
            if det:
                register(name, det, fix, reversible=rev)
        except Exception as _e:
            _log({"register_external_error": f"{name}: {_e}"})

_register_external()

if __name__ == "__main__":
    _cad = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SEAL_PM_CADENCE") or None
    res = run_once(cadence=_cad)
    print(json.dumps(res, ensure_ascii=False, indent=1))
